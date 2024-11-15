
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

        n_clients = 10

        m.submodules.server = server = dsp.RingMACServer()
        for n in range(n_clients):
            setattr(m.submodules, f"vca{n}", dsp.MacVCA(mac=server.add_client()))

        """
        for n in range(n_clients):
            setattr(m.submodules, f"vca{n}", dsp.MacVCA())
        """

        async def testbench(ctx):
            await ctx.tick()
            for n in range(n_clients):
                vca = getattr(m.submodules, f"vca{n}")
                ctx.set(vca.i.valid, 1)
                ctx.set(vca.i.payload[0].as_value(), 1024*n)
                ctx.set(vca.i.payload[1].as_value(), 2000)
                ctx.set(vca.o.ready, 1)
            for n in range(0, 1000):
                await ctx.tick()

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_ring.vcd", "w")):
            sim.run()