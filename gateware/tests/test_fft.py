import unittest

from math import cos, sin, pi

from amaranth              import *
from amaranth.sim          import *
from amaranth.utils        import log2_int

from amaranth_future       import fixed

from tiliqua.eurorack_pmod import ASQ
from tiliqua               import dsp

from vendor.fixedpointfft  import FixedPointFFT

class FFTTests(unittest.TestCase):

    def test_fft(self):

        fft = FixedPointFFT(bitwidth=18, pts=256)

        def testbench():
            dut = fft
            PTS = 256
            LPTS = log2_int(PTS)

            I =[int(cos(2*16*pi*i/PTS) * (2**16-1)) for i in range(PTS)]
            Q =[int(sin(2*16*pi*i/PTS) * (2**16-1)) for i in range(PTS)]

            # Loading window function
            # Rectangular
            WR =[(2**17-1) for i in range(PTS)]
            # Flat top 1-1.93*cos(2*pi*i/PTS)+1.29*cos(4*pi*i/PTS)-0.388*cos(6*pi*i/PTS)+0.032*cos(8*pi*i/PTS)
            #WR =[int((1-1.93*cos(2*pi*i/PTS)+1.29*cos(4*pi*i/PTS)-0.388*cos(6*pi*i/PTS)+0.032*cos(8*pi*i/PTS))*(2**17-1)) for i in range(PTS)]
            # Blackman Nuttall
            #WR =[int((0.3635819-0.4891775*cos(k*2*pi/PTS)+0.1365995*cos(k*4*pi/PTS)-0.0106411*cos(k*6*pi/PTS))*(2**17-1)) for k in range(PTS)]
            WI =[0 for i in range(PTS)]

            yield Tick()
            yield dut.wf_start.eq(1)
            yield Tick()
            yield dut.wf_start.eq(0)
            yield Tick()
            yield Tick()
            yield Tick()

            for i in range(PTS):
                yield dut.wf_real.eq(WR[i])
                yield dut.wf_imag.eq(WI[i])
                yield Tick()
                yield dut.wf_strobe.eq(1)
                yield Tick()
                yield dut.wf_strobe.eq(0)
                yield Tick()
                yield Tick()
                yield Tick()

            # Waiting done
            for _ in range(16):
                yield Tick()

            # FFT
            yield Tick()
            yield dut.start.eq(1)
            yield Tick()
            yield dut.start.eq(0)
            yield Tick()
            yield Tick()
            yield Tick()

            for i in range(PTS):
                yield dut.in_i.eq(I[i])
                yield dut.in_q.eq(Q[i])
                yield Tick()
                yield dut.strobe_in.eq(1)
                yield Tick()
                yield dut.strobe_in.eq(0)
                yield Tick()
                yield Tick()
                yield Tick()

            # Looks that it will take ~13 cycles for read-butterfly-write
            for _ in range(PTS*LPTS*13):
                yield Tick()

        sim = Simulator(fft)
        sim.add_clock(1e-6)
        sim.add_process(testbench)
        with sim.write_vcd(vcd_file=open("test_fft.vcd", "w")):
            sim.run()

