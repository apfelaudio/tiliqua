#[doc(hidden)]
pub use irq;
#[doc(hidden)]
pub use amaranth_soc_isr;
#[doc(hidden)]
pub use log;

#[macro_export]
macro_rules! impl_handlers {
    ($serial:ty, $timer:ty) => {
        pub mod handlers {
            use super::*;

            $crate::rt::irq::scoped_interrupts! {
                #[allow(non_camel_case_types)]
                pub enum Interrupt {
                    TIMER0,
                }
                use #[$crate::rt::amaranth_soc_isr::return_as_is];
            }

            static LOGGER: $crate::logger::WriteLogger<$serial> = $crate::logger::WriteLogger {
                writer: ::core::cell::RefCell::new(None),
                level: $crate::rt::log::Level::Trace,
            };

            pub fn logger_init(writer: $serial) {
                LOGGER.writer.replace(Some(writer));
                unsafe {
                    match $crate::rt::log::set_logger_racy(&LOGGER).map(|()| {
                        $crate::rt::log::set_max_level_racy($crate::rt::log::LevelFilter::Trace)
                    }) {
                        Ok(()) => (),
                        Err(_e) => {
                            panic!("Failed to set logger");
                        }
                    }
                }
            }

            #[cfg(not(test))]
            #[panic_handler]
            fn panic(panic_info: &::core::panic::PanicInfo) -> ! {
                if let Some(location) = panic_info.location() {
                    $crate::rt::log::error!("panic(): file '{}' at line {}",
                        location.file(),
                        location.line(),
                    );
                } else {
                    $crate::rt::log::error!("panic(): no location information");
                }
                $crate::rt::log::error!("{:?}", panic_info.message());
                loop {}
            }

            #[export_name = "ExceptionHandler"]
            fn exception_handler(trap_frame: &riscv_rt::TrapFrame) -> ! {
                $crate::rt::log::error!("exception_handler(): TrapFrame.ra={:x}", trap_frame.ra);
                loop {}
            }

            #[export_name = "DefaultHandler"]
            fn default_isr_handler() {
                let timer = unsafe { <$timer>::summon() };
                if timer.is_pending() {
                    unsafe { TIMER0(); }
                    timer.clear_pending();
                }
            }
        }
    };
}
