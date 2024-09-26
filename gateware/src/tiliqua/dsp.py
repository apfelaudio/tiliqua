# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Streaming DSP library with a strong focus on audio."""

from amaranth              import *
from amaranth.lib          import wiring, data, stream
from amaranth.lib.wiring   import In, Out
from amaranth.lib.fifo     import SyncFIFOBuffered
from amaranth.lib.memory   import Memory
from amaranth.utils        import exact_log2, ceil_log2

from scipy import signal

from amaranth_future       import fixed

from tiliqua.eurorack_pmod import ASQ # hardware native fixed-point sample type

# dummy values used to hook up to unused stream in/out ports, so they don't block forever
ASQ_READY = stream.Signature(ASQ, always_ready=True).flip().create()
ASQ_VALID = stream.Signature(ASQ, always_valid=True).create()

class Split(wiring.Component):

    """
    Split a single stream into multiple independent streams.
    """

    def __init__(self, n_channels, replicate=False, source=None):
        self.n_channels = n_channels
        self.replicate  = replicate
        self.source     = source

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

        if self.source is not None:
            wiring.connect(m, self.source, self.i)

        return m

    def wire_ready(self, m, channels):
        """Set out channels as permanently READY so they don't block progress."""
        for n in channels:
            wiring.connect(m, self.o[n], ASQ_READY)

class Merge(wiring.Component):

    """
    Merge multiple independent streams into a single stream.
    """

    def __init__(self, n_channels, sink=None):
        self.n_channels = n_channels
        self.sink       = sink
        super().__init__({
            "i": In(stream.Signature(ASQ)).array(n_channels),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, n_channels))),
        })

    def elaborate(self, platform):
        m = Module()

        m.d.comb += [self.i[n].ready.eq(self.o.ready & self.o.valid) for n in range(self.n_channels)]
        m.d.comb += [self.o.payload[n].eq(self.i[n].payload) for n in range(self.n_channels)]
        m.d.comb += self.o.valid.eq(Cat([self.i[n].valid for n in range(self.n_channels)]).all())

        if self.sink is not None:
            wiring.connect(m, self.o, self.sink)

        return m

    def wire_valid(self, m, channels):
        """Set in channels as permanently VALID so they don't block progress."""
        for n in channels:
            wiring.connect(m, ASQ_VALID, self.i[n])

def connect_remap(m, stream_o, stream_i, mapping):
    """
    Connect 2 streams, bypassing normal wiring.connect() checks
    that the signatures match. This allows easily remapping fields when
    you are trying to connect streams with different signatures.

    For example, say I have a stream with an ArrayLayout payload and want to
    map it to a different stream with a StructLayout payload, and the underlying
    bit-representation of both layouts do not match, I can remap using:

    .. code-block:: python

        dsp.connect_remap(m, vca_merge2a.o, vca0.i, lambda o, i : [
            i.payload.x   .eq(o.payload[0]),
            i.payload.gain.eq(o.payload[1] << 2)
        ])

    This is a bit of a hack. TODO perhaps implement this as a StreamConverter
    such that we can still use wiring.connect?.
    """

    m.d.comb += mapping(stream_o, stream_i) + [
        stream_i.valid.eq(stream_o.valid),
        stream_o.ready.eq(stream_i.ready)
    ]

def channel_remap(m, stream_o, stream_i, mapping_o_to_i):
    def remap(o, i):
        connections = []
        for k in mapping_o_to_i:
            connections.append(i.payload[mapping_o_to_i[k]].eq(o.payload[k]))
        return connections
    return connect_remap(m, stream_o, stream_i, remap)

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

class GainVCA(wiring.Component):

    """
    Voltage Controlled Amplifier where the gain amount can be > 1.
    The output is clipped to fit in a normal ASQ.
    """

    i: In(stream.Signature(data.StructLayout({
            "x": ASQ,
            "gain": fixed.SQ(2, ASQ.f_width), # only 2 extra bits, so -3 to +3 is OK
        })))
    o: Out(stream.Signature(ASQ))

    def elaborate(self, platform):
        m = Module()

        result = Signal(fixed.SQ(3, ASQ.f_width))
        m.d.comb += result.eq(self.i.payload.x * self.i.payload.gain)

        sat_hi = fixed.Const(0, shape=ASQ)
        sat_hi._value = 2**ASQ.f_width - 1 # move to Const.max()?
        sat_lo = fixed.Const(-1, shape=ASQ)

        with m.If(sat_hi < result):
            m.d.comb += self.o.payload.eq(sat_hi),
        with m.Elif(result < sat_lo):
            m.d.comb += self.o.payload.eq(sat_lo),
        with m.Else():
            m.d.comb += self.o.payload.eq(result),

        m.d.comb += [
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
        ]

        return m

