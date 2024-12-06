# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
CRT / Vectorscope simulator.
Rasterizes X/Y (audio channel 0, 1) and color (audio channel 3) to a simulated
CRT display, with intensity gradient and afterglow effects.

Simple gateware-only version, this is mostly useful for debugging the
memory and video subsystems with an ILA or simulation, as it's smaller.

See 'xbeam' for SoC version of the scope with a menu system.

.. code-block:: bash

    # for visualizing the color palette
    $ pdm colors_vectorscope

"""

import os
import math
import shutil
import subprocess

from amaranth                 import *
from amaranth.build           import *
from amaranth.lib             import wiring, data, stream
from amaranth.lib.wiring      import In, Out
from amaranth.lib.fifo        import AsyncFIFO, SyncFIFO
from amaranth.lib.cdc         import FFSynchronizer
from amaranth.utils           import log2_int
from amaranth.back            import verilog

from amaranth_future          import fixed
from amaranth_soc             import wishbone

from tiliqua.tiliqua_platform import *
from tiliqua                  import eurorack_pmod, dsp, sim, cache
from tiliqua.eurorack_pmod    import ASQ
from tiliqua                  import psram_peripheral
from tiliqua.cli              import top_level_cli
from tiliqua.sim              import FakeEurorackPmod, FakeTiliquaDomainGenerator
from tiliqua.video            import DVI_TIMINGS, FramebufferPHY
from tiliqua.raster           import Persistance, Stroke

from vendor.ila               import AsyncSerialILA

class VectorScopeTop(Elaboratable):

    """
    Top-level Vectorscope design.
    """

    def __init__(self, *, dvi_timings, wishbone_l2_cache, **kwargs):

        self.dvi_timings = dvi_timings
        self.wishbone_l2_cache = wishbone_l2_cache

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

        if self.wishbone_l2_cache:
            self.cache = cache.WishboneL2Cache(
                    addr_width=self.psram_periph.bus.addr_width,
                    cachesize_words=128)
            self.psram_periph.add_master(self.cache.slave)
        else:
            self.psram_periph.add_master(self.stroke.bus)

        # Only used for simulation
        self.fs_strobe = Signal()
        self.inject0 = Signal(signed(16))
        self.inject1 = Signal(signed(16))
        self.inject2 = Signal(signed(16))
        self.inject3 = Signal(signed(16))

        super().__init__()

    def elaborate(self, platform):
        m = Module()

        if not sim.is_hw(platform):
            self.pmod0 = FakeEurorackPmod()
            m.submodules.car = FakeTiliquaDomainGenerator()
            m.d.comb += [
                self.pmod0.sample_inject[0]._target.eq(self.inject0),
                self.pmod0.sample_inject[1]._target.eq(self.inject1),
                self.pmod0.sample_inject[2]._target.eq(self.inject2),
                self.pmod0.sample_inject[3]._target.eq(self.inject3),
                self.pmod0.fs_strobe.eq(self.fs_strobe),
            ]
        else:
            m.submodules.car = car = platform.clock_domain_generator(audio_192=True, pixclk_pll=self.dvi_timings.pll)
            m.submodules.reboot = reboot = RebootProvider(car.clocks_hz["sync"])
            m.submodules.btn = FFSynchronizer(
                    platform.request("encoder").s.i, reboot.button)

        if sim.is_hw(platform):
            self.pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True,
                touch_enabled=False,
                audio_192=True)
            m.d.comb += self.pmod0.codec_mute.eq(reboot.mute)

        pmod0 = self.pmod0
        m.submodules.pmod0 = pmod0
        self.stroke.pmod0 = pmod0

        m.submodules.astream = astream = eurorack_pmod.AudioStream(self.pmod0)
        m.submodules.video = self.video
        m.submodules.persist = self.persist
        m.submodules.stroke = self.stroke

        if self.wishbone_l2_cache:
            m.submodules.cache = self.cache
            wiring.connect(m, self.stroke.bus, self.cache.master)

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
        if not platform.ila:
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

def simulation_ports(fragment):
    return {
        "clk_sync":       (ClockSignal("sync"),                        None),
        "rst_sync":       (ResetSignal("sync"),                        None),
        "clk_dvi":        (ClockSignal("dvi"),                         None),
        "rst_dvi":        (ResetSignal("dvi"),                         None),
        "clk_audio":      (ClockSignal("audio"),                       None),
        "rst_audio":      (ResetSignal("audio"),                       None),
        "idle":           (fragment.psram_periph.simif.idle,           None),
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
        "fs_strobe":      (fragment.fs_strobe,                         None),
        "fs_inject0":     (fragment.inject0,                           None),
        "fs_inject1":     (fragment.inject1,                           None),
        "fs_inject2":     (fragment.inject2,                           None),
        "fs_inject3":     (fragment.inject3,                           None),
    }

def argparse_callback(parser):
    parser.add_argument('--cache', action='store_true',
                        help="Add L2 wishbone cache to stroke-raster converter.")

def argparse_fragment(args):
    return {
        "wishbone_l2_cache": args.cache
    }

if __name__ == "__main__":
    this_path = os.path.dirname(os.path.realpath(__file__))
    top_level_cli(
        VectorScopeTop,
        ila_supported=True,
        sim_ports=simulation_ports,
        sim_harness="../../src/top/vectorscope_no_soc/sim/sim.cpp",
        argparse_callback=argparse_callback,
        argparse_fragment=argparse_fragment
    )
