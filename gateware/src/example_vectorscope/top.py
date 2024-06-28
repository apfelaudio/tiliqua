# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD--3-Clause

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

class LxVideo(Elaboratable):

    def __init__(self, fb_base=None, bus_master=None, fifo_depth=128, sim=False):
        super().__init__()

        self.bus = wishbone.Interface(addr_width=bus_master.addr_width, data_width=32, granularity=8,
                                      features={"cti", "bte"})

        self.fifo = AsyncFIFO(width=32, depth=fifo_depth, r_domain='hdmi', w_domain='sync')

        self.fifo_depth = fifo_depth
        self.fb_base = fb_base
        self.fb_hsize = 720
        self.fb_vsize = 720

        self.dma_addr = Signal(32)

        # hdmi domain
        self.bytecounter = Signal(2)
        self.last_word   = Signal(32)
        self.consume_started = Signal(1, reset=0)

        self.enable = Signal(1, reset=0)

        self.sim = sim

        self.vtg_hcount = Signal(12)
        self.vtg_vcount = Signal(12)

        self.phy_r = Signal(8)
        self.phy_g = Signal(8)
        self.phy_b = Signal(8)

    def elaborate(self, platform) -> Module:
        m = Module()

        m.submodules.fifo = self.fifo

        vtg_hcount = self.vtg_hcount
        vtg_vcount = self.vtg_vcount

        phy_r = self.phy_r
        phy_g = self.phy_g
        phy_b = self.phy_b

        phy_de_hdmi = Signal()
        phy_vsync_hdmi = Signal()
        phy_vsync_sync = Signal()

        m.submodules.vsync_ff = FFSynchronizer(
                i=phy_vsync_hdmi, o=phy_vsync_sync, o_domain="sync")

        def add_sv(f):
            path = os.path.join("src/vendor/dvi", f)
            platform.add_file(f"build/{f}", open(path))

        if not self.sim:

            add_sv("vtg.sv")
            add_sv("simple_720p.sv")
            add_sv("tmds_encoder_dvi.sv")
            add_sv("dvi_generator.sv")

            dvi_pins = platform.request("dvi")

            m.submodules.vlxvid = Instance("vtg",
                i_clk_sys = ClockSignal("sync"),
                i_clk_hdmi = ClockSignal("hdmi"),
                i_clk_hdmi5x = ClockSignal("hdmi5x"),

                i_rst_sys = ResetSignal("sync"),
                i_rst_hdmi = ResetSignal("hdmi"),
                i_rst_hdmi5x = ResetSignal("hdmi5x"),

                o_gpdi_clk_p   = dvi_pins.pck.o,
                o_gpdi_data0_p = dvi_pins.pd0.o,
                o_gpdi_data1_p = dvi_pins.pd1.o,
                o_gpdi_data2_p = dvi_pins.pd2.o,
                o_gpdi_clk_n   = dvi_pins.nck.o,
                o_gpdi_data0_n = dvi_pins.nd0.o,
                o_gpdi_data1_n = dvi_pins.nd1.o,
                o_gpdi_data2_n = dvi_pins.nd2.o,

                o_vtg_hcount = vtg_hcount,
                o_vtg_vcount = vtg_vcount,
                o_phy_vsync  = phy_vsync_hdmi,
                o_phy_de  = phy_de_hdmi,

                i_phy_r = phy_r,
                i_phy_g = phy_g,
                i_phy_b = phy_b,
            )
        else:

            m.submodules.vlxvid = Instance("vtg",
                i_clk_sys = ClockSignal("sync"),
                i_clk_hdmi = ClockSignal("hdmi"),
                i_clk_hdmi5x = ClockSignal("hdmi5x"),

                i_rst_sys = ResetSignal("sync"),
                i_rst_hdmi = ResetSignal("hdmi"),
                i_rst_hdmi5x = ResetSignal("hdmi5x"),

                o_vtg_hcount = vtg_hcount,
                o_vtg_vcount = vtg_vcount,
                o_phy_vsync  = phy_vsync_hdmi,
                o_phy_de  = phy_de_hdmi,

                i_phy_r = phy_r,
                i_phy_g = phy_g,
                i_phy_b = phy_b,
            )

        # how?

        # 2 separate state machines, one in each sys / hdmi clk domain?

        # START
        # - load fifos
        # - wait for vtg 0, 0 (or 1 clock before)
        # THEN
        # - drain fifo on every clock
        # - burst read hyperram on every level = depth/2
        # - make sure correct burst size wraps correctly

        bus = self.bus

        dma_addr = self.dma_addr

        fb_len_words = (self.fb_hsize*self.fb_vsize) // 4


        drain_fifo = Signal(1, reset=0)
        drain_fifo_hdmi = Signal(1, reset=0)

        m.submodules.drain_fifo_ff = FFSynchronizer(
                i=drain_fifo, o=drain_fifo_hdmi, o_domain="hdmi")

        # bus -> FIFO
        # burst until FIFO is full, then wait until half empty.

        drained = Signal()

        # sync domain
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
                # drain HDMI side. We only want to drain once.
                m.d.comb += drain_fifo.eq(1)
                with m.If(self.fifo.w_level == 0):
                    m.d.sync += dma_addr.eq(0)
                    m.d.sync += drained.eq(1)
                    m.next = 'BURST'

        # FIFO -> PHY (1 word -> 4 pixels)

        bytecounter = self.bytecounter
        last_word   = self.last_word

        with m.If(drain_fifo_hdmi):
            m.d.hdmi += bytecounter.eq(0)
            m.d.comb += self.fifo.r_en.eq(1),
        with m.Elif(phy_de_hdmi & self.fifo.r_rdy):
            m.d.comb += self.fifo.r_en.eq(bytecounter == 0),
            m.d.hdmi += bytecounter.eq(bytecounter+1)
            with m.If(bytecounter == 0):
                m.d.hdmi += last_word.eq(self.fifo.r_data)
            with m.Else():
                m.d.hdmi += last_word.eq(last_word >> 8)

        import colorsys
        n_i = 16
        n_c = 16
        rs, gs, bs = [], [], []
        for i in range(n_i):
            for c in range(n_c):
                r, g, b = colorsys.hls_to_rgb(float(c)/n_c, float(1.35**(i+1))/(1.35**n_i), 0.75)
                rs.append(int(r*255))
                gs.append(int(g*255))
                bs.append(int(b*255))

        m.submodules.palette_r = palette_r = Memory(width=8, depth=256, init=rs)
        m.submodules.palette_g = palette_g = Memory(width=8, depth=256, init=gs)
        m.submodules.palette_b = palette_b = Memory(width=8, depth=256, init=bs)

        rd_port_r = palette_r.read_port(domain="comb")
        rd_port_g = palette_g.read_port(domain="comb")
        rd_port_b = palette_b.read_port(domain="comb")

        m.d.comb += rd_port_r.addr.eq(Cat(last_word[0:4], last_word[4:8]))
        m.d.comb += rd_port_g.addr.eq(Cat(last_word[0:4], last_word[4:8]))
        m.d.comb += rd_port_b.addr.eq(Cat(last_word[0:4], last_word[4:8]))

        m.d.comb += [
            phy_r.eq(rd_port_r.data),
            phy_g.eq(rd_port_g.data),
            phy_b.eq(rd_port_b.data),
        ]

        return m

