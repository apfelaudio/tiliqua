#
# This inherits a lot from HyperRAMDQSInterface from LUNA.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

"""Driver for HyperRAM, specifically tested on 7KL1282GAHY02."""

from amaranth import *
from amaranth.lib        import wiring
from amaranth.lib.wiring import In, Out

from tiliqua.sim import FakePSRAMSimulationInterface, is_hw
from vendor.dqs_phy import DQSPHYSignature

class HyperPSRAM(wiring.Component):

    """ Gateware interface to HyperRAM series self-refreshing DRAM chips.

    I/O port:
        B: phy              -- The primary physical connection to the DRAM chip.
        I: reset            -- An active-high signal used to provide a prolonged reset upon configuration.

        I: address[32]      -- The address to be targeted by the given operation.
        I: register_space   -- When set to 1, read and write requests target registers instead of normal RAM.
        I: perform_write    -- When set to 1, a transfer request is viewed as a write, rather than a read.
        I: single_page      -- If set, data accesses will wrap around to the start of the current page when done.
        I: start_transfer   -- Strobe that goes high for 1-8 cycles to request a read operation.
                               [This added duration allows other clock domains to easily perform requests.]
        I: final_word       -- Flag that indicates the current word is the last word of the transaction.

        O: read_data[32]    -- word that holds the 32 bits most recently read from the PSRAM
        I: write_data[32]   -- word that accepts the data to output during this transaction

        I: write_mask[4]    -- Mask to select which bits of 'write_data' are written to memory.
                               Unset (or 0) is written to memory. 1 is masked and not written.

        O: idle             -- High whenever the transmitter is idle (and thus we can start a new piece of data.)
        O: read_ready       -- Strobe that indicates when new data is ready for reading
        O: write_ready      -- Strobe that indicates `write_data` has been latched and is ready for new data
    """

    LOW_LATENCY_CLOCKS  = 3
    HIGH_LATENCY_CLOCKS = 5

    # Interface to actual RAM PHY.
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
    # Debug.
    fsm:            Out(unsigned(8))
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

        #
        # FSM datapath signals.
        #

        # Tracks whether we need to add an extra latency period between our
        # command and the data body.
        extra_latency   = Signal()

        # Tracks how many cycles of latency we have remaining between a command
        # and the relevant data stages.
        latency_clocks_remaining  = Signal(range(0, self.HIGH_LATENCY_CLOCKS + 1))

        #
        # Core operation FSM.
        #

        # Provide defaults for our control/status signals.
        m.d.sync += [
            self.phy.clk_en     .eq(0b11),
            self.phy.cs         .eq(1),
            self.phy.rwds.e     .eq(0),
            self.phy.dq.e       .eq(0),
            self.phy.read       .eq(0),
        ]
        m.d.comb += self.write_ready.eq(0),

        # Commands, in order of bytes sent:
        #   - WRBAAAAA
        #     W         => selects read or write; 1 = read, 0 = write
        #      R        => selects register or memory; 1 = register, 0 = memory
        #       B       => selects burst behavior; 0 = wrapped, 1 = linear
        #        AAAAA  => address bits [27:32]
        #
        #   - AAAAAAAA  => address bits [19:27]
        #   - AAAAAAAA  => address bits [11:19]
        #   - AAAAAAAA  => address bits [ 3:16]
        #   - 00000000  => [reserved]
        #   - 00000AAA  => address bits [ 0: 3]
        ca = Signal(48)
        m.d.comb += ca.eq(Cat(
            (current_address>>1)[0:3],
            Const(0, 13),
            (current_address>>1)[3:32],
            is_multipage,
            is_register,
            is_read
        ))

        if not is_hw(platform):
            m.d.comb += [
                self.simif.write_data .eq(self.write_data),
                self.simif.read_ready .eq(self.read_ready),
                self.simif.write_ready.eq(self.write_ready),
                self.simif.idle       .eq(self.idle),
                self.simif.address_ptr.eq(current_address),
            ]

        with m.FSM() as fsm:

            # IDLE state: waits for a transaction request
            with m.State('IDLE'):
                m.d.comb += self.idle        .eq(self.phy.ready)
                m.d.sync += self.phy.clk_en  .eq(0)

                # Once we have a transaction request, latch in our control
                # signals, and assert our chip-select.
                with m.If(self.start_transfer):
                    m.next = 'LATCH_RWDS'

                    m.d.sync += [
                        is_read             .eq(~self.perform_write),
                        is_register         .eq(self.register_space),
                        is_multipage        .eq(~self.single_page),
                        current_address     .eq(self.address),
                        self.phy.dq.o       .eq(0),
                    ]

                with m.Else():
                    m.d.sync += self.phy.cs.eq(0)


            # LATCH_RWDS -- latch in the value of the RWDS signal,
            # which determines our read/write latency.
            with m.State("LATCH_RWDS"):
                m.d.sync += extra_latency.eq(self.phy.rwds.i),
                m.d.sync += self.phy.clk_en.eq(0b11)
                m.next="SHIFT_COMMAND0"


            # SHIFT_COMMANDx -- shift each of our command words out
            with m.State('SHIFT_COMMAND0'):
                # Output the first 32 bits of our command.
                m.d.sync += [
                    self.phy.dq.o.eq(Cat(ca[16:48])),
                    self.phy.dq.e.eq(1),
                ]
                m.next = 'SHIFT_COMMAND1'

            with m.State('SHIFT_COMMAND1'):
                # Output the remaining 32 bits of our command.
                m.d.sync += [
                    self.phy.dq.o.eq(Cat(Const(0, 16), ca[0:16])),
                    self.phy.dq.e.eq(1),
                ]

                # If we have a register write, we don't need to handle
                # any latency. Move directly to our SHIFT_DATA state.
                with m.If(is_register & ~is_read):
                    m.next = 'WRITE_DATA'

                # Otherwise, react with either a short period of latency
                # or a longer one, depending on what the RAM requested via
                # RWDS.
                with m.Else():
                    m.next = "HANDLE_LATENCY"

                    # FIXME: our HyperRAM part has a fixed latency, but we could need to detect 
                    # different variants from the configuration register in the future.
                    with m.If(extra_latency | 1):
                        m.d.sync += latency_clocks_remaining.eq(self.HIGH_LATENCY_CLOCKS)
                    with m.Else():
                        m.d.sync += latency_clocks_remaining.eq(self.LOW_LATENCY_CLOCKS)


            # HANDLE_LATENCY -- applies clock cycles until our latency period is over.
            with m.State('HANDLE_LATENCY'):
                m.d.sync += latency_clocks_remaining.eq(latency_clocks_remaining - 1)
                with m.If(latency_clocks_remaining == 0):
                    with m.If(is_read):
                        m.next = 'READ_DATA'
                    with m.Else():
                        m.d.sync += self.phy.rwds.o.eq(self.write_mask),
                        m.next = 'WRITE_DATA'


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

                    m.d.sync += current_address.eq(current_address + 4)

                    if not is_hw(platform):
                        m.d.comb += self.read_data.eq(self.simif.read_data_view),

                    # If our controller is done with the transaction, end it.
                    with m.If(self.final_word):
                        m.d.sync += self.phy.clk_en.eq(0),
                        m.next = 'RECOVERY'

                with m.If(~self.phy.ready):
                    m.next = 'IDLE'

            # WRITE_DATA -- write a word to the PSRAM
            with m.State("WRITE_DATA"):
                m.d.sync += [
                    self.phy.dq.o    .eq(self.write_data),
                    self.phy.dq.e    .eq(1),
                    self.phy.rwds.e  .eq(~is_register),
                    self.phy.rwds.o  .eq(self.write_mask),
                ]
                m.d.comb += self.write_ready.eq(1),

                m.d.sync += current_address.eq(current_address + 4)

                # If we just finished a register write, we're done -- there's no need for recovery.
                with m.If(is_register):
                    m.next = 'IDLE'

                with m.Elif(self.final_word):
                    m.d.sync += self.phy.clk_en .eq(0)
                    m.next = 'RECOVERY'


            # RECOVERY state: wait for the required period of time before a new transaction
            with m.State('RECOVERY'):
                m.d.sync += self.phy.clk_en .eq(0)

                # TODO: implement recovery
                m.next = 'IDLE'

        m.d.comb += self.fsm.eq(fsm.state)

        return m
