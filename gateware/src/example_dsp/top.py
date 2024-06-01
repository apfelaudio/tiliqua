# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD--3-Clause

import os

import math

from amaranth              import *
from amaranth.build        import *
from amaranth.lib          import wiring, data
from amaranth.lib.wiring   import In, Out


from amaranth_future       import stream, fixed

from tiliqua.tiliqua_platform import TiliquaPlatform
from tiliqua                  import eurorack_pmod, dsp
from tiliqua.eurorack_pmod    import ASQ

# for sim
from amaranth.back import verilog
from tiliqua       import sim


class MirrorTop(Elaboratable):
    """Route audio inputs straight to outputs (in the audio domain)."""

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = platform.clock_domain_generator()

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)

        wiring.connect(m, audio_stream.istream, audio_stream.ostream)

        return m

class SVFTop(Elaboratable):

    """High-, Low-, Bandpass with cutoff & resonance control."""

    def elaborate(self, platform):

        m = Module()

        m.submodules.car = platform.clock_domain_generator()

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)

        m.submodules.svf0 = svf0 = dsp.SVF()

        # connect without 'wiring.connect' so we can see the payload field names.

        m.d.comb += [
            svf0.i.valid.eq(audio_stream.istream.valid),
            audio_stream.istream.ready.eq(svf0.i.ready),

            svf0.i.payload.x.eq(audio_stream.istream.payload[0]),
            svf0.i.payload.cutoff.eq(audio_stream.istream.payload[1]),
            svf0.i.payload.resonance.eq(audio_stream.istream.payload[2]),
        ]

        m.d.comb += [
            svf0.o.ready.eq(audio_stream.ostream.ready),
            audio_stream.ostream.valid.eq(svf0.o.valid),

            audio_stream.ostream.payload[0].eq(svf0.o.payload.lp),
            audio_stream.ostream.payload[1].eq(svf0.o.payload.hp),
            audio_stream.ostream.payload[2].eq(svf0.o.payload.bp),
        ]

        return m

class VCATop(Elaboratable):

    """Audio-rate VCA."""

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = platform.clock_domain_generator()

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)

        m.submodules.split4 = split4 = dsp.Split(n_channels=4)
        m.submodules.merge4 = merge4 = dsp.Merge(n_channels=4)

        m.submodules.merge2 = merge2 = dsp.Merge(n_channels=2)

        m.submodules.vca0 = vca0 = dsp.VCA()

        # connect with 'wiring.connect' to show how this works.

        wiring.connect(m, audio_stream.istream, split4.i)

        wiring.connect(m, split4.o[0], merge2.i[0])
        wiring.connect(m, split4.o[1], merge2.i[1])
        wiring.connect(m, split4.o[2], dsp.ASQ_READY)
        wiring.connect(m, split4.o[3], dsp.ASQ_READY)

        wiring.connect(m, merge2.o, vca0.i)
        wiring.connect(m, vca0.o, merge4.i[0])

        wiring.connect(m, dsp.ASQ_VALID, merge4.i[1])
        wiring.connect(m, dsp.ASQ_VALID, merge4.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge4.i[3])
        wiring.connect(m, merge4.o, audio_stream.ostream)

        return m

