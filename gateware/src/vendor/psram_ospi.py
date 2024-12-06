#
# This inherits a bit from HyperRAMDQSInterface from LUNA.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD-3-Clause

""" Driver for oSPI-PSRAM, specifically tested on APS256XXN-OBR."""

from amaranth import *
from amaranth.lib        import wiring
from amaranth.lib.wiring import In, Out

from tiliqua.sim import FakePSRAMSimulationInterface, is_hw
from vendor.dqs_phy import DQSPHYSignature

class OSPIPSRAM(wiring.Component):

    """
    Driver for oSPI self-refreshing DRAM chips (e.g. APS256XXN-OBR).

    Some differences between this and the HyperPSRAM driver:
    - Command and address formatting is completely different.
    - Some register writes are required for initial reset handling.
    - CS must be held high and commands reissued on page crossings.

    """

    READ_LATENCY_CLOCKS  = 0
    WRITE_LATENCY_CLOCKS = 1

    # FSM cycles necessary to leave CS high when crossing pages.
    CROSS_PAGE_CLOCKS    = 2

    PAGE_SIZE_BYTES      = 2048

    # Interface to actual (4:1) RAM PHY.
    phy:            Out(DQSPHYSignature())
    # Control.
    address:         In(unsigned(32))
    register_space:  In(unsigned(1))
    perform_write:   In(unsigned(1))
    single_page:     In(unsigned(1))
    start_transfer:  In(unsigned(1))
    final_word:      In(unsigned(1))
    # Status.
    idle:           Out(unsigned(1))
    read_ready:     Out(unsigned(1))
    write_ready:    Out(unsigned(1))
    # Data.
    read_data:      Out(unsigned(32))
    write_data:      In(unsigned(32))
    write_mask:      In(unsigned(4))
    register_data:   In(unsigned(8))
    # Debug.
    current_address: Out(unsigned(32))
    fsm:             Out(unsigned(8))
    # Simulation.
    simif:           In(FakePSRAMSimulationInterface())

    def elaborate(self, platform):
        m = Module()

        #
        # Latched control/addressing signals.
        #
        is_read         = Signal()
        is_register     = Signal()
        current_address = Signal(32)
        is_multipage    = Signal()
        register_data   = Signal(8)

        m.d.comb += self.current_address.eq(current_address)

        #
        # FSM datapath signals.
        #

        # Tracks how many cycles of latency we have remaining between a command
        # and the relevant data stages.
        latency_clocks_remaining = Signal(4)

        #
        # Core operation FSM.
        #

        # Provide defaults for our control/status signals.
        m.d.sync += [
            self.phy.clk_en     .eq(0b11),
            self.phy.cs         .eq(1),
            self.phy.rwds.e     .eq(0),
            self.phy.dq.e       .eq(0),
            self.phy.dq.o       .eq(0),
            self.phy.read       .eq(0),
        ]
        m.d.comb += self.write_ready.eq(0),

        ca = Signal(64)
        m.d.comb += ca.eq(Cat(
            Const(0, 8),
            register_data,
            current_address[0:32],
            Const(0x00, 4),
            Const(0, 1),
            Const(0, 1),
            is_register,
            ~is_read,
            Const(0x00, 4),
            Const(0, 1),
            Const(0, 1),
            is_register,
            ~is_read,
        ))

        reset_timer = Signal(16, reset=32768)
        cross_page = Signal(8, reset=0)

        if not is_hw(platform):
            m.d.comb += [
                self.simif.write_data .eq(self.write_data),
                self.simif.read_ready .eq(self.read_ready),
                self.simif.write_ready.eq(self.write_ready),
                self.simif.idle       .eq(self.idle),
                self.simif.address_ptr.eq(current_address),
            ]

        with m.FSM() as fsm:

            # APS256XXN-OBR has no dedicated reset line.
            # We must issue the command 0xFFFFFFFF manually, before
            # proceeding with any ordinary register writes or transactions.

            with m.State('PREINIT'):
                m.d.sync += reset_timer.eq(reset_timer - 1)
                m.d.sync += self.phy.cs.eq(0)
                with m.If(reset_timer == 0 & self.phy.ready):
                    m.d.sync += reset_timer.eq(32768)
                    m.next='INIT'
            with m.State('INIT'):
                m.d.sync += self.phy.clk_en.eq(0b11)
                m.d.sync += self.phy.cs.eq(0)
                m.d.sync += [
                    self.phy.dq.o.eq(0xFFFFFFFF),
                    self.phy.dq.e.eq(1),
                ]
                m.next="INIT_COMMAND0"
            with m.State('INIT_COMMAND0'):
                # Output the first 32 bits of our command.
                m.d.sync += [
                    self.phy.dq.o.eq(0xFFFFFFFF),
                    self.phy.dq.e.eq(1),
                ]
                m.next = 'INIT_COMMAND1'
            with m.State('INIT_COMMAND1'):
                # Output the next 32 bits of our command.
                m.d.sync += [
                    self.phy.dq.o.eq(0xFFFFFFFF),
                    self.phy.dq.e.eq(1),
                    self.phy.clk_en.eq(0)
                ]
                m.next = 'WAIT_RESET'
            with m.State('WAIT_RESET'):
                m.d.sync += reset_timer.eq(reset_timer - 1)
                m.d.sync += self.phy.cs.eq(0)
                with m.If(reset_timer == 0):
                    m.d.sync += self.phy.dq.o.eq(0),
                    m.next = 'IDLE'

            # Memory has been reset. Now we can proceeed with training/transactions.

            # IDLE state: waits for a transaction request
            with m.State('IDLE'):
                m.d.comb += self.idle        .eq(self.phy.ready)
                m.d.sync += self.phy.clk_en  .eq(0)

                # Once we have a transaction request, latch in our control
                # signals, and assert our chip-select.
                with m.If(self.start_transfer):
                    m.next = 'START_CLK'

                    m.d.sync += [
                        is_read             .eq(~self.perform_write),
                        is_register         .eq(self.register_space),
                        register_data       .eq(self.register_data),
                        is_multipage        .eq(~self.single_page),
                        current_address     .eq(self.address),
                        self.phy.dq.o       .eq(0),
                    ]

                with m.Else():
                    m.d.sync += self.phy.cs.eq(0)

            # START_CLK -- latch in the value of the RWDS signal,
            # which determines our read/write latency.
            with m.State("START_CLK"):
                m.d.sync += self.phy.clk_en.eq(0b11)
                m.next="SHIFT_COMMAND0"


            # SHIFT_COMMANDx -- shift each of our command words out
            with m.State('SHIFT_COMMAND0'):
                # Output the first 32 bits of our command.
                m.d.sync += [
                    self.phy.dq.o.eq(Cat(ca[32:64])),
                    self.phy.dq.e.eq(1),
                ]
                m.next = 'SHIFT_COMMAND1'

            with m.State('SHIFT_COMMAND1'):
                # Output the remaining 32 bits of our command.
                m.d.sync += [
                    self.phy.dq.o.eq(ca[0:32]),
                    self.phy.dq.e.eq(1),
                ]

                # If we have a register write, we're done after the command.
                with m.If(is_register & ~is_read):
                    m.d.sync += [
                        cross_page.eq(self.CROSS_PAGE_CLOCKS),
                        self.phy.clk_en.eq(0),
                    ]
                    m.next = 'RECOVERY'

                # Otherwise, react with either a short period of latency
                # or a longer one, depending whether we are reading or writing.
                with m.Else():
                    m.next = "HANDLE_LATENCY"
                    with m.If(is_read):
                        m.d.sync += latency_clocks_remaining.eq(self.READ_LATENCY_CLOCKS)
                    with m.Else():
                        m.d.sync += latency_clocks_remaining.eq(self.WRITE_LATENCY_CLOCKS)


            # HANDLE_LATENCY -- applies clock cycles until our latency period is over.
            with m.State('HANDLE_LATENCY'):
                m.d.sync += latency_clocks_remaining.eq(latency_clocks_remaining - 1)
                with m.If(latency_clocks_remaining == 0):
                    with m.If(is_read):
                        m.next = 'READ_DATA'
                    with m.Else():
                        m.d.sync += self.phy.rwds.o.eq(self.write_mask),
                        m.next = 'WRITE_DATA'

            with m.State('CROSS_PAGE'):
                m.d.sync += [
                    cross_page.eq(cross_page-1),
                ]
                with m.If(cross_page == 0):
                    m.d.sync += self.phy.dq.o.eq(0),
                    m.d.sync += self.phy.clk_en.eq(0),
                    m.next = 'START_CLK'
                with m.Else():
                    m.d.sync += self.phy.cs.eq(0)

            # READ_DATA -- reads words from the PSRAM
            with m.State('READ_DATA'):
                m.d.sync += self.phy.read.eq(0b11)
                datavalid_delay = Signal()
                m.d.sync += datavalid_delay.eq(self.phy.datavalid)
                with m.If(self.phy.datavalid):

                    m.d.comb += [
                        self.read_data     .eq(self.phy.dq.i),
                        self.read_ready    .eq(1),
                    ]

                    if not is_hw(platform):
                        m.d.comb += self.read_data.eq(self.simif.read_data_view),

                    m.d.sync += current_address.eq(current_address + 4)

                    # If our controller is done with the transaction, end it.
                    with m.If(self.final_word):
                        m.d.sync += [
                            cross_page.eq(self.CROSS_PAGE_CLOCKS),
                            self.phy.clk_en.eq(0),
                        ]
                        m.next = 'RECOVERY'
                    # we are about to cross a page boundary
                    with m.Elif((current_address & (self.PAGE_SIZE_BYTES-1)) == (self.PAGE_SIZE_BYTES-4)):
                        m.d.sync += [
                            cross_page.eq(self.CROSS_PAGE_CLOCKS),
                            self.phy.clk_en.eq(0),
                        ]
                        m.next = 'CROSS_PAGE'

            # WRITE_DATA -- write a word to the PSRAM
            with m.State("WRITE_DATA"):
                m.d.sync += [
                    self.phy.dq.o    .eq(self.write_data),
                    self.phy.dq.e    .eq(1),
                    self.phy.rwds.e  .eq(1),
                    self.phy.rwds.o  .eq(self.write_mask),
                ]
                m.d.comb += self.write_ready.eq(1),

                m.d.sync += current_address.eq(current_address + 4)

                with m.If(self.final_word):
                    m.d.sync += [
                        cross_page.eq(self.CROSS_PAGE_CLOCKS),
                        self.phy.clk_en.eq(0),
                    ]
                    m.next = 'RECOVERY'
                # we are about to cross a page boundary
                with m.Elif((current_address & (self.PAGE_SIZE_BYTES-1)) == (self.PAGE_SIZE_BYTES-4)):
                    m.d.sync += [
                        cross_page.eq(self.CROSS_PAGE_CLOCKS),
                        self.phy.clk_en.eq(0),
                    ]
                    m.next = 'CROSS_PAGE'


            # RECOVERY state: wait for the required period of time before a new transaction
            with m.State('RECOVERY'):
                m.d.sync += [
                    cross_page.eq(cross_page-1),
                    self.phy.cs.eq(0),
                    self.phy.clk_en.eq(0),
                ]
                with m.If(cross_page == 0):
                    m.next = 'IDLE'

        m.d.comb += self.fsm.eq(fsm.state)

        return m
