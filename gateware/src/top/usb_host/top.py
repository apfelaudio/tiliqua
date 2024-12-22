# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
"""
Extremely bare-bones USB MIDI host demo. EXPERIMENTAL.

***WARN*** This demo hardwires the VBUS output to ON !!! ***WARN***

At the moment this is only used for Tiliqua hardware validation.
NOTE: the MIDI USB configuration and endpoint IDs are hard-coded below.

At the moment, all the MIDI traffic is routed to CV outputs according
to the existing example (see docstring) in `top/dsp:MidiCVTop`.
"""

import sys

from amaranth                     import *
from amaranth.build               import *
from amaranth.lib.cdc             import FFSynchronizer

from amaranth_future              import fixed

from tiliqua                      import midi, eurorack_pmod
from tiliqua.usb_host             import *
from tiliqua.cli                  import top_level_cli
from tiliqua.tiliqua_platform     import RebootProvider
from vendor.ila                   import AsyncSerialILA

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

# These can be selected at top-level CLI.
MIDI_DEVICES = {
    # (name):                 (usb configuration_id, usb_midi_endpoint_id)
    "yamaha-cp73":            (1, 2),
    "arturia-keylab49-mkii":  (1, 1),
}

class USB2HostTest(Elaboratable):

    brief = "USB host MIDI to CV conversion (EXPERIMENT)."

    def __init__(self, usb_device_config_id, usb_midi_bulk_endp_id):
        self.usb_device_config_id = usb_device_config_id
        self.usb_midi_bulk_endp_id = usb_midi_bulk_endp_id
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
                hardcoded_configuration_id=self.usb_device_config_id,
                hardcoded_midi_endpoint=self.usb_midi_bulk_endp_id,
        )


        m.submodules.midi_decode = midi_decode = midi.MidiDecode(usb=True)
        wiring.connect(m, usb.o_midi_bytes, midi_decode.i)

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True,
                touch_enabled=False)
        m.d.comb += pmod0.codec_mute.eq(reboot.mute)


        m.submodules.audio_stream = audio_stream = eurorack_pmod.AudioStream(pmod0)
        m.submodules.midi_cv = self.midi_cv = midi.MonoMidiCV()
        wiring.connect(m, audio_stream.istream, self.midi_cv.i)
        wiring.connect(m, self.midi_cv.o, audio_stream.ostream)
        wiring.connect(m, midi_decode.o, self.midi_cv.i_midi)

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
                usb.receiver.stream.payload,
                usb.receiver.stream.next,
                usb.o_midi_bytes.valid,
                usb.o_midi_bytes.payload,
                midi_decode.o.payload.as_value(),
                midi_decode.o.valid,
                usb.midi_fifo.r_level,
                usb.handshake_detector.detected.ack,
                usb.handshake_detector.detected.nak,
                usb.handshake_detector.detected.stall,
                usb.handshake_detector.detected.nyet,
            ]

            self.ila = AsyncSerialILA(signals=ila_signals,
                                      sample_depth=8192, divisor=521,
                                      domain='usb', sample_rate=60e6) # ~115200 baud on USB clock
            m.submodules += self.ila

            m.d.comb += [
                self.ila.trigger.eq(midi_decode.o.payload.midi_type == midi.MessageType.NOTE_ON),
                platform.request("uart").tx.o.eq(self.ila.tx),
            ]

        return m

def argparse_callback(parser):
    parser.add_argument('--midi-device', type=str, default=None,
                        help=f"One of {list(MIDI_DEVICES)}")

def argparse_fragment(args):
    # Additional arguments to be provided to CoreTop
    if args.midi_device not in MIDI_DEVICES:
        print(f"provided '--midi-device {args.midi_device}' is not one of {list(MIDI_DEVICES)}")
        sys.exit(-1)

    config_id, endp_id = MIDI_DEVICES[args.midi_device]
    return {
        "usb_device_config_id": config_id,
        "usb_midi_bulk_endp_id": endp_id,
    }

if __name__ == "__main__":
    top_level_cli(
        USB2HostTest,
        video_core=False,
        ila_supported=True,
        argparse_callback=argparse_callback,
        argparse_fragment=argparse_fragment,
    )
