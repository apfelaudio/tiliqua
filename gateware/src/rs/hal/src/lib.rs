#![no_std]
#![allow(clippy::inline_always)]
#![allow(clippy::must_use_candidate)]

// modules
pub mod serial;
pub mod timer;
pub mod i2c;
pub mod dma_display;
pub mod encoder;
pub mod pca9635;
pub mod si5351;
pub mod pmod;
pub mod polysynth;
pub mod video;

pub use embedded_hal as hal;
pub use embedded_hal_nb as hal_nb;

#[macro_use]
extern crate bitflags;

pub use nb;
