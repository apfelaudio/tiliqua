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

                    self.registers.address().write(|w| unsafe { w.address().bits(address) } );
                    for op in operations.iter() {
                        match op {
                            Operation::Write(bytes) => {
                                for b in bytes.iter() {
                                    self.registers.transaction_data().write(
                                        |w| unsafe { w.transaction_data().bits(0x0000u16 | *b as u16) } );
                                }
                            }
                            Operation::Read(bytes) => {
                                for b in bytes.iter() {
                                    self.registers.transaction_data().write(
                                        |w| unsafe { w.transaction_data().bits(0x0100u16 | *b as u16) } );
                                }
                            },
                        }
                    }

                    // Start executing transactions
                    self.registers.start().write(|w| w.start().bit(true) );

                    // Wait for completion
                    while self.registers.busy().read().busy().bit() { }

                    // TODO more error flags
                    if self.registers.err().read().err().bit() {
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
