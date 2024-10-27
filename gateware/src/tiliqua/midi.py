# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Helpers for dealing with MIDI over serial or USB."""

from amaranth              import *
from amaranth.lib.fifo     import SyncFIFOBuffered
from amaranth.lib          import wiring, data, enum, stream
from amaranth.lib.wiring   import In, Out
from amaranth.lib.memory   import Memory

from amaranth_stdio.serial import AsyncSerialRX

from amaranth_future       import fixed
from tiliqua.eurorack_pmod import ASQ # hardware native fixed-point sample type

MIDI_BAUD_RATE = 31250

class MessageType(enum.Enum, shape=unsigned(4)):
    NOTE_OFF         = 0x8
    NOTE_ON          = 0x9
    POLY_PRESSURE    = 0xA
    CONTROL_CHANGE   = 0xB
    PROGRAM_CHANGE   = 0xC
    CHANNEL_PRESSURE = 0xD
    PITCH_BEND       = 0xE
    SYSEX            = 0xF

class MidiMessage(data.Struct):
    midi_payload: data.UnionLayout({
        "note_off": data.StructLayout({
            "velocity": unsigned(8),
            "note": unsigned(8),
        }),
        "note_on": data.StructLayout({
            "velocity": unsigned(8),
            "note": unsigned(8),
        }),
        "poly_pressure": data.StructLayout({
            "pressure": unsigned(8),
            "note": unsigned(8),
        }),
        "control_change": data.StructLayout({
            "data": unsigned(8),
            "controller_number": unsigned(8),
        }),
        "program_change": data.StructLayout({
            "_unused": unsigned(8),
            "program_number": unsigned(8),
        }),
        "channel_pressure": data.StructLayout({
            "_unused": unsigned(8),
            "pressure": unsigned(8),
        }),
        "pitch_bend": data.StructLayout({
            "msb": unsigned(8),
            "lsb": unsigned(8),
        }),
    })
    midi_channel: unsigned(4) # 4 bit midi channel
    midi_type:    MessageType # 4 bit message type

