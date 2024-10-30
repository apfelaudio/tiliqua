# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

from luna.usb2                     import USBDevice
from luna.gateware.interface.ulpi  import *
from luna.gateware.interface.utmi  import *
from luna.gateware.usb.usb2.packet import *

class USBSOFPacketGenerator(Elaboratable):

    _PACKET_SOF   = 0b10100101

    def __init__(self, standalone=False):
        self.tx = UTMITransmitInterface()

    def elaborate(self, platform):
        m = Module()

        frame_number = Signal(11, reset=227)
        sof_timer = Signal(32)
        send_sof = Signal()

        # TODO: s/600/60000 !!!!!!!!!!!!!!!
        with m.If(sof_timer == (600 - 1)):
            m.d.usb += sof_timer.eq(0)
            m.d.usb += frame_number.eq(frame_number + 1)
            m.d.comb += send_sof.eq(1)
        with m.Else():
            m.d.usb += sof_timer.eq(sof_timer + 1)

        with m.FSM(domain="usb"):

            with m.State('IDLE'):
                with m.If(send_sof):
                    m.next = "SEND_PID"

            with m.State('SEND_PID'):
                m.d.comb += [
                    self.tx.data       .eq(self._PACKET_SOF),
                    self.tx.valid      .eq(1),
                ]
                with m.If(self.tx.ready):
                    m.next = 'SEND_PAYLOAD0'

            with m.State('SEND_PAYLOAD0'):
                m.d.comb += [
                    self.tx.data       .eq(frame_number[0:8]),
                    self.tx.valid      .eq(1),
                ]
                with m.If(self.tx.ready):
                    m.next = 'SEND_PAYLOAD1'

            with m.State('SEND_PAYLOAD1'):
                crc5 = Signal(5)
                m.d.comb += [
                    crc5.eq(USBTokenDetector.generate_crc_for_token(frame_number)),
                    self.tx.data       .eq(Cat(crc5, frame_number[8:11])),
                    self.tx.valid      .eq(1),
                ]
                with m.If(self.tx.ready):
                    m.next = 'IDLE'

        return m

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
        m.submodules.sof_generator = sof_generator = USBSOFPacketGenerator()

        data_crc.add_interface(transmitter.crc)

        m.submodules.tx_multiplexer = tx_multiplexer = UTMIInterfaceMultiplexer()

        tx_multiplexer.add_input(sof_generator.tx)
        tx_multiplexer.add_input(transmitter.tx)
        tx_multiplexer.add_input(handshake_generator.tx)

        m.d.comb += [
            tx_multiplexer.output  .attach(self.utmi),
            data_crc.tx_valid      .eq(tx_multiplexer.output.valid & self.utmi.tx_ready),
            data_crc.tx_data       .eq(tx_multiplexer.output.data),
        ]

        # UTMI host mode settings
        m.d.comb += [
            self.utmi.dm_pulldown.eq(1), # enable host pulldowns
            self.utmi.dp_pulldown.eq(1),
            self.utmi.op_mode.eq(0), # normal
            self.utmi.xcvr_select.eq(0x01), # FS
            self.utmi.term_select.eq(1),
        ]

        return m
