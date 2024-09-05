import os

from amaranth                   import *
from amaranth.build             import *
from amaranth.lib               import wiring, data, stream
from amaranth.lib.wiring        import In, Out
from amaranth.lib.fifo          import AsyncFIFO
from amaranth.lib.cdc           import FFSynchronizer
from luna_soc.gateware.csr.base import Peripheral

from amaranth_future            import fixed

from example_usb_audio.util import EdgeToPulse

class EurorackPmodPeripheral(Peripheral, Elaboratable):

    """
    Extremely basic SoC peripheral for eurorack-pmod self-testing.
    TODO: extend this to allow glitch-free audio streaming with a FIFO interface.
    """

    def __init__(self, *, pmod, enable_out=False, **kwargs):

        super().__init__()

        self.pmod = pmod
        self.enable_out = enable_out

        # CSRs
        bank                   = self.csr_bank()

        # CODEC samples
        # TODO: synchronize to audio clock domain.
        # TODO: setattr breaks amaranth's name tracer for CSRs?

        # raw ADC samples
        self._sample_adc0 = bank.csr(16, "r")
        self._sample_adc1 = bank.csr(16, "r")
        self._sample_adc2 = bank.csr(16, "r")
        self._sample_adc3 = bank.csr(16, "r")

        # calibrated incoming samples
        self._sample_i0   = bank.csr(16, "r")
        self._sample_i1   = bank.csr(16, "r")
        self._sample_i2   = bank.csr(16, "r")
        self._sample_i3   = bank.csr(16, "r")

        # calibrated outgoing samples
        if self.enable_out:
            self._sample_o0   = bank.csr(16, "w")
            self._sample_o1   = bank.csr(16, "w")
            self._sample_o2   = bank.csr(16, "w")
            self._sample_o3   = bank.csr(16, "w")

        # continuous touch sensing
        self._touch0      = bank.csr(8, "r")
        self._touch1      = bank.csr(8, "r")
        self._touch2      = bank.csr(8, "r")
        self._touch3      = bank.csr(8, "r")
        self._touch4      = bank.csr(8, "r")
        self._touch5      = bank.csr(8, "r")
        self._touch6      = bank.csr(8, "r")
        self._touch7      = bank.csr(8, "r")

        # manual LED outputs
        self._led_mode  = bank.csr(8, "w")
        self._led0      = bank.csr(8, "w")
        self._led1      = bank.csr(8, "w")
        self._led2      = bank.csr(8, "w")
        self._led3      = bank.csr(8, "w")
        self._led4      = bank.csr(8, "w")
        self._led5      = bank.csr(8, "w")
        self._led6      = bank.csr(8, "w")
        self._led7      = bank.csr(8, "w")

        # Data from I2C peripherals on eurorack-pmod hardware.
        self._jack             = bank.csr(8, "r")
        self._eeprom_mfg       = bank.csr(8, "r")
        self._eeprom_dev       = bank.csr(8, "r")
        self._eeprom_serial    = bank.csr(32, "r")

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge

        # Hook all pmod signals up to CSRs

        for n in range(4):
            m.submodules += FFSynchronizer(
                    self.pmod.sample_adc[n], getattr(self, f"_sample_adc{n}").r_data, reset=0)
            m.submodules += FFSynchronizer(
                    self.pmod.sample_i[n], getattr(self, f"_sample_i{n}").r_data, reset=0)
            if self.enable_out:
                with m.If(getattr(self, f"_sample_o{n}").w_stb):
                    # TODO proper sync
                    m.d.sync += self.pmod.sample_o[n].eq(getattr(self, f"_sample_o{n}").w_data)

        for n in range(8):
            m.submodules += FFSynchronizer(
                    self.pmod.touch[n], getattr(self, f"_touch{n}").r_data, reset=0)

        # LED control
        with m.If(self._led_mode.w_stb):
            m.d.sync += self.pmod.led_mode.eq(self._led_mode.w_data)
        for n in range(8):
            with m.If(getattr(self, f"_led{n}").w_stb):
                m.d.sync += self.pmod.led[n].eq(getattr(self, f"_led{n}").w_data)

        m.submodules += FFSynchronizer(
                self.pmod.jack, self._jack.r_data, reset=0)
        m.submodules += FFSynchronizer(
                self.pmod.eeprom_mfg, self._eeprom_mfg.r_data, reset=0)
        m.submodules += FFSynchronizer(
                self.pmod.eeprom_dev, self._eeprom_dev.r_data, reset=0)
        m.submodules += FFSynchronizer(
                self.pmod.eeprom_serial, self._eeprom_serial.r_data, reset=0)

        return m
