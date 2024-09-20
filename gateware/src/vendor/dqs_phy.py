#
# This inherits a lot from HyperRAMDQSPHY from LUNA.
# Modified a little so it works easily with both HyperRAM and oSPI-RAM.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

"""
32-bit (4:1) DDR PSRAM PHY for ECP5, makes heavy use DQS memory controller hardware.
"""

from amaranth import *
from amaranth.lib        import wiring
from amaranth.lib.wiring import In, Out

class DQSPHYSignature(wiring.Signature):
    """
    Interface between a 32-bit (4:1) DQSPHY() and higher-level PSRAM drivers.
    """
    def __init__(self):
        class IOBusSignature(wiring.Signature):
            def __init__(self, *, n_io, n_e=1):
                super().__init__({
                    "i":  In(unsigned(n_io)),
                    "o": Out(unsigned(n_io)),
                    "e": Out(unsigned(n_e)),
                })
        super().__init__({
            "clk_en":         Out(unsigned(2)),
            "dq":             Out(IOBusSignature(n_io=32)),
            "rwds":           Out(IOBusSignature(n_io=4)),
            "cs":             Out(unsigned(1)),
            "reset":          Out(unsigned(1)),
            "read":           Out(unsigned(2)),
            "datavalid":       In(unsigned(1)),
            "burstdet":        In(unsigned(1)),
            "readclksel":     Out(unsigned(3)),
            "ready":           In(unsigned(1)),
        })

