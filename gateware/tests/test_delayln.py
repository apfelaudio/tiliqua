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
from tiliqua               import dsp, eurorack_pmod, cache
from tiliqua.eurorack_pmod import ASQ

from amaranth_soc          import csr
from amaranth_soc          import wishbone

from amaranth_future       import fixed

class WishboneAdapter(wiring.Component):
    def __init__(self, addr_width_i, addr_width_o, base):
        self.base = base
        super().__init__({
            "i": In(wishbone.Signature(addr_width=addr_width_i,
                                       data_width=16,
                                       granularity=8)),
            "o": Out(wishbone.Signature(addr_width=addr_width_o,
                                        data_width=32,
                                        granularity=8,
                                        features={'bte', 'cti'})),
        })

    def elaborate(self, platform):
        m = Module()

        m.d.comb += [
            self.i.ack.eq(self.o.ack),
            self.o.adr.eq((self.base<<2) + (self.i.adr>>1)),
            self.o.we.eq(self.i.we),
            self.o.cyc.eq(self.i.cyc),
            self.o.stb.eq(self.i.stb),
        ]

        with m.If(self.i.adr[0]):
            m.d.comb += [
                self.i.dat_r.eq(self.o.dat_r>>16),
                self.o.sel  .eq(self.i.sel<<2),
                self.o.dat_w.eq(self.i.dat_w<<16),
            ]
        with m.Else():
            m.d.comb += [
                self.i.dat_r.eq(self.o.dat_r),
                self.o.sel  .eq(self.i.sel),
                self.o.dat_w.eq(self.i.dat_w),
            ]

        return m

class DelayLineTests(unittest.TestCase):

    def test_persist(self):

        m = Module()

        l2c = cache.WishboneL2Cache(cachesize_words=4)

        dut = dsp.DelayLineWriter(
            max_delay=16
        )

        tap1 = dut.add_tap()
        tap2 = dut.add_tap()

        adapter = WishboneAdapter(addr_width_i=dut.bus.addr_width,
                                  addr_width_o=l2c.master.addr_width,
                                  base=0x0)

        wiring.connect(m, dut.bus, adapter.i)
        wiring.connect(m, adapter.o, l2c.master)

        m.submodules += [l2c, dut, adapter]

        async def stimulus_wr(ctx):
            for n in range(0, sys.maxsize):
                ctx.set(dut.sw.valid, 1)
                ctx.set(dut.sw.payload,
                        fixed.Const(0.8*math.sin(n*0.2), shape=ASQ))
                await ctx.tick()
                ctx.set(dut.sw.valid, 0)
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
            # Simulate some acks
            mem = [0] * 16
            membus = l2c.slave
            for _ in range(200):
                while not ctx.get(membus.stb):
                    await ctx.tick()
                # Simulate acks delayed from stb
                await ctx.tick().repeat(2)
                ctx.set(membus.ack, 1)
                adr = ctx.get(membus.adr)
                if ctx.get(membus.we):
                    if ctx.get(membus.sel == 0b0011):
                        mem[adr] = mem[adr] & 0xFFFF0000
                        mem[adr] |= ctx.get(membus.dat_w & 0xFFFF)
                    elif ctx.get(membus.sel == 0b1100):
                        mem[adr] = mem[adr] & 0x0000FFFF
                        mem[adr] |= ctx.get(membus.dat_w & 0xFFFF0000)
                    else:
                        mem[adr] = ctx.get(membus.dat_w)
                    print("write", hex(mem[adr]), "@", adr)
                else:
                    print("read", hex(mem[adr]), "@", adr)
                    ctx.set(membus.dat_r, mem[ctx.get(membus.adr)])
                await ctx.tick()
                ctx.set(membus.ack, 0)
                await ctx.tick()

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        sim.add_process(stimulus_wr)
        sim.add_process(stimulus_rd1)
        sim.add_process(stimulus_rd2)
        with sim.write_vcd(vcd_file=open("test_delayln.vcd", "w")):
            sim.run()
