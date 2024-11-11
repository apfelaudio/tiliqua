#![allow(unused_imports, unused_mut, unused_variables)]

// Default handlers and logger implementation shared by
// all firmware images. FIXME: this is currently shared
// by symlinking this into each project, however it
// should really be shared in a more idiomatic way -
// maybe a macro would work?
//
// The difficulty here is that this module depends on
// Serial0/Timer0, which is only instantiated in the firmware
// images themselves, so it's not so trivial to put these
// in a reuseable library.

use crate::{hal, pac};
use crate::{Serial0, Timer0};

use core::panic::PanicInfo;
use core::cell::RefCell;
use core::fmt::Write;

use tiliqua_lib::logger::WriteLogger;

use irq::{handler, scoped_interrupts};
use amaranth_soc_isr::return_as_is;

use log::*;

scoped_interrupts! {
    #[allow(non_camel_case_types)]
    pub enum Interrupt {
        TIMER0,
    }
    use #[return_as_is];
}

static LOGGER: WriteLogger<Serial0> = WriteLogger {
    writer: RefCell::new(None),
    level: Level::Trace,
};

pub fn logger_init(writer: Serial0) {
    LOGGER.writer.replace(Some(writer));
    unsafe {
        match log::set_logger_racy(&LOGGER).map(|()| log::set_max_level_racy(LevelFilter::Trace)) {
            Ok(()) => (),
            Err(_e) => {
                panic!("Failed to set logger");
            }
        }
    }
}

#[cfg(not(test))]
#[panic_handler]
fn panic(panic_info: &PanicInfo) -> ! {
    if let Some(location) = panic_info.location() {
        error!("panic(): file '{}' at line {}",
            location.file(),
            location.line(),
        );
    } else {
        error!("panic(): no location information");
    }
    loop {}
}

#[export_name = "ExceptionHandler"]
fn exception_handler(trap_frame: &riscv_rt::TrapFrame) -> ! {
    error!("exception_handler(): TrapFrame.ra={:x}", trap_frame.ra);
    loop {}
}

#[export_name = "DefaultHandler"]
fn default_isr_handler() {
    let peripherals = unsafe { pac::Peripherals::steal() };
    let sysclk = pac::clock::sysclk();
    let timer = Timer0::new(peripherals.TIMER0, sysclk);
    if timer.is_pending() {
        unsafe { TIMER0(); }
        timer.clear_pending();
    }
}
