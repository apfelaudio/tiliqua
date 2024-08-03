# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import logging
import os

from amaranth                            import *
from tiliqua.tiliqua_soc                 import TiliquaSoc
from tiliqua.tiliqua_platform            import set_environment_variables
from luna_soc                            import top_level_cli

if __name__ == "__main__":
    dvi_timings = set_environment_variables()
    this_directory = os.path.dirname(os.path.realpath(__file__))
    design = TiliquaSoc(firmware_path=os.path.join(this_directory, "fw/firmware.bin"),
                        dvi_timings=dvi_timings)
    design.genrust_constants()
    top_level_cli(design)
