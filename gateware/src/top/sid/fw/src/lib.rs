#![no_std]
#![no_main]

pub use tiliqua_pac as pac;
pub use tiliqua_hal as hal;

hal::impl_tiliqua_soc_pac!();

hal::impl_scope! {
    Scope0: pac::SCOPE_PERIPH,
}

tiliqua_lib::impl_handlers!(Serial0, Timer0);

pub mod options;
