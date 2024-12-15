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
        dut = eurorack_pmod.AK4619()
        cal = eurorack_pmod.Calibrator()
        dac_fifo = SyncFIFO(
            width=cal.i_cal.payload.shape().size, depth=16)
        wiring.connect(m, dut.o, cal.i_uncal)
        wiring.connect(m, cal.o_uncal, dut.i)
        wiring.connect(m, dac_fifo.r_stream, cal.i_cal)
        m.submodules += [dut, cal, dac_fifo]
        m = DomainRenamer({"audio": "sync"})(m)

        TICKS = 10000

        async def test_response(ctx):

            for n in range(16):
                def fn(n):
                    return 0.4*(math.sin(n*0.2) + math.sin(n))
                v = fixed.Const(fn(n), shape=eurorack_pmod.ASQ)
                ctx.set(dac_fifo.w_stream.valid,         1)
                #ctx.set(dac_fifo.w_stream.payload[0:16],  v.as_value())
                ctx.set(dac_fifo.w_stream.payload[16:32], v.as_value())
                #ctx.set(dac_fifo.w_stream.payload[32:48], v.as_value())
                #ctx.set(dac_fifo.w_stream.payload[48:64], v.as_value())
                await ctx.tick()

            ctx.set(dac_fifo.w_stream.valid, 0)

            for n in range(TICKS):
                ctx.set(dut.sdout1, n % 5 == 0)
                await ctx.tick()

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(test_response)
        with sim.write_vcd(vcd_file=open("test_i2s_tdm.vcd", "w")):
            sim.run()