class SawNCO(wiring.Component):

    """
    Sawtooth Numerically Controlled Oscillator.
    """

    i: In(stream.Signature(data.StructLayout({
            "freq_inc": ASQ,
            "phase": ASQ,
        })))
    o: Out(stream.Signature(ASQ))

    def __init__(self, extra_bits=16, shift=6):
        self.extra_bits = extra_bits
        self.shift = shift
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        s = Signal(fixed.SQ(self.extra_bits, ASQ.f_width))

        out_no_phase_mod = Signal(ASQ)

        m.d.comb += [
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
            out_no_phase_mod.eq(s >> self.shift),
            self.o.payload.eq(
                out_no_phase_mod + self.i.payload.phase),
        ]

        with m.If(self.i.valid & self.o.ready):
            m.d.sync += s.eq(s + self.i.payload.freq_inc),

        return m

class Trigger(wiring.Component):

    """
    When trigger condition is met, output is set to 1, for 1 stream cycle.

    Currently this only implements rising edge trigger.
    """

    i: In(stream.Signature(data.StructLayout({
            "sample":    ASQ,
            "threshold": ASQ,
        })))
    o: Out(stream.Signature(unsigned(1)))

    def elaborate(self, platform):
        m = Module()

        trigger = Signal()
        l_sample = Signal(shape=ASQ)

        m.d.comb += [
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
        ]

        with m.If(self.i.valid & self.o.ready):
            m.d.sync += l_sample.eq(self.i.payload.sample)
            m.d.comb += [
                self.o.payload.eq(
                    (l_sample              < self.i.payload.threshold) &
                    (self.i.payload.sample >= self.i.payload.threshold)
                ),
            ]

        return m

class Ramp(wiring.Component):

    """
    If trigger strobes a 1, ramps from -1 to 1, staying at 1 until retriggered.
    A retrigger mid-ramp does not restart the ramp until the output has reached 1.
    """

    i: In(stream.Signature(data.StructLayout({
            "trigger":  unsigned(1),
            "td":       ASQ, # time delta
        })))
    o: Out(stream.Signature(ASQ))

    def __init__(self, extra_bits=16, shift=6):
        self.extra_bits = extra_bits
        self.shift = shift
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        s = Signal(fixed.SQ(self.extra_bits, ASQ.f_width))

        m.d.comb += [
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
            self.o.payload.eq(s >> self.shift),
        ]

        with m.If(self.i.valid & self.o.ready):
            with m.If((self.o.payload > fixed.Const(0.95, shape=ASQ)) &
                      (self.o.payload.as_value()[15] == 0)):
                with m.If(self.i.payload.trigger):
                    m.d.sync += s.eq(ASQ.min() << self.shift)
            with m.Else():
                m.d.sync += s.eq(s + self.i.payload.td)

        return m

