# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

""" soldiercrab / tiliqua platform definitions and PLL configuration. """

import os

from amaranth import *
from amaranth.build import *
from amaranth.vendor import LatticeECP5Platform

from amaranth_boards.resources   import *

from luna.gateware.platform.core import LUNAPlatform

from tiliqua.video               import DVI_TIMINGS

class _SoldierCrabPlatform(LatticeECP5Platform):
    package      = "BG256"
    default_clk  = "clk48"
    default_rst  = "rst"

    # ULPI and PSRAM can both be populated with 3V3 or 1V8 parts -
    # the IOTYPE of bank 6 and 7 must match. U4 and R16 determine
    # which voltage these banks are supplied with.

    def bank_6_7_iotype(self):
        assert self.ulpi_psram_voltage in ["1V8", "3V3"]
        return "LVCMOS18" if self.ulpi_psram_voltage == "1V8" else "LVCMOS33"

    def bank_6_7_iotype_d(self):
        # 1V8 doesn't support LVCMOS differential outputs.
        # TODO(future): switch this to SSTL?
        if "18" not in self.bank_6_7_iotype():
            return self.bank_6_7_iotype() + "D"
        return self.bank_6_7_iotype()

    # Connections inside soldiercrab SoM.

    resources = [
        # 48MHz master
        Resource("clk48", 0, Pins("A8", dir="i"), Clock(48e6), Attrs(IO_TYPE="LVCMOS33")),

        # PROGRAMN, triggers warm self-reconfiguration
        Resource("self_program", 0, PinsN("T13", dir="o"),
                 Attrs(IO_TYPE="LVCMOS33", PULLMODE="UP")),

        # Indicator LEDs
        Resource("led_a", 0, PinsN("T14", dir="o"),  Attrs(IO_TYPE="LVCMOS33")),
        Resource("led_b", 0, PinsN("T15", dir="o"),  Attrs(IO_TYPE="LVCMOS33")),

        # USB2 PHY
        ULPIResource("ulpi", 0,
            data="N1 M2 M1 L2 L1 K2 K1 K3",
            clk="T3", clk_dir="o", dir="P2", nxt="P1",
            stp="R2", rst="T2", rst_invert=True,
            attrs=Attrs(IO_TYPE=bank_6_7_iotype)
        ),

        # oSPIRAM / HyperRAM
        Resource("ram", 0,
            Subsignal("clk",   DiffPairs("C3", "D3", dir="o"),
                      Attrs(IO_TYPE=bank_6_7_iotype_d)),
            Subsignal("dq",    Pins("F2 B1 C2 E1 E3 E2 F3 G4", dir="io")),
            Subsignal("rwds",  Pins( "D1", dir="io")),
            Subsignal("cs",    PinsN("B2", dir="o")),
            Subsignal("reset", PinsN("C1", dir="o")),
            Attrs(IO_TYPE=bank_6_7_iotype)
        ),

        # Configuration SPI flash
        Resource("spi_flash", 0,
            # Note: SCK needs to go through a USRMCLK instance.
            Subsignal("sdi",  Pins("T8",  dir="o")),
            Subsignal("sdo",  Pins("T7",  dir="i")),
            Subsignal("cs",   PinsN("N8", dir="o")),
            Attrs(IO_TYPE="LVCMOS33")
        ),
    ]

class SoldierCrabR2Platform(_SoldierCrabPlatform):
    device             = "LFE5U-45F"
    speed              = "7"
    ulpi_psram_voltage = "3V3"
    psram_id           = "7KL1282GAHY02"
    psram_registers    = []

    connectors  = [
        Connector("m2", 0,
            # 'E' side of slot (23 physical pins + 8 virtual pins)
            # Pins  1 .. 20 (inclusive)
            "-     -   -   -   -  C4  -  D5   - T10  A3   -  A2   -  B3   -  B4 R11  A4  E4 "
            # Pins 21 .. 30
            "T11  D4 M10   -   -   -  -   -   -   - "
            # Other side of slot (45 physical pins)
            # Pins 31 .. 50
            "-    D6   -  C5  B5   - A5  C6   -  C7  B6  D7  A6  D8   -  C9  B7 C10   -  D9 "
            # Pins 51 .. 70
            "A7  B11  B8 C11  A9 D13 B9 C13 A10 B13 B10 A13 B15 A14 A15 B14 C15 C14 B16 D14 "
            # Pins 71 .. 75
            "C16   - D16   -  -" ),
    ]

