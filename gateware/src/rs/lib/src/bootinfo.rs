use tiliqua_hal::dma_framebuffer::DVIModeline;
use tiliqua_manifest::BitstreamManifest;
use serde_derive::{Serialize, Deserialize};
use crc::{Crc, CRC_32_BZIP2};

const BOOTINFO_MAX_SIZE: usize = 1024;
const CRC_ALGORITHM: Crc<u32> = Crc::<u32>::new(&CRC_32_BZIP2);

/// Information shared from Tiliqua bootloader to SoC bitstreams.
/// This is placed in PSRAM at a known address.
#[derive(Clone, Serialize, Deserialize)]
#[expect(clippy::unsafe_derive_deserialize)]
pub struct BootInfo {
    pub manifest: BitstreamManifest,
    pub modeline: DVIModeline,
}

impl BootInfo {
    /// Serialize `BootInfo` to memory at the given address in PSRAM.
    /// This is intended to be only used by the bootloader bitstream.
    ///
    /// # Safety
    ///
    /// `addr` must point to `BOOTINFO_MAX_SIZE` writable bytes.
    pub unsafe fn to_addr(&self, addr: usize) -> Option<usize> {
        let digest = CRC_ALGORITHM.digest();
        let buffer = core::slice::from_raw_parts_mut(addr as *mut u8, BOOTINFO_MAX_SIZE);
        postcard::to_slice_crc32(self, buffer, digest).ok().map(|slice| slice.len())
    }

    /// Deserialize `BootInfo` from memory at the given address in PSRAM.
    /// This is intended to only be used by application bitstreams.
    ///
    /// # Safety
    ///
    /// `addr` must point to `BOOTINFO_MAX_SIZE` readable bytes.
    pub unsafe fn from_addr(addr: usize) -> Option<Self> {
        let digest = CRC_ALGORITHM.digest();
        let buffer = core::slice::from_raw_parts(addr as *const u8, BOOTINFO_MAX_SIZE);
        postcard::from_bytes_crc32(buffer, digest).ok()
    }
}
