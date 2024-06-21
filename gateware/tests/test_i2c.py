import unittest

from amaranth              import *
from amaranth.sim          import *
from vendor                import i2c

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

        top = i2c.I2CPeripheral(pads=i2c_pads, period_cyc=4)

        def testbench():
            # initial
            yield Tick()

            # set device address
            yield top._address.w_stb               .eq(1)
            yield top._address.w_data              .eq(0x55)
            yield Tick()

            # enqueue 2x write ops
            yield top._transaction_data.w_stb      .eq(1)
            yield top._transaction_data.w_data     .eq(0x042)
            yield Tick()
            yield top._transaction_data.w_stb      .eq(1)
            yield top._transaction_data.w_data     .eq(0x013)
            yield Tick()

            # enqueue 1x read op
            yield top._transaction_data.w_stb      .eq(1)
            yield top._transaction_data.w_data     .eq(0x100)
            yield Tick()

            # stop enqueueing ops
            yield top._transaction_data.w_stb      .eq(0)
            yield top._transaction_data.w_data     .eq(0)
            yield Tick()

            # start the i2c core
            yield top._start.w_stb .eq(1)
            yield top._start.w_data.eq(1)
            yield Tick()

            # run until it's done
            for _ in range(600):
                yield Tick()
            assert (yield top._busy.r_data == 0)

            # new device address
            yield Tick()
            yield top._address.w_stb               .eq(1)
            yield top._address.w_data              .eq(0x07)

            # enqueue 1x read op
            yield top._transaction_data.w_stb      .eq(1)
            yield top._transaction_data.w_data     .eq(0x100)
            yield Tick()

            # enqueue 1x write op
            yield top._transaction_data.w_stb      .eq(1)
            yield top._transaction_data.w_data     .eq(0x022)
            yield Tick()

            # start the i2c core
            yield top._start.w_stb .eq(1)
            yield top._start.w_data.eq(1)
            yield Tick()

            # run until it's done
            for _ in range(600):
                yield Tick()
            assert (yield top._busy.r_data == 0)

        sim = Simulator(top)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test_i2c_tx.vcd", "w")):
            sim.run()
