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
from amaranth.lib                                import wiring, data, stream
from amaranth.lib.wiring                         import In, Out, flipped, connect

from amaranth_soc                                import csr

from amaranth_future                             import fixed

from tiliqua                                     import eurorack_pmod, dsp, scope
from tiliqua.tiliqua_soc                         import TiliquaSoc
from tiliqua.cli                                 import top_level_cli

from tiliqua.eurorack_pmod                       import ASQ


class AudioFIFOPeripheral(wiring.Component):

    class OSampleReg(csr.Register, access="w"):
        sample: csr.Field(csr.action.W, unsigned(32))

    class FifoLenReg(csr.Register, access="r"):
        fifo_len: csr.Field(csr.action.R, unsigned(16))

    def __init__(self):
        regs = csr.Builder(addr_width=6, data_width=8)

        self._sample_o = regs.add(f"sample", self.OSampleReg(), offset=0x0)
        self._fifo_len = regs.add(f"fifo_len", self.FifoLenReg(), offset=0x4)

        self._bridge = csr.Bridge(regs.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
            "stream": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "fifo_len": In(unsigned(16)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge

        connect(m, flipped(self.bus), self._bridge.bus)

        m.d.sync += self.stream.valid.eq(0),
        with m.If(self._sample_o.f.sample.w_stb):
            m.d.sync += [
                self.stream.valid.eq(1),
                self.stream.payload[0].eq(self._sample_o.f.sample.w_data[ 0:16]),
                self.stream.payload[1].eq(self._sample_o.f.sample.w_data[16:32]),
            ]

        m.d.comb += self._fifo_len.f.fifo_len.r_data.eq(self.fifo_len)

        return m

class MacroOscSoc(TiliquaSoc):
    def __init__(self, **kwargs):

        # don't finalize the CSR bridge in TiliquaSoc, we're adding more peripherals.
        super().__init__(audio_192=False, audio_out_peripheral=False,
                         finalize_csr_bridge=False, **kwargs)

        # scope stroke bridge from audio stream
        fb_size = (self.video.fb_hsize, self.video.fb_vsize)

        # WARN: TiliquaSoc ends at 0x00000900
        self.vector_periph_base = 0x00001000
        self.scope_periph_base  = 0x00001100
        self.audio_fifo_base    = 0x00001200

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
        self.csr_decoder.add(self.audio_fifo.bus, addr=self.audio_fifo_base, name="audio_fifo")

        # now we can freeze the memory map
        self.finalize_csr_bridge()

    def elaborate(self, platform):

        m = Module()

        m.submodules += self.vector_periph

        m.submodules += self.scope_periph

        m.submodules += self.audio_fifo

        m.submodules += super().elaborate(platform)

        pmod0 = self.pmod0_periph.pmod

        m.submodules.astream = astream = eurorack_pmod.AudioStream(pmod0, fifo_depth=2048)

        self.scope_periph.source = astream.istream

        with m.If(self.scope_periph.soc_en):
            wiring.connect(m, astream.istream, self.scope_periph.i)
        with m.Else():
            wiring.connect(m, astream.istream, self.vector_periph.i)

        """
        m.d.comb += [
            astream.ostream.valid.eq(astream.istream.valid & astream.istream.ready),
            astream.ostream.payload.eq(astream.istream.payload),
        ]
        """

        wiring.connect(m, self.audio_fifo.stream, astream.ostream)
        m.d.comb += self.audio_fifo.fifo_len.eq(astream.dac_fifo.w_level)

        # Memory controller hangs if we start making requests to it straight away.
        with m.If(self.permit_bus_traffic):
            m.d.sync += self.vector_periph.en.eq(1)
            m.d.sync += self.scope_periph.en.eq(1)

        return m


if __name__ == "__main__":
    this_path = os.path.dirname(os.path.realpath(__file__))
    top_level_cli(MacroOscSoc, path=this_path,
                  argparse_fragment=lambda _: {"mainram_size": 0x20000})
