import unittest

from amaranth              import *
from amaranth.sim          import *
from amaranth.lib          import wiring
from tiliqua               import i2c

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

        def register_ix(register_name, field_name=None):
            for (reg_object, name, (s_byte, e_byte)) in dut.bus.memory_map.resources():
                name = str(name)[6:-2]
                if name == register_name:
                    if field_name is None:
                        return s_byte, 0, (e_byte - s_byte)*8
                    offset = 0
                    for path, action in reg_object:
                        name = "_".join([str(s) for s in path])
                        width = action.port.shape.width
                        if name == field_name:
                            return s_byte, offset, width
                        offset += width
            raise ValueError(f"{register_name} {field_name} does not exist in memory map.")

        async def wb_transaction(ctx, adr, we, sel, dat_w=None):
            ctx.set(bridge.wb_bus.cyc, 1)
            ctx.set(bridge.wb_bus.sel, sel)
            ctx.set(bridge.wb_bus.we,  we)
            ctx.set(bridge.wb_bus.adr, adr)
            ctx.set(bridge.wb_bus.stb, 1)
            if we:
                ctx.set(bridge.wb_bus.dat_w, dat_w)
            await ctx.tick().repeat(5)
            self.assertEqual(ctx.get(bridge.wb_bus.ack), 1)
            value = ctx.get(bridge.wb_bus.dat_r) if not we else None
            ctx.set(bridge.wb_bus.stb, 0)
            await ctx.tick()
            self.assertEqual(ctx.get(bridge.wb_bus.ack), 0)
            return value

        async def wb_csr_w(ctx, value, register_name, field_name=None):
            s_bytes, s_bits, w_bits = register_ix(register_name, field_name)

            # constrain ix_bits in 0..7, add rest to s_bytes
            s_bytes += s_bits // 8
            ix_bits  = s_bits % 8
            ix_bytes = s_bytes % 4
            w_bytes  = w_bits // 8
            if w_bits % 8 != 0:
                w_bytes += 1

            # compute bus access
            adr = s_bytes // 4
            dat_w = (value << ix_bytes) << ix_bits
            sel = int('1'*w_bytes, base=2) << ix_bytes

            return await wb_transaction(ctx, adr, 1, sel, dat_w)

        async def wb_csr_r(ctx, register_name, field_name=None):
            s_bytes, s_bits, w_bits = register_ix(register_name, field_name)

            # constrain ix_bits in 0..7, add rest to s_bytes
            s_bytes += s_bits // 8
            ix_bits  = s_bits % 8
            ix_bytes = s_bytes % 4

            # compute bus access
            adr = s_bytes // 4
            sel = 0b1111

            value_32b = await wb_transaction(ctx, adr, 0, sel)
            return ((value_32b >> ix_bytes) >> ix_bits) & int('1'*w_bits, base=2)

        async def testbench(ctx):
            # set device address
            await wb_csr_w(ctx,  0x55, "address")

            # enqueue 2x write ops
            await wb_csr_w(ctx, 0x042, "transaction_reg")
            await wb_csr_w(ctx, 0x013, "transaction_reg")

            # enqueue 1x read op
            await wb_csr_w(ctx, 0x100, "transaction_reg")

            # 3 transactions are enqueued
            self.assertEqual(ctx.get(dut._transactions.level), 3)

            # start the i2c core
            await wb_csr_w(ctx,     1, "start")

            # busy flag should go high
            self.assertEqual(await wb_csr_r(ctx, "status", "busy"), 1)

            # run for a while
            await ctx.tick().repeat(500)

            # busy flag should be low
            self.assertEqual(await wb_csr_r(ctx, "status", "busy"), 0)

            # all transactions drained.
            self.assertEqual(ctx.get(dut._transactions.level), 0)

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_i2c_tx.vcd", "w")):
            sim.run()
