import unittest

import math

from amaranth              import *
from amaranth.sim          import *
from amaranth_future       import fixed
from tiliqua.eurorack_pmod import ASQ

from tiliqua import dsp

class DSPTests(unittest.TestCase):

    def test_delayline(self):

        delay_line = dsp.DelayLine()

        def testbench():
            yield Tick()
            yield Tick()
            for n in range(0, 50):
                x = fixed.Const(0.8*math.sin(n*0.2), shape=ASQ)
                yield delay_line.sw.valid.eq(1)
                yield delay_line.sw.payload.eq(x)
                yield Tick()
                yield delay_line.sw.valid.eq(0)
                yield Tick()
                yield Tick()
            yield Tick()
            for n in range(0, 10):
                yield delay_line.da.payload.eq(n)
                yield delay_line.ds.ready.eq(1)
                yield delay_line.da.valid.eq(1)
                yield Tick()
                yield delay_line.da.valid.eq(0)
                yield Tick()

        sim = Simulator(delay_line)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test_delayline.vcd", "w")):
            sim.run()

    def test_pitch(self):

        m = Module()
        delay_line = dsp.DelayLine(max_delay=256)
        pitch_shift = dsp.PitchShift(delayln=delay_line, xfade=32)
        m.submodules += [delay_line, pitch_shift]

        def testbench():
            yield Tick()
            yield Tick()
            for n in range(0, 1000):
                x = fixed.Const(0.8*math.sin(n*0.1), shape=ASQ)
                yield delay_line.sw.valid.eq(1)
                yield delay_line.sw.payload.eq(x)
                yield Tick()
                yield delay_line.sw.valid.eq(0)
                yield Tick()
                yield Tick()
                yield pitch_shift.i.payload.pitch.eq(
                    fixed.Const(-0.8, shape=pitch_shift.dtype))
                yield pitch_shift.i.payload.grain_sz.eq(
                    delay_line.max_delay//2)
                yield pitch_shift.o.ready.eq(1)
                yield pitch_shift.i.valid.eq(1)
                yield Tick()
                yield pitch_shift.i.valid.eq(0)
                yield Tick()
                while (yield pitch_shift.i.ready) != 1:
                    yield Tick()

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test_pitch.vcd", "w")):
            sim.run()


    def test_svf(self):

        svf = dsp.SVF()

        def testbench():
            for n in range(0, 100):
                x = fixed.Const(0.4*(math.sin(n*0.2) + math.sin(n)), shape=ASQ)
                yield svf.i.payload.x.eq(x)
                yield svf.i.payload.cutoff.eq(fixed.Const(0.3, shape=ASQ))
                yield svf.i.payload.resonance.eq(fixed.Const(0.1, shape=ASQ))
                yield svf.i.valid.eq(1)
                yield Tick()
                yield svf.i.valid.eq(0)
                yield Tick()
                yield Tick()
                yield Tick()
                yield Tick()
                yield Tick()
                yield Tick()
                yield Tick()
                yield Tick()
                out0 = yield(svf.o.payload.hp)
                out1 = yield(svf.o.payload.lp)
                out2 = yield(svf.o.payload.bp)
                print(hex(out0), hex(out1), hex(out2))
                yield Tick()
                yield svf.o.ready.eq(1)
                yield Tick()
                yield svf.o.ready.eq(0)
                yield Tick()

        sim = Simulator(svf)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test_svf.vcd", "w")):
            sim.run()
