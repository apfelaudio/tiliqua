# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import math
import sys
import unittest

from parameterized import parameterized

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

    @parameterized.expand([
        ["b4_c64_lr_short_taps", 64,  4, True,  256, 1,   3],
        ["b4_c16_lr_long_taps",  16,  4, True,  256, 150, 220],
        ["b4_c64_lr_long_taps",  64,  4, True,  256, 150, 220],
        ["b4_c256_lr_long_taps", 256, 4, True,  256, 150, 220],
        ["b4_c64_lr_endpoints1", 64,  4, True,  256, 0,   255],
        ["b4_c64_lr_endpoints2", 64,  4, True,  256, 255, 0],
        ["b8_c64_lr_long_taps",  64,  8, True,  256, 150, 220],
        ["b4_c64_dp_short_taps", 64,  4, False, 256, 1,   3],
        ["b4_c64_dp_long_taps",  64,  4, False, 256, 150, 220],
        ["b8_c64_dp_long_taps",  64,  8, False, 256, 150, 220],
    ])
    def test_psram_delayln(self, name, cachesize_words, cache_burst_len, cache_lutram_backed,
                           max_delay, tap1_delay, tap2_delay):

        cache_kwargs = {
            "lutram_backed":   cache_lutram_backed,
            "burst_len":       cache_burst_len,
            "cachesize_words": cachesize_words,
        }

        dut = delay_line.DelayLine(
            max_delay=max_delay,
            psram_backed=True,
            base=0x0,
            addr_width_o=22,
            write_triggers_read=True,
            cache_kwargs=cache_kwargs,
        )

        tap1 = dut.add_tap(fixed_delay=tap1_delay)
        tap2 = dut.add_tap(fixed_delay=tap2_delay)

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
                    await ctx.tick().until(tap.o.valid)
                    expected_payload = next(s) if n_samples_o >= tap.fixed_delay else fixed.Const(0, shape=ASQ)
                    assert ctx.get(tap.o.payload == expected_payload)
                    n_samples_o += 1
            return _validate_tap

        async def psram_simulation(ctx):
            """Simulate a fake PSRAM bus."""
            mem = [0] * dut.max_delay
            membus = dut.bus
            # Respond to memory transactions forever
            while True:
                await ctx.tick().until(membus.stb)
                adr = adr_start = ctx.get(membus.adr)
                # Simulate ACKs delayed from stb (like real memory)
                await ctx.tick().repeat(8)
                # warn: only whole-word transactions are simulated
                if ctx.get(membus.we):
                    while ctx.get(membus.cti == wishbone.CycleType.INCR_BURST):
                        mem[adr] = ctx.get(membus.dat_w)
                        await ctx.tick()
                        ctx.set(membus.ack, 1)
                        adr += 1
                    await ctx.tick()
                else:
                    ctx.set(membus.ack, 1)
                    while ctx.get(membus.stb):
                        ctx.set(membus.dat_r, mem[adr])
                        await ctx.tick()
                        adr += 1
                assert adr - adr_start == dut._cache.burst_len
                ctx.set(membus.ack, 0)
                await ctx.tick()

        async def testbench(ctx):
            """Top-level testbench."""

            n_samples_in    = 0
            n_samples_tap1  = 0
            n_samples_tap2  = 0

            n_write_bursts  = 0
            n_read_bursts   = 0

            for _ in range(max_delay*40):
                n_samples_in    += ctx.get(dut.i.valid & dut.i.ready)
                n_samples_tap1  += ctx.get(tap1.o.valid & tap1.o.ready)
                n_samples_tap2  += ctx.get(tap2.o.valid & tap2.o.ready)
                n_write_bursts  += ctx.get(dut.bus.we  & (dut.bus.cti == wishbone.CycleType.END_OF_BURST))
                n_read_bursts   += ctx.get(~dut.bus.we & (dut.bus.cti == wishbone.CycleType.END_OF_BURST))
                await ctx.tick()

            print()
            print("n_samples_in",   n_samples_in)
            print("n_samples_tap1", n_samples_tap1)
            print("n_samples_tap2", n_samples_tap2)
            print("n_write_bursts", n_write_bursts)
            print("n_read_bursts",  n_read_bursts)

            samples_per_burst = (n_samples_in + n_samples_tap1 + n_samples_tap2) / (n_write_bursts + n_read_bursts)

            print("samples_per_burst", samples_per_burst)

            assert n_samples_in > 100
            assert abs(n_samples_in - n_samples_tap1) < 2
            assert abs(n_samples_in - n_samples_tap2) < 2

            if cachesize_words > 64:
                # arbitrarily chosen based on current cache performance
                assert samples_per_burst > 2 * cache_burst_len

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_process(stimulus_i)
        sim.add_testbench(validate_tap(tap1), background=True)
        sim.add_testbench(validate_tap(tap2), background=True)
        sim.add_testbench(psram_simulation,   background=True)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open(f"test_psram_delayln_{name}.vcd", "w")):
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
