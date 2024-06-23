#![no_std]
#![no_main]

pub use tiliqua_pac as pac;
pub use tiliqua_hal as hal;

tiliqua_hal::impl_serial! {
    Serial0: pac::UART,
}

tiliqua_hal::impl_timer! {
    Timer0: pac::TIMER,
}

pub mod log;
pub mod i2c;
