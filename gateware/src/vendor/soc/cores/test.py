from amaranth              import *
from amaranth.lib          import enum, wiring
from amaranth.lib.wiring   import In, Out, flipped, connect

from amaranth_soc          import csr, event


# Simple test peripheral to figure out amaranth register behaviors

class Peripheral(wiring.Component):
    class Writer(csr.Register, access="w"):
        a  : csr.Field(csr.action.W, unsigned(1))
        _0 : csr.Field(csr.action.ResRAW0, unsigned(7))
        b  : csr.Field(csr.action.W, unsigned(1))
        c  : csr.Field(csr.action.W, unsigned(1))
        d  : csr.Field(csr.action.W, unsigned(1))
        e  : csr.Field(csr.action.W, unsigned(1))
        f  : csr.Field(csr.action.W, unsigned(1))
        g  : csr.Field(csr.action.W, unsigned(1))
        h  : csr.Field(csr.action.W, unsigned(1))

    class ReaderWriter(csr.Register, access="rw"):
        a: csr.Field(csr.action.RW, unsigned(1))
        b: csr.Field(csr.action.RW, unsigned(1))
        c: csr.Field(csr.action.RW, unsigned(1))
        d: csr.Field(csr.action.RW, unsigned(1))
        e: csr.Field(csr.action.RW, unsigned(1))
        f: csr.Field(csr.action.RW, unsigned(1))
        g: csr.Field(csr.action.RW, unsigned(1))
        h: csr.Field(csr.action.RW, unsigned(1))

    class Reader(csr.Register, access="r"):
        a: csr.Field(csr.action.R, unsigned(1))
        b: csr.Field(csr.action.R, unsigned(1))
        c: csr.Field(csr.action.R, unsigned(1))
        d: csr.Field(csr.action.R, unsigned(1))
        e: csr.Field(csr.action.R, unsigned(1))
        f: csr.Field(csr.action.R, unsigned(1))
        g: csr.Field(csr.action.R, unsigned(1))
        h: csr.Field(csr.action.R, unsigned(1))


    def __init__(self):
        # registers
        regs = csr.Builder(addr_width=4, data_width=8)
        self._writer       = regs.add("writer",        self.Writer())
        self._readerwriter = regs.add("readerwriter",  self.ReaderWriter())
        self._reader       = regs.add("reader",        self.Reader())
        self._bridge = csr.Bridge(regs.as_memory_map())

        # events
        self._event0 = event.Source(path=("event0",))
        self._event1 = event.Source(path=("event1",))
        event_map = event.EventMap()
        event_map.add(self._event0)
        event_map.add(self._event1)
        self._events = csr.event.EventMonitor(event_map, data_width=8)

        # csr decoder
        self._decoder = csr.Decoder(addr_width=5, data_width=8)
        self._decoder.add(self._bridge.bus)
        self._decoder.add(self._events.bus, name="ev")

        super().__init__({
            "bus":    Out(self._decoder.bus.signature),
            "irq":    Out(unsigned(1)),
        })
        self.bus.memory_map = self._decoder.bus.memory_map

        # debug
        self.debug = Signal(8)

    def elaborate(self, platform):
        m = Module()
        m.submodules += [self._bridge, self._events, self._decoder]

        # connect bus
        connect(m, flipped(self.bus), self._decoder.bus)

        # connect events to irq line
        m.d.comb += [
            self.irq      .eq(self._events.src.i),
        ]

        # debug
        m.d.comb += [
            self.debug[0]  .eq(self._writer.f.a.w_stb & self._writer.f.a.w_data),
            self.debug[1]  .eq(self._writer.f.a.w_data),
            self.debug[2]  .eq(self._writer.f.b.w_stb & self._writer.f.b.w_data),
            self.debug[3]  .eq(self._writer.f.b.w_data),
            self.debug[4]  .eq(self._writer.f.c.w_stb & self._writer.f.c.w_data),
            self.debug[5]  .eq(self._writer.f.d.w_data),
            self.debug[6]  .eq(self._writer.f.d.w_stb & self._writer.f.d.w_data),
            self.debug[7]  .eq(self._writer.f.d.w_data),
        ]

        return m
