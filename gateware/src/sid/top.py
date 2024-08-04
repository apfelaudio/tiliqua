# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import logging
import os

from amaranth                            import *
from amaranth.lib                        import wiring, data
from amaranth.lib.fifo                   import SyncFIFO
from amaranth.lib.wiring                 import In, Out

from tiliqua                             import eurorack_pmod
from tiliqua.tiliqua_soc                 import TiliquaSoc
from tiliqua.tiliqua_platform            import set_environment_variables

from luna_soc                            import top_level_cli
from luna_soc.gateware.csr.base          import Peripheral

# for simulation
from tiliqua.video                       import DVI_TIMINGS
from amaranth.back                       import verilog

class SID(wiring.Component):

    clk:     In(1)
    bus_i:   In(data.StructLayout({
        "res":   unsigned(1),
        "r_w_n": unsigned(1),
        "phi2":  unsigned(1),
        "data":  unsigned(8),
        "addr":  unsigned(5),
        }))
    cs:      In(4)
    data_o:  Out(8)
    audio_o: Out(data.StructLayout({
        "right": signed(24),
        "left":  signed(24),
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

        # rough usage
        # - i_clk must be >20x phi2 clk (on bus_i)
        # - falling edge of phi2 starts internal pipeline, takes ~20 cycles
        # procedure:
        # - bring phi2 low for ~12 cycles
        # - bring phi2 high for ~12 cycles
        # - before next phi2 low:
        #   - save latest audio sample
        #   - maybe write to sid register using bus_i (keep data there until next clock)

        m.submodules.vsid = Instance("sid_api",
            i_clk     = ClockSignal("sync"),
            i_bus_i   = self.bus_i,
            i_cs      = self.cs,
            o_data_o  = self.data_o,
            o_audio_o = self.audio_o,
        )

        return m

class SIDPeripheral(Peripheral, Elaboratable):
    def __init__(self, *, transaction_depth=16):
        super().__init__()

        self.transaction_width = 16
        self._transactions = SyncFIFO(width=self.transaction_width,
                                      depth=transaction_depth)

        # CSRs
        bank                   = self.csr_bank()
        self._transaction_data = bank.csr(self.transaction_width, "w")

        self.sid = None

        # audio
        self.last_audio_left  = Signal(signed(24))
        self.last_audio_right = Signal(signed(24))

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge
        m.submodules.transactions = self._transactions

        # CSRs -> Transactions FIFO
        m.d.comb += [
            self._transactions.w_en      .eq(self._transaction_data.w_stb),
            self._transactions.w_data    .eq(self._transaction_data.w_data),
        ]

        DIVIDE_BY = 60 # sync clk / 60 should be ~1MHz. TODO generate this constant
        phi2_clk_counter = Signal(8)
        with m.If(phi2_clk_counter != DIVIDE_BY-1):
            m.d.sync += phi2_clk_counter.eq(phi2_clk_counter + 1)
        with m.Else():
            m.d.sync += phi2_clk_counter.eq(0)

        phi2 = Signal()
        phi2_edge = Signal()
        m.d.comb += [
            phi2.eq(phi2_clk_counter > int(DIVIDE_BY/2)),
            phi2_edge.eq(phi2_clk_counter == (DIVIDE_BY-1))
        ]

        # 'always' signals
        m.d.sync += [
            self.sid.bus_i.phi2  .eq(phi2),
            self.sid.cs          .eq(0b0100), # cs_n = 0, cs_io1_n = 1
        ]

        startup = Signal(8)

        # route FIFO'd transactions -> SID
        m.d.sync += self._transactions.r_en.eq(0)
        with m.If(phi2_edge):

            # TODO verify
            with m.If(startup < 24):
                m.d.sync += startup.eq(startup+1)
                m.d.sync += self.sid.bus_i.res.eq(1)
            with m.Else():
                m.d.sync += self.sid.bus_i.res.eq(0)

            m.d.sync += [
                # maybe consume 1 transaction, set as W instead of R if nothing is pending
                self._transactions.r_en.eq(1),
                self.sid.bus_i.r_w_n .eq(self._transactions.level == 0),
                self.sid.bus_i.addr  .eq(self._transactions.r_data),
                self.sid.bus_i.data  .eq(self._transactions.r_data >> 5),
                # audio signals
                self.last_audio_left .eq(self.sid.audio_o.left),
                self.last_audio_right.eq(self.sid.audio_o.right),
            ]

        return m

class SIDSoc(TiliquaSoc):
    def __init__(self, *, firmware_path, dvi_timings, sim=False):
        super().__init__(firmware_path=firmware_path, dvi_timings=dvi_timings, audio_192=False,
                         audio_out_peripheral=False, sim=sim)
        self.sid_periph = SIDPeripheral()
        self.soc.add_peripheral(self.sid_periph, addr=0xf0006000)

    def elaborate(self, platform):

        m = Module()

        m.submodules += super().elaborate(platform)

        pmod0 = self.pmod0_periph.pmod

        m.submodules.astream = astream = eurorack_pmod.AudioStream(pmod0)

        m.submodules.sid = sid = SID()

        self.sid_periph.sid = sid

        m.d.comb += [
            astream.ostream.valid.eq(1),
            astream.ostream.payload[0].sas_value().eq(self.sid_periph.last_audio_left>>8),
            astream.ostream.payload[1].sas_value().eq(self.sid_periph.last_audio_right>>8),
            astream.ostream.payload[2].eq(0),
            astream.ostream.payload[3].eq(0),
        ]

        return m

if __name__ == "__main__":
    dvi_timings = set_environment_variables()
    this_directory = os.path.dirname(os.path.realpath(__file__))
    design = SIDSoc(firmware_path=os.path.join(this_directory, "fw/firmware.bin"),
                    dvi_timings=dvi_timings)
    design.genrust_constants()
    top_level_cli(design)

class SimPlatform():
    def __init__(self):
        self.files = []
        pass
    def add_file(self, file_name, contents):
        self.files.append(file_name)

def sim():
    """
    End-to-end simulation of all the gateware in this project.
    """

    import subprocess

    build_dst = "build"
    dst = f"{build_dst}/sid_soc.v"
    print(f"write verilog implementation of 'sid_soc' to '{dst}'...")

    this_directory = os.path.dirname(os.path.realpath(__file__))
    top = SIDSoc(firmware_path=os.path.join(this_directory, "fw/firmware.bin"),
                 dvi_timings=DVI_TIMINGS["1280x720p60"],
                 sim=True)

    sim_platform = SimPlatform()

    os.makedirs(build_dst, exist_ok=True)
    with open(dst, "w") as f:
        f.write(verilog.convert(top, platform=sim_platform, ports=[
            ClockSignal("sync"),
            ResetSignal("sync"),
            ClockSignal("dvi"),
            ResetSignal("dvi"),
            ClockSignal("audio"),
            ResetSignal("audio"),
            top.soc.psram.psram.idle,
            top.soc.psram.psram.address_ptr,
            top.soc.psram.psram.read_data_view,
            top.soc.psram.psram.write_data,
            top.soc.psram.psram.read_ready,
            top.soc.psram.psram.write_ready,
            top.video.dvi_tgen.x,
            top.video.dvi_tgen.y,
            top.video.phy_r,
            top.video.phy_g,
            top.video.phy_b,
            top.pmod0.fs_strobe,
            top.inject0,
            top.inject1,
            top.inject2,
            top.inject3,
            ]))

    # TODO: warn if this is far from the PLL output?
    dvi_clk_hz = int(top.video.dvi_tgen.timings.pll.pixel_clk_mhz * 1e6)
    dvi_h_active = top.video.dvi_tgen.timings.h_active
    dvi_v_active = top.video.dvi_tgen.timings.v_active
    sync_clk_hz = 60000000
    audio_clk_hz = 48000000

    print(sim_platform.files)

    verilator_dst = "build/obj_dir"
    print(f"verilate '{dst}' into C++ binary...")
    subprocess.check_call(["verilator",
                           "-Wno-COMBDLY",
                           "-Wno-CASEINCOMPLETE",
                           "-Wno-CASEOVERLAP",
                           "-Wno-WIDTHEXPAND",
                           "-Wno-WIDTHTRUNC",
                           "-Wno-TIMESCALEMOD",
                           "-Wno-PINMISSING",
                           "-cc",
                           "--trace-fst",
                           "--exe",
                           "--Mdir", f"{verilator_dst}",
                           "--build",
                           "-j", "0",
                           "-Ibuild",
                           "-CFLAGS", f"-DDVI_H_ACTIVE={dvi_h_active}",
                           "-CFLAGS", f"-DDVI_V_ACTIVE={dvi_v_active}",
                           "-CFLAGS", f"-DDVI_CLK_HZ={dvi_clk_hz}",
                           "-CFLAGS", f"-DSYNC_CLK_HZ={sync_clk_hz}",
                           "-CFLAGS", f"-DAUDIO_CLK_HZ={audio_clk_hz}",
                           "../../src/example_vectorscope/sim/sim.cpp",
                           f"{dst}",
                           ] + [
                               f for f in sim_platform.files
                               if f.endswith(".svh") or f.endswith(".sv") or f.endswith(".v")
                           ],
                          env=os.environ)

    print(f"run verilated binary '{verilator_dst}/Vsid_soc'...")
    subprocess.check_call([f"{verilator_dst}/Vsid_soc"],
                          env=os.environ)

    print(f"done.")
