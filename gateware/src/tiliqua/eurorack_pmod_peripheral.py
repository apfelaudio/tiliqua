# Peripheral for accessing eurorack-pmod hardware from an SoC.
#
# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

import os

from amaranth                   import *
from amaranth.build             import *
from amaranth.lib               import wiring, data, stream
from amaranth.lib.wiring        import In, Out, flipped, connect
from amaranth.lib.cdc           import FFSynchronizer

from amaranth_soc               import csr

from amaranth_future            import fixed

class Peripheral(wiring.Component):

    class ISampleReg(csr.Register, access="r"):
        sample: csr.Field(csr.action.R, unsigned(16))

    class OSampleReg(csr.Register, access="w"):
        sample: csr.Field(csr.action.W, unsigned(16))

    class TouchReg(csr.Register, access="r"):
        touch: csr.Field(csr.action.R, unsigned(8))

    class TouchErrorsReg(csr.Register, access="r"):
        value: csr.Field(csr.action.R, unsigned(8))

    class LEDReg(csr.Register, access="w"):
        led: csr.Field(csr.action.W, unsigned(8))

    class JackReg(csr.Register, access="r"):
        jack: csr.Field(csr.action.R, unsigned(8))

    class EEPROMReg(csr.Register, access="r"):
        mfg: csr.Field(csr.action.R, unsigned(8))
        dev: csr.Field(csr.action.R, unsigned(8))
        serial: csr.Field(csr.action.R, unsigned(32))

    class FlagsReg(csr.Register, access="w"):
        mute: csr.Field(csr.action.W, unsigned(1))

    def __init__(self, *, pmod, enable_out=False, **kwargs):
        self.pmod = pmod
        self.enable_out = enable_out

        regs = csr.Builder(addr_width=6, data_width=8)

        # ADC and input samples
        self._sample_adc = [regs.add(f"sample_adc{i}", self.ISampleReg()) for i in range(4)]
        self._sample_i = [regs.add(f"sample_i{i}", self.ISampleReg()) for i in range(4)]

        # Output samples
        if self.enable_out:
            self._sample_o = [regs.add(f"sample_o{i}", self.OSampleReg()) for i in range(4)]

        # Touch sensing
        self._touch = [regs.add(f"touch{i}", self.TouchReg()) for i in range(8)]
        self._touch_err = regs.add("touch_err", self.TouchErrorsReg())

        # LED control
        self._led_mode = regs.add("led_mode", self.LEDReg())
        self._led = [regs.add(f"led{i}", self.LEDReg()) for i in range(8)]

        # I2C peripheral data
        self._jack = regs.add("jack", self.JackReg())
        self._eeprom = regs.add("eeprom", self.EEPROMReg())

        self._flags = regs.add("flags", self.FlagsReg())

        self._bridge = csr.Bridge(regs.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
            "mute": In(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge

        connect(m, flipped(self.bus), self._bridge.bus)

        m.d.comb += [
            self._touch_err.f.value.r_data.eq(self.pmod.touch_err),
            self._jack.f.jack.r_data.eq(self.pmod.jack),
            self._eeprom.f.mfg.r_data.eq(self.pmod.eeprom_mfg),
            self._eeprom.f.dev.r_data.eq(self.pmod.eeprom_dev),
            self._eeprom.f.serial.r_data.eq(self.pmod.eeprom_serial),
        ]

        mute_reg = Signal(init=0)
        m.d.comb += self.pmod.codec_mute.eq(mute_reg | self.mute)
        with m.If(self._flags.f.mute.w_stb):
            m.d.sync += mute_reg.eq(self._flags.f.mute.w_data)

        with m.If(self._led_mode.f.led.w_stb):
            m.d.sync += self.pmod.led_mode.eq(self._led_mode.f.led.w_data)

        for i in range(8):
            m.d.comb += self._touch[i].f.touch.r_data.eq(self.pmod.touch[i])
            with m.If(self._led[i].f.led.w_stb):
                m.d.sync += self.pmod.led[i].eq(self._led[i].f.led.w_data)

        # Audio domain signals need synchronizers
        for i in range(4):
            m.submodules += FFSynchronizer(self.pmod.sample_adc[i], self._sample_adc[i].f.sample.r_data, reset=0)
            m.submodules += FFSynchronizer(self.pmod.sample_i[i], self._sample_i[i].f.sample.r_data, reset=0)
            if self.enable_out:
                with m.If(self._sample_o[i].f.sample.w_stb):
                    m.d.sync += self.pmod.sample_o[i].eq(self._sample_o[i].f.sample.w_data)


        return m
