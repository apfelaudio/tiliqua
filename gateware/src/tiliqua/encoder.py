# Logic for dealing with rotary encoders.
#
# Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD-3-Clause

from amaranth                    import *
from amaranth.lib                import wiring, data
from amaranth.lib.wiring         import Component, In, Out, flipped, connect
from amaranth.lib.cdc            import FFSynchronizer
from amaranth_soc                import csr

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

class PinSignature(wiring.Signature):
    def __init__(self):
        super().__init__({
            "i":  In(unsigned(1)),
            "q":  In(unsigned(1)),
            "s":  In(unsigned(1)),
        })

class Provider(Component):
    def __init__(self):
        super().__init__({
            "pins": In(PinSignature())
        })

    def elaborate(self, platform):
        m = Module()
        enc = platform.request("encoder")
        m.d.comb += [
            self.pins.i.eq(enc.i.i),
            self.pins.q.eq(enc.q.i),
            self.pins.s.eq(enc.s.i),
        ]
        return m

class Peripheral(wiring.Component):

    class StepReg(csr.Register, access="r"):
        step: csr.Field(csr.action.R, signed(8))

    class ButtonReg(csr.Register, access="r"):
        button: csr.Field(csr.action.R, unsigned(1))

    def __init__(self, **kwargs):
        self.iq_decode = IQDecode()

        regs = csr.Builder(addr_width=5, data_width=8)

        self._step = regs.add("step", self.StepReg())
        self._button = regs.add("button", self.ButtonReg())

        self._bridge = csr.Bridge(regs.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
            "pins": Out(PinSignature()),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        m.submodules.iq_decode = self.iq_decode

        connect(m, flipped(self.bus), self._bridge.bus)

        read_occurred = Signal()
        d_steps = Signal(signed(8))

        m.d.comb += self.iq_decode.iq.eq(Cat(self.pins.i, self.pins.q))
        m.d.comb += self._step.f.step.r_data.eq(d_steps)

        button_sync = Signal()
        m.submodules += FFSynchronizer(self.pins.s, button_sync, reset=0)
        m.d.comb += self._button.f.button.r_data.eq(button_sync)

        with m.If(self._step.f.step.r_stb):
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
