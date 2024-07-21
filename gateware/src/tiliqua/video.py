# Utilities for synthesizing digital video timings and presenting a framebuffer.
#
# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import colorsys
import os

from amaranth              import *
from amaranth.build        import *
from amaranth.lib          import wiring, data
from amaranth.lib.wiring   import In, Out
from amaranth.lib.fifo     import AsyncFIFO, SyncFIFO
from amaranth.lib.cdc      import FFSynchronizer
from amaranth.utils        import log2_int
from amaranth.hdl.mem      import Memory

from luna_soc.gateware.vendor.amaranth_soc import wishbone

from dataclasses import dataclass

@dataclass
class DVITimings:
    h_active:      int
    h_sync_start:  int
    h_sync_end:    int
    h_total:       int
    h_sync_invert: bool
    v_active:      int
    v_sync_start:  int
    v_sync_end:    int
    v_total:       int
    v_sync_invert: bool
    refresh_rate:  float
    pixel_clk_mhz: float

DVI_TIMINGS = {
    # CVT 640x480p60
    # Every DVI-compatible monitor should support this.
    # But it's hard to find a PLL setting that gets close to the correct clock.
    "640x480p60": DVITimings(
        h_active      = 640,
        h_sync_start  = 656,
        h_sync_end    = 752,
        h_total       = 800,
        h_sync_invert = True,
        v_active      = 480,
        v_sync_start  = 490,
        v_sync_end    = 492,
        v_total       = 525,
        v_sync_invert = True,
        refresh_rate  = 59.94,
        pixel_clk_mhz = 25.175
    ),
    # DMT 800x600p60
    # Less monitors support this, but finding a good PLL setting is easy.
    "800x600p60": DVITimings(
        h_active      = 800,
        h_sync_start  = 840,
        h_sync_end    = 968,
        h_total       = 1056,
        h_sync_invert = False,
        v_active      = 600,
        v_sync_start  = 601,
        v_sync_end    = 605,
        v_total       = 628,
        v_sync_invert = False,
        refresh_rate  = 60.32,
        pixel_clk_mhz = 40.0
    ),
    # DMT 1280x720p60
    # This seems to also work with a 60MHz PLL for p50 on some monitors.
    "1280x720p60": DVITimings(
        h_active      = 1280,
        h_sync_start  = 1390,
        h_sync_end    = 1430,
        h_total       = 1650,
        h_sync_invert = False,
        v_active      = 720,
        v_sync_start  = 725,
        v_sync_end    = 730,
        v_total       = 750,
        v_sync_invert = False,
        refresh_rate  = 60.00,
        pixel_clk_mhz = 74.25,
    ),
    # A round AliExpress display
    "720x720p60": DVITimings(
        h_active      = 720,
        h_sync_start  = 760,
        h_sync_end    = 780,
        h_total       = 820,
        h_sync_invert = False,
        v_active      = 720,
        v_sync_start  = 744,
        v_sync_end    = 748,
        v_total       = 760,
        v_sync_invert = False,
        refresh_rate  = 60.0,
        pixel_clk_mhz = 37.39
    ),
    # A round Waveshare display
    "720x720p78": DVITimings(
        h_active      = 720,
        h_sync_start  = 760,
        h_sync_end    = 800,
        h_total       = 1000,
        h_sync_invert = False,
        v_active      = 720,
        v_sync_start  = 744,
        v_sync_end    = 748,
        v_total       = 760,
        v_sync_invert = False,
        refresh_rate  = 78.16,
        pixel_clk_mhz = 59.4,
    ),
}

class DVITimingGenerator(wiring.Component):

    """
    State machine to generate pixel position and hsync/vsync/de signals.
    Designed to run in the DVI pixel clock domain.
    """

    x: Out(unsigned(12))
    y: Out(unsigned(12))
    hsync: Out(unsigned(1))
    vsync: Out(unsigned(1))
    de: Out(unsigned(1))

    def __init__(self, timings: DVITimings):
        self.timings = timings
        super().__init__()

    def elaborate(self, platform) -> Module:
        m = Module()

        timings = self.timings

        with m.If(self.x == (timings.h_total-1)):
            m.d.sync += self.x.eq(0)
            with m.If(self.y == (timings.v_total-1)):
                m.d.sync += self.y.eq(0)
            with m.Else():
                m.d.sync += self.y.eq(self.y+1)
        with m.Else():
            m.d.sync += self.x.eq(self.x+1)

        # Note: sync inversion is not here and must be handled before the PHY.
        m.d.comb += [
            self.hsync.eq((self.x >= (timings.h_sync_start-1)) &
                          (self.x < (timings.h_sync_end-1))),
            self.vsync.eq((self.y >= (timings.v_sync_start-1)) &
                          (self.y < (timings.v_sync_end-1))),
            self.de.eq((self.x <= (timings.h_active-1)) &
                       (self.y <= (timings.v_active-1))),

        ]

        return m


