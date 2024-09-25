# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import math
import sys
import unittest

from amaranth              import *
from amaranth.sim          import *
from amaranth.lib          import wiring
from tiliqua               import cache

from amaranth_soc.csr      import wishbone

class CacheTests(unittest.TestCase):

    def test_cache(self):

        dut = cache.WishboneL2Cache()

        async def testbench(ctx):
            for adr in range(0, 5):
                await ctx.tick()
                ctx.set(dut.master.adr, adr)
                ctx.set(dut.master.cyc, 1)
                ctx.set(dut.master.stb, 1)
                ctx.set(dut.master.we,  0)
                ctx.set(dut.master.sel, 0b1111)
                while not ctx.get(dut.slave.stb):
                    await ctx.tick()
                ctx.set(dut.slave.ack,   1)
                ctx.set(dut.slave.dat_r, 0xdead0000 | adr)
                await ctx.tick()
                ctx.set(dut.slave.ack,  0)
                while not ctx.get(dut.master.ack):
                    await ctx.tick()
                ctx.set(dut.master.cyc, 0)
                ctx.set(dut.master.stb, 0)
                await ctx.tick().repeat(10)

            for adr in range(0, 5):
                await ctx.tick()
                ctx.set(dut.master.adr,   adr)
                ctx.set(dut.master.cyc,   1)
                ctx.set(dut.master.stb,   1)
                ctx.set(dut.master.we,    1)
                ctx.set(dut.master.sel,   0b1111)
                ctx.set(dut.master.dat_w, 0xfeed0000 | adr)
                while not ctx.get(dut.master.ack):
                    await ctx.tick()
                await ctx.tick()
                ctx.set(dut.master.cyc, 0)
                ctx.set(dut.master.stb, 0)
                await ctx.tick().repeat(10)


        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_cache.vcd", "w")):
            sim.run()