class SoldierCrabR3Platform(_SoldierCrabPlatform):
    device             = "LFE5U-25F"
    speed              = "6"
    ulpi_psram_voltage = "1V8"
    psram_id           = "APS256XXN-OBR"
    psram_registers    = [
        ("REG_MR0","REG_MR4",    0x00, 0x0c),
        ("REG_MR4","REG_MR8",    0x04, 0xc0),
        ("REG_MR8","TRAIN_INIT", 0x08, 0x0f),
    ]

    connectors  = [
        Connector("m2", 0,
            # 'E' side of slot (23 physical pins + 7 virtual pins)
            # Pins  1 .. 20 (inclusive)
            "-     -   -   -   -  C4  -  D5   - T10 A3 D11  A2 D13  B3   -  B4 R11  A4  E4 "
            # Pins 21 .. 30
            "T11  D4 M10   -   -   -  -   -   -   - "
            # Other side of slot (45 physical pins)
            # Pins 31 .. 50
            "-    D6   -  C5 B5   - A5  C6   -  C7  B6  D7  A6  D8   -  C9  B7 C10   -  D9 "
            # Pins 51 .. 70
            "A7  B11  B8 C11 A9 C12 B9 C13 A10 B13 B10 A13 B15 A14 A15 B14 C15 C14 B16 D14 "
            # Pins 71 .. 75
            "C16   - D16   -  -" ),
    ]

class _TiliquaR2Mobo:
    resources   = [

        # TODO: this pin is N/C, remove it
        Resource("rst", 0, PinsN("6", dir="i", conn=("m2", 0)), Attrs(IO_TYPE="LVCMOS33")),

        # Quadrature rotary encoder and switch. These are already debounced by an RC filter.
        Resource("encoder", 0,
                 Subsignal("i", PinsN("42", dir="i", conn=("m2", 0))),
                 Subsignal("q", PinsN("40", dir="i", conn=("m2", 0))),
                 Subsignal("s", PinsN("43", dir="i", conn=("m2", 0))),
                 Attrs(IO_TYPE="LVCMOS33")),

        # USB: 5V supply OUT enable (only touch this if you're sure you are a USB host!)
        Resource("usb_vbus_en", 0, PinsN("32", dir="o", conn=("m2", 0)),
                 Attrs(IO_TYPE="LVCMOS33")),

        # USB: Interrupt line from TUSB322I
        Resource("usb_int", 0, PinsN("47", dir="i", conn=("m2", 0)),
                 Attrs(IO_TYPE="LVCMOS33")),

        # Output enable for LEDs driven by PCA9635 on motherboard PCBA
        Resource("mobo_leds_oe", 0, PinsN("11", dir="o", conn=("m2", 0))),

        # DVI: Hotplug Detect
        Resource("dvi_hpd", 0, Pins("8", dir="i", conn=("m2", 0)),
                 Attrs(IO_TYPE="LVCMOS33")),

        # TRS MIDI RX
        Resource("midi", 0, Subsignal("rx", Pins("8", dir="i", conn=("m2", 0)),
                                      Attrs(IO_TYPE="LVCMOS33"))),

        # Motherboard PCBA I2C bus. Includes:
        # - address 0x05: PCA9635 LED driver
        # - address 0x47: TUSB322I USB-C controller
        # - address 0x50: DVI EDID EEPROM (through 3V3 <-> 5V translator)
        Resource("i2c", 0,
            Subsignal("sda", Pins("51", dir="io", conn=("m2", 0))),
            Subsignal("scl", Pins("53", dir="io", conn=("m2", 0))),
        ),

        # RP2040 UART bridge
        UARTResource(0,
            rx="19", tx="17", conn=("m2", 0),
            attrs=Attrs(IO_TYPE="LVCMOS33", PULLMODE="UP")
        ),

        # FFC connector to eurorack-pmod on the back.
        Resource("audio_ffc", 0,
            Subsignal("sdin1",   Pins("44", dir="o",  conn=("m2", 0))),
            Subsignal("sdout1",  Pins("46", dir="i",  conn=("m2", 0))),
            Subsignal("lrck",    Pins("48", dir="o",  conn=("m2", 0))),
            Subsignal("bick",    Pins("50", dir="o",  conn=("m2", 0))),
            Subsignal("mclk",    Pins("52", dir="o",  conn=("m2", 0))),
            Subsignal("pdn",     Pins("54", dir="o",  conn=("m2", 0))),
            Subsignal("i2c_sda", Pins("56", dir="io", conn=("m2", 0))),
            Subsignal("i2c_scl", Pins("58", dir="io", conn=("m2", 0))),
        ),

        # DVI
        # Note: technically DVI outputs are supposed to be open-drain, but
        # compatibility with cheap AliExpress screens seems better with push/pull outputs.
        Resource("dvi", 0,
            Subsignal("d0", Pins("13", dir="o", conn=("m2", 0))),
            Subsignal("d1", Pins("34", dir="o", conn=("m2", 0))),
            Subsignal("d2", Pins("20", dir="o", conn=("m2", 0))),
            Subsignal("ck", Pins("38", dir="o", conn=("m2", 0))),
            Attrs(IO_TYPE="LVCMOS33D", DRIVE="8", SLEWRATE="FAST")
         ),
    ]

    # Expansion connectors ex0 and ex1
    connectors  = [
        Connector("pmod", 0, "55 62 66 68 - - 57 60 64 70 - -", conn=("m2", 0)),
        Connector("pmod", 1, "59 63 67 71 - - 61 65 69 73 - -", conn=("m2", 0)),
    ]