class FramebufferPHY(Elaboratable):

    """
    Read pixels from a framebuffer in PSRAM and send them to the display.
    Pixels are DMA'd from PSRAM as a wishbone master in bursts of 'fifo_depth // 2' in the 'sync' clock domain.
    They are then piped with DVI timings to the display in the 'dvi' clock domain.

    Pixel storage itself is 8-bits: 4-bit intensity, 4-bit color.
    """

    def __init__(self, *, dvi_timings: DVITimings, fb_base, bus_master,
                 fb_size, fifo_depth=256, sim=False, fb_bytes_per_pixel=1):

        super().__init__()

        self.sim = sim
        self.fifo_depth = fifo_depth
        self.fb_base = fb_base
        self.fb_hsize, self.fb_vsize = fb_size
        self.fb_bytes_per_pixel = fb_bytes_per_pixel

        # We are a DMA master
        self.bus = wishbone.Interface(addr_width=bus_master.addr_width, data_width=32, granularity=8,
                                      features={"cti", "bte"})

        # FIFO to cache pixels from PSRAM.
        self.fifo = AsyncFIFO(width=32, depth=fifo_depth, r_domain='dvi', w_domain='sync')

        # Kick this to start the core
        self.enable = Signal(1, reset=0)

        # Tracking in DVI domain
        self.dvi_tgen = DomainRenamer("dvi")(DVITimingGenerator(dvi_timings))
        self.bytecounter = Signal(log2_int(4//self.fb_bytes_per_pixel))
        self.last_word   = Signal(32)
        self.consume_started = Signal(1, reset=0)

        # Current pixel color in DVI domain
        self.phy_r = Signal(8)
        self.phy_g = Signal(8)
        self.phy_b = Signal(8)

    @staticmethod
    def compute_color_palette():

        # Calculate 16*16 (256) color palette to map each 8-bit pixel storage
        # into R8/G8/B8 pixel value for sending to the DVI PHY. Each pixel
        # is stored as a 4-bit intensity and 4-bit color.
        #
        # TODO: make this runtime customizable?

        n_i = 16
        n_c = 16
        rs, gs, bs = [], [], []
        for i in range(n_i):
            for c in range(n_c):
                r, g, b = colorsys.hls_to_rgb(
                        float(c)/n_c, float(1.35**(i+1))/(1.35**n_i), 0.75)
                rs.append(int(r*255))
                gs.append(int(g*255))
                bs.append(int(b*255))

        return rs, gs, bs

    def elaborate(self, platform) -> Module:
        m = Module()

        m.submodules.fifo = self.fifo
        m.submodules.dvi_tgen = dvi_tgen = self.dvi_tgen

        # 'dvi' domain
        phy_r = self.phy_r
        phy_g = self.phy_g
        phy_b = self.phy_b

        # Create a VSync signal in the 'sync' domain.
        # NOTE: this is the same regardless of sync inversion.
        phy_vsync_sync = Signal()
        m.submodules.vsync_ff = FFSynchronizer(
                i=dvi_tgen.vsync, o=phy_vsync_sync, o_domain="sync")

        if not self.sim:

            # DVI PHY (not needed for simulation).
            """

            def add_sv(f):
                path = os.path.join("src/vendor/dvi", f)
                platform.add_file(f"build/{f}", open(path))

            add_sv("tmds_encoder_dvi.sv")
            add_sv("dvi_generator.sv")

            dvi_pins = platform.request("dvi")
            """

            # Register all DVI timing signals to cut timing path.
            s_dvi_de = Signal()
            s_dvi_b = Signal(unsigned(8))
            s_dvi_g = Signal(unsigned(8))
            s_dvi_r = Signal(unsigned(8))
            m.d.dvi += [
                s_dvi_de.eq(dvi_tgen.de),
                s_dvi_r.eq(phy_r),
                s_dvi_g.eq(phy_g),
                s_dvi_b.eq(phy_b),
            ]

            # Sync inversion before sending to PHY if required.
            # Better here than in DVITimingsGenerator itself in case
            # the sync signal is used by other logic.

            s_dvi_hsync = Signal()
            if dvi_tgen.timings.h_sync_invert:
                m.d.dvi += s_dvi_hsync.eq(~dvi_tgen.hsync),
            else:
                m.d.dvi += s_dvi_hsync.eq(dvi_tgen.hsync),

            s_dvi_vsync = Signal()
            if dvi_tgen.timings.v_sync_invert:
                m.d.dvi += s_dvi_vsync.eq(~dvi_tgen.vsync),
            else:
                m.d.dvi += s_dvi_vsync.eq(dvi_tgen.vsync),

            dvi_pmod = [
                Resource(f"dvi_pmod_t", 0,
                    Subsignal("b3", Pins("1",  conn=("pmod", 0), dir='o')),
                    Subsignal("ck", Pins("2",  conn=("pmod", 0), dir='o')),
                    Subsignal("b0", Pins("3",  conn=("pmod", 0), dir='o')),
                    Subsignal("hs", Pins("4",  conn=("pmod", 0), dir='o')),
                    Subsignal("vs", Pins("10", conn=("pmod", 0), dir='o')),
                    Subsignal("de", Pins("9",  conn=("pmod", 0), dir='o')),
                    Subsignal("b1", Pins("8",  conn=("pmod", 0), dir='o')),
                    Subsignal("b2", Pins("7",  conn=("pmod", 0), dir='o')),
                    Attrs(IO_TYPE="LVCMOS33"),
                ),
                Resource(f"dvi_pmod_b", 0,
                    Subsignal("r3", Pins("1",  conn=("pmod", 1), dir='o')),
                    Subsignal("r1", Pins("2",  conn=("pmod", 1), dir='o')),
                    Subsignal("g3", Pins("3",  conn=("pmod", 1), dir='o')),
                    Subsignal("g1", Pins("4",  conn=("pmod", 1), dir='o')),
                    Subsignal("g0", Pins("10", conn=("pmod", 1), dir='o')),
                    Subsignal("g2", Pins("9",  conn=("pmod", 1), dir='o')),
                    Subsignal("r0", Pins("8",  conn=("pmod", 1), dir='o')),
                    Subsignal("r2", Pins("7",  conn=("pmod", 1), dir='o')),
                    Attrs(IO_TYPE="LVCMOS33"),
                )
            ]
            platform.add_resources(dvi_pmod)
            dvit = platform.request(f"dvi_pmod_t")
            dvib = platform.request(f"dvi_pmod_b")

            m.d.comb += [
                dvit.ck.o.eq(~ClockSignal("dvi")),
                dvit.de.o.eq(s_dvi_de),
                dvit.hs.o.eq(s_dvi_hsync),
                dvit.vs.o.eq(s_dvi_vsync),

                dvit.b3.eq(s_dvi_b[7]),
                dvit.b2.eq(s_dvi_b[6]),
                dvit.b1.eq(s_dvi_b[5]),
                dvit.b0.eq(s_dvi_b[4]),

                dvib.g3.eq(s_dvi_g[7]),
                dvib.g2.eq(s_dvi_g[6]),
                dvib.g1.eq(s_dvi_g[5]),
                dvib.g0.eq(s_dvi_g[4]),

                dvib.r3.eq(s_dvi_r[7]),
                dvib.r2.eq(s_dvi_r[6]),
                dvib.r1.eq(s_dvi_r[5]),
                dvib.r0.eq(s_dvi_r[4]),
            ]

            """
            # Instantiate the DVI PHY itself
            # TODO: port this to Amaranth as well!
            m.submodules.dvi_gen = Instance("dvi_generator",
                i_rst_pix = ResetSignal("dvi"),
                i_clk_pix = ClockSignal("dvi"),
                i_clk_pix_5x = ClockSignal("dvi5x"),

                i_de = s_dvi_de,
                i_data_in_ch0 = s_dvi_b,
                i_data_in_ch1 = s_dvi_g,
                i_data_in_ch2 = s_dvi_r,
                i_ctrl_in_ch0 = Cat(s_dvi_hsync, s_dvi_vsync),
                i_ctrl_in_ch1 = 0,
                i_ctrl_in_ch2 = 0,

                o_tmds_clk_serial = dvi_pins.ck.o,
                o_tmds_ch0_serial = dvi_pins.d0.o,
                o_tmds_ch1_serial = dvi_pins.d1.o,
                o_tmds_ch2_serial = dvi_pins.d2.o,
            )
            """

        # DMA master bus
        bus = self.bus

        # Current offset into the framebuffer
        dma_addr = Signal(32)

        # Length of framebuffer in 32-bit words
        fb_len_words = (self.fb_bytes_per_pixel * (self.fb_hsize*self.fb_vsize)) // 4

        # DMA bus master -> FIFO state machine
        # Burst until FIFO is full, then wait until half empty.

        # Signal from 'dvi' to 'sync' domain to drain FIFO if we are in vsync.
        drain_fifo = Signal(1, reset=0)
        drain_fifo_dvi = Signal(1, reset=0)
        m.submodules.drain_fifo_ff = FFSynchronizer(
                i=drain_fifo, o=drain_fifo_dvi, o_domain="dvi")
        drained = Signal()

        # Read to FIFO in sync domain
        with m.FSM() as fsm:
            with m.State('OFF'):
                with m.If(self.enable):
                    m.next = 'BURST'
            with m.State('BURST'):
                m.d.comb += [
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(0),
                    bus.sel.eq(2**(bus.data_width//8)-1),
                    bus.adr.eq(self.fb_base + dma_addr), # FIXME
                    self.fifo.w_data.eq(bus.dat_r),
                ]
                with m.If(~self.fifo.w_rdy):
                    # FIFO full, hold off for next burst.
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.END_OF_BURST)
                    m.next = 'WAIT'
                with m.Else():
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.INCR_BURST)
                with m.If(bus.stb & bus.ack & self.fifo.w_rdy): # WARN: drops last word
                    m.d.comb += self.fifo.w_en.eq(1)
                    with m.If(dma_addr < (fb_len_words-1)):
                        m.d.sync += dma_addr.eq(dma_addr + 1)
                    with m.Else():
                        m.d.sync += dma_addr.eq(0)
            with m.State('WAIT'):
                with m.If(~phy_vsync_sync):
                    m.d.sync += drained.eq(0)
                with m.If(phy_vsync_sync & ~drained):
                    m.next = 'VSYNC'
                with m.Elif(self.fifo.w_level < self.fifo_depth//2):
                    m.next = 'BURST'
            with m.State('VSYNC'):
                # drain DVI side. We only want to drain once.
                m.d.comb += drain_fifo.eq(1)
                with m.If(self.fifo.w_level == 0):
                    m.d.sync += dma_addr.eq(0)
                    m.d.sync += drained.eq(1)
                    m.next = 'BURST'

        # 'dvi' domain: read FIFO -> DVI PHY (1 fifo word is N pixels)
        bytecounter = self.bytecounter
        last_word   = self.last_word
        with m.If(drain_fifo_dvi):
            m.d.dvi += bytecounter.eq(0)
            m.d.comb += self.fifo.r_en.eq(1),
        with m.Elif(dvi_tgen.de & self.fifo.r_rdy):
            m.d.comb += self.fifo.r_en.eq(bytecounter == 0),
            m.d.dvi += bytecounter.eq(bytecounter+1)
            with m.If(bytecounter == 0):
                m.d.dvi += last_word.eq(self.fifo.r_data)
            with m.Else():
                m.d.dvi += last_word.eq(last_word >> 8)

        rs, gs, bs = self.compute_color_palette()
        m.submodules.palette_r = palette_r = Memory(width=8, depth=256, init=rs)
        m.submodules.palette_g = palette_g = Memory(width=8, depth=256, init=gs)
        m.submodules.palette_b = palette_b = Memory(width=8, depth=256, init=bs)

        rd_port_r = palette_r.read_port(domain="comb")
        rd_port_g = palette_g.read_port(domain="comb")
        rd_port_b = palette_b.read_port(domain="comb")

        # Index by intensity (4-bit) and color (4-bit)
        m.d.comb += rd_port_r.addr.eq(Cat(last_word[0:4], last_word[4:8]))
        m.d.comb += rd_port_g.addr.eq(Cat(last_word[0:4], last_word[4:8]))
        m.d.comb += rd_port_b.addr.eq(Cat(last_word[0:4], last_word[4:8]))

        # hook up to DVI PHY
        m.d.comb += [
            phy_r.eq(rd_port_r.data),
            phy_g.eq(rd_port_g.data),
            phy_b.eq(rd_port_b.data),
        ]

        return m
