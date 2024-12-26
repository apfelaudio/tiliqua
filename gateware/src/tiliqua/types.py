# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import enum

from typing import List, Optional

from dataclasses import dataclass
from dataclasses_json import dataclass_json

BITSTREAM_MANIFEST_VERSION = 0

class FirmwareLocation(str, enum.Enum):
    BRAM      = "bram"
    SPIFlash  = "spiflash"
    PSRAM     = "psram"

@dataclass_json
@dataclass
class MemoryRegion:
    filename: str
    spiflash_src: int
    psram_dst: Optional[int]
    size: int
    crc: int

@dataclass_json
@dataclass
class BitstreamManifest:
    name: str
    version: int
    sha: str
    brief: str
    video: str
    regions: List[MemoryRegion]
