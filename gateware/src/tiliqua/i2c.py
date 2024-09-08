# Transaction-based I2C peripheral.
#
# This file is built on `interfaces/i2c` from LUNA.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

from amaranth                    import *
from amaranth.lib                import wiring
from amaranth.lib.wiring         import Component, In, Out, flipped, connect
from amaranth.lib.fifo           import SyncFIFO
from amaranth_soc                import csr, gpio
from luna.gateware.interface.i2c import I2CInitiator

class PinSignature(wiring.Signature):
    def __init__(self):
        super().__init__({
            "sda":  Out(gpio.PinSignature()),
            "scl":  Out(gpio.PinSignature()),
        })

class Provider(Component):
    def __init__(self):
        super().__init__({
            "pins": In(PinSignature())
        })

    def elaborate(self, platform):
        m = Module()
        i2c = platform.request("i2c")
        m.d.comb += [
            i2c.sda.o.eq(self.pins.sda.o),
            i2c.sda.oe.eq(self.pins.sda.oe),
            self.pins.sda.i.eq(i2c.sda.i),
            i2c.scl.o.eq(self.pins.scl.o),
            i2c.scl.oe.eq(self.pins.scl.oe),
            self.pins.scl.i.eq(i2c.scl.i),
        ]
        return m

class Peripheral(wiring.Component):

    """Transaction-based I2C peripheral.

    All I2C transactions (read + write) for a single address may
    be enqueued on the transaction fifo at once. Once the core is
    started, it will execute these transactions, and any read operations
    encountered will append bytes to the rx fifo. On a NAK or other
    error, both FIFOs are drained so the core is in a clean state,
    and an `err` flag is asserted. To write to a new device address,
    the address CSR must be written, and the core re-started.

    The semantics of the transaction fifo are designed to match
    the `transaction()` method from `embedded-hal-1.0.0`. The
    contract of this method is repeated here for convenience:

    source: https://github.com/rust-embedded/embedded-hal/blob/master/embedded-hal/src/i2c.rs
    Transaction contract:
    - Before executing the first operation an ST is sent automatically.
      This is followed by `SAD+R/W`  as appropriate.
    - Data from adjacent operations of the same type are sent after each
      other without an `SP`  or `SR`.
    - Between adjacent operations of a different type an `SR` and
      `SAD+R/W` is sent.
    - After executing the last operation an SP is sent automatically.
    - If the last operation is a `Read` the master does not send an
      acknowledge for the last byte.
    - `ST` = start condition
    - `SAD+R/W` = slave address followed by bit 1 to indicate reading or
       0 to indicate writing
    - `SR` = repeated start condition

    CSR registers
    -------------
    start : write-only
        Write a '1' to execute all transactions in the transaction FIFO.
    address : write-only
        7-bit address of the target I2C device for the transactions.
    transaction : write-only
        Transaction FIFO. Each entry can be a write or read transaction.
        The 8 bit data words are the data to write (for write transactions),
        or simply ignored (for read transactions).
    rx_data : read-only
        Read FIFO. 8-bit entries, one per successful read transaction.
        This should only be read once 'busy' has deasserted.

    -- status registers --
    busy : read-only
        If the core is currently executing transactions, '1', else '0'.
    transaction_full : write-only
        If the transaction FIFO is full, '1', else '0'.
    err : read
        '1' if an error (e.g. NACK) has occurred.
        this flag is reset on a new set of transactions ('start' is set).

    TODO
    ----
    - Add more types of error flags than simply NACK.
    - Add an 'abort' CSR to let the SoC drain our FIFOs if it decides
      to abort a transaction midway through writing it (e.g. FIFOs full).
    - Revise 'READ_RECV_VALUE' state. It should never ack the last read
      byte per the transaction() contract.
    """

    class StartReg(csr.Register, access="w"):
        start: csr.Field(csr.action.W, unsigned(1))

    class AddressReg(csr.Register, access="w"):
        address: csr.Field(csr.action.W, unsigned(7))

    class TransactionReg(csr.Register, access="w"):
        rw:   csr.Field(csr.action.W, unsigned(1))
        data: csr.Field(csr.action.W, unsigned(8))

    class RxDataReg(csr.Register, access="r"):
        data: csr.Field(csr.action.R, unsigned(8))

    class StatusReg(csr.Register, access="r"):
        busy:             csr.Field(csr.action.R, unsigned(1))
        transaction_full: csr.Field(csr.action.R, unsigned(1))
        error:            csr.Field(csr.action.R, unsigned(1))

    def __init__(self, *, period_cyc, clk_stretch=False,
                 transaction_depth=32, rx_depth=8, **kwargs):
        self.period_cyc = period_cyc
        self.clk_stretch = clk_stretch

        self.data_width = 8
        self.addr_width = 7
        self.transaction_width = self.data_width + 1

        self._transactions = SyncFIFO(width=self.transaction_width, depth=transaction_depth)
        self._rx_fifo = SyncFIFO(width=self.data_width, depth=rx_depth)

        regs = csr.Builder(addr_width=5, data_width=8)

        self._start = regs.add("start", self.StartReg())
        self._address = regs.add("address", self.AddressReg())
        self._transaction_reg = regs.add("transaction_reg", self.TransactionReg())
        self._rx_data = regs.add("rx_data", self.RxDataReg())
        self._status = regs.add("status", self.StatusReg())

        self._bridge = csr.Bridge(regs.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
            "pins": Out(PinSignature()),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

        self.address = Signal(self.addr_width)
        self.transaction_rw = Signal()
        self.transaction_data = Signal(self.data_width)

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        m.submodules.rx_fifo = self._rx_fifo
        m.submodules.transactions = self._transactions

        connect(m, flipped(self.bus), self._bridge.bus)

        err = Signal()

        with m.If(self._address.f.address.w_stb):
            m.d.sync += self.address.eq(self._address.f.address.w_data)

        m.d.comb += self._status.f.error.r_data.eq(err)

        m.d.comb += [
            self._transactions.w_en.eq(self._transaction_reg.element.w_stb),
            self._transactions.w_data.eq(Cat(self._transaction_reg.f.data.w_data,
                                             self._transaction_reg.f.rw.w_data)),
            self._status.f.transaction_full.r_data.eq(~self._transactions.w_rdy),
            self._rx_data.f.data.r_data.eq(self._rx_fifo.r_data),
            self._rx_fifo.r_en.eq(self._rx_data.f.data.r_stb),
            self.transaction_rw.eq(self._transactions.r_data[8]),
            self.transaction_data.eq(self._transactions.r_data[0:8]),
        ]

        m.submodules.i2c = i2c = I2CInitiator(pads=self.pins, period_cyc=self.period_cyc, clk_stretch=self.clk_stretch)
        m.d.comb += [
            i2c.start.eq(0),
            i2c.write.eq(0),
            i2c.read.eq(0),
            i2c.stop.eq(0),
        ]

        current_transaction_rw = Signal()
        transaction_stb = Signal()
        last_transaction = Signal()

        m.d.comb += self._transactions.r_en.eq(transaction_stb)
        m.d.comb += last_transaction.eq(self._transactions.level == 0)

        with m.FSM() as fsm:

            # We're busy whenever we're not IDLE; indicate so.
            m.d.comb += self._status.f.busy.r_data.eq(~fsm.ongoing('IDLE'))

            with m.State('IDLE'):
                with m.If(self._start.f.start.w_stb & self._start.f.start.w_data):
                    m.next = 'START'

            with m.State('START'):
                m.d.sync += err.eq(0)
                with m.If(~i2c.busy):
                    m.d.comb += i2c.start.eq(1),
                    m.next = 'SEND_DEV_ADDRESS'

            with m.State("SEND_DEV_ADDRESS"):
                with m.If(~i2c.busy):
                    m.d.comb += [
                        i2c.data_i     .eq((self.address << 1) | self.transaction_rw),
                        i2c.write      .eq(1),
                    ]
                    m.d.sync += current_transaction_rw.eq(self.transaction_rw)
                    m.next = "ACK_DEV_ADDRESS"

            with m.State("ACK_DEV_ADDRESS"):
                with m.If(~i2c.busy):
                    with m.If(~i2c.ack_o):
                        m.next = "ABORT"
                    with m.Elif(last_transaction):
                        # zero-length transaction
                        m.next = "FINISH"
                    with m.Elif(current_transaction_rw != self.transaction_rw):
                        # zero-length transaction
                        m.next = "START"
                    with m.Elif(current_transaction_rw == 1):
                        m.next = "RD_RECV_VALUE"
                    with m.Else():
                        m.next = "WR_SEND_VALUE"

            with m.State("RD_RECV_VALUE"):
                with m.If(~i2c.busy):
                    m.d.comb += [
                        transaction_stb.eq(1),
                        i2c.ack_i      .eq(1), # FIXME:  0 in last read byte
                        i2c.read       .eq(1),
                    ]
                    m.next = "RD_WAIT_VALUE"

            with m.State("RD_WAIT_VALUE"):
                with m.If(~i2c.busy):
                    m.d.comb += [
                        self._rx_fifo.w_data.eq(i2c.data_o),
                        self._rx_fifo.w_en.eq(1),
                    ]
                    with m.If(last_transaction):
                        m.next = "FINISH"
                    with m.Elif(self.transaction_rw != 1):
                        m.next = "START"
                    with m.Else():
                        m.next = "RD_RECV_VALUE"

            with m.State("WR_SEND_VALUE"):
                with m.If(~i2c.busy):
                    m.d.comb += [
                        transaction_stb.eq(1),
                        i2c.data_i     .eq(self.transaction_data),
                        i2c.write      .eq(1),
                    ]
                    m.next = "WR_ACK_VALUE"

            with m.State("WR_ACK_VALUE"):
                with m.If(~i2c.busy):
                    with m.If(~i2c.ack_o):
                        m.next = "ABORT"
                    with m.Elif(last_transaction):
                        m.next = "FINISH"
                    with m.Elif(self.transaction_rw != 0):
                        m.next = "START"
                    with m.Else():
                        m.next = "WR_SEND_VALUE"

            with m.State("FINISH"):
                with m.If(~i2c.busy):
                    m.d.comb += i2c.stop.eq(1),
                    m.next = "IDLE"

            with m.State("ABORT"):
                with m.If(~i2c.busy):
                    m.d.sync += err.eq(1)
                    m.d.comb += i2c.stop.eq(1)
                    m.next = "DRAIN_FIFOS"

            with m.State("DRAIN_FIFOS"):
                with m.If((self._transactions.level == 0) &
                          (self._rx_fifo.level == 0)):
                    m.next = "IDLE"
                with m.Else():
                    m.d.comb += self._transactions.r_en.eq(1)
                    m.d.comb += self._rx_fifo.r_en.eq(1)

        return m
