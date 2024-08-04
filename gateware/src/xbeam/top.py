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

from luna_soc.util.readbin                       import get_mem_data
from luna_soc                                    import top_level_cli
from luna_soc.gateware.csr.base                  import Peripheral

from tiliqua                                     import eurorack_pmod
from tiliqua.tiliqua_platform                    import TiliquaPlatform, set_environment_variables
from tiliqua.tiliqua_soc                         import TiliquaSoc

from example_vectorscope.top                     import Stroke

class VSPeripheral(Peripheral, Elaboratable):

    """
    Placeholder peripheral that bridges SoC memory space such that
    we can tweak vectorscope properties from Rust.
    """

    def __init__(self):

        super().__init__()

        self.persist           = Signal(16, reset=1024)
        self.hue               = Signal(8,  reset=0)
        self.intensity         = Signal(8,  reset=4)
        self.decay             = Signal(8,  reset=1)
        self.scale             = Signal(8,  reset=6)

        # CSRs
        bank                   = self.csr_bank()
        self._persist          = bank.csr(16, "w")
        self._hue              = bank.csr(8, "w")
        self._intensity        = bank.csr(8, "w")
        self._decay            = bank.csr(8, "w")
        self._scale            = bank.csr(8, "w")

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge

        with m.If(self._persist.w_stb):
            m.d.sync += self.persist.eq(self._persist.w_data)

        with m.If(self._hue.w_stb):
            m.d.sync += self.hue.eq(self._hue.w_data)

        with m.If(self._intensity.w_stb):
            m.d.sync += self.intensity.eq(self._intensity.w_data)

        with m.If(self._decay.w_stb):
            m.d.sync += self.decay.eq(self._decay.w_data)

        with m.If(self._scale.w_stb):
            m.d.sync += self.scale.eq(self._scale.w_data)

        return m

class XbeamSoc(TiliquaSoc):
    def __init__(self, *, firmware_path, dvi_timings):
        super().__init__(firmware_path=firmware_path, dvi_timings=dvi_timings, audio_192=True,
                         audio_out_peripheral=False)
        # scope stroke bridge from audio stream
        fb_size = (self.video.fb_hsize, self.video.fb_vsize)
        self.stroke = Stroke(
                fb_base=self.video.fb_base, bus_master=self.soc.psram.bus, fb_size=fb_size)
        self.soc.psram.add_master(self.stroke.bus)
        # scope controls
        self.vs_periph = VSPeripheral()
        self.soc.add_peripheral(self.vs_periph, addr=0xf0006000)

    def elaborate(self, platform):

        m = Module()

        m.submodules += super().elaborate(platform)

        pmod0 = self.pmod0_periph.pmod

        m.submodules.astream = astream = eurorack_pmod.AudioStream(pmod0)
        wiring.connect(m, astream.istream, self.stroke.i)

        m.submodules.stroke = self.stroke

        # Memory controller hangs if we start making requests to it straight away.
        # TODO collapse this into delay already present in super()
        on_delay = Signal(32)
        with m.If(on_delay < 0xFFFF):
            m.d.sync += on_delay.eq(on_delay+1)
        with m.Else():
            m.d.sync += self.stroke.enable.eq(1)

        m.d.comb += [
            self.persist.holdoff.eq(self.vs_periph.persist),
            self.persist.decay.eq(self.vs_periph.decay),
            self.stroke.hue.eq(self.vs_periph.hue),
            self.stroke.intensity.eq(self.vs_periph.intensity),
            self.stroke.scale.eq(self.vs_periph.scale),
        ]

        return m


if __name__ == "__main__":
    dvi_timings = set_environment_variables()
    this_directory = os.path.dirname(os.path.realpath(__file__))
    design = XbeamSoc(firmware_path=os.path.join(this_directory, "fw/firmware.bin"),
                        dvi_timings=dvi_timings)
    design.genrust_constants()
    top_level_cli(design)
