# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD--3-Clause

import os

from amaranth              import *
from amaranth.build        import *

from util                  import EdgeToPulse

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

class EurorackPmod(Elaboratable):
    """
    Amaranth wrapper for Verilog files from `eurorack-pmod` project.

    Requires an "audio" clock domain running at 12.288MHz (256*Fs).

    There are some Amaranth I2S cores around, however they seem to
    use oversampling, which can be glitchy at such high bit clock
    rates (as needed for 4x4 TDM the AK4619 requires).
    """

    def __init__(self, pmod_pins, width=16, hardware_r33=True):
        self.pmod_pins = pmod_pins
        self.width = width
        self.hardware_r33 = hardware_r33

        self.cal_in0 = Signal(signed(width))
        self.cal_in1 = Signal(signed(width))
        self.cal_in2 = Signal(signed(width))
        self.cal_in3 = Signal(signed(width))

        self.cal_out0 = Signal(signed(width))
        self.cal_out1 = Signal(signed(width))
        self.cal_out2 = Signal(signed(width))
        self.cal_out3 = Signal(signed(width))

        self.eeprom_mfg = Signal(8)
        self.eeprom_dev = Signal(8)
        self.eeprom_serial = Signal(32)
        self.jack = Signal(8)

        self.sample_adc0 = Signal(signed(width))
        self.sample_adc1 = Signal(signed(width))
        self.sample_adc2 = Signal(signed(width))
        self.sample_adc3 = Signal(signed(width))

        self.force_dac_output = Signal(signed(width))

        self.touch = [Signal(8) for _ in range(8)]

        self.fs_strobe = Signal()


    def add_verilog_sources(self, platform):

        #
        # Verilog sources from `eurorack-pmod` project.
        #
        # Assumes `eurorack-pmod` repo is checked out in this directory and
        # `git submodule update --init` has been run!
        #

        vroot = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                             "../deps/eurorack-pmod/gateware")

        # Defines and default cal for PMOD hardware version.
        if self.hardware_r33:
            platform.add_file("eurorack_pmod_defines.sv", "`define HW_R33\n`define TOUCH_SENSE_ENABLED")
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
            p_W = self.width,

            # Ports (clk + reset)
            i_clk_256fs = ClockSignal("audio"),
            i_clk_fs = clk_fs, #FIXME: deprecate
            i_rst = ResetSignal("audio"),

            # Pads (tristate, require different logic to hook these
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
            o_cal_in0 = self.cal_in0,
            o_cal_in1 = self.cal_in1,
            o_cal_in2 = self.cal_in2,
            o_cal_in3 = self.cal_in3,
            i_cal_out0 = self.cal_out0,
            i_cal_out1 = self.cal_out1,
            i_cal_out2 = self.cal_out2,
            i_cal_out3 = self.cal_out3,

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
            o_sample_adc0 = self.sample_adc0,
            o_sample_adc1 = self.sample_adc1,
            o_sample_adc2 = self.sample_adc2,
            o_sample_adc3 = self.sample_adc3,
            i_force_dac_output = self.force_dac_output,
        )

        return m
