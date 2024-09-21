# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import os

from tiliqua.tiliqua_soc                 import TiliquaSoc
from tiliqua.cli                         import top_level_cli

if __name__ == "__main__":
    this_path = os.path.dirname(os.path.realpath(__file__))
    top_level_cli(TiliquaSoc, path=this_path)
