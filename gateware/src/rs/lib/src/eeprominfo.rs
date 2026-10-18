use embedded_hal::i2c::I2c;
use serde_derive::{Serialize, Deserialize};
use tiliqua_hal::eeprom::{EepromDriver, EepromError};
use crc::{Crc, CRC_32_BZIP2};
use serde;

const EEPROM_CALIBRATION_ADDR: u8 = 0x00;
const EEPROM_CALIBRATION_SIZE: usize = 0x40;
const EEPROM_CONFIG_ADDR: u8 = 0x40;
const EEPROM_CONFIG_SIZE: usize = 0x40;
const CRC_ALGORITHM: Crc<u32> = Crc::<u32>::new(&CRC_32_BZIP2);

#[derive(Debug, PartialEq, Eq, Clone, Serialize, Deserialize)]
pub struct EepromCalibration {
    pub adc_scale: [i32; 4],
    pub adc_zero:  [i32; 4],
    pub dac_scale: [i32; 4],
    pub dac_zero:  [i32; 4],
    pub fractional_bits: u8,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EepromConfig {
    pub last_boot_slot: Option<u8>,
}

pub struct EepromManager<I2C> {
    eeprom: EepromDriver<I2C>,
}

impl<I2C> EepromManager<I2C>
where
    I2C: I2c,
{
    pub fn new(i2c: I2C) -> Self {
        Self {
            eeprom: EepromDriver::new(i2c),
        }
    }

    fn read_data<T, const SIZE: usize>(&mut self, addr: u8) -> Result<T, EepromError<I2C::Error>>
    where
        T: serde::de::DeserializeOwned,
    {
        let mut buffer = [0u8; SIZE];
        self.eeprom.read_bytes(addr, &mut buffer)?;
        let digest = CRC_ALGORITHM.digest();
        match postcard::from_bytes_crc32(&buffer, digest) {
            Ok(data) => Ok(data),
            Err(_) => Err(EepromError::InvalidData),
        }
    }

    fn write_data<T, const SIZE: usize>(&mut self, addr: u8, data: &T) -> Result<(), EepromError<I2C::Error>>
    where
        T: serde::Serialize,
    {
        let mut buffer = [0u8; SIZE];
        let digest = CRC_ALGORITHM.digest();
        let serialized = postcard::to_slice_crc32(data, &mut buffer, digest)
            .map_err(|e| {
                log::error!("eeprominfo: serialization failed: {e:?}");
                EepromError::InvalidSize
            })?;
        self.eeprom.write_bytes(addr, serialized)
    }

    pub fn read_calibration(&mut self) -> Result<EepromCalibration, EepromError<I2C::Error>> {
        self.read_data::<EepromCalibration, EEPROM_CALIBRATION_SIZE>(EEPROM_CALIBRATION_ADDR)
    }

    pub fn write_calibration(&mut self, cal_data: &EepromCalibration) -> Result<(), EepromError<I2C::Error>> {
        self.write_data::<EepromCalibration, EEPROM_CALIBRATION_SIZE>(EEPROM_CALIBRATION_ADDR, cal_data)
    }

    pub fn read_config(&mut self) -> Result<EepromConfig, EepromError<I2C::Error>> {
        self.read_data::<EepromConfig, EEPROM_CONFIG_SIZE>(EEPROM_CONFIG_ADDR)
    }

    pub fn write_config(&mut self, config: &EepromConfig) -> Result<(), EepromError<I2C::Error>> {
        self.write_data::<EepromConfig, EEPROM_CONFIG_SIZE>(EEPROM_CONFIG_ADDR, config)
    }
}
