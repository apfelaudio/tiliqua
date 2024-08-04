# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import logging
import os

from amaranth                            import *
from amaranth.lib                        import wiring, data
from amaranth.lib.wiring                 import In, Out

from tiliqua                             import eurorack_pmod
from tiliqua.tiliqua_soc                 import TiliquaSoc
from tiliqua.tiliqua_platform            import set_environment_variables
from luna_soc                            import top_level_cli

class SID(wiring.Component):

    # TODO: check struct packing order

    clk:     In(1)
    bus_i:   In(data.StructLayout({
        "addr":  unsigned(5),
        "data":  unsigned(8),
        "phi2":  unsigned(1),
        "r_w_n": unsigned(1),
        "res":   unsigned(1),
        }))
    cs:      In(4)
    data_o:  Out(8)
    audio_o: Out(data.StructLayout({
        "left":  signed(24),
        "right": signed(24),
        }))

    def add_verilog_sources(self, platform):
        vroot = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                             "../../deps/reDIP-SID/gateware")

        # Include all files necessary for top-level 'sid_api.sv' to be instantiated.

        for file in ["sid_pkg.sv",
                     "sid_api.sv",
                     "sid_filter.sv",
                     "sid_voice.sv",
                     "sid_dac.sv",
                     "sid_pot.sv",
                     "sid_envelope.sv",
                     "sid_waveform.sv",
                     "sid_waveform_PST.svh",
                     "sid_waveform__ST.svh",
                     "sid_waveform_PS__6581.hex",
                     "sid_waveform_PS__8580.hex",
                     "sid_waveform_P_T_6581.hex",
                     "sid_waveform_P_T_8580.hex",
                     "dac_6581_envelope.hex",
                     "dac_6581_cutoff.hex",
                     "sid_control.sv",
                     "dac_6581_waveform.hex"]:
            platform.add_file(file, open(os.path.join(vroot, file)))

        # Exclude ICE40 muladd.sv, replace with a generic one that works on ECP5 --

        platform.add_file("muladd_ecp5.sv", """
            module muladd (
                input  logic signed [31:0] c,
                input  logic               s,
                input  logic signed [15:0] a,
                input  logic signed [15:0] b,
                output logic signed [31:0] o
            );

            always_comb begin
                if (s == 0)
                    o = c + (a*b);
                else
                    o = c - (a*b);
            end

            endmodule
        """)

    def elaborate(self, platform) -> Module:

        m = Module()

        self.add_verilog_sources(platform)

        m.submodules.vsid = Instance("sid_api",
            i_clk = ClockSignal("audio"),
            i_bus_i = self.bus_i,
            i_cs = self.cs,
            o_data_o = self.data_o,
            o_audio_o = self.audio_o,
        )

        return m

class SIDSoc(TiliquaSoc):
    def __init__(self, *, firmware_path, dvi_timings):
        super().__init__(firmware_path=firmware_path, dvi_timings=dvi_timings, audio_192=True,
                         audio_out_peripheral=False)

    def elaborate(self, platform):

        m = Module()

        m.submodules += super().elaborate(platform)

        pmod0 = self.pmod0_periph.pmod

        m.submodules.astream = astream = eurorack_pmod.AudioStream(pmod0)

        m.submodules.sid = sid = SID()

        return m

if __name__ == "__main__":
    dvi_timings = set_environment_variables()
    this_directory = os.path.dirname(os.path.realpath(__file__))
    design = SIDSoc(firmware_path=os.path.join(this_directory, "fw/firmware.bin"),
                    dvi_timings=dvi_timings)
    design.genrust_constants()
    top_level_cli(design)
