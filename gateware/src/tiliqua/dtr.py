# Logic for dealing with die temperature readout.
#
# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

from amaranth                    import *
from amaranth.lib                import wiring, data
from amaranth.lib.wiring         import In, Out, flipped, connect
from amaranth.lib.cdc            import FFSynchronizer
from amaranth_soc                import csr

class Peripheral(wiring.Component):

    class TemperatureReg(csr.Register, access="r"):
        temperature: csr.Field(csr.action.R, unsigned(8))

    def __init__(self, **kwargs):
        regs = csr.Builder(addr_width=5, data_width=8)

        self._temperature = regs.add("temperature", self.TemperatureReg())

        self._bridge = csr.Bridge(regs.as_memory_map())

        super().__init__({
            "bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge

        connect(m, flipped(self.bus), self._bridge.bus)

        read_cnt = Signal(32)
        start_pulse = Signal()
        dtr_valid = Signal()
        dtr_code = Signal(6)

        m.submodules.dtr = Instance("DTR",
            i_STARTPULSE = start_pulse,
            o_DTROUT7    = dtr_valid,
            o_DTROUT5    = dtr_code[5],
            o_DTROUT4    = dtr_code[4],
            o_DTROUT3    = dtr_code[3],
            o_DTROUT2    = dtr_code[2],
            o_DTROUT1    = dtr_code[1],
            o_DTROUT0    = dtr_code[0],
        )

        m.d.sync += read_cnt.eq(read_cnt + 1)
        with m.If(read_cnt == 60000000):
            m.d.sync += read_cnt.eq(0)

        m.d.comb += start_pulse.eq(0)
        with m.If(read_cnt < 10000):
            m.d.comb += start_pulse.eq(1)

        valid_code = Signal(6)
        with m.If(dtr_valid):
            m.d.sync += valid_code.eq(dtr_code)

        m.d.comb += self._temperature.f.temperature.r_data.eq(valid_code)

        return m
