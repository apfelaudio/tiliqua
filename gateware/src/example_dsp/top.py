# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Collection of designs showing how to use the DSP library."""

import os
import sys
import subprocess

import math

from amaranth              import *
from amaranth.build        import *
from amaranth.lib          import wiring, data
from amaranth.lib.wiring   import In, Out


from amaranth_future       import stream, fixed

from tiliqua.tiliqua_platform import TiliquaPlatform
from tiliqua                  import eurorack_pmod, dsp, midi
from tiliqua.eurorack_pmod    import ASQ

# for sim
from amaranth.back import verilog
from tiliqua       import sim

class Mirror(wiring.Component):

    """Route audio inputs straight to outputs (in the audio domain)."""

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def elaborate(self, platform):
        m = Module()
        wiring.connect(m, wiring.flipped(self.i), wiring.flipped(self.o))
        return m

class ResonantFilter(wiring.Component):

    """High-, Low-, Bandpass with cutoff & resonance control."""

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def elaborate(self, platform):

        m = Module()

        m.submodules.svf0 = svf0 = dsp.SVF()

        # connect without 'wiring.connect' so we can see the payload field names.

        m.d.comb += [
            svf0.i.valid.eq(self.i.valid),
            self.i.ready.eq(svf0.i.ready),

            svf0.i.payload.x.eq(self.i.payload[0]),
            svf0.i.payload.cutoff.eq(self.i.payload[1]),
            svf0.i.payload.resonance.eq(self.i.payload[2]),
        ]

        m.d.comb += [
            svf0.o.ready.eq(self.o.ready),
            self.o.valid.eq(svf0.o.valid),

            self.o.payload[0].eq(svf0.o.payload.lp),
            self.o.payload[1].eq(svf0.o.payload.hp),
            self.o.payload[2].eq(svf0.o.payload.bp),
        ]

        return m

class DualVCA(wiring.Component):

    """Audio-rate VCA."""

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def elaborate(self, platform):
        m = Module()

        m.submodules.split4 = split4 = dsp.Split(n_channels=4)
        m.submodules.merge4 = merge4 = dsp.Merge(n_channels=4)

        m.submodules.merge2a = merge2a = dsp.Merge(n_channels=2)
        m.submodules.merge2b = merge2b = dsp.Merge(n_channels=2)

        m.submodules.vca0 = vca0 = dsp.VCA()
        m.submodules.vca1 = vca1 = dsp.VCA()

        # connect with 'wiring.connect' to show how this works.

        wiring.connect(m, wiring.flipped(self.i), split4.i)

        wiring.connect(m, split4.o[0], merge2a.i[0])
        wiring.connect(m, split4.o[1], merge2a.i[1])
        wiring.connect(m, split4.o[2], merge2b.i[0])
        wiring.connect(m, split4.o[3], merge2b.i[1])

        wiring.connect(m, merge2a.o, vca0.i)
        wiring.connect(m, vca0.o, merge4.i[0])

        wiring.connect(m, merge2b.o, vca1.i)
        wiring.connect(m, vca1.o, merge4.i[1])

        wiring.connect(m, dsp.ASQ_VALID, merge4.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge4.i[3])
        wiring.connect(m, merge4.o, wiring.flipped(self.o))

        return m

