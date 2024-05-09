from amaranth              import *
from amaranth.lib          import wiring, data
from amaranth.lib.wiring   import In, Out
from amaranth.hdl.mem      import Memory
from amaranth.utils        import log2_int

from amaranth_future       import stream, fixed

from tiliqua.eurorack_pmod import ASQ # hardware native fixed-point sample type

# dummy values used to hook up to unused stream in/out ports, so they don't block forever
ASQ_READY = stream.Signature(ASQ, always_ready=True).flip().create()
ASQ_VALID = stream.Signature(ASQ, always_valid=True).create()

class Split(wiring.Component):

    """
    Split a single stream into multiple independent streams.
    """

    def __init__(self, n_channels, replicate=False):
        self.n_channels = n_channels
        self.replicate  = replicate

        if self.replicate:
            super().__init__({
                "i": In(stream.Signature(ASQ)),
                "o": Out(stream.Signature(ASQ)).array(n_channels),
            })
        else:
            super().__init__({
                "i": In(stream.Signature(data.ArrayLayout(ASQ, n_channels))),
                "o": Out(stream.Signature(ASQ)).array(n_channels),
            })

    def elaborate(self, platform):
        m = Module()

        done = Signal(self.n_channels)

        m.d.comb += self.i.ready.eq(Cat([self.o[n].ready | done[n] for n in range(self.n_channels)]).all())
        m.d.comb += [self.o[n].valid.eq(self.i.valid & ~done[n]) for n in range(self.n_channels)]

        if self.replicate:
            m.d.comb += [self.o[n].payload.eq(self.i.payload) for n in range(self.n_channels)]
        else:
            m.d.comb += [self.o[n].payload.eq(self.i.payload[n]) for n in range(self.n_channels)]

        flow = [self.o[n].valid & self.o[n].ready
                for n in range(self.n_channels)]
        end  = Cat([flow[n] | done[n]
                    for n in range(self.n_channels)]).all()
        with m.If(end):
            m.d.sync += done.eq(0)
        with m.Else():
            for n in range(self.n_channels):
                with m.If(flow[n]):
                    m.d.sync += done[n].eq(1)

        return m

class Merge(wiring.Component):

    """
    Merge multiple independent streams into a single stream.
    """

    def __init__(self, n_channels):
        self.n_channels = n_channels
        super().__init__({
            "i": In(stream.Signature(ASQ)).array(n_channels),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, n_channels))),
        })

    def elaborate(self, platform):
        m = Module()

        m.d.comb += [self.i[n].ready.eq(self.o.ready & self.o.valid) for n in range(self.n_channels)]
        m.d.comb += [self.o.payload[n].eq(self.i[n].payload) for n in range(self.n_channels)]
        m.d.comb += self.o.valid.eq(Cat([self.i[n].valid for n in range(self.n_channels)]).all())

        return m

class VCA(wiring.Component):

    """
    Voltage Controlled Amplifier.
    """

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

class SawNCO(wiring.Component):

    """
    Sawtooth Numerically Controlled Oscillator.

    FIXME: tune this 1V/Oct
    """

    i: In(stream.Signature(ASQ))
    o: Out(stream.Signature(ASQ))

    def elaborate(self, platform):
        m = Module()

        s = Signal(fixed.SQ(16, ASQ.f_width))

        m.d.comb += [
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
        ]

        with m.If(self.i.valid):
            m.d.sync += [
                s.eq(s + self.i.payload),
                self.o.payload.eq(s.round() >> 6),
            ]

        return m

