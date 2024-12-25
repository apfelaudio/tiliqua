# Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Utilities for simulating Tiliqua designs."""

import os
import shutil
import subprocess

from amaranth              import *
from amaranth.back         import verilog
from amaranth.build        import *
from amaranth.lib          import wiring, data
from amaranth.lib.wiring   import In, Out

from tiliqua.eurorack_pmod import ASQ
from tiliqua.types         import FirmwareLocation

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
        m.domains.fast   = ClockDomain()

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
class VerilatorPlatform():
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
        "clk_sync":       (ClockSignal("sync"),                          None),
        "rst_sync":       (ResetSignal("sync"),                          None),
        "clk_dvi":        (ClockSignal("dvi"),                           None),
        "rst_dvi":        (ResetSignal("dvi"),                           None),
        "clk_audio":      (ClockSignal("audio"),                         None),
        "rst_audio":      (ResetSignal("audio"),                         None),
        "uart0_w_data":   (fragment.uart0._tx_data.f.data.w_data,        None),
        "uart0_w_stb":    (fragment.uart0._tx_data.f.data.w_stb,         None),
        "address_ptr":    (fragment.psram_periph.simif.address_ptr,      None),
        "read_data_view": (fragment.psram_periph.simif.read_data_view,   None),
        "write_data":     (fragment.psram_periph.simif.write_data,       None),
        "read_ready":     (fragment.psram_periph.simif.read_ready,       None),
        "write_ready":    (fragment.psram_periph.simif.write_ready,      None),
        "spiflash_addr":  (fragment.spiflash_periph.spi_mmap.simif_addr, None),
        "spiflash_data":  (fragment.spiflash_periph.spi_mmap.simif_data, None),
        "dvi_x":          (fragment.video.dvi_tgen.x,                    None),
        "dvi_y":          (fragment.video.dvi_tgen.y,                    None),
        "dvi_r":          (fragment.video.phy_r,                         None),
        "dvi_g":          (fragment.video.phy_g,                         None),
        "dvi_b":          (fragment.video.phy_b,                         None),
        "fs_strobe":      (fragment.sim_fs_strobe,                       None),
    }

def simulate(fragment, ports, harness, hw_platform, tracing=False):

    build_dst = "build"
    dst = f"{build_dst}/tiliqua_soc.v"
    print(f"write verilog implementation of 'tiliqua_soc' to '{dst}'...")

    sim_platform = VerilatorPlatform(hw_platform)

    os.makedirs(build_dst, exist_ok=True)
    with open(dst, "w") as f:
        f.write(verilog.convert(
            fragment,
            platform=sim_platform,
            ports=ports
            ))

    # Write all additional files added with platform.add_file()
    # to build/ directory, so verilator build can find them.
    for file in sim_platform.files:
        with open(os.path.join("build", file), "w") as f:
            f.write(sim_platform.files[file])

    tracing_flags = ["--trace-fst", "--trace-structs"] if tracing else []

    if hasattr(fragment, "video"):
        # TODO: warn if this is far from the PLL output?
        dvi_clk_hz = int(fragment.video.dvi_tgen.timings.pll.pixel_clk_mhz * 1e6)
        dvi_h_active = fragment.video.dvi_tgen.timings.h_active
        dvi_v_active = fragment.video.dvi_tgen.timings.v_active
        video_cflags = [
           "-CFLAGS", f"-DDVI_H_ACTIVE={dvi_h_active}",
           "-CFLAGS", f"-DDVI_V_ACTIVE={dvi_v_active}",
           "-CFLAGS", f"-DDVI_CLK_HZ={dvi_clk_hz}",
        ]
    else:
        video_cflags = []

    if hasattr(fragment, "psram_periph"):
        psram_cflags = [
           "-CFLAGS", f"-DPSRAM_SIM=1",
       ]
    else:
        psram_cflags = []

    firmware_cflags = []
    if hasattr(fragment, "fw_location"):
        firmware_cflags += [
           "-CFLAGS", f"-DFIRMWARE_BIN_PATH=\\\"{fragment.firmware_bin_path}\\\"",
        ]
        match fragment.fw_location:
            case FirmwareLocation.PSRAM:
                firmware_cflags += [
                    "-CFLAGS", f"-DPSRAM_FW_OFFSET={hex(fragment.fw_base - fragment.psram_base)}",
                ]
            case FirmwareLocation.SPIFlash:
                firmware_cflags += [
                    "-CFLAGS", f"-DSPIFLASH_FW_OFFSET={hex(fragment.fw_base - fragment.spiflash_base)}",
                ]

    clock_sync_hz = 60000000
    audio_clk_hz = 48000000
    fast_clk_hz = 120000000

    verilator_dst = "build/obj_dir"
    shutil.rmtree(verilator_dst, ignore_errors=True)
    print(f"verilate '{dst}' into C++ binary...")
    subprocess.check_call(["verilator",
                           "-Wno-COMBDLY",
                           "-Wno-CASEINCOMPLETE",
                           "-Wno-CASEOVERLAP",
                           "-Wno-WIDTHEXPAND",
                           "-Wno-WIDTHTRUNC",
                           "-Wno-TIMESCALEMOD",
                           "-Wno-PINMISSING",
                           "-Wno-ASCRANGE",
                           "-Wno-UNSIGNED",
                           "-cc"] + tracing_flags + [
                           "--exe",
                           "--Mdir", f"{verilator_dst}",
                           "--build",
                           "-j", "0",
                           "-Ibuild",
                           "-CFLAGS", f"-DSYNC_CLK_HZ={clock_sync_hz}",
                           "-CFLAGS", f"-DAUDIO_CLK_HZ={audio_clk_hz}",
                           "-CFLAGS", f"-DFAST_CLK_HZ={fast_clk_hz}",
                          ] + video_cflags + psram_cflags + firmware_cflags + [
                           harness,
                           f"{dst}",
                          ] + [
                               f for f in sim_platform.files
                               if f.endswith(".svh") or f.endswith(".sv") or f.endswith(".v")
                          ],
                          env=os.environ)

    print(f"run verilated binary '{verilator_dst}/Vtiliqua_soc'...")
    subprocess.check_call([f"{verilator_dst}/Vtiliqua_soc"],
                          env=os.environ)

    print(f"done.")
