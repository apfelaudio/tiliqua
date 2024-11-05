# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Utilities for simulating Tiliqua designs."""

import os
import shutil
import subprocess

from amaranth              import *
from amaranth.back         import cxxrtl
from amaranth.build        import *
from amaranth.lib          import wiring, data
from amaranth.lib.wiring   import In, Out

from tiliqua.eurorack_pmod import ASQ

class FakeEurorackPmod(wiring.Component):
    """ Fake EurorackPmod. """

    fs_strobe: Out(1)
    sample_i:  Out(data.ArrayLayout(ASQ, 4))
    sample_o:   In(data.ArrayLayout(ASQ, 4))
    touch:     Out(8).array(8)
    jack:      Out(8)

    # simulation interface
    sample_inject:   In(ASQ).array(4)
    sample_extract: Out(ASQ).array(4)

    def elaborate(self, platform) -> Module:
        m = Module()

        for n in range(4):
            m.d.comb += self.sample_i[n].eq(self.sample_inject[n])
            m.d.comb += self.sample_extract[n].eq(self.sample_o[n])

        return m

class FakeTiliquaDomainGenerator(Elaboratable):
    """ Fake Clock generator for Tiliqua platform. """

    def __init__(self, *, clock_frequencies=None, clock_signal_name=None):
        pass

    def elaborate(self, platform):
        m = Module()

        m.domains.sync   = ClockDomain()
        m.domains.audio  = ClockDomain()
        m.domains.dvi    = ClockDomain()

        return m

class FakePSRAMSimulationInterface(wiring.Signature):
    def __init__(self):
        super().__init__({
            "idle":           Out(unsigned(1)),
            "read_ready":     Out(unsigned(1)),
            "write_ready":    Out(unsigned(1)),
            "address_ptr":    Out(unsigned(32)),
            "read_data_view":  In(unsigned(32)),
            "write_data":     Out(unsigned(32)),
        })

# Main purpose of using this custom platform instead of
# simply None is to track extra files added to the build.
class CxxRtlPlatform():
    def __init__(self, hw_platform):
        self.files = {}
        self.ila = False
        self.psram_id = hw_platform.psram_id
        self.psram_registers = hw_platform.psram_registers

    def add_file(self, file_name, contents):
        self.files[file_name] = contents

def is_hw(platform):
    # assumption: anything that inherits from Platform is a
    # real hardware platform. Anything else isn't.
    # is there a better way of doing this?
    return isinstance(platform, Platform)

def soc_simulation_ports(fragment):
    return {
        "clk_sync":       (ClockSignal("sync"),                        None),
        "rst_sync":       (ResetSignal("sync"),                        None),
        "clk_dvi":        (ClockSignal("dvi"),                         None),
        "rst_dvi":        (ResetSignal("dvi"),                         None),
        "uart0_w_data":   (fragment.uart0._tx_data.f.data.w_data,      None),
        "uart0_w_stb":    (fragment.uart0._tx_data.f.data.w_stb,       None),
        "address_ptr":    (fragment.psram_periph.simif.address_ptr,    None),
        "read_data_view": (fragment.psram_periph.simif.read_data_view, None),
        "write_data":     (fragment.psram_periph.simif.write_data,     None),
        "read_ready":     (fragment.psram_periph.simif.read_ready,     None),
        "write_ready":    (fragment.psram_periph.simif.write_ready,    None),
        "dvi_x":          (fragment.video.dvi_tgen.x,                  None),
        "dvi_y":          (fragment.video.dvi_tgen.y,                  None),
        "dvi_r":          (fragment.video.phy_r,                       None),
        "dvi_g":          (fragment.video.phy_g,                       None),
        "dvi_b":          (fragment.video.phy_b,                       None),
    }

def simulate(fragment, ports, harness, hw_platform, tracing=False):

    build_dst = "build"
    dst = f"{build_dst}/tiliqua_soc.cpp"
    print(f"cxxrtl: elaborate design to '{dst}'...")

    sim_platform = CxxRtlPlatform(hw_platform)

    os.makedirs(build_dst, exist_ok=True)
    with open(dst, "w") as f:
        f.write(cxxrtl.convert(
            fragment,
            platform=sim_platform,
            ports=ports
            ))

    # Write all additional files added with platform.add_file()
    # to build/ directory, so verilator build can find them.
    for file in sim_platform.files:
        with open(os.path.join("build", file), "w") as f:
            f.write(sim_platform.files[file])

    if hasattr(fragment, "video"):
        # TODO: warn if this is far from the PLL output?
        dvi_clk_hz = int(fragment.video.dvi_tgen.timings.pll.pixel_clk_mhz * 1e6)
        dvi_h_active = fragment.video.dvi_tgen.timings.h_active
        dvi_v_active = fragment.video.dvi_tgen.timings.v_active
        video_cflags = [
           f"-DDVI_H_ACTIVE={dvi_h_active}",
           f"-DDVI_V_ACTIVE={dvi_v_active}",
           f"-DDVI_CLK_HZ={dvi_clk_hz}",
        ]
    else:
        video_cflags = []

    if hasattr(fragment, "psram_periph"):
        psram_cflags = [
           "-DPSRAM_SIM=1",
       ]
    else:
        psram_cflags = []

    clock_sync_hz = 60000000
    audio_clk_hz = 48000000

    tracing_flags = ["-DTRACE_VCD"] if tracing else []

    yosys_dat_dir = subprocess.check_output(
            ['yosys-config', '--datdir'], env=os.environ).strip().decode()
    yosys_include = os.path.join(
            yosys_dat_dir, "include/backends/cxxrtl/runtime")

    print(f"compile '{dst}' into C++ binary...")
    subprocess.check_call(["clang++",
                           "-g",
                           "-O3",
                           "-std=c++14",
                           "-Wno-array-bounds",
                           "-Wno-shift-count-overflow",
                           f"-I{yosys_include}",
                           f"-Ibuild",
                           f"-DAUDIO_CLK_HZ={audio_clk_hz}",
                           f"-DSYNC_CLK_HZ={clock_sync_hz}",
                          ] + tracing_flags + video_cflags + psram_cflags + [
                            harness,
                           "-o", f"{build_dst}/tb_tiliqua_soc",
                          ],
                           env=os.environ)

    print(f"run binary {build_dst}/tb_tiliqua_soc ...")
    subprocess.check_call([f"{build_dst}/tb_tiliqua_soc"],
                          env=os.environ)

    print(f"done.")