class WaveShaper(wiring.Component):

    """
    Waveshaper that maps x to f(x), where the function must be
    stateless so we can precompute a mapping lookup table.

    Linear interpolation is used between lut elements.
    """

    i: In(stream.Signature(ASQ))
    o: Out(stream.Signature(ASQ))

    def __init__(self, lut_function=None, lut_size=512, continuous=False):
        self.lut_size = lut_size
        self.lut_addr_width = exact_log2(lut_size)
        self.continuous = continuous

        # build LUT such that we can index into it using 2s
        # complement and pluck out results with correct sign.
        self.lut = []
        for i in range(lut_size):
            x = None
            if i < lut_size//2:
                x = 2*i / lut_size
            else:
                x = 2*(i - lut_size) / lut_size
            fx = lut_function(x)
            self.lut.append(fixed.Const(fx, shape=ASQ)._value)

        super().__init__()

    def elaborate(self, platform):
        m = Module()

        # TODO (amaranth 0.5+): use native ASQ shape in LUT memory
        m.submodules.mem = mem = Memory(
            shape=signed(ASQ.as_shape().width), depth=self.lut_size, init=self.lut)
        rport = mem.read_port()

        ltype = fixed.SQ(self.lut_addr_width-1, ASQ.f_width-self.lut_addr_width+1)

        x = Signal(ltype)
        y = Signal(ASQ)

        trunc = Signal()

        with m.FSM() as fsm:
            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                    m.d.sync += x.eq(self.i.payload << ltype.i_width)
                    m.d.sync += y.eq(0)
                    m.next = 'READ0'
            with m.State('READ0'):
                m.d.comb += [
                    rport.en.eq(1),
                ]
                # is this a function where f(+1) ~= f(-1)
                if self.continuous:
                    m.d.comb += rport.addr.eq(x.truncate()+1)
                else:
                    with m.If((x.truncate()).raw() ==
                              2**(self.lut_addr_width-1)-1):
                        m.d.comb += trunc.eq(1)
                        m.d.comb += rport.addr.eq(x.truncate())
                    with m.Else():
                        m.d.comb += rport.addr.eq(x.truncate()+1)
                m.next = 'MAC0'
            with m.State('MAC0'):
                m.d.sync += y.eq(fixed.Value(ASQ, rport.data) *
                                 (x - x.truncate()))
                m.d.comb += [
                    rport.addr.eq(x.truncate()),
                    rport.en.eq(1),
                ]
                m.next = 'MAC1'
            with m.State('MAC1'):
                m.d.sync += y.eq(y + fixed.Value(ASQ, rport.data) *
                                 (x.truncate() - x + 1))
                m.next = 'WAIT-READY'

            with m.State('WAIT-READY'):
                m.d.comb += [
                    self.o.valid.eq(1),
                    self.o.payload.eq(y),
                ]
                with m.If(self.o.ready):
                    m.next = 'WAIT-VALID'

        return m

