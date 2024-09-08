# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import logging
import os
import sys
import math

from amaranth                                    import *
from amaranth.hdl.rec                            import Record
from amaranth.lib                                import wiring, data, stream
from amaranth.lib.wiring                         import In, Out, connect, flipped

from amaranth_soc                                import csr

from amaranth_future                             import fixed

from tiliqua                                     import eurorack_pmod, dsp, midi
from tiliqua.eurorack_pmod                       import ASQ
from tiliqua.tiliqua_platform                    import TiliquaPlatform, set_environment_variables
from tiliqua.tiliqua_soc                         import TiliquaSoc, top_level_cli

from xbeam.top                                   import VectorTracePeripheral

# TODO: reconcile this with Diffuser in tiliqua.dsp
# it's almost the same, just some coefficients tweaked so it doesn't
# saturate quite as easily.
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

        self.matrix = matrix_mix

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

    i_touch_control: In(unsigned(1))

    i_touch: In(8).array(8)
    i_jack:  In(8)

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
        # TODO: port to lib.memory (for amaranth ~= 0.5)
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

            with m.If(~self.i_touch_control):
                # Filter cutoff on all channels is min(mod wheel, note velocity)
                # Cutoff itself is smoothed by boxcars before being sent to SVF cutoff.
                with m.If(last_cc1 < voice_tracker.o[n].payload.velocity):
                    m.d.comb += boxcars[n].i.payload.sas_value().eq(last_cc1 << 4)
                with m.Else():
                    m.d.comb += boxcars[n].i.payload.sas_value().eq(
                            voice_tracker.o[n].payload.velocity << 4)
                # Connect MIDI voice.note -> note to frequency LUT
                m.d.comb += [
                    rports[n].en.eq(1),
                    rports[n].addr.eq(voice_tracker.o[n].payload.note),
                ]
            with m.Else():
                # only first 6 channels touch sensitive
                if n < 6:
                    with m.If(self.i_jack[n] == 0):
                        m.d.comb += boxcars[n].i.payload.sas_value().eq(self.i_touch[n] << 3)
                    with m.Else():
                        m.d.comb += boxcars[n].i.payload.eq(0)
                # Connect notes from fixed scale for touchsynth
                touch_note_map = [48, 48+7, 48+12, 48+12+3, 48+12+7, 48+24, 0, 0]
                m.d.comb += [
                    rports[n].en.eq(1),
                    rports[n].addr.eq(touch_note_map[n]),
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
        self.diffuser = diffuser

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

class SynthPeripheral(wiring.Component):

    """
    Bridges SoC memory space such that we can peek and poke
    registers of the polysynth engine from our SoC.
    """

    class Drive(csr.Register, access="w"):
        value: csr.Field(csr.action.W, unsigned(16))

    class Reso(csr.Register, access="w"):
        value: csr.Field(csr.action.W, unsigned(16))

    class Voice(csr.Register, access="r"):
        note:   csr.Field(csr.action.R, unsigned(8))
        cutoff: csr.Field(csr.action.R, unsigned(8))

    class Matrix(csr.Register, access="w"):
        """Mixing matrix coefficient: commit on write strobe, MatrixBusy set until done."""
        o_x:   csr.Field(csr.action.W, unsigned(4))
        i_y:   csr.Field(csr.action.W, unsigned(4))
        value: csr.Field(csr.action.W, signed(24))

    class MatrixBusy(csr.Register, access="r"):
        busy: csr.Field(csr.action.R, unsigned(1))

    class TouchControl(csr.Register, access="w"):
        value: csr.Field(csr.action.W, unsigned(1))

    def __init__(self, synth=None):
        self.synth = synth
        regs = csr.Builder(addr_width=5, data_width=8)
        self._drive = regs.add("drive", self.Drive())
        self._reso = regs.add("reso", self.Reso())
        self._voices = [regs.add(f"voices{i}", self.Voice()) for i in range(8)]
        self._matrix = regs.add("matrix", self.Matrix())
        self._matrix_busy = regs.add("matrix_busy", self.MatrixBusy())
        self._touch_control = regs.add("touch_control", self.TouchControl())
        self._bridge = csr.Bridge(regs.as_memory_map())
        super().__init__({
            "bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)

        # top-level tweakables
        with m.If(self._drive.f.value.w_stb):
            m.d.sync += self.synth.drive.eq(self._drive.f.value.w_data)
        with m.If(self._reso.f.value.w_stb):
            m.d.sync += self.synth.reso.eq(self._reso.f.value.w_data)
        with m.If(self._touch_control.f.value.w_stb):
            m.d.sync += self.synth.i_touch_control.eq(self._touch_control.f.value.w_data)

        # voice tracking
        for i, voice in enumerate(self._voices):
            m.d.comb += [
                voice.f.note.r_data  .eq(self.synth.voice_states[i].note),
                voice.f.cutoff.r_data.eq(self.synth.voice_states[i].cutoff)
            ]

        # matrix coefficient update logic
        matrix_busy = Signal()
        m.d.comb += self._matrix_busy.f.busy.r_data.eq(matrix_busy)
        with m.If(self._matrix.element.w_stb & ~matrix_busy):
            m.d.sync += [
                matrix_busy.eq(1),
                self.synth.diffuser.matrix.c.payload.o_x         .eq(self._matrix.f.o_x.w_data),
                self.synth.diffuser.matrix.c.payload.i_y         .eq(self._matrix.f.i_y.w_data),
                self.synth.diffuser.matrix.c.payload.v.as_value().eq(self._matrix.f.value.w_data),
                self.synth.diffuser.matrix.c.valid.eq(1),
            ]
        with m.If(matrix_busy & self.synth.diffuser.matrix.c.ready):
            # coefficient has been written
            m.d.sync += [
                matrix_busy.eq(0),
                self.synth.diffuser.matrix.c.valid.eq(0),
            ]

        return m

class PolySoc(TiliquaSoc):
    def __init__(self, *, firmware_path, dvi_timings):

        # don't finalize the CSR bridge in TiliquaSoc, we're adding more peripherals.
        super().__init__(firmware_path=firmware_path, dvi_timings=dvi_timings, audio_192=False,
                         audio_out_peripheral=False, touch=True, finalize_csr_bridge=False)

        fb_size = (self.video.fb_hsize, self.video.fb_vsize)

        # WARN: TiliquaSoc ends at 0x00000900
        self.vector_periph_base = 0x00001000
        self.synth_periph_base  = 0x00001100

        self.vector_periph = VectorTracePeripheral(
            fb_base=self.video.fb_base,
            fb_size=fb_size,
            bus_dma=self.psram_periph,
            fs=48000,
            n_upsample=8)
        self.csr_decoder.add(self.vector_periph.bus, addr=self.vector_periph_base, name="vector_periph")

        # synth controls
        self.synth_periph = SynthPeripheral()
        self.csr_decoder.add(self.synth_periph.bus, addr=self.synth_periph_base, name="synth_periph")

        # now we can freeze the memory map
        self.finalize_csr_bridge()

    def elaborate(self, platform):

        m = Module()

        m.submodules += self.vector_periph

        m.submodules += self.synth_periph

        m.submodules.polysynth = polysynth = PolySynth()
        self.synth_periph.synth = polysynth

        m.submodules += super().elaborate(platform)

        pmod0 = self.pmod0_periph.pmod

        m.submodules.astream = astream = eurorack_pmod.AudioStream(pmod0)

        # polysynth midi
        midi_pins = platform.request("midi")
        m.submodules.serialrx = serialrx = midi.SerialRx(
                system_clk_hz=60e6, pins=midi_pins)
        m.submodules.midi_decode = midi_decode = midi.MidiDecode()
        wiring.connect(m, serialrx.o, midi_decode.i)
        wiring.connect(m, midi_decode.o, polysynth.i_midi)

        # hook up touch + jack
        m.d.comb += polysynth.i_jack.eq(pmod0.jack)
        m.d.comb += [polysynth.i_touch[n].eq(pmod0.touch[n]) for n in range(0, 8)]

        # polysynth audio
        wiring.connect(m, astream.istream, polysynth.i)
        wiring.connect(m, polysynth.o, astream.ostream)

        # polysynth out -> vectorscope TODO use true split
        m.d.comb += [
            self.vector_periph.i.valid.eq(polysynth.o.valid),
            self.vector_periph.i.payload[0].eq(polysynth.o.payload[2]),
            self.vector_periph.i.payload[1].eq(polysynth.o.payload[3]),
        ]

        # Memory controller hangs if we start making requests to it straight away.
        with m.If(self.permit_bus_traffic):
            m.d.sync += self.vector_periph.en.eq(1)

        return m


if __name__ == "__main__":
    dvi_timings = set_environment_variables()
    this_directory = os.path.dirname(os.path.realpath(__file__))
    design = PolySoc(firmware_path=os.path.join(this_directory, "fw/firmware.bin"),
                     dvi_timings=dvi_timings)
    top_level_cli(design)
