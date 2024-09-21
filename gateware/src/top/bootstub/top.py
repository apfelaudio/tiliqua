# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""
Tiny bitstream only used to reconfigure from a desired address.

These can be compressed to a small size and programmed over JTAG using the
Tiliqua's on-board RP2040 to the FPGA SRAM, to allow jumping to arbitrary
bitstreams in the SPI flash WITHOUT exhausting write cycles on the flash memory.
"""

import os
import shutil
import sys
import subprocess

from amaranth              import *
from amaranth.build        import *
from tiliqua.cli           import top_level_cli

class BootStubTop(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        m.d.comb += platform.request("self_program").o.eq(1)
        return m

if __name__ == "__main__":
    top_level_cli(BootStubTop, video_core=False)
