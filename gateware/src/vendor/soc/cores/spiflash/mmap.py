#
# This file is part of LUNA.
#
# Copyright (c) 2024 Great Scott Gadgets <info@greatscottgadgets.com>
# SPDX-License-Identifier: BSD-3-Clause

# Based on code from LiteSPI

from amaranth                           import Signal, Module, Cat, C, DomainRenamer
from amaranth.utils                     import log2_int
from amaranth.lib                       import wiring
from amaranth.lib.wiring                import In, Out, flipped, connect

from amaranth_soc                       import wishbone
from amaranth_soc.memory                import MemoryMap

from .port                              import SPIControlPort
from .utils                             import WaitTimer


class SPIFlashMemoryMap(wiring.Component):
    """Wishbone Memory-mapped SPI Flash controller.

    Supports sequential accesses so that command and address is only sent when necessary.
    """

    MMAP_DEFAULT_TIMEOUT = 256
    OE_MASK = {
        1: 0b00000001,
        2: 0b00000011,
        4: 0b00001111,
        8: 0b11111111,
    }

    def __init__(self, *, size, data_width=32, granularity=8, name=None, domain="sync", byteorder="little"):
        wiring.Component.__init__(self, SPIControlPort(data_width))

        self._name     = name
        self._size     = size
        self._domain   = domain
        self.byteorder = byteorder

        mem_depth      = (self._size * granularity) // data_width
        wb_addr_width  = log2_int(mem_depth)
        wb_data_width  = data_width
        mm_addr_width  = log2_int(self._size)
        mm_data_width  = granularity

        # self.bus = wishbone.Interface(
        #     addr_width=wb_addr_width,
        #     data_width=wb_data_width,
        #     granularity=granularity,
        # )

        map = MemoryMap(addr_width=mm_addr_width, data_width=mm_data_width)
        map.add_resource(self, name=self._name, size=self._size)


        super().__init__({
            "bus" : In(wishbone.Signature(
                addr_width=wb_addr_width,
                data_width=wb_data_width,
                granularity=granularity,
            )),
        })
        self.bus.memory_map = map

    @staticmethod
    def reverse_bytes(word):
        nbytes = len(word) // 8
        return Cat(word.word_select(nbytes - i - 1, 8) for i in range(nbytes))

    def elaborate(self, platform):
        m = Module()

        # Flash configuration.
        flash_read_opcode = 0xeb
        flash_cmd_bits    = 8
        flash_addr_bits   = 24
        flash_data_bits   = 32
        flash_cmd_width   = 1
        flash_addr_width  = 4
        flash_bus_width   = 4
        flash_dummy_bits  = 24
        flash_dummy_value = 0xff0000

        # Aliases.
        source = self.source
        sink   = self.sink
        cs     = self.cs
        bus    = self.bus

        # Burst Control.
        burst_cs      = Signal()
        burst_adr     = Signal(len(self.bus.adr), reset_less=True)
        burst_timeout = WaitTimer(self.MMAP_DEFAULT_TIMEOUT, domain=self._domain)
        m.submodules.burst_timeout = burst_timeout


        with m.FSM(domain=self._domain):
            with m.State("IDLE"):
                # Keep CS active after Burst for Timeout.
                m.d.comb += [
                    burst_timeout.wait.eq(1),
                    cs.eq(burst_cs),
                ]
                m.d.sync += burst_cs.eq(burst_cs & ~burst_timeout.done)
                # On Bus Read access...
                with m.If(bus.cyc & bus.stb & ~bus.we):
                    # If CS is still active and Bus address matches previous Burst address:
                    # Just continue the current Burst.
                    with m.If(burst_cs & (bus.adr == burst_adr)):
                        m.next = "BURST-REQ"
                    # Otherwise initialize a new Burst.
                    with m.Else():
                        m.d.comb += cs.eq(0)
                        m.next = "BURST-CMD"

            with m.State("BURST-CMD"):
                m.d.comb += [
                    cs              .eq(1),
                    source.valid    .eq(1),
                    source.data     .eq(flash_read_opcode), # send command.
                    source.len      .eq(flash_cmd_bits),
                    source.width    .eq(flash_cmd_width),
                    source.mask     .eq(self.OE_MASK[flash_cmd_width]),
                ]
                with m.If(source.ready):
                    m.next = "CMD-RET"

            with m.State("CMD-RET"):
                m.d.comb += [
                    cs              .eq(1),
                    sink.ready      .eq(1),
                ]
                with m.If(sink.valid):
                    m.next = "BURST-ADDR"

            with m.State("BURST-ADDR"):
                m.d.comb += [
                    cs              .eq(1),
                    source.valid    .eq(1),
                    source.width    .eq(flash_addr_width),
                    source.mask     .eq(self.OE_MASK[flash_addr_width]),
                    source.data     .eq(Cat(C(0, 2), bus.adr)), # send address.
                    source.len      .eq(flash_addr_bits),
                ]
                m.d.sync += [
                    burst_cs        .eq(1),
                    burst_adr       .eq(bus.adr),
                ]
                with m.If(source.ready):
                    m.next = "ADDR-RET"

            with m.State("ADDR-RET"):
                m.d.comb += [
                    cs              .eq(1),
                    sink.ready      .eq(1),
                ]
                with m.If(sink.valid):
                    with m.If(flash_dummy_bits == 0):
                        m.next = "BURST-REQ"
                    with m.Else():
                        m.next = "DUMMY"

            with m.State("DUMMY"):
                m.d.comb += [
                    cs              .eq(1),
                    source.valid    .eq(1),
                    source.width    .eq(flash_addr_width),
                    source.mask     .eq(self.OE_MASK[flash_addr_width]),
                    source.data     .eq(flash_dummy_value),
                    source.len      .eq(flash_dummy_bits),
                ]
                with m.If(source.ready):
                    m.next = "DUMMY-RET"

            with m.State("DUMMY-RET"):
                m.d.comb += [
                    cs              .eq(1),
                    sink.ready      .eq(1),
                ]
                with m.If(sink.valid):
                    m.next = "BURST-REQ"

            with m.State("BURST-REQ"):
                m.d.comb += [
                    cs              .eq(1),
                    source.valid    .eq(1),
                    source.width    .eq(flash_bus_width),
                    source.mask     .eq(0),
                    source.len      .eq(flash_data_bits),
                ]
                with m.If(source.ready):
                    m.next = "BURST-DAT"

            with m.State("BURST-DAT"):
                word = self.reverse_bytes(sink.data) if self.byteorder == "little" else sink.data
                m.d.comb += [
                    cs              .eq(1),
                    sink.ready      .eq(1),
                    bus.dat_r       .eq(word),
                ]
                with m.If(sink.valid):
                    m.d.comb += bus.ack.eq(1)
                    m.d.sync += burst_adr.eq(burst_adr + 1)
                    m.next = "IDLE"


        # Convert our sync domain to the domain requested by the user, if necessary.
        if self._domain != "sync":
            m = DomainRenamer({"sync": self._domain})(m)

        return m
