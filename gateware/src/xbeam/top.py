# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
Advanced version of `example_vectorscope` that includes an SoC and menu system.
Rasterizes X/Y (audio channel 0, 1) and color (audio channel 3) to a simulated CRT.
"""

import logging
import os
import sys

from amaranth                                    import *
from amaranth.hdl.rec                            import Record
from amaranth.lib                                import wiring, data

from amaranth_future                             import stream, fixed

from luna_soc.util.readbin                       import get_mem_data
from luna_soc                                    import top_level_cli
from luna_soc.gateware.csr.base                  import Peripheral

from tiliqua                                     import eurorack_pmod, dsp
from tiliqua.tiliqua_platform                    import TiliquaPlatform, set_environment_variables
from tiliqua.tiliqua_soc                         import TiliquaSoc

from example_vectorscope.top                     import Stroke

class VectorTracePeripheral(Peripheral, Elaboratable):

    def __init__(self, fb_base, fb_size, bus, **kwargs):

        super().__init__()

        self.stroke = Stroke (
                fb_base=fb_base, bus_master=bus.bus, fb_size=fb_size, **kwargs)
        bus.add_master(self.stroke.bus)

        self.i                 = self.stroke.i

        self.en                = Signal()
        self.soc_en            = Signal()

        bank                   = self.csr_bank()
        self._en               = bank.csr(1, "w")
        self._hue              = bank.csr(8, "w")
        self._intensity        = bank.csr(8, "w")
        self._xscale           = bank.csr(8, "w")
        self._yscale           = bank.csr(8, "w")

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge

        m.submodules += self.stroke

        m.d.comb += self.stroke.enable.eq(self.en & self.soc_en)

        with m.If(self._hue.w_stb):
            m.d.sync += self.stroke.hue.eq(self._hue.w_data)

        with m.If(self._intensity.w_stb):
            m.d.sync += self.stroke.intensity.eq(self._intensity.w_data)

        with m.If(self._xscale.w_stb):
            m.d.sync += self.stroke.scale_x.eq(self._xscale.w_data)

        with m.If(self._yscale.w_stb):
            m.d.sync += self.stroke.scale_y.eq(self._yscale.w_data)

        with m.If(self._en.w_stb):
            m.d.sync += self.soc_en.eq(self._en.w_data)

        return m

class ScopeTracePeripheral(Peripheral, Elaboratable):

    def __init__(self, fb_base, fb_size, bus):

        super().__init__()

        self.stroke0 = Stroke(
                fb_base=fb_base, bus_master=bus.bus, fb_size=fb_size, n_upsample=None)
        self.stroke1 = Stroke(
                fb_base=fb_base, bus_master=bus.bus, fb_size=fb_size, n_upsample=None)
        self.stroke2 = Stroke(
                fb_base=fb_base, bus_master=bus.bus, fb_size=fb_size, n_upsample=None)
        self.stroke3 = Stroke(
                fb_base=fb_base, bus_master=bus.bus, fb_size=fb_size, n_upsample=None)

        self.strokes = [self.stroke0, self.stroke1, self.stroke2, self.stroke3]

        self.isplit4 = dsp.Split(4)
        self.i = self.isplit4.i

        for s in self.strokes:
            bus.add_master(s.bus)
            bus.add_master(s.bus)
            bus.add_master(s.bus)
            bus.add_master(s.bus)

        self.en                = Signal()
        self.soc_en            = Signal()

        self.timebase          = Signal(shape=dsp.ASQ)
        self.trigger_lvl       = Signal(shape=dsp.ASQ)
        self.trigger_always    = Signal()

        bank                   = self.csr_bank()
        self._en               = bank.csr(1, "w")
        self._hue              = bank.csr(8,  "w")
        self._intensity        = bank.csr(8,  "w")
        self._timebase         = bank.csr(16, "w")
        self._yscale           = bank.csr(8,  "w")
        self._trigger_always   = bank.csr(1,  "w")
        self._trigger_lvl      = bank.csr(16, "w")
        self._ypos0            = bank.csr(16, "w")
        self._ypos1            = bank.csr(16, "w")
        self._ypos2            = bank.csr(16, "w")
        self._ypos3            = bank.csr(16, "w")

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge

        m.submodules += self.strokes

        for s in self.strokes:
            m.d.comb += s.enable.eq(self.en & self.soc_en)

        # Scope and trigger
        # Ch0 is routed through trigger, the rest are not.
        m.submodules.isplit4 = self.isplit4

        # 2 copies of input channel 0
        m.submodules.irep2   = irep2   = dsp.Split(2, replicate=True, source=self.isplit4.o[0])

        # Send one copy to trigger => ramp => X
        m.submodules.trig    = trig    = dsp.Trigger()
        m.submodules.ramp    = ramp    = dsp.Ramp()
        # Audio => Trigger
        dsp.connect_remap(m, irep2.o[0], trig.i, lambda o, i : [
            i.payload.sample   .eq(o.payload),
            i.payload.threshold.eq(self.trigger_lvl),
        ])
        # Trigger => Ramp
        dsp.connect_remap(m, trig.o, ramp.i, lambda o, i : [
            i.payload.trigger.eq(o.payload | self.trigger_always),
            i.payload.td.eq(self.timebase),
        ])

        # Split ramp into 4 streams, one for each channel
        m.submodules.rampsplit4 = rampsplit4 = dsp.Split(4, replicate=True, source=ramp.o)

        # Rasterize ch0: Ramp => X, Audio => Y
        m.submodules.ch0_merge4 = ch0_merge4 = dsp.Merge(4, sink=self.strokes[0].i)
        ch0_merge4.wire_valid(m, [2, 3])
        wiring.connect(m, rampsplit4.o[0], ch0_merge4.i[0])
        wiring.connect(m, irep2.o[1],      ch0_merge4.i[1])

        # Rasterize ch1-ch3: Ramp => X, Audio => Y
        for ch in [1, 2, 3]:
            ch_merge4 = dsp.Merge(4, sink=self.strokes[ch].i)
            m.submodules += ch_merge4
            ch_merge4.wire_valid(m, [2, 3])
            wiring.connect(m, rampsplit4.o[ch],   ch_merge4.i[0])
            wiring.connect(m, self.isplit4.o[ch], ch_merge4.i[1])

        # Wishbone tweakables

        with m.If(self._hue.w_stb):
            for ch, s in enumerate(self.strokes):
                m.d.sync += s.hue.eq(self._hue.w_data + ch*3)

        with m.If(self._intensity.w_stb):
            for s in self.strokes:
                m.d.sync += s.intensity.eq(self._intensity.w_data)

        with m.If(self._timebase.w_stb):
            m.d.sync += self.timebase.sas_value().eq(self._timebase.w_data)

        with m.If(self._yscale.w_stb):
            for s in self.strokes:
                m.d.sync += s.scale_y.eq(self._yscale.w_data)

        with m.If(self._trigger_lvl.w_stb):
            m.d.sync += self.trigger_lvl.sas_value().eq(self._trigger_lvl.w_data)

        with m.If(self._ypos0.w_stb):
            m.d.sync += self.strokes[0].y_offset.eq(self._ypos0.w_data)

        with m.If(self._ypos1.w_stb):
            m.d.sync += self.strokes[1].y_offset.eq(self._ypos1.w_data)

        with m.If(self._ypos2.w_stb):
            m.d.sync += self.strokes[2].y_offset.eq(self._ypos2.w_data)

        with m.If(self._ypos3.w_stb):
            m.d.sync += self.strokes[3].y_offset.eq(self._ypos3.w_data)

        with m.If(self._en.w_stb):
            m.d.sync += self.soc_en.eq(self._en.w_data)

        with m.If(self._trigger_always.w_stb):
            m.d.sync += self.trigger_always.eq(self._trigger_always.w_data)

        return m

class XbeamSoc(TiliquaSoc):
    def __init__(self, *, firmware_path, dvi_timings):
        super().__init__(firmware_path=firmware_path, dvi_timings=dvi_timings, audio_192=True,
                         audio_out_peripheral=False)
        # scope stroke bridge from audio stream
        fb_size = (self.video.fb_hsize, self.video.fb_vsize)

        self.vector_periph = VectorTracePeripheral(
            fb_base=self.video.fb_base,
            fb_size=fb_size,
            bus=self.soc.psram)
        self.soc.add_peripheral(self.vector_periph, addr=0xf0007000)

        self.scope_periph = ScopeTracePeripheral(
            fb_base=self.video.fb_base,
            fb_size=fb_size,
            bus=self.soc.psram)
        self.soc.add_peripheral(self.scope_periph, addr=0xf0008000)

    def elaborate(self, platform):

        m = Module()

        m.submodules += super().elaborate(platform)

        pmod0 = self.pmod0_periph.pmod

        m.submodules.astream = astream = eurorack_pmod.AudioStream(pmod0)

        self.scope_periph.source = astream.istream

        with m.If(self.scope_periph.soc_en):
            wiring.connect(m, astream.istream, self.scope_periph.i)
        with m.Else():
            wiring.connect(m, astream.istream, self.vector_periph.i)

        # Memory controller hangs if we start making requests to it straight away.
        with m.If(self.permit_bus_traffic):
            m.d.sync += self.vector_periph.en.eq(1)
            m.d.sync += self.scope_periph.en.eq(1)

        return m


if __name__ == "__main__":
    dvi_timings = set_environment_variables()
    this_directory = os.path.dirname(os.path.realpath(__file__))
    design = XbeamSoc(firmware_path=os.path.join(this_directory, "fw/firmware.bin"),
                        dvi_timings=dvi_timings)
    design.genrust_constants()
    top_level_cli(design)
