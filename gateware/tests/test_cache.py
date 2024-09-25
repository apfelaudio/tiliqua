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

        # test values used in test

        def memory_read_value(adr):
            return 0xdead0000 | adr

        def memory_write_value(adr):
            return 0xfeed0000 | adr

        async def read_fill(ctx, adr, with_writeback=False):
            """simulate reads from cache where refills are required."""
            await ctx.tick()
            ctx.set(dut.master.adr, adr)
            ctx.set(dut.master.cyc, 1)
            ctx.set(dut.master.stb, 1)
            ctx.set(dut.master.we,  0)
            ctx.set(dut.master.sel, 0b1111)
            print(f"read_fill(adr={adr}) initiate master")
            while not ctx.get(dut.slave.stb):
                await ctx.tick()
            # This is needed if we read from a cache line that will
            # writeback before the refill i.e. it was occupied.
            if with_writeback:
                ctx.set(dut.slave.ack, 1)
                dat = ctx.get(dut.slave.dat_w)
                print(f"read_fill(adr={adr}) slave write {hex(dat)}")
                await ctx.tick()
            ctx.set(dut.slave.ack,   1)
            slave_reads = memory_read_value(adr)
            ctx.set(dut.slave.dat_r, slave_reads)
            print(f"read_fill(adr={adr}) slave acks with {hex(slave_reads)}")
            await ctx.tick()
            ctx.set(dut.slave.ack,  0)
            while not ctx.get(dut.master.ack):
                await ctx.tick()
            master_reads = ctx.get(dut.master.dat_r)
            print(f"read_fill(adr={adr}) master reads {hex(master_reads)}")
            # cache miss: master should read whatever the slave just read.
            self.assertEqual(master_reads, slave_reads)
            ctx.set(dut.master.cyc, 0)
            ctx.set(dut.master.stb, 0)
            await ctx.tick().repeat(10)

        async def write_assume_present(ctx, adr):
            """simulate writes to cache where the lines are already in the cache."""
            await ctx.tick()
            ctx.set(dut.master.adr,   adr)
            ctx.set(dut.master.cyc,   1)
            ctx.set(dut.master.stb,   1)
            ctx.set(dut.master.we,    1)
            ctx.set(dut.master.sel,   0b1111)
            ctx.set(dut.master.dat_w, memory_write_value(adr))
            print(f"write_assume_present(adr={adr}) master writes {hex(0xfeed0000 | adr)}")
            while not ctx.get(dut.master.ack):
                await ctx.tick()
            await ctx.tick()
            ctx.set(dut.master.cyc, 0)
            ctx.set(dut.master.stb, 0)
            await ctx.tick().repeat(10)

        async def read_assume_present(ctx, adr, after_read=True):
            """simulate reads from cache where the lines are already in the cache."""
            await ctx.tick()
            ctx.set(dut.master.adr,   adr)
            ctx.set(dut.master.cyc,   1)
            ctx.set(dut.master.stb,   1)
            ctx.set(dut.master.we,    0)
            ctx.set(dut.master.sel,   0b1111)
            while not ctx.get(dut.master.ack):
                await ctx.tick()
            rdat = ctx.get(dut.master.dat_r)
            print(f"read_assume_present(adr={adr}) got {hex(rdat)}")
            if after_read:
                self.assertEqual(rdat, memory_read_value(adr))
            else:
                self.assertEqual(rdat, memory_write_value(adr))
            await ctx.tick()
            ctx.set(dut.master.cyc, 0)
            ctx.set(dut.master.stb, 0)
            await ctx.tick().repeat(10)

        async def testbench(ctx):

            # fill up some cache lines
            for adr in range(0, 5):
                await read_fill(ctx, adr)

            # verify we can write to them, without downstream memory accesses.
            for adr in range(0, 5):
                await write_assume_present(ctx, adr)

            # verify we can read the above writes, without downstream memory accesses.
            for adr in range(0, 5):
                await read_assume_present(ctx, adr, after_read=False)

            # read from some cache lines that will evict (writeback) the above cache
            # lines, and then fill the cache with new values
            for adr in range(512, 517):
                await read_fill(ctx, adr, with_writeback=True)

            # verify we can read the above lines, without downstream memory accesses.
            for adr in range(512, 517):
                await read_assume_present(ctx, adr, after_read=True)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_cache.vcd", "w")):
            sim.run()