class SVF(wiring.Component):

    """
    Oversampled Chamberlin State Variable Filter.

    Reference: Fig.3 in https://arxiv.org/pdf/2111.05592

    """

    i: In(stream.Signature(data.StructLayout({
            "x": ASQ,
            "cutoff": ASQ,
            "resonance": ASQ,
        })))

    o: Out(stream.Signature(data.StructLayout({
            "hp": ASQ,
            "lp": ASQ,
            "bp": ASQ,
        })))

    def elaborate(self, platform):
        m = Module()

        # is this stable with only 18 bits? (native multiplier width)
        dtype = fixed.SQ(2, ASQ.f_width)

        abp   = Signal(dtype)
        alp   = Signal(dtype)
        ahp   = Signal(dtype)
        x     = Signal(dtype)
        kK    = Signal(dtype)
        kQinv = Signal(dtype)

        # internal oversampling iterations
        n_oversample = 2
        oversample = Signal(8)

        with m.FSM() as fsm:
            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                   m.d.sync += x.eq(self.i.payload.x),
                   m.d.sync += oversample.eq(0)
                   # FIXME: signedness check without working around `fixed`
                   with m.If(self.i.payload.cutoff.as_value()[15] == 0):
                       m.d.sync += kK.eq(self.i.payload.cutoff)
                   with m.If(self.i.payload.resonance.as_value()[15] == 0):
                       m.d.sync += kQinv.eq(self.i.payload.resonance)
                   m.next = 'MAC0'
            with m.State('MAC0'):
                m.d.sync += alp.eq(abp*kK + alp)
                m.next = 'MAC1'
            with m.State('MAC1'):
                m.d.sync += ahp.eq(x - alp - kQinv*abp)
                m.next = 'MAC2'
            with m.State('MAC2'):
                m.d.sync += abp.eq(ahp*kK + abp)
                with m.If(oversample != n_oversample - 1):
                    m.d.sync += oversample.eq(oversample + 1)
                    m.next = 'MAC0'
                with m.Else():
                    # FIXME: average of last N oversamples, instead of last
                    m.next = 'WAIT-READY'
            with m.State('WAIT-READY'):
                m.d.comb += [
                    self.o.valid.eq(1),
                    self.o.payload.hp.eq(ahp >> 1),
                    self.o.payload.lp.eq(alp >> 1),
                    self.o.payload.bp.eq(abp >> 1),
                ]
                with m.If(self.o.ready):
                    m.next = 'WAIT-VALID'

        return m

class DelayLine(wiring.Component):

    """
    Delay line with variable delay length. This can also be
    used as a fixed delay line or a wavetable / grain storage.

    - 'sw': sample write, each one written to an incrementing
    index in a local circular buffer.
    - 'da': delay address, each strobe (later) emits a 'ds' (sample),
    the value of the audio sample 'da' elements later than the
    last sample write 'sw' to occur up to 'max_delay'.

    Other uses:
    - If 'da' is a constant, this becomes a fixed delay line.
    - If 'sw' stop sending samples, this is like a frozen wavetable.

    """

    def __init__(self, max_delay=512):
        # max_delay must be a power of 2
        assert(2**log2_int(max_delay) == max_delay)
        self.max_delay = max_delay
        self.address_width = log2_int(max_delay)
        super().__init__({
            "sw": In(stream.Signature(ASQ)),
            "da": In(stream.Signature(unsigned(self.address_width))),
            "ds": Out(stream.Signature(ASQ)),
        })

    def elaborate(self, platform):
        m = Module()

        m.submodules.mem = mem = Memory(
            width=ASQ.as_shape().width, depth=self.max_delay, init=[])
        wport = mem.write_port()
        rport = mem.read_port(transparent=True)

        wrpointer = Signal(self.address_width)
        rdpointer = Signal(self.address_width)

        #
        # read side (da -> ds)
        #

        m.d.comb += [
            rport.addr.eq(rdpointer),
            self.ds.payload.eq(rport.data),
            self.da.ready.eq(1),
        ]

        # Set read pointer on valid delay address
        with m.If(self.da.valid):
            m.d.comb += [
                # Read pointer must be wrapped to max delay
                # Should wrap correctly as long as max delay is POW2
                rdpointer.eq(wrpointer - self.da.payload),
                rport.en.eq(1),
            ]
            m.d.sync += self.ds.valid.eq(1),
        with m.Else():
            m.d.sync += self.ds.valid.eq(0),

        #
        # write side (sw -> circular buffer)
        #

        m.d.comb += [
            self.sw.ready.eq(1),
            wport.addr.eq(wrpointer),
            wport.en.eq(self.sw.valid),
            wport.data.eq(self.sw.payload),
        ]

        with m.If(wport.en):
            with m.If(wrpointer != (self.max_delay - 1)):
                m.d.sync += wrpointer.eq(wrpointer + 1)
            with m.Else():
                m.d.sync += wrpointer.eq(0)

        return m

