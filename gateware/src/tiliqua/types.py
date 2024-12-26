# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import enum

from typing import Optional

from dataclasses import dataclass
from dataclasses_json import dataclass_json

class FirmwareLocation(str, enum.Enum):
    BRAM      = "bram"
    SPIFlash  = "spiflash"
    PSRAM     = "psram"

@dataclass_json
@dataclass
class FirmwareImage:
    spiflash_src: int
    psram_dst: Optional[int]
    size: int

@dataclass_json
@dataclass
class BitstreamManifest:
    name: str
    brief: str
    video: str
    fw_img: Optional[FirmwareImage]