class TiliquaDomainGenerator(Elaboratable):
    """ Clock generator for Tiliqua platform. """

    def __init__(self, *, pixclk_pll=None, audio_192=False, clock_frequencies=None, clock_signal_name=None):
        super().__init__()
        self.pixclk_pll = pixclk_pll
        self.audio_192  = audio_192

    def elaborate(self, platform):
        m = Module()

        # Create our domains.
        m.domains.sync   = ClockDomain()
        m.domains.usb    = ClockDomain()
        m.domains.fast   = ClockDomain()
        m.domains.audio  = ClockDomain()
        m.domains.raw48  = ClockDomain()

        if self.pixclk_pll is not None:
            m.domains.dvi   = ClockDomain()
            m.domains.dvi5x = ClockDomain()


        clk48 = platform.request(platform.default_clk, dir='i').i
        reset  = platform.request(platform.default_rst, dir='i').i
        #reset  = Signal(1, reset=0)

        # ecppll -i 48 --clkout0 60 --clkout1 120 --clkout2 50 --reset -f pll60.v
        # 60MHz for USB (currently also sync domain. fast is for DQS)

        m.d.comb += [
            ClockSignal("raw48").eq(clk48),
        ]

        feedback60 = Signal()
        locked60   = Signal()
        m.submodules.pll = Instance("EHXPLLL",

                # Clock in.
                i_CLKI=clk48,

                # Generated clock outputs.
                o_CLKOP=feedback60,
                o_CLKOS=ClockSignal("fast"),
                o_CLKOS2=ClockSignal("audio"),

                # Status.
                o_LOCK=locked60,

                # PLL parameters...
                p_PLLRST_ENA="ENABLED",
                p_INTFB_WAKE="DISABLED",
                p_STDBY_ENABLE="DISABLED",
                p_DPHASE_SOURCE="DISABLED",
                p_OUTDIVIDER_MUXA="DIVA",
                p_OUTDIVIDER_MUXB="DIVB",
                p_OUTDIVIDER_MUXC="DIVC",
                p_OUTDIVIDER_MUXD="DIVD",
                p_CLKI_DIV=4,
                p_CLKOP_ENABLE="ENABLED",
                p_CLKOP_DIV=10,
                p_CLKOP_CPHASE=4,
                p_CLKOP_FPHASE=0,
                p_CLKOS_ENABLE="ENABLED",
                p_CLKOS_DIV=5,
                p_CLKOS_CPHASE=4,
                p_CLKOS_FPHASE=0,
                p_CLKOS2_ENABLE="ENABLED",
                p_CLKOS2_DIV=12 if self.audio_192 else 48, # 50.0MHz (~195kHz) or 12.0MHz (~47kHz)
                p_CLKOS2_CPHASE=4,
                p_CLKOS2_FPHASE=0,
                p_FEEDBK_PATH="CLKOP",
                p_CLKFB_DIV=5,

                # Internal feedback.
                i_CLKFB=feedback60,

                # Control signals.
                i_RST=reset,
                i_PHASESEL0=0,
                i_PHASESEL1=0,
                i_PHASEDIR=1,
                i_PHASESTEP=1,
                i_PHASELOADREG=1,
                i_STDBY=0,
                i_PLLWAKESYNC=0,

                # Output Enables.
                i_ENCLKOP=0,
                i_ENCLKOS=0,
                i_ENCLKOS2=0,
                i_ENCLKOS3=0,

                # Synthesis attributes.
                a_ICP_CURRENT="12",
                a_LPF_RESISTOR="8"
        )

        if self.pixclk_pll is not None:

            # Extra PLL to generate DVI clocks, 1x pixel clock and 5x (half DVI TDMS clock, output is DDR)

            feedback_dvi = Signal()
            locked_dvi   = Signal()
            m.submodules.pll_dvi = Instance("EHXPLLL",

                    # Clock in.
                    i_CLKI=clk48,

                    # Generated clock outputs.
                    o_CLKOP=feedback_dvi,
                    o_CLKOS=ClockSignal("dvi5x"),
                    o_CLKOS2=ClockSignal("dvi"),

                    # Status.
                    o_LOCK=locked_dvi,

                    # PLL parameters...
                    p_PLLRST_ENA      = "ENABLED",
                    p_INTFB_WAKE      = "DISABLED",
                    p_STDBY_ENABLE    = "DISABLED",
                    p_DPHASE_SOURCE   = "DISABLED",
                    p_OUTDIVIDER_MUXA = "DIVA",
                    p_OUTDIVIDER_MUXB = "DIVB",
                    p_OUTDIVIDER_MUXC = "DIVC",
                    p_OUTDIVIDER_MUXD = "DIVD",
                    p_CLKI_DIV        = self.pixclk_pll.clki_div,
                    p_CLKOP_ENABLE    = "ENABLED",
                    p_CLKOP_DIV       = self.pixclk_pll.clkop_div,
                    p_CLKOP_CPHASE    = self.pixclk_pll.clkop_cphase,
                    p_CLKOP_FPHASE    = 0,
                    p_CLKOS_ENABLE    = "ENABLED",
                    p_CLKOS_DIV       = self.pixclk_pll.clkos_div,
                    p_CLKOS_CPHASE    = self.pixclk_pll.clkos_cphase,
                    p_CLKOS_FPHASE    = 0,
                    p_CLKOS2_ENABLE   = "ENABLED",
                    p_CLKOS2_DIV      = self.pixclk_pll.clkos2_div,
                    p_CLKOS2_CPHASE   = self.pixclk_pll.clkos2_cphase,
                    p_CLKOS2_FPHASE   = 0,
                    p_FEEDBK_PATH     = "CLKOP",
                    p_CLKFB_DIV       = self.pixclk_pll.clkfb_div,

                    # Internal feedback.
                    i_CLKFB=feedback_dvi,

                    # Control signals.
                    i_RST=reset,
                    i_PHASESEL0=0,
                    i_PHASESEL1=0,
                    i_PHASEDIR=1,
                    i_PHASESTEP=1,
                    i_PHASELOADREG=1,
                    i_STDBY=0,
                    i_PLLWAKESYNC=0,

                    # Output Enables.
                    i_ENCLKOP=0,
                    i_ENCLKOS=0,
                    i_ENCLKOS2=0,
                    i_ENCLKOS3=0,

                    # Synthesis attributes.
                    a_ICP_CURRENT="12",
                    a_LPF_RESISTOR="8"
            )


        # Derived clocks and resets
        m.d.comb += [
            ClockSignal("sync")  .eq(feedback60),
            ClockSignal("usb")   .eq(feedback60),

            ResetSignal("sync")  .eq(~locked60),
            ResetSignal("fast")  .eq(~locked60),
            ResetSignal("usb")   .eq(~locked60),
            ResetSignal("audio") .eq(~locked60),
        ]

        if self.pixclk_pll is not None:
            m.d.comb += [
                ResetSignal("dvi")  .eq(~locked_dvi),
                ResetSignal("dvi5x").eq(~locked_dvi),
            ]

        return m

class TiliquaR2SC2Platform(SoldierCrabR2Platform, LUNAPlatform):
    name                   = ("Tiliqua R2 / SoldierCrab R2 "
                              f"({SoldierCrabR2Platform.device}/{SoldierCrabR2Platform.psram_id})")
    clock_domain_generator = TiliquaDomainGenerator
    default_usb_connection = "ulpi"

    resources = [
        *SoldierCrabR2Platform.resources,
        *_TiliquaR2Mobo.resources
    ]

    connectors = [
        *SoldierCrabR2Platform.connectors,
        *_TiliquaR2Mobo.connectors
    ]

class TiliquaR2SC3Platform(SoldierCrabR3Platform, LUNAPlatform):
    name                   = ("Tiliqua R2 / SoldierCrab R3 "
                              f"({SoldierCrabR3Platform.device}/{SoldierCrabR3Platform.psram_id})")
    clock_domain_generator = TiliquaDomainGenerator
    default_usb_connection = "ulpi"

    resources = [
        *SoldierCrabR3Platform.resources,
        *_TiliquaR2Mobo.resources
    ]

    connectors = [
        *SoldierCrabR3Platform.connectors,
        *_TiliquaR2Mobo.connectors
    ]
