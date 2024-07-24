# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

""" soldiercrab / tiliqua platform definitions and PLL configuration. """

from amaranth import *
from amaranth.build import *
from amaranth.vendor import LatticeECP5Platform

from amaranth_boards.resources import *

from luna.gateware.platform.core import LUNAPlatform

# Connections inside soldiercrab SoM.
# TODO: move this to dedicated class and use Connector() construct for card edge.
resources_soldiercrab = [
    # 48MHz master
    Resource("clk48", 0, Pins("A8", dir="i"), Clock(48e6), Attrs(IO_TYPE="LVCMOS33")),

    # PROGRAMN, triggers warm self-reconfiguration
    Resource("self_program", 0, PinsN("T13", dir="o"), Attrs(IO_TYPE="LVCMOS33", PULLMODE="UP")),

    # Indicator LEDs
    Resource("led_a", 0, PinsN("T14", dir="o"),  Attrs(IO_TYPE="LVCMOS33")),
    Resource("led_b", 0, PinsN("T15", dir="o"),  Attrs(IO_TYPE="LVCMOS33")),

    # USB2 PHY
    ULPIResource("ulpi", 0,
        data="N1 M2 M1 L2 L1 K2 K1 K3",
        clk="T3", clk_dir="o", dir="P2", nxt="P1",
        stp="R2", rst="T2", rst_invert=True,
        attrs=Attrs(IO_TYPE="LVCMOS18")),

    # oSPIRAM / HyperRAM
    Resource("ram", 0,
        Subsignal("clk",   Pins("C3", dir="o")),
        Subsignal("dq",    Pins("F2 B1 C2 E1 E3 E2 F3 G4", dir="io")),
        Subsignal("rwds",  Pins( "D1", dir="io")),
        Subsignal("cs",    PinsN("B2", dir="o")),
        Attrs(IO_TYPE="LVCMOS18")
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

class _TiliquaPlatform(LatticeECP5Platform):
    device      = "LFE5U-25F"
    package     = "BG256"
    speed       = "6"
    default_clk = "clk48"
    default_rst = "rst"

    ram_timings = dict(clock_skew = 127)

    resources   = resources_soldiercrab + [

        # TODO: this pin is N/C, remove it
        Resource("rst", 0, PinsN("C4", dir="i"), Attrs(IO_TYPE="LVCMOS33")),

        # Quadrature rotary encoder and switch. These are already debounced by an RC filter.
        Resource("encoder", 0,
                 Subsignal("i", PinsN("D7", dir="i")),
                 Subsignal("q", PinsN("C7", dir="i")),
                 Subsignal("s", PinsN("A6", dir="i")),
                 Attrs(IO_TYPE="LVCMOS33")),

        # USB: 5V supply OUT enable (only touch this if you're sure you are a USB host!)
        Resource("usb_vbus_en", 0, PinsN("D6", dir="o"),  Attrs(IO_TYPE="LVCMOS33")),

        # USB: Interrupt line from TUSB322I
        Resource("usb_int", 0, PinsN("B7", dir="i"),  Attrs(IO_TYPE="LVCMOS33")),

        # Output enable for LEDs driven by PCA9635 on motherboard PCBA
        Resource("mobo_leds_oe", 0, PinsN("A3", dir="o")),

        # DVI: Hotplug Detect
        Resource("dvi_hpd", 0, Pins("A5", dir="i"),  Attrs(IO_TYPE="LVCMOS33")),

        # TRS MIDI RX
        Resource("midi", 0,
                 Subsignal("rx", Pins("D5", dir="i"), Attrs(IO_TYPE="LVCMOS33"))),

        # Motherboard PCBA I2C bus. Includes:
        # - address 0x05: PCA9635 LED driver
        # - address 0x47: TUSB322I USB-C controller
        # - address 0x50: DVI EDID EEPROM (through 3V3 <-> 5V translator)
        Resource("mobo_i2c", 0,
            Subsignal("sda",    Pins("A7", dir="io")),
            Subsignal("scl",    Pins("B8", dir="io")),
        ),

        # RP2040 UART bridge
        UARTResource(0,
            rx="A4", tx="B4",
            attrs=Attrs(IO_TYPE="LVCMOS33", PULLMODE="UP")
        ),

        # FFC connector to eurorack-pmod on the back.
        Resource("audio_ffc", 0,
            Subsignal("sdin1",  Pins("D8",  dir="o")),
            Subsignal("sdout1", Pins("C9",  dir="i")),
            Subsignal("lrck",   Pins("C10", dir="o")),
            Subsignal("bick",   Pins("D9",  dir="o")),
            Subsignal("mclk",   Pins("B11", dir="o")),
            Subsignal("pdn",    Pins("C11", dir="o")),
            Subsignal("i2c_sda",    Pins("D13", dir="io")),
            Subsignal("i2c_scl",    Pins("C13", dir="io")),
        ),

        # DVI
        # Note: technically DVI outputs are supposed to be open-drain, but
        # compatibility with cheap AliExpress screens seems better with push/pull outputs.
        Resource("dvi", 0,
            Subsignal("d0", Pins("A2", dir="o")),
            Subsignal("d1", Pins("C5", dir="o")),
            Subsignal("d2", Pins("E4", dir="o")),
            Subsignal("ck", Pins("C6", dir="o")),
            Attrs(IO_TYPE="LVCMOS33D", DRIVE="8", SLEWRATE="FAST")
         ),
    ]

    # Expansion connectors ex0 and ex1
    connectors  = [
        Connector("pmod", 0, "A9 A13 B14 C14 - - B9 B13 A14 D14 - -"),
        Connector("pmod", 1, "A10 B15 C15 C16 - - B10 A15 B16 D16 - -"),
    ]

class TiliquaDomainGenerator(Elaboratable):
    """ Clock generator for Tiliqua platform. """

    def __init__(self, *, audio_192=False, clock_frequencies=None, clock_signal_name=None):
        super().__init__()
        self.audio_192 = audio_192

    def elaborate(self, platform):
        m = Module()

        # Create our domains.
        m.domains.sync   = ClockDomain()
        m.domains.usb    = ClockDomain()
        m.domains.fast   = ClockDomain()
        m.domains.audio  = ClockDomain()
        m.domains.raw48  = ClockDomain()
        m.domains.dvi   = ClockDomain()
        m.domains.dvi5x = ClockDomain()


        clk48 = platform.request(platform.default_clk, dir='i').i
        reset  = platform.request(platform.default_rst, dir='i').i
        #reset  = Signal(1, reset=0)

        # ecppll -i 48 --clkout0 60 --clkout1 120 --clkout2 40 --clkout3 200 --reset -f pll60.v
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

        # Extra PLL to generate 720p60 5x pixel clock (371.25MHz)
        # CLKOP and CLKOS come from:
        # ecppll -i 48 --clkout0 371.25 --highres --reset -f pll60.v
        # CLKOS2 is manually added.
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
                p_PLLRST_ENA="ENABLED",
                p_INTFB_WAKE="DISABLED",
                p_STDBY_ENABLE="DISABLED",
                p_DPHASE_SOURCE="DISABLED",
                p_OUTDIVIDER_MUXA="DIVA",
                p_OUTDIVIDER_MUXB="DIVB",
                p_OUTDIVIDER_MUXC="DIVC",
                p_OUTDIVIDER_MUXD="DIVD",
                p_CLKI_DIV=15,
                p_CLKOP_ENABLE="ENABLED",
                p_CLKOP_DIV=58,
                p_CLKOP_CPHASE=9,
                p_CLKOP_FPHASE=0,
                p_CLKOS_ENABLE="ENABLED",
                p_CLKOS_DIV=2,
                p_CLKOS_CPHASE=0,
                p_CLKOS_FPHASE=0,
                p_CLKOS2_ENABLE="ENABLED",
                p_CLKOS2_DIV=5,
                p_CLKOS2_CPHASE=0,
                p_CLKOS2_FPHASE=0,
                p_FEEDBK_PATH="CLKOP",
                p_CLKFB_DIV=4,

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

        """

        feedback_audio  = Signal()
        locked_audio    = Signal()

        if self.audio_192:
            # 49.152MHz for 256*Fs Audio domain (192KHz Fs)
            # ecppll -i 48 --clkout0 49.152 --highres --reset -f pll2.v
            m.submodules.audio_pll = Instance("EHXPLLL",
                    # Status.
                    o_LOCK=locked_audio,

                    # PLL parameters...
                    p_PLLRST_ENA="ENABLED",
                    p_INTFB_WAKE="DISABLED",
                    p_STDBY_ENABLE="DISABLED",
                    p_DPHASE_SOURCE="DISABLED",
                    p_OUTDIVIDER_MUXA="DIVA",
                    p_OUTDIVIDER_MUXB="DIVB",
                    p_OUTDIVIDER_MUXC="DIVC",
                    p_OUTDIVIDER_MUXD="DIVD",

                    p_CLKI_DIV = 13,
                    p_CLKOP_ENABLE = "ENABLED",
                    p_CLKOP_DIV = 71,
                    p_CLKOP_CPHASE = 9,
                    p_CLKOP_FPHASE = 0,
                    p_CLKOS_ENABLE = "ENABLED",
                    p_CLKOS_DIV = 16,
                    p_CLKOS_CPHASE = 0,
                    p_CLKOS_FPHASE = 0,
                    p_FEEDBK_PATH = "CLKOP",
                    p_CLKFB_DIV = 3,

                    # Clock in.
                    i_CLKI=clk48,

                    # Internal feedback.
                    i_CLKFB=feedback_audio,

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
                    i_ENCLKOS2=0,

                    # Generated clock outputs.
                    o_CLKOP=feedback_audio,
                    o_CLKOS=ClockSignal("audio"),

                    # Synthesis attributes.
                    a_FREQUENCY_PIN_CLKI="48",
                    a_FREQUENCY_PIN_CLKOS="12.288",
                    a_ICP_CURRENT="12",
                    a_LPF_RESISTOR="8",
                    a_MFG_ENABLE_FILTEROPAMP="1",
                    a_MFG_GMCREF_SEL="2"
            )
        else:
            # 12.288MHz for 256*Fs Audio domain (48KHz Fs)
            # ecppll -i 48 --clkout0 12.288 --highres --reset -f pll2.v
            m.submodules.audio_pll = Instance("EHXPLLL",
                    # Status.
                    o_LOCK=locked_audio,

                    # PLL parameters...
                    p_PLLRST_ENA="ENABLED",
                    p_INTFB_WAKE="DISABLED",
                    p_STDBY_ENABLE="DISABLED",
                    p_DPHASE_SOURCE="DISABLED",
                    p_OUTDIVIDER_MUXA="DIVA",
                    p_OUTDIVIDER_MUXB="DIVB",
                    p_OUTDIVIDER_MUXC="DIVC",
                    p_OUTDIVIDER_MUXD="DIVD",

                    p_CLKI_DIV = 5,
                    p_CLKOP_ENABLE = "ENABLED",
                    p_CLKOP_DIV = 32,
                    p_CLKOP_CPHASE = 9,
                    p_CLKOP_FPHASE = 0,
                    p_CLKOS_ENABLE = "ENABLED",
                    p_CLKOS_DIV = 50,
                    p_CLKOS_CPHASE = 0,
                    p_CLKOS_FPHASE = 0,
                    p_FEEDBK_PATH = "CLKOP",
                    p_CLKFB_DIV = 2,

                    # Clock in.
                    i_CLKI=clk48,

                    # Internal feedback.
                    i_CLKFB=feedback_audio,

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
                    i_ENCLKOS2=0,

                    # Generated clock outputs.
                    o_CLKOP=feedback_audio,
                    o_CLKOS=ClockSignal("audio"),

                    # Synthesis attributes.
                    a_FREQUENCY_PIN_CLKI="48",
                    a_FREQUENCY_PIN_CLKOS="12.288",
                    a_ICP_CURRENT="12",
                    a_LPF_RESISTOR="8",
                    a_MFG_ENABLE_FILTEROPAMP="1",
                    a_MFG_GMCREF_SEL="2"
            )
        """

        # Derived clocks and resets
        m.d.comb += [
            ClockSignal("sync")  .eq(feedback60),
            ClockSignal("usb")   .eq(feedback60),
            ClockSignal("audio").eq(feedback60),

            ResetSignal("sync")  .eq(~locked60),
            ResetSignal("fast")  .eq(~locked60),
            ResetSignal("usb")   .eq(~locked60),
            ResetSignal("dvi")  .eq(~locked_dvi),
            ResetSignal("dvi5x").eq(~locked_dvi),

            ResetSignal("audio")   .eq(~locked60),
        ]

        return m

class TiliquaPlatform(_TiliquaPlatform, LUNAPlatform):
    name                   = "Tiliqua (45F)"
    clock_domain_generator = TiliquaDomainGenerator
    default_usb_connection = "ulpi"
