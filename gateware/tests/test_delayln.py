# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import math
import sys
import unittest

from amaranth              import *
from amaranth.sim          import *
from amaranth.lib          import wiring
from amaranth.lib.wiring   import In, Out
from tiliqua               import dsp, eurorack_pmod, cache, delay_line
from tiliqua.eurorack_pmod import ASQ

from amaranth_soc          import csr
from amaranth_soc          import wishbone

from amaranth_future       import fixed

class DelayLineTests(unittest.TestCase):

    def test_psram_delayln(self):

        dut = delay_line.DelayLine(
            max_delay=256,
            psram_backed=True,
            base=0x0,
            addr_width_o=22,
            write_triggers_read=True,
        )

        tap1 = dut.add_tap(fixed_delay=150)
        tap2 = dut.add_tap(fixed_delay=220)

        def stimulus_values():
            for n in range(0, sys.maxsize):
                yield fixed.Const(0.8*math.sin(n*0.2), shape=ASQ)

        async def stimulus_i(ctx):
            """Send `stimulus_values` to the DUT."""
            s = stimulus_values()
            while True:
                await ctx.tick().until(dut.i.ready)
                ctx.set(dut.i.valid, 1)
                ctx.set(dut.i.payload, next(s))
                await ctx.tick()
                ctx.set(dut.i.valid, 0)

        def validate_tap(tap):
            """Verify tap outputs exactly match a delayed stimulus."""
            async def _validate_tap(ctx):
                s = stimulus_values()
                n_samples_o = 0
                ctx.set(tap.o.ready, 1)
                while True:
                    # there must be a more idiomatic way to write this kind of stuff..
                    async for _, _, valid, payload in ctx.tick().sample(
                            tap.o.valid, tap.o.payload):
                        if valid:
                            if n_samples_o >= tap.fixed_delay:
                                self.assertEqual(payload.__repr__(),
                                                 next(s).__repr__())
                            else:
                                self.assertEqual(payload.__repr__(),
                                                 fixed.Const(0, shape=ASQ).__repr__())
                            n_samples_o += 1
            return _validate_tap

        async def psram_testbench(ctx):
            """Simulate DelayLine against a fake PSRAM bus."""
            mem = [0] * dut.max_delay
            membus = dut.bus
            for _ in range(100):
                while not ctx.get(membus.stb):
                    await ctx.tick()
                adr = adr_start = ctx.get(membus.adr)
                # Simulate ACKs delayed from stb (like real memory)
                await ctx.tick().repeat(8)
                # warn: only whole-word transactions are simulated
                if ctx.get(membus.we):
                    while ctx.get(membus.cti == wishbone.CycleType.INCR_BURST):
                        mem[adr] = ctx.get(membus.dat_w)
                        print("write", hex(mem[adr]), "@", adr)
                        await ctx.tick()
                        ctx.set(membus.ack, 1)
                        adr += 1
                    await ctx.tick()
                else:
                    ctx.set(membus.ack, 1)
                    while ctx.get(membus.stb):
                        print("read", hex(mem[adr]), "@", adr)
                        ctx.set(membus.dat_r, mem[adr])
                        await ctx.tick()
                        adr += 1
                assert adr - adr_start == dut._cache.burst_len
                ctx.set(membus.ack, 0)
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_process(stimulus_i)
        sim.add_process(validate_tap(tap1))
        sim.add_process(validate_tap(tap2))
        sim.add_testbench(psram_testbench)
        with sim.write_vcd(vcd_file=open("test_psram_delayln.vcd", "w")):
            sim.run()

    def test_sram_delayln(self):

        dut = delay_line.DelayLine(
            max_delay=256,
            write_triggers_read=False,
        )

        tap1 = dut.add_tap()
        tap2 = dut.add_tap()

        async def stimulus_wr(ctx):
            for n in range(0, sys.maxsize):
                ctx.set(dut.i.valid, 1)
                ctx.set(dut.i.payload,
                        fixed.Const(0.8*math.sin(n*0.2), shape=ASQ))
                await ctx.tick()
                ctx.set(dut.i.valid, 0)
                await ctx.tick().repeat(30)

        async def stimulus_rd1(ctx):
            ctx.set(tap1.o.ready, 1)
            for n in range(0, sys.maxsize):
                ctx.set(tap1.i.valid, 1)
                ctx.set(tap1.i.payload, 4)
                await ctx.tick()
                ctx.set(tap1.i.valid, 0)
                await ctx.tick().repeat(30)

        async def stimulus_rd2(ctx):
            ctx.set(tap2.o.ready, 1)
            for n in range(0, sys.maxsize):
                ctx.set(tap2.i.valid, 1)
                ctx.set(tap2.i.payload, 10)
                await ctx.tick()
                ctx.set(tap2.i.valid, 0)
                await ctx.tick().repeat(30)

        async def testbench(ctx):
            n_rd1 = 0
            n_rd2 = 0
            for n in range(200):
                await ctx.tick()
                if ctx.get(tap1.o.valid) and ctx.get(tap1.o.ready):
                    n_rd1 += 1
                if ctx.get(tap2.o.valid) and ctx.get(tap2.o.ready):
                    n_rd2 += 1
            # both taps produced some output samples
            assert n_rd1 > 5
            assert n_rd2 > 5

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        sim.add_process(stimulus_wr)
        sim.add_process(stimulus_rd1)
        sim.add_process(stimulus_rd2)
        with sim.write_vcd(vcd_file=open("test_sram_delayln.vcd", "w")):
            sim.run()
