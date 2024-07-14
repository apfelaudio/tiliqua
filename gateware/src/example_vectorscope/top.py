# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
CRT / Vectorscope simulator.
Simple gateware-only version, see 'xbeam' for SoC version with a menu system.
Rasterizes X/Y (audio channel 0, 1) and color (audio channel 3) to a simulated
CRT display, with intensity gradient and afterglow effects.

Default 800x600p60 seems to work with all the monitors I have, but other screens might
need timing + PLL adjustments.

There are top-level scripts for building/simulating e.g.

$ pdm build_vectorscope
$ pdm sim_vectorscope
# for visualizing the palette
$ pdm colors_vectorscope
"""

import os
import math
import subprocess

from amaranth              import *
from amaranth.build        import *
from amaranth.lib          import wiring, data
from amaranth.lib.wiring   import In, Out
from amaranth.lib.fifo     import AsyncFIFO, SyncFIFO
from amaranth.lib.cdc      import FFSynchronizer
from amaranth.utils        import log2_int
from amaranth.hdl.mem      import Memory

from amaranth_future       import stream, fixed

from tiliqua.tiliqua_platform import TiliquaPlatform, TiliquaDomainGenerator
from tiliqua                  import eurorack_pmod, dsp
from tiliqua.eurorack_pmod    import ASQ

from tiliqua.psram_peripheral import PSRAMPeripheral
from luna_soc.gateware.vendor.amaranth_soc import wishbone

from amaranth.back import verilog

from tiliqua.sim import FakeEurorackPmod, FakeTiliquaDomainGenerator

from tiliqua.video import DVI_TIMINGS, FramebufferPHY

from tiliqua.raster import Persistance, Stroke

class VectorScopeTop(Elaboratable):

    """
    Top-level Vectorscope design.
    Can be instantiated with 'sim=True', which swaps out most things that touch hardware for mocks.
    """

    def __init__(self, sim=False):

        self.sim = sim

        # One PSRAM with an internal arbiter to support multiple DMA masters.
        self.hyperram = PSRAMPeripheral(
                size=16*1024*1024, sim=sim)

        # WARN: You have to modify the platform PLL if you change the pixel clock!
        # TODO: integrate ecp5_pll from lambdasoc or custom solution --
        timings = DVI_TIMINGS["800x600p60"]
        fb_base = 0x0
        fb_size = (timings.h_active, timings.v_active)

        # All of our DMA masters
        self.video = FramebufferPHY(
                fb_base=fb_base, dvi_timings=timings, fb_size=fb_size,
                bus_master=self.hyperram.bus, sim=sim)
        self.persist = Persistance(
                fb_base=fb_base, bus_master=self.hyperram.bus, fb_size=fb_size)

        self.stroke0 = Stroke(
                fb_base=fb_base, bus_master=self.hyperram.bus, fb_size=fb_size, upsample_factor=None, default_hue=0, default_x=-260, default_y=-225)
        self.stroke1 = Stroke(
                fb_base=fb_base, bus_master=self.hyperram.bus, fb_size=fb_size, upsample_factor=None, default_hue=2, default_x=-260, default_y=-75)
        self.stroke2 = Stroke(
                fb_base=fb_base, bus_master=self.hyperram.bus, fb_size=fb_size, upsample_factor=None, default_hue=4, default_x=-260, default_y=75)
        self.stroke3 = Stroke(
                fb_base=fb_base, bus_master=self.hyperram.bus, fb_size=fb_size, upsample_factor=None, default_hue=8, default_x=-260, default_y=225)
        self.stroke4 = Stroke(
                fb_base=fb_base, bus_master=self.hyperram.bus, fb_size=fb_size, upsample_factor=None, default_hue=10,default_x=260, default_y=-225)
        self.stroke5 = Stroke(
                fb_base=fb_base, bus_master=self.hyperram.bus, fb_size=fb_size, upsample_factor=None, default_hue=12,default_x=260,  default_y=-75)
        self.stroke6 = Stroke(
                fb_base=fb_base, bus_master=self.hyperram.bus, fb_size=fb_size, upsample_factor=None, default_hue=14,default_x=260,  default_y=75)
        self.stroke7 = Stroke(
                fb_base=fb_base, bus_master=self.hyperram.bus, fb_size=fb_size, upsample_factor=None, default_hue=15,default_x=260,  default_y=225)

        self.hyperram.add_master(self.video.bus)
        self.hyperram.add_master(self.persist.bus)
        self.hyperram.add_master(self.stroke0.bus)
        self.hyperram.add_master(self.stroke1.bus)
        self.hyperram.add_master(self.stroke2.bus)
        self.hyperram.add_master(self.stroke3.bus)
        self.hyperram.add_master(self.stroke4.bus)
        self.hyperram.add_master(self.stroke5.bus)
        self.hyperram.add_master(self.stroke6.bus)
        self.hyperram.add_master(self.stroke7.bus)

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
            m.submodules.car = TiliquaDomainGenerator(audio_192=True)

        if not self.sim:
            self.pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True,
                touch_enabled=False,
                audio_192=True)

        pmod0 = self.pmod0
        m.submodules.pmod0 = pmod0

        m.submodules.astream = astream = eurorack_pmod.AudioStream(self.pmod0)
        m.submodules.hyperram = self.hyperram
        m.submodules.video = self.video
        m.submodules.persist = self.persist

        m.submodules.stroke0 = self.stroke0
        m.submodules.stroke1 = self.stroke1
        m.submodules.stroke2 = self.stroke2
        m.submodules.stroke3 = self.stroke3
        m.submodules.stroke4 = self.stroke4
        m.submodules.stroke5 = self.stroke5
        m.submodules.stroke6 = self.stroke6
        m.submodules.stroke7 = self.stroke7

        m.submodules.split = split = dsp.Split(n_channels=4)
        m.submodules.split2 = split2 = dsp.Split(n_channels=4)
        m.submodules.splitr = splitr = dsp.Split(n_channels=8, replicate=True)

        from example_dsp.top import Diffuser
        m.submodules.diffuser = diffuser = Diffuser()
        wiring.connect(m, diffuser.o, astream.ostream)
        m.d.comb += [
            diffuser.i.valid.eq(astream.istream.valid),
            diffuser.i.payload.eq(astream.istream.payload),
            split2.i.valid.eq(diffuser.o.valid),
            split2.i.payload.eq(diffuser.o.payload),
        ]

        wiring.connect(m, astream.istream, split.i)

        m.submodules.saw = saw = dsp.SawNCO(shift=0)
        sample_rate_hz=192000
        freq_inc = 100.0 * (1.0 / sample_rate_hz)
        m.d.comb += [
            saw.i.payload.freq_inc.eq(fixed.Const(freq_inc, shape=ASQ)),
            saw.i.valid.eq(astream.istream.valid),
        ]
        wiring.connect(m, saw.o, splitr.i)

        m.submodules.merge0 = merge0 = dsp.Merge(n_channels=4)
        m.submodules.merge1 = merge1 = dsp.Merge(n_channels=4)
        m.submodules.merge2 = merge2 = dsp.Merge(n_channels=4)
        m.submodules.merge3 = merge3 = dsp.Merge(n_channels=4)
        m.submodules.merge4 = merge4 = dsp.Merge(n_channels=4)
        m.submodules.merge5 = merge5 = dsp.Merge(n_channels=4)
        m.submodules.merge6 = merge6 = dsp.Merge(n_channels=4)
        m.submodules.merge7 = merge7 = dsp.Merge(n_channels=4)

        wiring.connect(m, splitr.o[0], merge0.i[0])
        wiring.connect(m, splitr.o[1], merge1.i[0])
        wiring.connect(m, splitr.o[2], merge2.i[0])
        wiring.connect(m, splitr.o[3], merge3.i[0])
        wiring.connect(m, splitr.o[4], merge4.i[0])
        wiring.connect(m, splitr.o[5], merge5.i[0])
        wiring.connect(m, splitr.o[6], merge6.i[0])
        wiring.connect(m, splitr.o[7], merge7.i[0])

        wiring.connect(m, split.o[0],    merge0.i[1])
        wiring.connect(m, dsp.ASQ_VALID, merge0.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge0.i[3])

        wiring.connect(m, split.o[1],    merge1.i[1])
        wiring.connect(m, dsp.ASQ_VALID, merge1.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge1.i[3])

        wiring.connect(m, split.o[2],    merge2.i[1])
        wiring.connect(m, dsp.ASQ_VALID, merge2.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge2.i[3])

        wiring.connect(m, split.o[3],    merge3.i[1])
        wiring.connect(m, dsp.ASQ_VALID, merge3.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge3.i[3])

        wiring.connect(m, split2.o[0],    merge4.i[1])
        wiring.connect(m, dsp.ASQ_VALID, merge4.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge4.i[3])

        wiring.connect(m, split2.o[1],    merge5.i[1])
        wiring.connect(m, dsp.ASQ_VALID, merge5.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge5.i[3])

        wiring.connect(m, split2.o[2],    merge6.i[1])
        wiring.connect(m, dsp.ASQ_VALID, merge6.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge6.i[3])

        wiring.connect(m, split2.o[3],    merge7.i[1])
        wiring.connect(m, dsp.ASQ_VALID, merge7.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge7.i[3])

        wiring.connect(m, merge0.o, self.stroke0.i)
        wiring.connect(m, merge1.o, self.stroke1.i)
        wiring.connect(m, merge2.o, self.stroke2.i)
        wiring.connect(m, merge3.o, self.stroke3.i)
        wiring.connect(m, merge4.o, self.stroke4.i)
        wiring.connect(m, merge5.o, self.stroke5.i)
        wiring.connect(m, merge6.o, self.stroke6.i)
        wiring.connect(m, merge7.o, self.stroke7.i)

        # Memory controller hangs if we start making requests to it straight away.
        on_delay = Signal(32)
        with m.If(on_delay < 0xFFFF):
            m.d.sync += on_delay.eq(on_delay+1)
        with m.Else():
            m.d.sync += self.video.enable.eq(1)
            m.d.sync += self.persist.enable.eq(1)
            m.d.sync += self.stroke0.enable.eq(1)
            m.d.sync += self.stroke1.enable.eq(1)
            m.d.sync += self.stroke2.enable.eq(1)
            m.d.sync += self.stroke3.enable.eq(1)
            m.d.sync += self.stroke4.enable.eq(1)
            m.d.sync += self.stroke5.enable.eq(1)
            m.d.sync += self.stroke6.enable.eq(1)
            m.d.sync += self.stroke7.enable.eq(1)

        return m

def build():
    overrides = {
        "debug_verilog": True,
        "verbose": True,
        "nextpnr_opts": "--timing-allow-fail",
        "ecppack_opts": "--freq 38.8 --compress",
    }
    TiliquaPlatform().build(VectorScopeTop(), **overrides)

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

    top = VectorScopeTop(sim=True)

    os.makedirs(build_dst, exist_ok=True)
    with open(dst, "w") as f:
        f.write(verilog.convert(top, ports=[
            ClockSignal("sync"),
            ResetSignal("sync"),
            ClockSignal("dvi"),
            ResetSignal("dvi"),
            ClockSignal("audio"),
            ResetSignal("audio"),
            top.hyperram.psram.idle,
            top.hyperram.psram.address_ptr,
            top.hyperram.psram.read_data_view,
            top.hyperram.psram.write_data,
            top.hyperram.psram.read_ready,
            top.hyperram.psram.write_ready,
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
    dvi_clk_hz = int(top.video.dvi_tgen.timings.pixel_clk_mhz * 1e6)
    dvi_h_active = top.video.dvi_tgen.timings.h_active
    dvi_v_active = top.video.dvi_tgen.timings.v_active
    sync_clk_hz = 60000000
    audio_clk_hz = 48000000

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