class PitchShift(wiring.Component):

    """
    Granular pitch shifter. Works by crossfading 2 separately
    tracked taps on a delay line. As a result, maximum grain
    size is the delay line 'max_delay' // 2.

    The delay line itself must be hooked up to the input audio
    source from outside this component (this allows multiple
    shifters to share a single delay line).
    """

    def __init__(self, delayln, xfade=256):
        assert(2**log2_int(xfade) == xfade)
        assert(xfade < delayln.max_delay/4)
        self.delayln    = delayln
        self.xfade      = xfade
        self.xfade_bits = log2_int(xfade)
        # delay type: integer component is index into delay line
        self.dtype = fixed.SQ(self.delayln.address_width, 8)
        super().__init__({
            "i": In(stream.Signature(data.StructLayout({
                    "pitch": self.dtype,
                    "grain_sz": unsigned(log2_int(delayln.max_delay)),
                  }))),
            "o": Out(stream.Signature(ASQ)),
        })

    def elaborate(self, platform):
        m = Module()


        # Current position in delay line 0, 1 (+= pitch every sample)
        delay0 = Signal(self.dtype)
        delay1 = Signal(self.dtype)
        # Last samples from delay lines
        sample0 = Signal(ASQ)
        sample1 = Signal(ASQ)
        # Envelope values
        env0 = Signal(ASQ)
        env1 = Signal(ASQ)

        s    = Signal(self.dtype)
        m.d.comb += s.eq(delay0 + self.i.payload.pitch)

        # Last latched grain size, pitch
        grain_sz_latched = Signal(self.i.payload.grain_sz.shape())

        # Second tap always uses second half of delay line.
        m.d.comb += delay1.eq(delay0 + grain_sz_latched)

        with m.FSM() as fsm:
            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                    pitch    = self.i.payload.pitch
                    grain_sz = self.i.payload.grain_sz
                    m.d.sync += grain_sz_latched.eq(grain_sz)
                    with m.If((delay0 + pitch) < fixed.Const(0, shape=self.dtype)):
                        m.d.sync += delay0.eq(delay0 + grain_sz + pitch)
                    with m.Elif((delay0 + pitch) > fixed.Value.cast(grain_sz)):
                        m.d.sync += delay0.eq(delay0 + pitch - grain_sz)
                    with m.Else():
                        m.d.sync += delay0.eq(delay0 + pitch)
                    m.next = 'TAP0'
            with m.State('TAP0'):
                m.d.comb += [
                    self.delayln.ds.ready.eq(1),
                    self.delayln.da.valid.eq(1),
                    self.delayln.da.payload.eq(delay0.round() >> delay0.f_width),
                ]
                with m.If(self.delayln.ds.valid):
                    m.d.sync += sample0.eq(self.delayln.ds.payload)
                    m.next = 'TAP1'
            with m.State('TAP1'):
                m.d.comb += [
                    self.delayln.ds.ready.eq(1),
                    self.delayln.da.valid.eq(1),
                    self.delayln.da.payload.eq(delay1.round() >> delay1.f_width),
                ]
                with m.If(self.delayln.ds.valid):
                    m.d.sync += sample1.eq(self.delayln.ds.payload)
                    m.next = 'ENV'
            with m.State('ENV'):
                with m.If(delay0 < self.xfade):
                    # Map delay0 <= [0, xfade] to env0 <= [0, 1]
                    m.d.sync += [
                        env0.eq(delay0 >> self.xfade_bits),
                        env1.eq(fixed.Const(0.99, shape=ASQ) -
                                (delay0 >> self.xfade_bits)),
                    ]
                with m.Else():
                    # If we're outside the xfade, just take tap 0
                    m.d.sync += [
                        env0.eq(fixed.Const(0.99, shape=ASQ)),
                        env1.eq(fixed.Const(0, shape=ASQ)),
                    ]
                m.next = 'WAIT-SOURCE-READY'
            with m.State('WAIT-SOURCE-READY'):
                m.d.comb += [
                    self.o.valid.eq(1),
                    # FIXME: move these into a MAC loop to save a multiplier.
                    self.o.payload.eq(
                        (sample0 * env0) + (sample1 * env1)
                    )
                ]
                with m.If(self.o.ready):
                    m.next = 'WAIT-VALID'
        return m


class Mix2(wiring.Component):

    i: In(stream.Signature(data.ArrayLayout(ASQ, 2)))
    o: Out(stream.Signature(data.ArrayLayout(ASQ, 1)))

    def elaborate(self, platform):
        m = Module()

        m.d.comb += [
            self.o.payload[0].eq((self.i.payload[0] >> 1) +
                                 (self.i.payload[1] >> 1)),
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
        ]

        return m
