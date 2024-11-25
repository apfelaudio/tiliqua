#[macro_export]
macro_rules! impl_i2c {
    ($(
        $I2CX:ident: $PACI2CX:ty,
    )+) => {
        $(
            /// I2c peripheral
            #[derive(Debug)]
            pub struct $I2CX {
                registers: $PACI2CX,
            }

            // lifecycle
            impl $I2CX {
                /// Create a new `I2c` from the [`I2C`](crate::pac::I2C) peripheral.
                pub fn new(registers: $PACI2CX) -> Self {
                    Self { registers }
                }

                /// Release the [`I2C`](crate::pac::I2C) peripheral and consume self.
                pub fn free(self) -> $PACI2CX {
                    self.registers
                }

                /// Obtain a static `I2c` instance for use in e.g. interrupt handlers
                pub unsafe fn summon() -> Self {
                    Self {
                        registers: <$PACI2CX>::steal(),
                    }
                }
            }

            impl From<$PACI2CX> for $I2CX {
                fn from(registers: $PACI2CX) -> $I2CX {
                    $I2CX::new(registers)
                }
            }

            impl $crate::hal::i2c::ErrorType for $I2CX {
                type Error = $crate::hal::i2c::ErrorKind;
            }

            impl $crate::hal::i2c::I2c<$crate::hal::i2c::SevenBitAddress> for $I2CX {
                fn transaction(
                    &mut self,
                    address: u8,
                    operations: &mut [$crate::hal::i2c::Operation<'_>],
                ) -> Result<(), $crate::hal::i2c::ErrorKind> {

                    use $crate::hal::i2c::Operation;

                    let mut enospace = false;

                    let mut total_bytes = 0;
                    for op in operations.iter() {
                        total_bytes += match op {
                            Operation::Write(bytes) => bytes.len(),
                            Operation::Read(bytes)  => bytes.len(),
                        };
                    }

                    self.registers.address().write(|w| unsafe { w.address().bits(address) } );

                    let mut sent_bytes = 0;
                    for op in operations.iter() {
                        match op {
                            Operation::Write(bytes) => {
                                for b in bytes.iter() {
                                    enospace |= self.registers.status().read().full().bit();
                                    self.registers.transaction_reg().write( |w| unsafe {
                                        w.rw().bit(false);
                                        w.data().bits(*b);
                                        w.last().bit(sent_bytes == total_bytes - 1)
                                    });
                                    sent_bytes += 1;
                                }
                            }
                            Operation::Read(bytes) => {
                                for b in bytes.iter() {
                                    enospace |= self.registers.status().read().full().bit();
                                    self.registers.transaction_reg().write( |w| unsafe {
                                        w.rw().bit(true);
                                        w.last().bit(sent_bytes == total_bytes - 1)
                                    } );
                                    sent_bytes += 1;
                                }
                            },
                        }
                    }

                    // Wait for completion
                    while self.registers.status().read().busy().bit() { }

                    // TODO more explicit error flags!

                    if enospace {
                        // transaction FIFO ran out of space (we ran and drained it anyway)
                        return Err($crate::hal::i2c::ErrorKind::Other);
                    }

                    // Note: this error flag is cleared on the next transaction start().
                    if self.registers.status().read().error().bit() {
                        return Err($crate::hal::i2c::ErrorKind::Other);
                    }


                    // Copy out recieved bytes
                    for op in operations.iter_mut() {
                        match op {
                            Operation::Read(bytes) => {
                                for b in bytes.iter_mut() {
                                    *b = self.registers.rx_data().read().bits() as u8;
                                }
                            },
                            _ => {}
                        }
                    }

                    Ok(())
                }
            }
        )+
    }
}
