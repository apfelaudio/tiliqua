# This file inherits a bit of `interfaces/psram` from LUNA, but is mostly new.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD-3-Clause

from amaranth             import *
from amaranth.lib         import wiring
from amaranth.lib.wiring  import In, flipped
from amaranth.utils       import exact_log2

from amaranth_soc         import wishbone
from amaranth_soc.memory  import MemoryMap

from vendor.psram_ospi    import OSPIPSRAM
from vendor.psram_hyper   import HyperPSRAM
from vendor.dqs_phy       import DQSPHY

from tiliqua              import sim

class Peripheral(wiring.Component):

    """
    Wishbone PSRAM peripheral with multiple masters and burst support.

    You can add this to an SoC as an ordinary peripheral, however it also
    has an internal arbiter (for multiple DMA masters) using add_master().

    Default region name is "ram" as that is accepted by luna-soc SVD generation
    as a memory region, in the future "psram" might also be acceptable.
    """

    def __init__(self, *, size, data_width=32, granularity=8, name="psram"):
        if not isinstance(size, int) or size <= 0 or size & size-1:
            raise ValueError("Size must be an integer power of two, not {!r}"
                             .format(size))
        if size < data_width // granularity:
            raise ValueError("Size {} cannot be lesser than the data width/granularity ratio "
                             "of {} ({} / {})"
                              .format(size, data_width // granularity, data_width, granularity))

        self.size        = size
        self.granularity = granularity
        self.name        = name
        self.mem_depth   = (size * granularity) // data_width

        # memory map
        memory_map = MemoryMap(addr_width=exact_log2(size), data_width=granularity)
        memory_map.add_resource(name=("memory", self.name,), size=size, resource=self)

        # bus
        super().__init__({
            "bus": In(wishbone.Signature(addr_width=exact_log2(self.mem_depth),
                                         data_width=data_width,
                                         granularity=granularity,
                                         features={"cti", "bte"})),
            # internal psram simulation interface
            # should be optimized out in non-sim builds.
            "simif": In(sim.FakePSRAMSimulationInterface())
        })
        self.bus.memory_map = memory_map

        # hram arbiter
        self._hram_arbiter = wishbone.Arbiter(addr_width=exact_log2(self.mem_depth),
                                              data_width=data_width,
                                              granularity=granularity,
                                              features={"cti", "bte"})
        self._hram_arbiter.add(flipped(self.bus))
        self.shared_bus = self._hram_arbiter.bus

    def add_master(self, bus):
        self._hram_arbiter.add(bus)

    def elaborate(self, platform):
        m = Module()

        # arbiter
        m.submodules.arbiter = self._hram_arbiter

        if "APS256XXN" in platform.psram_id:
            self.psram = psram = OSPIPSRAM()
        elif "7KL1282GA" in platform.psram_id:
            self.psram = psram = HyperPSRAM()
        else:
            assert False, f"Unsupported PSRAM: {platform.psram_id}"

        if sim.is_hw(platform):
            # Real PHY and PSRAM controller
            self.psram_phy = DQSPHY()
            wiring.connect(m, psram.phy, self.psram_phy.phy)
            m.submodules += [self.psram_phy, self.psram]
        else:
            # PSRAM controller only, with fake PHY signals and simulation interface.
            m.submodules.psram = psram
            wiring.connect(m, self.simif, flipped(psram.simif))
            # Simulate DATAVALID delay after READ of ~ 8 cycles.
            phy_read_cnt = Signal(8)
            with m.If(psram.phy.read != 0):
                m.d.sync += phy_read_cnt.eq(phy_read_cnt + 1)
            with m.Else():
                m.d.sync += phy_read_cnt.eq(0)
            # Assert minimum PHY signals needed for psram to progress.
            m.d.comb += [
                psram.phy.ready.eq(1),
                psram.phy.burstdet.eq(1),
                psram.phy.datavalid.eq(phy_read_cnt > 8),
            ]

        counter      = Signal(range(128))
        timeout      = Signal(range(128))
        read_counter = Signal(range(32))
        readclksel   = Signal(3, reset=0)

        m.d.comb += [
            psram.single_page            .eq(0),
            psram.phy.readclksel         .eq(readclksel)
        ]

        m.d.sync += [
            psram.register_space         .eq(0),
            psram.start_transfer         .eq(0),
            psram.perform_write          .eq(0),
        ]

        with m.FSM() as fsm:

            # Initialize memory registers (read/write timings) before
            # we kick off memory training.
            for state, state_next, reg_mr, reg_data in platform.psram_registers:
                with m.State(state):
                    with m.If(psram.idle & ~psram.start_transfer):
                        m.d.sync += [
                            psram.start_transfer.eq(1),
                            psram.register_space.eq(1),
                            psram.perform_write .eq(1),
                            psram.address       .eq(reg_mr),
                            psram.register_data .eq(reg_data),
                        ]
                        m.next = state_next

            # Memory read leveling (training to find good readclksel)
            with m.State("TRAIN_INIT"):
                with m.If(psram.idle):
                    m.d.sync += [
                        timeout.eq(0),
                        read_counter.eq(3),
                        psram.start_transfer.eq(1),
                    ]
                    m.next = "TRAIN"
            with m.State("TRAIN"):
                m.d.sync += psram.start_transfer.eq(0),
                m.d.sync += timeout.eq(timeout + 1)
                m.d.comb += psram.final_word.eq(read_counter == 1)
                with m.If(psram.read_ready):
                    m.d.sync += read_counter.eq(read_counter - 1)
                with m.If(timeout == 127):
                    m.next = "WAIT1"
                    m.d.sync += counter.eq(counter + 1)
                    with m.If(counter == 127):
                        m.next = "IDLE"
                    with m.If(~psram.phy.burstdet):
                        m.d.sync += readclksel.eq(readclksel + 1)
                        m.d.sync += counter.eq(0)
            with m.State("WAIT1"):
                m.next = "TRAIN_INIT"

            # Training complete, now we can accept transactions.
            with m.State('IDLE'):
                with m.If(self.shared_bus.cyc & self.shared_bus.stb & psram.idle):
                    m.d.sync += [
                        psram.start_transfer          .eq(1),
                        psram.write_data              .eq(self.shared_bus.dat_w),
                        psram.write_mask              .eq(~self.shared_bus.sel),
                        psram.address                 .eq(self.shared_bus.adr << 2),
                        psram.perform_write           .eq(self.shared_bus.we),
                    ]
                    m.next = 'GO'
            with m.State('GO'):
                with m.If(self.shared_bus.cti != wishbone.CycleType.INCR_BURST):
                    m.d.comb += psram.final_word      .eq(1)
                with m.If(psram.read_ready | psram.write_ready):
                    m.d.comb += [
                        self.shared_bus.dat_r         .eq(psram.read_data),
                        self.shared_bus.ack           .eq(1),
                    ]
                    m.d.sync += [
                        psram.write_data              .eq(self.shared_bus.dat_w),
                        psram.write_mask              .eq(~self.shared_bus.sel),
                    ]
                    with m.If(self.shared_bus.cti != wishbone.CycleType.INCR_BURST):
                        m.d.comb += psram.final_word  .eq(1)
                        m.next = 'IDLE'
                # FIXME: odd case --
                # We have a page crossing during final word assertion, so psram doesn't
                # pick it up, so we have to keep final_word asserted until psram is idle.
                with m.If(~self.shared_bus.cyc & ~self.shared_bus.stb):
                    m.d.comb += psram.final_word.eq(1)
                    m.next = 'ABORT'
            with m.State('ABORT'):
                m.d.comb += psram.final_word.eq(1)
                with m.If(psram.idle):
                    m.next = 'IDLE'

        return m
