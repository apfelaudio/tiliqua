# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import unittest

from amaranth              import *
from amaranth.sim          import *
from amaranth.lib          import wiring, data
from amaranth.lib.memory   import Memory
from tiliqua               import eurorack_pmod

class I2CTests(unittest.TestCase):

    def test_i2s_tdm(self):

        m = Module()
        dut = eurorack_pmod.AK4619()
        cal = eurorack_pmod.Calibrator()
        wiring.connect(m, dut.o, cal.i_uncal)
        wiring.connect(m, cal.o_uncal, dut.i)
        m.submodules += [dut, cal]
        m = DomainRenamer({"audio": "sync"})(m)

        TICKS = 10000

        async def test_response(ctx):
            for n in range(TICKS):
                ctx.set(dut.sdout1, n % 5 == 0)
                ctx.set(cal.i_cal.payload[0].raw(), 0xDEAD)
                await ctx.tick()

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(test_response)
        with sim.write_vcd(vcd_file=open("test_i2s_tdm.vcd", "w")):
            sim.run()
