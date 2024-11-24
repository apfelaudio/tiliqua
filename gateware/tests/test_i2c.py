# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import unittest

from amaranth              import *
from amaranth.sim          import *
from amaranth.lib          import wiring, data
from amaranth.lib.memory   import Memory
from tiliqua               import i2c, test_util, eurorack_pmod
from vendor                import i2c as vendor_i2c

from amaranth_soc          import csr
from amaranth_soc.csr      import wishbone

class I2CTests(unittest.TestCase):

    def test_i2c_peripheral(self):

        m = Module()
        dut = i2c.Peripheral(period_cyc=4)
        decoder = csr.Decoder(addr_width=28, data_width=8)
        decoder.add(dut.bus, addr=0, name="dut")
        bridge = wishbone.WishboneCSRBridge(decoder.bus, data_width=32)
        m.submodules += [dut, decoder, bridge]

        async def test_stimulus(ctx):

            async def csr_write(ctx, value, register, field=None):
                await test_util.wb_csr_w(
                        ctx, dut.bus, bridge.wb_bus, value, register, field)

            async def csr_read(ctx, register, field=None):
                return await test_util.wb_csr_r(
                        ctx, dut.bus, bridge.wb_bus, register, field)

            # set device address
            await csr_write(ctx, 0x55, "address")

            # enqueue 2x write ops
            await csr_write(ctx, 0x042, "transaction_reg")
            await csr_write(ctx, 0x013, "transaction_reg")

            # enqueue 1x read + last op
            await csr_write(ctx, 0x300, "transaction_reg")

            # 3 transactions are enqueued
            self.assertEqual(ctx.get(dut.i2c_stream._transactions.level), 3)

            # busy flag should go high
            self.assertEqual(await csr_read(ctx, "status", "busy"), 1)

            await ctx.tick().repeat(500)

            # busy flag should be low
            self.assertEqual(await csr_read(ctx, "status", "busy"), 0)

            # all transactions drained.
            self.assertEqual(ctx.get(dut.i2c_stream._transactions.level), 0)

        async def test_response(ctx):

            was_busy = False
            data_written = []
            while True:
                await ctx.tick()
                if ctx.get(dut.i2c_stream.status.busy) and not was_busy:
                    was_busy = True
                if was_busy and not ctx.get(dut.i2c_stream.status.busy):
                    break
                if ctx.get(dut.i2c_stream.i2c.start):
                    print("i2c.start")
                if ctx.get(dut.i2c_stream.i2c.write):
                    v = ctx.get(dut.i2c_stream.i2c.data_i)
                    print("i2c.write", hex(v))
                    data_written.append(v)
                if ctx.get(dut.i2c_stream.i2c.read):
                    print("i2c.read",  hex(ctx.get(dut.i2c_stream.i2c.data_o)))
                if ctx.get(dut.i2c_stream.i2c.stop):
                    print("i2c.stop")

            self.assertEqual(data_written, [0xaa, 0x42, 0x13, 0xab])

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(test_stimulus)
        sim.add_testbench(test_response, background=True)
        with sim.write_vcd(vcd_file=open("test_i2c_peripheral.vcd", "w")):
            sim.run()

    def test_i2c_master(self):

        m = Module()
        dut = eurorack_pmod.I2CMaster(audio_192=False)
        m.submodules += [dut]

        TICKS = 20000

        async def test_response(ctx):
            was_busy = False
            data_written = []
            ctx.set(dut.led[0], -10)
            ctx.set(dut.led[1], 10)
            for _ in range(TICKS):
                await ctx.tick()
                if ctx.get(dut.i2c_stream.i2c.start):
                    print("i2c.start")
                if ctx.get(dut.i2c_stream.i2c.write):
                    v = ctx.get(dut.i2c_stream.i2c.data_i)
                    print("i2c.write", hex(v))
                    data_written.append(v)
                if ctx.get(dut.i2c_stream.i2c.read):
                    print("i2c.read",  hex(ctx.get(dut.i2c_stream.i2c.data_o)))
                if ctx.get(dut.i2c_stream.i2c.stop):
                    print("i2c.stop")

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(test_response)
        with sim.write_vcd(vcd_file=open("test_i2c_peripheral.vcd", "w")):
            sim.run()

    def test_i2c_luna_register_interface(self):

        m = Module()
        dut = vendor_i2c.I2CRegisterInterface(period_cyc=4, max_data_bytes=16)
        m.submodules += [dut]

        async def testbench(ctx):
            ctx.set(dut.dev_address,   0x5)
            ctx.set(dut.reg_address,   0x42)
            ctx.set(dut.size,          4)
            ctx.set(dut.write_request, 1)
            ctx.set(dut.write_data[-32:], 0xDEADBEEF)
            await ctx.tick()
            ctx.set(dut.write_request, 0)
            data_written = []
            print()
            while ctx.get(dut.busy):
                if ctx.get(dut.i2c.start):
                    print("i2c.start")
                if ctx.get(dut.i2c.write):
                    v = ctx.get(dut.i2c.data_i)
                    print("i2c.write", hex(v))
                    data_written.append(v)
                if ctx.get(dut.i2c.read):
                    print("i2c.read",  hex(ctx.get(dut.i2c.data_o)))
                if ctx.get(dut.i2c.stop):
                    print("i2c.stop")
                await ctx.tick()

            self.assertEqual(data_written, [0xa, 0x42, 0xde, 0xad, 0xbe, 0xef])

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_i2c_luna_register_interface.vcd", "w")):
            sim.run()
