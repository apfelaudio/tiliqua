# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD--3-Clause

import os

import math

from amaranth              import *
from amaranth.build        import *
from amaranth.lib          import wiring, data
from amaranth.lib.wiring   import In, Out
from amaranth.lib.fifo     import AsyncFIFO, SyncFIFO
from amaranth.lib.cdc      import FFSynchronizer
from amaranth.utils        import log2_int

from amaranth_future       import stream, fixed

from tiliqua.tiliqua_platform import TiliquaPlatform, TiliquaDomainGenerator
from tiliqua                  import eurorack_pmod, dsp
from tiliqua.eurorack_pmod    import ASQ

from amaranth_soc          import wishbone
from tiliqua.psram         import HyperRAMDQSPHY, HyperRAMDQSInterface

def gpdi_from_pmod(platform, pmod_index):
    gpdi = [
        Resource(f"gpdi{pmod_index}", pmod_index,
            Subsignal("data2_p", Pins("1",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("data1_p", Pins("2",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("data0_p", Pins("3",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("clk_p",   Pins("4",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("data2_n", Pins("7",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("data1_n", Pins("8",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("data0_n", Pins("9",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("clk_n",   Pins("10", conn=("pmod", pmod_index), dir='o')),
            Attrs(IO_TYPE="LVCMOS33", SLEWRATE="FAST")
        )
    ]
    platform.add_resources(gpdi)
    return platform.request(f"gpdi{pmod_index}")

class LxVideo(Elaboratable):

    def __init__(self, fb_base=None, bus_master=None, fifo_depth=128):
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

    def elaborate(self, platform) -> Module:
        m = Module()

        m.submodules.fifo = self.fifo

        gpdi = gpdi_from_pmod(platform, 0)

        lxvid_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "lxvid.v")
        platform.add_file("build/lxvid.v", open(lxvid_path))

        vtg_hcount = Signal(12)
        vtg_vcount = Signal(12)

        phy_r = Signal(8)
        phy_g = Signal(8)
        phy_b = Signal(8)

        phy_de_hdmi = Signal()
        phy_vsync_hdmi = Signal()
        phy_vsync_sync = Signal()

        m.submodules.vsync_ff = FFSynchronizer(
                i=phy_vsync_hdmi, o=phy_vsync_sync, o_domain="sync")

        m.submodules.vlxvid = Instance("lxvid",
            i_clk_sys = ClockSignal("sync"),
            i_clk_hdmi = ClockSignal("hdmi"),
            i_clk_hdmi5x = ClockSignal("hdmi5x"),

            i_rst_sys = ResetSignal("sync"),
            i_rst_hdmi = ResetSignal("hdmi"),
            i_rst_hdmi5x = ResetSignal("hdmi5x"),

            o_gpdi_clk_n = gpdi.clk_n.o,
            o_gpdi_clk_p = gpdi.clk_p.o,
            o_gpdi_data0_n = gpdi.data0_n.o,
            o_gpdi_data0_p = gpdi.data0_p.o,
            o_gpdi_data1_n = gpdi.data1_n.o,
            o_gpdi_data1_p = gpdi.data1_p.o,
            o_gpdi_data2_n = gpdi.data2_n.o,
            o_gpdi_data2_p = gpdi.data2_p.o,

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


        m.d.comb += [
            phy_r.eq(last_word[0:8]),
            phy_g.eq(last_word[0:8]),
            phy_b.eq(last_word[0:8]),
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

        self.dma_addr_in = Signal(32, reset=1)
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
                    self.fifo.w_data.eq(bus.dat_r),
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
                    with m.If(dma_addr_in < (fb_len_words-1)):
                        m.d.sync += dma_addr_in.eq(dma_addr_in + 1)
                    with m.Else():
                        m.d.sync += dma_addr_in.eq(0)

            with m.State('WAIT1'):
                m.d.sync += holdoff_count.eq(holdoff_count + 1)
                with m.If(holdoff_count == self.holdoff):
                    m.next = 'BURST-OUT'

            with m.State('BURST-OUT'):
                m.d.sync += holdoff_count.eq(0)
                m.d.comb += [
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(1),
                    bus.sel.eq(2**(bus.data_width//8)-1),
                    bus.adr.eq(self.fb_base + dma_addr_out),
                    bus.dat_w.eq((self.fifo.r_data >> 1) & 0x7f7f7f7f),
                ]

                with m.If(~self.fifo.r_rdy):
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.END_OF_BURST)
                    m.next = 'WAIT2'
                with m.Else():
                    m.d.comb += bus.cti.eq(
                            wishbone.CycleType.INCR_BURST)
                with m.If(bus.stb & bus.ack & self.fifo.r_rdy):
                    m.d.comb += self.fifo.r_en.eq(1)
                    with m.If(dma_addr_out < (fb_len_words-1)):
                        m.d.sync += dma_addr_out.eq(dma_addr_out + 1)
                    with m.Else():
                        m.d.sync += dma_addr_out.eq(0)

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
        self.fs_strobe = Signal(1)

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

        pmod0 = self.pmod0

        m.d.comb += self.fs_strobe.eq(pmod0.fs_strobe)

        px_read = self.px_read
        px_sum = self.px_sum

        inc = 64

        with m.FSM() as fsm:

            with m.State('OFF'):
                with m.If(self.enable):
                    m.next = 'LATCH0'

            with m.State('LATCH0'):

                with m.If(pmod0.fs_strobe):
                    m.d.sync += [
                        sample_x.eq(pmod0.sample_i[0]>>6),
                        sample_y.eq(pmod0.sample_i[1]>>6),
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

                with m.If(px_sum + inc >= 0xFF):
                    m.d.comb += bus.dat_w.eq(px_read | (Const(0xFF, unsigned(32)) << (sample_x[0:2]*8))),
                with m.Else():
                    m.d.comb += bus.dat_w.eq(
                        (px_read & ~(Const(0xFF, unsigned(32)) << (sample_x[0:2]*8))) |
                        ((px_sum + inc) << (sample_x[0:2]*8))
                         )

                """
                m.d.comb += bus.dat_w.eq(0xFFFFFFFF),
                """

                with m.If(bus.stb & bus.ack):
                    m.next = 'LATCH0'

        return m

class HyperRAMPeripheral(Elaboratable):
    """HyperRAM peripheral.

    Parameters
    ----------
    size : int
        Memory size in bytes.
    data_width : int
        Bus data width.
    granularity : int
        Bus granularity.

    Attributes
    ----------
    bus : :class:`amaranth_soc.wishbone.Interface`
        Wishbone bus interface.
    """
    def __init__(self, *, size, data_width=32, granularity=8):
        super().__init__()

        if not isinstance(size, int) or size <= 0 or size & size-1:
            raise ValueError("Size must be an integer power of two, not {!r}"
                             .format(size))
        if size < data_width // granularity:
            raise ValueError("Size {} cannot be lesser than the data width/granularity ratio "
                             "of {} ({} / {})"
                              .format(size, data_width // granularity, data_width, granularity))

        self.mem_depth = (size * granularity) // data_width

        self.bus = wishbone.Interface(addr_width=log2_int(self.mem_depth),
                                      data_width=data_width, granularity=granularity,
                                      features={"cti", "bte"})

        self._hram_arbiter = wishbone.Arbiter(addr_width=log2_int(self.mem_depth),
                                              data_width=data_width, granularity=granularity,
                                              features={"cti", "bte"})
        self._hram_arbiter.add(self.bus)
        self.shared_bus = self._hram_arbiter.bus

        self.size        = size
        self.granularity = granularity

        self.psram_phy = HyperRAMDQSPHY(bus=None)
        self.psram = HyperRAMDQSInterface(phy=self.psram_phy.phy)

    def add_master(self, bus):
        self._hram_arbiter.add(bus)

    @property
    def constant_map(self):
        return ConstantMap(
            SIZE = self.size,
        )

    def elaborate(self, platform):
        m = Module()

        m.submodules.arbiter    = self._hram_arbiter

        self.psram_phy.bus = platform.request('ram', dir={'rwds':'-', 'dq':'-', 'cs':'-'})
        m.submodules += [self.psram_phy, self.psram]
        psram = self.psram

        m.d.comb += [
            self.psram_phy.bus.reset.o        .eq(0),
            psram.single_page      .eq(0),
            psram.register_space   .eq(0),
            psram.perform_write.eq(self.shared_bus.we),
        ]

        with m.FSM() as fsm:
            with m.State('IDLE'):
                with m.If(self.shared_bus.cyc & self.shared_bus.stb & psram.idle):
                    m.d.sync += [
                        psram.start_transfer.eq(1),
                        psram.write_data.eq(self.shared_bus.dat_w),
                        psram.address.eq(self.shared_bus.adr << 1),
                    ]
                    m.next = 'GO'
            with m.State('GO'):
                m.d.sync += psram.start_transfer.eq(0),
                with m.If(self.shared_bus.cti != wishbone.CycleType.INCR_BURST):
                    m.d.comb += psram.final_word.eq(1)
                with m.If(psram.read_ready | psram.write_ready):
                    m.d.comb += self.shared_bus.dat_r.eq(psram.read_data),
                    m.d.comb += self.shared_bus.ack.eq(1)
                    m.d.sync += psram.write_data.eq(self.shared_bus.dat_w),
                    with m.If(self.shared_bus.cti != wishbone.CycleType.INCR_BURST):
                        m.d.comb += psram.final_word.eq(1)
                        m.next = 'IDLE'

        return m

class VectorScopeTop(Elaboratable):

    def __init__(self):


        self.hyperram = HyperRAMPeripheral(size=16*1024*1024)
        self.video = LxVideo(fb_base=0x0, bus_master=self.hyperram.bus)
        self.persist = Persistance(fb_base=0x0, bus_master=self.hyperram.bus)
        self.draw = Draw(fb_base=0x0, bus_master=self.hyperram.bus)

        self.hyperram.add_master(self.video.bus)
        self.hyperram.add_master(self.persist.bus)
        self.hyperram.add_master(self.draw.bus)

        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = TiliquaDomainGenerator()

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

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

def build_vectorscope():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(VectorScopeTop())