class Pitch(wiring.Component):

    """Pitch shifter with CV-controlled pitch."""

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def elaborate(self, platform):
        m = Module()

        m.submodules.split4 = split4 = dsp.Split(n_channels=4)
        m.submodules.merge4 = merge4 = dsp.Merge(n_channels=4)

        m.submodules.delay_line = delay_line = dsp.DelayLine(max_delay=8192)
        m.submodules.pitch_shift = pitch_shift = dsp.PitchShift(
            delayln=delay_line, xfade=delay_line.max_delay//4)

        wiring.connect(m, wiring.flipped(self.i), split4.i)

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

        wiring.connect(m, merge4.o, wiring.flipped(self.o))

        return m

class Matrix(wiring.Component):

    """Matrix mixer with fixed coefficients."""

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def elaborate(self, platform):
        m = Module()

        m.submodules.matrix_mix = matrix_mix = dsp.MatrixMix(
            i_channels=4, o_channels=4,
            coefficients=[[0.4, 0.3, 0.2, 0.1],
                          [0.1, 0.4, 0.3, 0.2],
                          [0.2, 0.1, 0.4, 0.3],
                          [0.3, 0.2, 0.1, 0.4]])

        wiring.connect(m, wiring.flipped(self.i), matrix_mix.i)
        wiring.connect(m, matrix_mix.o, wiring.flipped(self.o))

        return m

class Diffuser(wiring.Component):

    """
    4-channel feedback delay, diffused by a matrix mixer.
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def elaborate(self, platform):
        m = Module()

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

        wiring.connect(m, wiring.flipped(self.i), split4.i)

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

        wiring.connect(m, merge4.o, wiring.flipped(self.o))

        return m

class DualWaveshaper(wiring.Component):

    """Soft distortion, channel 1/2 inputs, 3 is overdrive gain."""

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def elaborate(self, platform):
        m = Module()

        m.submodules.merge4 = merge4 = dsp.Merge(n_channels=4)

        def scaled_tanh(x):
            return math.tanh(3.0*x)

        m.submodules.vca0 = vca0 = dsp.GainVCA()
        m.submodules.vca1 = vca1 = dsp.GainVCA()
        m.submodules.waveshaper0 = waveshaper0 = dsp.WaveShaper(lut_function=scaled_tanh)
        m.submodules.waveshaper1 = waveshaper1 = dsp.WaveShaper(lut_function=scaled_tanh)

        m.d.comb += [
            vca0.i.valid.eq(self.i.valid),
            vca1.i.valid.eq(self.i.valid),
            self.i.ready.eq(vca0.i.ready),

            vca0.i.payload.x.eq(self.i.payload[0]),
            vca1.i.payload.x.eq(self.i.payload[1]),
            vca0.i.payload.gain.eq(self.i.payload[2] << 2),
            vca1.i.payload.gain.eq(self.i.payload[2] << 2),
        ]

        wiring.connect(m, vca0.o, waveshaper0.i)
        wiring.connect(m, vca1.o, waveshaper1.i)

        wiring.connect(m, waveshaper0.o, merge4.i[0])
        wiring.connect(m, waveshaper1.o, merge4.i[1])

        wiring.connect(m, dsp.ASQ_VALID, merge4.i[2])
        wiring.connect(m, dsp.ASQ_VALID, merge4.i[3])
        wiring.connect(m, merge4.o, wiring.flipped(self.o))

        return m

class TouchMixTop(wiring.Component):

    """Matrix mixer, combine touch inputs in interesting ways."""

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def elaborate(self, platform):
        m = Module()

        m.submodules.matrix_mix = matrix_mix = dsp.MatrixMix(
            i_channels=4, o_channels=4,
            coefficients=[[0.5, -0.5, 0.25, 0.1],
                          [0.5, -0.5, 0.25, 0.2],
                          [-0.5, 0.5, 0.25, 0.3],
                          [-0.5, 0.5, 0.25, 0.4]])

        wiring.connect(m, wiring.flipped(self.i), matrix_mix.i)
        wiring.connect(m, matrix_mix.o, wiring.flipped(self.o))

        return m


class QuadNCO(wiring.Component):

    """Audio-rate NCO with oversampling. 4 different waveform outputs."""

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def elaborate(self, platform):
        m = Module()

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

        wiring.connect(m, wiring.flipped(self.i), split4.i)

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

        wiring.connect(m, merge4.o, wiring.flipped(self.o))

        return m

class MidiPolyTop(wiring.Component):

    """
    Simple polyphonic MIDI synthesizer.

    Each voice is routed to a separate output channel and goes through
    a filter that can be modulated by the note velocity or mod wheel.

    TODO: Add DC blockers on output channels. When SVF cutoff is zero the
    outputs stick at DC.
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    i_midi: In(stream.Signature(midi.MidiMessage))

    def elaborate(self, platform):
        m = Module()

        max_voices = 4

        # Create LUTs from midi note to freq_inc (ASQ tuning into NCO).
        # Store it in memories where the address is the midi note,
        # and the data coming out is directly routed to NCO freq_inc.
        lut = []
        sample_rate_hz = 48000
        for i in range(128):
            freq = 440 * 2**((i-69)/12.0)
            freq_inc = freq * (1.0 / sample_rate_hz)
            lut.append(fixed.Const(freq_inc, shape=ASQ)._value)
        mems = [Memory(width=ASQ.as_shape().width, depth=len(lut), init=lut)
                for _ in range(max_voices)]
        rports = [mems[n].read_port(transparent=True) for n in range(max_voices)]
        m.submodules += mems

        # Create our MIDI voice tracker, oscillators, VCA
        voice_tracker = midi.MidiVoiceTracker(max_voices=max_voices)
        ncos = [dsp.SawNCO(shift=0) for _ in range(max_voices)]
        svfs = [dsp.SVF() for _ in range(max_voices)]
        merge4 = dsp.Merge(n_channels=4)
        m.submodules += [voice_tracker, ncos, svfs, merge4]

        # Connect MIDI stream -> voice tracker
        wiring.connect(m, wiring.flipped(self.i_midi), voice_tracker.i)

        # Use CC1 (mod wheel) as upper bound on filter cutoff.
        last_cc1 = Signal(8, reset=255)
        with m.If(self.i_midi.valid):
            msg = self.i_midi.payload
            with m.Switch(msg.midi_type):
                with m.Case(midi.MessageType.CONTROL_CHANGE):
                    # mod wheel is CC 1
                    with m.If(msg.midi_payload.control_change.controller_number == 1):
                        m.d.sync += last_cc1.eq(msg.midi_payload.control_change.data)

        for n in range(max_voices):

            # Filter cutoff on all channels is min(mod wheel, note velocity)
            with m.If(last_cc1 < voice_tracker.o[n].payload.velocity):
                m.d.comb += svfs[n].i.payload.cutoff.sas_value().eq(last_cc1 << 4)
            with m.Else():
                m.d.comb += svfs[n].i.payload.cutoff.sas_value().eq(voice_tracker.o[n].payload.velocity << 4)

            m.d.comb += [
                # Connect voice.note -> note to frequency LUT
                rports[n].en.eq(1),
                rports[n].addr.eq(voice_tracker.o[n].payload.note),

                # Connect LUT output -> NCO.i (clocked at i.valid for normal sample rate)
                ncos[n].i.valid.eq(self.i.valid),
                ncos[n].i.payload.freq_inc.eq(rports[n].data),

                # Connect voice.vel and NCO.o -> SVF.i
                svfs[n].i.valid.eq(ncos[n].o.valid),
                svfs[n].i.payload.x.eq(ncos[n].o.payload >> 2),
                svfs[n].i.payload.resonance.sas_value().eq(8000),
                ncos[n].o.ready.eq(svfs[n].i.ready),

                # Connect SVF -> merge4 channel
                svfs[n].o.ready.eq(merge4.i[n].ready),
                merge4.i[n].valid.eq(svfs[n].o.valid),
                merge4.i[n].payload.eq(svfs[n].o.payload.lp),
            ]

        # One channel per voice -> output
        wiring.connect(m, merge4.o, wiring.flipped(self.o)),

        return m

class MidiCVTop(wiring.Component):

    """
    Simple monophonic MIDI to CV conversion.

    Output mapping is:
    Output 0: Gate
    Output 1: V/oct CV
    Output 2: Velocity
    Output 3: Mod Wheel (CC1)
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    # Note: MIDI is valid at a much lower rate than audio streams
    i_midi: In(stream.Signature(midi.MidiMessage))

    def elaborate(self, platform):
        m = Module()

        m.d.comb += [
            # Always forward our audio payload
            self.i.ready.eq(1),
            self.o.valid.eq(1),

            # Always ready for MIDI messages
            self.i_midi.ready.eq(1),
        ]

        # Create a LUT from midi note to voltage (output ASQ).
        lut = []
        for i in range(128):
            volts_per_note = 1.0/12.0
            volts = i*volts_per_note - 5
            # convert volts to audio sample
            x = volts/(2**15/4000)
            lut.append(fixed.Const(x, shape=ASQ)._value)

        # Store it in a memory where the address is the midi note,
        # and the data coming out is directly routed to V/Oct out.
        m.submodules.mem = mem = Memory(
            width=ASQ.as_shape().width, depth=len(lut), init=lut)
        rport = mem.read_port(transparent=True)
        m.d.comb += [
            rport.en.eq(1),
        ]

        # Route memory straight out to our note payload.
        m.d.sync += self.o.payload[1].as_value().eq(rport.data),

        with m.If(self.i_midi.valid):
            msg = self.i_midi.payload
            with m.Switch(msg.midi_type):
                with m.Case(midi.MessageType.NOTE_ON):
                    m.d.sync += [
                        # Gate output on
                        self.o.payload[0].eq(fixed.Const(0.5, shape=ASQ)),
                        # Set velocity output
                        self.o.payload[2].as_value().eq(
                            msg.midi_payload.note_on.velocity << 8),
                        # Set note index in LUT
                        rport.addr.eq(msg.midi_payload.note_on.note),
                    ]
                with m.Case(midi.MessageType.NOTE_OFF):
                    # Zero gate and velocity on NOTE_OFF
                    m.d.sync += [
                        self.o.payload[0].eq(0),
                        self.o.payload[2].eq(0),
                    ]
                with m.Case(midi.MessageType.CONTROL_CHANGE):
                    # mod wheel is CC 1
                    with m.If(msg.midi_payload.control_change.controller_number == 1):
                        m.d.sync += [
                            self.o.payload[3].as_value().eq(
                                msg.midi_payload.control_change.data << 8),
                        ]

        return m

class SimTop(Elaboratable):

    def __init__(self, core):
        self.pmod0 = sim.FakeEurorackPmod()
        self.core = core()
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        m.submodules.car = sim.FakeTiliquaDomainGenerator()
        m.submodules.pmod0 = pmod0 = self.pmod0
        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)
        m.submodules.core = self.core
        wiring.connect(m, audio_stream.istream, self.core.i)
        wiring.connect(m, self.core.o, audio_stream.ostream)
        return m

class CoreTop(Elaboratable):

    def __init__(self, core, touch=False):
        self.core = core()
        self.touch = touch
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        m.submodules.car = platform.clock_domain_generator()
        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True,
                touch_enabled=self.touch)
        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)
        m.submodules.core = self.core
        wiring.connect(m, audio_stream.istream, self.core.i)
        wiring.connect(m, self.core.o, audio_stream.ostream)

        if hasattr(self.core, "i_midi"):
            # For now, if a core requests midi input, we connect it up
            # to the type-A serial MIDI RX input. In theory this bytestream
            # could also come from LUNA in host or device mode.
            uart_pins = platform.request("uart", 1)
            m.submodules.serialrx = serialrx = midi.SerialRx(
                    system_clk_hz=60e6, pins=uart_pins)
            m.submodules.midi_decode = midi_decode = midi.MidiDecode()
            wiring.connect(m, serialrx.o, midi_decode.i)
            wiring.connect(m, midi_decode.o, self.core.i_midi)

        return m

def get_core(name):
    """Get top-level DSP core attributes by a short name."""

    cores = {
        #             (touch, class name)
        "mirror":     (False, Mirror),
        "svf":        (False, ResonantFilter),
        "vca":        (False, DualVCA),
        "pitch":      (False, Pitch),
        "matrix":     (False, Matrix),
        "diffuser":   (False, Diffuser),
        "touchmix":   (True,  TouchMixTop),
        "waveshaper": (False, DualWaveshaper),
        "nco":        (False, QuadNCO),
        "midicv":     (False, MidiCVTop),
        "midipoly":   (False, MidiPolyTop),
    }

    if name not in cores:
        print(f"provided core '{name}' is not one of {list(cores)}")
        sys.exit(-1)

    return cores[name]

def build(core_name: str):
    """Build a bitstream for a top-level DSP core."""

    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    touch, cls_core = get_core(core_name)
    top = CoreTop(cls_core, touch=touch)
    TiliquaPlatform().build(top)

def simulate(core_name: str):
    """Simulate a top-level DSP core using Verilator."""

    _, cls_core = get_core(core_name)
    build_dst = "build"
    dst = f"{build_dst}/core.v"
    print(f"write verilog implementation of '{core_name}' to '{dst}'...")

    top = SimTop(cls_core)

    os.makedirs(build_dst, exist_ok=True)
    with open(dst, "w") as f:
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

    verilator_dst = "build/obj_dir"

    print(f"verilate '{dst}' into C++ binary...")
    subprocess.check_call(["verilator",
                           "-Wno-COMBDLY",
                           "-Wno-CASEINCOMPLETE",
                           "-Wno-CASEOVERLAP",
                           "-Wno-WIDTHEXPAND",
                           "-Wno-WIDTHTRUNC",
                           "-Wno-TIMESCALEMOD",
                           "-Wno-PINMISSING",
                           "-cc",
                           "--trace-fst",
                           "--exe",
                           "--Mdir", f"{verilator_dst}",
                           "--build",
                           "-j", "0",
                           "../../src/example_dsp/sim_dsp_core.cpp",
                           f"{dst}"],
                          env=os.environ)

    print(f"run verilated binary '{verilator_dst}/Vcore'...")
    subprocess.check_call([f"{verilator_dst}/Vcore"],
                          env=os.environ)

    print(f"done.")
