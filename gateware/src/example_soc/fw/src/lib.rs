#![no_std]
#![no_main]

pub use tiliqua_pac as pac;
pub use lunasoc_hal as hal;

lunasoc_hal::impl_serial! {
    Serial0: pac::UART,
}

lunasoc_hal::impl_timer! {
    Timer0: pac::TIMER,
}

pub mod log;
pub mod i2c;