class PitchTop(Elaboratable):

    """Pitch shifter with CV-controlled pitch."""

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = platform.clock_domain_generator()

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)

        m.submodules.split4 = split4 = dsp.Split(n_channels=4)
        m.submodules.merge4 = merge4 = dsp.Merge(n_channels=4)

        m.submodules.delay_line = delay_line = dsp.DelayLine(max_delay=8192)
        m.submodules.pitch_shift = pitch_shift = dsp.PitchShift(
            delayln=delay_line, xfade=delay_line.max_delay//4)

        wiring.connect(m, audio_stream.istream, split4.i)

        # write audio samples to delay line
        wiring.connect(m, split4.o[0], delay_line.sw)

        # hook up 2nd input channel as pitch control, use fixed grain_sz
        m.d.comb += [
            split4.o[1].ready.eq(pitch_shift.i.ready),
            pitch_shift.i.valid.eq(split4.o[1].valid),
            pitch_shift.i.payload.pitch.eq(split4.o[1].payload.sas_value() >> 8),
            pitch_shift.i.payload.grain_sz.eq(delay_line.max_delay//2),
        ]

        wiring.connect(m, split4.o[2], dsp.ASQ_READY)
        wiring.connect(m, split4.o[3], dsp.ASQ_READY)

        # first channel is pitch shift output
        wiring.connect(m, pitch_shift.o, merge4.i[0])
        wiring.connect(m, dsp.ASQ_VALID, merge4.i[1])
        wiring.connect(m, dsp.ASQ_VALID, merge4.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge4.i[3])

        wiring.connect(m, merge4.o, audio_stream.ostream)

        return m

class MatrixTop(Elaboratable):

    """Matrix mixer with fixed coefficients."""

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = platform.clock_domain_generator()

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)

        m.submodules.matrix_mix = matrix_mix = dsp.MatrixMix(
            i_channels=4, o_channels=4,
            coefficients=[[0.4, 0.3, 0.2, 0.1],
                          [0.1, 0.4, 0.3, 0.2],
                          [0.2, 0.1, 0.4, 0.3],
                          [0.3, 0.2, 0.1, 0.4]])

        wiring.connect(m, audio_stream.istream, matrix_mix.i)
        wiring.connect(m, matrix_mix.o, audio_stream.ostream)

        return m

class DiffuserTop(Elaboratable):

    """
    4-channel feedback delay, diffused by a matrix mixer.
    """

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = platform.clock_domain_generator()

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)

        # quadrants in the below matrix are:
        #
        # [in    -> out] [in    -> delay]
        # [delay -> out] [delay -> delay] <- feedback
        #

        m.submodules.matrix_mix = matrix_mix = dsp.MatrixMix(
            i_channels=8, o_channels=8,
            coefficients=[[0.2, 0.0, 0.0, 0.0, 0.8, 0.0, 0.0, 0.0], # in0
                          [0.0, 0.2, 0.0, 0.0, 0.0, 0.8, 0.0, 0.0], #  |
                          [0.0, 0.0, 0.2, 0.0, 0.0, 0.0, 0.8, 0.0], #  |
                          [0.0, 0.0, 0.0, 0.2, 0.0, 0.0, 0.0, 0.8], # in3
                          [0.8, 0.0, 0.0, 0.0, 0.4,-0.4,-0.4,-0.4], # ds0
                          [0.0, 0.8, 0.0, 0.0,-0.4, 0.4,-0.4,-0.4], #  |
                          [0.0, 0.0, 0.8, 0.0,-0.4,-0.4, 0.4,-0.4], #  |
                          [0.0, 0.0, 0.0, 0.8,-0.4,-0.4,-0.4, 0.4]])# ds3
                          # out0 ------- out3  sw0 ---------- sw3

        delay_lines = [
            dsp.DelayLine(max_delay=2048),
            dsp.DelayLine(max_delay=4096),
            dsp.DelayLine(max_delay=8192),
            dsp.DelayLine(max_delay=8192),
        ]
        m.submodules += delay_lines

        m.d.comb += [delay_lines[n].da.valid.eq(1) for n in range(4)]
        m.d.comb += [
            delay_lines[0].da.payload.eq(2000),
            delay_lines[1].da.payload.eq(3000),
            delay_lines[2].da.payload.eq(5000),
            delay_lines[3].da.payload.eq(7000),
        ]

        m.submodules.split4 = split4 = dsp.Split(n_channels=4)
        m.submodules.merge4 = merge4 = dsp.Merge(n_channels=4)

        m.submodules.split8 = split8 = dsp.Split(n_channels=8)
        m.submodules.merge8 = merge8 = dsp.Merge(n_channels=8)

        wiring.connect(m, audio_stream.istream, split4.i)

        # matrix <-> independent streams
        wiring.connect(m, matrix_mix.o, split8.i)
        wiring.connect(m, merge8.o, matrix_mix.i)

        for n in range(4):
            # audio -> matrix [0-3]
            wiring.connect(m, split4.o[n], merge8.i[n])
            # delay -> matrix [4-7]
            wiring.connect(m, delay_lines[n].ds, merge8.i[4+n])

        for n in range(4):
            # matrix -> audio [0-3]
            wiring.connect(m, split8.o[n], merge4.i[n])
            # matrix -> delay [4-7]
            wiring.connect(m, split8.o[4+n], delay_lines[n].sw)

        wiring.connect(m, merge4.o, audio_stream.ostream)

        return m

