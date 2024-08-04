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
from tiliqua.sim                                 import FakeEurorackPmod, FakeTiliquaDomainGenerator

from example_vectorscope.top                     import Persistance

from luna_soc.gateware.csr.base                  import Peripheral

TILIQUA_CLOCK_SYNC_HZ = int(60e6)

class TiliquaSoc(Elaboratable):
    def __init__(self, *, firmware_path, dvi_timings, audio_192=False, audio_out_peripheral=True,
                 sim=False):

        self.audio_192 = audio_192
        self.dvi_timings = dvi_timings
        self.sim = sim

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
            internal_sram_size=32768,
            internal_sram_init=firmware
        )

        # ... add memory-mapped psram/hyperram peripheral (128Mbit)
        self.psram_base = 0x20000000
        self.psram_size_bytes = 16*1024*1024
        self.soc.psram = PSRAMPeripheral(size=self.psram_size_bytes, sim=self.sim)
        self.soc.add_peripheral(self.soc.psram, addr=self.psram_base)

        # ... add our video PHY (DMAs from PSRAM starting at fb_base)
        fb_base = self.psram_base
        fb_size = (dvi_timings.h_active, dvi_timings.v_active)
        self.video = FramebufferPHY(
                fb_base=fb_base, dvi_timings=dvi_timings, fb_size=fb_size,
                bus_master=self.soc.psram.bus, sim=self.sim)
        self.soc.psram.add_master(self.video.bus)

        # ... add our video persistance effect (all writes gradually fade) -
        # this is an interesting alternative to double-buffering that looks
        # kind of like an old CRT with slow-scanning.
        self.persist = Persistance(
                fb_base=fb_base, bus_master=self.soc.psram.bus, fb_size=fb_size)
        self.soc.psram.add_master(self.persist.bus)

        self.i2c0 = I2CPeripheral(pads=self.i2c_pins, period_cyc=240)
        self.soc.add_peripheral(self.i2c0, addr=0xf0002000)

        self.encoder0 = EncoderPeripheral(pins=self.encoder_pins)
        self.soc.add_peripheral(self.encoder0, addr=0xf0003000)

        self.pmod0_periph = eurorack_pmod.EurorackPmodPeripheral(
                pmod=None, enable_out=audio_out_peripheral)
        self.soc.add_peripheral(self.pmod0_periph, addr=0xf0004000)

        if not self.sim:
            self.temperature_periph = DieTemperaturePeripheral()
            self.soc.add_peripheral(self.temperature_periph, addr=0xf0005000)

        if self.sim:
            self.pmod0 = FakeEurorackPmod()
            self.inject0 = Signal(signed(16))
            self.inject1 = Signal(signed(16))
            self.inject2 = Signal(signed(16))
            self.inject3 = Signal(signed(16))

        super().__init__()

    def elaborate(self, platform):

        assert os.path.exists(self.firmware_path)

        m = Module()

        # generate our domain clocks/resets
        if self.sim:
            m.submodules.car = FakeTiliquaDomainGenerator()
            m.d.comb += [
                self.pmod0.sample_inject[0]._target.eq(self.inject0),
                self.pmod0.sample_inject[1]._target.eq(self.inject1),
                self.pmod0.sample_inject[2]._target.eq(self.inject2),
                self.pmod0.sample_inject[3]._target.eq(self.inject3)
            ]
        else:
            m.submodules.car = platform.clock_domain_generator(audio_192=self.audio_192,
                                                               pixclk_pll=self.dvi_timings.pll)

        if not self.sim:
            # add a eurorack pmod instance without an audio stream for basic self-testing
            self.pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True,
                touch_enabled=False,
                audio_192=self.audio_192)

        # connect it to our test peripheral before instantiating SoC.
        self.pmod0_periph.pmod = self.pmod0

        m.submodules.video = self.video
        m.submodules.persist = self.persist
        m.submodules.soc = self.soc

        # Memory controller hangs if we start making requests to it straight away.
        on_delay = Signal(32)
        with m.If(on_delay < 0xFFFF):
            m.d.sync += on_delay.eq(on_delay+1)
        with m.Else():
            m.d.sync += self.video.enable.eq(1)
            m.d.sync += self.persist.enable.eq(1)

        """
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
        """

        return m

    def genrust_constants(self):
        with open("src/rs/lib/src/generated_constants.rs", "w") as f:
            f.write(f"pub const CLOCK_SYNC_HZ: u32    = {self.clock_sync_hz};\n")
            f.write(f"pub const PSRAM_BASE: usize     = 0x{self.psram_base:x};\n")
            f.write(f"pub const PSRAM_SZ_BYTES: usize = 0x{self.psram_size_bytes:x};\n")
            f.write(f"pub const PSRAM_SZ_WORDS: usize = PSRAM_SZ_BYTES / 4;\n")
            f.write(f"pub const H_ACTIVE: u32         = {self.video.fb_hsize};\n")
            f.write(f"pub const V_ACTIVE: u32         = {self.video.fb_vsize};\n")
            f.write(f"pub const PSRAM_FB_BASE: usize  = 0x{self.video.fb_base:x};\n")
