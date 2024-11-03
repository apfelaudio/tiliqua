# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
"""
Extremely bare-bones USB MIDI host demo. EXPERIMENTAL.

***WARN*** This demo hardwires the VBUS output to ON !!! ***WARN***

At the moment this is only used for Tiliqua hardware validation.
NOTE: the MIDI USB configuration and endpoint IDs are hard-coded below.
"""

from amaranth                     import *
from amaranth.build               import *
from amaranth.lib.cdc             import FFSynchronizer

from amaranth_future              import fixed

from tiliqua.usb_host             import *
from tiliqua.cli                  import top_level_cli
from tiliqua.tiliqua_platform     import RebootProvider
from tiliqua.eurorack_pmod        import ASQ
from tiliqua                      import midi, eurorack_pmod

from vendor.ila                   import AsyncSerialILA

class MidiCVTop(wiring.Component):

    """
    Simple monophonic MIDI to CV conversion.

    in: (USB MIDI HOST)
    out0: Gate
    out1: V/oct CV
    out2: Velocity
    out3: Mod Wheel (CC1)
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

class USB2HostTest(Elaboratable):

    #
    # FIXME: hardcoded device properties
    #
    # You can get this by looking at the device descriptors
    # on a PC --> Find an 'Interface descriptor' with subclass
    # 0x03 (MIDI Streaming). The parent configuration ID is the
    # correct configuration ID. The IN (bulk) endpoint ID is the
    # MIDI BULK endpoint ID.
    #
    # These will not be hardcoded when this demo is finished.
    #

    _HARDCODE_DEVICE_CONFIGURATION_ID = 1
    _HARDCODE_MIDI_BULK_ENDPOINT_ID   = 1

    def __init__(self, **kwargs):
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = car = platform.clock_domain_generator()
        m.submodules.reboot = reboot = RebootProvider(car.clocks_hz["sync"])
        m.submodules.btn = FFSynchronizer(
                platform.request("encoder").s.i, reboot.button)

        ulpi = platform.request(platform.default_usb_connection)
        m.submodules.usb = usb = SimpleUSBMIDIHost(
                bus=ulpi,
                hardcoded_configuration_id=self._HARDCODE_DEVICE_CONFIGURATION_ID,
                hardcoded_midi_endpoint=self._HARDCODE_MIDI_BULK_ENDPOINT_ID,
        )

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True,
                touch_enabled=False)

        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)
        m.submodules.midi_cv = self.midi_cv = MidiCVTop()
        wiring.connect(m, audio_stream.istream, self.midi_cv.i)
        wiring.connect(m, self.midi_cv.o, audio_stream.ostream)

        m.submodules.midi_decode = midi_decode = midi.MidiDecode()
        wiring.connect(m, midi_decode.o, self.midi_cv.i_midi)

        m.d.comb += midi_decode.i.payload.eq(usb.receiver.stream.payload)

        # FIXME: this should be cleaned up and moved to an independent component
        with m.FSM(domain="usb"):
            with m.State('IDLE'):
                with m.If(usb.midi_bulk_in & usb.receiver.stream.valid):
                    m.next = 'MIDI-RX'
            with m.State('MIDI-RX'):
                cnt = Signal(2)
                m.d.comb += midi_decode.i.valid.eq(usb.receiver.stream.valid)
                with m.If(midi_decode.i.valid):
                    m.d.usb += cnt.eq(cnt+1)
                with m.If(~usb.midi_bulk_in | (cnt == 2)):
                    m.d.usb += cnt.eq(0)
                    m.next = 'IDLE'

        # XXX: this demo enables VBUS output
        m.d.comb += platform.request("usb_vbus_en").o.eq(1)

        if platform.ila:
            test_signal = Signal(16, reset=0xFEED)

            ila_signals = [
                test_signal,
                usb.translator.tx_valid,
                usb.translator.tx_data,
                usb.translator.tx_ready,
                usb.translator.rx_valid,
                usb.translator.rx_data,
                usb.translator.rx_active,
                usb.translator.busy,
                usb.receiver.packet_complete,
                usb.receiver.crc_mismatch,
                usb.handshake_detector.detected.ack,
                usb.handshake_detector.detected.nak,
                usb.handshake_detector.detected.stall,
                usb.handshake_detector.detected.nyet,
                usb.midi_bulk_in,
                usb.receiver.stream.valid,
                usb.receiver.stream.payload,
                midi_decode.o.valid,
                midi_decode.o.payload.as_value(),
            ]

            self.ila = AsyncSerialILA(signals=ila_signals,
                                      sample_depth=8192, divisor=521,
                                      domain='usb', sample_rate=60e6) # ~115200 baud on USB clock
            m.submodules += self.ila

            m.d.comb += [
                self.ila.trigger.eq(usb.midi_bulk_in),
                platform.request("uart").tx.o.eq(self.ila.tx),
            ]

        return m

if __name__ == "__main__":
    top_level_cli(USB2HostTest, video_core=False, ila_supported=True)
