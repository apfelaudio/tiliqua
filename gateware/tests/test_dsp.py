import unittest

import math

from amaranth              import *
from amaranth.sim          import *
from amaranth_future       import fixed
from amaranth.lib          import wiring, data
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

    def test_matrix(self):

        matrix = dsp.MatrixMix(
            i_channels=4, o_channels=4,
            coefficients=[[1, 0, 0, 0],
                          [0, 1, 0, 0],
                          [0, 0, 1, 0],
                          [0, 0, 0, 1]])

        def testbench():
            yield matrix.i.payload[0].eq(fixed.Const(0.2, shape=ASQ))
            yield matrix.i.payload[1].eq(fixed.Const(0.4,  shape=ASQ))
            yield matrix.i.payload[2].eq(fixed.Const(0.6,  shape=ASQ))
            yield matrix.i.payload[3].eq(fixed.Const(0.8,  shape=ASQ))
            yield matrix.i.valid.eq(1)
            yield Tick()
            yield matrix.i.valid.eq(0)
            yield Tick()
            yield matrix.o.ready.eq(1)
            while (yield matrix.o.valid) != 1:
                yield Tick()
            for n in range(matrix.o_channels):
                p = (yield matrix.o.payload[n])
                c = fixed.Const(0, shape=ASQ)
                c._value = p
                print(c.as_float())

        sim = Simulator(matrix)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test_matrix.vcd", "w")):
            sim.run()

    def test_fixed(self):

        d = fixed.Const(4000, shape=fixed.SQ(2, 4))
        e = fixed.Const(4000, shape=fixed.UQ(2, 4))
        d = fixed.Const(-4000, shape=fixed.SQ(2, 4))
        e = fixed.Const(-4000, shape=fixed.UQ(2, 4))
        print(d, e)

        print(fixed.SQ(2, 4).max())
        print(fixed.SQ(2, 4).min())
        print(ASQ.max())
        print(ASQ.min())

    def test_waveshaper(self):

        def scaled_tanh(x):
            return math.tanh(3.0*x)

        waveshaper = dsp.WaveShaper(lut_function=scaled_tanh, lut_size=16)

        def testbench():
            yield Tick()
            for n in range(0, 100):
                x = fixed.Const(math.sin(n*0.10), shape=ASQ)
                yield waveshaper.i.payload.eq(x)
                yield waveshaper.i.valid.eq(1)
                yield waveshaper.o.ready.eq(1)
                yield Tick()
                yield waveshaper.i.valid.eq(0)
                while (yield waveshaper.o.valid) != 1:
                    yield Tick()

        sim = Simulator(waveshaper)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test_waveshaper.vcd", "w")):
            sim.run()

    def test_gainvca(self):

        def scaled_tanh(x):
            return math.tanh(3.0*x)

        m = Module()
        vca = dsp.GainVCA()
        waveshaper = dsp.WaveShaper(lut_function=scaled_tanh)

        m.submodules += [vca, waveshaper]

        m.d.sync += [
            waveshaper.i.payload.eq(vca.o.payload),
        ]

        def testbench():
            yield Tick()
            for n in range(0, 100):
                x = fixed.Const(0.8*math.sin(n*0.3), shape=ASQ)
                gain = fixed.Const(3.0*math.sin(n*0.1), shape=fixed.SQ(2, ASQ.f_width))
                yield vca.i.payload.x.eq(x)
                yield vca.i.payload.gain.eq(gain)
                yield vca.i.valid.eq(1)
                yield Tick()

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test_gainvca.vcd", "w")):
            sim.run()

    def test_nco(self):

        m = Module()

        def sine_osc(x):
            return math.sin(math.pi*x)

        nco = dsp.SawNCO()
        waveshaper = dsp.WaveShaper(lut_function=sine_osc, lut_size=128,
                                    continuous=True)

        m.submodules += [nco, waveshaper]

        wiring.connect(m, nco.o, waveshaper.i)

        def testbench():
            yield waveshaper.o.ready.eq(1)
            yield Tick()
            for n in range(0, 400):
                phase = fixed.Const(0.1*math.sin(n*0.10), shape=ASQ)
                yield nco.i.payload.freq_inc.eq(0.66)
                yield nco.i.payload.phase.eq(phase)
                yield nco.i.valid.eq(1)
                yield Tick()
                yield nco.i.valid.eq(0)
                yield Tick()
                while (yield waveshaper.o.valid) != 1:
                    yield Tick()

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test_nco.vcd", "w")):
            sim.run()

    def test_fir(self):

        fir = dsp.FIR(fs=48000, filter_cutoff_hz=1000,
                      filter_order=24)

        def testbench():
            for n in range(0, 100):
                x = fixed.Const(0.4*(math.sin(n*0.2) + math.sin(n)), shape=ASQ)
                yield fir.i.payload.eq(x)
                yield fir.i.valid.eq(1)
                yield Tick()
                yield fir.i.valid.eq(0)
                yield Tick()
                while (yield fir.o.valid) != 1:
                    yield Tick()
                out0 = yield(fir.o.payload)
                yield fir.o.ready.eq(1)
                yield Tick()
                yield fir.o.ready.eq(0)
                yield Tick()

        sim = Simulator(fir)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test_fir.vcd", "w")):
            sim.run()

    def test_resample(self):

        resample = dsp.Resample(fs_in=48000, n_up=2, m_down=1)

        def testbench():
            for n in range(0, 100):
                x = fixed.Const(0.4*(math.sin(n*0.2) + math.sin(n)), shape=ASQ)
                yield resample.i.payload.eq(x)
                yield resample.i.valid.eq(1)
                yield Tick()
                yield resample.i.valid.eq(0)
                yield Tick()
                for _ in range(2):
                    while (yield resample.o.valid) != 1:
                        yield Tick()
                    yield resample.o.ready.eq(1)
                    yield Tick()
                    yield resample.o.ready.eq(0)
                    yield Tick()

        sim = Simulator(resample)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test_resample.vcd", "w")):
            sim.run()
