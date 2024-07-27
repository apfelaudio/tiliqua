# Logic for dealing with die temperature readout.
#
# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause

from amaranth                    import *
from amaranth.lib                import wiring, data
from amaranth.lib.wiring         import In, Out
from amaranth.lib.cdc            import FFSynchronizer
from luna_soc.gateware.csr.base  import Peripheral

class DieTemperaturePeripheral(Peripheral, Elaboratable):

    def __init__(self, **kwargs):

        super().__init__()

        # CSRs
        bank                   = self.csr_bank()
        self._temperature      = bank.csr(8, "r")

        # Peripheral bus
        self._bridge    = self.bridge(data_width=32, granularity=8, alignment=2)
        self.bus        = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge  = self._bridge

        read_cnt    = Signal(32)

        start_pulse = Signal()
        dtr_valid   = Signal()
        dtr_code    = Signal(6)

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

        m.d.comb += self._temperature.r_data.eq(valid_code)

        return m
