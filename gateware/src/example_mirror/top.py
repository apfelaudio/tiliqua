# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD--3-Clause

import os

from amaranth              import *
from amaranth.build        import *
from amaranth.lib          import wiring, data
from amaranth.lib.wiring   import In, Out

from amaranth.lib.fifo     import AsyncFIFO

from amaranth_future       import stream, fixed

from tiliqua.tiliqua_platform import TiliquaPlatform
from tiliqua                  import eurorack_pmod
from tiliqua.eurorack_pmod    import ASQ

class AudioStream(wiring.Component):

    """
    Domain crossing logic to move samples from `eurorack-pmod` logic in the audio domain
    to logic in a different (faster) domain using a stream interface.
    """

    istream: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))
    ostream: In(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def __init__(self, eurorack_pmod, stream_domain="sync", fifo_depth=8):

        self.eurorack_pmod = eurorack_pmod
        self.stream_domain = stream_domain
        self.fifo_depth = fifo_depth

        super().__init__()

    def elaborate(self, platform) -> Module:

        m = Module()

        m.submodules.adc_fifo = adc_fifo = AsyncFIFO(
                width=self.eurorack_pmod.sample_i.shape().size, depth=self.fifo_depth,
                w_domain="audio", r_domain=self.stream_domain)
        m.submodules.dac_fifo = dac_fifo = AsyncFIFO(
                width=self.eurorack_pmod.sample_o.shape().size, depth=self.fifo_depth,
                w_domain=self.stream_domain, r_domain="audio")

        adc_stream = stream.fifo_r_stream(adc_fifo)
        dac_stream = wiring.flipped(stream.fifo_w_stream(dac_fifo))

        wiring.connect(m, adc_stream, wiring.flipped(self.istream))
        wiring.connect(m, wiring.flipped(self.ostream), dac_stream)

        eurorack_pmod = self.eurorack_pmod

        # below is synchronous logic in the *audio domain*

        # On every fs_strobe, latch and write all channels concatenated
        # into one entry of adc_fifo.

        m.d.audio += [
            # WARN: ignoring rdy in write domain. Mostly fine as long as
            # stream_domain is faster than audio_domain.
            adc_fifo.w_en.eq(eurorack_pmod.fs_strobe),
            adc_fifo.w_data.eq(self.eurorack_pmod.sample_i),
        ]


        # Once fs_strobe hits, write the next pending samples to CODEC

        with m.FSM(domain="audio") as fsm:
            with m.State('READ'):
                with m.If(eurorack_pmod.fs_strobe & dac_fifo.r_rdy):
                    m.d.audio += dac_fifo.r_en.eq(1)
                    m.next = 'SEND'
            with m.State('SEND'):
                m.d.audio += [
                    dac_fifo.r_en.eq(0),
                    self.eurorack_pmod.sample_o.eq(dac_fifo.r_data),
                ]
                m.next = 'READ'

        return m

class VCA(wiring.Component):

    i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

    def elaborate(self, platform):
        m = Module()

        wiring.connect(m, wiring.flipped(self.i), wiring.flipped(self.o))

        m.d.comb += self.o.payload[0].eq(self.i.payload[0] * self.i.payload[1])

        return m

class MirrorTop(Elaboratable):
    """Route audio inputs straight to outputs (in the audio domain)."""

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = platform.clock_domain_generator()

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

        m.submodules.audio_stream = audio_stream = AudioStream(pmod0)

        m.submodules.vca = vca = VCA()
        wiring.connect(m, audio_stream.istream, vca.i)
        wiring.connect(m, vca.o, audio_stream.ostream)

        return m

def build():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(MirrorTop())
