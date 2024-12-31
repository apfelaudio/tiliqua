# Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD-3-Clause

""" Tiliqua and SoldierCrab platform definitions. """

from amaranth                    import *
from amaranth.build              import *
from amaranth.lib                import wiring
from amaranth.vendor             import LatticeECP5Platform

from amaranth_boards.resources   import *

from luna.gateware.platform.core import LUNAPlatform

from tiliqua                     import tiliqua_pll

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

        # Connection to our SPI flash but using quad mode (QSPI)
        Resource("qspi_flash", 0,
            # SCK is on pin 9; but doesn't have a traditional I/O buffer.
            # Instead, we'll need to drive a clock into a USRMCLK instance.
            # See interfaces/flash.py for more information.
            Subsignal("dq",  Pins("T8 T7 M7 N7",  dir="io")),
            Subsignal("cs",  PinsN("N8", dir="o")),
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
            Subsignal("pdn_d",   Pins("54", dir="o",  conn=("m2", 0))),
            Subsignal("i2c_sda", Pins("56", dir="io", conn=("m2", 0))),
            Subsignal("i2c_scl", Pins("58", dir="io", conn=("m2", 0))),
            Attrs(IO_TYPE="LVCMOS33")
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

class _TiliquaR3Mobo:
    resources   = [
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
        Resource("dvi_hpd", 0, Pins("37", dir="i", conn=("m2", 0)),
                 Attrs(IO_TYPE="LVCMOS33")),

        # TRS MIDI RX
        Resource("midi", 0, Subsignal("rx", Pins("6", dir="i", conn=("m2", 0)),
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
            Subsignal("mclk",    Pins("67", dir="o",  conn=("m2", 0))),
            Subsignal("pdn_d",   Pins("65", dir="o",  conn=("m2", 0))),
            Subsignal("pdn_clk", Pins("56", dir="o",  conn=("m2", 0))),
            Subsignal("i2c_sda", Pins("71", dir="io", conn=("m2", 0))),
            Subsignal("i2c_scl", Pins("69", dir="io", conn=("m2", 0))),
            Attrs(IO_TYPE="LVCMOS33")
        ),

        # DVI
        # Note: technically DVI outputs are supposed to be open-drain, but
        # compatibility with cheap AliExpress screens seems better with push/pull outputs.
        Resource("dvi", 0,
            Subsignal("d0", Pins("60", dir="o", conn=("m2", 0))),
            Subsignal("d1", Pins("62", dir="o", conn=("m2", 0))),
            Subsignal("d2", Pins("68", dir="o", conn=("m2", 0))),
            Subsignal("ck", Pins("52", dir="o", conn=("m2", 0))),
            Attrs(IO_TYPE="LVCMOS33D", DRIVE="8", SLEWRATE="FAST")
         ),

        Resource("i2c_ext", 0,
            Subsignal("sda", Pins("41", dir="io", conn=("m2", 0))),
            Subsignal("scl", Pins("66", dir="io", conn=("m2", 0))),
            Attrs(PULLMODE="UP")
        ),
    ]

    # Expansion connectors ex0 and ex1
    connectors  = [
        #Connector("pmod", 0, "55 38 66 41 - - 57 35 34 70 - -", conn=("m2", 0)),
        Connector("pmod", 1, "59 63 14 20 - - 61 15 13 22 - -", conn=("m2", 0)),
    ]

class TiliquaR2SC2Platform(SoldierCrabR2Platform, LUNAPlatform):
    name                   = ("Tiliqua R2 / SoldierCrab R2 "
                              f"({SoldierCrabR2Platform.device}/{SoldierCrabR2Platform.psram_id})")
    clock_domain_generator = tiliqua_pll.TiliquaDomainGenerator4PLLs
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
    clock_domain_generator = tiliqua_pll.TiliquaDomainGenerator2PLLs
    default_usb_connection = "ulpi"

    resources = [
        *SoldierCrabR3Platform.resources,
        *_TiliquaR2Mobo.resources
    ]

    connectors = [
        *SoldierCrabR3Platform.connectors,
        *_TiliquaR2Mobo.connectors
    ]

class TiliquaR3SC3Platform(SoldierCrabR3Platform, LUNAPlatform):
    name                   = ("Tiliqua R3 / SoldierCrab R3 "
                              f"({SoldierCrabR3Platform.device}/{SoldierCrabR3Platform.psram_id})")
    clock_domain_generator = tiliqua_pll.TiliquaDomainGenerator2PLLs
    default_usb_connection = "ulpi"

    resources = [
        *SoldierCrabR3Platform.resources,
        *_TiliquaR3Mobo.resources
    ]

    connectors = [
        *SoldierCrabR3Platform.connectors,
        *_TiliquaR3Mobo.connectors
    ]

class RebootProvider(wiring.Component):

    """
    Issue a 'self_program' (return to bootloader) when the 'button'
    signal is high for 'reboot_seconds', and a 'mute' output shortly
    before then (to warn the CODEC to prevent pops).
    """

    button: wiring.In(unsigned(1))
    mute:   wiring.Out(unsigned(1))

    def __init__(self, clock_sync_hz, reboot_seconds=3, mute_seconds=2.5):
        self.reboot_seconds = reboot_seconds
        self.mute_seconds   = mute_seconds
        self.clock_sync_hz  = clock_sync_hz
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        timeout_reboot = self.reboot_seconds*self.clock_sync_hz
        timeout_mute   = int(self.mute_seconds*self.clock_sync_hz)
        assert(timeout_reboot > (timeout_mute - 0.25))
        button_counter = Signal(range(timeout_reboot+1))
        with m.If(button_counter >= timeout_mute):
            m.d.sync += self.mute.eq(1)
        with m.If(button_counter >= timeout_reboot):
            m.d.comb += platform.request("self_program").o.eq(1)
        with m.Else():
            # we already started muting. point of no return.
            with m.If(self.button | self.mute):
                m.d.sync += button_counter.eq(button_counter + 1)
            with m.Else():
                m.d.sync += button_counter.eq(0)
        return m
