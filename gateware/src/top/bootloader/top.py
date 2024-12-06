# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import os

from tiliqua.tiliqua_soc                 import TiliquaSoc
from tiliqua.cli                         import top_level_cli

if __name__ == "__main__":
    this_path = os.path.dirname(os.path.realpath(__file__))
    # FIXME: more RAM needed for this bitstream because `serde` has quite huge code size.
    top_level_cli(TiliquaSoc, path=this_path,
                  argparse_fragment=lambda _: {
                      "mainram_size": 0x10000,
                      "spiflash_fw_offset": None
                  })
