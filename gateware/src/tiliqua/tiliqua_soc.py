# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# Based on some work from LUNA project licensed under BSD. Anything new
# in this file is issued under the following license:
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import logging
import os

from amaranth                                    import *
from amaranth.hdl.rec                            import Record
from amaranth.lib                                import wiring, data

from luna_soc.gateware.cpu.vexriscv              import VexRiscv
from luna_soc.gateware.lunasoc                   import LunaSoC
from luna_soc.gateware.csr                       import GpioPeripheral, LedPeripheral

from luna_soc.util.readbin                       import get_mem_data

from tiliqua.tiliqua_platform                    import TiliquaPlatform
from tiliqua.psram_peripheral                    import PSRAMPeripheral

from tiliqua.i2c                                 import I2CPeripheral
from tiliqua.encoder                             import EncoderPeripheral
from tiliqua.dtr                                 import DieTemperaturePeripheral
from tiliqua.video                               import DVI_TIMINGS, FramebufferPHY
from tiliqua                                     import eurorack_pmod

from example_vectorscope.top                     import Persistance

from luna_soc.gateware.csr.base                  import Peripheral

TILIQUA_CLOCK_SYNC_HZ = int(60e6)

class PersistPeripheral(Peripheral, Elaboratable):

    """
    Tweak display persistance properties from SoC memory space.
    """

    def __init__(self, fb_base, fb_size, bus):

        super().__init__()

        self.en                = Signal()

        self.persist = Persistance(
                fb_base=fb_base, bus_master=bus.bus, fb_size=fb_size)
        bus.add_master(self.persist.bus)

        # CSRs
        bank                   = self.csr_bank()
        self._persist          = bank.csr(16, "w")
        self._decay            = bank.csr(8, "w")

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge

        m.submodules += self.persist

        m.d.comb += self.persist.enable.eq(self.en)

        with m.If(self._persist.w_stb):
            m.d.sync += self.persist.holdoff.eq(self._persist.w_data)

        with m.If(self._decay.w_stb):
            m.d.sync += self.persist.decay.eq(self._decay.w_data)

        return m


