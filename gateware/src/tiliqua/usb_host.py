# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause
"""
Extremely bare-bones USB host logic intended for using Tiliqua as a USB MIDI host.
Consider this EXPERIMENTAL. Error handling / retries are not finished.
"""

from amaranth                      import *
from amaranth.lib                  import data, enum, wiring, stream, fifo
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
    # Lightweight storage for token contents,
    # excluding crc5 and pid nibble that are
    # added before this is sent on the wire.
    pid:  TokenPID
    data: data.StructLayout({
        "addr": unsigned(7),
        "endp": unsigned(4),
    })

class SetupPayload(data.Struct):

    class Recipient(enum.Enum, shape=unsigned(5)):
        DEVICE    = 0
        INTERFACE = 1
        ENDPOINT  = 2
        OTHER     = 3

    class Type(enum.Enum, shape=unsigned(2)):
        STANDARD  = 0
        CLASS     = 1
        ENDPOINT  = 2
        RESERVED  = 3

    class Direction(enum.Enum, shape=unsigned(1)):
        HOST_TO_DEVICE = 0
        DEVICE_TO_HOST = 1

    class StandardRequest(enum.Enum, shape=unsigned(8)):
        SET_ADDRESS       = 0x05
        GET_DESCRIPTOR    = 0x06
        SET_CONFIGURATION = 0x09

    bmRequestType: data.StructLayout({
        'bmRecipient': Recipient,
        'bmType':      Type,
        'bmDirection': Direction,
    })
    bRequest:      StandardRequest
    wValue:        unsigned(16)
    wIndex:        unsigned(16)
    wLength:       unsigned(16)

    #
    # Some helpers to quickly create standard request types.
    # These can be passed directly to the `init` field of signals
    # of shape SetupPayload.
    #

    def init_get_descriptor(value, length):
        return {
            'bmRequestType': {
                'bmRecipient': SetupPayload.Recipient.DEVICE,
                'bmType':      SetupPayload.Type.STANDARD,
                'bmDirection': SetupPayload.Direction.DEVICE_TO_HOST,
            },
            'bRequest': SetupPayload.StandardRequest.GET_DESCRIPTOR,
            'wValue':   value,
            'wIndex':   0x0000,
            'wLength':  length,
        }

    def init_set_address(address):
        return {
            'bmRequestType': {
                'bmRecipient': SetupPayload.Recipient.DEVICE,
                'bmType':      SetupPayload.Type.STANDARD,
                'bmDirection': SetupPayload.Direction.HOST_TO_DEVICE,
            },
            'bRequest': SetupPayload.StandardRequest.SET_ADDRESS,
            'wValue':   address,
            'wIndex':   0x0000,
            'wLength':  0x0000,
        }


    def init_set_configuration(configuration):
        return {
            'bmRequestType': {
                'bmRecipient': SetupPayload.Recipient.DEVICE,
                'bmType':      SetupPayload.Type.STANDARD,
                'bmDirection': SetupPayload.Direction.HOST_TO_DEVICE,
            },
            'bRequest': SetupPayload.StandardRequest.SET_CONFIGURATION,
            'wValue':   configuration,
            'wIndex':   0x0000,
            'wLength':  0x0000,
        }


class USBTokenPacketGenerator(wiring.Component):

    """
    Send a stream of TokenPayloads over UTMI.

    A TokenPayload requires a second PID nibble and crc5 for it to
    be ready for the wire (UTMI). This is calculated here.
    """

    #
    # IN tokens use InterPacketTimer to determine when `txa`
    # (Tx Allowed) is permitted, other tokens need more time.
    # This is that time in cycles.
    #
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

    TODO: microframes for HS links.
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

        if platform is None:
            # Reduce delays in simulation testcases
            self._SOF_CYCLES //= 10
            self._SOF_TX_TO_TX_MIN //= 10

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


