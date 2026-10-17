#![no_std]
#![no_main]

pub use tiliqua_pac as pac;
pub use tiliqua_hal as hal;

hal::impl_tiliqua_soc_pac!();

hal::impl_delay_line! {
    DelayLine0: pac::DELAYLN_PERIPH0,
}

hal::impl_grain_player! {
    GrainPlayer0: pac::GRAIN_PERIPH0,
    GrainPlayer1: pac::GRAIN_PERIPH1,
    GrainPlayer2: pac::GRAIN_PERIPH2,
}

tiliqua_lib::impl_handlers!(Serial0, Timer0);

pub mod channel;
pub mod flash;
pub mod options;
