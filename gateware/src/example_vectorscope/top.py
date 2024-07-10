# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
CRT / Vectorscope simulator.
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

class Persistance(Elaboratable):

    """
    Read pixels from a framebuffer in PSRAM and apply gradual intensity reduction to simulate oscilloscope glow.
    Pixels are DMA'd from PSRAM as a wishbone master in bursts of 'fifo_depth // 2' in the 'sync' clock domain.
    The block of pixels has its intensity reduced and is then DMA'd back to the bus.

    'holdoff' is used to keep this core from saturating the bus between bursts.
    """

    def __init__(self, *, fb_base, bus_master, fb_size,
                 fifo_depth=128, holdoff_default=1024, fb_bytes_per_pixel=1):
        super().__init__()

        self.fb_base = fb_base
        self.fb_hsize, self.fb_vsize = fb_size
        self.fifo_depth = fifo_depth
        self.holdoff = Signal(16, reset=holdoff_default)
        self.fb_bytes_per_pixel = fb_bytes_per_pixel

        # We are a DMA master
        self.bus = wishbone.Interface(addr_width=bus_master.addr_width, data_width=32, granularity=8,
                                      features={"cti", "bte"})

        # FIFO to cache pixels from PSRAM.
        self.fifo = SyncFIFO(width=32, depth=fifo_depth)

        # Current addresses in the framebuffer (read and write sides)
        self.dma_addr_in = Signal(32, reset=0)
        self.dma_addr_out = Signal(32)

        # Kick to start this core.
        self.enable = Signal(1, reset=0)

    def elaborate(self, platform) -> Module:
        m = Module()

        # Length of framebuffer in 32-bit words
        fb_len_words = (self.fb_bytes_per_pixel * (self.fb_hsize*self.fb_vsize)) // 4

        holdoff_count = Signal(32)
        pnext = Signal(32)
        wr_source = Signal(32)

        m.submodules.fifo = self.fifo
        bus = self.bus
        dma_addr_in = self.dma_addr_in
        dma_addr_out = self.dma_addr_out

        # Persistance state machine in 'sync' domain.
        with m.FSM() as fsm:
            with m.State('OFF'):
                with m.If(self.enable):
                    m.next = 'BURST-IN'

            with m.State('BURST-IN'):
                m.d.sync += holdoff_count.eq(0)
                m.d.comb += [
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(0),
                    bus.sel.eq(2**(bus.data_width//8)-1),
                    bus.adr.eq(self.fb_base + dma_addr_in),
                ]
                with m.If(~self.fifo.w_rdy):
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.END_OF_BURST)
                    m.next = 'WAIT1'
                with m.Else():
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.INCR_BURST)
                with m.If(bus.stb & bus.ack & self.fifo.w_rdy): # WARN: drops last word
                    m.d.comb += self.fifo.w_en.eq(1)
                    m.d.comb += self.fifo.w_data.eq(bus.dat_r),
                    with m.If(dma_addr_in < (fb_len_words-1)):
                        m.d.sync += dma_addr_in.eq(dma_addr_in + 1)
                    with m.Else():
                        m.d.sync += dma_addr_in.eq(0)

            with m.State('WAIT1'):
                m.d.sync += holdoff_count.eq(holdoff_count + 1)
                with m.If(holdoff_count > self.holdoff):
                    m.d.sync += pnext.eq(self.fifo.r_data)
                    m.d.comb += self.fifo.r_en.eq(1)
                    m.next = 'BURST-OUT'

            with m.State('BURST-OUT'):
                m.d.sync += holdoff_count.eq(0)

                # Incoming pixel array (read from FIFO)
                pixa = Signal(data.ArrayLayout(unsigned(8), 4))
                # Outgoing pixel array (write to bus)
                pixb = Signal(data.ArrayLayout(unsigned(8), 4))

                m.d.comb += [
                    pixa.eq(wr_source),
                ]

                # The actual persistance calculation. 4 pixels at a time.
                for n in range(4):
                    # color
                    m.d.comb += pixb[n][0:4].eq(pixa[n][0:4])
                    # intensity
                    with m.If(pixa[n][4:8] > 0):
                        m.d.comb += pixb[n][4:8].eq(pixa[n][4:8] - 1)

                m.d.comb += [
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(1),
                    bus.sel.eq(2**(bus.data_width//8)-1),
                    bus.dat_w.eq(pixb),
                    bus.adr.eq(self.fb_base + dma_addr_out),
                    wr_source.eq(pnext),
                ]

                with m.If(~self.fifo.r_rdy):
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.END_OF_BURST)
                    m.next = 'WAIT2'
                with m.Else():
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.INCR_BURST)
                with m.If(bus.stb & bus.ack):
                    m.d.comb += self.fifo.r_en.eq(1)
                    m.d.comb += wr_source.eq(self.fifo.r_data),
                    with m.If(dma_addr_out < (fb_len_words-1)):
                        m.d.sync += dma_addr_out.eq(dma_addr_out + 1)
                        m.d.comb += bus.adr.eq(self.fb_base + dma_addr_out + 1),
                    with m.Else():
                        m.d.sync += dma_addr_out.eq(0)
                        m.d.comb += bus.adr.eq(self.fb_base + 0),

            with m.State('WAIT2'):
                m.d.sync += holdoff_count.eq(holdoff_count + 1)
                with m.If(holdoff_count > self.holdoff):
                    m.next = 'BURST-IN'

        return m

