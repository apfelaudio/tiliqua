import unittest

from amaranth              import *
from amaranth.sim          import *
from amaranth.lib          import wiring
from tiliqua               import i2c, test_util

from amaranth_soc          import csr
from amaranth_soc.csr      import wishbone

class I2CTests(unittest.TestCase):

    def test_i2c_tx(self):

        class FakeTristate:
            i  = Signal()
            o  = Signal()
            oe = Signal()

        class FakeI2CPads:
            sda = FakeTristate()
            scl = FakeTristate()

        i2c_pads = FakeI2CPads()

        m = Module()
        dut = i2c.Peripheral(pads=i2c_pads, period_cyc=4)
        decoder = csr.Decoder(addr_width=28, data_width=8)
        decoder.add(dut.bus, addr=0, name="dut")
        bridge = wishbone.WishboneCSRBridge(decoder.bus, data_width=32)
        m.submodules += [dut, decoder, bridge]

        async def testbench(ctx):

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

            # enqueue 1x read op
            await csr_write(ctx, 0x100, "transaction_reg")

            # 3 transactions are enqueued
            self.assertEqual(ctx.get(dut._transactions.level), 3)

            # start the i2c core
            await csr_write(ctx,     1, "start")

            # busy flag should go high
            self.assertEqual(await csr_read(ctx, "status", "busy"), 1)

            # run for a while
            await ctx.tick().repeat(500)

            # busy flag should be low
            self.assertEqual(await csr_read(ctx, "status", "busy"), 0)

            # all transactions drained.
            self.assertEqual(ctx.get(dut._transactions.level), 0)

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_i2c_tx.vcd", "w")):
            sim.run()