class WaveshaperTop(Elaboratable):

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = platform.clock_domain_generator()

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)

        m.submodules.merge4 = merge4 = dsp.Merge(n_channels=4)

        def scaled_tanh(x):
            return math.tanh(3.0*x)

        m.submodules.vca0 = vca0 = dsp.GainVCA()
        m.submodules.vca1 = vca1 = dsp.GainVCA()
        m.submodules.waveshaper0 = waveshaper0 = dsp.WaveShaper(lut_function=scaled_tanh)
        m.submodules.waveshaper1 = waveshaper1 = dsp.WaveShaper(lut_function=scaled_tanh)

        m.d.comb += [
            vca0.i.valid.eq(audio_stream.istream.valid),
            vca1.i.valid.eq(audio_stream.istream.valid),
            audio_stream.istream.ready.eq(vca0.i.ready),

            vca0.i.payload.x.eq(audio_stream.istream.payload[0]),
            vca1.i.payload.x.eq(audio_stream.istream.payload[1]),
            vca0.i.payload.gain.eq(audio_stream.istream.payload[2] << 2),
            vca1.i.payload.gain.eq(audio_stream.istream.payload[2] << 2),
        ]

        wiring.connect(m, vca0.o, waveshaper0.i)
        wiring.connect(m, vca1.o, waveshaper1.i)

        wiring.connect(m, waveshaper0.o, merge4.i[0])
        wiring.connect(m, waveshaper1.o, merge4.i[1])

        wiring.connect(m, dsp.ASQ_VALID, merge4.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge4.i[3])
        wiring.connect(m, merge4.o, audio_stream.ostream)

        return m

class TouchMixTop(Elaboratable):

    """Matrix mixer, combine touch inputs in interesting ways."""

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = platform.clock_domain_generator()

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True,
                touch_enabled=True)

        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)

        m.submodules.matrix_mix = matrix_mix = dsp.MatrixMix(
            i_channels=4, o_channels=4,
            coefficients=[[0.5, -0.5, 0.25, 0.1],
                          [0.5, -0.5, 0.25, 0.2],
                          [-0.5, 0.5, 0.25, 0.3],
                          [-0.5, 0.5, 0.25, 0.4]])

        wiring.connect(m, audio_stream.istream, matrix_mix.i)
        wiring.connect(m, matrix_mix.o, audio_stream.ostream)

        return m