class SerialRx(wiring.Component):

    """Stream of raw bytes from a serial port at MIDI baud rates."""

    o: Out(stream.Signature(unsigned(8)))

    def __init__(self, *, system_clk_hz, pins, rx_depth=64):

        self.phy = AsyncSerialRX(
            divisor=int(system_clk_hz // MIDI_BAUD_RATE),
            pins=pins)
        self.rx_fifo = SyncFIFOBuffered(
            width=self.phy.data.width, depth=rx_depth)

        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules._phy = self.phy
        m.submodules._rx_fifo = self.rx_fifo

        # serial PHY -> RX FIFO
        m.d.comb += [
            self.rx_fifo.w_data.eq(self.phy.data),
            self.rx_fifo.w_en.eq(self.phy.rdy),
            self.phy.ack.eq(self.rx_fifo.w_rdy),
        ]

        # RX FIFO -> output stream
        wiring.connect(m, self.rx_fifo.r_stream, wiring.flipped(self.o))

        return m

class MidiDecode(wiring.Component):

    """Convert raw MIDI bytes into a stream of MIDI messages."""

    i: In(stream.Signature(unsigned(8)))
    o: Out(stream.Signature(MidiMessage))

    def elaborate(self, platform):
        m = Module()

        # If we're half-way through a message and don't get the rest of it
        # for this timeout, we give up and ignore the message.
        timeout = Signal(24)
        timeout_cycles = 60000 # 1msec
        m.d.sync += timeout.eq(timeout-1)

        with m.FSM() as fsm:
            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                # all valid command messages have highest bit set
                with m.If(self.i.valid & self.i.payload[7]):
                    m.d.sync += timeout.eq(timeout_cycles)
                    m.d.sync += self.o.payload.as_value()[16:24].eq(self.i.payload)
                    # TODO: handle 0-byte payload messages
                    m.next = 'READ0'
                    # skip anything that doesn't look like a command message
            with m.State('READ0'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(timeout == 0):
                    m.next = 'WAIT-VALID'
                with m.Elif(self.i.valid):
                    m.d.sync += self.o.payload.as_value()[8:16].eq(self.i.payload)
                    with m.Switch(self.o.payload.midi_type):
                        # 1-byte payload
                        with m.Case(MessageType.CHANNEL_PRESSURE,
                                    MessageType.PROGRAM_CHANGE):
                            m.next = 'WAIT-READY'
                        # 2-byte payload
                        with m.Default():
                            m.next = 'READ1'
            with m.State('READ1'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(timeout == 0):
                    m.next = 'WAIT-VALID'
                with m.Elif(self.i.valid):
                    m.d.sync += self.o.payload.as_value()[:8].eq(self.i.payload)
                    m.next = 'WAIT-READY'
            with m.State('WAIT-READY'):
                # TODO: skip if it's a command we don't know how to parse.
                m.d.comb += self.o.valid.eq(1),
                with m.If(self.o.ready):
                    m.next = 'WAIT-VALID'

        return m

class MidiVoice(data.Struct):
    note:         unsigned(8)
    velocity:     unsigned(8)
    gate:         unsigned(1)
    freq_inc:     ASQ
    velocity_mod: unsigned(8)

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
    """

    def __init__(self, max_voices=8, velocity_mod=False, zero_velocity_gate=False):
        self.max_voices = max_voices
        self.velocity_mod = velocity_mod
        self.zero_velocity_gate = zero_velocity_gate
        super().__init__({
            "i": In(stream.Signature(MidiMessage)),
            "o": Out(MidiVoice).array(max_voices),
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
        last_pb = Signal(shape=ASQ)    # last pitch bend position

        # write index for NOTE_ON select + commit
        voice_ix_write = Signal(range(self.max_voices), init=0)

        # voice mask (binary 1 is for an occupied voice slot)
        voice_mask = Signal(self.max_voices)

        # freq / mod / pb update index
        ix_update = Signal(range(self.max_voices))

        # FSM to process incoming MIDI messages one at a time and update
        # internal memories based on these messagse.

        with m.FSM() as fsm:

            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                    m.d.sync += msg.eq(self.i.payload)
                    with m.Switch(self.i.payload.midi_type):
                        with m.Case(MessageType.NOTE_ON):
                            m.d.sync += voice_ix_write.eq(0)
                            m.next = 'NOTE-ON-SELECT'
                        with m.Case(MessageType.NOTE_OFF):
                            m.next = 'NOTE-OFF'
                        with m.Case(MessageType.CONTROL_CHANGE):
                            m.next = 'CONTROL-CHANGE'
                        with m.Case(MessageType.PITCH_BEND):
                            m.next = 'PITCH-BEND'
                        with m.Case(MessageType.POLY_PRESSURE):
                            m.next = 'POLY-PRESSURE'

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
                                self.o[n].note.eq(msg.midi_payload.note_on.note),
                                self.o[n].velocity.eq(msg.midi_payload.note_on.velocity),
                                self.o[n].gate.eq(1),
                            ]
                            if not self.velocity_mod:
                                m.d.sync += self.o[n].velocity_mod.eq(msg.midi_payload.note_on.velocity)
                m.next = 'UPDATE'

            with m.State('NOTE-OFF'):
                # cull any voice that matches the MIDI payload note #
                for n in range(self.max_voices):
                    with m.If(self.o[n].note == msg.midi_payload.note_off.note):
                        m.d.sync += [
                            voice_mask.bit_select(n, 1).eq(0),
                            self.o[n].gate.eq(0),
                        ]
                        if self.zero_velocity_gate:
                            m.d.sync += self.o[n].velocity.eq(0)
                        else:
                            m.d.sync += self.o[n].velocity.eq(msg.midi_payload.note_off.velocity)
                m.next = 'UPDATE'

            with m.State('POLY-PRESSURE'):
                # update any voice that matches the MIDI payload note #
                # TODO: rather than piggybacking on velocity, this should probably be its own field..
                for n in range(self.max_voices):
                    with m.If(self.o[n].note == msg.midi_payload.poly_pressure.note):
                        m.d.sync += self.o[n].velocity.eq(msg.midi_payload.poly_pressure.pressure)
                m.next = 'UPDATE'

            with m.State('CONTROL-CHANGE'):
                with m.If(msg.midi_payload.control_change.controller_number == 1):
                    m.d.sync += last_cc1.eq(msg.midi_payload.control_change.data)
                with m.If(msg.midi_payload.control_change.controller_number == 123):
                    # all stop
                    for n in range(self.max_voices):
                        m.d.sync += self.o[n].gate.eq(0)
                        if self.zero_velocity_gate:
                            m.d.sync += self.o[n].velocity.eq(0)
                m.next = 'UPDATE'

            with m.State('PITCH-BEND'):
                # convert 14-bit pitch bend to 16-bit signed ASQ -1 .. 1
                pb = Signal(signed(16))
                m.d.comb += pb.eq(Cat(msg.midi_payload.pitch_bend.lsb,
                                      msg.midi_payload.pitch_bend.msb))
                m.d.sync += last_pb.raw().eq(pb-(2*8192))
                m.next = 'UPDATE'

            with m.State('UPDATE'):
                # set LUT not address so we can calculate frequency from it
                with m.Switch(ix_update):
                    for n in range(self.max_voices):
                        with m.Case(n):
                            m.d.comb += f_lut_rport.addr.eq(self.o[n].note),
                m.next = 'UPDATE-FREQ-VEL'

            with m.State('UPDATE-FREQ-VEL'):

                # Update linear frequency and velocity based on note values,
                # pitch bend and (optionally) mod wheel.

                # pitch bend factor
                pb_factor = fixed.Const(0.1225, shape=ASQ)
                pb_scaled = Signal(shape=ASQ)
                m.d.comb += pb_scaled.eq(pb_factor * last_pb)

                # linearized frequency from LUT * pitch bend
                calculated_freq = Signal(ASQ)
                f_inc_base = Signal(ASQ)
                m.d.comb += [
                    f_inc_base.raw().eq(f_lut_rport.data),
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
                                with m.If(last_cc1 < self.o[n].velocity):
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