class DQSPHY(wiring.Component):
    """
    32-bit PSRAM PHY, based on ECP5 DQS hard macros.
    This works with HyperRAM and oSPI-RAM.
    Tested up to 200MHz DDR (400MT/sec) on lowest speed grade ECP5.
    """

    # Interface to the higher-level PSRAM driver.
    phy: In(DQSPHYSignature())

    def elaborate(self, platform):
        m = Module()

        self.bus = platform.request('ram', dir={'rwds':'-', 'dq':'-', 'cs':'-'})

        # Handle initial DDRDLL lock & delay code update
        pause = Signal()
        freeze = Signal()
        lock = Signal()
        uddcntln = Signal()
        counter = Signal(range(9))
        readclksel = Signal.like(self.phy.readclksel, reset=2)
        m.d.sync += counter.eq(counter + 1)
        m.d.comb += self.phy.ready.eq(0)
        with m.FSM() as fsm:
            with m.State('INIT'):
                m.d.sync += [
                    pause.eq(1),
                    freeze.eq(0),
                    uddcntln.eq(0),
                ]

                with m.If(lock):
                    m.next = 'FREEZE'
                    m.d.sync += [
                        freeze.eq(1),
                        counter.eq(0),
                    ]

            with m.State('FREEZE'):
                with m.If(counter == 8):
                    m.next = 'UPDATE'
                    m.d.sync += [
                        uddcntln.eq(1),
                        counter.eq(0),
                    ]

            with m.State('UPDATE'):
                with m.If(counter == 8):
                    m.next = 'UPDATED'
                    m.d.sync += [
                        uddcntln.eq(0),
                        counter.eq(0),
                    ]

            with m.State('UPDATED'):
                with m.If(counter == 8):
                    m.next = 'READY'
                    m.d.sync += [
                        pause.eq(0),
                        counter.eq(0),
                    ]

            with m.State('READY'):
                m.d.comb += self.phy.ready.eq(1)
                with m.If(self.phy.readclksel != readclksel):
                    m.d.sync += [
                        counter.eq(0),
                        pause.eq(1),
                    ]
                    m.next = 'PAUSE'

            with m.State('PAUSE'):
                with m.If(counter == 4):
                    m.d.sync += [
                        counter.eq(0),
                        readclksel.eq(self.phy.readclksel),
                    ]
                    m.next = 'READCLKSEL'

            with m.State('READCLKSEL'):
                with m.If(counter == 4):
                    m.d.sync += pause.eq(0)
                    m.next = 'READY'


        # DQS (RWDS) input
        rwds_o = Signal()
        rwds_oe_n = Signal()
        rwds_in = Signal()

        dqsr90 = Signal()
        dqsw = Signal()
        dqsw270 = Signal()
        ddrdel = Signal()
        readptr = Signal(3)
        writeptr = Signal(3)
        m.submodules += [
            Instance("DDRDLLA",
                i_CLK=ClockSignal("fast"),
                i_RST=ResetSignal(),
                i_FREEZE=freeze,
                i_UDDCNTLN=uddcntln,
                o_DDRDEL=ddrdel,
                o_LOCK=lock,
            ),
            Instance("BB",
                i_I=rwds_o,
                i_T=rwds_oe_n,
                o_O=rwds_in,
                io_B=self.bus.rwds.io
            ),
            Instance("TSHX2DQSA",
                i_RST=ResetSignal(),
                i_ECLK=ClockSignal("fast"),
                i_SCLK=ClockSignal(),
                i_DQSW=dqsw,
                i_T0=~self.phy.rwds.e,
                i_T1=~self.phy.rwds.e,
                o_Q=rwds_oe_n
            ),
            Instance("DQSBUFM",
                i_SCLK=ClockSignal(),
                i_ECLK=ClockSignal("fast"),
                i_RST=ResetSignal(),

                i_DQSI=rwds_in,
                i_DDRDEL=ddrdel,
                i_PAUSE=pause,
                i_READ0=self.phy.read[0],
                i_READ1=self.phy.read[1],
                **{f"i_READCLKSEL{i}": readclksel[i] for i in range(len(readclksel))},

                i_RDLOADN=0,
                i_RDMOVE=0,
                i_RDDIRECTION=1,
                i_WRLOADN=0,
                i_WRMOVE=0,
                i_WRDIRECTION=1,

                o_DQSR90=dqsr90,
                o_DQSW=dqsw,
                o_DQSW270=dqsw270,
                **{f"o_RDPNTR{i}": readptr[i] for i in range(len(readptr))},
                **{f"o_WRPNTR{i}": writeptr[i] for i in range(len(writeptr))},

                o_DATAVALID=self.phy.datavalid,
                o_BURSTDET=self.phy.burstdet,
            ),
        ]

        # Clock
        clk_out = Signal()
        clk_dqsw270 = Signal()
        m.submodules += [
            Instance("DELAYG",
                p_DEL_MODE="DQS_CMD_CLK",
                i_A=clk_out,
                o_Z=self.bus.clk.o,
            ),
            Instance("ODDRX2F",
                i_D0=0,
                i_D1=self.phy.clk_en[1],
                i_D2=0,
                i_D3=self.phy.clk_en[0],
                i_SCLK=ClockSignal(),
                i_ECLK=ClockSignal("fast"),
                i_RST=ResetSignal(),
                o_Q=clk_out,
            ),
        ]

        # CS
        cs_out = Signal()
        m.submodules += [
            Instance("DELAYG",
                p_DEL_MODE="DQS_CMD_CLK",
                i_A=cs_out,
                o_Z=self.bus.cs.io,
            ),
            Instance("ODDRX2F",
                i_D0=~self.phy.cs,
                i_D1=~self.phy.cs,
                i_D2=~self.phy.cs,
                i_D3=~self.phy.cs,
                i_SCLK=ClockSignal(),
                i_ECLK=ClockSignal("fast"),
                i_RST=ResetSignal(),
                o_Q=cs_out,
            ),
        ]

        # RWDS out
        m.submodules += [
            Instance("ODDRX2DQSB",
                i_DQSW=dqsw,
                i_D0=self.phy.rwds.o[3],
                i_D1=self.phy.rwds.o[2],
                i_D2=self.phy.rwds.o[1],
                i_D3=self.phy.rwds.o[0],
                i_SCLK=ClockSignal(),
                i_ECLK=ClockSignal("fast"),
                i_RST=ResetSignal(),
                o_Q=rwds_o,
            ),
        ]

        # DQ
        for i in range(8):
            dq_in   = Signal(name=f"dq_in{i}")
            dq_in_delayed   = Signal(name=f"dq_in_delayed{i}")
            dq_oe_n = Signal(name=f"dq_oe_n{i}")
            dq_o    = Signal(name=f"dq_o{i}")
            # Out
            m.submodules += [
                # Tristate
                Instance("BB",
                    i_I=dq_o,
                    i_T=dq_oe_n,
                    o_O=dq_in,
                    io_B=self.bus.dq.io[i]
                ),
                Instance("TSHX2DQA",
                    i_T0=~self.phy.dq.e,
                    i_T1=~self.phy.dq.e,
                    i_SCLK=ClockSignal(),
                    i_ECLK=ClockSignal("fast"),
                    i_DQSW270=dqsw270,
                    i_RST=ResetSignal(),
                    o_Q=dq_oe_n,
                ),

                # Output
                Instance("ODDRX2DQA",
                    i_DQSW270=dqsw270,
                    i_D0=self.phy.dq.o[i+24],
                    i_D1=self.phy.dq.o[i+16],
                    i_D2=self.phy.dq.o[i+8],
                    i_D3=self.phy.dq.o[i],
                    i_SCLK=ClockSignal(),
                    i_ECLK=ClockSignal("fast"),
                    i_RST=ResetSignal(),
                    o_Q=dq_o,
                ),

                # Input
                Instance("DELAYG",
                    p_DEL_MODE="DQS_ALIGNED_X2",
                    i_A=dq_in,
                    o_Z=dq_in_delayed,
                ),
                Instance("IDDRX2DQA",
                    i_D=dq_in_delayed,
                    i_DQSR90=dqsr90,
                    i_SCLK=ClockSignal(),
                    i_ECLK=ClockSignal("fast"),
                    i_RST=ResetSignal(),
                    **{f"i_RDPNTR{i}": readptr[i] for i in range(len(readptr))},
                    **{f"i_WRPNTR{i}": writeptr[i] for i in range(len(writeptr))},
                    o_Q0=self.phy.dq.i[i+24],
                    o_Q1=self.phy.dq.i[i+16],
                    o_Q2=self.phy.dq.i[i+8],
                    o_Q3=self.phy.dq.i[i],
                ),
            ]

        return m
