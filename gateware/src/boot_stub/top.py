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
from tiliqua.tiliqua_platform import TiliquaPlatform

class BootStubTop(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        m.d.comb += platform.request("self_program").eq(1)
        return m

def build():
    if len(sys.argv) < 3:
        print("must supply: <address> <suffix> - for example:")
        print("$ pdm build_boot_stub 0x100000 1 # produces 'build/bootstub1.bit'")
        sys.exit(-1)
    address = sys.argv[1]
    suffix  = sys.argv[2]
    os.environ["AMARANTH_verbose"] = "1"
    os.environ["AMARANTH_debug_verilog"] = "1"
    os.environ["AMARANTH_ecppack_opts"] = f"--compress --bootaddr {address}"
    top = BootStubTop()
    TiliquaPlatform().build(top)

    # copy the bitstream somewhere so it doesn't get overridden on the next bootstub build
    src = "build/top.bit"
    dst = f"build/bootstub{suffix}.bit"
    print(f"copying {src} to {dst}")
    shutil.copy(src, dst)
