# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD--3-Clause

import os

from amaranth              import *
from amaranth.build        import *

from amaranth.lib.fifo     import AsyncFIFO

from tiliqua.tiliqua_platform import TiliquaPlatform
from tiliqua.eurorack_pmod import EurorackPmod

class AudioStream(Elaboratable):

    """
    Domain crossing logic to move samples from `eurorack-pmod` logic in the audio domain
    to logic in a different domain using a stream interface.
    """

    def __init__(self, eurorack_pmod, out_stream, in_stream, stream_domain="sync", fifo_depth=8):

        self.out_stream = out_stream
        self.in_stream = in_stream
        self.eurorack_pmod = eurorack_pmod
        self.stream_domain = stream_domain
        self.fifo_depth = fifo_depth

    def elaborate(self, platform) -> Module:

        m = Module()

        eurorack_pmod = self.eurorack_pmod
        SW      = eurorack_pmod.width       # Sample width used in underlying I2S driver.

        #
        # INPUT SIDE
        # eurorack-pmod calibrated INPUT samples -> out_stream
        #

        m.submodules.adc_fifo = adc_fifo = AsyncFIFO(width=SW*4, depth=self.fifo_depth, w_domain="audio", r_domain=self.stream_domain)

        # (audio domain) on every sample strobe, latch and write all channels concatenated into one entry
        # of adc_fifo.

        m.d.audio += [
            # FIXME: ignoring rdy in write domain. Should be fine as write domain
            # will always be slower than the read domain, but should be fixed.
            adc_fifo.w_en.eq(eurorack_pmod.fs_strobe),
            adc_fifo.w_data[    :SW*1].eq(eurorack_pmod.sample_i[0]),
            adc_fifo.w_data[SW*1:SW*2].eq(eurorack_pmod.sample_i[1]),
            adc_fifo.w_data[SW*2:SW*3].eq(eurorack_pmod.sample_i[2]),
            adc_fifo.w_data[SW*3:SW*4].eq(eurorack_pmod.sample_i[3]),
        ]

        # (stream domain)

        m.d.comb += [
            self.out_stream.valid.eq(adc_fifo.r_rdy),
            adc_fifo.r_en.eq(self.out_stream.ready),
            self.out_stream.payload.eq(adc_fifo.r_data),
        ]

        #
        # OUTPUT SIDE
        # in_stream -> eurorack-pmod calibrated OUTPUT samples.
        #

        m.submodules.dac_fifo = dac_fifo = AsyncFIFO(width=SW*4, depth=self.fifo_depth, w_domain=self.stream_domain, r_domain="audio")

        # (stream domain)
        m.d.comb += [
            dac_fifo.w_en.eq(self.in_stream.valid),
            in_stream.ready.eq(dac_fifo.w_rdy),
            dac_fifo.w_data.eq(stream.payload),
        ]

        # (audio domain) once fs_strobe hits, write the next pending sample to eurorack_pmod.
        with m.FSM(domain="audio") as fsm:
            with m.State('READ'):
                with m.If(eurorack_pmod.fs_strobe & dac_fifo.r_rdy):
                    m.d.audio += dac_fifo.r_en.eq(1)
                    m.next = 'SEND'
            with m.State('SEND'):
                m.d.audio += [
                    dac_fifo.r_en.eq(0),
                    eurorack_pmod.sample_o[0].eq(dac_fifo.r_data[    :SW*1]),
                    eurorack_pmod.sample_o[1].eq(dac_fifo.r_data[SW*1:SW*2]),
                    eurorack_pmod.sample_o[2].eq(dac_fifo.r_data[SW*2:SW*3]),
                    eurorack_pmod.sample_o[3].eq(dac_fifo.r_data[SW*3:SW*4]),
                ]
                m.next = 'READ'

        return m

class MirrorTop(Elaboratable):
    """Route audio inputs straight to outputs (in the audio domain)."""

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = platform.clock_domain_generator()

        m.submodules.pmod0 = pmod0 = EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

        m.d.comb += [pmod0.sample_o[i].eq(pmod0.sample_i[i]) for i in range(4)]

        return m

def build():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(MirrorTop())
