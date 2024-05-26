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

from amaranth.back import verilog

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

        if not self.sim:
            lxvid_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "lxvid.v")
            platform.add_file("build/lxvid.v", open(lxvid_path))

            gpdi = gpdi_from_pmod(platform, 0)

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
        else:
            m.submodules.vlxvid = Instance("lxvid",
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


        m.d.comb += [
            phy_r.eq(last_word[0:8]),
            phy_g.eq(last_word[0:8]),
            phy_b.eq(last_word[0:8]),
        ]

        return m

class Persistance(Elaboratable):

    def __init__(self, fb_base=None, bus_master=None, fifo_depth=8, holdoff=64):
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
                m.d.comb += [
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(1),
                    bus.sel.eq(2**(bus.data_width//8)-1),
                    bus.dat_w.eq((wr_source >> 1) & 0x7f7f7f7f),
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
                        sample_x.eq(pmod0.sample_i[0].sas_value()>>6),
                        sample_y.eq(pmod0.sample_i[1].sas_value()>>6),
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

class FakeHyperRAMDQSInterface(Elaboratable):

    HIGH_LATENCY_CLOCKS = 5

    def __init__(self):

        #
        # I/O port.
        #
        self.reset            = Signal()

        # Control signals.
        self.address          = Signal(32)
        self.register_space   = Signal()
        self.perform_write    = Signal()
        self.single_page      = Signal()
        self.start_transfer   = Signal()
        self.final_word       = Signal()

        # Status signals.
        self.idle             = Signal()
        self.read_ready       = Signal()
        self.write_ready      = Signal()

        # Data signals.
        self.read_data        = Signal(32)
        self.write_data       = Signal(32)

        self.clk = Signal()

        self.fsm = Signal(8)

        self.address_ptr      = Signal(32)
        self.read_data_view   = Signal(32)

    def elaborate(self, platform):
        m = Module()

        #
        # Latched control/addressing signals.
        #
        is_read         = Signal()
        is_register     = Signal()
        is_multipage    = Signal()

        #
        # FSM datapath signals.
        #

        # Tracks whether we need to add an extra latency period between our
        # command and the data body.
        extra_latency   = Signal()

        # Tracks how many cycles of latency we have remaining between a command
        # and the relevant data stages.
        latency_clocks_remaining  = Signal(range(0, self.HIGH_LATENCY_CLOCKS + 1))

        with m.FSM() as fsm:

            with m.State('IDLE'):
                m.d.comb += self.idle        .eq(1)
                with m.If(self.start_transfer):
                    m.next = 'LATCH_RWDS'
                    m.d.sync += [
                        is_read             .eq(~self.perform_write),
                        is_register         .eq(self.register_space),
                        is_multipage        .eq(~self.single_page),
                        # address is specified with 16-bit granularity.
                        # <<1 gets us to 8-bit for our fake uint8 storage.
                        self.address_ptr    .eq(self.address<<1),
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
                    self.read_data     .eq(self.read_data_view),
                    self.read_ready    .eq(1),
                ]
                m.d.sync += self.address_ptr.eq(self.address_ptr + 4)
                with m.If(self.final_word):
                    m.next = 'RECOVERY'
            with m.State("WRITE_DATA"):
                m.d.comb += self.write_ready.eq(1),
                m.d.sync += self.address_ptr.eq(self.address_ptr + 4)
                with m.If(is_register):
                    m.next = 'IDLE'
                with m.Elif(self.final_word):
                    m.next = 'RECOVERY'
            with m.State('RECOVERY'):
                m.d.sync += self.address_ptr.eq(0)
                m.next = 'IDLE'

        m.d.comb += self.fsm.eq(fsm.state)

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
    def __init__(self, *, size, data_width=32, granularity=8, sim=False):
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

        self.sim = sim
        if sim:
            self.psram = FakeHyperRAMDQSInterface()
        else:
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

        if self.sim:
            m.submodules += self.psram
        else:
            self.psram_phy.bus = platform.request('ram', dir={'rwds':'-', 'dq':'-', 'cs':'-'})
            m.submodules += [self.psram_phy, self.psram]
            m.d.comb += self.psram_phy.bus.reset.o        .eq(0),

        psram = self.psram

        m.d.comb += [
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


class FakeEurorackPmod(Elaboratable):

    def __init__(self):
        self.sample_i = [Signal(ASQ) for _ in range(4)]
        self.fs_strobe = Signal()

    def elaborate(self, platform) -> Module:
        m = Module()
        ###
        return m

class TiliquaFakeDomainGenerator(Elaboratable):
    """ Clock generator for Tiliqua platform. """

    def __init__(self, *, clock_frequencies=None, clock_signal_name=None):
        pass

    def elaborate(self, platform):
        m = Module()

        # Create our domains.
        m.domains.sync   = ClockDomain()
        m.domains.usb    = ClockDomain()
        m.domains.fast   = ClockDomain()
        m.domains.audio  = ClockDomain()
        m.domains.raw48  = ClockDomain()
        m.domains.hdmi  = ClockDomain()
        m.domains.hdmi5x  = ClockDomain()

        return m

class VectorScopeTop(Elaboratable):

    def __init__(self, sim=False):

        self.sim = sim

        self.hyperram = HyperRAMPeripheral(
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

        super().__init__()

    def elaborate(self, platform):
        m = Module()

        if self.sim:
            m.submodules.car = TiliquaFakeDomainGenerator()
        else:
            m.submodules.car = TiliquaDomainGenerator()

        if not self.sim:
            self.pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

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

def build_vectorscope():
    overrides = {
        "debug_verilog": True,
        "verbose": True,
        "nextpnr_opts": "--timing-allow-fail"
    }
    TiliquaPlatform().build(VectorScopeTop(), **overrides)

def verilog_vectorscope():
    top = VectorScopeTop(sim=True)
    with open("vectorscope.v", "w") as f:
        f.write(verilog.convert(top, ports={
            "clk_sync":             (ClockSignal("sync"),               None),
            "rst_sync":             (ResetSignal("sync"),               None),
            "clk_hdmi":             (ClockSignal("hdmi"),               None),
            "rst_hdmi":             (ResetSignal("hdmi"),               None),
            "psram_idle":           (top.hyperram.psram.idle,           None),
            "psram_address_ptr":    (top.hyperram.psram.address_ptr,    None),
            "psram_read_data_view": (top.hyperram.psram.read_data_view, None),
            "psram_write_data":     (top.hyperram.psram.write_data,     None),
            "psram_read_ready":     (top.hyperram.psram.read_ready,     None),
            "psram_write_ready":    (top.hyperram.psram.write_ready,    None),
            "video_hcount":         (top.video.vtg_hcount,              None),
            "video_vcount":         (top.video.vtg_vcount,              None),
            "video_r":              (top.video.phy_r,                   None),
            "video_g":              (top.video.phy_g,                   None),
            "video_b":              (top.video.phy_b,                   None),
            "pmod0_fs_strobe":      (top.pmod0.fs_strobe,               None),
            "pmod0_sample_i0":      (top.pmod0.sample_i[0]._target,     None),
            "pmod0_sample_i1":      (top.pmod0.sample_i[1]._target,     None),
            }))
