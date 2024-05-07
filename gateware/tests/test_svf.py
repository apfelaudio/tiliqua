import unittest

import math

from amaranth.sim import *
from amaranth_future import fixed
from tiliqua.eurorack_pmod import ASQ

from example_dsp.top import SVF

class SVFTests(unittest.TestCase):

    def test_svf(self):

        svf = SVF()

        def testbench():
            for n in range(0, 100):
                x = fixed.Const(0.4*(math.sin(n*0.2) + math.sin(n)), shape=ASQ)
                yield svf.i.payload[0].eq(x)
                yield svf.i.payload[1].eq(fixed.Const(0.3, shape=ASQ))
                yield svf.i.payload[2].eq(fixed.Const(0.1, shape=ASQ))
                yield svf.i.valid.eq(1)
                yield Tick()
                yield svf.i.valid.eq(0)
                yield Tick()
                yield Tick()
                yield Tick()
                yield Tick()
                out0 = yield(svf.o.payload[0])
                out1 = yield(svf.o.payload[1])
                out2 = yield(svf.o.payload[2])
                print(hex(out0), hex(out1), hex(out2))
                yield svf.o.ready.eq(1)
                yield Tick()
                yield Tick()

        sim = Simulator(svf)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test.vcd", "w")):
            sim.run()
