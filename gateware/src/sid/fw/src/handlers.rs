#![allow(unused_imports, unused_mut, unused_variables)]

use crate::{hal, pac};
use crate::Serial0;

use core::panic::PanicInfo;
use core::cell::RefCell;
use core::fmt::Write;

use tiliqua_lib::log::WriteLogger;

use log::*;

static LOGGER: WriteLogger<Serial0> = WriteLogger {
    writer: RefCell::new(None),
    level: Level::Trace,
};

pub fn logger_init(writer: Serial0) {
    LOGGER.writer.replace(Some(writer));
    match log::set_logger(&LOGGER).map(|()| log::set_max_level(LevelFilter::Trace)) {
        Ok(()) => (),
        Err(_e) => {
            panic!("Failed to set logger");
        }
    }
}

#[riscv_rt::pre_init]
unsafe fn pre_main() {
    pac::cpu::vexriscv::flush_icache();
    pac::cpu::vexriscv::flush_dcache();
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
fn default_isr_handler() -> ! {
    error!("default_isr_handler()");
    loop {}
}
