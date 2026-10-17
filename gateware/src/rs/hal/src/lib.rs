#![cfg_attr(not(test), no_std)]
#![allow(clippy::inline_always)]
#![allow(clippy::must_use_candidate)]

#[cfg(test)]
#[macro_use]
extern crate std;

// modules
pub mod delay_line;
pub mod dma_framebuffer;
pub mod grain_player;
pub mod encoder;
pub mod i2c;
pub mod pca9635;
pub mod pmod;
pub mod polysynth;
pub mod serial;
pub mod si5351;
pub mod timer;
pub mod tusb322;
pub mod persist;
pub mod cy8cmbr3xxx;
pub mod spiflash;
pub mod eeprom;
pub mod scope;
pub mod vector;

pub use embedded_hal as hal;
pub use embedded_hal_nb as hal_nb;
pub use embedded_storage::nor_flash;
pub use embedded_graphics;

#[macro_use]
extern crate bitflags;

pub use nb;

// Peripherals common to all ordinary tiliqua_soc instances.
#[macro_export]
macro_rules! impl_tiliqua_soc_pac {
    () => {
        tiliqua_hal::impl_serial! {
            Serial0: tiliqua_pac::UART0,
        }

        tiliqua_hal::impl_timer! {
            Timer0: tiliqua_pac::TIMER0,
        }

        tiliqua_hal::impl_i2c! {
            I2c0: tiliqua_pac::I2C0,
        }

        tiliqua_hal::impl_i2c! {
            I2c1: tiliqua_pac::I2C1,
        }

        tiliqua_hal::impl_encoder! {
            Encoder0: tiliqua_pac::ENCODER0,
        }

        tiliqua_hal::impl_eurorack_pmod! {
            EurorackPmod0: tiliqua_pac::PMOD0_PERIPH,
        }

        tiliqua_hal::impl_persist! {
            Persist0: tiliqua_pac::PERSIST_PERIPH,
        }

        tiliqua_hal::impl_dma_framebuffer! {
            DMAFramebuffer0: tiliqua_pac::FRAMEBUFFER_PERIPH,
            Palette0: tiliqua_pac::PALETTE_PERIPH,
            Blit0: tiliqua_pac::BLIT,
            PixelPlot0: tiliqua_pac::PIXEL_PLOT,
            Line0: tiliqua_pac::LINE,
        }

        tiliqua_hal::impl_spiflash! {
            SPIFlash0: tiliqua_pac::SPIFLASH_CTRL,
        }
    };
}

#[macro_export]
macro_rules! new_framebuffer {
    ($peripherals:ident, $modeline:expr) => {
        DMAFramebuffer0::new(
            $peripherals.FRAMEBUFFER_PERIPH,
            $peripherals.PALETTE_PERIPH,
            $peripherals.BLIT,
            $peripherals.PIXEL_PLOT,
            $peripherals.LINE,
            tiliqua_pac::constants::PSRAM_FB_BASE,
            $modeline,
            tiliqua_pac::constants::BLIT_MEM_BASE,
        )
    };
}

#[cfg(test)]
mod tests {
    use super::*;
    use embedded_hal::i2c::{I2c, ErrorType, Operation};

    // Mock I2C implementation for testing
    pub struct MockI2c;

    impl ErrorType for MockI2c {
        type Error = core::convert::Infallible;
    }

    impl I2c for MockI2c {
        fn write(&mut self, addr: u8, bytes: &[u8]) -> Result<(), Self::Error> {
            log::info!("I2c::write(addr=0x{addr:02X}): {bytes:02X?}");
            Ok(())
        }

        fn write_read(
            &mut self,
            address: u8,
            bytes: &[u8],
            buffer: &mut [u8],
        ) -> Result<(), Self::Error> {
            log::info!("I2c::write_read(addr=0x{address:02X}):");
            log::info!("  Write: {bytes:02X?}");
            log::info!("  Read buffer size: {}", buffer.len());
            Ok(())
        }

        fn transaction(
            &mut self,
            address: u8,
            operations: &mut [Operation<'_>],
        ) -> Result<(), Self::Error> {
            log::info!("I2c::transaction(addr=0x{address:02X}):");
            for (i, op) in operations.iter().enumerate() {
                match op {
                    Operation::Read(buffer) => {
                        log::info!("  Op {}: Read {} bytes", i, buffer.len());
                    }
                    Operation::Write(bytes) => {
                        log::info!("  Op {i}: Write {bytes:02X?}");
                    }
                }
            }
            Ok(())
        }
    }

    use std::sync::Once;

    static INIT: Once = Once::new();

    pub fn setup_logger() {
      INIT.call_once(env_logger::init);
    }
}
