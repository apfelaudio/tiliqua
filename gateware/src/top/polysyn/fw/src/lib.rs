#![no_std]
#![no_main]

pub use tiliqua_pac as pac;
pub use tiliqua_hal as hal;

use pac::constants::N_VOICES;

hal::impl_tiliqua_soc_pac!();

tiliqua_hal::impl_polysynth! {
    Polysynth0: pac::SYNTH_PERIPH,
    N_VOICES
}

hal::impl_vector! {
    Vector0: pac::VECTOR_PERIPH,
}

tiliqua_lib::impl_handlers!(Serial0, Timer0);

pub mod options;
pub mod wavetable;
