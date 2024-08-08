# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import logging
import os
import sys
import math

from amaranth                                    import *
from amaranth.hdl.rec                            import Record
from amaranth.lib                                import wiring, data
from amaranth.lib.wiring                         import In, Out

from amaranth_future                             import stream, fixed

from luna_soc.util.readbin                       import get_mem_data
from luna_soc                                    import top_level_cli
from luna_soc.gateware.csr.base                  import Peripheral

from tiliqua                                     import eurorack_pmod, dsp, midi
from tiliqua.eurorack_pmod                       import ASQ
from tiliqua.tiliqua_platform                    import TiliquaPlatform, set_environment_variables
from tiliqua.tiliqua_soc                         import TiliquaSoc

from example_vectorscope.top                     import Stroke

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
            coefficients=[[0.6, 0.0, 0.0, 0.0, 0.8, 0.0, 0.0, 0.0], # in0
                          [0.0, 0.6, 0.0, 0.0, 0.0, 0.8, 0.0, 0.0], #  |
                          [0.0, 0.0, 0.6, 0.0, 0.0, 0.0, 0.8, 0.0], #  |
                          [0.0, 0.0, 0.0, 0.6, 0.0, 0.0, 0.0, 0.8], # in3
                          [0.4, 0.0, 0.0, 0.0, 0.4,-0.4,-0.4,-0.4], # ds0
                          [0.0, 0.4, 0.0, 0.0,-0.4, 0.4,-0.4,-0.4], #  |
                          [0.0, 0.0, 0.4, 0.0,-0.4,-0.4, 0.4,-0.4], #  |
                          [0.0, 0.0, 0.0, 0.4,-0.4,-0.4,-0.4, 0.4]])# ds3
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

