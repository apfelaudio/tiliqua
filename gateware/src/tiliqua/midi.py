# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Helpers for dealing with MIDI over serial or USB."""

from amaranth              import *
from amaranth.lib.fifo     import SyncFIFOBuffered
from amaranth.lib          import wiring, data, enum, stream
from amaranth.lib.wiring   import In, Out

from vendor.serial         import AsyncSerialRX

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

class MidiVoiceTracker(wiring.Component):

    """
    Read a stream of MIDI messages. Decode it into `max_voices` independent
    streams, one stream per voice, with voice culling. Outgoing streams have
    'valid' wired to 1, be careful how you synchronize them.
    """

    def __init__(self, max_voices=8):
        self.max_voices = max_voices
        super().__init__({
            "i": In(stream.Signature(MidiMessage)),
            "o": Out(stream.Signature(MidiVoice)).array(max_voices),
        });

    def elaborate(self, platform):
        m = Module()

        c_voice = Signal(range(self.max_voices))
        msg = Signal(MidiMessage)

        for n in range(self.max_voices):
            m.d.comb += self.o[n].valid.eq(1)

        with m.FSM() as fsm:

            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                    with m.Switch(self.i.payload.midi_type):
                        with m.Case(MessageType.NOTE_ON):
                            m.d.sync += msg.eq(self.i.payload)
                            m.next = 'NOTE-ON'
                        with m.Case(MessageType.NOTE_OFF):
                            m.d.sync += msg.eq(self.i.payload)
                            m.next = 'NOTE-OFF'

            with m.State('NOTE-ON'):
                # Simple round robin selection of voice location
                # TODO: if there is a slot that previously had this note, write
                # the new velocity there for retriggering.
                with m.If(c_voice == self.max_voices - 1):
                    m.d.sync += c_voice.eq(0)
                with m.Else():
                    m.d.sync += c_voice.eq(c_voice + 1)
                # Set voice in current location to MIDI payload attributes
                with m.Switch(c_voice):
                    for n in range(self.max_voices):
                        with m.Case(n):
                            m.d.sync += [
                                self.o[n].payload.note.eq(msg.midi_payload.note_on.note),
                                self.o[n].payload.velocity.eq(msg.midi_payload.note_on.velocity),
                            ]
                m.next = 'WAIT-VALID'

            with m.State('NOTE-OFF'):
                # Cull any voice that matches the MIDI payload note #
                for n in range(self.max_voices):
                    with m.If(self.o[n].payload.note == msg.midi_payload.note_on.note):
                        m.d.sync += self.o[n].payload.velocity.eq(0)
                m.next = 'WAIT-VALID'

        return m
