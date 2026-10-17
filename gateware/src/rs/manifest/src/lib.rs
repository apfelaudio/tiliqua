// Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
//
// SPDX-License-Identifier: CERN-OHL-S-2.0

#![cfg_attr(not(test), no_std)]

// WARNING: Make sure this schema matches `lib.py`!
//
// Rust representation of 'Bitstream Manifests', describing each bitstream
// flashed to the Tiliqua, alongside any memory regions and settings required
// for it to start up correctly (these are set up by the bootloader).
//
// This representation is used manifest parsing in the bootloader.

use heapless::{String, Vec};
use serde::{Deserialize, Serialize};
use log::info;

pub const FLASH_PAGE_SZ: u32         = 0x1000;
pub const FLASH_SECTOR_SZ: u32       = 0x10000;
pub const MANIFEST_MAGIC: u32        = 0xFEEDBEEF;
pub const N_MANIFESTS: usize         = 8;
pub const SLOT_BITSTREAM_BASE: usize = 0x100000; // First user slot starts here
pub const SLOT_SIZE: usize           = 0x100000; // Spacing between user slots
pub const MANIFEST_OFFSET: usize     = 0xF0000;
pub const MANIFEST_SIZE: usize       = 0x1000;
pub const BITSTREAM_NAME_LEN: usize  = 32;
pub const BITSTREAM_TAG_LEN: usize   = 8;
pub const REGION_MAX_N: usize        = 5;
pub const REGION_FILE_LEN: usize     = 16;
pub const HELP_BRIEF_MAX_SIZE: usize = 64;
pub const HELP_IO_MAX_SIZE: usize    = 20;
pub const HELP_IO_LEFT_N: usize      = 8;
pub const HELP_IO_RIGHT_N: usize     = 6;

#[derive(Deserialize, Serialize, Clone, Debug, PartialEq, Eq)]
pub enum RegionType {
    /// Bitstream region that gets loaded directly by the bootloader
    Bitstream,
    /// `XiP` firmware that executes directly from SPI flash
    XipFirmware,
    /// Region that gets copied from SPI flash to RAM before use (firmware.bin to PSRAM)
    RamLoad,
    /// Option storage region for persistent application settings
    OptionStorage,
    /// Manifest region containing metadata about the bitstream
    Manifest,
    /// Copied from a file on an attached USB drive to PSRAM
    UsbLoad,
}

#[derive(Deserialize, Serialize, Clone)]
pub struct MemoryRegion {
    pub filename: String<REGION_FILE_LEN>,
    pub region_type: RegionType,
    pub spiflash_src: Option<u32>,
    pub psram_dst: Option<u32>,
    pub size: u32,
    pub crc: Option<u32>,
}

#[derive(Deserialize, Serialize, Clone)]
pub struct ExternalPLLConfig {
    pub clk0_hz: u32,
    pub clk1_hz: Option<u32>,
    pub clk1_inherit: bool,
    pub spread_spectrum: Option<f32>,
}

#[derive(Deserialize, Serialize, Clone)]
pub struct BitstreamHelp {
    pub brief: String<HELP_BRIEF_MAX_SIZE>,
    pub video: String<64>,
    pub io_left: [String<HELP_IO_MAX_SIZE>; HELP_IO_LEFT_N],
    pub io_right: [String<HELP_IO_MAX_SIZE>; HELP_IO_RIGHT_N],
}

#[derive(Deserialize, Serialize, Clone)]
pub struct BitstreamManifest {
    pub hw_rev: u32,
    pub name: String<BITSTREAM_NAME_LEN>,
    pub tag: String<BITSTREAM_TAG_LEN>,
    pub regions: Vec<MemoryRegion, REGION_MAX_N>,
    pub help: Option<BitstreamHelp>,
    pub external_pll_config: Option<ExternalPLLConfig>,
    pub magic: u32,
}

impl BitstreamManifest {

    pub fn print(&self) {
        info!("BitstreamManifest {{");
        info!("\tmagic:    {:#x}", self.magic);
        info!("\thw_rev:   {}",    self.hw_rev);
        info!("\tname:    '{}'",   self.name);
        info!("\ttag:     '{}'",   self.tag);
        if let Some(help) = &self.help {
            info!("\thelp = {{");
            info!("\t\tbrief:   '{}'", help.brief);
            info!("\t\tvideo:   '{}'", help.video);
            info!("\t}}");
        }
        if let Some(clocks) = &self.external_pll_config {
            info!("\texternal_pll_config = {{");
            info!("\t\tclk0_hz: {}", clocks.clk0_hz);
            info!("\t\tclk1_hz: {:?}", clocks.clk1_hz);
            info!("\t\tclk1_inherit: {:?}", clocks.clk1_inherit);
            info!("\t\tspread_spectrum: {:?}", clocks.spread_spectrum);
            info!("\t}}");
        }
        for (i, region) in self.regions.iter().enumerate() {
            info!("\tmemory_region[{i}] = {{");
            info!("\t\tfilename:     '{}'", region.filename);
            if let Some(spiflash_src) = region.spiflash_src {
                info!("\t\tspiflash_src: {spiflash_src:#x}");
            } else {
                info!("\t\tspiflash_src: None");
            }
            if let Some(psram_dst) = region.psram_dst {
                info!("\t\tpsram_dst:    {psram_dst:#x} (copyto)");
            }
            info!("\t\tsize:         {:#x}", region.size);
            if let Some(crc) = region.crc {
                info!("\t\tcrc:          {crc:#x}");
            }
            info!("\t}}");
        }
        info!("}}");
    }

    pub fn from_slice(manifest_slice: &[u8]) -> Option<Self> {
        let manifest_de = serde_json_core::from_slice::<Self>(manifest_slice);
        match manifest_de {
            Ok((contents, _rest)) => {
                info!("BitstreamManifest: parse OK");
                Some(contents)
            }
            Err(err) => {
                info!("BitstreamManifest: parse error {err:?}");
                None
            }
        }
    }

    pub fn from_addr(addr: usize, size: usize) -> Option<Self> {

        let manifest_slice = unsafe {
            core::slice::from_raw_parts(
                addr as *mut u8,
                size,
            )
        };

        // Erasing flash should always set bytes to 0xff. Count back from the
        // end of the manifest region to find where there is data. Otherwise,
        // Serde will fail out with a TrailingCharacters error.
        let mut last_byte = size;
        for i in (0..size).rev() {
            if manifest_slice[i] != 0xff {
                last_byte = i+1;
                break;
            }
        }

        if last_byte == size {
            info!("Manifest region is all ones, ignoring.");
            return None
        }

        let manifest_slice = &manifest_slice[0..last_byte];
        info!("Manifest length: {last_byte}");

        Self::from_slice(manifest_slice)
    }

    pub fn get_option_storage_window(&self) -> Option<core::ops::Range<u32>> {
        for region in &self.regions {
            if region.region_type == RegionType::OptionStorage {
                if let Some(spiflash_src) = region.spiflash_src {
                    return Some(spiflash_src..(spiflash_src+region.size));
                }
            }
        }
        None
    }
}
