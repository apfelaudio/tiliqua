# This file re-uses some of `interfaces/psram` from LUNA.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

from amaranth       import *
from amaranth.utils import log2_int
from amaranth.build import *

from luna_soc.gateware.vendor.lambdasoc.periph    import Peripheral
from luna_soc.gateware.vendor.amaranth_soc        import wishbone
from luna_soc.gateware.vendor.amaranth_soc.memory import MemoryMap
from luna_soc.gateware.vendor.amaranth_soc.periph import ConstantMap

from vendor.psram import HyperRAMDQSPHY, HyperRAMDQSInterface

class FakeHyperRAMDQSInterface(Elaboratable):

    """
    Fake HyperRAMDQSInterface used for simulation.

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
        # signals used for simulation interface
        self.fsm              = Signal(8)
        self.address_ptr      = Signal(32)
        self.read_data_view   = Signal(32)

    def elaborate(self, platform):
        m = Module()
        is_read         = Signal()
        is_register     = Signal()
        is_multipage    = Signal()
        extra_latency   = Signal()
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

class PSRAMPeripheral(Peripheral, Elaboratable):

    """
    Wishbone PSRAM peripheral with multiple masters and burst support.

    You can add this to an SoC as an ordinary peripheral, however it also
    has an internal arbiter (for multiple DMA masters) using add_master().

    Default region name is "ram" as that is accepted by luna-soc SVD generation
    as a memory region, in the future "psram" might also be acceptable.
    """

    def __init__(self, *, size, data_width=32, granularity=8, name="ram", sim=False):
        super().__init__()

        self.name = name

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

        mem_map = MemoryMap(addr_width=log2_int(size), data_width=granularity, name=self.name)
        mem_map.add_resource(self, size=size, name=self.name)
        self.bus.memory_map = mem_map

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

        m.submodules.arbiter = self._hram_arbiter

        if self.sim:
            m.submodules += self.psram
        else:
            self.psram_phy.bus = platform.request('ram', dir={'rwds':'-', 'dq':'-', 'cs':'-'})
            m.submodules += [self.psram_phy, self.psram]

        psram = self.psram

        m.d.comb += [
            psram.single_page      .eq(0),
            psram.register_space.eq(0),
            psram.perform_write.eq(self.shared_bus.we),
        ]

        # PSRAM reset
        counter = Signal(16)
        with m.If(counter < 32768):
            m.d.comb += self.psram_phy.bus.reset.o.eq(0)
            m.d.sync += counter.eq(counter + 1)
        with m.Elif(counter != 65535):
            m.d.comb += self.psram_phy.bus.reset.o.eq(1)
            m.d.sync += counter.eq(counter + 1)
        with m.Else():
            m.d.comb += self.psram_phy.bus.reset.o.eq(0)


        with m.FSM() as fsm:
            with m.State('IDLE'):
                with m.If(self.shared_bus.cyc & self.shared_bus.stb & psram.idle):
                    m.d.sync += [
                        psram.start_transfer.eq(1),
                        psram.write_data.eq(self.shared_bus.dat_w),
                        psram.write_mask.eq(~self.shared_bus.sel),
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
                    m.d.sync += psram.write_mask.eq(~self.shared_bus.sel),
                    with m.If(self.shared_bus.cti != wishbone.CycleType.INCR_BURST):
                        m.d.comb += psram.final_word.eq(1)
                        m.next = 'IDLE'

        return m
