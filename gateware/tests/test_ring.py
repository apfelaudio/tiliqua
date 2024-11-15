
# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import math
import sys
import unittest
from parameterized         import parameterized

from amaranth              import *
from amaranth.sim          import *
from amaranth.lib          import wiring

from tiliqua.eurorack_pmod import ASQ
from tiliqua               import dsp

class RingTests(unittest.TestCase):

    def test_ring(self):

        m = Module()

        m.submodules.server = server = dsp.RingMACServer()
        m.submodules.vca0    = vca0    = dsp.MacVCA(mac=server.add_client())
        m.submodules.vca1    = vca1    = dsp.MacVCA(mac=server.add_client())

        async def testbench(ctx):
            await ctx.tick()
            ctx.set(vca0.i.valid, 1)
            ctx.set(vca0.i.payload[0].as_value(), 1024)
            ctx.set(vca0.i.payload[1].as_value(), 2000)
            ctx.set(vca0.o.ready, 1)
            ctx.set(vca1.i.valid, 1)
            ctx.set(vca1.i.payload[0].as_value(), 512)
            ctx.set(vca1.i.payload[1].as_value(), 1000)
            ctx.set(vca1.o.ready, 1)
            for n in range(0, 1000):
                await ctx.tick()

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_ring.vcd", "w")):
            sim.run()
