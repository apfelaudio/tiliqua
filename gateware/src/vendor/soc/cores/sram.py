# From lambdasoc, with some small updates
# TODO: consider returning to use upstream lambdasoc instead...
#
# Copyright (C) 2020 LambdaConcept
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:

# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.

# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from amaranth import *
from amaranth.lib import wiring, memory
from amaranth.lib.wiring import In
from amaranth.utils import exact_log2

from amaranth_soc import wishbone
from amaranth_soc.memory import MemoryMap

__all__ = ["SRAMPeripheralNew"]


class Peripheral(wiring.Component):
    """SRAM storage peripheral.

    Parameters
    ----------
    size : int
        Memory size in bytes.
    data_width : int
        Bus data width.
    granularity : int
        Bus granularity.
    writable : bool
        Memory is writable.

    Attributes
    ----------
    bus : :class:`amaranth_soc.wishbone.Interface`
        Wishbone bus interface.
    """
    # TODO raise bus.err if read-only and a bus write is attempted.
    def __init__(self, *, size, data_width=32, granularity=8, writable=True):
        if not isinstance(size, int) or size <= 0 or size & size-1:
            raise ValueError("Size must be an integer power of two, not {!r}"
                             .format(size))
        if size < data_width // granularity:
            raise ValueError("Size {} cannot be lesser than the data width/granularity ratio "
                             "of {} ({} / {})"
                              .format(size, data_width // granularity, data_width, granularity))

        size_words = (size * granularity) // data_width
        self._mem  = memory.Memory(depth=size_words, shape=data_width, init=[])

        super().__init__({
            "bus": In(wishbone.Signature(addr_width=exact_log2(size_words), data_width=data_width,
                                         granularity=granularity)),
        })

        memory_map = MemoryMap(addr_width=exact_log2(size), data_width=granularity)
        memory_map.add_resource(name=("memory",), size=size, resource=self)
        self.bus.memory_map = memory_map

        self.size = size
        self.granularity = granularity
        self.writable = writable

    @property
    def init(self):
        return self._mem.init

    @init.setter
    def init(self, init):
        self._mem.init = init

    def elaborate(self, platform):
        m = Module()
        m.submodules.mem = self._mem

        incr = Signal.like(self.bus.adr)

        mem_rp = self._mem.read_port()
        m.d.comb += self.bus.dat_r.eq(mem_rp.data)

        with m.If(self.bus.cyc & self.bus.stb):
            m.d.sync += self.bus.ack.eq(1)
            m.d.comb += mem_rp.addr.eq(self.bus.adr)

        with m.If(self.bus.ack):
            m.d.sync += self.bus.ack.eq(0)

        if self.writable:
            mem_wp = self._mem.write_port(granularity=self.granularity)
            m.d.comb += mem_wp.addr.eq(mem_rp.addr)
            m.d.comb += mem_wp.data.eq(self.bus.dat_w)
            with m.If(self.bus.cyc & self.bus.stb & self.bus.we):
                m.d.comb += mem_wp.en.eq(self.bus.sel)

        return m
