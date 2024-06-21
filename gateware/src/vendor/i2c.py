# This file re-uses some of `interfaces/i2c` from LUNA.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

from amaranth                    import *
from amaranth.lib.fifo           import SyncFIFO
from luna.gateware.interface.i2c import I2CInitiator
from luna_soc.gateware.csr.base  import Peripheral

class I2CPeripheral(Peripheral, Elaboratable):

    def __init__(self, *, pads, period_cyc, clk_stretch=False,
                 transaction_depth=32, rx_depth=8, **kwargs):

        super().__init__()

        self.pads          = pads
        self.period_cyc    = period_cyc
        self.clk_stretch   = clk_stretch

        self.data_width        = 8
        self.addr_width        = 7
        self.transaction_width = self.data_width + 1

        self._transactions = SyncFIFO(width=self.transaction_width, depth=transaction_depth)
        self._rx_fifo      = SyncFIFO(width=self.data_width, depth=rx_depth)

        # CSRs
        bank                   = self.csr_bank()
        self._busy             = bank.csr(1, "r")
        self._start            = bank.csr(1, "w")
        self._address          = bank.csr(self.addr_width, "w")
        self._transaction_data = bank.csr(self.transaction_width, "w")
        self._transactions_rdy = bank.csr(1, "r")
        self._rx_data          = bank.csr(self.data_width, "r")
        self._err              = bank.csr(1, "rw")

        # Storage for CSRs
        self.address           = Signal(self.addr_width)

        # Wires to the last transaction FIFO output
        self.transaction_rw    = Signal()
        self.transaction_data  = Signal(self.data_width)

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge
        m.submodules.rx_fifo = self._rx_fifo
        m.submodules.transactions = self._transactions

        with m.If(self._address.w_stb):
            m.d.sync += self.address.eq(self._address.w_data)

        with m.If(self._err.w_stb):
            m.d.sync += self._err.r_data.eq(self._err.w_data)

        m.d.comb += [
            # Transactions FIFO <- CSRs
            self._transactions.w_en       .eq(self._transaction_data.w_stb),
            self._transactions.w_data     .eq(self._transaction_data.w_data),
            self._transactions_rdy.r_data .eq(self._transactions.w_rdy),
            # CSRs <- Rx FIFO
            self._rx_data.r_data          .eq(self._rx_fifo.r_data),
            self._rx_fifo.r_en            .eq(self._rx_data.r_stb),
            # PHY <- Transactions FIFO
            self.transaction_rw          .eq(self._transactions.r_data[8]),
            self.transaction_data        .eq(self._transactions.r_data[0:8]),
        ]

        # I2C initiator (low level manager) and default signal values
        m.submodules.i2c = i2c = I2CInitiator(pads=self.pads, period_cyc=self.period_cyc, clk_stretch=self.clk_stretch)
        m.d.comb += [
            i2c.start .eq(0),
            i2c.write .eq(0),
            i2c.read  .eq(0),
            i2c.stop  .eq(0),
        ]

        current_transaction_rw = Signal()
        transaction_stb        = Signal()
        last_transaction       = Signal()

        m.d.comb += self._transactions.r_en.eq(transaction_stb)
        m.d.comb += last_transaction.eq(self._transactions.level == 0)

        with m.FSM() as fsm:

            # We're busy whenever we're not IDLE; indicate so.
            m.d.comb += self._busy.r_data.eq(~fsm.ongoing('IDLE'))

            with m.State('IDLE'):
                with m.If(self._start.w_stb & self._start.w_data):
                    m.next = 'START'

            with m.State('START'):
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
                    m.d.sync += self._err.r_data.eq(1)
                    m.d.comb += i2c.stop.eq(1)
                    m.next = "DRAIN-FIFOS"

            with m.State("DRAIN-FIFOS"):
                with m.If((self._transactions.level == 0) &
                          (self._rx_fifo.level == 0)):
                    m.next = "IDLE"
                with m.Else():
                    m.d.comb += self._transactions.r_en.eq(1)
                    m.d.comb += self._rx_fifo.r_en.eq(1)

        return m