class Draw(Elaboratable):

    """
    Read audio samples in the 'audio' domain, upsample them, and draw to the framebuffer.
    Pixels are DMA'd to PSRAM as a wishbone master, NOT in bursts, as we have no idea
    where each pixel is going to land beforehand. This is the most expensive use of
    PSRAM time in this project as we spend ages waiting on memory latency.

    TODO: can we somehow cache bursts of pixels here?

    Each pixel must be read before we write it for 2 reasons:
    - We have 4 pixels per word, so we can't just write 1 pixel as it would erase the
      adjacent ones.
    - We don't just set max intensity on drawing a pixel, rather we read the current
      intensity and add to it. Otherwise, we get no intensity gradient and the display
      looks nowhere near as nice :)

    To obtain more points, the pixels themselves are upsampled using an FIR-based
    fractional resampler. This is kind of analogous to sin(x)/x interpolation.
    """

    def __init__(self, *, fb_base, bus_master, fb_size,
                 pmod0=None, fb_bytes_per_pixel=1):
        super().__init__()

        self.fb_base = fb_base
        self.fb_hsize, self.fb_vsize = fb_size
        self.fb_bytes_per_pixel = fb_bytes_per_pixel

        self.bus = wishbone.Interface(addr_width=bus_master.addr_width, data_width=32, granularity=8,
                                      features={"cti", "bte"})

        self.sample_x = Signal(signed(16))
        self.sample_y = Signal(signed(16))
        self.sample_p = Signal(signed(16))
        self.sample_c = Signal(signed(16))

        self.hue = Signal(4, reset=0);


        self.px_read = Signal(32)
        self.px_sum = Signal(16)
        self.pmod0 = pmod0

        # Kick this to start the core
        self.enable = Signal(1, reset=0)


    def elaborate(self, platform) -> Module:
        m = Module()

        bus = self.bus

        fb_len_words = (self.fb_hsize*self.fb_vsize) // 4

        sample_x = self.sample_x
        sample_y = self.sample_y
        sample_p = self.sample_p
        sample_c = self.sample_c

        pmod0 = self.pmod0

        m.submodules.astream = astream = eurorack_pmod.AudioStream(pmod0)
        m.submodules.split = split = dsp.Split(n_channels=4)
        m.submodules.merge = merge = dsp.Merge(n_channels=4)

        N_UP=6
        m.submodules.resample0 = resample0 = dsp.Resample(fs_in=192000, n_up=N_UP, m_down=1)
        m.submodules.resample1 = resample1 = dsp.Resample(fs_in=192000, n_up=N_UP, m_down=1)
        m.submodules.resample2 = resample2 = dsp.Resample(fs_in=192000, n_up=N_UP, m_down=1)
        m.submodules.resample3 = resample3 = dsp.Resample(fs_in=192000, n_up=N_UP, m_down=1)

        wiring.connect(m, astream.istream, split.i)

        wiring.connect(m, split.o[0], resample0.i)
        wiring.connect(m, split.o[1], resample1.i)
        wiring.connect(m, split.o[2], resample2.i)
        wiring.connect(m, split.o[3], resample3.i)

        wiring.connect(m, resample0.o, merge.i[0])
        wiring.connect(m, resample1.o, merge.i[1])
        wiring.connect(m, resample2.o, merge.i[2])
        wiring.connect(m, resample3.o, merge.i[3])


        px_read = self.px_read
        px_sum = self.px_sum

        with m.FSM() as fsm:

            with m.State('OFF'):
                with m.If(self.enable):
                    m.next = 'LATCH0'

            with m.State('LATCH0'):

                m.d.comb += merge.o.ready.eq(1)
                # Fired on every audio sample fs_strobe
                with m.If(merge.o.valid):
                    m.d.sync += [
                        # TODO this >>6 scales input -> screen mapping.
                        # should be better exposed for tweaking.
                        sample_x.eq(merge.o.payload[0].sas_value()>>6),
                        sample_y.eq(merge.o.payload[1].sas_value()>>6),
                        sample_p.eq(merge.o.payload[2].sas_value()),
                        sample_c.eq(merge.o.payload[3].sas_value()),
                    ]
                    m.next = 'LATCH1'

            with m.State('LATCH1'):
                fb_hwords = ((self.fb_hsize*self.fb_bytes_per_pixel)//4)
                m.d.sync += [
                    bus.sel.eq(0xf),
                    bus.adr.eq(self.fb_base + (sample_y + (self.fb_vsize//2))*fb_hwords + ((fb_hwords//2) + (sample_x >> 2))),
                ]
                m.next = 'READ'

            with m.State('READ'):

                m.d.comb += [
                    bus.cti.eq(wishbone.CycleType.CLASSIC),
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(0),
                ]

                with m.If(bus.stb & bus.ack):
                    m.d.sync += px_read.eq(bus.dat_r)
                    m.d.sync += px_sum.eq(((bus.dat_r >> (sample_x[0:2]*8)) & 0xff))
                    m.next = 'WAIT'

            with m.State('WAIT'):
                m.next = 'WAIT2'

            with m.State('WAIT2'):
                m.next = 'WAIT3'

            with m.State('WAIT3'):
                m.next = 'WRITE'

            with m.State('WRITE'):

                m.d.comb += [
                    bus.cti.eq(wishbone.CycleType.CLASSIC),
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(1),
                ]

                # The actual drawing logic
                # Basically we just increment the intensity and clamp it to a maximum
                # for the correct bits of the native bus word for this pixel.
                #
                # TODO: color is always overridden, perhaps we should mix it?

                new_color = Signal(unsigned(4))
                white = Signal(unsigned(4))
                m.d.comb += white.eq(0xf)
                m.d.comb += new_color.eq((sample_c>>10) + self.hue)

                inc=4
                with m.If(px_sum[4:8] + inc >= 0xF):
                    m.d.comb += bus.dat_w.eq(
                        (px_read & ~(Const(0xFF, unsigned(32)) << (sample_x[0:2]*8))) |
                        (Cat(new_color, white) << (sample_x[0:2]*8))
                         )
                with m.Else():
                    m.d.comb += bus.dat_w.eq(
                        (px_read & ~(Const(0xFF, unsigned(32)) << (sample_x[0:2]*8))) |
                        (Cat(new_color, (px_sum[4:8] + inc)) << (sample_x[0:2]*8))
                         )

                with m.If(bus.stb & bus.ack):
                    m.next = 'LATCH0'

        return m

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
        self.draw = Draw(
                fb_base=fb_base, bus_master=self.hyperram.bus, fb_size=fb_size)

        self.hyperram.add_master(self.video.bus)
        self.hyperram.add_master(self.persist.bus)
        self.hyperram.add_master(self.draw.bus)

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
        self.draw.pmod0 = pmod0

        m.submodules.hyperram = self.hyperram
        m.submodules.video = self.video
        m.submodules.persist = self.persist
        m.submodules.draw = self.draw

        # Memory controller hangs if we start making requests to it straight away.
        on_delay = Signal(32)
        with m.If(on_delay < 0xFFFF):
            m.d.sync += on_delay.eq(on_delay+1)
        with m.Else():
            m.d.sync += self.video.enable.eq(1)
            m.d.sync += self.persist.enable.eq(1)
            m.d.sync += self.draw.enable.eq(1)

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