class NCOTop(Elaboratable):

    """Audio-rate NCO."""

    def __init__(self):
        self.pmod0 = sim.FakeEurorackPmod()
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        """
        m.submodules.car = platform.clock_domain_generator()
        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)
        """

        m.submodules.car = sim.FakeTiliquaDomainGenerator()
        m.submodules.pmod0 = pmod0 = self.pmod0

        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)

        m.submodules.split4 = split4 = dsp.Split(n_channels=4)
        m.submodules.merge4 = merge4 = dsp.Merge(n_channels=4)

        m.submodules.rep4 = rep4 = dsp.Split(n_channels=4,
                                             replicate=True)

        m.submodules.merge2 = merge2 = dsp.Merge(n_channels=2)

        m.submodules.nco    = nco    = dsp.SawNCO(shift=4)

        def v_oct_lut(x, clamp_lo=-8.0, clamp_hi=6.0):
            def volts_to_freq(volts, a3_freq_hz=440.0):
                return (a3_freq_hz / 8.0) * 2 ** (volts + 2.0 - 3.0/4.0)
            def volts_to_delta(volts, sample_rate_hz=48000):
                return (1.0 / sample_rate_hz) * volts_to_freq(volts)
            # convert audio sample [-1, 1] to volts
            x = x*(2**15/4000)
            if x > clamp_hi:
                x = clamp_hi
            if x < clamp_lo:
                x = clamp_lo
            out = volts_to_delta(x) * 16
            print(x, volts_to_freq(x), out)
            return out

        m.submodules.v_oct = v_oct = dsp.WaveShaper(
                lut_function=v_oct_lut, lut_size=128, continuous=False)

        amplitude = 0.4


        def sine_osc(x):
            return amplitude*math.sin(math.pi*x)

        def saw_osc(x):
            return amplitude*x

        def tri_osc(x):
            return amplitude * (2*abs(x) - 1.0)

        def square_osc(x):
            return amplitude if x > 0 else -amplitude

        waveshapers = [
            dsp.WaveShaper(lut_function=sine_osc,
                           lut_size=128, continuous=True),
            dsp.WaveShaper(lut_function=saw_osc,
                           lut_size=128, continuous=True),
            dsp.WaveShaper(lut_function=tri_osc,
                           lut_size=128, continuous=True),
            dsp.WaveShaper(lut_function=square_osc,
                           lut_size=128, continuous=True),
        ]

        m.submodules += waveshapers

        N_UP = 4
        M_DOWN = 4

        m.submodules.resample_up0 = resample_up0 = dsp.Resample(
                fs_in=48000, n_up=N_UP, m_down=1)
        m.submodules.resample_up1 = resample_up1 = dsp.Resample(
                fs_in=48000, n_up=N_UP, m_down=1)

        m.submodules.down0 = resample_down0 = dsp.Resample(
                fs_in=48000*N_UP, n_up=1, m_down=M_DOWN)
        m.submodules.down1 = resample_down1 = dsp.Resample(
                fs_in=48000*N_UP, n_up=1, m_down=M_DOWN)
        m.submodules.down2 = resample_down2 = dsp.Resample(
                fs_in=48000*N_UP, n_up=1, m_down=M_DOWN)
        m.submodules.down3 = resample_down3 = dsp.Resample(
                fs_in=48000*N_UP, n_up=1, m_down=M_DOWN)

        wiring.connect(m, audio_stream.istream, split4.i)

        wiring.connect(m, split4.o[0], resample_up0.i)
        wiring.connect(m, split4.o[1], resample_up1.i)
        wiring.connect(m, split4.o[2], dsp.ASQ_READY)
        wiring.connect(m, split4.o[3], dsp.ASQ_READY)

        wiring.connect(m, resample_up0.o, v_oct.i)
        wiring.connect(m, v_oct.o, merge2.i[0])
        wiring.connect(m, resample_up1.o, merge2.i[1])
        wiring.connect(m, merge2.o, nco.i)
        wiring.connect(m, nco.o, rep4.i)
        wiring.connect(m, rep4.o[0], waveshapers[0].i)
        wiring.connect(m, rep4.o[1], waveshapers[1].i)
        wiring.connect(m, rep4.o[2], waveshapers[2].i)
        wiring.connect(m, rep4.o[3], waveshapers[3].i)

        wiring.connect(m, waveshapers[0].o, resample_down0.i)
        wiring.connect(m, waveshapers[1].o, resample_down1.i)
        wiring.connect(m, waveshapers[2].o, resample_down2.i)
        wiring.connect(m, waveshapers[3].o, resample_down3.i)

        wiring.connect(m, resample_down0.o, merge4.i[0])
        wiring.connect(m, resample_down1.o, merge4.i[1])
        wiring.connect(m, resample_down2.o, merge4.i[2])
        wiring.connect(m, resample_down3.o, merge4.i[3])

        wiring.connect(m, merge4.o, audio_stream.ostream)

        return m


def build_mirror():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(MirrorTop())

def build_svf():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(SVFTop())

def build_vca():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(VCATop())

def build_pitch():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(PitchTop())

def build_matrix():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(MatrixTop())

def build_diffuser():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(DiffuserTop())

def build_waveshaper():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(WaveshaperTop())

def build_touchmix():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(TouchMixTop())

def build_nco():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(NCOTop())

def verilog_nco():
    top = NCOTop()
    with open("nco.v", "w") as f:
        f.write(verilog.convert(top, ports=[
            ClockSignal("audio"),
            ResetSignal("audio"),
            ClockSignal("sync"),
            ResetSignal("sync"),
            top.pmod0.fs_strobe,
            top.pmod0.sample_inject[0]._target,
            top.pmod0.sample_inject[1]._target,
            top.pmod0.sample_inject[2]._target,
            top.pmod0.sample_inject[3]._target,
            top.pmod0.sample_extract[0]._target,
            top.pmod0.sample_extract[1]._target,
            top.pmod0.sample_extract[2]._target,
            top.pmod0.sample_extract[3]._target,
        ]))
