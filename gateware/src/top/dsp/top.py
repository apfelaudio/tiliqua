# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Collection of designs showing how to use the DSP library."""

import os
import sys
import subprocess

import math

from amaranth                 import *
from amaranth.build           import *
from amaranth.lib             import wiring, data, stream
from amaranth.lib.wiring      import In, Out
from amaranth_soc             import wishbone
from amaranth_future          import fixed

from tiliqua                  import eurorack_pmod, dsp, midi, psram_peripheral, delay
from tiliqua.cache            import WishboneL2Cache
from tiliqua.eurorack_pmod    import ASQ
from tiliqua.cli              import top_level_cli

# for sim
from amaranth.back            import verilog
from tiliqua                  import sim

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

        m.submodules.delay_line = delay_line = dsp.DelayLine(
            max_delay=8192, psram_backed=False)
        m.submodules.pitch_shift = pitch_shift = dsp.PitchShift(
            tap=delay_line.add_tap(), xfade=delay_line.max_delay//4)

        wiring.connect(m, wiring.flipped(self.i), split4.i)

        # write audio samples to delay line
        wiring.connect(m, split4.o[0], delay_line.i)

        # hook up 2nd input channel as pitch control, use fixed grain_sz
        m.d.comb += [
            split4.o[1].ready.eq(pitch_shift.i.ready),
            pitch_shift.i.valid.eq(split4.o[1].valid),
            pitch_shift.i.payload.pitch.eq(split4.o[1].payload.raw() >> 8),
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

class PSRAMPingPongDelay(wiring.Component):

    """
    2-channel stereo ping-pong delay, backed by external PSRAM.

    2 delay lines are instantiated in isolated slices of the external
    memory address space. Using external memory allows for much longer
    delay times whilst using less resources, compared to SRAM-backed
    delay lines, however on a larger design, you have to be careful
    that PSRAM-backed delay lines don't get starved by other PSRAM
    traffic (i.e video framebuffer operations).

    Tiliqua input 0/1 is stereo in, output 0/1 is stereo out.
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    # shared bus to external memory
    bus: Out(wishbone.Signature(addr_width=22,
                                data_width=32,
                                granularity=8,
                                features={'bte', 'cti'}))

    def __init__(self):
        super().__init__()

        # 2 delay lines, backed by 2 different slices of PSRAM address space.

        self.delayln1 = dsp.DelayLine(
            max_delay=0x4000, # careful this doesn't collide with delayln2.base!
            psram_backed=True,
            addr_width_o=self.bus.addr_width,
            base=0x00000,
        )

        self.delayln2 = dsp.DelayLine(
            max_delay=0x4000,
            psram_backed=True,
            addr_width_o=self.bus.addr_width,
            base=0x4000,
        )

        # Both delay lines share our memory bus round-robin for all operations.

        self._arbiter = wishbone.Arbiter(addr_width=self.bus.addr_width,
                                         data_width=self.bus.data_width,
                                         granularity=self.bus.granularity,
                                         features=self.bus.features)
        self._arbiter.add(self.delayln1.bus)
        self._arbiter.add(self.delayln2.bus)

        # Create the PingPongCore using the above delay lines.

        self.pingpong = delay.PingPongDelay(self.delayln1, self.delayln2)

    def elaborate(self, platform):
        m = Module()

        m.submodules.arbiter  = self._arbiter
        m.submodules.delayln1 = self.delayln1
        m.submodules.delayln2 = self.delayln2
        m.submodules.pingping = self.pingpong

        wiring.connect(m, self._arbiter.bus, wiring.flipped(self.bus))

        # Map hardware in/out channels 0, 1 (of 4) to pingpong stereo channels 0, 1

        dsp.channel_remap(m, wiring.flipped(self.i), self.pingpong.i, {0: 0, 1: 1})
        dsp.channel_remap(m, self.pingpong.o, wiring.flipped(self.o), {0: 0, 1: 1})

        return m

class SRAMPingPongDelay(wiring.Component):

    """
    2-channel stereo ping-pong delay, backed by internal SRAM.

    Tiliqua input 0/1 is stereo in, output 0/1 is stereo out.
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def __init__(self):
        super().__init__()

        # 2 delay lines, backed by independent slabs of internal SRAM.

        self.delayln1 = dsp.DelayLine(max_delay=0x4000)
        self.delayln2 = dsp.DelayLine(max_delay=0x4000)

        # Create the PingPongCore using the above delay lines.

        self.pingpong = delay.PingPongDelay(self.delayln1, self.delayln2)

    def elaborate(self, platform):
        m = Module()

        m.submodules.delayln1 = self.delayln1
        m.submodules.delayln2 = self.delayln2

        m.submodules.pingping = self.pingpong

        # Map hardware in/out channels 0, 1 (of 4) to pingpong stereo channels 0, 1

        dsp.channel_remap(m, wiring.flipped(self.i), self.pingpong.i, {0: 0, 1: 1})
        dsp.channel_remap(m, self.pingpong.o, wiring.flipped(self.o), {0: 0, 1: 1})

        return m

class PSRAMDiffuser(wiring.Component):

    """
    PSRAM-backed 4-channel feedback delay, diffused by a matrix mixer.
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))
    bus: Out(wishbone.Signature(addr_width=22,
                                data_width=32,
                                granularity=8,
                                features={'bte', 'cti'}))

    def __init__(self):
        super().__init__()

        # 4 delay lines, backed by 4 different slices of PSRAM address space.

        self.delay_lines = [
            dsp.DelayLine(
                max_delay=0x10000,
                psram_backed=True,
                addr_width_o=self.bus.addr_width,
                base=0x00000,
            ),
            dsp.DelayLine(
                max_delay=0x10000,
                psram_backed=True,
                addr_width_o=self.bus.addr_width,
                base=0x10000,
            ),
            dsp.DelayLine(
                max_delay=0x10000,
                psram_backed=True,
                addr_width_o=self.bus.addr_width,
                base=0x20000,
            ),
            dsp.DelayLine(
                max_delay=0x10000,
                psram_backed=True,
                addr_width_o=self.bus.addr_width,
                base=0x30000,
            ),
        ]

        # All delay lines share our top-level bus for read/write operations.

        self._arbiter = wishbone.Arbiter(addr_width=self.bus.addr_width,
                                         data_width=self.bus.data_width,
                                         granularity=self.bus.granularity,
                                         features=self.bus.features)
        for delayln in self.delay_lines:
            self._arbiter.add(delayln.bus)

        self.diffuser = delay.Diffuser(self.delay_lines)

    def elaborate(self, platform):
        m = Module()

        dsp.named_submodules(m.submodules, self.delay_lines)

        wiring.connect(m, self._arbiter.bus, wiring.flipped(self.bus))

        m.submodules.diffuser = self.diffuser

        wiring.connect(m, wiring.flipped(self.i), self.diffuser.i)
        wiring.connect(m, self.diffuser.o, wiring.flipped(self.o))

        return m

class SRAMDiffuser(wiring.Component):

    """
    SRAM-backed 4-channel feedback delay, diffused by a matrix mixer.
    """

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def __init__(self):
        super().__init__()

        # 4 delay lines, backed by 4 independent SRAM banks.

        self.delay_lines = [
            dsp.DelayLine(max_delay=2048),
            dsp.DelayLine(max_delay=4096),
            dsp.DelayLine(max_delay=8192),
            dsp.DelayLine(max_delay=8192),
        ]

        self.diffuser = delay.Diffuser(self.delay_lines)

    def elaborate(self, platform):
        m = Module()

        dsp.named_submodules(m.submodules, self.delay_lines)

        m.submodules.diffuser = self.diffuser

        wiring.connect(m, wiring.flipped(self.i), self.diffuser.i)
        wiring.connect(m, self.diffuser.o, wiring.flipped(self.o))

        return m

class CoreTop(Elaboratable):

    def __init__(self, dsp_core, enable_touch):
        self.core = dsp_core()
        self.touch = enable_touch

        # Only used for simulation
        self.fs_strobe = Signal()
        self.inject0 = Signal(signed(16))
        self.inject1 = Signal(signed(16))
        self.inject2 = Signal(signed(16))
        self.inject3 = Signal(signed(16))

        self.psram_periph = psram_peripheral.Peripheral(size=16*1024*1024)

        super().__init__()

    def elaborate(self, platform):
        m = Module()

        if sim.is_hw(platform):
            m.submodules.car = platform.clock_domain_generator()
            m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                    pmod_pins=platform.request("audio_ffc"),
                    hardware_r33=True,
                    touch_enabled=self.touch)
        else:
            m.submodules.car = sim.FakeTiliquaDomainGenerator()
            m.submodules.pmod0 = pmod0 = sim.FakeEurorackPmod()
            m.d.comb += [
                pmod0.sample_inject[0]._target.eq(self.inject0),
                pmod0.sample_inject[1]._target.eq(self.inject1),
                pmod0.sample_inject[2]._target.eq(self.inject2),
                pmod0.sample_inject[3]._target.eq(self.inject3),
                pmod0.fs_strobe.eq(self.fs_strobe),
            ]

        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)
        m.submodules.core = self.core
        wiring.connect(m, audio_stream.istream, self.core.i)
        wiring.connect(m, self.core.o, audio_stream.ostream)

        if hasattr(self.core, "i_midi") and sim.is_hw(platform):
            # For now, if a core requests midi input, we connect it up
            # to the type-A serial MIDI RX input. In theory this bytestream
            # could also come from LUNA in host or device mode.
            midi_pins = platform.request("midi")
            m.submodules.serialrx = serialrx = midi.SerialRx(
                    system_clk_hz=60e6, pins=midi_pins)
            m.submodules.midi_decode = midi_decode = midi.MidiDecode()
            wiring.connect(m, serialrx.o, midi_decode.i)
            wiring.connect(m, midi_decode.o, self.core.i_midi)

        if hasattr(self.core, "bus"):
            m.submodules.psram_periph = self.psram_periph
            wiring.connect(m, self.core.bus, self.psram_periph.bus)

        return m

# Different DSP cores that can be selected at top-level CLI.
CORES = {
    #                 (touch, class name)
    "mirror":         (False, Mirror),
    "svf":            (False, ResonantFilter),
    "vca":            (False, DualVCA),
    "pitch":          (False, Pitch),
    "matrix":         (False, Matrix),
    "touchmix":       (True,  TouchMixTop),
    "waveshaper":     (False, DualWaveshaper),
    "nco":            (False, QuadNCO),
    "midicv":         (False, MidiCVTop),
    "psram_pingpong": (False, PSRAMPingPongDelay),
    "sram_pingpong":  (False, SRAMPingPongDelay),
    "psram_diffuser": (False, PSRAMDiffuser),
    "sram_diffuser":  (False, SRAMDiffuser),
}

def simulation_ports(fragment):
    return {
        "clk_audio":      (ClockSignal("audio"),                       None),
        "rst_audio":      (ResetSignal("audio"),                       None),
        "clk_sync":       (ClockSignal("sync"),                        None),
        "rst_sync":       (ResetSignal("sync"),                        None),
        "fs_strobe":      (fragment.fs_strobe,                         None),
        "fs_inject0":     (fragment.inject0,                           None),
        "fs_inject1":     (fragment.inject1,                           None),
        "fs_inject2":     (fragment.inject2,                           None),
        "fs_inject3":     (fragment.inject3,                           None),
        "idle":           (fragment.psram_periph.simif.idle,           None),
        "address_ptr":    (fragment.psram_periph.simif.address_ptr,    None),
        "read_data_view": (fragment.psram_periph.simif.read_data_view, None),
        "write_data":     (fragment.psram_periph.simif.write_data,     None),
        "read_ready":     (fragment.psram_periph.simif.read_ready,     None),
        "write_ready":    (fragment.psram_periph.simif.write_ready,    None),
    }

def argparse_callback(parser):
    parser.add_argument('--dsp-core', type=str, default="mirror",
                        help=f"One of {list(CORES)}")

def argparse_fragment(args):
    # Additional arguments to be provided to CoreTop
    if args.dsp_core not in CORES:
        print(f"provided '--dsp-core {args.dsp_core}' is not one of {list(CORES)}")
        sys.exit(-1)

    touch, cls_name = CORES[args.dsp_core]
    return {
        "dsp_core": cls_name,
        "enable_touch": touch,
    }

if __name__ == "__main__":
    top_level_cli(
        CoreTop,
        video_core=False,
        sim_ports=simulation_ports,
        sim_harness="../../src/top/dsp/sim_dsp_core.cpp",
        argparse_callback=argparse_callback,
        argparse_fragment=argparse_fragment,
    )
