# Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""MIDI voice tracking, allocation and culling."""

from amaranth import *
from amaranth.lib import data, stream, wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

from amaranth_future import fixed

from ..dsp import ASQ
from .types import *

class MidiVoice(data.Struct):
    note:         unsigned(8)
    velocity:     unsigned(8)
    gate:         unsigned(1)
    freq_inc:     ASQ
    velocity_mod: unsigned(8)
    channel:      unsigned(4)

class MidiVoiceTracker(wiring.Component):

    """
    Read a stream of MIDI messages. Decode it into :py:`max_voices` independent
    :py:`MidiVoice` registers, one per voice, with voice culling.

    After each :py:`NOTE_ON` event, a voice is selected, its :py:`MidiVoice.note` is set,
    the :py:`MidiVoice.gate` attribute is set to 1, and `freq_inc` (linearized
    frequency used for NCOs) is calculated.

    Pitch bend constantly updates :py:`freq_inc` on all channels. Mod wheel may optionally
    be used to cap velocity outputs on all channels using :py:`velocity_mod`.

    After each :py:`NOTE_OFF` event, :py:`MidiVoice.gate` is set to 0. If :py:`zero_velocity_gate`
    is set, the velocity is also set to 0 (instead of the MIDI release velocity).

    Sustain pedal (CC64) is supported: while held, NOTE_OFF events mark voices as
    sustained instead of clearing their gates. When the pedal is released, all
    sustained voices have their gates cleared.
    """

    def __init__(self, max_voices=8, velocity_mod=False, zero_velocity_gate=False,
                 master_channel=0):
        self.max_voices = max_voices
        self.velocity_mod = velocity_mod
        self.zero_velocity_gate = zero_velocity_gate
        self.master_channel = master_channel
        super().__init__({
            "i": In(stream.Signature(MidiMessage)),
            "voice_active": In(data.ArrayLayout(unsigned(1), max_voices)),
            "o": Out(MidiVoice).array(max_voices),
            "mpe_en": In(unsigned(1)),
            "pb_scale": In(unsigned(8), init=2),
        });

    def elaborate(self, platform):
        m = Module()

        # MIDI note -> linearized frequency LUT memory (exponential converter)

        lut = []
        sample_rate_hz = 48000
        for i in range(128):
            freq = 440 * 2**((i-69)/12.0)
            freq_inc = freq * (1.0 / sample_rate_hz)
            lut.append(fixed.Const(freq_inc, shape=ASQ)._value)
        m.submodules.f_lut_mem = f_lut_mem = Memory(
                shape=signed(ASQ.as_shape().width), depth=len(lut), init=lut)
        f_lut_rport = f_lut_mem.read_port()
        m.d.comb += f_lut_rport.en.eq(1)

        # State captured on each incoming MIDI message

        msg = Signal(MidiMessage)      # last MIDI message
        last_cc1 = Signal(8, init=255) # last cc1 (mod wheel) position
        pb_voice = [Signal(signed(18), name=f"pb_voice_{n}")
                    for n in range(self.max_voices)]

        pb_factor = fixed.Const(2**(1/12) - 1, shape=ASQ)
        pb_scaled = Signal(shape=ASQ)

        # write index for NOTE_ON select + commit
        voice_ix_write = Signal(range(self.max_voices), init=0)

        # voice mask (binary 1 is for an occupied voice slot)
        voice_mask = Signal(self.max_voices)

        # sustain pedal (CC64) state
        sustain_held = Signal()
        sustain_mask = Signal(self.max_voices)

        # freq / mod / pb update index
        ix_update = Signal(range(self.max_voices))

        mpe_press  = [Signal(7, name=f"mpe_press_{n}")
                      for n in range(self.max_voices)]
        mpe_timbre = [Signal(7, name=f"mpe_timbre_{n}")
                      for n in range(self.max_voices)]

        # FSM to process incoming MIDI messages one at a time and update
        # internal memories based on these messagse.

        with m.FSM() as fsm:

            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                    m.d.sync += msg.eq(self.i.payload)
                    with m.Switch(self.i.payload.status.kind):
                        with m.Case(Status.Kind.NOTE_ON):
                            with m.If(self.i.payload.midi_payload.note_on.velocity == 0):
                                # According to the MIDI standard, a device may transmit a
                                # NOTE_ON with velocity=0, and this should be treated exactly
                                # the same as a note OFF.
                                m.next = 'NOTE-OFF'
                            with m.Else():
                                m.d.sync += voice_ix_write.eq(0)
                                m.next = 'NOTE-ON-MATCH'
                        with m.Case(Status.Kind.NOTE_OFF):
                            m.next = 'NOTE-OFF'
                        with m.Case(Status.Kind.CONTROL_CHANGE):
                            m.next = 'CONTROL-CHANGE'
                        with m.Case(Status.Kind.PITCH_BEND):
                            m.next = 'PITCH-BEND'
                        with m.Case(Status.Kind.POLY_PRESSURE):
                            m.next = 'POLY-PRESSURE'
                        with m.Case(Status.Kind.CHANNEL_PRESSURE):
                            m.next = 'CHANNEL-PRESSURE'
                        with m.Default():
                            m.next = 'WAIT-VALID'

            with m.State('NOTE-ON-MATCH'):
                # prefer reusing a voice slot that already holds the same note,
                # so the ADSR retrigger lands on the correct envelope state.
                match_found = Signal()
                with m.Switch(voice_ix_write):
                    for n in range(self.max_voices):
                        with m.Case(n):
                            m.d.comb += match_found.eq(
                                (self.o[n].note == msg.midi_payload.note_on.note) &
                                (~self.mpe_en |
                                 (self.o[n].channel == msg.status.nibble.channel)))
                with m.If(match_found):
                    m.next = 'NOTE-ON-COMMIT'
                with m.Elif(voice_ix_write == self.max_voices - 1):
                    m.d.sync += voice_ix_write.eq(0)
                    m.next = 'NOTE-ON-SELECT-IDLE'
                with m.Else():
                    m.d.sync += voice_ix_write.eq(voice_ix_write + 1)

            with m.State('NOTE-ON-SELECT-IDLE'):
                # prefer a free slot whose ADSR has fully decayed
                slot_active = Signal()
                with m.Switch(voice_ix_write):
                    for n in range(self.max_voices):
                        with m.Case(n):
                            m.d.comb += slot_active.eq(self.voice_active[n])
                with m.If(~voice_mask.bit_select(voice_ix_write, 1) & ~slot_active):
                    m.next = 'NOTE-ON-COMMIT'
                with m.Elif(voice_ix_write == self.max_voices - 1):
                    m.d.sync += voice_ix_write.eq(0)
                    m.next = 'NOTE-ON-SELECT'
                with m.Else():
                    m.d.sync += voice_ix_write.eq(voice_ix_write + 1)

            with m.State('NOTE-ON-SELECT'):
                # find an empty note slot to write to
                # warn: need at least 1 clock for freq LUT RAM output to update
                # so best not to commit from the same FSM state.
                with m.If(~voice_mask.bit_select(voice_ix_write, 1)):
                    m.next = 'NOTE-ON-COMMIT'
                with m.Else():
                    m.d.sync += voice_ix_write.eq(voice_ix_write + 1)
                    with m.If(voice_ix_write == self.max_voices - 1):
                        # no free note slots
                        m.next = 'WAIT-VALID'

            with m.State('NOTE-ON-COMMIT'):
                # commit the new note to the found slot
                with m.Switch(voice_ix_write):
                    for n in range(self.max_voices):
                        with m.Case(n):
                            m.d.sync += [
                                voice_mask.bit_select(n, 1).eq(1),
                                sustain_mask.bit_select(n, 1).eq(0),
                                self.o[n].note.eq(msg.midi_payload.note_on.note),
                                self.o[n].velocity.eq(msg.midi_payload.note_on.velocity),
                                self.o[n].gate.eq(1),
                                self.o[n].channel.eq(msg.status.nibble.channel),
                            ]
                            m.d.sync += [
                                mpe_press[n].eq(msg.midi_payload.note_on.velocity),
                                mpe_timbre[n].eq(0),
                            ]
                            with m.If(self.mpe_en &
                                      (self.o[n].channel != msg.status.nibble.channel)):
                                m.d.sync += pb_voice[n].eq(0)
                            if not self.velocity_mod:
                                m.d.sync += self.o[n].velocity_mod.eq(msg.midi_payload.note_on.velocity)
                m.next = 'UPDATE'

            with m.State('NOTE-OFF'):
                # cull any voice that matches the MIDI payload note #
                for n in range(self.max_voices):
                    with m.If((self.o[n].note == msg.midi_payload.note_off.note) &
                              (~self.mpe_en |
                               (self.o[n].channel == msg.status.nibble.channel))):
                        with m.If(sustain_held):
                            # pedal held: keep gate, mark for deferred release
                            m.d.sync += sustain_mask.bit_select(n, 1).eq(1)
                        with m.Else():
                            m.d.sync += [
                                voice_mask.bit_select(n, 1).eq(0),
                                self.o[n].gate.eq(0),
                            ]
                            if self.zero_velocity_gate:
                                m.d.sync += self.o[n].velocity.eq(0)
                m.next = 'UPDATE'

            with m.State('POLY-PRESSURE'):
                # update any voice that matches the MIDI payload note #
                # TODO: rather than piggybacking on velocity, this should probably be its own field?
                for n in range(self.max_voices):
                    with m.If((self.o[n].note == msg.midi_payload.poly_pressure.note) & self.o[n].gate):
                        m.d.sync += self.o[n].velocity.eq(msg.midi_payload.poly_pressure.pressure)
                m.next = 'UPDATE'

            with m.State('CHANNEL-PRESSURE'):
                with m.If(self.mpe_en):
                    for n in range(self.max_voices):
                        with m.If((self.o[n].channel == msg.status.nibble.channel) &
                                  self.o[n].gate):
                            m.d.sync += mpe_press[n].eq(
                                msg.midi_payload.channel_pressure.pressure)
                m.next = 'UPDATE'

            with m.State('CONTROL-CHANGE'):
                with m.If(self.mpe_en &
                          (msg.midi_payload.control_change.controller_number == 74)):
                    for n in range(self.max_voices):
                        with m.If((self.o[n].channel == msg.status.nibble.channel) &
                                  self.o[n].gate):
                            m.d.sync += mpe_timbre[n].eq(
                                msg.midi_payload.control_change.data)
                with m.If((msg.midi_payload.control_change.controller_number == 1) &
                          (msg.midi_payload.control_change.data != 0)):
                    m.d.sync += last_cc1.eq(msg.midi_payload.control_change.data)
                with m.If(msg.midi_payload.control_change.controller_number == 64):
                    # sustain pedal
                    with m.If(msg.midi_payload.control_change.data >= 64):
                        m.d.sync += sustain_held.eq(1)
                    with m.Else():
                        m.d.sync += sustain_held.eq(0)
                        # release all sustained voices
                        for n in range(self.max_voices):
                            with m.If(sustain_mask.bit_select(n, 1)):
                                m.d.sync += [
                                    voice_mask.bit_select(n, 1).eq(0),
                                    sustain_mask.bit_select(n, 1).eq(0),
                                    self.o[n].gate.eq(0),
                                ]
                                if self.zero_velocity_gate:
                                    m.d.sync += self.o[n].velocity.eq(0)
                with m.If(msg.midi_payload.control_change.controller_number == 123):
                    # all stop
                    for n in range(self.max_voices):
                        m.d.sync += self.o[n].gate.eq(0)
                        if self.zero_velocity_gate:
                            m.d.sync += self.o[n].velocity.eq(0)
                    m.d.sync += sustain_held.eq(0)
                    m.d.sync += sustain_mask.eq(0)
                m.next = 'UPDATE'

            with m.State('PITCH-BEND'):
                pb = Signal(signed(15))
                m.d.comb += pb.eq(Cat(msg.midi_payload.pitch_bend.lsb[:7],
                                      msg.midi_payload.pitch_bend.msb[:7]) - 8192)
                pb_semis = Signal(signed(18))
                m.d.comb += pb_semis.eq((pb * self.pb_scale) >> 5)
                with m.If(self.mpe_en &
                          (msg.status.nibble.channel != self.master_channel)):
                    for n in range(self.max_voices):
                        with m.If(self.o[n].channel == msg.status.nibble.channel):
                            m.d.sync += pb_voice[n].eq(pb_semis)
                with m.Else():
                    for n in range(self.max_voices):
                        m.d.sync += pb_voice[n].eq(pb_semis)
                m.next = 'UPDATE'

            with m.State('UPDATE'):
                # set LUT not address so we can calculate frequency from it
                pb_cur = Signal(signed(18))
                note_cur = Signal(8)
                with m.Switch(ix_update):
                    for n in range(self.max_voices):
                        with m.Case(n):
                            m.d.comb += [
                                note_cur.eq(self.o[n].note),
                                pb_cur.eq(pb_voice[n]),
                            ]
                bent_note = Signal(signed(12))
                m.d.comb += bent_note.eq(note_cur + (pb_cur >> 8))
                with m.If(bent_note < 0):
                    m.d.comb += f_lut_rport.addr.eq(0)
                with m.Elif(bent_note > 127):
                    m.d.comb += f_lut_rport.addr.eq(127)
                with m.Else():
                    m.d.comb += f_lut_rport.addr.eq(bent_note)
                pb_residue = Signal(fixed.UQ(0, 8))
                m.d.comb += pb_residue.as_value().eq(pb_cur[:8])
                m.d.sync += pb_scaled.eq(pb_factor * pb_residue)
                m.next = 'UPDATE-FREQ-VEL'

            with m.State('UPDATE-FREQ-VEL'):

                # Update linear frequency and velocity based on note values,
                # pitch bend and (optionally) mod wheel.

                # linearized frequency from LUT * pitch bend
                calculated_freq = Signal(ASQ)
                f_inc_base = Signal(ASQ)
                m.d.comb += [
                    f_inc_base.as_value().eq(f_lut_rport.data),
                    calculated_freq.eq(f_inc_base + f_inc_base*pb_scaled),
                ]

                # latch to correct output register
                with m.Switch(ix_update):
                    for n in range(self.max_voices):
                        with m.Case(n):
                            # latch linear frequency + pitch bend
                            m.d.sync += self.o[n].freq_inc.eq(calculated_freq)
                            # optional mod wheel caps `velocity_mod` field.
                            if self.velocity_mod:
                                with m.If(self.mpe_en):
                                    m.d.sync += self.o[n].velocity_mod.eq(
                                        mpe_press[n] + mpe_timbre[n])
                                with m.Elif(last_cc1 < self.o[n].velocity):
                                    m.d.sync += self.o[n].velocity_mod.eq(last_cc1)
                                with m.Else():
                                    m.d.sync += self.o[n].velocity_mod.eq(self.o[n].velocity)

                # Check if we've updated every slot.
                m.d.sync += ix_update.eq(ix_update + 1)
                with m.If(ix_update == self.max_voices - 1):
                    m.next = 'WAIT-VALID'
                with m.Else():
                    m.next = 'UPDATE'

        return m
