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
    note:     unsigned(8)
    velocity: unsigned(8)
    gate:     unsigned(1)
    freq_inc: ASQ

class MidiVoiceTracker(wiring.Component):

    """
    Read a stream of MIDI messages. Decode it into `max_voices` independent
    streams, one stream per voice, with voice culling.
    """

    def __init__(self, max_voices=8, mod_wheel_caps_velocity=True, zero_velocity_gate=False):
        self.max_voices = max_voices
        self.mod_wheel_caps_velocity = mod_wheel_caps_velocity
        self.zero_velocity_gate = zero_velocity_gate
        super().__init__({
            "i": In(stream.Signature(MidiMessage)),
            "o": Out(stream.Signature(MidiVoice)).array(max_voices),
        });

    def elaborate(self, platform):
        m = Module()

        # MIDI note -> linearized frequency LUT memory

        lut = []
        sample_rate_hz = 48000
        for i in range(128):
            freq = 440 * 2**((i-69)/12.0)
            freq_inc = freq * (1.0 / sample_rate_hz)
            lut.append(fixed.Const(freq_inc, shape=ASQ)._value)
        m.submodules.f_lut_mem = f_lut_mem = Memory(
                shape=signed(ASQ.as_shape().width), depth=len(lut), init=lut)
        f_lut_rport = f_lut_mem.read_port()

        # Voice state memory

        m.submodules.voice_mem = voice_mem = Memory(
            shape=MidiVoice,
            depth=self.max_voices, init=[])
        voice_rport = voice_mem.read_port()
        voice_wport = voice_mem.write_port()
        cull_rport  = voice_mem.read_port()

        m.d.comb += cull_rport.en.eq(1)

        # State captured on each incoming MIDI message

        msg = Signal(MidiMessage)
        last_cc1 = Signal(8, init=255)

        voice_ix_write = Signal(range(self.max_voices), init=0)

        # Keep as many signals outside of FSM states as possible.

        m.d.comb += [
            f_lut_rport.en.eq(1),
            f_lut_rport.addr.eq(msg.midi_payload.note_on.note),
            voice_wport.data.note.eq(msg.midi_payload.note_on.note),
            voice_wport.data.velocity.eq(msg.midi_payload.note_on.velocity),
            voice_wport.data.gate.eq(1),
            voice_wport.data.freq_inc.eq(f_lut_rport.data),
            voice_rport.en.eq(1),
        ]

        # All outgoing voice streams contain the same payloads to reduce
        # logic usage. Only the stream strobes are iterated over.

        for n in range(self.max_voices):
            m.d.comb += [
                self.o[n].payload.note.eq(voice_rport.data.note),
                self.o[n].payload.velocity.eq(voice_rport.data.velocity),
                self.o[n].payload.gate.eq(voice_rport.data.gate),
                self.o[n].payload.freq_inc.eq(voice_rport.data.freq_inc),
            ]
            if self.mod_wheel_caps_velocity:
                with m.If(last_cc1 < voice_rport.data.velocity):
                    m.d.comb += self.o[n].payload.velocity.eq(last_cc1)


        with m.FSM() as fsm:

            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                    m.d.sync += msg.eq(self.i.payload)
                    with m.Switch(self.i.payload.midi_type):
                        with m.Case(MessageType.NOTE_ON):
                            m.next = 'NOTE-ON'
                        with m.Case(MessageType.NOTE_OFF):
                            m.next = 'NOTE-OFF'
                        with m.Case(MessageType.CONTROL_CHANGE):
                            m.next = 'CONTROL-CHANGE'

            with m.State('NOTE-ON'):
                m.d.comb += voice_wport.en.eq(1)
                m.d.comb += voice_wport.addr.eq(voice_ix_write)
                # Simple round robin selection of voice location
                # TODO: if there is a slot that previously had this note, write
                # the new velocity there for retriggering.
                with m.If(voice_ix_write == self.max_voices - 1):
                    m.d.sync += voice_ix_write.eq(0)
                with m.Else():
                    m.d.sync += voice_ix_write.eq(voice_ix_write + 1)
                m.next = 'WAIT-VALID'

            with m.State('NOTE-OFF'):
                # Cull any voice that matches the MIDI payload note #
                # by walking the voice memory.

                with m.If(cull_rport.addr == self.max_voices - 1):
                    m.d.sync += cull_rport.addr.eq(0)
                    m.next = 'NOTE-OFF-LAST'
                with m.Else():
                    m.d.sync += cull_rport.addr.eq(cull_rport.addr + 1)

                m.d.comb += voice_wport.data.gate.eq(0),
                if self.zero_velocity_gate:
                    m.d.comb += voice_wport.data.velocity.eq(0),
                with m.If(cull_rport.addr > 0):
                    m.d.comb += voice_wport.addr.eq(cull_rport.addr - 1),

                with m.If((cull_rport.data.note == msg.midi_payload.note_off.note)):
                    m.d.comb += voice_wport.en.eq(1),

            with m.State('NOTE-OFF-LAST'):
                m.d.comb += voice_wport.data.gate.eq(0),
                if self.zero_velocity_gate:
                    m.d.comb += voice_wport.data.velocity.eq(0),
                m.d.comb += voice_wport.addr.eq(self.max_voices - 1),
                with m.If((cull_rport.data.note == msg.midi_payload.note_off.note)):
                    m.d.comb += voice_wport.en.eq(1),
                m.next = 'WAIT-VALID'

            with m.State('CONTROL-CHANGE'):
                with m.If(msg.midi_payload.control_change.controller_number == 1):
                    m.d.sync += last_cc1.eq(msg.midi_payload.control_change.data)
                m.next = 'WAIT-VALID'

        with m.FSM() as fsm:

            with m.State('WAIT-READY'):
                with m.Switch(voice_rport.addr):
                    for n in range(self.max_voices):
                        with m.Case(n):
                            m.d.comb += self.o[n].valid.eq(1),
                            with m.If(self.o[n].ready):
                                m.next = 'NEXT'

            with m.State('NEXT'):
                with m.If(voice_rport.addr == self.max_voices - 1):
                    m.d.sync += voice_rport.addr.eq(0)
                with m.Else():
                    m.d.sync += voice_rport.addr.eq(voice_rport.addr + 1)
                m.next = 'WAIT-READ'

            with m.State('WAIT-READ'):
                m.next = 'WAIT-READY'


        return m
