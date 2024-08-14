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

    def __init__(self, fb_base, fb_size, bus):

        super().__init__()

        self.stroke = Stroke(
                fb_base=fb_base, bus_master=bus.bus, fb_size=fb_size)
        bus.add_master(self.stroke.bus)

        self.i                 = self.stroke.i
        self.en                = Signal()

        bank                   = self.csr_bank()
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

        m.d.comb += self.stroke.enable.eq(self.en)

        with m.If(self._hue.w_stb):
            m.d.sync += self.stroke.hue.eq(self._hue.w_data)

        with m.If(self._intensity.w_stb):
            m.d.sync += self.stroke.intensity.eq(self._intensity.w_data)

        with m.If(self._xscale.w_stb):
            m.d.sync += self.stroke.scale_x.eq(self._xscale.w_data)

        with m.If(self._yscale.w_stb):
            m.d.sync += self.stroke.scale_y.eq(self._yscale.w_data)

        return m

class ScopeTracePeripheral(Peripheral, Elaboratable):

    def __init__(self, fb_base, fb_size, bus):

        super().__init__()

        self.stroke = Stroke(
                fb_base=fb_base, bus_master=bus.bus, fb_size=fb_size, n_upsample=None)
        bus.add_master(self.stroke.bus)

        self.source            = None
        self.en                = Signal()

        self.timebase          = Signal(shape=dsp.ASQ)

        bank                   = self.csr_bank()
        self._hue              = bank.csr(8,  "w")
        self._intensity        = bank.csr(8,  "w")
        self._timebase         = bank.csr(16, "w")
        self._yscale           = bank.csr(8,  "w")
        self._ypos             = bank.csr(16, "w")

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge

        m.submodules += self.stroke

        m.d.comb += self.stroke.enable.eq(self.en)

        # Scope and trigger
        m.submodules.isplit4 = isplit4 = dsp.Split(4, source=self.source)
        isplit4.wire_ready(m, [1, 2, 3])

        # 2 copies of input channel 0
        m.submodules.irep2   = irep2   = dsp.Split(2, replicate=True, source=isplit4.o[0])

        # Send one copy to trigger => ramp => X
        m.submodules.trig    = trig    = dsp.Trigger()
        m.submodules.ramp    = ramp    = dsp.Ramp()
        # Audio => Trigger
        dsp.connect_remap(m, irep2.o[0], trig.i, lambda o, i : [
            i.payload.sample   .eq(o.payload),
            i.payload.threshold.eq(fixed.Const(0.0, shape=dsp.ASQ)),
        ])
        # Trigger => Ramp
        dsp.connect_remap(m, trig.o, ramp.i, lambda o, i : [
            i.payload.trigger.eq(o.payload),
            i.payload.td.eq(self.timebase),
        ])
        # Rasterize: Ramp => X, Audio => Y
        m.submodules.merge4 = merge4 = dsp.Merge(4, sink=self.stroke.i)
        merge4.wire_valid(m, [2, 3])
        wiring.connect(m, ramp.o,     merge4.i[0])
        wiring.connect(m, irep2.o[1], merge4.i[1])

        with m.If(self._hue.w_stb):
            m.d.sync += self.stroke.hue.eq(self._hue.w_data)

        with m.If(self._intensity.w_stb):
            m.d.sync += self.stroke.intensity.eq(self._intensity.w_data)

        with m.If(self._timebase.w_stb):
            m.d.sync += self.timebase.sas_value().eq(self._timebase.w_data)

        with m.If(self._yscale.w_stb):
            m.d.sync += self.stroke.scale_y.eq(self._yscale.w_data)

        with m.If(self._ypos.w_stb):
            m.d.sync += self.stroke.y_offset.eq(self._ypos.w_data)

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

        #wiring.connect(m, astream.istream, self.vector_periph.i)

        # Memory controller hangs if we start making requests to it straight away.
        with m.If(self.permit_bus_traffic):
            #m.d.sync += self.vector_periph.en.eq(1)
            m.d.sync += self.scope_periph.en.eq(1)

        return m


if __name__ == "__main__":
    dvi_timings = set_environment_variables()
    this_directory = os.path.dirname(os.path.realpath(__file__))
    design = XbeamSoc(firmware_path=os.path.join(this_directory, "fw/firmware.bin"),
                        dvi_timings=dvi_timings)
    design.genrust_constants()
    top_level_cli(design)
