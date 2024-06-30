# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Tiny bitstream only used to reconfigure from a desired address."""

import os
import sys
import subprocess

from amaranth              import *
from amaranth.build        import *
from tiliqua.tiliqua_platform import TiliquaPlatform

class BootStubTop(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        m.d.comb += platform.request("self_program").eq(1)
        return m

def build():
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    os.environ["AMARANTH_ecppack_opts"] = "--compress --bootaddr 0x0"
    top = BootStubTop()
    TiliquaPlatform().build(top)
