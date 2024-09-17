# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
CRT / Vectorscope simulator.
Simple gateware-only version, see 'xbeam' for SoC version with a menu system.
Rasterizes X/Y (audio channel 0, 1) and color (audio channel 3) to a simulated
CRT display, with intensity gradient and afterglow effects.

Default 1280x720p60 seems to work with all the monitors I have, but other screens might
need timing + PLL adjustments.

There are top-level scripts for building/simulating e.g.

$ pdm build_vectorscope
$ pdm sim_vectorscope
# for visualizing the palette
$ pdm colors_vectorscope
"""

import os
import math
import shutil
import subprocess

from amaranth              import *
from amaranth.build        import *
from amaranth.lib          import wiring, data, stream
from amaranth.lib.wiring   import In, Out
from amaranth.lib.fifo     import AsyncFIFO, SyncFIFO
from amaranth.lib.cdc      import FFSynchronizer
from amaranth.utils        import log2_int
from amaranth.hdl.mem      import Memory

from amaranth_future       import fixed

from tiliqua.tiliqua_platform import TiliquaPlatform, TiliquaDomainGenerator, set_environment_variables
from tiliqua                  import eurorack_pmod, dsp
from tiliqua.eurorack_pmod    import ASQ

from tiliqua                  import psram_peripheral

from amaranth_soc             import wishbone

from amaranth.back import verilog

from tiliqua.sim import FakeEurorackPmod, FakeTiliquaDomainGenerator

from tiliqua.video import DVI_TIMINGS, FramebufferPHY

from tiliqua.raster import Persistance, Stroke

from vendor.ila import AsyncSerialILA, AsyncSerialILAFrontend

class VectorScopeTop(Elaboratable):

    """
    Top-level Vectorscope design.
    Can be instantiated with 'sim=True', which swaps out most things that touch hardware for mocks.
    """

    def __init__(self, *, dvi_timings, sim=False, ila=False):

        self.dvi_timings = dvi_timings
        self.sim = sim
        self.use_ila = ila

        # One PSRAM with an internal arbiter to support multiple DMA masters.
        self.psram_periph = psram_peripheral.Peripheral(size=16*1024*1024)

        fb_base = 0x0
        fb_size = (dvi_timings.h_active, dvi_timings.v_active)

        # All of our DMA masters
        self.video = FramebufferPHY(
                fb_base=fb_base, dvi_timings=dvi_timings, fb_size=fb_size,
                bus_master=self.psram_periph.bus)
        self.persist = Persistance(
                fb_base=fb_base, bus_master=self.psram_periph.bus, fb_size=fb_size)
        self.stroke = Stroke(
                fb_base=fb_base, bus_master=self.psram_periph.bus, fb_size=fb_size)

        self.psram_periph.add_master(self.video.bus)
        self.psram_periph.add_master(self.persist.bus)
        self.psram_periph.add_master(self.stroke.bus)

        if self.sim:
            self.pmod0 = FakeEurorackPmod()
            self.inject0 = Signal(signed(16))
            self.inject1 = Signal(signed(16))
            self.inject2 = Signal(signed(16))
            self.inject3 = Signal(signed(16))

        super().__init__()

    def elaborate(self, platform):
        m = Module()

        if self.sim:
            m.submodules.car = FakeTiliquaDomainGenerator()
            m.d.comb += [
                self.pmod0.sample_inject[0]._target.eq(self.inject0),
                self.pmod0.sample_inject[1]._target.eq(self.inject1),
                self.pmod0.sample_inject[2]._target.eq(self.inject2),
                self.pmod0.sample_inject[3]._target.eq(self.inject3)
            ]
        else:
            m.submodules.car = TiliquaDomainGenerator(audio_192=True, pixclk_pll=self.dvi_timings.pll)

        if not self.sim:
            self.pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True,
                touch_enabled=False,
                audio_192=True)

        pmod0 = self.pmod0
        m.submodules.pmod0 = pmod0
        self.stroke.pmod0 = pmod0

        m.submodules.astream = astream = eurorack_pmod.AudioStream(self.pmod0)
        m.submodules.video = self.video
        m.submodules.persist = self.persist
        m.submodules.stroke = self.stroke

        wiring.connect(m, astream.istream, self.stroke.i)

        # Memory controller hangs if we start making requests to it straight away.
        on_delay = Signal(32)
        with m.If(on_delay < 0xFFFF):
            m.d.sync += on_delay.eq(on_delay+1)
        with m.Else():
            m.d.sync += self.video.enable.eq(1)
            m.d.sync += self.persist.enable.eq(1)
            m.d.sync += self.stroke.enable.eq(1)

        # Optional ILA, very useful for low-level PSRAM debugging...
        if not self.use_ila:
            m.submodules.psram_periph = self.psram_periph
        else:
            # HACK: eager elaboration so ILA has something to attach to
            m.submodules.psram_periph = self.psram_periph.elaborate(platform)

            test_signal = Signal(16, reset=0xFEED)
            ila_signals = [
                test_signal,
                self.psram_periph.psram.idle,
                self.psram_periph.psram.perform_write,
                self.psram_periph.psram.start_transfer,
                self.psram_periph.psram.final_word,
                self.psram_periph.psram.read_ready,
                self.psram_periph.psram.write_ready,
                self.psram_periph.psram.fsm,
                self.psram_periph.psram.phy.datavalid,
                self.psram_periph.psram.phy.burstdet,
                self.psram_periph.psram.phy.cs,
                self.psram_periph.psram.phy.clk_en,
                self.psram_periph.psram.phy.ready,
                self.psram_periph.psram.phy.readclksel,
            ]
            self.ila = AsyncSerialILA(signals=ila_signals,
                                      sample_depth=4096, divisor=521,
                                      domain='sync', sample_rate=60e6) # ~115200 baud on USB clock
            m.submodules += self.ila
            m.d.comb += [
                self.ila.trigger.eq(self.psram_periph.psram.start_transfer),
                platform.request("uart").tx.o.eq(self.ila.tx), # needs FFSync?
            ]

        return m

def build(ila=False):
    dvi_timings = set_environment_variables()
    top = VectorScopeTop(dvi_timings=dvi_timings, ila=ila)
    TiliquaPlatform().build(top)
    if ila:
        subprocess.check_call(["openFPGALoader",
                               "-c", "dirtyJtag",
                               "build/top.bit"],
                              env=os.environ)
        # TODO: make serial port selectable
        frontend = AsyncSerialILAFrontend("/dev/ttyACM0", baudrate=115200, ila=top.ila)
        frontend.emit_vcd("out.vcd")



def colors():
    """
    Render image of intensity/color palette used internally by FramebufferPHY.
    This is useful for quickly tweaking it.
    """
    import matplotlib
    import matplotlib.pyplot as plt
    from matplotlib import colors
    import numpy as np
    rs, gs, bs = FramebufferPHY.compute_color_palette()

    i_levels = 16
    c_levels = 16
    data = np.empty((i_levels, c_levels, 3), dtype=np.uint8)
    for i in range(i_levels):
        for c in range(c_levels):
            data[i,c,:] = (rs[i*i_levels + c],
                           gs[i*i_levels + c],
                           bs[i*i_levels + c])

    fig, ax = plt.subplots()
    ax.imshow(data)
    ax.grid(which='major', axis='both', linestyle='-', color='k', linewidth=2)
    ax.set_xticks(np.arange(-.5, 16, 1));
    ax.set_yticks(np.arange(-.5, 16, 1));
    save_to = 'vectorscope_palette.png'
    print(f'save palette render to {save_to}')
    plt.savefig(save_to)

def sim():
    """
    End-to-end simulation of all the gateware in this project.
    """

    build_dst = "build"
    dst = f"{build_dst}/vectorscope.v"
    print(f"write verilog implementation of 'example_vectorscope' to '{dst}'...")

    top = VectorScopeTop(dvi_timings=DVI_TIMINGS["1280x720p60"], sim=True)

    os.makedirs(build_dst, exist_ok=True)
    with open(dst, "w") as f:
        f.write(verilog.convert(top, ports={
            "clk_sync":       (ClockSignal("sync"),                   None),
            "rst_sync":       (ResetSignal("sync"),                   None),
            "clk_dvi":        (ClockSignal("dvi"),                    None),
            "rst_dvi":        (ResetSignal("dvi"),                    None),
            "clk_audio":      (ClockSignal("audio"),                  None),
            "rst_audio":      (ResetSignal("audio"),                  None),
            "idle":           (top.psram_periph.simif.idle,           None),
            "address_ptr":    (top.psram_periph.simif.address_ptr,    None),
            "read_data_view": (top.psram_periph.simif.read_data_view, None),
            "write_data":     (top.psram_periph.simif.write_data,     None),
            "read_ready":     (top.psram_periph.simif.read_ready,     None),
            "write_ready":    (top.psram_periph.simif.write_ready,    None),
            "dvi_x":          (top.video.dvi_tgen.x,                  None),
            "dvi_y":          (top.video.dvi_tgen.y,                  None),
            "dvi_r":          (top.video.phy_r,                       None),
            "dvi_g":          (top.video.phy_g,                       None),
            "dvi_b":          (top.video.phy_b,                       None),
            "fs_strobe":      (top.pmod0.fs_strobe,                   None),
            "fs_inject0":     (top.inject0,                           None),
            "fs_inject1":     (top.inject1,                           None),
            "fs_inject2":     (top.inject2,                           None),
            "fs_inject3":     (top.inject3,                           None),
            }))

    # TODO: warn if this is far from the PLL output?
    dvi_clk_hz = int(top.video.dvi_tgen.timings.pll.pixel_clk_mhz * 1e6)
    dvi_h_active = top.video.dvi_tgen.timings.h_active
    dvi_v_active = top.video.dvi_tgen.timings.v_active
    sync_clk_hz = 60000000
    audio_clk_hz = 48000000

    verilator_dst = "build/obj_dir"
    shutil.rmtree(verilator_dst)
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
                           "-CFLAGS", f"-DDVI_H_ACTIVE={dvi_h_active}",
                           "-CFLAGS", f"-DDVI_V_ACTIVE={dvi_v_active}",
                           "-CFLAGS", f"-DDVI_CLK_HZ={dvi_clk_hz}",
                           "-CFLAGS", f"-DSYNC_CLK_HZ={sync_clk_hz}",
                           "-CFLAGS", f"-DAUDIO_CLK_HZ={audio_clk_hz}",
                           "../../src/example_vectorscope/sim/sim.cpp",
                           f"{dst}",
                           ],
                          env=os.environ)

    print(f"run verilated binary '{verilator_dst}/Vvectorscope'...")
    subprocess.check_call([f"{verilator_dst}/Vvectorscope"],
                          env=os.environ)

    print(f"done.")
