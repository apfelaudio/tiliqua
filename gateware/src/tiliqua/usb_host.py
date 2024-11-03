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

    # IN tokens use InterPacketTimer to determine when `txa`
    # (Tx Allowed) is permitted, other tokens need more time.
    # This is that time in cycles.
    _LONG_TXA_POST_TRANSMIT = 200

    def __init__(self):
        self.tx = UTMITransmitInterface()
        self.timer = InterpacketTimerInterface()
        super().__init__({
            "i": In(stream.Signature(TokenPayload)),
            "txa": Out(1),
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
                    m.d.comb += self.timer.start.eq(1)
                    with m.If(pkt.pid == TokenPID.IN):
                        m.d.comb += self.txa.eq(1)
                        m.next = 'WAIT-SHORT-TXA'
                    with m.Else():
                        m.next = 'WAIT-LONG-TXA'

            with m.State('WAIT-SHORT-TXA'):
                with m.If(self.timer.tx_allowed):
                    m.next = 'IDLE'

            with m.State('WAIT-LONG-TXA'):
                cnt = Signal(range(self._LONG_TXA_POST_TRANSMIT))
                m.d.usb += cnt.eq(cnt+1)
                with m.If(cnt == (self._LONG_TXA_POST_TRANSMIT - 1)):
                    m.d.comb += self.txa.eq(1)
                    m.d.usb += cnt.eq(0)
                    m.next = 'IDLE'

        return m

class USBSOFController(wiring.Component):

    """
    If :py:`enable == 1`, emit a single SOF TokenPayload every 1ms.

    :py:`txa` is strobed when transmissions are allowed after a SOF is sent.
    """

    enable: In(1)
    txa:    Out(1)
    o: Out(stream.Signature(TokenPayload))

    # FS: emit a SOF packet every 1ms
    _SOF_CYCLES = 60000

    # FS: delay from SOF packet being enqueued and this controller
    # strobing `txa` to indicate the next packet may be sent.
    # TODO: reduce this number? 0.7msec just taken from traces.
    _SOF_TX_TO_TX_MIN = 7*6000

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
                    m.next = 'WAIT-TX-ALLOWED'

            with m.State('WAIT-TX-ALLOWED'):
                cnt = Signal(range(self._SOF_TX_TO_TX_MIN))
                m.d.usb += cnt.eq(cnt+1)
                with m.If(cnt == (self._SOF_TX_TO_TX_MIN - 1)):
                    m.d.comb += self.txa.eq(1)
                    m.d.usb += cnt.eq(0)
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
            self.receiver   = USBDataPacketReceiver(utmi=self.utmi)
            self.handshake_detector  = USBHandshakeDetector(utmi=self.utmi)

    def elaborate(self, platform):

        m = Module()

        if not self.sim:
            m.submodules.translator = self.translator
        m.submodules.transmitter         = transmitter = USBDataPacketGenerator()
        m.submodules.receiver            = receiver            = self.receiver
        m.submodules.data_crc            = data_crc = USBDataPacketCRC()
        m.submodules.handshake_generator = handshake_generator = USBHandshakeGenerator()
        m.submodules.handshake_detector  = handshake_detector = self.handshake_detector
        m.submodules.token_generator     = token_generator = USBTokenPacketGenerator()
        m.submodules.sof_controller      = sof_controller = USBSOFController()
        m.submodules.timer               = timer = \
            USBInterpacketTimer(fs_only  = True)
        m.submodules.tx_multiplexer      = tx_multiplexer = UTMIInterfaceMultiplexer()

        # Data CRC interfaces
        data_crc.add_interface(transmitter.crc)
        data_crc.add_interface(receiver.data_crc)

        # Inter-packet timer interfaces.
        timer.add_interface(receiver.timer)
        timer.add_interface(token_generator.timer)

        # UTMI transmission interfaces
        tx_multiplexer.add_input(token_generator.tx)
        tx_multiplexer.add_input(transmitter.tx)
        tx_multiplexer.add_input(handshake_generator.tx)

        # Unless a particular state below is sending tokens, token
        # generator is always hooked up to the SOF generator.
        wiring.connect(m, sof_controller.o, token_generator.i)

        m.d.comb += [
            # Enable host pulldowns
            self.utmi.dm_pulldown.eq(1),
            self.utmi.dp_pulldown.eq(1),

            # By default, put transceiver in normal FS mode
            # (non-driving unless we actively send packets)
            self.utmi.op_mode.eq(UTMIOperatingMode.NORMAL),
            self.utmi.xcvr_select.eq(USBSpeed.FULL),
            self.utmi.term_select.eq(UTMITerminationSelect.LS_FS_NORMAL),

            # Wire up respective LUNA components
            timer.speed.eq(USBSpeed.FULL),
            tx_multiplexer.output .attach(self.utmi),
            data_crc.tx_valid     .eq(tx_multiplexer.output.valid & self.utmi.tx_ready),
            data_crc.tx_data      .eq(tx_multiplexer.output.data),
            data_crc.rx_data      .eq(self.utmi.rx_data),
            data_crc.rx_valid     .eq(self.utmi.rx_valid),

            # Enable SOF transmission by default.
            sof_controller.enable.eq(1),
        ]

        midi_toggle = Signal()
        m.d.comb += platform.request("led_a").o.eq(midi_toggle)

        _CONNECT_UNTIL_RESET_CYCLES = 13*600000 # 130ms
        _BUS_RESET_HOLD_CYCLES      = 6*600000  # 60ms

        with m.FSM(domain="usb"):

            #
            # HELPERS FOR CONSTRUCTING FSM STATES
            #

            def send_token(state_id, pid, addr, endp, next_state_id):
                """
                Create an FSM state that emits a token packet
                with the provided payload and does not move to
                the next state until transmissions are allowed again.
                """
                with m.State(state_id):
                    enqueued = Signal()
                    m.d.comb += [
                        token_generator.i.valid.eq(1),
                        token_generator.i.payload.pid.eq(pid),
                        token_generator.i.payload.data.addr.eq(addr),
                        token_generator.i.payload.data.endp.eq(endp),
                    ]
                    with m.If(token_generator.i.ready):
                        m.d.usb += enqueued.eq(1)
                    with m.If(enqueued & token_generator.txa):
                        m.d.usb += enqueued.eq(0)
                        m.next = next_state_id
            #
            # BUS RESET LOGIC
            #

            # TODO: move bus reset logic to dedicated component

            # Wait for an FS device to be connected
            # If it remains connected for 130ms, issue a bus reset.
            with m.State('IDLE'):
                _LINE_STATE_FS_HS_J = 0b01
                # Do not drive bus. Disable SOF transmission
                m.d.comb += sof_controller.enable.eq(0),
                connected_for_cycles = Signal(32)
                with m.If(self.utmi.line_state == _LINE_STATE_FS_HS_J):
                    m.d.usb += connected_for_cycles.eq(connected_for_cycles+1)
                with m.Else():
                    m.d.usb += connected_for_cycles.eq(0)
                with m.If(connected_for_cycles == _CONNECT_UNTIL_RESET_CYCLES):
                    m.next = 'BUS-RESET'

            # Bus reset: issue an SE0 for 60ms
            with m.State('BUS-RESET'):
                # Drive SE0 on bus. Disable SOF transmission
                m.d.comb += [
                    sof_controller.enable.eq(0),
                    self.utmi.op_mode.eq(UTMIOperatingMode.RAW_DRIVE),
                    self.utmi.xcvr_select.eq(USBSpeed.HIGH),
                    self.utmi.term_select.eq(UTMITerminationSelect.HS_NORMAL),
                ]
                se0_cycles = Signal(64)
                m.d.usb += se0_cycles.eq(se0_cycles+1)
                with m.If(se0_cycles == _BUS_RESET_HOLD_CYCLES):
                    m.d.usb += se0_cycles.eq(0)
                    m.next = 'SOF-TOKEN'

            #
            # HOST PACKET STATE MACHINE
            #

            # Send SOFs, and once every N SOFs, try the setup sequence.
            with m.State('SOF-TOKEN'):
                mod = Signal(16)
                with m.If(sof_controller.txa):
                    m.d.usb += mod.eq(mod+1)
                    with m.If(mod == 1024):
                        m.d.usb += mod.eq(0)
                    with m.If(mod == 65):
                        m.next = 'SETUP-TOKEN'

            send_token('SETUP-TOKEN', TokenPID.SETUP, 0, 0, 'SETUP-DATA0')

            with m.State('SETUP-DATA0'):

                data = Array([
                    Const(0x80, shape=8),
                    Const(0x06, shape=8),
                    Const(0x00, shape=8),
                    Const(0x01, shape=8),
                    Const(0x00, shape=8),
                    Const(0x00, shape=8),
                    Const(0x40, shape=8),
                    Const(0x00, shape=8),
                ])
                ix = Signal(range(len(data)))

                m.d.comb += [
                    transmitter.data_pid.eq(0), # DATA0
                    transmitter.stream.valid.eq(1),
                    transmitter.stream.payload.eq(data[ix]),
                ]

                with m.If(ix == 0):
                    m.d.comb += transmitter.stream.first.eq(1)
                with m.If(ix == len(data) - 1):
                    m.d.comb += transmitter.stream.last.eq(1)

                with m.If(transmitter.stream.ready):
                    m.d.usb += ix.eq(ix+1)
                    with m.If(ix == len(data) - 1):
                        m.next = 'WAIT-ACK'

            with m.State('WAIT-ACK'):
                with m.If(handshake_detector.detected.ack):
                    m.next = 'SOF-IN'
                with m.If(token_generator.timer.rx_timeout):
                    m.next = 'SOF-TOKEN'

            with m.State('SOF-IN'):
                with m.If(sof_controller.txa):
                    m.next = 'IN-TOKEN'

            send_token('IN-TOKEN', TokenPID.IN, 0, 0, 'SETUP-DATA1-IN')

            with m.State('SETUP-DATA1-IN'):
                with m.If(receiver.packet_complete):
                    m.next = 'SETUP-DATA1-ACK'
                """ TODO
                with m.If(receiver.timer.rx_timeout):
                    m.next = 'SOF-TOKEN'
                """

            with m.State('SETUP-DATA1-ACK'):
                with m.If(receiver.ready_for_response):
                    m.d.comb += handshake_generator.issue_ack.eq(1)
                    m.next = 'SOF-OUT'

            with m.State('SOF-OUT'):
                with m.If(sof_controller.txa):
                    m.next = 'SETUP-DATA1-ZLP-OUT'

            send_token('SETUP-DATA1-ZLP-OUT', TokenPID.OUT, 0, 0, 'SETUP-DATA1-ZLP')

            with m.State('SETUP-DATA1-ZLP'):
                m.d.comb += [
                    transmitter.data_pid.eq(1), # DATA1
                    transmitter.stream.last.eq(1),
                    transmitter.stream.valid.eq(1),
                ]
                m.next = 'ZLP-WAIT-ACK'

            with m.State('ZLP-WAIT-ACK'):
                with m.If(handshake_detector.detected.ack):
                    m.next = 'SOF-SETUP1'

            with m.State('SOF-SETUP1'):
                with m.If(sof_controller.txa):
                    m.next = 'SETUP1-TOKEN'

            send_token('SETUP1-TOKEN', TokenPID.SETUP, 0, 0, 'SETUP1-DATA0')

            with m.State('SETUP1-DATA0'):

                data = Array([
                    Const(0x00, shape=8),
                    Const(0x05, shape=8),
                    Const(0x12, shape=8),
                    Const(0x00, shape=8),
                    Const(0x00, shape=8),
                    Const(0x00, shape=8),
                    Const(0x00, shape=8),
                    Const(0x00, shape=8),
                ])
                ix = Signal(range(len(data)))

                m.d.comb += [
                    transmitter.data_pid.eq(0), # DATA0
                    transmitter.stream.valid.eq(1),
                    transmitter.stream.payload.eq(data[ix]),
                ]

                with m.If(ix == 0):
                    m.d.comb += transmitter.stream.first.eq(1)
                with m.If(ix == len(data) - 1):
                    m.d.comb += transmitter.stream.last.eq(1)

                with m.If(transmitter.stream.ready):
                    m.d.usb += ix.eq(ix+1)
                    with m.If(ix == len(data) - 1):
                        m.next = 'SETUP1-WAIT-ACK'

            with m.State('SETUP1-WAIT-ACK'):
                with m.If(handshake_detector.detected.ack):
                    m.next = 'SOF-SETUP1-IN'
                with m.If(token_generator.timer.rx_timeout):
                    m.next = 'SOF-TOKEN'

            with m.State('SOF-SETUP1-IN'):
                with m.If(sof_controller.txa):
                    m.next = 'SETUP1-IN-TOKEN'

            send_token('SETUP1-IN-TOKEN', TokenPID.IN, 0, 0, 'SETUP1-DATA1-IN')

            with m.State('SETUP1-DATA1-IN'):
                with m.If(receiver.packet_complete):
                    m.next = 'SETUP1-DATA1-ACK'
                """ TODO
                with m.If(receiver.timer.rx_timeout):
                    m.next = 'SOF-TOKEN'
                """

            with m.State('SETUP1-DATA1-ACK'):
                with m.If(receiver.ready_for_response):
                    m.d.comb += handshake_generator.issue_ack.eq(1)
                    m.next = 'SOF-SETUP2'

            # HENCEFORTH ADDR=18

            with m.State('SOF-SETUP2'):
                with m.If(sof_controller.txa):
                    m.next = 'SETUP2-TOKEN'

            send_token('SETUP2-TOKEN', TokenPID.SETUP, 18, 0, 'SETUP2-DATA0')

            with m.State('SETUP2-DATA0'):

                data = Array([
                    Const(0x00, shape=8),
                    Const(0x09, shape=8),
                    Const(0x01, shape=8),
                    Const(0x00, shape=8),
                    Const(0x00, shape=8),
                    Const(0x00, shape=8),
                    Const(0x00, shape=8),
                    Const(0x00, shape=8),
                ])
                ix = Signal(range(len(data)))

                m.d.comb += [
                    transmitter.data_pid.eq(0), # DATA0
                    transmitter.stream.valid.eq(1),
                    transmitter.stream.payload.eq(data[ix]),
                ]

                with m.If(ix == 0):
                    m.d.comb += transmitter.stream.first.eq(1)
                with m.If(ix == len(data) - 1):
                    m.d.comb += transmitter.stream.last.eq(1)

                with m.If(transmitter.stream.ready):
                    m.d.usb += ix.eq(ix+1)
                    with m.If(ix == len(data) - 1):
                        m.next = 'SETUP2-WAIT-ACK'

            with m.State('SETUP2-WAIT-ACK'):
                with m.If(handshake_detector.detected.ack):
                    m.next = 'SOF-SETUP2-IN'
                with m.If(token_generator.timer.rx_timeout):
                    m.next = 'SOF-SETUP2'

            with m.State('SOF-SETUP2-IN'):
                with m.If(sof_controller.txa):
                    m.next = 'SETUP2-IN-TOKEN'

            send_token('SETUP2-IN-TOKEN', TokenPID.IN, 18, 0, 'SETUP2-DATA1-IN')

            with m.State('SETUP2-DATA1-IN'):
                with m.If(receiver.packet_complete):
                    m.next = 'SETUP2-DATA1-ACK'
                """ TODO
                with m.If(receiver.timer.rx_timeout):
                    m.next = 'SOF-TOKEN'
                """

            with m.State('SETUP2-DATA1-ACK'):
                with m.If(receiver.ready_for_response):
                    m.d.comb += handshake_generator.issue_ack.eq(1)
                    m.next = 'SOF-MIDI'

            with m.State('SOF-MIDI'):
                with m.If(sof_controller.txa):
                    m.next = 'BULK-IN-TOKEN'

            send_token('BULK-IN-TOKEN', TokenPID.IN, 18, 1, 'MIDI-BULK-IN')

            with m.State('MIDI-BULK-IN'):
                with m.If(receiver.ready_for_response):
                    m.d.comb += handshake_generator.issue_ack.eq(1)
                    m.d.usb += midi_toggle.eq(~midi_toggle)
                    m.next = 'SOF-MIDI'
                with m.If(handshake_detector.detected.nak):
                    m.next = 'SOF-MIDI'

        return m

