# Logic for dealing with rotary encoders.
#
# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

from amaranth                    import *
from amaranth.lib                import wiring, data
from amaranth.lib.wiring         import In, Out
from amaranth.lib.cdc            import FFSynchronizer
from luna_soc.gateware.csr.base  import Peripheral

class IQDecode(wiring.Component):

    iq: In(unsigned(2))
    step: Out(unsigned(1))
    direction: Out(unsigned(1))

    def __init__(self):
        super().__init__()
        self.iq_history = Array(Signal(2) for _ in range(2))

    def elaborate(self, _platform):
        m = Module()
        iq_sync = Signal(unsigned(2))
        m.submodules += FFSynchronizer(self.iq, iq_sync, reset=0)
        m.d.sync += self.iq_history[1].eq(self.iq_history[0]),
        m.d.sync += self.iq_history[0].eq(iq_sync),
        m.d.comb += self.step.eq(Cat(self.iq_history).xor()),
        m.d.comb += self.direction.eq(Cat(self.iq_history[1][0],
                                          self.iq_history[0][1]).xor()),
        return m

class EncoderPeripheral(Peripheral, Elaboratable):

    def __init__(self, *, pins, **kwargs):

        super().__init__()

        # Encoder logic
        self.pins              = pins
        self.iq_decode         = IQDecode()

        # CSRs
        bank                   = self.csr_bank()
        self._step             = bank.csr(8, "r")
        self._button           = bank.csr(1, "r")

        self.button_sync       = Signal()

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge
        m.submodules.iq_decode = self.iq_decode

        read_occurred = Signal()
        d_steps       = Signal(signed(8))

        m.d.comb += self.iq_decode.iq.eq(Cat(self.pins.i.i, self.pins.q.i))
        m.d.comb += self._step.r_data.eq(d_steps)

        m.submodules += FFSynchronizer(self.pins.s.i, self.button_sync, reset=0)
        m.d.comb += self._button.r_data.eq(self.button_sync)

        with m.If(self._step.r_stb):
            m.d.sync += read_occurred.eq(1)

        with m.If(self.iq_decode.step):
            with m.If(self.iq_decode.direction):
                m.d.sync += d_steps.eq(d_steps + 1)
            with m.Else():
                m.d.sync += d_steps.eq(d_steps - 1)
        with m.Else():
            with m.If(read_occurred):
                m.d.sync += read_occurred.eq(0)
                m.d.sync += d_steps.eq(0)

        return m
