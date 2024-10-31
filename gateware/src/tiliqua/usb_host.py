# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

from amaranth                      import *
from amaranth.lib                  import data, enum, wiring, stream
from amaranth.lib.wiring           import In, Out

from luna.usb2                     import USBDevice
from luna.gateware.interface.ulpi  import *
from luna.gateware.interface.utmi  import *
from luna.gateware.usb.usb2.packet import *

class TokenPID(enum.Enum, shape=unsigned(4)):
    OUT   = USBPacketID.OUT
    IN    = USBPacketID.IN
    SOF   = USBPacketID.SOF
    SETUP = USBPacketID.SETUP

class TokenPayload(data.Struct):
    pid:  TokenPID
    data: data.StructLayout({
        "addr": unsigned(7),
        "endp": unsigned(4),
    })

class USBTokenPacketGenerator(wiring.Component):

    """
    Send a stream of TokenPayloads over UTMI.

    A TokenPayload requires a second PID nibble and crc5 for it to
    be ready for the wire (UTMI). This is calculated here.
    """

    def __init__(self):
        self.tx = UTMITransmitInterface()
        super().__init__({
            "i": In(stream.Signature(TokenPayload))
        })

    def elaborate(self, platform):
        m = Module()

        pkt = Signal(shape=TokenPayload)

        with m.FSM(domain="usb"):

            with m.State('IDLE'):
                m.d.comb += self.i.ready.eq(1)
                with m.If(self.i.valid):
                    m.d.usb += pkt.eq(self.i.payload)
                    m.next = "SEND_PID"

            with m.State('SEND_PID'):

                with m.Switch(pkt.pid):
                    with m.Case(TokenPID.OUT):
                        m.d.comb += self.tx.data.eq(USBPacketID.OUT.byte()),
                    with m.Case(TokenPID.IN):
                        m.d.comb += self.tx.data.eq(USBPacketID.IN.byte()),
                    with m.Case(TokenPID.SOF):
                        m.d.comb += self.tx.data.eq(USBPacketID.SOF.byte()),
                    with m.Case(TokenPID.SETUP):
                        m.d.comb += self.tx.data.eq(USBPacketID.SETUP.byte()),

                m.d.comb += self.tx.valid.eq(1),

                with m.If(self.tx.ready):
                    m.next = 'SEND_PAYLOAD0'

            with m.State('SEND_PAYLOAD0'):
                m.d.comb += [
                    self.tx.data .eq(pkt.data.as_value()[0:8]),
                    self.tx.valid.eq(1),
                ]
                with m.If(self.tx.ready):
                    m.next = 'SEND_PAYLOAD1'

            with m.State('SEND_PAYLOAD1'):
                crc5 = Signal(5)
                m.d.comb += [
                    crc5.eq(USBTokenDetector.generate_crc_for_token(pkt.data.as_value())),
                    self.tx.data .eq(Cat(pkt.data.as_value()[8:11], crc5)),
                    self.tx.valid.eq(1),
                ]
                with m.If(self.tx.ready):
                    m.next = 'IDLE'

        return m

class USBSOFController(wiring.Component):

    """
    If :py:`enable == 1`, emit a single SOF TokenPayload every 1ms.
    """

    enable: In(1)
    o: Out(stream.Signature(TokenPayload))

    # LS: emit a SOF packet every 1ms
    _SOF_CYCLES = 60000

    def elaborate(self, platform):
        m = Module()

        sof_timer = Signal(16)
        frame_number = Signal(11, reset=0)

        m.d.usb +=  sof_timer.eq(sof_timer + 1),

        m.d.comb += [
            self.o.payload.pid.eq(TokenPID.SOF),
            self.o.payload.data.eq(frame_number),
        ]

        with m.FSM(domain="usb"):

            with m.State('OFF'):
                m.d.usb += [
                    sof_timer.eq(0),
                    frame_number.eq(0),
                ]
                with m.If(self.enable):
                    m.next = 'IDLE'

            with m.State('IDLE'):
                with m.If(sof_timer == (self._SOF_CYCLES - 1)):
                    m.d.usb += sof_timer.eq(0)
                    m.d.usb += frame_number.eq(frame_number + 1)
                    m.next = 'SEND'
                with m.If(~self.enable):
                    m.next = 'OFF'

            with m.State('SEND'):
                m.d.comb += self.o.valid.eq(1)
                with m.If(self.o.ready):
                    m.next = 'IDLE'

        return m


class SimpleUSBHost(Elaboratable):

    def __init__(self, *, bus=None, handle_clocking=True, sim=False):

        self.sim = sim
        if self.sim:
            self.utmi = UTMIInterface()
        else:
            self.utmi = UTMITranslator(ulpi=bus, handle_clocking=handle_clocking)
            self.translator = self.utmi

    def elaborate(self, platform):

        m = Module()

        if not self.sim:
            m.submodules.translator = self.translator

        m.submodules.transmitter = transmitter = USBDataPacketGenerator()
        m.submodules.data_crc = data_crc = USBDataPacketCRC()
        m.submodules.handshake_generator = handshake_generator = USBHandshakeGenerator()
        m.submodules.token_generator = token_generator = USBTokenPacketGenerator()
        m.submodules.sof_controller = sof_controller = USBSOFController()

        wiring.connect(m, sof_controller.o, token_generator.i)

        data_crc.add_interface(transmitter.crc)

        m.submodules.tx_multiplexer = tx_multiplexer = UTMIInterfaceMultiplexer()

        tx_multiplexer.add_input(token_generator.tx)
        tx_multiplexer.add_input(transmitter.tx)
        tx_multiplexer.add_input(handshake_generator.tx)

        m.d.comb += [
            tx_multiplexer.output  .attach(self.utmi),
            data_crc.tx_valid      .eq(tx_multiplexer.output.valid & self.utmi.tx_ready),
            data_crc.tx_data       .eq(tx_multiplexer.output.data),
        ]

        m.d.comb += [
            self.utmi.dm_pulldown.eq(1), # enable host pulldowns
            self.utmi.dp_pulldown.eq(1),
        ]


        cnt = Signal(64)
        m.d.usb += cnt.eq(cnt+1)
        # 100ms bus reset, then enter FS NORMAL and start generating SOF packets
        bus_reset = cnt < 6000
        with m.If(bus_reset):
            m.d.comb += [
                sof_controller.enable.eq(0),
                self.utmi.op_mode.eq(UTMIOperatingMode.RAW_DRIVE),
                self.utmi.xcvr_select.eq(USBSpeed.HIGH),
                self.utmi.term_select.eq(UTMITerminationSelect.HS_NORMAL),
            ]
        with m.Else():
            m.d.comb += [
                sof_controller.enable.eq(1),
                self.utmi.op_mode.eq(UTMIOperatingMode.NORMAL),
                self.utmi.xcvr_select.eq(USBSpeed.FULL),
                self.utmi.term_select.eq(UTMITerminationSelect.LS_FS_NORMAL),
            ]

        return m

