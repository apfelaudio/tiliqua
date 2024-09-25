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
            async def read_fill(adr, with_writeback=False):
                await ctx.tick()
                ctx.set(dut.master.adr, adr)
                ctx.set(dut.master.cyc, 1)
                ctx.set(dut.master.stb, 1)
                ctx.set(dut.master.we,  0)
                ctx.set(dut.master.sel, 0b1111)
                print(f"read_fill(adr={adr}) initiate master")
                while not ctx.get(dut.slave.stb):
                    await ctx.tick()
                if with_writeback:
                    ctx.set(dut.slave.ack, 1)
                    dat = ctx.get(dut.slave.dat_w)
                    print(f"read_fill(adr={adr}) slave write {hex(dat)}")
                    await ctx.tick()
                ctx.set(dut.slave.ack,   1)
                ctx.set(dut.slave.dat_r, 0xdead0000 | adr)
                print(f"read_fill(adr={adr}) slave acks with {hex(0xdead0000 | adr)}")
                await ctx.tick()
                ctx.set(dut.slave.ack,  0)
                while not ctx.get(dut.master.ack):
                    await ctx.tick()
                master_reads = ctx.get(dut.master.dat_r)
                print(f"read_fill(adr={adr}) master reads {hex(master_reads)}")
                ctx.set(dut.master.cyc, 0)
                ctx.set(dut.master.stb, 0)
                await ctx.tick().repeat(10)

            async def write_assume_present(adr):
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

            async def read_assume_present(adr):
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
                await ctx.tick()
                ctx.set(dut.master.cyc, 0)
                ctx.set(dut.master.stb, 0)
                await ctx.tick().repeat(10)

            for adr in range(0, 5):
                await read_fill(adr)

            for adr in range(0, 5):
                await write_assume_present(adr)

            #for adr in range(0, 5):
            #    await read_assume_present(adr)

            for adr in range(512, 517):
                await read_fill(adr, with_writeback=True)

            for adr in range(512, 517):
                await read_assume_present(adr)


        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_cache.vcd", "w")):
            sim.run()
