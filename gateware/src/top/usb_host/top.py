# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
"""
Extremely bare-bones USB MIDI host demo. EXPERIMENTAL.

***WARN*** This demo hardwires the VBUS output to ON !!! ***WARN***

At the moment this is only used for Tiliqua hardware validation.
NOTE: the MIDI USB configuration and endpoint IDs are hard-coded below.

At the moment, all the MIDI traffic does is blink an LED.
A better demo would run a MIDI/CV conversion as we do for TRS MIDI.
"""


from amaranth                     import *
from amaranth.build               import *
from amaranth.lib.cdc             import FFSynchronizer

from tiliqua.usb_host             import *
from tiliqua.cli                  import top_level_cli
from tiliqua.tiliqua_platform     import RebootProvider
from vendor.ila                   import AsyncSerialILA

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
            ]

            self.ila = AsyncSerialILA(signals=ila_signals,
                                      sample_depth=8192, divisor=521,
                                      domain='usb', sample_rate=60e6) # ~115200 baud on USB clock
            m.submodules += self.ila

            m.d.comb += [
                self.ila.trigger.eq(usb.translator.tx_valid),
                platform.request("uart").tx.o.eq(self.ila.tx),
            ]

        return m

if __name__ == "__main__":
    top_level_cli(USB2HostTest, video_core=False, ila_supported=True)
