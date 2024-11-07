#
# This file is part of LUNA.
#
# Copyright (c) 2024 Great Scott Gadgets <info@greatscottgadgets.com>
# SPDX-License-Identifier: BSD-3-Clause

# Based on code from LiteSPI

from amaranth               import *
from amaranth.lib           import wiring
from amaranth.utils         import bits_for
from amaranth.lib.wiring    import connect, In, Out

from .port              import SPIControlPort
from .utils             import WaitTimer

class SPIPinSignature(wiring.Signature):
    def __init__(self):
        super().__init__({
            "dq" : Out(
                wiring.Signature({
                    "i"  :  In  (unsigned(4)),
                    "o"  :  Out (unsigned(4)),
                    "oe" :  Out (unsigned(1)),
                })
            ),
            "cs" : Out(
                wiring.Signature({
                    "o"  :  Out (unsigned(1)),
                })
            ),
            "sck" : Out(
                wiring.Signature({
                    "o"  :  Out (unsigned(1)),
                })
            ),
        })

class ECP5ConfigurationFlashProvider(wiring.Component):
    """ Gateware that creates a connection to an MSPI configuration flash.

    Automatically uses appropriate platform resources; this abstracts away details
    necessary to e.g. drive the MCLK lines on an ECP5, which has special handling.
    """
    def __init__(self):
        super().__init__({
            "pins": In(SPIPinSignature())
        })

    def elaborate(self, platform):
        m = Module()

        spi = platform.request("qspi_flash")
        m.d.comb += [
            self.pins.dq.i.eq(spi.dq.i),
            spi.dq.o.eq(self.pins.dq.o),
            spi.dq.oe.eq(self.pins.dq.oe),
            spi.cs.o.eq(self.pins.cs.o),
        ]

        # Get the ECP5 block that's responsible for driving the MCLK pin,
        # and drive it using our SCK line.
        user_mclk = Instance('USRMCLK', i_USRMCLKI=self.pins.sck.o, i_USRMCLKTS=0)
        m.submodules += user_mclk

        return m

