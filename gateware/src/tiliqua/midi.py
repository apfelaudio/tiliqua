# Helpers for dealing with MIDI over serial or USB.

from amaranth import *
from amaranth.lib.fifo import SyncFIFOBuffered
from amaranth.lib          import wiring, data, enum
from amaranth.lib.wiring   import In, Out

from amaranth_future import stream
from vendor.serial import AsyncSerialRX

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
    midi_type:    MessageType # 4 bit message type
    midi_channel: unsigned(4) # 4 bit midi channel
    midi_payload: data.UnionLayout({
        "note_off": data.StructLayout({
            "note": unsigned(8),
            "velocity": unsigned(8)
        }),
        "note_on": data.StructLayout({
            "note": unsigned(8),
            "velocity": unsigned(8)
        }),
        "poly_pressure": data.StructLayout({
            "note": unsigned(8),
            "pressure": unsigned(8)
        }),
        "control_change": data.StructLayout({
            "controller_number": unsigned(8),
            "data": unsigned(8)
        }),
        "program_change": data.StructLayout({
            "program_number": unsigned(8),
            "_unused": unsigned(8)
        }),
        "channel_pressure": data.StructLayout({
            "pressure": unsigned(8),
            "_unused": unsigned(8)
        }),
        "pitch_bend": data.StructLayout({
            "lsb": unsigned(8),
            "msb": unsigned(8)
        }),
    })

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
        rstream = stream.fifo_r_stream(self.rx_fifo)
        wiring.connect(m, rstream, wiring.flipped(self.o))

        return m

class MidiDecode(wiring.Component):

    """Convert raw MIDI bytes into a stream of MIDI messages."""

    i: In(stream.Signature(unsigned(8)))
    o: Out(stream.Signature(MidiMessage))

    def elaborate(self, platform):
        m = Module()

        with m.FSM() as fsm:
            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                # all valid command messages have highest bit set
                with m.If(self.i.valid & (self.i.payload & 0x80)):
                    m.d.sync += self.o.payload.as_value()[:8].eq(self.i.payload)
                    m.next = 'READ0'
                    # skip anything that looks suspicious
            with m.State('READ0'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                    m.d.sync += self.o.payload.as_value()[8:16].eq(self.i.payload)
                    m.next = 'READ1'
            with m.State('READ1'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                    m.d.sync += self.o.payload.as_value()[16:24].eq(self.i.payload)
                    m.next = 'WAIT-READY'
            with m.State('WAIT-READY'):
                m.d.comb += self.o.valid.eq(1),
                with m.If(self.o.ready):
                    m.next = 'WAIT-VALID'

        return m
