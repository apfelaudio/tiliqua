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

    i: In(stream.Signature(data.ArrayLayout(ASQ, 2)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 1)))

    def elaborate(self, platform):
        m = Module()

        m.d.comb += [
            self.o.payload[0].eq(self.i.payload[0] * self.i.payload[1]),
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
        ]

        return m

class AudioStreamSplitter(wiring.Component):

    def __init__(self, n_channels):
        self.n_channels = n_channels
        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(ASQ, n_channels))),
            "o": Out(stream.Signature(ASQ)).array(n_channels),
        })

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.i.ready.eq(Cat([self.o[n].ready for n in range(self.n_channels)]).all())
        m.d.comb += [self.o[n].payload.eq(self.i.payload[n]) for n in range(self.n_channels)]
        m.d.comb += [self.o[n].valid.eq(self.i.valid) for n in range(self.n_channels)]

        return m

class AudioStreamCombiner(wiring.Component):

    def __init__(self, n_channels):
        self.n_channels = n_channels
        super().__init__({
            "i": In(stream.Signature(ASQ)).array(n_channels),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, n_channels))),
        })

    def elaborate(self, platform):
        m = Module()

        m.d.comb += [self.i[n].ready.eq(self.o.ready) for n in range(self.n_channels)]
        m.d.comb += [self.o.payload[n].eq(self.i[n].payload) for n in range(self.n_channels)]
        m.d.comb += self.o.valid.eq(Cat([self.i[n].valid for n in range(self.n_channels)]).all())

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

        wiring.connect(m, audio_stream.istream, audio_stream.ostream)

        return m

class VCATop(Elaboratable):

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = platform.clock_domain_generator()

        m.submodules.pmod0 = pmod0 = eurorack_pmod.EurorackPmod(
                pmod_pins=platform.request("audio_ffc"),
                hardware_r33=True)

        m.submodules.audio_stream = audio_stream = AudioStream(pmod0)

        m.submodules.splitter4 = splitter4 = AudioStreamSplitter(n_channels=4)
        m.submodules.combiner4 = combiner4 = AudioStreamCombiner(n_channels=4)

        m.submodules.combiner2 = combiner2 = AudioStreamCombiner(n_channels=2)

        m.submodules.vca0 = vca0 = VCA()

        wiring.connect(m, audio_stream.istream, splitter4.i)
        print(Value.cast(splitter4.o[0].payload))
        print(Value.cast(combiner2.i[0].payload))
        wiring.connect(m, splitter4.o[0], combiner2.i[0])
        wiring.connect(m, splitter4.o[1], combiner2.i[1])
        wiring.connect(m, splitter4.o[2], stream.Signature(ASQ, always_ready=True).flip().create())
        wiring.connect(m, splitter4.o[3], stream.Signature(ASQ, always_ready=True).flip().create())
        wiring.connect(m, combiner2.o, vca0.i)
        wiring.connect(m, vca0.o, combiner4.i[0])
        wiring.connect(m, stream.Signature(ASQ, always_valid=True).create(), combiner4.i[1])
        wiring.connect(m, stream.Signature(ASQ, always_valid=True).create(), combiner4.i[2])
        wiring.connect(m, stream.Signature(ASQ, always_valid=True).create(), combiner4.i[3])
        wiring.connect(m, combiner4.o, audio_stream.ostream)

        return m

def build_mirror():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(MirrorTop())

def build_vca():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    TiliquaPlatform().build(VCATop())