class Persistance(Elaboratable):

    def __init__(self, fb_base=None, bus_master=None, fifo_depth=128, holdoff=1024):
        super().__init__()

        self.bus = wishbone.Interface(addr_width=bus_master.addr_width, data_width=32, granularity=8,
                                      features={"cti", "bte"})

        self.fifo = SyncFIFO(width=32, depth=fifo_depth)

        self.holdoff = holdoff

        self.fifo_depth = fifo_depth
        self.fb_base = fb_base
        self.fb_hsize = 720
        self.fb_vsize = 720

        self.dma_addr_in = Signal(32, reset=0)
        self.dma_addr_out = Signal(32)

        self.enable = Signal(1, reset=0)

    def elaborate(self, platform) -> Module:
        m = Module()

        m.submodules.fifo = self.fifo

        bus = self.bus

        dma_addr_in = self.dma_addr_in
        dma_addr_out = self.dma_addr_out

        fb_len_words = (self.fb_hsize*self.fb_vsize) // 4

        holdoff_count = Signal(32)

        pnext = Signal(32)
        wr_source = Signal(32)

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
                with m.If(holdoff_count == self.holdoff):
                    m.d.sync += pnext.eq(self.fifo.r_data)
                    m.d.comb += self.fifo.r_en.eq(1)
                    m.next = 'BURST-OUT'

            with m.State('BURST-OUT'):
                m.d.sync += holdoff_count.eq(0)

                pixa = Signal(data.ArrayLayout(unsigned(8), 4))
                pixb = Signal(data.ArrayLayout(unsigned(8), 4))

                m.d.comb += [
                    pixa.eq(wr_source),
                ]

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
                with m.If(holdoff_count == self.holdoff):
                    m.next = 'BURST-IN'

        return m

