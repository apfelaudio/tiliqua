#![no_std]
#![no_main]

pub use tiliqua_pac as pac;
pub use tiliqua_hal as hal;

use tiliqua_lib::generated_constants::N_VOICES;

tiliqua_hal::impl_serial! {
    Serial0: pac::UART0,
}

tiliqua_hal::impl_timer! {
    Timer0: pac::TIMER0,
}

tiliqua_hal::impl_i2c! {
    I2c0: pac::I2C0,
}

tiliqua_hal::impl_encoder! {
    Encoder0: pac::ENCODER0,
}

tiliqua_hal::impl_eurorack_pmod! {
    EurorackPmod0: pac::PMOD0_PERIPH,
}

tiliqua_hal::impl_polysynth! {
    Polysynth0: pac::SYNTH_PERIPH,
    N_VOICES
}

tiliqua_hal::impl_video! {
    Video0: pac::VIDEO_PERIPH,
}

pub mod handlers;
pub mod opts;
