#![cfg_attr(feature = "nightly", feature(error_in_core))]
#![cfg_attr(feature = "nightly", feature(panic_info_message))]
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
pub mod pmod;
pub mod polysynth;

pub use embedded_hal as hal;
pub use embedded_hal_nb as hal_nb;

pub use nb;
