# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
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

class FakeEurorackPmod(Elaboratable):
    """ Fake EurorackPmod. """

    def __init__(self):
        self.sample_i = Signal(data.ArrayLayout(ASQ, 4))
        self.sample_o = Signal(data.ArrayLayout(ASQ, 4))
        self.sample_inject  = [Signal(ASQ) for _ in range(4)]
        self.sample_extract = [Signal(ASQ) for _ in range(4)]
        self.fs_strobe = Signal()

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

class FakePSRAM(wiring.Component):

    """
    Fake PSRAM used for simulation.

    This is just HyperRAMDQSInterface with the dependency
    on the PHY removed and extra signals added for memory
    injection/instrumentation, such that it is possible
    to simulate an SoC against the true RAM timings.
    """

    HIGH_LATENCY_CLOCKS = 5


    def __init__(self):
        self.reset            = Signal()
        self.address          = Signal(32)
        self.register_space   = Signal()
        self.register_data    = Signal(8)
        self.perform_write    = Signal()
        self.single_page      = Signal()
        self.start_transfer   = Signal()
        self.final_word       = Signal()
        self.idle             = Signal()
        self.read_ready       = Signal()
        self.write_ready      = Signal()
        self.read_data        = Signal(32)
        self.write_data       = Signal(32)
        self.write_mask       = Signal(4) # TODO

        super().__init__({
            "simif": In(FakePSRAMSimulationInterface())
        })

    def elaborate(self, platform):
        m = Module()

        is_read         = Signal()
        is_register     = Signal()
        is_multipage    = Signal()
        extra_latency   = Signal()
        latency_clocks_remaining  = Signal(range(0, self.HIGH_LATENCY_CLOCKS + 1))

        m.d.comb += [
            self.simif.write_data .eq(self.write_data),
            self.simif.read_ready .eq(self.read_ready),
            self.simif.write_ready.eq(self.write_ready),
            self.simif.idle       .eq(self.idle),
        ]

        with m.FSM() as fsm:
            with m.State('IDLE'):
                m.d.comb += self.idle        .eq(1)
                with m.If(self.start_transfer):
                    m.next = 'LATCH_RWDS'
                    m.d.sync += [
                        is_read     .eq(~self.perform_write),
                        is_register .eq(self.register_space),
                        is_multipage.eq(~self.single_page),
                        # address is specified with 16-bit granularity.
                        # <<1 gets us to 8-bit for our fake uint8 storage.
                        self.simif.address_ptr.eq(self.address<<1),
                    ]
            with m.State("LATCH_RWDS"):
                m.next="SHIFT_COMMAND0"
            with m.State('SHIFT_COMMAND0'):
                m.next = 'SHIFT_COMMAND1'
            with m.State('SHIFT_COMMAND1'):
                with m.If(is_register & ~is_read):
                    m.next = 'WRITE_DATA'
                with m.Else():
                    m.next = "HANDLE_LATENCY"
                    m.d.sync += latency_clocks_remaining.eq(self.HIGH_LATENCY_CLOCKS)
            with m.State('HANDLE_LATENCY'):
                m.d.sync += latency_clocks_remaining.eq(latency_clocks_remaining - 1)
                with m.If(latency_clocks_remaining == 0):
                    with m.If(is_read):
                        m.next = 'READ_DATA'
                    with m.Else():
                        m.next = 'WRITE_DATA'
            with m.State('READ_DATA'):
                m.d.comb += [
                    self.read_data .eq(self.simif.read_data_view),
                    self.read_ready.eq(1),
                ]
                m.d.sync += self.simif.address_ptr.eq(self.simif.address_ptr + 4)
                with m.If(self.final_word):
                    m.next = 'RECOVERY'
            with m.State("WRITE_DATA"):
                m.d.comb += self.write_ready.eq(1),
                m.d.sync += self.simif.address_ptr.eq(self.simif.address_ptr + 4)
                with m.If(is_register):
                    m.next = 'IDLE'
                with m.Elif(self.final_word):
                    m.next = 'RECOVERY'
            with m.State('RECOVERY'):
                m.d.sync += self.simif.address_ptr.eq(0)
                m.next = 'IDLE'
        return m

# Main purpose of using this custom platform instead of
# simply None is to track extra files added to the build.
class VerilatorPlatform():
    def __init__(self):
        self.files = {}

    def add_file(self, file_name, contents):
        self.files[file_name] = contents

def is_hw(platform):
    # assumption: anything that inherits from Platform is a
    # real hardware platform. Anything else isn't.
    # is there a better way of doing this?
    return isinstance(platform, Platform)

def simulate_soc(fragment, tracing=False):

    build_dst = "build"
    dst = f"{build_dst}/tiliqua_soc.v"
    print(f"write verilog implementation of 'tiliqua_soc' to '{dst}'...")

    sim_platform = VerilatorPlatform()

    os.makedirs(build_dst, exist_ok=True)
    with open(dst, "w") as f:
        f.write(verilog.convert(
            fragment,
            platform=sim_platform,
            ports={
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
            }))

    # Write all additional files added with platform.add_file()
    # to build/ directory, so verilator build can find them.
    for file in sim_platform.files:
        with open(os.path.join("build", file), "w") as f:
            f.write(sim_platform.files[file])

    tracing_flags = ["--trace-fst", "--trace-structs"] if tracing else []

    # TODO: warn if this is far from the PLL output?
    dvi_clk_hz = int(fragment.video.dvi_tgen.timings.pll.pixel_clk_mhz * 1e6)
    dvi_h_active = fragment.video.dvi_tgen.timings.h_active
    dvi_v_active = fragment.video.dvi_tgen.timings.v_active

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
                           "-cc"] + tracing_flags + [
                           "--exe",
                           "--Mdir", f"{verilator_dst}",
                           "--build",
                           "-j", "0",
                           "-Ibuild",
                           "-CFLAGS", f"-DSYNC_CLK_HZ={fragment.clock_sync_hz}",
                           "-CFLAGS", f"-DDVI_H_ACTIVE={dvi_h_active}",
                           "-CFLAGS", f"-DDVI_V_ACTIVE={dvi_v_active}",
                           "-CFLAGS", f"-DDVI_CLK_HZ={dvi_clk_hz}",
                           "../../src/selftest/sim.cpp",
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
