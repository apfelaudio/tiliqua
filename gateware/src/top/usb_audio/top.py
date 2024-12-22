# Copyright (c) 2021 Hans Baier <hansfbaier@gmail.com>
# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD--3-Clause
"""
4-channel USB2 audio interface, based on LUNA project.

Enumerates as a 4-in, 4-out 48kHz sound card.

TODO: this may require a resampler to improve stability
as the PLL is not currently generating exactly 256*48kHz,
however it seems to be close enough, at least for
glitch-free audio on my Linux machine...
"""

import os

from amaranth              import *
from amaranth.build        import *
from amaranth.lib.cdc      import FFSynchronizer
from amaranth.lib.fifo     import SyncFIFO, AsyncFIFO, SyncFIFOBuffered

from luna                import top_level_cli
from luna.usb2           import (USBDevice,
                                 USBIsochronousInMemoryEndpoint,
                                 USBIsochronousOutStreamEndpoint,
                                 USBIsochronousInStreamEndpoint,
                                 USBStreamInEndpoint,
                                 USBStreamOutEndpoint)

from usb_protocol.types                       import USBRequestType, USBRequestRecipient, USBTransferType, USBSynchronizationType, USBUsageType, USBDirection, USBStandardRequests
from usb_protocol.types.descriptors.uac2      import AudioClassSpecificRequestCodes
from usb_protocol.emitters                    import DeviceDescriptorCollection
from usb_protocol.emitters.descriptors        import uac2, standard, midi1

from luna.gateware.platform                   import NullPin
from luna.gateware.usb.usb2.device            import USBDevice
from luna.gateware.usb.usb2.request           import USBRequestHandler, StallOnlyRequestHandler
from luna.gateware.usb.stream                 import USBInStreamInterface
from luna.gateware.stream.generator           import StreamSerializer
from luna.gateware.stream                     import StreamInterface
from luna.gateware.architecture.car           import PHYResetController

from tiliqua.cli                              import top_level_cli
from tiliqua.eurorack_pmod                    import EurorackPmod
from tiliqua.tiliqua_platform                 import RebootProvider
from vendor.ila                               import AsyncSerialILA

from util                   import EdgeToPulse, connect_fifo_to_stream, connect_stream_to_fifo
from usb_stream_to_channels import USBStreamToChannels
from channels_to_usb_stream import ChannelsToUSBStream
from audio_to_channels      import AudioToChannels


