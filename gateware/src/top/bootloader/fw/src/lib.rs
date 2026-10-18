#![no_std]
#![no_main]

pub use tiliqua_pac as pac;
pub use tiliqua_hal as hal;

hal::impl_tiliqua_soc_pac!();

guh_usb_msc::impl_usb_msc! {
    UsbMsc0: pac::USB_MSC,
}

tiliqua_lib::impl_handlers!(Serial0, Timer0);

pub mod options;
