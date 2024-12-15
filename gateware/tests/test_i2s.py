# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import unittest

import math

from amaranth              import *
from amaranth.sim          import *
from amaranth.lib          import wiring, data
from amaranth.lib.memory   import Memory
from amaranth.lib.fifo     import SyncFIFO
from tiliqua               import eurorack_pmod

from amaranth_future       import fixed

class I2CTests(unittest.TestCase):

    def test_i2s_tdm(self):

        m = Module()
        dut = eurorack_pmod.I2STDM()
        cal = eurorack_pmod.I2SCalibrator()
        wiring.connect(m, dut.o, cal.i_uncal)
        wiring.connect(m, cal.o_uncal, dut.i)
        m.d.comb += dut.en_dac.eq(cal.en_dac)
        m.submodules += [dut, cal]
        m = DomainRenamer({"audio": "sync"})(m)

        TICKS = 10000

        async def test_response(ctx):

            for n in range(16):
                def fn(n):
                    return 0.4*(math.sin(n*0.2) + math.sin(n))
                v = fixed.Const(fn(n), shape=eurorack_pmod.ASQ)
                ctx.set(cal.i_cal.valid, 1)
                #ctx.set(dac_fifo.w_stream.payload[0:16],  v.as_value())
                ctx.set(cal.i_cal.payload, [0, v, 0, 0])
                #ctx.set(dac_fifo.w_stream.payload[32:48], v.as_value())
                #ctx.set(dac_fifo.w_stream.payload[48:64], v.as_value())
                await ctx.tick()
                ctx.set(cal.i_cal.valid, 0)
                for n in range(8):
                    await ctx.tick()

            for n in range(TICKS):
                ctx.set(dut.sdout1, n % 5 == 0)
                await ctx.tick()

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(test_response)
        with sim.write_vcd(vcd_file=open("test_i2s_tdm.vcd", "w")):
            sim.run()
