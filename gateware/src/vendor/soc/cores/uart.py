from amaranth              import *
from amaranth.lib          import wiring
from amaranth.lib.wiring   import Component, In, Out, flipped, connect

from amaranth_soc          import csr
from amaranth_stdio.serial import AsyncSerialRX, AsyncSerialTX


__all__ = ["PinSignature", "Peripheral"]


class PinSignature(wiring.Signature):
    """UART pin signature.

    Interface attributes
    --------------------
    tx : :class:`Signal`
        Output.
    rx : :class:`Signal`
        Input.
    """
    def __init__(self):
        super().__init__({
            "tx":  Out(unsigned(1)),
            "rx":  In(unsigned(1)),
        })

class Provider(Component):
    def __init__(self, index):
        self.index = index
        super().__init__({
            "pins": In(PinSignature())
        })

    def elaborate(self, platform):
        m = Module()
        uart = platform.request("uart", self.index)
        m.d.comb += [
            self.pins.rx .eq(uart.rx.i),
            uart.tx.o    .eq(self.pins.tx),
        ]
        return m

class Peripheral(wiring.Component):
    # FIXME group registers
    class TxData(csr.Register, access="w"):
        """valid to write to when tx_rdy is high, will trigger a transmit"""
        data: csr.Field(csr.action.W, unsigned(8))

    class RxData(csr.Register, access="r"):
        """valid to read from when rx_avail is high, last received byte"""
        data: csr.Field(csr.action.R, unsigned(8))

    class TxReady(csr.Register, access="r"):
        """is '1' when 1-byte transmit buffer is empty"""
        txe: csr.Field(csr.action.R, unsigned(1))

    class RxAvail(csr.Register, access="r"):
        """is '1' when 1-byte receive buffer is full; reset by a read from rx_data"""
        rxe: csr.Field(csr.action.R, unsigned(1))

    class BaudRate(csr.Register, access="rw"):
        """baud rate divider, defaults to init"""
        def __init__(self, init):
            super().__init__({
                "div": csr.Field(csr.action.RW, unsigned(24), init=init),
            })


    """A minimal UART."""
    def __init__(self, *, divisor):
        self._init_divisor = divisor

        regs = csr.Builder(addr_width=5, data_width=8)

        self._tx_data   = regs.add("tx_data",  self.TxData(),  offset=0x00)
        self._rx_data   = regs.add("rx_data",  self.RxData(),  offset=0x04)
        self._tx_ready  = regs.add("tx_ready", self.TxReady(), offset=0x08)
        self._rx_avail  = regs.add("rx_avail", self.RxAvail(), offset=0x0c)
        self._divisor   = regs.add("divisor",  self.BaudRate(init=self._init_divisor), offset=0x10)

        self._bridge = csr.Bridge(regs.as_memory_map())

        super().__init__({
            "bus":  In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
            "pins": Out(PinSignature()),
        })
        self.bus.memory_map = self._bridge.bus.memory_map


    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge

        connect(m, flipped(self.bus), self._bridge.bus)

        m.submodules.tx = tx = AsyncSerialTX(divisor=self._init_divisor, divisor_bits=24)
        m.d.comb += [
            self.pins.tx.eq(tx.o),
            tx.data.eq(self._tx_data.f.data.w_data),
            tx.ack.eq(self._tx_data.f.data.w_stb),
            self._tx_ready.f.txe.r_data.eq(tx.rdy),
            tx.divisor.eq(self._divisor.f.div.data)
        ]

        rx_buf = Signal(unsigned(8))
        rx_avail = Signal()

        m.submodules.rx = rx = AsyncSerialRX(divisor=self._init_divisor, divisor_bits=24)

        with m.If(self._rx_data.f.data.r_stb):
            m.d.sync += rx_avail.eq(0)

        with m.If(rx.rdy):
            m.d.sync += [
                rx_buf.eq(rx.data),
                rx_avail.eq(1)
            ]

        m.d.comb += [
            rx.i.eq(self.pins.rx),
            rx.ack.eq(~rx_avail),
            rx.divisor.eq(self._divisor.f.div.data),
            self._rx_data.f.data.r_data.eq(rx_buf),
            self._rx_avail.f.rxe.r_data.eq(rx_avail)
        ]

        return m
