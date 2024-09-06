/// Re-export hal serial error type
pub use crate::hal_nb::serial::ErrorKind as Error;

#[macro_export]
macro_rules! impl_serial {
    ($(
        $SERIALX:ident: $PACUARTX:ty,
    )+) => {
        $(
            #[derive(Debug)]
            pub struct $SERIALX {
                registers: $PACUARTX,
            }

            // lifecycle
            impl $SERIALX {
                /// Create a new `Serial` from the [`UART`](crate::pac::UART) peripheral.
                pub fn new(registers: $PACUARTX) -> Self {
                    Self { registers }
                }

                /// Release the [`Uart`](crate::pac::UART) peripheral and consume self.
                pub fn free(self) -> $PACUARTX {
                    self.registers
                }

                /// Obtain a static `Serial` instance for use in e.g. interrupt handlers
                ///
                /// # Safety
                ///
                /// 'Tis thine responsibility, that which thou doth summon.
                pub unsafe fn summon() -> Self {
                    Self {
                        registers: <$PACUARTX>::steal(),
                    }
                }
            }

            // trait: From
            impl From<$PACUARTX> for $SERIALX {
                fn from(registers: $PACUARTX) -> $SERIALX {
                    $SERIALX::new(registers)
                }
            }

            // trait: core::fmt::Write
            impl core::fmt::Write for $SERIALX {
                fn write_str(&mut self, s: &str) -> core::fmt::Result {
                    use $crate::nb;
                    use $crate::hal_nb::serial::Write;
                    let _ = s
                        .bytes()
                        .map(|c| nb::block!(self.write(c)))
                        .last();
                    Ok(())
                }
            }

            // - embedded_hal 1.0 traits --------------------------------------

            // trait: hal_nb::serial::ErrorType
            impl $crate::hal_nb::serial::ErrorType for $SERIALX {
                type Error = $crate::serial::Error;
            }

            // trait: hal_nb::serial::Write
            impl $crate::hal_nb::serial::Write for $SERIALX {
                fn write(&mut self, byte: u8) -> $crate::nb::Result<(), Self::Error> {
                    if self.registers.tx_ready().read().txe().bit() {
                        self.registers.tx_data().write(|w| unsafe { w.data().bits(byte.into()) });
                        Ok(())
                    } else {
                        Err($crate::nb::Error::WouldBlock)
                    }
                }

                fn flush(&mut self) -> $crate::nb::Result<(), Self::Error> {
                    if self.registers.tx_ready().read().txe().bit() {
                        Ok(())
                    } else {
                        Err($crate::nb::Error::WouldBlock)
                    }
                }
            }
        )+
    }
}