class USB2AudioInterface(Elaboratable):
    """ USB Audio Class v2 interface """

    brief = "USB soundcard, 4in + 4out."

    NR_CHANNELS = 4
    MAX_PACKET_SIZE = int(224 // 8 * NR_CHANNELS)
    MAX_PACKET_SIZE_MIDI = 64

    def __init__(self, **kwargs):
        super().__init__()

    def create_descriptors(self):
        """ Creates the descriptors that describe our audio topology. """

        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.bcdUSB             = 2.00
            d.bDeviceClass       = 0xEF
            d.bDeviceSubclass    = 0x02
            d.bDeviceProtocol    = 0x01
            d.idVendor           = 0x1209
            d.idProduct          = 0xAA62

            d.iManufacturer      = "apf.audio"
            d.iProduct           = "Tiliqua"
            d.iSerialNumber      = "r2-beta-0000"
            d.bcdDevice          = 0.01

            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as configDescr:
            # Interface Association
            interfaceAssociationDescriptor                 = uac2.InterfaceAssociationDescriptorEmitter()
            interfaceAssociationDescriptor.bInterfaceCount = 3 # Audio Control + Inputs + Outputs
            configDescr.add_subordinate_descriptor(interfaceAssociationDescriptor)

            # Interface Descriptor (Control)
            interfaceDescriptor = uac2.StandardAudioControlInterfaceDescriptorEmitter()
            interfaceDescriptor.bInterfaceNumber = 0
            configDescr.add_subordinate_descriptor(interfaceDescriptor)

            # AudioControl Interface Descriptor
            audioControlInterface = self.create_audio_control_interface_descriptor()
            configDescr.add_subordinate_descriptor(audioControlInterface)

            # Audio I/O stream descriptors
            self.create_output_channels_descriptor(configDescr)
            self.create_input_channels_descriptor(configDescr)

            # Midi descriptors
            midi_interface, midi_streaming_interface = self.create_midi_interface_descriptor()
            configDescr.add_subordinate_descriptor(midi_interface)
            configDescr.add_subordinate_descriptor(midi_streaming_interface)

        return descriptors


    def create_audio_control_interface_descriptor(self):
        audioControlInterface = uac2.ClassSpecificAudioControlInterfaceDescriptorEmitter()

        # AudioControl Interface Descriptor (ClockSource)
        clockSource = uac2.ClockSourceDescriptorEmitter()
        clockSource.bClockID     = 1
        clockSource.bmAttributes = uac2.ClockAttributes.INTERNAL_FIXED_CLOCK
        clockSource.bmControls   = uac2.ClockFrequencyControl.HOST_READ_ONLY
        audioControlInterface.add_subordinate_descriptor(clockSource)


        # streaming input port from the host to the USB interface
        inputTerminal               = uac2.InputTerminalDescriptorEmitter()
        inputTerminal.bTerminalID   = 2
        inputTerminal.wTerminalType = uac2.USBTerminalTypes.USB_STREAMING
        # The number of channels needs to be 2 here in order to be recognized
        # default audio out device by Windows. We provide an alternate
        # setting with the full channel count, which also references
        # this terminal ID
        inputTerminal.bNrChannels   = self.NR_CHANNELS
        inputTerminal.bCSourceID    = 1
        audioControlInterface.add_subordinate_descriptor(inputTerminal)

        # audio output port from the USB interface to the outside world
        outputTerminal               = uac2.OutputTerminalDescriptorEmitter()
        outputTerminal.bTerminalID   = 3
        outputTerminal.wTerminalType = uac2.OutputTerminalTypes.SPEAKER
        outputTerminal.bSourceID     = 2
        outputTerminal.bCSourceID    = 1
        audioControlInterface.add_subordinate_descriptor(outputTerminal)

        # audio input port from the outside world to the USB interface
        inputTerminal               = uac2.InputTerminalDescriptorEmitter()
        inputTerminal.bTerminalID   = 4
        inputTerminal.wTerminalType = uac2.InputTerminalTypes.MICROPHONE
        inputTerminal.bNrChannels   = self.NR_CHANNELS
        inputTerminal.bCSourceID    = 1
        audioControlInterface.add_subordinate_descriptor(inputTerminal)

        # audio output port from the USB interface to the host
        outputTerminal               = uac2.OutputTerminalDescriptorEmitter()
        outputTerminal.bTerminalID   = 5
        outputTerminal.wTerminalType = uac2.USBTerminalTypes.USB_STREAMING
        outputTerminal.bSourceID     = 4
        outputTerminal.bCSourceID    = 1
        audioControlInterface.add_subordinate_descriptor(outputTerminal)

        return audioControlInterface


    def create_output_streaming_interface(self, c, *, nr_channels, alt_setting_nr):
        # Interface Descriptor (Streaming, OUT, active setting)
        activeAudioStreamingInterface                   = uac2.AudioStreamingInterfaceDescriptorEmitter()
        activeAudioStreamingInterface.bInterfaceNumber  = 1
        activeAudioStreamingInterface.bAlternateSetting = alt_setting_nr
        activeAudioStreamingInterface.bNumEndpoints     = 2
        c.add_subordinate_descriptor(activeAudioStreamingInterface)

        # AudioStreaming Interface Descriptor (General)
        audioStreamingInterface               = uac2.ClassSpecificAudioStreamingInterfaceDescriptorEmitter()
        audioStreamingInterface.bTerminalLink = 2
        audioStreamingInterface.bFormatType   = uac2.FormatTypes.FORMAT_TYPE_I
        audioStreamingInterface.bmFormats     = uac2.TypeIFormats.PCM
        audioStreamingInterface.bNrChannels   = nr_channels
        c.add_subordinate_descriptor(audioStreamingInterface)

        # AudioStreaming Interface Descriptor (Type I)
        typeIStreamingInterface  = uac2.TypeIFormatTypeDescriptorEmitter()
        typeIStreamingInterface.bSubslotSize   = 4
        typeIStreamingInterface.bBitResolution = 24 # we use all 24 bits
        c.add_subordinate_descriptor(typeIStreamingInterface)

        # Endpoint Descriptor (Audio out)
        audioOutEndpoint = standard.EndpointDescriptorEmitter()
        audioOutEndpoint.bEndpointAddress     = USBDirection.OUT.to_endpoint_address(1) # EP 1 OUT
        audioOutEndpoint.bmAttributes         = USBTransferType.ISOCHRONOUS  | \
                                                (USBSynchronizationType.ASYNC << 2) | \
                                                (USBUsageType.DATA << 4)
        audioOutEndpoint.wMaxPacketSize = self.MAX_PACKET_SIZE
        audioOutEndpoint.bInterval       = 1
        c.add_subordinate_descriptor(audioOutEndpoint)

        # AudioControl Endpoint Descriptor
        audioControlEndpoint = uac2.ClassSpecificAudioStreamingIsochronousAudioDataEndpointDescriptorEmitter()
        c.add_subordinate_descriptor(audioControlEndpoint)

        # Endpoint Descriptor (Feedback IN)
        feedbackInEndpoint = standard.EndpointDescriptorEmitter()
        feedbackInEndpoint.bEndpointAddress  = USBDirection.IN.to_endpoint_address(1) # EP 1 IN
        feedbackInEndpoint.bmAttributes      = USBTransferType.ISOCHRONOUS  | \
                                               (USBSynchronizationType.NONE << 2)  | \
                                               (USBUsageType.FEEDBACK << 4)
        feedbackInEndpoint.wMaxPacketSize    = 4
        feedbackInEndpoint.bInterval         = 4
        c.add_subordinate_descriptor(feedbackInEndpoint)


    def create_output_channels_descriptor(self, c):
        #
        # Interface Descriptor (Streaming, OUT, quiet setting)
        #
        quietAudioStreamingInterface = uac2.AudioStreamingInterfaceDescriptorEmitter()
        quietAudioStreamingInterface.bInterfaceNumber  = 1
        quietAudioStreamingInterface.bAlternateSetting = 0
        c.add_subordinate_descriptor(quietAudioStreamingInterface)

        # we need the default alternate setting to be stereo
        # out for windows to automatically recognize
        # and use this audio interface
        self.create_output_streaming_interface(c, nr_channels=self.NR_CHANNELS, alt_setting_nr=1)


    def create_input_streaming_interface(self, c, *, nr_channels, alt_setting_nr, channel_config=0):
        # Interface Descriptor (Streaming, IN, active setting)
        activeAudioStreamingInterface = uac2.AudioStreamingInterfaceDescriptorEmitter()
        activeAudioStreamingInterface.bInterfaceNumber  = 2
        activeAudioStreamingInterface.bAlternateSetting = alt_setting_nr
        activeAudioStreamingInterface.bNumEndpoints     = 1
        c.add_subordinate_descriptor(activeAudioStreamingInterface)

        # AudioStreaming Interface Descriptor (General)
        audioStreamingInterface                 = uac2.ClassSpecificAudioStreamingInterfaceDescriptorEmitter()
        audioStreamingInterface.bTerminalLink   = 5
        audioStreamingInterface.bFormatType     = uac2.FormatTypes.FORMAT_TYPE_I
        audioStreamingInterface.bmFormats       = uac2.TypeIFormats.PCM
        audioStreamingInterface.bNrChannels     = nr_channels
        audioStreamingInterface.bmChannelConfig = channel_config
        c.add_subordinate_descriptor(audioStreamingInterface)

        # AudioStreaming Interface Descriptor (Type I)
        typeIStreamingInterface  = uac2.TypeIFormatTypeDescriptorEmitter()
        typeIStreamingInterface.bSubslotSize   = 4
        typeIStreamingInterface.bBitResolution = 24 # we use all 24 bits
        c.add_subordinate_descriptor(typeIStreamingInterface)

        # Endpoint Descriptor (Audio out)
        audioOutEndpoint = standard.EndpointDescriptorEmitter()
        audioOutEndpoint.bEndpointAddress     = USBDirection.IN.to_endpoint_address(2) # EP 2 IN
        audioOutEndpoint.bmAttributes         = USBTransferType.ISOCHRONOUS  | \
                                                (USBSynchronizationType.ASYNC << 2) | \
                                                (USBUsageType.DATA << 4)
        audioOutEndpoint.wMaxPacketSize = self.MAX_PACKET_SIZE
        audioOutEndpoint.bInterval      = 1
        c.add_subordinate_descriptor(audioOutEndpoint)

        # AudioControl Endpoint Descriptor
        audioControlEndpoint = uac2.ClassSpecificAudioStreamingIsochronousAudioDataEndpointDescriptorEmitter()
        c.add_subordinate_descriptor(audioControlEndpoint)


    def create_input_channels_descriptor(self, c):
        #
        # Interface Descriptor (Streaming, IN, quiet setting)
        #
        quietAudioStreamingInterface = uac2.AudioStreamingInterfaceDescriptorEmitter()
        quietAudioStreamingInterface.bInterfaceNumber  = 2
        quietAudioStreamingInterface.bAlternateSetting = 0
        c.add_subordinate_descriptor(quietAudioStreamingInterface)

        # Windows wants a stereo pair as default setting, so let's have it
        self.create_input_streaming_interface(c, nr_channels=self.NR_CHANNELS, alt_setting_nr=1, channel_config=0x3)

    def create_midi_interface_descriptor(self):
        midi_interface = midi1.StandardMidiStreamingInterfaceDescriptorEmitter()
        midi_interface.bInterfaceNumber = 3
        midi_interface.bNumEndpoints    = 2

        midi_streaming_interface = midi1.ClassSpecificMidiStreamingInterfaceDescriptorEmitter()

        outToHostJack = midi1.MidiOutJackDescriptorEmitter()
        outToHostJack.bJackID = 1
        outToHostJack.bJackType = midi1.MidiStreamingJackTypes.EMBEDDED
        outToHostJack.add_source(2)
        midi_streaming_interface.add_subordinate_descriptor(outToHostJack)

        inToDeviceJack = midi1.MidiInJackDescriptorEmitter()
        inToDeviceJack.bJackID = 2
        inToDeviceJack.bJackType = midi1.MidiStreamingJackTypes.EXTERNAL
        midi_streaming_interface.add_subordinate_descriptor(inToDeviceJack)

        inFromHostJack = midi1.MidiInJackDescriptorEmitter()
        inFromHostJack.bJackID = 3
        inFromHostJack.bJackType = midi1.MidiStreamingJackTypes.EMBEDDED
        midi_streaming_interface.add_subordinate_descriptor(inFromHostJack)

        outFromDeviceJack = midi1.MidiOutJackDescriptorEmitter()
        outFromDeviceJack.bJackID = 4
        outFromDeviceJack.bJackType = midi1.MidiStreamingJackTypes.EXTERNAL
        outFromDeviceJack.add_source(3)
        midi_streaming_interface.add_subordinate_descriptor(outFromDeviceJack)

        outEndpoint = midi1.StandardMidiStreamingBulkDataEndpointDescriptorEmitter()
        outEndpoint.bEndpointAddress = USBDirection.OUT.to_endpoint_address(3)
        outEndpoint.wMaxPacketSize = self.MAX_PACKET_SIZE_MIDI
        midi_streaming_interface.add_subordinate_descriptor(outEndpoint)

        outMidiEndpoint = midi1.ClassSpecificMidiStreamingBulkDataEndpointDescriptorEmitter()
        outMidiEndpoint.add_associated_jack(3)
        midi_streaming_interface.add_subordinate_descriptor(outMidiEndpoint)

        inEndpoint = midi1.StandardMidiStreamingBulkDataEndpointDescriptorEmitter()
        inEndpoint.bEndpointAddress = USBDirection.IN.to_endpoint_address(3)
        inEndpoint.wMaxPacketSize = self.MAX_PACKET_SIZE_MIDI
        midi_streaming_interface.add_subordinate_descriptor(inEndpoint)

        inMidiEndpoint = midi1.ClassSpecificMidiStreamingBulkDataEndpointDescriptorEmitter()
        inMidiEndpoint.add_associated_jack(1)
        midi_streaming_interface.add_subordinate_descriptor(inMidiEndpoint)

        return (midi_interface, midi_streaming_interface)

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = car = platform.clock_domain_generator()
        m.submodules.reboot = reboot = RebootProvider(car.clocks_hz["sync"])
        m.submodules.btn = FFSynchronizer(
                platform.request("encoder").s.i, reboot.button)

        ulpi = platform.request(platform.default_usb_connection)
        m.submodules.usb = usb = USBDevice(bus=ulpi)

        # Add our standard control endpoint to the device.
        descriptors = self.create_descriptors()
        control_ep = usb.add_control_endpoint()
        control_ep.add_standard_request_handlers(descriptors, blacklist=[
            lambda setup:   (setup.type    == USBRequestType.STANDARD)
                          & (setup.request == USBStandardRequests.SET_INTERFACE)
        ])

        # Attach our class request handlers.
        class_request_handler = UAC2RequestHandlers()
        control_ep.add_request_handler(class_request_handler)

        # Attach class-request handlers that stall any vendor or reserved requests,
        # as we don't have or need any.
        stall_condition = lambda setup : \
            (setup.type == USBRequestType.VENDOR) | \
            (setup.type == USBRequestType.RESERVED)
        control_ep.add_request_handler(StallOnlyRequestHandler(stall_condition))

        ep1_out = USBIsochronousOutStreamEndpoint(
            endpoint_number=1, # EP 1 OUT
            max_packet_size=self.MAX_PACKET_SIZE)
        usb.add_endpoint(ep1_out)

        ep1_in = USBIsochronousInMemoryEndpoint(
            endpoint_number=1, # EP 1 IN
            max_packet_size=4)
        usb.add_endpoint(ep1_in)

        ep2_in = USBIsochronousInStreamEndpoint(
            endpoint_number=2, # EP 2 IN
            max_packet_size=self.MAX_PACKET_SIZE)
        usb.add_endpoint(ep2_in)

        # MIDI endpoints
        usb_ep3_out = USBStreamOutEndpoint(
            endpoint_number=3, # EP 3 OUT
            max_packet_size=self.MAX_PACKET_SIZE_MIDI)
        usb.add_endpoint(usb_ep3_out)

        usb_ep3_in = USBStreamInEndpoint(
            endpoint_number=3, # EP 3 IN
            max_packet_size=self.MAX_PACKET_SIZE_MIDI)
        usb.add_endpoint(usb_ep3_in)

        # calculate bytes in frame for audio in
        audio_in_frame_bytes = Signal(range(self.MAX_PACKET_SIZE), reset=24 * self.NR_CHANNELS)
        audio_in_frame_bytes_counting = Signal()

        with m.If(ep1_out.stream.valid & ep1_out.stream.ready):
            with m.If(audio_in_frame_bytes_counting):
                m.d.usb += audio_in_frame_bytes.eq(audio_in_frame_bytes + 1)

            with m.If(ep1_out.stream.first):
                m.d.usb += [
                    audio_in_frame_bytes.eq(1),
                    audio_in_frame_bytes_counting.eq(1),
                ]
            with m.Elif(ep1_out.stream.last):
                m.d.usb += audio_in_frame_bytes_counting.eq(0)

        # Connect our device as a high speed device
        m.d.comb += [
            ep1_in.bytes_in_frame.eq(4),
            ep2_in.bytes_in_frame.eq(audio_in_frame_bytes),
            usb.connect          .eq(1),
            usb.full_speed_only  .eq(0),
        ]

        # feedback endpoint
        feedbackValue      = Signal(32, reset=0x60000)
        bitPos             = Signal(5)

        # this tracks the number of audio frames since the last USB frame
        # 12.288MHz / 8kHz = 1536, so we need at least 11 bits = 2048
        # we need to capture 32 micro frames to get to the precision
        # required by the USB standard, so and that is 0xc000, so we
        # need 16 bits here
        audio_clock_counter = Signal(24)
        sof_counter         = Signal(5)

        audio_clock_usb = Signal()
        m.submodules.audio_clock_usb_sync = FFSynchronizer(ClockSignal("audio"), audio_clock_usb, o_domain="usb")
        m.submodules.audio_clock_usb_pulse = audio_clock_usb_pulse = DomainRenamer("usb")(EdgeToPulse())
        audio_clock_tick = Signal()
        m.d.usb += [
            audio_clock_usb_pulse.edge_in.eq(audio_clock_usb),
            audio_clock_tick.eq(audio_clock_usb_pulse.pulse_out),
        ]

        with m.If(audio_clock_tick):
            m.d.usb += audio_clock_counter.eq(audio_clock_counter + 1)

        with m.If(usb.sof_detected):
            m.d.usb += sof_counter.eq(sof_counter + 1)

            # according to USB2 standard chapter 5.12.4.2
            # we need 2**13 / 2**8 = 2**5 = 32 SOF-frames of
            # sample master frequency counter to get enough
            # precision for the sample frequency estimate
            # / 2**8 because the ADAT-clock = 256 times = 2**8
            # the sample frequency and sof_counter is 5 bits
            # so it wraps automatically every 32 SOFs
            with m.If(sof_counter == 0):
                m.d.usb += [
                    # FIFO feedback?
                    feedbackValue.eq(audio_clock_counter << 3),
                    audio_clock_counter.eq(0),
                ]

        m.d.comb += [
            bitPos.eq(ep1_in.address << 3),
            ep1_in.value.eq(0xff & (feedbackValue >> bitPos)),
        ]

        m.submodules.usb_to_channel_stream = usb_to_channel_stream = \
            DomainRenamer("usb")(USBStreamToChannels(self.NR_CHANNELS))

        m.submodules.channels_to_usb_stream = channels_to_usb_stream = \
            DomainRenamer("usb")(ChannelsToUSBStream(self.NR_CHANNELS))

        def detect_active_audio_in(m, name: str, usb, ep2_in):
            audio_in_seen   = Signal(name=f"{name}_audio_in_seen")
            audio_in_active = Signal(name=f"{name}_audio_in_active")

            # detect if we don't have a USB audio IN packet
            with m.If(usb.sof_detected):
                m.d.usb += [
                    audio_in_active.eq(audio_in_seen),
                    audio_in_seen.eq(0),
                ]

            with m.If(ep2_in.data_requested):
                m.d.usb += audio_in_seen.eq(1)

            return audio_in_active

            usb_audio_in_active  = detect_active_audio_in(m, "usb", usb, ep2_in)

        usb_audio_in_active = detect_active_audio_in(m, "usb", usb, ep2_in)

        m.d.comb += [
            # Wire USB <-> stream synchronizers
            usb_to_channel_stream.usb_stream_in.stream_eq(ep1_out.stream),
            ep2_in.stream.stream_eq(channels_to_usb_stream.usb_stream_out),

            channels_to_usb_stream.no_channels_in.eq(self.NR_CHANNELS),
            channels_to_usb_stream.data_requested_in.eq(ep2_in.data_requested),
            channels_to_usb_stream.frame_finished_in.eq(ep2_in.frame_finished),
            channels_to_usb_stream.audio_in_active.eq(usb_audio_in_active),
            usb_to_channel_stream.no_channels_in.eq(self.NR_CHANNELS),
        ]

        m.submodules.pmod0 = pmod0 = EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)
        m.d.comb += pmod0.codec_mute.eq(reboot.mute)

        m.submodules.audio_to_channels = AudioToChannels(
                pmod0,
                to_usb_stream=channels_to_usb_stream.channel_stream_in,
                from_usb_stream=usb_to_channel_stream.channel_stream_out)

        jack_period = Signal(32)
        jack_usb = Signal(8)
        m.submodules.jack_sync = FFSynchronizer(pmod0.jack, jack_usb, o_domain="usb")

        N_TOUCH_CHANNELS = 8
        touch_usb = []
        for n in range(N_TOUCH_CHANNELS):
            touch_usb.append(Signal(8))
            setattr(m.submodules, f"touch_usb_synchronizer{n}",
                    FFSynchronizer(pmod0.touch[n], touch_usb[n], o_domain="usb"))

        touch_ch = Signal(3)

        with m.FSM(domain="usb") as fsm:
            with m.State("WAIT"):
                # 100Hz // TODO make this delta
                with m.If(jack_period == int(60000000 / 40)):
                    m.d.usb += [
                        jack_period.eq(0),
                        touch_ch.eq(0)
                    ]
                    m.next = "B0"
                with m.Else():
                    m.d.usb += jack_period.eq(jack_period + 1)
            with m.State("B0"):
                m.d.comb += [
                    usb_ep3_in.stream.payload.eq(0x0B),
                    usb_ep3_in.stream.first.eq(touch_ch == 0),
                    usb_ep3_in.stream.valid.eq(1),
                ]
                with m.If(usb_ep3_in.stream.ready):
                    m.next = "B1"
            with m.State("B1"):
                m.d.comb += [
                    usb_ep3_in.stream.payload.eq(0xB0),
                    usb_ep3_in.stream.valid.eq(1),
                ]
                with m.If(usb_ep3_in.stream.ready):
                    m.next = "B2"
            with m.State("B2"):
                m.d.comb += [
                    usb_ep3_in.stream.payload.eq(touch_ch),
                    usb_ep3_in.stream.valid.eq(1),
                ]
                with m.If(usb_ep3_in.stream.ready):
                    m.next = "B3"
            with m.State("B3"):

                m.d.comb += [
                    usb_ep3_in.stream.last.eq(touch_ch == (N_TOUCH_CHANNELS - 1)),
                    usb_ep3_in.stream.valid.eq(1),
                ]

                # Infer mux of active channel to payload
                with m.Switch(touch_ch):
                    for n in range(N_TOUCH_CHANNELS):
                        with m.Case(n):
                            # Shift to get 0-127 as MIDI CC requires
                            m.d.comb += usb_ep3_in.stream.payload.eq(touch_usb[n] >> 1),

                with m.If(usb_ep3_in.stream.ready):
                    with m.If(touch_ch == (N_TOUCH_CHANNELS - 1)):
                        m.next = "WAIT"
                    with m.Else():
                        m.d.usb += touch_ch.eq(touch_ch + 1)
                        m.next = "B0"

        if platform.ila:

            test_signal = Signal(16, reset=0xFEED)
            pmod_sample_o0 = Signal(16)

            m.d.comb += pmod_sample_o0.eq(pmod0.sample_o[0])

            ila_signals = [
                test_signal,
                pmod_sample_o0,
                pmod0.fs_strobe,
                m.submodules.audio_to_channels.dac_fifo_level,

                # channel stream
                #usb_to_channel_stream.channel_stream_out.channel_nr,
                #usb_to_channel_stream.channel_stream_out.payload,
                #usb_to_channel_stream.channel_stream_out.valid,
                #usb_to_channel_stream.garbage_seen_out,

                # interface from IsochronousOutStreamEndpoint
                #usb_to_channel_stream.usb_stream_in.first,
                #usb_to_channel_stream.usb_stream_in.valid,
                #usb_to_channel_stream.usb_stream_in.payload,
                #usb_to_channel_stream.usb_stream_in.last,
                #usb_to_channel_stream.usb_stream_in.ready,

                # interface to IsochronousOutStreamEndpoint
                #ep1_out.interface.rx.next,
                #ep1_out.interface.rx.valid,
                #ep1_out.interface.rx.payload,

                usb.sof_detected,
                sof_counter,
                feedbackValue,
                bitPos,
            ]

            self.ila = AsyncSerialILA(signals=ila_signals,
                                      sample_depth=8192, divisor=521,
                                      domain='usb', sample_rate=60e6) # ~115200 baud on USB clock
            m.submodules += self.ila

            m.d.comb += [
                self.ila.trigger.eq(pmod0.sample_o[0] > Const(1000)),
                #self.ila.trigger.eq(usb_audio_in_active),
                platform.request("uart").tx.o.eq(self.ila.tx), # needs FFSync?
            ]

        return m

