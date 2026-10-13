# Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
``VoiceBlock`` - a polyphonic voice block - N oscillators, ADSRs, filters, etc and downmix.

This takes a different approach to the other streaming cores, whereby each core exchanges
``Block``s of samples, kind of a packet with one entry per voice delineated by a ``first``
flag. This keeps the interconnect logic required between blocks much smaller than would
otherwise be the case if every element was simply duplicated N times. This trades off
maximum throughput, but for polyphony like this, time multiplexing is necessary anyway.
For state variables, local BRAMs save and restore voice-specific state for each sample.

TODO: Come up with a principled approach of combining these cores with the existing
single-in, single-out ones? Perhaps we could instantiate the same type of operation as e.g.
a ``MultiSVF`` or just an ``SVF`` based on how the core is instantiated or connected.
Such automation quickly becomes something like an HLS problem, which I really don't want to
solve...

``VoiceBlock`` is constructed from the following components (all in this file):

    .. code-block:: text

        MultiWavetableOsc ──┐
                            ├── BlockMerge ──> MultiSVF (and DC block) ──> VoiceMixer
        MultiADSR ──────────┘
"""

from amaranth import *
from amaranth.lib import data, enum, stream, wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out

from amaranth.utils import exact_log2
from amaranth_future import fixed

from . import ASQ, mac
from .block import Block, BlockMerge


class MultiWavetableOsc(wiring.Component):

    """
    N wavetable oscillators with:
    - Per-voice pitch input
    - Global phase modulation (up to audio rate)
    - Linear interpolation between wavetable samples

    Free-running: sample rate is determined by backpressure on ``self.o``.
    """


    def __init__(self, n, wt_size=512, sq=ASQ, phase_f_bits=8):
        self.n = n
        self.wt_size = wt_size
        self.sq = sq
        self.phase_sq = fixed.UQ(exact_log2(wt_size) + 1, phase_f_bits)
        super().__init__({
            # Pitch input (per-voice)
            "voice_freq_incs": In(data.ArrayLayout(sq, n)),
            # Phase modulation (all voices) for CV FM up to audio rate
            "phase_mod":       In(sq),
            # Wavetable memory access (to expose to SoC)
            "wt_write_addr":   In(unsigned(exact_log2(wt_size))),
            "wt_write_data":   In(signed(sq.i_bits + sq.f_bits)),
            "wt_write_en":     In(1),
            # Samples out
            "o": Out(stream.Signature(Block(sq))),
        })

    def elaborate(self, platform):
        m = Module()
        N = self.n
        WT_SIZE = self.wt_size

        # Wavetable memory

        wt_sample_bits = self.sq.i_bits + self.sq.f_bits
        m.submodules.wt_mem = wt_mem = Memory(
            shape=signed(wt_sample_bits), depth=WT_SIZE, init=[0]*WT_SIZE)
        wt_rport = wt_mem.read_port()
        wt_wport = wt_mem.write_port()
        m.d.comb += [
            wt_rport.en.eq(1),
            wt_wport.addr.eq(self.wt_write_addr),
            wt_wport.data.eq(self.wt_write_data),
            wt_wport.en.eq(self.wt_write_en),
        ]

        # Per-voice phase memory

        m.submodules.state_mem = state_mem = Memory(
            shape=self.phase_sq, depth=N, init=[])
        st_rport = state_mem.read_port()
        st_wport = state_mem.write_port()
        m.d.comb += st_rport.en.eq(1)

        # Registers across all voices

        cur_phase = Signal(self.phase_sq)
        cur_freq_inc = Signal(self.sq)
        wt_sample = Signal(self.sq)
        read0 = Signal(self.sq)
        read1 = Signal(self.sq)
        voice_ix = Signal(range(N))

        # Phase-modulated 'final phase' for wavetable lookup.
        phase_mod = Signal(self.sq)
        m.d.comb += phase_mod.eq(self.phase_mod)
        mod_pos = cur_phase + (phase_mod << self.phase_sq.f_bits)

        with m.FSM():

            with m.State('LOAD'):
                m.d.comb += st_rport.addr.eq(voice_ix)
                m.next = 'LATCH-STATE'

            with m.State('LATCH-STATE'):
                m.d.sync += cur_phase.eq(st_rport.data)
                with m.Switch(voice_ix):
                    for n in range(N):
                        with m.Case(n):
                            # Latch freq_inc for current voice
                            m.d.sync += cur_freq_inc.eq(self.voice_freq_incs[n])
                m.next = 'PHASE-ADVANCE'

            with m.State('PHASE-ADVANCE'):
                new_pos = Signal(self.phase_sq)
                m.d.comb += new_pos.eq(
                    cur_phase + (cur_freq_inc << exact_log2(WT_SIZE)))
                with m.If(new_pos.truncate() >= WT_SIZE):
                    wt_size_phase = fixed.Const(WT_SIZE, self.phase_sq)
                    m.d.sync += cur_phase.eq(new_pos - wt_size_phase)
                with m.Else():
                    m.d.sync += cur_phase.eq(new_pos)
                m.next = 'WT-ADDR0'

            with m.State('WT-ADDR0'):
                m.d.comb += wt_rport.addr.eq(mod_pos.truncate() + 1)
                m.next = 'WT-READ0'

            with m.State('WT-READ0'):
                m.d.sync += read0.eq(wt_rport.data)
                m.d.comb += wt_rport.addr.eq(mod_pos.truncate())
                m.next = 'WT-READ1'

            with m.State('WT-READ1'):
                m.d.sync += read1.eq(wt_rport.data)
                m.next = 'INTERP'

            with m.State('INTERP'):
                frac = Signal(fixed.UQ(1, mod_pos.shape().f_bits))
                m.d.comb += frac.eq(mod_pos - mod_pos.truncate())
                m.d.sync += wt_sample.eq(read1 + (read0 - read1) * frac)
                m.next = 'STORE'

            with m.State('STORE'):
                m.d.comb += [
                    st_wport.addr.eq(voice_ix),
                    st_wport.data.eq(cur_phase),
                    st_wport.en.eq(1),
                ]
                m.next = 'EMIT'

            with m.State('EMIT'):
                m.d.comb += [
                    self.o.valid.eq(1),
                    self.o.payload.sample.eq(wt_sample),
                    self.o.payload.first.eq(voice_ix == 0),
                ]
                with m.If(self.o.ready):
                    with m.If(voice_ix == N - 1):
                        m.d.sync += voice_ix.eq(0)
                        m.next = 'LOAD'
                    with m.Else():
                        m.d.sync += voice_ix.eq(voice_ix + 1)
                        m.next = 'LOAD'

        return m


class MultiADSR(wiring.Component):
    """
    N ADSR envelope generators with:
    - Independent gate/velocity per voice (output is velocity * adsr product)
    - Global attack/decay/sustain/release control
    - Outgoing bitfield of which voices are still traversing through ADSR states
      (e.g. gate released, but voice is still releasing - used for culling)

    Free-running: sample rate is determined by backpressure on ``self.o``.
    """

    class Phase(enum.Enum, shape=unsigned(3)):
        IDLE    = 0
        ATTACK  = 1
        DECAY   = 2
        SUSTAIN = 3
        RELEASE = 4

    EnvUQ = fixed.UQ(0, 16)

    def __init__(self, n):
        self.n = n
        super().__init__({
            # Gate and velocity inputs (independent per-voice)
            "voice_gates":    In(data.ArrayLayout(unsigned(1), n)),
            "voice_velocity": In(data.ArrayLayout(self.EnvUQ, n)),
            # Global ADSR rates
            "attack_rate":    In(self.EnvUQ),
            "decay_rate":     In(self.EnvUQ),
            "sustain_level":  In(self.EnvUQ),
            "release_rate":   In(self.EnvUQ),
            # Samples out and bitfield of active voices
            "o": Out(stream.Signature(Block(self.EnvUQ))),
            "voice_active": Out(data.ArrayLayout(unsigned(1), n)),
        })

    def elaborate(self, platform):
        m = Module()
        N = self.n
        EnvUQ = self.EnvUQ

        # Per-voice ADSR state memory

        adsr_state_layout = data.StructLayout({
            "l_gate":     unsigned(1),
            "adsr_phase": self.Phase,
            "adsr_level": EnvUQ,
        })
        m.submodules.state_mem = state_mem = Memory(
            shape=adsr_state_layout, depth=N, init=[])
        st_rport = state_mem.read_port()
        st_wport = state_mem.write_port()
        m.d.comb += st_rport.en.eq(1)

        # Registers fetched / stored from above state mem

        l_gate      = Signal(1)
        adsr_phase  = Signal(self.Phase)
        adsr_level  = Signal(EnvUQ)
        cur_gate    = Signal(1)
        cur_vel_mod = Signal(EnvUQ)
        voice_ix    = Signal(range(N))

        with m.FSM():

            with m.State('LOAD'):
                m.d.comb += st_rport.addr.eq(voice_ix)
                m.next = 'LATCH-STATE'

            with m.State('LATCH-STATE'):
                m.d.sync += [
                    l_gate.eq(st_rport.data.l_gate),
                    adsr_phase.eq(st_rport.data.adsr_phase),
                    adsr_level.eq(st_rport.data.adsr_level),
                ]
                with m.Switch(voice_ix):
                    for n in range(N):
                        with m.Case(n):
                            # Latch gate, vel for current voice
                            m.d.sync += [
                                cur_gate.eq(self.voice_gates[n]),
                                cur_vel_mod.eq(self.voice_velocity[n]),
                            ]
                m.next = 'ADSR-UPDATE'

            with m.State('ADSR-UPDATE'):

                gate_rising  = Signal()
                gate_falling = Signal()
                m.d.comb += [
                    gate_rising.eq(cur_gate & ~l_gate),
                    gate_falling.eq(~cur_gate & l_gate),
                ]

                active_phase = Signal(self.Phase)
                m.d.comb += active_phase.eq(adsr_phase)
                with m.If(gate_rising):
                    m.d.comb += active_phase.eq(self.Phase.ATTACK)
                with m.Elif(gate_falling):
                    m.d.comb += active_phase.eq(self.Phase.RELEASE)

                next_level = Signal(EnvUQ)
                out_phase = Signal(self.Phase)
                m.d.comb += [
                    next_level.eq(adsr_level),
                    out_phase.eq(active_phase),
                ]

                with m.Switch(active_phase):
                    with m.Case(self.Phase.IDLE):
                        m.d.comb += next_level.eq(0)
                    with m.Case(self.Phase.ATTACK):
                        attack_sum = adsr_level + self.attack_rate
                        with m.If(attack_sum >= EnvUQ.max()):
                            m.d.comb += [
                                next_level.eq(EnvUQ.max()),
                                out_phase.eq(self.Phase.DECAY),
                            ]
                        with m.Else():
                            m.d.comb += next_level.eq(attack_sum)
                    with m.Case(self.Phase.DECAY):
                        with m.If(adsr_level <= self.sustain_level + self.decay_rate):
                            m.d.comb += [
                                next_level.eq(self.sustain_level),
                                out_phase.eq(self.Phase.SUSTAIN),
                            ]
                        with m.Else():
                            m.d.comb += next_level.eq(adsr_level - self.decay_rate)
                    with m.Case(self.Phase.SUSTAIN):
                        m.d.comb += next_level.eq(self.sustain_level)
                    with m.Case(self.Phase.RELEASE):
                        with m.If(adsr_level <= self.release_rate):
                            m.d.comb += [
                                next_level.eq(0),
                                out_phase.eq(self.Phase.IDLE),
                            ]
                        with m.Else():
                            m.d.comb += next_level.eq(adsr_level - self.release_rate)

                m.d.sync += [
                    adsr_level.eq(next_level),
                    adsr_phase.eq(out_phase),
                    l_gate.eq(cur_gate),
                ]
                m.next = 'STORE'

            with m.State('STORE'):
                m.d.comb += [
                    st_wport.addr.eq(voice_ix),
                    st_wport.data.l_gate.eq(l_gate),
                    st_wport.data.adsr_phase.eq(adsr_phase),
                    st_wport.data.adsr_level.eq(adsr_level),
                    st_wport.en.eq(1),
                ]
                with m.Switch(voice_ix):
                    for n in range(N):
                        with m.Case(n):
                            m.d.sync += self.voice_active[n].eq(
                                adsr_phase != self.Phase.IDLE)
                m.next = 'EMIT'

            with m.State('EMIT'):
                m.d.comb += [
                    self.o.valid.eq(1),
                    self.o.payload.first.eq(voice_ix == 0),
                    self.o.payload.sample.eq(adsr_level * cur_vel_mod),
                ]
                with m.If(self.o.ready):
                    with m.If(voice_ix == N - 1):
                        m.d.sync += voice_ix.eq(0)
                    with m.Else():
                        m.d.sync += voice_ix.eq(voice_ix + 1)
                    m.next = 'LOAD'

        return m


class MultiSVF(wiring.Component):

    """
    N oversampled Chamberlin state variable filters with:
    - Per-voice samples in and cutoff (envelope)
    - Global resonance (all filters)
    - LPF output has a DC-blocking stage so 0 cutoff doesn't emit DC

    This is just N copies of the one-in-one-out ``SVF`` and ``DCBlock`` from ``dsp.filters``.
    """

    N_OVERSAMPLE = 2

    def __init__(self, n, sq=ASQ, env_uq=MultiADSR.EnvUQ, macp=None):
        self.n = n
        self.sq = sq
        self.macp = macp or mac.MAC.default()
        super().__init__({
            "i":    In(stream.Signature(Block(data.StructLayout({"x": sq, "env": env_uq})))),
            "reso": In(unsigned(16)),
            "o":    Out(stream.Signature(Block(sq))),
        })

    def elaborate(self, platform):
        m = Module()
        N = self.n
        m.submodules.macp = mp = self.macp

        # Per-voice SVF state memory

        svf_sh = fixed.SQ(mac.SQNative.i_bits, mac.SQNative.f_bits + 2)
        state_layout = data.StructLayout({
            "abp":  svf_sh,
            "alp":  svf_sh,
            "ahp":  svf_sh,
            "dc_x": svf_sh,
            "dc_y": svf_sh,
        })
        m.submodules.state_mem = state_mem = Memory(
            shape=state_layout, depth=N, init=[])
        st_rport = state_mem.read_port()
        st_wport = state_mem.write_port()
        m.d.comb += st_rport.en.eq(1)

        # Working registers
        svf_abp = Signal(svf_sh)
        svf_alp = Signal(svf_sh)
        svf_ahp = Signal(svf_sh)

        svf_x     = Signal(mac.SQNative)
        svf_kK    = Signal(mac.SQNative)
        svf_kQinv = Signal(mac.SQNative)
        oversample = Signal(range(self.N_OVERSAMPLE))

        # DC block: multiply-free first-order highpass
        # y[n] = x[n] - x[n-1] + y[n-1] - (y[n-1] >> DC_SHIFT)
        DC_SHIFT = 9  # 1/512 ≈ 0.998, ~1.5Hz corner at 48kHz
        dc_x = Signal(svf_sh)
        dc_y = Signal(svf_sh)

        in_first = Signal(1)
        voice_ix = Signal(range(N))

        N_OCTAVES = 5
        env_scaled = Signal(unsigned(16))
        octave = env_scaled[-3:]
        mantissa = env_scaled[:-3]
        kK_min = (1 << (16 - 3)) >> N_OCTAVES
        kK_exp = Signal(unsigned(16))
        kK_shift = Signal(unsigned(3))
        m.d.comb += [
            env_scaled.eq((self.i.payload.sample.env.as_value() * N_OCTAVES) >> 3),
            kK_shift.eq(N_OCTAVES - octave),
            kK_exp.eq((Cat(mantissa, C(1, 1)) >> kK_shift) - kK_min),
        ]

        with m.FSM():

            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1)
                with m.If(self.i.valid):
                    m.d.sync += [
                        svf_x.eq(self.i.payload.sample.x >> 1),
                        svf_kK.as_value().eq(kK_exp),
                        svf_kQinv.as_value().eq(self.reso),
                        in_first.eq(self.i.payload.first),
                        oversample.eq(0),
                    ]
                    m.d.comb += st_rport.addr.eq(voice_ix)
                    m.next = 'LOAD'

            with m.State('LOAD'):
                m.d.sync += [
                    svf_abp.eq(st_rport.data.abp),
                    svf_alp.eq(st_rport.data.alp),
                    svf_ahp.eq(st_rport.data.ahp),
                    dc_x.eq(st_rport.data.dc_x),
                    dc_y.eq(st_rport.data.dc_y),
                ]
                m.next = 'SVF-MAC0'

            with m.State('SVF-MAC0'):
                # alp = abp*kK + alp
                with mp.Multiply(m, a=svf_abp, b=svf_kK):
                    m.d.sync += svf_alp.eq(mp.result.z + svf_alp)
                    m.next = 'SVF-MAC1'

            with m.State('SVF-MAC1'):
                # ahp = abp*(-kQinv) + (x - alp)
                with mp.Multiply(m, a=svf_abp, b=-svf_kQinv):
                    m.d.sync += svf_ahp.eq(mp.result.z + (svf_x - svf_alp))
                    m.next = 'SVF-MAC2'

            with m.State('SVF-MAC2'):
                # abp = ahp*kK + abp
                with mp.Multiply(m, a=svf_ahp, b=svf_kK):
                    m.d.sync += svf_abp.eq(mp.result.z + svf_abp)
                    m.next = 'SVF-OVER'

            with m.State('SVF-OVER'):
                with m.If(oversample == self.N_OVERSAMPLE - 1):
                    m.next = 'DC-BLOCK'
                with m.Else():
                    m.d.sync += oversample.eq(oversample + 1)
                    m.next = 'SVF-MAC0'

            with m.State('DC-BLOCK'):
                # Multiply-free DC blocking highpass on SVF lowpass output.
                svf_out = svf_alp >> 1
                m.d.sync += [
                    dc_y.eq(svf_out - dc_x + dc_y - (dc_y >> DC_SHIFT)),
                    dc_x.eq(svf_out),
                ]
                m.next = 'STORE'

            with m.State('STORE'):
                m.d.comb += [
                    st_wport.addr.eq(voice_ix),
                    st_wport.data.abp.eq(svf_abp),
                    st_wport.data.alp.eq(svf_alp),
                    st_wport.data.ahp.eq(svf_ahp),
                    st_wport.data.dc_x.eq(dc_x),
                    st_wport.data.dc_y.eq(dc_y),
                    st_wport.en.eq(1),
                ]
                m.next = 'EMIT'

            with m.State('EMIT'):
                m.d.comb += [
                    self.o.valid.eq(1),
                    self.o.payload.first.eq(in_first),
                    self.o.payload.sample.eq(dc_y),
                ]
                with m.If(self.o.ready):
                    with m.If(voice_ix == N - 1):
                        m.d.sync += voice_ix.eq(0)
                    with m.Else():
                        m.d.sync += voice_ix.eq(voice_ix + 1)
                    m.next = 'WAIT-VALID'

        return m


class VoiceMixer(wiring.Component):

    """
    Downmix N voices into a stereo output stream.
    - Even-indexed voices sum to left, odd-indexed to right
    - Gain-scaled by 2/N to prevent clipping
    """

    def __init__(self, n, sq=ASQ):
        self.n = n
        self.sq = sq
        super().__init__({
            "i": In(stream.Signature(Block(sq))),
            "o": Out(stream.Signature(data.ArrayLayout(sq, 2))),
        })

    def elaborate(self, platform):
        m = Module()
        N = self.n

        sq = self.sq
        mix_gain = fixed.Const(0.75 * 2.0 / N, shape=sq)

        acc_l = Signal(sq)
        acc_r = Signal(sq)
        voice_ix = Signal(range(N))

        with m.FSM():

            with m.State('CLEAR'):
                m.d.sync += [
                    acc_l.eq(0),
                    acc_r.eq(0),
                    voice_ix.eq(0),
                ]
                m.next = 'WAIT-VALID'

            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1)
                with m.If(self.i.valid):
                    scaled = self.i.payload.sample * mix_gain

                    # Even voices -> left, odd voices -> right
                    with m.If(voice_ix[0] == 0):
                        m.d.sync += acc_l.eq(acc_l + scaled)
                    with m.Else():
                        m.d.sync += acc_r.eq(acc_r + scaled)

                    with m.If(voice_ix == N - 1):
                        m.next = 'EMIT-OUTPUT'
                    with m.Else():
                        m.d.sync += voice_ix.eq(voice_ix + 1)

            with m.State('EMIT-OUTPUT'):
                m.d.comb += [
                    self.o.valid.eq(1),
                    self.o.payload[0].eq(acc_l),
                    self.o.payload[1].eq(acc_r),
                ]
                with m.If(self.o.ready):
                    m.next = 'CLEAR'

        return m


class VoiceBlock(wiring.Component):

    WAVETABLE_SIZE = 512

    def __init__(self, n_voices, sq=ASQ):
        self.n_voices = n_voices
        self.sq = sq
        super().__init__({
            # Stereo audio output stream
            "o": Out(stream.Signature(data.ArrayLayout(sq, 2))),

            # Voice control (from MidiVoiceTracker)
            "voice_gates":     In(data.ArrayLayout(unsigned(1), self.n_voices)),
            "voice_freq_incs": In(data.ArrayLayout(sq, self.n_voices)),
            "voice_velocity":  In(data.ArrayLayout(MultiADSR.EnvUQ, self.n_voices)),

            # Global synth parameters (from CSR)
            "reso":           In(unsigned(16)),
            "attack_rate":    In(MultiADSR.EnvUQ),
            "decay_rate":     In(MultiADSR.EnvUQ),
            "sustain_level":  In(MultiADSR.EnvUQ),
            "release_rate":   In(MultiADSR.EnvUQ),

            # Wavetable write port (firmware fills via CSR)
            "wt_write_addr":  In(unsigned(exact_log2(self.WAVETABLE_SIZE))),
            "wt_write_data":  In(signed(sq.i_bits + sq.f_bits)),
            "wt_write_en":    In(1),

            # Phase modulation input (applied to all oscillators)
            "phase_mod":      In(sq),

            # Per-voice ADSR idle status
            "voice_active":   Out(data.ArrayLayout(unsigned(1), self.n_voices)),

            # Per-voice envelope level (top 8 bits of ADSR output)
            "voice_cutoffs":     Out(data.ArrayLayout(unsigned(8), self.n_voices)),
        })

    def elaborate(self, platform):
        m = Module()

        N = self.n_voices

        sq = self.sq
        m.submodules.osc = osc = MultiWavetableOsc(n=N, wt_size=self.WAVETABLE_SIZE, sq=sq)
        m.submodules.adsr = adsr = MultiADSR(n=N)
        m.submodules.merge = merge = BlockMerge({"x": sq, "env": MultiADSR.EnvUQ})
        m.submodules.svf = svf = MultiSVF(n=N, sq=sq)
        m.submodules.mixer = mixer = VoiceMixer(n=N, sq=sq)

        # Wire external ports -> internal components
        for n in range(N):
            m.d.comb += [
                osc.voice_freq_incs[n].eq(self.voice_freq_incs[n]),
                adsr.voice_gates[n].eq(self.voice_gates[n]),
                adsr.voice_velocity[n].eq(self.voice_velocity[n]),
                self.voice_active[n].eq(adsr.voice_active[n]),
            ]
        m.d.comb += [
            osc.wt_write_addr.eq(self.wt_write_addr),
            osc.wt_write_data.eq(self.wt_write_data),
            osc.wt_write_en.eq(self.wt_write_en),
            osc.phase_mod.eq(self.phase_mod),
            adsr.attack_rate.eq(self.attack_rate),
            adsr.decay_rate.eq(self.decay_rate),
            adsr.sustain_level.eq(self.sustain_level),
            adsr.release_rate.eq(self.release_rate),
            svf.reso.eq(self.reso),
        ]

        # Stream pipeline
        wiring.connect(m, osc.o, merge.x)
        wiring.connect(m, adsr.o, merge.env)
        wiring.connect(m, merge.o, svf.i)
        wiring.connect(m, svf.o, mixer.i)
        wiring.connect(m, mixer.o, wiring.flipped(self.o))

        # Tap ADSR output stream to capture per-voice envelope values.
        # Voices always arrive in order 0..N-1, so a simple counter tracks them.
        env_voice_ix = Signal(range(N))
        with m.If(adsr.o.valid & adsr.o.ready):
            with m.Switch(env_voice_ix):
                for n in range(N):
                    with m.Case(n):
                        m.d.sync += self.voice_cutoffs[n].eq(
                            adsr.o.payload.sample.as_value() >> 8)
            with m.If(env_voice_ix == N - 1):
                m.d.sync += env_voice_ix.eq(0)
            with m.Else():
                m.d.sync += env_voice_ix.eq(env_voice_ix + 1)

        return m
