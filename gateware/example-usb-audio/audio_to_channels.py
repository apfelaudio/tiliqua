# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD--3-Clause

from amaranth              import *
from amaranth.lib.fifo     import AsyncFIFO

class AudioToChannels(Elaboratable):

    """
    Domain crossing logic to move samples from `eurorack-pmod` logic in the audio domain
    to `channels_to_usb_stream` and `usb_stream_to_channels` logic in the USB domain.
    """

    def __init__(self, eurorack_pmod, to_usb_stream, from_usb_stream):

        self.to_usb = to_usb_stream
        self.from_usb = from_usb_stream
        self.eurorack_pmod = eurorack_pmod

    def elaborate(self, platform) -> Module:

        m = Module()

        eurorack_pmod = self.eurorack_pmod

        # Sample widths
        SW      = eurorack_pmod.width       # Sample width used in underlying I2S driver.
        SW_USB  = self.to_usb.payload.width # Sample width used for USB transfers.
        N_ZFILL = SW_USB - SW               # Zero padding if SW < SW_USB

        assert(N_ZFILL >= 0)

        #
        # INPUT SIDE
        # eurorack-pmod calibrated INPUT samples -> USB Channel stream -> HOST
        #

        m.submodules.adc_fifo = adc_fifo = AsyncFIFO(width=SW*4, depth=64, w_domain="audio", r_domain="usb")

        # (audio domain) on every sample strobe, latch and write all channels concatenated into one entry
        # of adc_fifo.

        m.d.audio += [
            # FIXME: ignoring rdy in write domain. Should be fine as write domain
            # will always be slower than the read domain, but should be fixed.
            adc_fifo.w_en.eq(eurorack_pmod.fs_strobe),
            adc_fifo.w_data[    :SW*1].eq(eurorack_pmod.cal_in0),
            adc_fifo.w_data[SW*1:SW*2].eq(eurorack_pmod.cal_in1),
            adc_fifo.w_data[SW*2:SW*3].eq(eurorack_pmod.cal_in2),
            adc_fifo.w_data[SW*3:SW*4].eq(eurorack_pmod.cal_in3),
        ]

        # (usb domain) unpack samples from the adc_fifo (one big concatenated
        # entry with samples for all channels once per sample strobe) and feed them
        # into ChannelsToUSBStream with one entry per channel, i.e 1 -> 4 entries
        # per sample strobe in the audio domain.

        # Storage for samples in the USB domain as we send them to the channel stream.
        adc_latched = Signal(SW*4)

        with m.FSM(domain="usb") as fsm:

            with m.State('WAIT'):
                m.d.usb += self.to_usb.valid.eq(0),
                with m.If(adc_fifo.r_rdy):
                    m.d.usb += adc_fifo.r_en.eq(1)
                    m.next = 'LATCH'

            with m.State('LATCH'):
                m.d.usb += [
                    adc_fifo.r_en.eq(0),
                    adc_latched.eq(adc_fifo.r_data)
                ]
                m.next = 'CH0'

            def generate_channel_states(channel, next_state_name):
                with m.State(f'CH{channel}'):
                    m.d.usb += [
                        # FIXME: currently filling bottom bits with zeroes for SW bit -> SW_USB bit
                        # sample conversion. Better to just switch native rate of I2S driver.
                        self.to_usb.payload.eq(
                            Cat(Const(0, N_ZFILL), adc_latched[channel*SW:(channel+1)*SW])),
                        self.to_usb.channel_no.eq(channel),
                        self.to_usb.valid.eq(1),
                    ]
                    m.next = f'CH{channel}-SEND'
                with m.State(f'CH{channel}-SEND'):
                    with m.If(self.to_usb.ready):
                        m.d.usb += self.to_usb.valid.eq(0)
                        m.next = next_state_name

            generate_channel_states(0, 'CH1')
            generate_channel_states(1, 'CH2')
            generate_channel_states(2, 'CH3')
            generate_channel_states(3, 'WAIT')

        #
        # OUTPUT SIDE
        # HOST -> USB Channel stream -> eurorack-pmod calibrated OUTPUT samples.
        #

        for n, output in zip(range(4), [eurorack_pmod.cal_out0, eurorack_pmod.cal_out1,
                                        eurorack_pmod.cal_out2, eurorack_pmod.cal_out3]):

            # FIXME: we shouldn't need one FIFO per channel
            fifo = AsyncFIFO(width=SW, depth=64, w_domain="usb", r_domain="audio")
            setattr(m.submodules, f'dac_fifo{n}', fifo)

            # (usb domain) if the channel_no matches, demux it into the correct channel FIFO
            m.d.comb += [
                fifo.w_data.eq(self.from_usb.payload[N_ZFILL:]),
                fifo.w_en.eq((self.from_usb.channel_no == n) &
                             self.from_usb.valid),
            ]

            # (audio domain) once fs_strobe hits, write the next pending sample to eurorack_pmod.
            with m.FSM(domain="audio") as fsm:
                with m.State('READ'):
                    with m.If(eurorack_pmod.fs_strobe & fifo.r_rdy):
                        m.d.audio += fifo.r_en.eq(1)
                        m.next = 'SEND'
                with m.State('SEND'):
                    m.d.audio += [
                        fifo.r_en.eq(0),
                        output.eq(fifo.r_data),
                    ]
                    m.next = 'READ'

        # FIXME: make this less lenient
        m.d.comb += self.from_usb.ready.eq(
            m.submodules.dac_fifo0.w_rdy | m.submodules.dac_fifo1.w_rdy |
            m.submodules.dac_fifo2.w_rdy | m.submodules.dac_fifo3.w_rdy)

        return m