class TiliquaSoc(Elaboratable):
    def __init__(self, *, firmware_path, dvi_timings, audio_192=False,
                 audio_out_peripheral=True, touch=False):

        self.touch = touch
        self.audio_192 = audio_192
        self.dvi_timings = dvi_timings
        # FIXME move somewhere more obvious
        self.video_rotate_90 = True if os.getenv("TILIQUA_VIDEO_ROTATE") == "1" else False

        self.uart_pins = Record([
            ('rx', [('i', 1)]),
            ('tx', [('o', 1)])
        ])

        self.i2c_pins = Record([
            ('sda', [('i', 1), ('o', 1), ('oe', 1)]),
            ('scl', [('i', 1), ('o', 1), ('oe', 1)]),
        ])

        self.encoder_pins = Record([
            ('i', [('i', 1)]),
            ('q', [('i', 1)]),
            ('s', [('i', 1)])
        ])

        self.clock_sync_hz = TILIQUA_CLOCK_SYNC_HZ
        self.soc = LunaSoC(
            cpu=VexRiscv(reset_addr=0x40000000, variant="cynthion"),
            clock_frequency=self.clock_sync_hz,
        )

        self.firmware_path = firmware_path
        firmware = get_mem_data(firmware_path,
                                data_width=32, endianness="little")

        self.soc.add_core_peripherals(
            uart_pins=self.uart_pins,
            internal_sram_size=32768*2,
            internal_sram_init=firmware
        )

        # ... add memory-mapped psram/hyperram peripheral (128Mbit)
        self.psram_base = 0xf1000000
        self.psram_size_bytes = 16*1024*1024
        self.soc.psram = PSRAMPeripheral(size=self.psram_size_bytes)
        self.soc.add_peripheral(self.soc.psram, addr=self.psram_base)

        # ... add our video PHY (DMAs from PSRAM starting at fb_base)
        fb_base = self.psram_base
        fb_size = (dvi_timings.h_active, dvi_timings.v_active)
        self.video = FramebufferPHY(
                fb_base=fb_base, dvi_timings=dvi_timings, fb_size=fb_size,
                bus_master=self.soc.psram.bus, sim=False)
        self.soc.psram.add_master(self.video.bus)

        self.i2c0 = I2CPeripheral(pads=self.i2c_pins, period_cyc=240)
        self.soc.add_peripheral(self.i2c0, addr=0xf0002000)

        self.encoder0 = EncoderPeripheral(pins=self.encoder_pins)
        self.soc.add_peripheral(self.encoder0, addr=0xf0003000)

        self.pmod0_periph = eurorack_pmod.EurorackPmodPeripheral(
                pmod=None, enable_out=audio_out_peripheral)
        self.soc.add_peripheral(self.pmod0_periph, addr=0xf0004000)

        self.temperature_periph = DieTemperaturePeripheral()
        self.soc.add_peripheral(self.temperature_periph, addr=0xf0005000)

        # ... add our video persistance effect (all writes gradually fade) -
        # this is an interesting alternative to double-buffering that looks
        # kind of like an old CRT with slow-scanning.
        self.persist_periph = PersistPeripheral(
            fb_base=self.video.fb_base,
            fb_size=fb_size,
            bus=self.soc.psram)
        self.soc.add_peripheral(self.persist_periph, addr=0xf0006000)

        self.permit_bus_traffic = Signal()

        super().__init__()

    def elaborate(self, platform):

        assert os.path.exists(self.firmware_path)

        m = Module()

        # add a eurorack pmod instance without an audio stream for basic self-testing
        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True,
                touch_enabled=self.touch,
                audio_192=self.audio_192)
        # connect it to our test peripheral before instantiating SoC.
        self.pmod0_periph.pmod = pmod0

        m.submodules.video = self.video
        m.submodules.soc = self.soc

        # Memory controller hangs if we start making requests to it straight away.
        on_delay = Signal(32)
        with m.If(on_delay < 0xFFFF):
            m.d.sync += on_delay.eq(on_delay+1)
        with m.Else():
            m.d.sync += self.permit_bus_traffic.eq(1)
            m.d.sync += self.video.enable.eq(1)
            m.d.sync += self.persist_periph.en.eq(1)

        # generate our domain clocks/resets
        m.submodules.car = platform.clock_domain_generator(audio_192=self.audio_192,
                                                           pixclk_pll=self.dvi_timings.pll)

        # Connect up our UART
        uart_io = platform.request("uart", 0)
        m.d.comb += [
            uart_io.tx.o.eq(self.uart_pins.tx),
            self.uart_pins.rx.eq(uart_io.rx)
        ]
        if hasattr(uart_io.tx, 'oe'):
            m.d.comb += uart_io.tx.oe.eq(~self.soc.uart._phy.tx.rdy),

        # Connect up the rotary encoder + switch
        enc = platform.request("encoder", 0)
        m.d.comb += [
            self.encoder_pins.i.i.eq(enc.i.i),
            self.encoder_pins.q.i.eq(enc.q.i),
            self.encoder_pins.s.i.eq(enc.s.i),
        ]

        # Connect i2c peripheral to mobo i2c
        mobo_i2c = platform.request("mobo_i2c")
        m.d.comb += [
            mobo_i2c.sda.o.eq(self.i2c_pins.sda.o),
            mobo_i2c.sda.oe.eq(self.i2c_pins.sda.oe),
            self.i2c_pins.sda.i.eq(mobo_i2c.sda.i),
            mobo_i2c.scl.o.eq(self.i2c_pins.scl.o),
            mobo_i2c.scl.oe.eq(self.i2c_pins.scl.oe),
            self.i2c_pins.scl.i.eq(mobo_i2c.scl.i),
        ]

        # Enable LED driver on motherboard
        m.d.comb += platform.request("mobo_leds_oe").o.eq(1),

        # Encoder push override -- hold for N sec will reconfigure
        # to next BOOTADDR specified by bitstream (normally bootloader).
        # Best to do this in hardware so we can still recover to the
        # bootloader even if e.g. a softcore crashes.

        REBOOT_SEC = 3
        boot_ctr = Signal(unsigned(32))
        with m.If(self.encoder0.button_sync):
            m.d.sync += boot_ctr.eq(boot_ctr + 1)
        with m.Else():
            m.d.sync += boot_ctr.eq(0)
        with m.If(boot_ctr > REBOOT_SEC*self.clock_sync_hz):
            m.d.comb += platform.request("self_program").o.eq(1)

        return m

    def genrust_constants(self):
        with open("src/rs/lib/src/generated_constants.rs", "w") as f:
            f.write(f"pub const CLOCK_SYNC_HZ: u32    = {self.clock_sync_hz};\n")
            f.write(f"pub const PSRAM_BASE: usize     = 0x{self.psram_base:x};\n")
            f.write(f"pub const PSRAM_SZ_BYTES: usize = 0x{self.psram_size_bytes:x};\n")
            f.write(f"pub const PSRAM_SZ_WORDS: usize = PSRAM_SZ_BYTES / 4;\n")
            f.write(f"pub const H_ACTIVE: u32         = {self.video.fb_hsize};\n")
            f.write(f"pub const V_ACTIVE: u32         = {self.video.fb_vsize};\n")
            f.write(f"pub const VIDEO_ROTATE_90: bool = {'true' if self.video_rotate_90 else 'false'};\n")
            f.write(f"pub const PSRAM_FB_BASE: usize  = 0x{self.video.fb_base:x};\n")
