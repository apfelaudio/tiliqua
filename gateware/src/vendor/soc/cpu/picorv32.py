from amaranth             import *
from amaranth.lib.wiring  import Component, In, Out

from amaranth_soc         import wishbone
from amaranth_soc.periph  import ConstantMap

import os
import logging

class Picorv32(Component):
    def __init__(self):
        super().__init__({
            "ext_reset":     In(unsigned(1)),
            "bus": Out(wishbone.Signature(
                addr_width=30,
                data_width=32,
                granularity=8,
            )),
        })
        self._source_file = f"picorv32.v"
        self._source_path = os.path.join(os.path.dirname(__file__),
                                         "verilog", self._source_file)
        if not os.path.exists(self._source_path):
            FileNotFoundError(f"Verilog source file not found: {self._source_path}")
        with open(self._source_path, "r") as f:
            logging.info(f"reading verilog file: {self._source_path}")
            self._source_verilog = f.read()

    def elaborate(self, platform):
        m = Module()

        platform.add_file(self._source_file, self._source_verilog)
        wbm_adr = Signal(32)
        self._cpu = Instance(
            "picorv32_wb",
            # clock and reset
            i_wb_rst_i = ResetSignal("sync") | self.ext_reset,
            i_wb_clk_i = ClockSignal("sync"),
            # master wishbone bus
            o_wbm_adr_o  = wbm_adr,
            o_wbm_dat_o  = self.bus.dat_w,
            o_wbm_sel_o  = self.bus.sel,
            o_wbm_cyc_o  = self.bus.cyc,
            o_wbm_stb_o  = self.bus.stb,
            o_wbm_we_o   = self.bus.we,
            i_wbm_dat_i  = self.bus.dat_r,
            i_wbm_ack_i  = self.bus.ack,
        )

        m.d.comb += [
            self.bus.adr.eq(wbm_adr>>2)
        ]

        m.submodules.picorv32 = self._cpu

        return m
