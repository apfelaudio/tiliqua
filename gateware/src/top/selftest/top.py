# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
"""
Collect some information about Tiliqua health, display it on the video
output and log it over serial. This is mostly used to check for
hardware issues and for bringup.
"""

import os

from tiliqua.tiliqua_soc         import TiliquaSoc
from tiliqua.cli                 import top_level_cli

if __name__ == "__main__":
    this_path = os.path.dirname(os.path.realpath(__file__))
    top_level_cli(TiliquaSoc, path=this_path,
                  argparse_fragment=lambda _: {"mainram_size": 0x10000})