class SVF(wiring.Component):

    """
    Oversampled Chamberlin State Variable Filter.

    Filter `cutoff` and `resonance` are tunable at the system sample rate.

    Highpass, lowpass, bandpass routed out on stream payloads `hp`, `lp`, `bp`.

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

        # shared multiplier for z = a*b+c
        mac_a = Signal(dtype)
        mac_b = Signal(dtype)
        mac_c = Signal(dtype)
        mac_z = Signal(dtype)
        m.d.comb += mac_z.eq(mac_a*mac_b + mac_c)

        with m.FSM() as fsm:
            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                   m.d.sync += x.eq(self.i.payload.x),
                   m.d.sync += oversample.eq(0)
                   # FIXME: signedness (>=0)  check without working around `fixed`
                   with m.If(self.i.payload.cutoff.as_value()[15] == 0):
                       m.d.sync += kK.eq(self.i.payload.cutoff)
                   with m.If(self.i.payload.resonance.as_value()[15] == 0):
                       m.d.sync += kQinv.eq(self.i.payload.resonance)
                   m.next = 'MAC0'
            with m.State('MAC0'):
                m.d.comb += [
                    mac_a.eq(abp),
                    mac_b.eq(kK),
                    mac_c.eq(alp),
                ]
                m.d.sync += alp.eq(mac_z)
                m.next = 'MAC1'
            with m.State('MAC1'):
                m.d.comb += [
                    mac_a.eq(abp),
                    mac_b.eq(-kQinv),
                    mac_c.eq(x - alp),
                ]
                m.d.sync += ahp.eq(mac_z)
                m.next = 'MAC2'
            with m.State('MAC2'):
                m.d.comb += [
                    mac_a.eq(ahp),
                    mac_b.eq(kK),
                    mac_c.eq(abp),
                ]
                m.d.sync += abp.eq(mac_z)
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

class KickFeedback(Elaboratable):
    """
    Inject a single dummy (garbage) sample after reset between
    two streams. This is necessary to break infinite blocking
    after reset if streams are set up in a feedback loop.
    """
    def __init__(self, o, i):
        self.o = o
        self.i = i
    def elaborate(self, platform):
        m = Module()
        wiring.connect(m, self.o, self.i)
        with m.FSM() as fsm:
            with m.State('KICK'):
                m.d.comb += self.i.valid.eq(1)
                with m.If(self.i.ready):
                    m.next = 'FORWARD'
            with m.State('FORWARD'):
                pass
        return m

def connect_feedback_kick(m, o, i):
    m.submodules += KickFeedback(o, i)

class PitchShift(wiring.Component):

    """
    Granular pitch shifter. Works by crossfading 2 separately
    tracked taps on a delay line. As a result, maximum grain
    size is the delay line 'max_delay' // 2.

    The delay line tap itself must be hooked up to the input
    source from outside this component (this allows multiple
    shifters to share a single delay line).
    """

    def __init__(self, tap, xfade=256):
        assert xfade <= (tap.max_delay // 4)
        self.tap        = tap
        self.xfade      = xfade
        self.xfade_bits = exact_log2(xfade)
        # delay type: integer component is index into delay line
        # +1 is necessary so that we don't overflow on adding grain_sz.
        self.dtype = fixed.SQ(self.tap.addr_width+1, 8)
        super().__init__({
            "i": In(stream.Signature(data.StructLayout({
                    "pitch": self.dtype,
                    "grain_sz": unsigned(exact_log2(tap.max_delay)),
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
                    self.tap.o.ready.eq(1),
                    self.tap.i.valid.eq(1),
                    self.tap.i.payload.eq(delay0.round() >> delay0.f_width),
                ]
                with m.If(self.tap.o.valid):
                    m.d.comb += self.tap.i.valid.eq(0),
                    m.d.sync += sample0.eq(self.tap.o.payload)
                    m.next = 'TAP1'
            with m.State('TAP1'):
                m.d.comb += [
                    self.tap.o.ready.eq(1),
                    self.tap.i.valid.eq(1),
                    self.tap.i.payload.eq(delay1.round() >> delay1.f_width),
                ]
                with m.If(self.tap.o.valid):
                    m.d.comb += self.tap.i.valid.eq(0),
                    m.d.sync += sample1.eq(self.tap.o.payload)
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

class MatrixMix(wiring.Component):

    """
    Matrix mixer with tunable coefficients and configurable
    input & output channel count. Uses a single multiplier.

    Coefficients must fit inside the self.ctype declared below.
    Coefficients can be updated in real-time by writing them
    to the `c` stream (position `o_x`, `i_y`, value `v`).
    """

    def __init__(self, i_channels, o_channels, coefficients):

        assert(len(coefficients)       == i_channels)
        assert(len(coefficients[0])    == o_channels)

        self.i_channels = i_channels
        self.o_channels = o_channels

        self.ctype = fixed.SQ(2, ASQ.f_width)

        coefficients_flat = [
            fixed.Const(x, shape=self.ctype)._value
            for xs in coefficients
            for x in xs
        ]

        assert(len(coefficients_flat) == i_channels*o_channels)

        # matrix coefficient memory
        # TODO (amaranth 0.5+): use native shape in LUT memory
        self.mem = Memory(
            shape=signed(self.ctype.as_shape().width),
            depth=i_channels*o_channels, init=coefficients_flat)

        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(ASQ, i_channels))),
            "c": In(stream.Signature(data.StructLayout({
                "o_x": unsigned(exact_log2(self.o_channels)),
                "i_y": unsigned(exact_log2(self.i_channels)),
                "v":   self.ctype
                }))),
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, o_channels))),
        })

    def elaborate(self, platform):
        m = Module()

        m.submodules.mem = self.mem
        wport = self.mem.write_port()
        rport = self.mem.read_port(transparent_for=(wport,))

        i_latch = Signal(data.ArrayLayout(self.ctype, self.i_channels))
        o_accum = Signal(data.ArrayLayout(
            fixed.SQ(self.ctype.i_width*2, self.ctype.f_width),
            self.o_channels))

        i_ch   = Signal(exact_log2(self.i_channels))
        o_ch   = Signal(exact_log2(self.o_channels))
        # i/o channel index, one cycle behind.
        l_i_ch = Signal(exact_log2(self.i_channels))
        o_ch_l = Signal(exact_log2(self.o_channels))
        # we've finished all accumulation steps.
        done = Signal(1)

        m.d.comb += [
            rport.en.eq(1),
            rport.addr.eq(Cat(o_ch, i_ch)),
        ]

        # coefficient update logic

        with m.If(self.c.ready):
            m.d.comb += [
                wport.addr.eq(Cat(self.c.payload.o_x, self.c.payload.i_y)),
                wport.en.eq(self.c.valid),
                wport.data.eq(self.c.payload.v),
            ]

        # main multiplications state machine

        with m.FSM() as fsm:
            with m.State('WAIT-VALID'):
                m.d.comb += self.c.ready.eq(1), # permit coefficient updates
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                    m.d.sync += [
                        o_accum.eq(0),
                        i_ch.eq(0),
                        o_ch.eq(0),
                        done.eq(0),
                    ]
                    # FIXME: assigning each element of the payload is necessary
                    # because assignment of a data.ArrayLayout ignores the
                    # underlying fixed-point types. This should be cleaner!
                    m.d.sync += [
                        i_latch[n].eq(self.i.payload[n])
                        for n in range(self.i_channels)
                    ]
                    m.next = 'NEXT'
            with m.State('NEXT'):
                m.next = 'MAC'
                m.d.sync += [
                    o_ch_l.eq(o_ch),
                    l_i_ch.eq(i_ch),
                ]
                with m.If(o_ch == (self.o_channels - 1)):
                    m.d.sync += o_ch.eq(0)
                    with m.If(i_ch == (self.i_channels - 1)):
                        m.d.sync += done.eq(1)
                    with m.Else():
                        m.d.sync += i_ch.eq(i_ch+1)
                with m.Else():
                    m.d.sync += o_ch.eq(o_ch+1)
            with m.State('MAC'):
                m.next = 'NEXT'
                m.d.sync += [
                    o_accum[o_ch_l].eq(o_accum[o_ch_l] +
                                       (fixed.Value(self.ctype, rport.data) *
                                        i_latch[l_i_ch]))
                ]
                with m.If(done):
                    m.next = 'WAIT-READY'
            with m.State('WAIT-READY'):
                m.d.comb += self.c.ready.eq(1), # permit coefficient updates
                m.d.comb += [
                    self.o.valid.eq(1),
                ]
                m.d.comb += [
                    self.o.payload[n].eq(o_accum[n])
                    for n in range(self.o_channels)
                ]
                with m.If(self.o.ready):
                    m.next = 'WAIT-VALID'

        return m

class FIR(wiring.Component):

    """
    Fixed-point FIR filter that uses a single multiplier.

    Filter order must be of the form :py:`2**N`, to allow for efficient
    implementation of index wrapping (internal memory sizes power of 2).

    Members
    -------
    i : :py:`In(stream.Signature(ASQ))`
        Input stream for sending samples to the filter.
    o : :py:`In(stream.Signature(ASQ))`
        Output stream for getting samples to the filter. There is 1 output
        sample per input sample, presented :py:`filter_order+1` cycles after
        the input sample.
    """

    i: In(stream.Signature(ASQ))
    o: Out(stream.Signature(ASQ))

    def __init__(self,
                 fs:               int,
                 filter_cutoff_hz: int,
                 filter_order:     int,
                 filter_type:      str='lowpass',
                 prescale:         float=1):
        """
        fs : int
            Sample rate of the filter, used for calculating FIR coefficients.
        filter_cutoff_hz : int
            Cutoff frequency of the filter, used for calculating FIR coefficients.
        filter_order : int
            Size of the filter (number of coefficients), as FIRs are symmetric.
            Must be of the form :py:`filter_order == 2**N`. If this is not
            held, the filter order is changed to the next larger order of
            this form.
        filter_type : str
            Type of the filter passed to :py:`signal.firwin` - :py:`"lowpass"`,
            :py:`"highpass"` or so on.
        prescale : float
            All taps are scaled by :py:`prescale`. This is used in cases where
            you are upsampling and need to preserve energy. Be careful with this,
            it can overflow the tap coefficients (you'll get a warning).
        """
        order_needed = 2**ceil_log2(filter_order)
        if order_needed != filter_order:
            print(f"WARN: FIR: using next highest 2**N order (was: {filter_order}, now: {order_needed})")
            filter_order = order_needed
        taps = signal.firwin(numtaps=filter_order, cutoff=filter_cutoff_hz,
                             fs=fs, pass_zero=filter_type, window='hamming')
        assert exact_log2(len(taps))
        self.taps_float = taps
        self.prescale   = prescale
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        # Tap and accumulator sizes

        self.ctype = fixed.SQ(2, ASQ.f_width)

        n = len(self.taps_float)

        # Filter tap memory and read port

        # if t*prescale overflows, fixed.Const should provide a warning.
        m.submodules.taps_mem = taps_mem = Memory(
            shape=self.ctype, depth=n, init=[
                fixed.Const(t*self.prescale, shape=self.ctype)
                for t in self.taps_float
            ]
        )

        taps_rport = taps_mem.read_port()

        # Input sample memory, write and read port

        m.submodules.x_mem = x_mem = Memory(
            shape=self.ctype, depth=n, init=[]
        )

        x_wport = x_mem.write_port()
        x_rport = x_mem.read_port()

        # FIR filter logic

        ix    = Signal(range(n))
        w_pos = Signal(range(n))
        a  = Signal(self.ctype)
        b  = Signal(self.ctype)
        y  = Signal(self.ctype)

        m.d.comb += taps_rport.en.eq(1)
        m.d.comb += taps_rport.addr.eq(0)
        m.d.comb += x_wport.addr.eq(w_pos)
        m.d.comb += x_wport.data.eq(self.i.payload)
        m.d.comb += x_rport.addr.eq(w_pos)
        m.d.comb += x_rport.en.eq(1)
        with m.If(ix < (n-1)):
            m.d.comb += taps_rport.addr.eq(ix+1)
            m.d.comb += x_rport.addr.eq(w_pos+ix+1)

        with m.FSM() as fsm:
            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1),
                with m.If(self.i.valid):
                    m.d.comb += x_wport.en.eq(1)
                    m.d.sync += [
                        w_pos.eq(w_pos+1),
                        ix.eq(0),
                        a.eq(self.i.payload),
                        b.eq(taps_rport.data),
                        y.eq(0)
                    ]
                    m.next = "MAC"
            with m.State("MAC"):
                m.d.sync += y.eq(y + (a * b))
                with m.If(ix == (n-1)):
                    m.next = "WAIT-READY"
                with m.Else():
                    m.d.sync += [
                        a.eq(x_rport.data),
                        b.eq(taps_rport.data),
                        ix.eq(ix + 1)
                    ]
            with m.State('WAIT-READY'):
                m.d.comb += [
                    self.o.valid.eq(1),
                    self.o.payload.eq(y)
                ]
                with m.If(self.o.ready):
                    m.next = 'WAIT-VALID'

        return m

class Resample(wiring.Component):

    """
    Fractional N/M resampler.

    Upsamples by factor N, filters the result, then downsamples by factor M.
    The upsampling action zero-pads before applying the low-pass filter, so
    the low-pass filter coefficients are prescaled by N to preserve total energy.

    Takes some inspiration from `amlib/dsp/resampler.py` from
    `https://github.com/amaranth-farm/amlib`, however this implementation is
    mostly rewritten. The `amlib` resampler is copyright (c) 2021 Hans Baier
    <hansfbaier@gmail.com> and was licensed under CERN-OHL-W-2.0
    """

    i: In(stream.Signature(ASQ))
    o: Out(stream.Signature(ASQ))

    def __init__(self,
                 fs_in:  int,
                 n_up:   int,
                 m_down: int,
                 bw:     float=0.4):

        self.fs_in  = fs_in
        self.n_up   = n_up
        self.m_down = m_down
        self.bw     = bw

        super().__init__()

    def elaborate(self, platform):

        m = Module()

        m.submodules.filt = filt = FIR(
            fs=self.fs_in*self.n_up,
            filter_cutoff_hz=min(self.fs_in*self.bw,
                                 int((self.fs_in*self.bw)*(self.n_up/self.m_down))),
            filter_order=8*max(self.n_up, self.m_down), # order must be scaled by upsampling factor
            prescale=self.n_up)

        m.submodules.down_fifo = down_fifo = SyncFIFOBuffered(
            width=ASQ.as_shape().width, depth=self.n_up)

        upsampled_signal  = Signal(ASQ)
        upsample_counter  = Signal(range(self.n_up))

        m.d.comb += [
            self.i.ready.eq((upsample_counter == 0) & down_fifo.w_rdy & filt.i.ready),
            down_fifo.w_en.eq(down_fifo.w_rdy & filt.o.valid),
            filt.o.ready.eq(down_fifo.w_en),
        ]

        with m.If(filt.i.ready):
            with m.If(self.i.valid & self.i.ready):
                m.d.comb += [
                    filt.i.payload.eq(self.i.payload),
                    filt.i.valid.eq(1),
                ]
                m.d.sync += upsample_counter.eq(self.n_up - 1)
            with m.Elif(upsample_counter > 0):
                m.d.comb += [
                    filt.i.payload.eq(0),
                    filt.i.valid.eq(1),
                ]
                m.d.sync += upsample_counter.eq(upsample_counter - 1)

        downsample_counter = Signal(range(self.m_down+1))

        m.d.comb += [
            down_fifo.w_data.eq(filt.o.payload),
        ]

        with m.If(down_fifo.r_rdy):
            with m.If(downsample_counter == 0):
                m.d.comb += [
                    self.o.payload.eq(down_fifo.r_data),
                    self.o.valid.eq(1),
                ]
                # hold onto sample if counter == 0
                with m.If(self.o.ready):
                    m.d.comb += down_fifo.r_en.eq(1)
                    m.d.sync += downsample_counter.eq(self.m_down - 1)
            with m.Else():
                # drop samples if counter != 0
                m.d.comb += down_fifo.r_en.eq(1)
                m.d.sync += downsample_counter.eq(downsample_counter - 1)

        return m

class Boxcar(wiring.Component):

    """
    Simple Boxcar Average.

    Average of previous N samples, implemented with an accumulator.
    Requires no multiplies, often useful for simple smoothing.

    Can be used in low- or high-pass mode.
    """

    i: In(stream.Signature(ASQ))
    o: Out(stream.Signature(ASQ))

    def __init__(self, n: int=32, hpf=False):
        # pow2 constraint on N allows us to shift instead of divide
        assert(2**exact_log2(n) == n)
        self.n = n
        self.hpf = hpf
        super().__init__()

    def elaborate(self, platform):

        m = Module()

        # accumulator should be large enough to fit N samples
        accumulator = Signal(fixed.SQ(2 + exact_log2(self.n), ASQ.f_width))
        fifo_r_asq  = Signal(ASQ)
        fifo_r_en_l = Signal()

        # delay element
        fifo = m.submodules.fifo = fifo = SyncFIFOBuffered(
            width=ASQ.as_shape().width, depth=self.n)

        # route input -> fifo
        wiring.connect(m, wiring.flipped(self.i), fifo.w_stream)

        # accumulator maintenance
        m.d.sync += fifo_r_en_l.eq(fifo.r_en)
        m.d.comb += fifo_r_asq.raw().eq(fifo.r_data) # raw -> ASQ
        with m.If(self.i.valid & self.i.ready):
            with m.If(fifo_r_en_l):
                # sample in + out simultaneously (normal case)
                m.d.sync += accumulator.eq(accumulator + self.i.payload - fifo_r_asq)
            with m.Else():
                # sample in only
                m.d.sync += accumulator.eq(accumulator + self.i.payload)
        with m.Elif(fifo_r_en_l):
            # sample out only
            m.d.sync += accumulator.eq(accumulator - fifo_r_asq)

        # output route to output, accumulator division
        if self.hpf:
            # boxcar hpf
            m.d.comb += self.o.payload.eq(fifo_r_asq - (accumulator >> exact_log2(self.n))),
        else:
            # normal averaging lpf
            m.d.comb += self.o.payload.eq(accumulator >> exact_log2(self.n)),
        m.d.comb += [
            self.o.valid.eq(fifo.level == self.n), # VERIFY
            fifo.r_en.eq(self.o.valid & self.o.ready),
        ]

        return m

def named_submodules(m_submodules, elaboratables, override_name=None):
    """
    Normally, using constructs like:

    .. code-block:: python

        m.submodules += delaylines

    You get generated code with names like U$14 ... as Amaranth's
    namer doesn't give such modules a readable name.

    Instead, you can do:

    .. code-block:: python

        named_submodules(m.submodules, delaylines)

    And this helper will give each instance a name.

    TODO: is there an idiomatic way of doing this?
    """
    if override_name is None:
        [setattr(m_submodules, f"{type(e).__name__.lower()}{i}", e) for i, e in enumerate(elaboratables)]
    else:
        [setattr(m_submodules, f"{override_name}{i}", e) for i, e in enumerate(elaboratables)]