class SPIPHYController(wiring.Component):
    """Provides a generic PHY that can be used by a SPI flash controller.

    It supports single/dual/quad/octal output reads from the flash chips.
    """
    def __init__(self, data_width=32, divisor=0, domain="sync"):
        super().__init__({
            "ctrl": In(SPIControlPort(data_width)),
            "pins": Out(SPIPinSignature()),
        })
        self.divisor = divisor
        self._domain = domain

    def elaborate(self, platform):
        m = Module()

        pads   = self.pins
        sink   = self.ctrl.sink
        source = self.ctrl.source

        # Clock Generator.
        m.submodules.clkgen = clkgen = SPIClockGenerator(self.divisor, domain=self._domain)
        spi_clk_divisor = self.divisor

        # CS control: ensure cs_delay cycles between XFers.
        cs_delay = 0
        cs_enable = Signal()
        if cs_delay > 0:
            m.submodules.cs_timer = cs_timer  = WaitTimer(cs_delay + 1, domain=self._domain)
            m.d.comb += [
                cs_timer.wait    .eq(self.ctrl.cs),
                cs_enable        .eq(cs_timer.done),
            ]
        else:
            m.d.comb += cs_enable.eq(self.ctrl.cs)

        # I/Os.
        dq_o  = Signal.like(pads.dq.o)
        dq_i  = Signal.like(pads.dq.i)
        dq_oe = Signal.like(pads.dq.oe)
        m.d.sync += [
            pads.sck.o  .eq(clkgen.clk),
            pads.cs.o   .eq(cs_enable),
            pads.dq.o   .eq(dq_o),
            pads.dq.oe  .eq(dq_oe),
            dq_i        .eq(pads.dq.i),
        ]
        if hasattr(pads.cs, 'oe'):
            m.d.comb += pads.cs.oe.eq(1)

        # Data Shift Registers.
        sr_cnt       = Signal(8, reset_less=True)
        sr_out_load  = Signal()
        sr_out_shift = Signal()
        sr_out       = Signal(len(source.data), reset_less=True)
        sr_in_shift  = Signal()
        sr_in        = Signal(len(source.data), reset_less=True)

        # Data Out Generation/Load/Shift.
        m.d.comb += dq_oe.eq(source.mask)
        with m.Switch(source.width):
            with m.Case(1):
                m.d.comb += dq_o.eq(sr_out[-1:])
            with m.Case(2):
                m.d.comb += dq_o.eq(sr_out[-2:])
            with m.Case(4):
                m.d.comb += dq_o.eq(sr_out[-4:])
            with m.Case(8):
                m.d.comb += dq_o.eq(sr_out[-8:])

        with m.If(sr_out_load):
            m.d.sync += sr_out.eq(source.data << (len(source.data) - source.len).as_unsigned())

        with m.If(sr_out_shift):
            with m.Switch(source.width):
                with m.Case(1):
                    m.d.sync += sr_out.eq(Cat(C(0, 1), sr_out))
                with m.Case(2):
                    m.d.sync += sr_out.eq(Cat(C(0, 2), sr_out))
                with m.Case(4):
                    m.d.sync += sr_out.eq(Cat(C(0, 4), sr_out))
                with m.Case(8):
                    m.d.sync += sr_out.eq(Cat(C(0, 8), sr_out))

        # Data In Shift.
        with m.If(sr_in_shift):
            with m.Switch(source.width):
                with m.Case(1):
                    m.d.sync += sr_in.eq(Cat(dq_i[1], sr_in))  # 1 = peripheral output
                with m.Case(2):
                    m.d.sync += sr_in.eq(Cat(dq_i[:2], sr_in))
                with m.Case(4):
                    m.d.sync += sr_in.eq(Cat(dq_i[:4], sr_in))
                with m.Case(8):
                    m.d.sync += sr_in.eq(Cat(dq_i[:8], sr_in))


        m.d.comb += sink.data.eq(sr_in)

        with m.FSM(domain=self._domain):

            with m.State("WAIT-CMD-DATA"):
                # Wait for CS and a CMD from the Core.
                with m.If(cs_enable & source.valid):
                    # Load Shift Register Count/Data Out.
                    m.d.sync += sr_cnt.eq(source.len - source.width)
                    m.d.comb += sr_out_load.eq(1)
                    # Start XFER.
                    m.next = 'XFER'

            with m.State('XFER'):
                m.d.comb += [
                    clkgen.en   .eq(1),
                    # Data in / out shift.
                    sr_in_shift .eq(clkgen.sample),
                    sr_out_shift.eq(clkgen.update),
                ]

                # Shift register count update/check.
                with m.If(clkgen.update):
                    m.d.sync += sr_cnt.eq(sr_cnt - source.width)
                    # End xfer.
                    with m.If(sr_cnt == 0):
                        m.next = 'XFER-END'

            with m.State('XFER-END'):
                # Last data already captured in XFER when divisor > 0 so only capture for divisor == 0.
                with m.If((spi_clk_divisor > 0) | clkgen.sample):
                    # Accept CMD.
                    m.d.comb += source.ready.eq(1)
                    # Capture last data (only for spi_clk_divisor == 0).
                    m.d.comb += sr_in_shift.eq(spi_clk_divisor == 0)
                    # Send Status/Data to Core.
                    m.next = "SEND-STATUS-DATA"

            with m.State('SEND-STATUS-DATA'):
                # Send data in to core and return to IDLE when accepted.
                m.d.comb += sink.valid.eq(1)
                with m.If(sink.ready):
                    m.next = 'WAIT-CMD-DATA'

        # Convert our sync domain to the domain requested by the user, if necessary.
        if self._domain != "sync":
            m = DomainRenamer({"sync": self._domain})(m)

        return m



class SPIClockGenerator(Elaboratable):
    def __init__(self, divisor, domain="sync"):
        self._domain      = domain
        self.div          = divisor
        self.en           = Signal()
        self.clk          = Signal()
        self.sample       = Signal()
        self.update       = Signal()

    def elaborate(self, platform):
        m = Module()

        div          = self.div
        cnt_width    = bits_for(div)
        cnt          = Signal(cnt_width)
        posedge      = Signal()
        negedge      = Signal()
        posedge_reg  = Signal()
        posedge_reg2 = Signal()

        m.d.comb += [
            posedge     .eq(self.en & ~self.clk & (cnt == div)),
            negedge     .eq(self.en &  self.clk & (cnt == div)),
            self.update .eq(negedge),
            self.sample .eq(posedge_reg2),
        ]

        # Delayed edge to account for IO register delays.
        m.d.sync += [
            posedge_reg    .eq(posedge),
            posedge_reg2   .eq(posedge_reg),
        ]

        with m.If(self.en):
            with m.If(cnt < div):
                m.d.sync += cnt.eq(cnt + 1)
            with m.Else():
                m.d.sync += [
                    cnt.eq(0),
                    self.clk.eq(~self.clk),
                ]
        with m.Else():
            m.d.sync += [
                cnt.eq(0),
                self.clk.eq(0),
            ]

        # Convert our sync domain to the domain requested by the user, if necessary.
        if self._domain != "sync":
            m = DomainRenamer({"sync": self._domain})(m)

        return m
