# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import logging

from amaranth                            import *
from tiliqua.video                       import DVI_TIMINGS
from tiliqua.tiliqua_soc                 import TiliquaSoc
from tiliqua.tiliqua_platform            import set_tiliqua_default_amaranth_overrides

if __name__ == "__main__":
    from luna_soc import top_level_cli
    set_tiliqua_default_amaranth_overrides()
    design = TiliquaSoc(firmware_path="src/selftest/fw/firmware.bin")
    design.genrust_constants()
    top_level_cli(design)
