# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Amaranth wrapper and clock domain crossing for `eurorack-pmod` hardware."""

import os

from amaranth                   import *
from amaranth.build             import *
from amaranth.lib               import wiring, data
from amaranth.lib.wiring        import In, Out
from amaranth.lib.fifo          import AsyncFIFO
from amaranth.lib.cdc           import FFSynchronizer
from luna_soc.gateware.csr.base import Peripheral

from amaranth_future       import fixed, stream

from example_usb_audio.util import EdgeToPulse

WIDTH = 16

# Native 'Audio sample SQ', shape of audio samples from CODEC.
ASQ = fixed.SQ(0, WIDTH-1)

class AudioStream(wiring.Component):

    """
    Domain crossing logic to move samples from `eurorack-pmod` logic in the audio domain
    to logic in a different (faster) domain using a stream interface.
    This is used by most DSP examples for glitch-free audio streaming.
    """

    istream: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))
    ostream: In(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def __init__(self, eurorack_pmod, stream_domain="sync", fifo_depth=8):

        self.eurorack_pmod = eurorack_pmod
        self.stream_domain = stream_domain
        self.fifo_depth = fifo_depth

        super().__init__()

    def elaborate(self, platform) -> Module:

        m = Module()

        m.submodules.adc_fifo = adc_fifo = AsyncFIFO(
                width=self.eurorack_pmod.sample_i.shape().size, depth=self.fifo_depth,
                w_domain="audio", r_domain=self.stream_domain)
        m.submodules.dac_fifo = dac_fifo = AsyncFIFO(
                width=self.eurorack_pmod.sample_o.shape().size, depth=self.fifo_depth,
                w_domain=self.stream_domain, r_domain="audio")

        adc_stream = stream.fifo_r_stream(adc_fifo)
        dac_stream = wiring.flipped(stream.fifo_w_stream(dac_fifo))

        wiring.connect(m, adc_stream, wiring.flipped(self.istream))
        wiring.connect(m, wiring.flipped(self.ostream), dac_stream)

        eurorack_pmod = self.eurorack_pmod

        # below is synchronous logic in the *audio domain*

        # On every fs_strobe, latch and write all channels concatenated
        # into one entry of adc_fifo.

        m.d.audio += [
            # WARN: ignoring rdy in write domain. Mostly fine as long as
            # stream_domain is faster than audio_domain.
            adc_fifo.w_en.eq(eurorack_pmod.fs_strobe),
            adc_fifo.w_data.eq(self.eurorack_pmod.sample_i),
        ]


        # Once fs_strobe hits, write the next pending samples to CODEC

        with m.FSM(domain="audio") as fsm:
            with m.State('READ'):
                with m.If(eurorack_pmod.fs_strobe & dac_fifo.r_rdy):
                    m.d.audio += dac_fifo.r_en.eq(1)
                    m.next = 'SEND'
            with m.State('SEND'):
                m.d.audio += [
                    dac_fifo.r_en.eq(0),
                    self.eurorack_pmod.sample_o.eq(dac_fifo.r_data),
                ]
                m.next = 'READ'

        return m

class EurorackPmodPeripheral(Peripheral, Elaboratable):

    """
    Extremely basic SoC peripheral for eurorack-pmod self-testing.
    TODO: extend this to allow glitch-free audio streaming with a FIFO interface.
    """

    def __init__(self, *, pmod, **kwargs):

        super().__init__()

        self.pmod = pmod

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
            with m.If(getattr(self, f"_sample_o{n}").w_stb):
                # TODO proper sync
                m.d.sync += self.pmod.sample_o[n].eq(getattr(self, f"_sample_o{n}").w_data)

        for n in range(8):
            m.submodules += FFSynchronizer(
                    self.pmod.touch[n], getattr(self, f"_touch{n}").r_data, reset=0)

        m.submodules += FFSynchronizer(
                self.pmod.jack, self._jack.r_data, reset=0)
        m.submodules += FFSynchronizer(
                self.pmod.eeprom_mfg, self._eeprom_mfg.r_data, reset=0)
        m.submodules += FFSynchronizer(
                self.pmod.eeprom_dev, self._eeprom_dev.r_data, reset=0)
        m.submodules += FFSynchronizer(
                self.pmod.eeprom_serial, self._eeprom_serial.r_data, reset=0)

        return m

class EurorackPmod(wiring.Component):
    """
    Amaranth wrapper for Verilog files from `eurorack-pmod` project.

    Requires an "audio" clock domain running at 12.288MHz (256*Fs).

    There are some Amaranth I2S cores around, however they seem to
    use oversampling, which can be glitchy at such high bit clock
    rates (as needed for 4x4 TDM the AK4619 requires).
    """

    # Output strobe once per sample in the `audio` domain (256*Fs)
    fs_strobe: Out(1)

    # Audio samples latched on `fs_strobe`.
    sample_i: Out(data.ArrayLayout(ASQ, 4))
    sample_o: In(data.ArrayLayout(ASQ, 4))

    # Touch sensing and jacksense outputs.
    touch: Out(8).array(8)
    jack: Out(8)

    # Read from the onboard I2C eeprom.
    # These will be valid a few hundred milliseconds after boot.
    eeprom_mfg: Out(8)
    eeprom_dev: Out(8)
    eeprom_serial: Out(32)

    # Signals only used for calibration
    sample_adc: Out(signed(WIDTH)).array(4)
    force_dac_output: In(signed(WIDTH))

    def __init__(self, pmod_pins, hardware_r33=True, touch_enabled=True):

        self.pmod_pins = pmod_pins
        self.hardware_r33 = hardware_r33
        self.touch_enabled = touch_enabled

        super().__init__()

    def add_verilog_sources(self, platform):

        #
        # Verilog sources from `eurorack-pmod` project.
        #
        # Assumes `eurorack-pmod` repo is checked out in this directory and
        # `git submodule update --init` has been run!
        #

        vroot = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                             "../../deps/eurorack-pmod/gateware")

        # Defines and default cal for PMOD hardware version.
        if self.hardware_r33:
            touch_define = "`define TOUCH_SENSE_ENABLED" if self.touch_enabled else ""
            platform.add_file("eurorack_pmod_defines.sv", f"`define HW_R33\n{touch_define}")
            platform.add_file("cal/cal_mem_default_r33.hex",
                              open(os.path.join(vroot, "cal/cal_mem_default_r33.hex")))
        else:
            platform.add_file("eurorack_pmod_defines.sv", "`define HW_R31")
            platform.add_file("cal/cal_mem_default_r31.hex",
                              open(os.path.join(vroot, "cal/cal_mem_default_r31.hex")))

        # Verilog implementation
        platform.add_file("eurorack_pmod.sv", open(os.path.join(vroot, "eurorack_pmod.sv")))
        platform.add_file("pmod_i2c_master.sv", open(os.path.join(vroot, "drivers/pmod_i2c_master.sv")))
        platform.add_file("ak4619.sv", open(os.path.join(vroot, "drivers/ak4619.sv")))
        platform.add_file("cal.sv", open(os.path.join(vroot, "cal/cal.sv")))
        platform.add_file("i2c_master.sv", open(os.path.join(vroot, "external/no2misc/rtl/i2c_master.v")))

        # .hex files for I2C initialization
        platform.add_file("drivers/ak4619-cfg.hex",
                          open(os.path.join(vroot, "drivers/ak4619-cfg.hex")))
        platform.add_file("drivers/pca9635-cfg.hex",
                          open(os.path.join(vroot, "drivers/pca9635-cfg.hex")))
        platform.add_file("drivers/cy8cmbr3108-cfg.hex",
                          open(os.path.join(vroot, "drivers/cy8cmbr3108-cfg.hex")))

    def elaborate(self, platform) -> Module:

        m = Module()

        self.add_verilog_sources(platform)

        pmod_pins = self.pmod_pins

        # 1/256 clk_fs divider. this is not a true clock domain, don't create one.
        # FIXME: this should be removed from `eurorack-pmod` verilog implementation
        # and just replaced with a strobe. that's all its used for anyway. For this
        # reason we do NOT expose this signal and only the 'strobe' version created next.
        clk_fs = Signal()
        clkdiv_fs = Signal(8)
        m.d.audio += clkdiv_fs.eq(clkdiv_fs+1)
        m.d.comb += clk_fs.eq(clkdiv_fs[-1])

        # Create a strobe from the sample clock 'clk_fs` that asserts for 1 cycle
        # per sample in the 'audio' domain. This is useful for latching our samples
        # and hooking up to various signals in our FIFOs external to this module.
        m.submodules.fs_edge = fs_edge = DomainRenamer("audio")(EdgeToPulse())
        m.d.audio += fs_edge.edge_in.eq(clk_fs),
        m.d.comb += self.fs_strobe.eq(fs_edge.pulse_out)


        # When i2c oe is asserted, we always want to pull down.
        m.d.comb += [
            pmod_pins.i2c_scl.o.eq(0),
            pmod_pins.i2c_sda.o.eq(0),
        ]

        m.submodules.veurorack_pmod = Instance("eurorack_pmod",
            # Parameters
            p_W = WIDTH,

            # Ports (clk + reset)
            i_clk_256fs = ClockSignal("audio"),
            i_strobe = self.fs_strobe,
            i_rst = ResetSignal("audio"),

            # Pads (tristate, may require different logic to hook these
            # up to pads depending on the target platform).
            o_i2c_scl_oe = pmod_pins.i2c_scl.oe,
            i_i2c_scl_i = pmod_pins.i2c_scl.i,
            o_i2c_sda_oe = pmod_pins.i2c_sda.oe,
            i_i2c_sda_i = pmod_pins.i2c_sda.i,

            # Pads (directly hooked up to pads without extra logic required)
            o_pdn = pmod_pins.pdn.o,
            o_mclk = pmod_pins.mclk.o,
            o_sdin1 = pmod_pins.sdin1.o,
            i_sdout1 = pmod_pins.sdout1.i,
            o_lrck = pmod_pins.lrck.o,
            o_bick = pmod_pins.bick.o,

            # Ports (clock at clk_fs)
            o_cal_in0 = self.sample_i[0],
            o_cal_in1 = self.sample_i[1],
            o_cal_in2 = self.sample_i[2],
            o_cal_in3 = self.sample_i[3],
            i_cal_out0 = self.sample_o[0],
            i_cal_out1 = self.sample_o[1],
            i_cal_out2 = self.sample_o[2],
            i_cal_out3 = self.sample_o[3],

            # Ports (serialized data fetched over I2C)
            o_eeprom_mfg = self.eeprom_mfg,
            o_eeprom_dev = self.eeprom_dev,
            o_eeprom_serial = self.eeprom_serial,
            o_jack = self.jack,

            o_touch0 = self.touch[0],
            o_touch1 = self.touch[1],
            o_touch2 = self.touch[2],
            o_touch3 = self.touch[3],
            o_touch4 = self.touch[4],
            o_touch5 = self.touch[5],
            o_touch6 = self.touch[6],
            o_touch7 = self.touch[7],

            # Debug ports
            o_sample_adc0 = self.sample_adc[0],
            o_sample_adc1 = self.sample_adc[1],
            o_sample_adc2 = self.sample_adc[2],
            o_sample_adc3 = self.sample_adc[3],
            i_force_dac_output = self.force_dac_output,
        )

        return m

def pins_from_pmod_connector_with_ribbon(platform, pmod_index):
    """Create a eurorack-pmod resource on a given PMOD connector. Assumes ribbon cable flip."""
    eurorack_pmod = [
        Resource(f"eurorack_pmod{pmod_index}", pmod_index,
            Subsignal("sdin1",   Pins("1",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("sdout1",  Pins("2",  conn=("pmod", pmod_index), dir='i')),
            Subsignal("lrck",    Pins("3",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("bick",    Pins("4",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("mclk",    Pins("10", conn=("pmod", pmod_index), dir='o')),
            Subsignal("pdn",     Pins("9",  conn=("pmod", pmod_index), dir='o')),
            Subsignal("i2c_sda", Pins("8",  conn=("pmod", pmod_index), dir='io')),
            Subsignal("i2c_scl", Pins("7",  conn=("pmod", pmod_index), dir='io')),
            Attrs(IO_TYPE="LVCMOS33"),
        )
    ]
    platform.add_resources(eurorack_pmod)
    return platform.request(f"eurorack_pmod{pmod_index}")

