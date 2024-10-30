# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

from luna.usb2                     import USBDevice
from luna.gateware.interface.ulpi  import *
from luna.gateware.interface.utmi  import *
from luna.gateware.usb.usb2.packet import *

class SimpleUSBHost(Elaboratable):

    def __init__(self, *, bus=None, handle_clocking=True, sim=False):

        self.sim = sim
        if self.sim:
            self.utmi = UTMIInterface()
        else:
            self.utmi = UTMITranslator(ulpi=bus, handle_clocking=handle_clocking)
            self.bus_busy   = self.utmi.busy
            self.translator = self.utmi
        self.always_fs  = False
        self.data_clock = 60e6

    def elaborate(self, platform):

        m = Module()

        if not self.sim:
            m.submodules.translator = self.translator

        m.submodules.transmitter = transmitter = USBDataPacketGenerator()
        m.submodules.data_crc = data_crc = USBDataPacketCRC()
        m.submodules.handshake_generator = handshake_generator = USBHandshakeGenerator()

        data_crc.add_interface(transmitter.crc)

        m.submodules.tx_multiplexer = tx_multiplexer = UTMIInterfaceMultiplexer()

        tx_multiplexer.add_input(transmitter.tx)
        tx_multiplexer.add_input(handshake_generator.tx)

        m.d.comb += [
            tx_multiplexer.output  .attach(self.utmi),
            data_crc.tx_valid      .eq(tx_multiplexer.output.valid & self.utmi.tx_ready),
            data_crc.tx_data       .eq(tx_multiplexer.output.data),

            transmitter.data_pid   .eq(0),
        ]

        frame_number = Signal(11)
        sof_timer = Signal(32)
        with m.If(sof_timer == (600 - 1)):
            m.d.usb += sof_timer.eq(0)
            m.d.usb += frame_number.eq(frame_number + 1)
            # HACK: send a ZLP instead of a SOF
            m.d.comb += [
                handshake_generator.issue_nak.eq(1),
            ]
        with m.Else():
            m.d.usb += sof_timer.eq(sof_timer + 1)

        # UTMI host mode settings
        m.d.comb += [
            self.utmi.dm_pulldown.eq(1), # enable host pulldowns
            self.utmi.dp_pulldown.eq(1),
            self.utmi.op_mode.eq(0), # normal
            self.utmi.xcvr_select.eq(0x01), # FS
            self.utmi.term_select.eq(1),
        ]

        return m

