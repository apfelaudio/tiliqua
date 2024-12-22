# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
'Macro-Oscillator' runs a downsampled version of the DSP code from a
famous Eurorack module (credits below), on a softcore, to demonstrate the
compute capabilities available if you do everything in software.

All 24 engines are available for tweaking and patching via the UI.
A couple of engines use a bit more compute and may cause the UI to
slow down, however you should never get audio glitches.
A scope and vectorscope is included and hooked up to the oscillator
outputs so you can visualize exactly what the softcore is spitting out.

The original module was designed to run at 48kHz. Here, we instantiate
a powerful (rv32imafc) softcore (this one includes an FPU), which
is enough to run most engines at ~24kHz-48kHz, however with the video
and menu system running simultaneously, it's necessary to clock
this down to 12kHz. Surprisingly, most engines still sound reasonable.
The resampling from 12kHz <-> 48kHz is performed in hardware below.

Jack mapping:

    - In0: frequency modulation
    - In1: trigger
    - In2: timbre modulation
    - In3: morph modulation
    - Out2: 'out' output
    - Out3: 'aux' output

There is quite some heavy compute here and RAM usage, as a result,
the firmware and buffers are too big to fit in BRAM. In this demo,
the firmware is in memory-mapped SPI flash and the DSP buffers are
allocated from external PSRAM.

Credits to Emilie Gillet for the original Plaits module and firmware.

Credits to Oliver Rockstedt for the Rust port of said firmware:
    https://github.com/sourcebox/mi-plaits-dsp-rs

The Rust port is what is running on this softcore.
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


# Simple 2-fifo DMA peripheral for writing glitch-free audio from a softcore.
class AudioFIFOPeripheral(wiring.Component):

    class FifoLenReg(csr.Register, access="r"):
        fifo_len: csr.Field(csr.action.R, unsigned(16))

    def __init__(self, fifo_sz=4*4, fifo_data_width=32, granularity=8, elastic_sz=64*3):
        regs = csr.Builder(addr_width=6, data_width=8)

        # Out and Aux FIFOs
        self.elastic_sz = elastic_sz
        self._fifo0 = fifo.SyncFIFOBuffered(
            width=ASQ.as_shape().width, depth=elastic_sz)
        self._fifo1 = fifo.SyncFIFOBuffered(
            width=ASQ.as_shape().width, depth=elastic_sz)

        # Amount of elements in fifo0, used by softcore for scheduling.
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

        # Fixed memory region for the audio fifo rather than CSRs, so each 32-bit write
        # takes a single bus cycle (CSRs take longer).
        wb_memory_map = MemoryMap(addr_width=exact_log2(fifo_sz), data_width=granularity)
        wb_memory_map.add_resource(name=("audio_fifo",), size=fifo_sz, resource=self)
        self.wb_bus.memory_map = wb_memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge

        m.submodules._fifo0 = self._fifo0
        m.submodules._fifo1 = self._fifo1

        connect(m, flipped(self.csr_bus), self._bridge.bus)

        # Route writes to DMA region to audio FIFOs
        wstream0 = self._fifo0.w_stream
        wstream1 = self._fifo1.w_stream
        with m.If(self.wb_bus.cyc & self.wb_bus.stb & self.wb_bus.we):
            with m.Switch(self.wb_bus.adr):
                with m.Case(0):
                    m.d.comb += [
                        self.wb_bus.ack.eq(1),
                        wstream0.valid.eq(1),
                        wstream0.payload.eq(self.wb_bus.dat_w),
                    ]
                with m.Case(1):
                    m.d.comb += [
                        self.wb_bus.ack.eq(1),
                        wstream1.valid.eq(1),
                        wstream1.payload.eq(self.wb_bus.dat_w),
                    ]

        m.d.comb += self._fifo_len.f.fifo_len.r_data.eq(self._fifo0.level)

        # Resample 12kHz to 48kHz
        m.submodules.resample_up0 = resample_up0 = dsp.Resample(
                fs_in=12000, n_up=4, m_down=1)
        m.submodules.resample_up1 = resample_up1 = dsp.Resample(
                fs_in=12000, n_up=4, m_down=1)
        wiring.connect(m, self._fifo0.r_stream, resample_up0.i)
        wiring.connect(m, self._fifo1.r_stream, resample_up1.i)

        # Last 2 outputs
        m.submodules.merge = merge = dsp.Merge(4, wiring.flipped(self.stream))
        merge.wire_valid(m, [0, 1])
        wiring.connect(m, resample_up0.o, merge.i[2])
        wiring.connect(m, resample_up1.o, merge.i[3])

        return m

class MacroOscSoc(TiliquaSoc):

    brief = "Emulation of a famous Eurorack module."

    def __init__(self, **kwargs):

        # don't finalize the CSR bridge in TiliquaSoc, we're adding more peripherals.
        super().__init__(audio_192=False, audio_out_peripheral=False,
                         finalize_csr_bridge=False, mainram_size=0x10000,
                         cpu_variant="tiliqua_rv32imafc", **kwargs)

        # scope stroke bridge from audio stream
        fb_size = (self.video.fb_hsize, self.video.fb_vsize)

        # WARN: TiliquaSoc ends at 0x00000900
        self.vector_periph_base  = 0x00001000
        self.scope_periph_base   = 0x00001100
        self.audio_fifo_csr_base = 0x00001200
        # offset 0x0 is FIFO0, offset 0x4 is FIFO1
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

        # TODO: take this from parsed memory region list
        self.add_rust_constant(
            f"pub const AUDIO_FIFO_MEM_BASE: usize = 0x{self.audio_fifo_mem_base:x};\n")
        self.add_rust_constant(
            f"pub const AUDIO_FIFO_ELASTIC_SZ: usize = {self.audio_fifo.elastic_sz};\n")

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

        wiring.connect(m, self.audio_fifo.stream, astream.ostream)

        # Extra FIFO between audio out stream and plotting components
        # This FIFO does not block the audio stream.

        m.submodules.plot_fifo = plot_fifo = fifo.SyncFIFOBuffered(
            width=data.ArrayLayout(ASQ, 4).as_shape().width, depth=16)

        # Route audio outputs 2/3 to plotting stream (scope / vector)
        m.d.comb += [
            plot_fifo.w_stream.valid.eq(self.audio_fifo.stream.valid & astream.ostream.ready),
            plot_fifo.w_stream.payload[0:16] .eq(self.audio_fifo.stream.payload[2]),
            plot_fifo.w_stream.payload[16:32].eq(self.audio_fifo.stream.payload[3]),
        ]

        # Switch to use scope or vectorscope
        with m.If(self.scope_periph.soc_en):
            wiring.connect(m, plot_fifo.r_stream, self.scope_periph.i)
        with m.Else():
            wiring.connect(m, plot_fifo.r_stream, self.vector_periph.i)

        # Memory controller hangs if we start making requests to it straight away.
        with m.If(self.permit_bus_traffic):
            m.d.sync += self.vector_periph.en.eq(1)
            m.d.sync += self.scope_periph.en.eq(1)

        return m


if __name__ == "__main__":
    this_path = os.path.dirname(os.path.realpath(__file__))
    top_level_cli(MacroOscSoc, path=this_path)