class PolySynth(wiring.Component):

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    i_midi: In(stream.Signature(midi.MidiMessage))

    drive: In(unsigned(16))
    reso: In(unsigned(16))

    voice_states: Out(data.StructLayout({
        "note":  unsigned(8),
        "cutoff": unsigned(8),
    })).array(8)

    def elaborate(self, platform):
        m = Module()

        # supported simultaneous voices
        n_voices = 8

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
                for _ in range(n_voices)]
        rports = [mems[n].read_port(transparent=True) for n in range(n_voices)]
        m.submodules += mems

        voice_tracker = midi.MidiVoiceTracker(max_voices=n_voices)
        # 1 smoother per oscillator for filter cutoff, to prevent pops.
        boxcars = [dsp.Boxcar(n=16) for _ in range(n_voices)]
        # 1 oscillator and filter per oscillator
        ncos = [dsp.SawNCO(shift=0) for _ in range(n_voices)]
        svfs = [dsp.SVF() for _ in range(n_voices)]
        merge = dsp.Merge(n_channels=n_voices)
        m.submodules += [voice_tracker, boxcars, ncos, svfs, merge]

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

        # analog ins
        m.submodules.cv_in = cv_in = dsp.Split(
                n_channels=4, source=wiring.flipped(self.i))
        cv_in.wire_ready(m, [2, 3])

        for n in range(n_voices):

            m.d.comb += [
                self.voice_states[n].note.eq(rports[n].addr),
                self.voice_states[n].cutoff.eq(boxcars[n].i.payload.as_value() >> 3),
            ]

            # Filter cutoff on all channels is min(mod wheel, note velocity)
            # Cutoff itself is smoothed by boxcars before being sent to SVF cutoff.
            with m.If(last_cc1 < voice_tracker.o[n].payload.velocity):
                m.d.comb += boxcars[n].i.payload.sas_value().eq(last_cc1 << 4)
            with m.Else():
                m.d.comb += boxcars[n].i.payload.sas_value().eq(
                        voice_tracker.o[n].payload.velocity << 4)

            m.d.comb += [
                # Connect voice.note -> note to frequency LUT
                rports[n].en.eq(1),
                rports[n].addr.eq(voice_tracker.o[n].payload.note),
            ]

            # Connect LUT output -> NCO.i (clocked at i.valid for normal sample rate)
            dsp.connect_remap(m, cv_in.o[0], ncos[n].i, lambda o, i : [
                # For fun, phase mod on audio in #0
                i.payload.phase   .eq(o.payload),
                i.payload.freq_inc.eq(rports[n].data) # ok, always valid
            ])

            # Connect voice.vel and NCO.o -> SVF.i
            dsp.connect_remap(m, ncos[n].o, svfs[n].i, lambda o, i : [
                i.payload.x                    .eq(o.payload >> 1),
                i.payload.resonance.sas_value().eq(self.reso),
                i.payload.cutoff               .eq(boxcars[n].o.payload) # hack
            ])

            # Connect SVF LPF -> merge channel
            dsp.connect_remap(m, svfs[n].o, merge.i[n], lambda o, i : [
                i.payload.eq(o.payload.lp),
            ])

            # HACK: Boxcar synchronization
            m.d.comb += [
                boxcars[n].i.valid.eq(ncos[n].o.valid),
                boxcars[n].o.ready.eq(svfs[n].i.ready),
            ]

        # Voice mixdown to stereo. Alternate left/right
        o_channels = 2
        coefficients = [[0.75*o_channels/n_voices, 0.0                ],
                        [0.0,                      0.75*o_channels/n_voices]] * (n_voices // 2)
        m.submodules.matrix_mix = matrix_mix = dsp.MatrixMix(
            i_channels=n_voices, o_channels=o_channels,
            coefficients=coefficients)
        wiring.connect(m, merge.o, matrix_mix.i),

        # Output diffuser

        m.submodules.diffuser = diffuser = Diffuser()

        # Stereo HPF to remove DC from any voices in 'zero cutoff'
        # Route to audio output channels 2 & 3

        output_hpfs = [dsp.SVF() for _ in range(o_channels)]
        m.submodules += output_hpfs

        m.submodules.hpf_split2 = hpf_split2 = dsp.Split(n_channels=2, source=matrix_mix.o)
        m.submodules.hpf_merge4 = hpf_merge4 = dsp.Merge(n_channels=4, sink=diffuser.i)
        hpf_merge4.wire_valid(m, [0, 1])

        for lr in [0, 1]:
            dsp.connect_remap(m, hpf_split2.o[lr], output_hpfs[lr].i, lambda o, i : [
                i.payload.x                     .eq(o.payload),
                i.payload.cutoff.sas_value()    .eq(200),
                i.payload.resonance.sas_value() .eq(20000),
            ])

            dsp.connect_remap(m, output_hpfs[lr].o, hpf_merge4.i[2+lr], lambda o, i : [
                i.payload.eq(o.payload.hp << 2)
            ])

        # Implement stereo distortion effect after diffuser.

        m.submodules.diffuser_split4 = diffuser_split4 = dsp.Split(
                n_channels=4, source=diffuser.o)
        diffuser_split4.wire_ready(m, [0, 1])

        m.submodules.cv_gain_split2 = cv_gain_split2 = dsp.Split(
                n_channels=2, replicate=True, source=cv_in.o[1])

        def scaled_tanh(x):
            return math.tanh(3.0*x)

        outs = []
        for lr in [0, 1]:
            vca = dsp.GainVCA()
            waveshaper = dsp.WaveShaper(lut_function=scaled_tanh)
            vca_merge2 = dsp.Merge(n_channels=2)
            m.submodules += [vca, waveshaper, vca_merge2]

            wiring.connect(m, diffuser_split4.o[2+lr], vca_merge2.i[0])
            wiring.connect(m, cv_gain_split2.o[lr],    vca_merge2.i[1])

            dsp.connect_remap(m, vca_merge2.o, vca.i, lambda o, i : [
                i.payload.x   .eq(o.payload[0]),
                #i.payload.gain.eq(o.payload[1] << 2)
                i.payload.gain.eq(self.drive << 2)
            ])

            wiring.connect(m, vca.o, waveshaper.i)
            outs.append(waveshaper.o)

        # Final outputs on channel 2, 3
        m.submodules.merge4 = merge4 = dsp.Merge(
                n_channels=4, sink=wiring.flipped(self.o))
        merge4.wire_valid(m, [0, 1])
        wiring.connect(m, outs[0], merge4.i[2])
        wiring.connect(m, outs[1], merge4.i[3])

        return m

class SynthPeripheral(Peripheral, Elaboratable):

    """
    Bridges SoC memory space such that we can peek and poke
    registers of the polysynth engine from our SoC.
    """

    def __init__(self, synth=None):

        super().__init__()

        self.synth = synth

        # CSRs
        bank                   = self.csr_bank()
        self._drive            = bank.csr(16, "w")
        self._reso             = bank.csr(16, "w")

        self._voice0_note      = bank.csr(8, "r")
        self._voice1_note      = bank.csr(8, "r")
        self._voice2_note      = bank.csr(8, "r")
        self._voice3_note      = bank.csr(8, "r")
        self._voice4_note      = bank.csr(8, "r")
        self._voice5_note      = bank.csr(8, "r")
        self._voice6_note      = bank.csr(8, "r")
        self._voice7_note      = bank.csr(8, "r")

        self._voice0_cutoff    = bank.csr(8, "r")
        self._voice1_cutoff    = bank.csr(8, "r")
        self._voice2_cutoff    = bank.csr(8, "r")
        self._voice3_cutoff    = bank.csr(8, "r")
        self._voice4_cutoff    = bank.csr(8, "r")
        self._voice5_cutoff    = bank.csr(8, "r")
        self._voice6_cutoff    = bank.csr(8, "r")
        self._voice7_cutoff    = bank.csr(8, "r")

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge

        with m.If(self._drive.w_stb):
            m.d.sync += self.synth.drive.eq(self._drive.w_data)

        with m.If(self._reso.w_stb):
            m.d.sync += self.synth.reso.eq(self._reso.w_data)

        for n in range(8):
            m.d.comb += [
                getattr(self, f"_voice{n}_note").r_data  .eq(self.synth.voice_states[n].note),
                getattr(self, f"_voice{n}_cutoff").r_data.eq(self.synth.voice_states[n].cutoff)
            ]

        return m


class VSPeripheral(Peripheral, Elaboratable):

    """
    Bridges SoC memory space such that we can peek and poke
    registers of the vectorscope stroke engine from our SoC.
    """

    def __init__(self):

        super().__init__()

        self.persist           = Signal(16, reset=1024)
        self.hue               = Signal(8,  reset=0)
        self.intensity         = Signal(8,  reset=4)
        self.decay             = Signal(8,  reset=1)
        self.scale             = Signal(8,  reset=6)

        # CSRs
        bank                   = self.csr_bank()
        self._persist          = bank.csr(16, "w")
        self._hue              = bank.csr(8, "w")
        self._intensity        = bank.csr(8, "w")
        self._decay            = bank.csr(8, "w")
        self._scale            = bank.csr(8, "w")

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge

        with m.If(self._persist.w_stb):
            m.d.sync += self.persist.eq(self._persist.w_data)

        with m.If(self._hue.w_stb):
            m.d.sync += self.hue.eq(self._hue.w_data)

        with m.If(self._intensity.w_stb):
            m.d.sync += self.intensity.eq(self._intensity.w_data)

        with m.If(self._decay.w_stb):
            m.d.sync += self.decay.eq(self._decay.w_data)

        with m.If(self._scale.w_stb):
            m.d.sync += self.scale.eq(self._scale.w_data)

        return m

class PolySoc(TiliquaSoc):
    def __init__(self, *, firmware_path, dvi_timings):
        super().__init__(firmware_path=firmware_path, dvi_timings=dvi_timings, audio_192=False,
                         audio_out_peripheral=False)
        # scope stroke bridge from audio stream
        fb_size = (self.video.fb_hsize, self.video.fb_vsize)
        self.stroke = Stroke(
                fb_base=self.video.fb_base, bus_master=self.soc.psram.bus, fb_size=fb_size,
                fs=48000, n_upsample=8)
        self.soc.psram.add_master(self.stroke.bus)
        # scope controls
        self.vs_periph = VSPeripheral()
        self.soc.add_peripheral(self.vs_periph, addr=0xf0006000)
        # synth controls
        self.synth_periph = SynthPeripheral()
        self.soc.add_peripheral(self.synth_periph, addr=0xf0007000)

    def elaborate(self, platform):

        m = Module()

        m.submodules.polysynth = polysynth = PolySynth()
        self.synth_periph.synth = polysynth

        m.submodules += super().elaborate(platform)

        pmod0 = self.pmod0_periph.pmod

        m.submodules.astream = astream = eurorack_pmod.AudioStream(pmod0)

        m.submodules.stroke = self.stroke

        # Memory controller hangs if we start making requests to it straight away.
        # TODO collapse this into delay already present in super()
        on_delay = Signal(32)
        with m.If(on_delay < 0xFFFF):
            m.d.sync += on_delay.eq(on_delay+1)
        with m.Else():
            m.d.sync += self.stroke.enable.eq(1)

        m.d.comb += [
            self.persist.holdoff.eq(self.vs_periph.persist),
            self.persist.decay.eq(self.vs_periph.decay),
            self.stroke.hue.eq(self.vs_periph.hue),
            self.stroke.intensity.eq(self.vs_periph.intensity),
            self.stroke.scale.eq(self.vs_periph.scale),
        ]

        # polysynth midi
        midi_pins = platform.request("midi")
        m.submodules.serialrx = serialrx = midi.SerialRx(
                system_clk_hz=60e6, pins=midi_pins)
        m.submodules.midi_decode = midi_decode = midi.MidiDecode()
        wiring.connect(m, serialrx.o, midi_decode.i)
        wiring.connect(m, midi_decode.o, polysynth.i_midi)

        # polysynth audio
        wiring.connect(m, astream.istream, polysynth.i)
        wiring.connect(m, polysynth.o, astream.ostream)

        # polysynth out -> vectorscope TODO use true split
        m.d.comb += [
            self.stroke.i.valid.eq(polysynth.o.valid),
            self.stroke.i.payload[0].eq(polysynth.o.payload[2]),
            self.stroke.i.payload[1].eq(polysynth.o.payload[3]),
        ]

        return m


if __name__ == "__main__":
    dvi_timings = set_environment_variables()
    this_directory = os.path.dirname(os.path.realpath(__file__))
    design = PolySoc(firmware_path=os.path.join(this_directory, "fw/firmware.bin"),
                     dvi_timings=dvi_timings)
    design.genrust_constants()
    top_level_cli(design)