class Draw(Elaboratable):

    def __init__(self, fb_base=None, bus_master=None, pmod0=None):
        super().__init__()

        self.bus = wishbone.Interface(addr_width=bus_master.addr_width, data_width=32, granularity=8,
                                      features={"cti", "bte"})

        self.fb_base = fb_base
        self.fb_hsize = 720
        self.fb_vsize = 720

        self.sample_x = Signal(signed(16))
        self.sample_y = Signal(signed(16))
        self.sample_p = Signal(signed(16))
        self.sample_c = Signal(signed(16))

        self.enable = Signal(1, reset=0)

        self.px_read = Signal(32)
        self.px_sum = Signal(16)

        self.pmod0 = pmod0


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
                with m.If(merge.o.valid):
                    m.d.sync += [
                        sample_x.eq(merge.o.payload[0].sas_value()>>6),
                        sample_y.eq(merge.o.payload[1].sas_value()>>6),
                        sample_p.eq(merge.o.payload[2].sas_value()),
                        sample_c.eq(merge.o.payload[3].sas_value()),
                    ]
                    m.next = 'LATCH1'

            with m.State('LATCH1'):
                m.d.sync += [
                    bus.sel.eq(0xf),
                    bus.adr.eq(self.fb_base + (sample_y + 360)*(720//4) + (90 + (sample_x >> 2))),
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

                new_color = Signal(unsigned(4))
                white = Signal(unsigned(4))
                m.d.comb += white.eq(0xf)
                m.d.comb += new_color.eq(sample_c>>10)
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

    def __init__(self, sim=False):

        self.sim = sim

        self.hyperram = PSRAMPeripheral(
                size=16*1024*1024, sim=sim)
        self.video = LxVideo(fb_base=0x0, bus_master=self.hyperram.bus,
                             sim=sim)
        self.persist = Persistance(fb_base=0x0, bus_master=self.hyperram.bus)
        self.draw = Draw(fb_base=0x0, bus_master=self.hyperram.bus)

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
            m.submodules.car = TiliquaDomainGenerator()

        if not self.sim:
            self.pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True,
                touch_enabled=False)

        pmod0 = self.pmod0
        m.submodules.pmod0 = pmod0
        self.draw.pmod0 = pmod0

        m.submodules.hyperram = self.hyperram
        m.submodules.video = self.video
        m.submodules.persist = self.persist
        m.submodules.draw = self.draw

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
        "nextpnr_opts": "--timing-allow-fail"
    }
    TiliquaPlatform().build(VectorScopeTop(), **overrides)

def sim():

    build_dst = "build"
    dst = f"{build_dst}/vectorscope.v"
    print(f"write verilog implementation of 'example_vectorscope' to '{dst}'...")

    top = VectorScopeTop(sim=True)

    os.makedirs(build_dst, exist_ok=True)
    with open(dst, "w") as f:
        f.write(verilog.convert(top, ports=[
            ClockSignal("sync"),
            ResetSignal("sync"),
            ClockSignal("hdmi"),
            ResetSignal("hdmi"),
            ClockSignal("audio"),
            ResetSignal("audio"),
            top.hyperram.psram.idle,
            top.hyperram.psram.address_ptr,
            top.hyperram.psram.read_data_view,
            top.hyperram.psram.write_data,
            top.hyperram.psram.read_ready,
            top.hyperram.psram.write_ready,
            top.video.vtg_hcount,
            top.video.vtg_vcount,
            top.video.phy_r,
            top.video.phy_g,
            top.video.phy_b,
            top.pmod0.fs_strobe,
            top.inject0,
            top.inject1,
            top.inject2,
            top.inject3,
            ]))

    hdmi_clk_hz = 60000000
    sync_clk_hz = 60000000
    audio_clk_hz = 48000

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
                           "-CFLAGS", f"-DHDMI_CLK_HZ={hdmi_clk_hz}",
                           "-CFLAGS", f"-DSYNC_CLK_HZ={sync_clk_hz}",
                           "-CFLAGS", f"-DAUDIO_CLK_HZ={audio_clk_hz}",
                           "../../src/example_vectorscope/sim/sim.cpp",
                           f"{dst}",
                           "src/vendor/dvi/simple_720p.sv",
                           "src/vendor/dvi/vtg_sim.sv",
                           ],
                          env=os.environ)

    print(f"run verilated binary '{verilator_dst}/Vvectorscope'...")
    subprocess.check_call([f"{verilator_dst}/Vvectorscope"],
                          env=os.environ)

    print(f"done.")