class SimpleUSBMIDIHost(Elaboratable):

    """
    Extremely bare-bones USB MIDI host that uses an ULPI PHY interface.
    For now, full-speed only (most MIDI devices are).

    This does the bare minimum to enumerate a device, set the correct configuration
    and poll it for MIDI information on a BULK IN endpoint.

    Proper error handling and retries are not complete yet. This controller
    currently walks the happy path of a few MIDI devices I have fine, however
    still requires extensive testing.

    The USB configuration ID and MIDI endpoint are currently hard-coded, that is,
    the descriptors themselves are not inspected to find the correct interface,
    and you must provide them before elaboration.

    This should not be so hard to add. Goal of this core initially is just
    to verify the Tiliqua hardware design can function as a USB host.
    """

    # Address that is assigned to the connected device during the SETUP phase.
    # The host is free to choose whatever it wants for this.
    _DEFAULT_DEVICE_ADDR = 0x12

    def __init__(self, *, bus=None, handle_clocking=True, sim=False,
                 hardcoded_configuration_id=0x0001,
                 hardcoded_midi_endpoint=1):
        # FIXME: for now, the configuration and endpoint ID for the MIDI interface is hardcoded.
        self.configuration_id = hardcoded_configuration_id
        self.midi_endpoint_id = hardcoded_midi_endpoint
        self.sim = sim
        if self.sim:
            self.utmi = UTMIInterface()
        else:
            self.utmi = UTMITranslator(ulpi=bus, handle_clocking=handle_clocking)
            self.translator = self.utmi
        self.receiver   = USBDataPacketReceiver(utmi=self.utmi)
        self.handshake_detector  = USBHandshakeDetector(utmi=self.utmi)
        # TODO: Out() member
        self.o_midi_bytes = stream.Signature(unsigned(8)).create()
        self.midi_fifo = fifo.SyncFIFOBuffered(width=8, depth=16)

    def elaborate(self, platform):

        m = Module()

        if not self.sim:
            m.submodules.translator = self.translator
        m.submodules.transmitter         = transmitter = USBDataPacketGenerator()
        m.submodules.receiver            = receiver = self.receiver
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

        _CONNECT_UNTIL_RESET_CYCLES = 13*600000 # 130ms
        _BUS_RESET_HOLD_CYCLES      = 6*600000  # 60ms
        _SOF_COUNTER_MAX            = 1024

        # Index after every SOF_COUNTER_MAX rolls over at which
        # to attempt a setup request to enter BULK_IN poll mode.
        if self.sim:
            _SETUP_ON_SOF_INDEX     = 1
        else:
            _SETUP_ON_SOF_INDEX     = 65

        #
        # FIFO to store MIDI data as it arrives.
        #
        # If it ends up storing a healthy MIDI packet, its
        # r_stream is hooked up to the external interface until
        # this FIFO is drained.
        #

        m.submodules.midi_fifo = midi_fifo = self.midi_fifo
        m.d.comb += [
            # w_en only strobed in MIDI-BULK-IN phase
            midi_fifo.w_data.eq(receiver.stream.payload),
        ]

        with m.FSM(domain="usb"):

            #
            # HELPERS FOR CONSTRUCTING FSM STATES
            #

            def fsm_tx_token(state_id, pid, addr, endp, next_state_id):
                """
                Single FSM state that emits a token packet
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

            def fsm_tx_data_stage(state_id, data_shape, data_pid, data_payload, next_state_id):
                """
                Single FSM state that emits a DATA0/DATA1 packet. and does not move
                to the next state until the packet is enqueued for transmission.
                """
                with m.State(state_id):
                    data_length = data_shape.as_shape().size // 8
                    payload = Const(data_payload, shape=data_shape)
                    data_view = Signal(data.ArrayLayout(unsigned(8), data_length))
                    ix = Signal(range(data_length))
                    m.d.comb += [
                        data_view.eq(payload),
                        transmitter.data_pid.eq(data_pid), # DATA0/DATA1 etc
                        transmitter.stream.valid.eq(1),
                        transmitter.stream.payload.eq(data_view[ix]),
                    ]
                    with m.If(ix == 0):
                        m.d.comb += transmitter.stream.first.eq(1)
                    with m.If(ix == len(data_view) - 1):
                        m.d.comb += transmitter.stream.last.eq(1)
                    with m.If(transmitter.stream.ready):
                        m.d.usb += ix.eq(ix+1)
                        with m.If(ix == len(data_view) - 1):
                            m.next = next_state_id

            def fsm_sequence_zlp_out(state_id, next_state_id, data_pid=1):
                """
                Wait for next SOF, emit an OUT token followed by ZLP and check it is acknowledged.
                """
                with m.State(state_id):
                    with m.If(sof_controller.txa):
                        m.next = f'{state_id}-TOKEN'

                fsm_tx_token(f'{state_id}-TOKEN', TokenPID.OUT, 0, 0, f'{state_id}-TX-ZLP')

                with m.State(f'{state_id}-TX-ZLP'):
                    m.d.comb += [
                        transmitter.data_pid.eq(data_pid),
                        transmitter.stream.last.eq(1),
                        transmitter.stream.valid.eq(1),
                    ]
                    # FIXME: cannot gate on transmitter.stream.ready because
                    # ZLP never strobes that signal! need another way..
                    m.next = f'{state_id}-WAIT-ACK'

                with m.State(f'{state_id}-WAIT-ACK'):
                    # FIXME: detect ZLP ACK failure
                    with m.If(handshake_detector.detected.ack):
                        m.next = next_state_id

            def fsm_sequence_rx_in_stage_ignore(state_id, state_ok, state_err, addr=0, endp=0):
                """
                Wait for next SOF.
                Emit an IN token, verify we got data and acknowledge it.
                The data itself is simply ignored for now.
                """

                with m.State(state_id):
                    with m.If(sof_controller.txa):
                        m.next = f'{state_id}-TOKEN'

                fsm_tx_token(f'{state_id}-TOKEN', TokenPID.IN, addr, endp, f'{state_id}-WAIT-PKT')

                with m.State(f'{state_id}-WAIT-PKT'):
                    # FIXME: tolerate rx timeout
                    with m.If(receiver.packet_complete):
                        m.next = f'{state_id}-ACK-PKT'
                    with m.If(handshake_detector.detected.nak):
                        m.next = state_err

                with m.State(f'{state_id}-ACK-PKT'):
                    with m.If(receiver.ready_for_response):
                        m.d.comb += handshake_generator.issue_ack.eq(1)
                        m.next = state_ok

            def fsm_sequence_setup(state_id, state_ok, state_err, setup_payload, addr=0, endp=0):
                """
                Wait for next SOF.
                Emit a SETUP token, followed by a DATA0 payload and check it is acknowledged.
                """
                with m.State(state_id):
                    with m.If(sof_controller.txa):
                        m.next = f'{state_id}-TOKEN'

                fsm_tx_token(f'{state_id}-TOKEN', TokenPID.SETUP, addr, endp, f'{state_id}-DATA0')

                fsm_tx_data_stage(f'{state_id}-DATA0',
                                  data_shape=SetupPayload,
                                  data_pid=0, # DATA0
                                  data_payload=setup_payload,
                                  next_state_id=f'{state_id}-WAIT-ACK')

                with m.State(f'{state_id}-WAIT-ACK'):
                    with m.If(handshake_detector.detected.ack):
                        m.next = state_ok
                    with m.If(token_generator.timer.rx_timeout |
                              handshake_detector.detected.nak):
                        m.next = state_err

            if not self.sim:

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
                        m.next = 'SOF-IDLE'

            #
            # HOST PACKET STATE MACHINE
            #

            #
            # SOF IDLE LOOP
            #
            # Send SOFs, and once every N SOFs, try the setup sequence.
            #

            with m.State('SOF-IDLE'):
                sof_counter = Signal(range(_SOF_COUNTER_MAX))
                with m.If(sof_controller.txa):
                    m.d.usb += sof_counter.eq(sof_counter+1)
                    with m.If(sof_counter == (_SOF_COUNTER_MAX-1)):
                        m.d.usb += sof_counter.eq(0)
                    with m.If(sof_counter == _SETUP_ON_SOF_INDEX):
                        m.next = 'SETUP0'

            #
            # SETUP0: GET_DESCRIPTOR
            #

            fsm_sequence_setup('SETUP0',
                               state_ok='SETUP0-IN',
                               state_err='SOF-IDLE',
                               setup_payload=SetupPayload.init_get_descriptor(0x0100, 0x0040),
                               addr=0,
                               endp=0)

            fsm_sequence_rx_in_stage_ignore('SETUP0-IN', state_ok='SETUP0-ZLP-OUT', state_err='SETUP0-IN')

            fsm_sequence_zlp_out('SETUP0-ZLP-OUT', 'SETUP1')

            #
            # SETUP1: SET_ADDRESS
            #

            fsm_sequence_setup('SETUP1',
                               state_ok='SETUP1-IN',
                               state_err='SOF-IDLE',
                               setup_payload=SetupPayload.init_set_address(self._DEFAULT_DEVICE_ADDR),
                               addr=0,
                               endp=0)

            fsm_sequence_rx_in_stage_ignore('SETUP1-IN', state_ok='SETUP2', state_err='SETUP1-IN')

            #
            # SETUP2: SET_CONFIGURATION
            #

            # NOTE: device address is now set! Token addr must always be set to match.

            fsm_sequence_setup('SETUP2',
                               state_ok='SETUP2-IN',
                               state_err='SOF-IDLE',
                               setup_payload=SetupPayload.init_set_configuration(self.configuration_id),
                               addr=self._DEFAULT_DEVICE_ADDR,
                               endp=0)

            fsm_sequence_rx_in_stage_ignore('SETUP2-IN', state_ok='MIDI-IDLE-SOF',
                                            state_err='SETUP2-IN', addr=self._DEFAULT_DEVICE_ADDR)

            #
            # MIDI BULK IN (continuous polling)
            #

            with m.State('MIDI-IDLE-SOF'):
                # always drain MIDI FIFO before we poll the endpoint
                m.d.comb += midi_fifo.r_en.eq(1)
                with m.If(sof_controller.txa):
                    m.next = 'BULK-IN-TOKEN'

            fsm_tx_token('BULK-IN-TOKEN', TokenPID.IN, self._DEFAULT_DEVICE_ADDR,
                         self.midi_endpoint_id, 'MIDI-BULK-IN')

            with m.State('MIDI-BULK-IN'):
                # send incoming packet to MIDI FIFO
                m.d.comb += midi_fifo.w_en.eq(receiver.stream.next)
                # it may or may not contain useful data (potentially just a NAK)
                # if it is not useful, the FIFO is drained in MIDI-IDLE-SOF.
                with m.If(receiver.ready_for_response):
                    m.d.comb += handshake_generator.issue_ack.eq(1)
                    m.next = 'MIDI-CONSUME'
                with m.If(handshake_detector.detected.nak):
                    m.next = 'MIDI-IDLE-SOF'
                # TODO: no response for too long -> disconnect?

            with m.State('MIDI-CONSUME'):
                # the FIFO contains valid MIDI data. Wait here until its consumed.
                wiring.connect(m, midi_fifo.r_stream, wiring.flipped(self.o_midi_bytes))
                with m.If(midi_fifo.r_level == 0):
                    m.next = 'MIDI-IDLE-SOF'

            #
            # TODO: Disconnect logic -> go back to before BUS RESET
            #

        return m

