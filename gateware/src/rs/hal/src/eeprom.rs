use embedded_hal::i2c::{I2c, Operation};

pub const EEPROM_ADDR: u8 = 0x52;
pub const EEPROM_MAX_TRANSACTION_SIZE: usize = 5;

#[derive(Debug)]
pub enum EepromError<I2cError> {
    I2c(I2cError),
    InvalidSize,
    InvalidData,
}

pub struct EepromDriver<I2C> {
    i2c: I2C,
    address: u8,
}

impl<I2C> EepromDriver<I2C> 
where
    I2C: I2c,
{
    pub fn new(i2c: I2C) -> Self {
        Self {
            i2c,
            address: EEPROM_ADDR,
        }
    }

    fn read_bytes_bounded(&mut self, addr: u8, buffer: &mut [u8]) -> Result<(), EepromError<I2C::Error>> {
        if buffer.len() > (EEPROM_MAX_TRANSACTION_SIZE - 1) {
            return Err(EepromError::InvalidSize);
        }
        self.i2c.transaction(self.address, &mut [
            Operation::Write(&[addr]),
            Operation::Read(buffer)
        ]).map_err(EepromError::I2c)
    }

    fn write_bytes_bounded(&mut self, addr: u8, data: &[u8]) -> Result<(), EepromError<I2C::Error>> {
        if data.len() > EEPROM_MAX_TRANSACTION_SIZE - 1 {
            return Err(EepromError::InvalidSize);
        }
        let mut write_buffer = [0u8; EEPROM_MAX_TRANSACTION_SIZE];
        write_buffer[0] = addr;
        write_buffer[1..=data.len()].copy_from_slice(data);
        let mut attempts = 0;
        loop {
            match self.i2c.transaction(self.address, &mut [
                Operation::Write(&write_buffer[..=data.len()])
            ]) {
                Ok(()) => break,
                Err(e) => {
                    attempts += 1;
                    if attempts >= 10 {
                        return Err(EepromError::I2c(e));
                    }
                }
            }
        }
        Ok(())
    }

    pub fn read_bytes(&mut self, addr: u8, buffer: &mut [u8]) -> Result<(), EepromError<I2C::Error>> {
        let mut offset = 0;
        while offset < buffer.len() {
            let chunk_size = (buffer.len() - offset).min(EEPROM_MAX_TRANSACTION_SIZE - 1);
            self.read_bytes_bounded(
                addr + offset as u8, 
                &mut buffer[offset..offset + chunk_size]
            )?;
            offset += chunk_size;
        }
        Ok(())
    }

    pub fn write_bytes(&mut self, addr: u8, data: &[u8]) -> Result<(), EepromError<I2C::Error>> {
        let mut offset = 0;
        while offset < data.len() {
            let chunk_size = (data.len() - offset).min(EEPROM_MAX_TRANSACTION_SIZE - 1);
            self.write_bytes_bounded(
                addr + offset as u8,
                &data[offset..offset + chunk_size]
            )?;
            offset += chunk_size;
        }
        Ok(())
    }

    pub fn read_id(&mut self) -> Result<[u8; 6], EepromError<I2C::Error>> {
        let mut id = [0u8; 6];
        self.read_bytes(0xFA, &mut id)?;
        Ok(id)
    }
}