class UAC2RequestHandlers(USBRequestHandler):
    """ request handlers to implement UAC2 functionality. """
    def __init__(self):
        super().__init__()

        self.output_interface_altsetting_nr = Signal(3)
        self.input_interface_altsetting_nr  = Signal(3)
        self.interface_settings_changed     = Signal()

    def elaborate(self, platform):
        m = Module()

        interface         = self.interface
        setup             = self.interface.setup

        m.submodules.transmitter = transmitter = \
            StreamSerializer(data_length=14, domain="usb", stream_type=USBInStreamInterface, max_length_width=14)

        m.d.usb += self.interface_settings_changed.eq(0)

        #
        # Class request handlers.
        #
        with m.If(setup.type == USBRequestType.STANDARD):
            with m.If((setup.recipient == USBRequestRecipient.INTERFACE) &
                      (setup.request == USBStandardRequests.SET_INTERFACE)):

                m.d.comb += interface.claim.eq(1)

                interface_nr   = setup.index
                alt_setting_nr = setup.value

                m.d.usb += [
                    self.output_interface_altsetting_nr.eq(0),
                    self.input_interface_altsetting_nr.eq(0),
                    self.interface_settings_changed.eq(1),
                ]

                with m.Switch(interface_nr):
                    with m.Case(1):
                        m.d.usb += self.output_interface_altsetting_nr.eq(alt_setting_nr)
                    with m.Case(2):
                        m.d.usb += self.input_interface_altsetting_nr.eq(alt_setting_nr)

                # Always ACK the data out...
                with m.If(interface.rx_ready_for_response):
                    m.d.comb += interface.handshakes_out.ack.eq(1)

                # ... and accept whatever the request was.
                with m.If(interface.status_requested):
                    m.d.comb += self.send_zlp()

        request_clock_freq = (setup.value == 0x100) & (setup.index == 0x0100)
        with m.Elif(setup.type == USBRequestType.CLASS):
            with m.Switch(setup.request):
                with m.Case(AudioClassSpecificRequestCodes.RANGE):
                    m.d.comb += interface.claim.eq(1)
                    m.d.comb += transmitter.stream.attach(self.interface.tx)

                    with m.If(request_clock_freq):
                        m.d.comb += [
                            Cat(transmitter.data).eq(
                                Cat(Const(0x1, 16), # no triples
                                    Const(48000, 32), # MIN
                                    Const(48000, 32), # MAX
                                    Const(0, 32))),   # RES
                            transmitter.max_length.eq(setup.length)
                        ]
                    with m.Else():
                        m.d.comb += interface.handshakes_out.stall.eq(1)

                    # ... trigger it to respond when data's requested...
                    with m.If(interface.data_requested):
                        m.d.comb += transmitter.start.eq(1)

                    # ... and ACK our status stage.
                    with m.If(interface.status_requested):
                        m.d.comb += interface.handshakes_out.ack.eq(1)

                with m.Case(AudioClassSpecificRequestCodes.CUR):
                    m.d.comb += interface.claim.eq(1)
                    m.d.comb += transmitter.stream.attach(self.interface.tx)
                    with m.If(request_clock_freq & (setup.length == 4)):
                        m.d.comb += [
                            Cat(transmitter.data[0:4]).eq(Const(48000, 32)),
                            transmitter.max_length.eq(4)
                        ]
                    with m.Else():
                        m.d.comb += interface.handshakes_out.stall.eq(1)

                    # ... trigger it to respond when data's requested...
                    with m.If(interface.data_requested):
                        m.d.comb += transmitter.start.eq(1)

                    # ... and ACK our status stage.
                    with m.If(interface.status_requested):
                        m.d.comb += interface.handshakes_out.ack.eq(1)

                return m

if __name__ == "__main__":
    top_level_cli(USB2AudioInterface, video_core=False, ila_supported=True)
