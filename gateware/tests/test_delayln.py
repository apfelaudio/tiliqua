# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import math
import sys
import unittest

from amaranth              import *
from amaranth.sim          import *
from amaranth.lib          import wiring
from tiliqua               import dsp, eurorack_pmod
from tiliqua.eurorack_pmod import ASQ

from amaranth_soc          import csr
from amaranth_soc.csr      import wishbone

from amaranth_future       import fixed

class DelayLineTests(unittest.TestCase):

    def test_persist(self):

        class FakeBusMaster:
            addr_width = 30

        dut = dsp.DelayLineWriter(
            max_delay=16
        )

        tap1 = dut.add_tap()
        tap2 = dut.add_tap()

        async def stimulus_wr(ctx):
            for n in range(0, sys.maxsize):
                ctx.set(dut.sw.valid, 1)
                ctx.set(dut.sw.payload,
                        fixed.Const(0.8*math.sin(n*0.2), shape=ASQ))
                await ctx.tick()
                ctx.set(dut.sw.valid, 0)
                await ctx.tick().repeat(10)

        async def stimulus_rd1(ctx):
            ctx.set(tap1.o.ready, 1)
            for n in range(0, sys.maxsize):
                ctx.set(tap1.i.valid, 1)
                ctx.set(tap1.i.payload, 4)
                await ctx.tick()
                ctx.set(tap1.i.valid, 0)
                await ctx.tick().repeat(10)

        async def stimulus_rd2(ctx):
            ctx.set(tap2.o.ready, 1)
            for n in range(0, sys.maxsize):
                ctx.set(tap2.i.valid, 1)
                ctx.set(tap2.i.payload, 10)
                await ctx.tick()
                ctx.set(tap2.i.valid, 0)
                await ctx.tick().repeat(10)

        async def testbench(ctx):
            # Simulate some acks
            mem = [0] * 16
            for _ in range(200):
                while not ctx.get(dut.bus.stb):
                    await ctx.tick()
                # Simulate acks delayed from stb
                await ctx.tick()
                ctx.set(dut.bus.ack, 1)
                adr = ctx.get(dut.bus.adr)
                if ctx.get(dut.bus.we):
                    mem[adr] = ctx.get(dut.bus.dat_w)
                    print("write", mem[adr], "@", adr)
                else:
                    print("read", mem[adr], "@", adr)
                    ctx.set(dut.bus.dat_r, mem[ctx.get(dut.bus.adr)])
                await ctx.tick()
                ctx.set(dut.bus.ack, 0)
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        sim.add_process(stimulus_wr)
        sim.add_process(stimulus_rd1)
        sim.add_process(stimulus_rd2)
        with sim.write_vcd(vcd_file=open("test_delayln.vcd", "w")):
            sim.run()
