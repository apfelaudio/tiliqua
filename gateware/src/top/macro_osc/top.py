# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
Vectorscope and 4-channel oscilloscope with menu system.

In vectorscope mode, rasterize X/Y (audio channel 0, 1) and
color (audio channel 3) to a simulated CRT.

In oscilloscope mode, all 4 input channels are plotted simultaneosly
in classic oscilloscope fashion.
"""

import logging
import os
import sys

from amaranth                                    import *
from amaranth.lib                                import wiring, data, stream, fifo
from amaranth.lib.wiring                         import In, Out, flipped, connect
from amaranth.utils       import exact_log2

from amaranth_soc                                import csr
from amaranth_soc         import wishbone
from amaranth_soc.memory  import MemoryMap

from amaranth_future                             import fixed

from tiliqua                                     import eurorack_pmod, dsp, scope
from tiliqua.tiliqua_soc                         import TiliquaSoc
from tiliqua.cli                                 import top_level_cli

from tiliqua.eurorack_pmod                       import ASQ


class AudioFIFOPeripheral(wiring.Component):

    class FifoLenReg(csr.Register, access="r"):
        fifo_len: csr.Field(csr.action.R, unsigned(16))

    def __init__(self, fifo_sz=4*4, fifo_data_width=32, granularity=8, elastic_sz=1024):
        regs = csr.Builder(addr_width=6, data_width=8)

        self._fifo0 = fifo.SyncFIFOBuffered(
            width=ASQ.as_shape().width, depth=elastic_sz)

        self._fifo_len = regs.add(f"fifo_len", self.FifoLenReg(), offset=0x4)

        self._bridge = csr.Bridge(regs.as_memory_map())

        mem_depth  = (fifo_sz * granularity) // fifo_data_width
        super().__init__({
            "csr_bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
            "wb_bus":  In(wishbone.Signature(addr_width=exact_log2(mem_depth),
                                             data_width=fifo_data_width,
                                             granularity=granularity)),
            "stream": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
        })

        self.csr_bus.memory_map = self._bridge.bus.memory_map

        wb_memory_map = MemoryMap(addr_width=exact_log2(fifo_sz), data_width=granularity)
        wb_memory_map.add_resource(name=("audio_fifo",), size=fifo_sz, resource=self)
        self.wb_bus.memory_map = wb_memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge

        m.submodules._fifo0 = self._fifo0

        connect(m, flipped(self.csr_bus), self._bridge.bus)

        wstream = self._fifo0.w_stream
        with m.If(self.wb_bus.cyc & self.wb_bus.stb & self.wb_bus.we):
            with m.Switch(self.wb_bus.adr):
                with m.Case(0):
                    m.d.comb += [
                        self.wb_bus.ack.eq(1),
                        wstream.valid.eq(1),
                        wstream.payload.eq(self.wb_bus.dat_w),
                    ]

        m.d.comb += self._fifo_len.f.fifo_len.r_data.eq(self._fifo0.level)

        # Resample 12kHz to 48kHz
        m.submodules.resample_up0 = resample_up0 = dsp.Resample(
                fs_in=12000, n_up=4, m_down=1)
        wiring.connect(m, self._fifo0.r_stream, resample_up0.i)

        m.submodules.merge = merge = dsp.Merge(4, wiring.flipped(self.stream))
        wiring.connect(m, resample_up0.o, merge.i[0])
        merge.wire_valid(m, [1, 2, 3])

        return m

class MacroOscSoc(TiliquaSoc):
    def __init__(self, **kwargs):

        # don't finalize the CSR bridge in TiliquaSoc, we're adding more peripherals.
        super().__init__(audio_192=False, audio_out_peripheral=False,
                         finalize_csr_bridge=False, **kwargs)

        # scope stroke bridge from audio stream
        fb_size = (self.video.fb_hsize, self.video.fb_vsize)

        # WARN: TiliquaSoc ends at 0x00000900
        self.vector_periph_base  = 0x00001000
        self.scope_periph_base   = 0x00001100
        self.audio_fifo_csr_base = 0x00001200
        self.audio_fifo_mem_base = 0xa0000000

        self.vector_periph = scope.VectorTracePeripheral(
            fb_base=self.video.fb_base,
            fb_size=fb_size,
            bus_dma=self.psram_periph,
            video_rotate_90=self.video_rotate_90)
        self.csr_decoder.add(self.vector_periph.bus, addr=self.vector_periph_base, name="vector_periph")

        self.scope_periph = scope.ScopeTracePeripheral(
            fb_base=self.video.fb_base,
            fb_size=fb_size,
            bus_dma=self.psram_periph,
            video_rotate_90=self.video_rotate_90)
        self.csr_decoder.add(self.scope_periph.bus, addr=self.scope_periph_base, name="scope_periph")

        self.audio_fifo = AudioFIFOPeripheral()
        self.csr_decoder.add(self.audio_fifo.csr_bus, addr=self.audio_fifo_csr_base, name="audio_fifo")
        self.wb_decoder.add(self.audio_fifo.wb_bus, addr=self.audio_fifo_mem_base, name="audio_fifo")

        # now we can freeze the memory map
        self.finalize_csr_bridge()

    def elaborate(self, platform):

        m = Module()

        m.submodules += self.vector_periph

        m.submodules += self.scope_periph

        m.submodules += self.audio_fifo

        m.submodules += super().elaborate(platform)

        pmod0 = self.pmod0_periph.pmod

        m.submodules.astream = astream = eurorack_pmod.AudioStream(pmod0, fifo_depth=8)

        self.scope_periph.source = astream.istream

        with m.If(self.scope_periph.soc_en):
            wiring.connect(m, astream.istream, self.scope_periph.i)
        with m.Else():
            wiring.connect(m, astream.istream, self.vector_periph.i)


        wiring.connect(m, self.audio_fifo.stream, astream.ostream)

        # Memory controller hangs if we start making requests to it straight away.
        with m.If(self.permit_bus_traffic):
            m.d.sync += self.vector_periph.en.eq(1)
            m.d.sync += self.scope_periph.en.eq(1)

        return m


if __name__ == "__main__":
    this_path = os.path.dirname(os.path.realpath(__file__))
    top_level_cli(MacroOscSoc, path=this_path,
                  argparse_fragment=lambda _: {"mainram_size": 0x20000})
