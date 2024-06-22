use embedded_hal::i2c::{Operation, ErrorKind};

use tiliqua_pac as pac;

pub struct I2cDevice {
    inner: pac::I2C0,
}

/// TODO: switch to embedded-hal proper once luna-soc is no longer
/// pinned to a version of embedded-hal pre-1.0.0 that doesn't have
/// the I2C transaction API yet.
///
impl I2cDevice {

    pub fn new(dev: pac::I2C0) -> Self {
        I2cDevice {
            inner: dev
        }
    }

    pub fn transaction(
        &mut self,
        address: u8,
        operations: &mut [Operation<'_>],
    ) -> Result<(), ErrorKind> {

        self.inner.address().write(|w| unsafe { w.address().bits(address) } );
        for op in operations.iter() {
            match op {
                Operation::Write(bytes) => {
                    for b in bytes.iter() {
                        self.inner.transaction_data().write(
                            |w| unsafe { w.transaction_data().bits(0x0000u16 | *b as u16) } );
                    }
                }
                Operation::Read(bytes) => {
                    for b in bytes.iter() {
                        self.inner.transaction_data().write(
                            |w| unsafe { w.transaction_data().bits(0x0100u16 | *b as u16) } );
                    }
                },
            }
        }

        // Start executing transactions
        self.inner.start().write(|w| w.start().bit(true) );

        // Wait for completion
        while self.inner.busy().read().busy().bit() { }

        // Copy out recieved bytes
        for op in operations.iter_mut() {
            match op {
                Operation::Read(bytes) => {
                    for b in bytes.iter_mut() {
                        *b = self.inner.rx_data().read().bits() as u8;
                    }
                },
                _ => {}
            }
        }


        Ok(())
    }
}
