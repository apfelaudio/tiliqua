use embedded_hal::i2c::I2c;
use embedded_hal::i2c::Operation;

// I2C address of the CY8CMBR3108 device (0x37)
const CY8CMBR3108_ADDR: u8 = 0x37;

// CRC calculation constants (from cy8cmbr3108 reference code)
const CY8CMBR3XXX_CONFIG_DATA_LENGTH: usize = 126;
const CY8CMBR3XXX_CRC_BIT_WIDTH: u16 = 2 * 8;
const CY8CMBR3XXX_CRC_BIT4_MASK: u16 = 0x0F;
const CY8CMBR3XXX_CRC_BIT4_SHIFT: u16 = 4;
const CY8CMBR3XXX_CCITT16_DEFAULT_SEED: u16 = 0xffff;
const CY8CMBR3XXX_CCITT16_POLYNOM: u16 = 0x1021;

// Command register and commands
const REG_COMMAND: u8           = 0x86;
const REG_CONFIG_CRC0: u8       = 0x7e;
const REG_TOTAL_WORKING_SNS: u8 = 0x97;
const CMD_SAVE_CHECK_CRC: u8    = 0x02;
const CMD_WRITE_RESET: u8       = 0xFF;

/// Default configuration for the CY8CMBR3108/CY8CMBR3116
/// Hand-picked after a lot of fiddling, for Tiliqua.
const DEFAULT_CONFIG: [u8; CY8CMBR3XXX_CONFIG_DATA_LENGTH] = [
    0x00,
    0x00, // SENSOR_EN (all disabled by default)
    0x00, // FSS_EN
    0x00,
    0x00, // TOGGLE_EN
    0x00,
    0x00, // LED_ON_EN
    0x00,
    0x00, // SENSITIVITY0
    0x00, // SENSITIVITY1
    0x00, // SENSITIVITY2
    0x00, // SENSITIVITY3
    0x80, // BASE_THRESHOLD0
    0x80, // BASE_THRESHOLD1
    0x80, // FINGER_THRESHOLD2
    0x80, // FINGER_THRESHOLD3
    0x80, // FINGER_THRESHOLD4
    0x80, // FINGER_THRESHOLD5
    0x80, // FINGER_THRESHOLD6
    0x80, // FINGER_THRESHOLD7
    0x80, // FINGER_THRESHOLD8
    0x80, // FINGER_THRESHOLD9
    0x80, // FINGER_THRESHOLD10
    0x80, // FINGER_THRESHOLD11
    0x80, // FINGER_THRESHOLD12
    0x80, // FINGER_THRESHOLD13
    0x80, // FINGER_THRESHOLD14
    0x80, // FINGER_THRESHOLD15
    0x04, // SENSOR_DEBOUNCE
    0x9f, // BUTTON_HYS
    0x00,
    0xB2, // BUTTON_LBR
    0x94, // BUTTON_NNT
    0x94, // BUTTON_NT
    0x00,
    0x00,
    0x00,
    0x00,
    0x00, // PROX_EN
    0x80, // PROX_CFG
    0x05, // PROX_CFG2
    0x00,
    0x00, // PROX_TOUCH_TH0
    0x02,
    0x00, // PROX_TOUCH_TH1
    0x02,
    0x00, // PROX_RESOLUTION0
    0x00, // PROX_RESOLUTION1
    0x00, // PROX_HYS
    0x00,
    0x00, // PROX_LBR
    0x00, // PROX_NNT
    0x00, // PROX_NT
    0x1e, // PROX_POSITIVE_TH0
    0x1e, // PROX_POSITIVE_TH1
    0x00,
    0x00,
    0x1e, // PROX_NEGATIVE_TH0
    0x1e, // PROX_NEGATIVE_TH1
    0x00,
    0x00,
    0x00, // LED_ON_TIME
    0x01, // BUZZER_CFG
    0x01, // BUZZER_ON_TIME
    0x00, // GPO_CFG
    0xff, // PWM_DUTYCYCLE_CFG0
    0xff, // PWM_DUTYCYCLE_CFG1
    0xff, // PWM_DUTYCYCLE_CFG2
    0xff, // PWM_DUTYCYCLE_CFG3
    0x00, // PWM_DUTYCYCLE_CFG4
    0x00, // PWM_DUTYCYCLE_CFG5
    0x00, // PWM_DUTYCYCLE_CFG6
    0x00, // PWM_DUTYCYCLE_CFG7
    0x00,
    0x00,
    0x00,
    0x10, // SPO_CFG
    0x03, // DEVICE_CFG0
    0x00, // DEVICE_CFG1
    0x20, // DEVICE_CFG2
    0x00, // DEVICE_CFG3
    0x37, // I2C_ADDR
    0x01, // REFRESH_CTRL
    0x0f,
    0x00,
    0x0a, // STATE_TIMEOUT
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00, // SLIDER_CFG
    0x00,
    0x00,
    0x00,
    0x00, // SLIDER1_CFG
    0x00, // SLIDER1_RESOLUTION
    0x00, // SLIDER1_THRESHOLD
    0x00,
    0x00,
    0x00,
    0x00, // SLIDER2_CFG
    0x00, // SLIDER2_RESOLUTION
    0x00, // SLIDER2_THRESHOLD
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00, // SLIDER_LBR
    0x00, // SLIDER_NNT
    0x00, // SLIDER_NT
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00,
    0x00, // SCRATCHPAD0
    0x00, // SCRATCHPAD1
    0x00,
    0x00,
];

pub struct Cy8cmbr3108Driver<I2C> {
    i2c: I2C,
    pub config: [u8; CY8CMBR3XXX_CONFIG_DATA_LENGTH],
}

impl<I2C: I2c> Cy8cmbr3108Driver<I2C> {

    pub fn new(i2c: I2C, sensors_en: &[u8]) -> Self {
        let mut config = DEFAULT_CONFIG;
        // Set enabled sensor bits by index.
        for n_sensor in sensors_en {
            if *n_sensor < 8 {
                config[0] |= 1<<*n_sensor;
            } else {
                config[1] |= 1<<(*n_sensor-8);
            }
        }
        Self { i2c, config }
    }

    pub fn calculate_crc(&self) -> u16 {
        let mut seed = CY8CMBR3XXX_CCITT16_DEFAULT_SEED;
        // Calculate CRC for all configuration bytes except the CRC itself
        for byte_value in &self.config {
            // Process high 4 bits
            let table_index = (byte_value >> CY8CMBR3XXX_CRC_BIT4_SHIFT) as u16 & CY8CMBR3XXX_CRC_BIT4_MASK
                ^ (seed >> (CY8CMBR3XXX_CRC_BIT_WIDTH - CY8CMBR3XXX_CRC_BIT4_SHIFT));
            seed = (CY8CMBR3XXX_CCITT16_POLYNOM * table_index) 
                ^ (seed << CY8CMBR3XXX_CRC_BIT4_SHIFT);
            seed &= 0xffff;
            // Process low 4 bits
            let table_index = *byte_value as u16 & CY8CMBR3XXX_CRC_BIT4_MASK
                ^ (seed >> (CY8CMBR3XXX_CRC_BIT_WIDTH - CY8CMBR3XXX_CRC_BIT4_SHIFT));
            seed = (CY8CMBR3XXX_CCITT16_POLYNOM * table_index) 
                ^ (seed << CY8CMBR3XXX_CRC_BIT4_SHIFT);
            seed &= 0xffff;
        }
        seed
    }

    pub fn check_busy(&mut self) -> Result<bool, I2C::Error> {
        let cmd_register = self.read_register(REG_COMMAND)?;
        Ok(cmd_register != 0)
    }

    pub fn disable(&mut self) -> Result<(), I2C::Error> {
        self.write_register(REG_COMMAND, 0x07)?; // Disable + enter low-power mode
        Ok(())
    }

    pub fn get_stored_crc(&mut self) -> Result<u16, I2C::Error> {
        let crc0 = self.read_register(REG_CONFIG_CRC0)?;
        let crc1 = self.read_register(REG_CONFIG_CRC0+1)?;
        Ok(crc0 as u16 + ((crc1 as u16) << 8))
    }

    fn commit_config_to_nvm(&mut self) -> Result<(), I2C::Error> {
        let crc = self.calculate_crc();
        let crc0 = (crc&0xff) as u8;
        let crc1 = (crc>>8) as u8;
        self.write_register(REG_CONFIG_CRC0, crc0)?;
        self.write_register(REG_CONFIG_CRC0+1, crc1)?;
        // Check CRC, save to NVM.
        self.write_register(REG_COMMAND, CMD_SAVE_CHECK_CRC)
    }

    pub fn reprogram_nvm_and_reset(&mut self) -> Result<(), I2C::Error> {
        self.write_config_to_sram()?;
        self.commit_config_to_nvm()?;
        let mut timeout = 0;
        while timeout != 100 {
            match self.check_busy() {
                Ok(false) => break,
                _ => timeout += 1
            }
        }
        self.reset()
    }

    pub fn read_n_working_sensors(&mut self) -> Result<u8, I2C::Error> {
        let total_working_sns = self.read_register(REG_TOTAL_WORKING_SNS)?;
        Ok(total_working_sns & 0x1f)
    }

    fn read_register(&mut self, register: u8) -> Result<u8, I2C::Error> {
        let mut buffer = [0u8];
        self.i2c.transaction(
            CY8CMBR3108_ADDR,
            &mut [Operation::Write(&[register]), Operation::Read(&mut buffer)]
        )?;
        Ok(buffer[0])
    }

    fn write_register(&mut self, register: u8, value: u8) -> Result<(), I2C::Error> {
        self.i2c.transaction(
            CY8CMBR3108_ADDR,
            &mut [Operation::Write(&[register, value])]
        )
    }

    fn write_config_to_sram(&mut self) -> Result<(), I2C::Error> {
        for reg_addr in 0..CY8CMBR3XXX_CONFIG_DATA_LENGTH {
            let buffer = [reg_addr as u8, self.config[reg_addr]];
            self.i2c.transaction(
                CY8CMBR3108_ADDR,
                &mut [Operation::Write(&buffer)]
            )?;
        }
        Ok(())
    }

    pub fn reset(&mut self) -> Result<(), I2C::Error> {
        self.write_register(REG_COMMAND, CMD_WRITE_RESET)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tests::{setup_logger, MockI2c};

    #[test]
    fn test_crc_calculation() {
        setup_logger();
        let mut cy8 = Cy8cmbr3108Driver::new(MockI2c, &[5, 7, 8, 9, 10, 11, 12, 13]);
        log::info!("sensor_en0 = {:#x}", cy8.config[0]);
        log::info!("sensor_en1 = {:#x}", cy8.config[1]);
        log::info!("default_crc = {:#x}", cy8.calculate_crc());
        assert_eq!(cy8.calculate_crc(), 0x5fd7);
        cy8.commit_config_to_nvm();
    }

    #[test]
    fn test_write_config_to_sram() {
        setup_logger();
        let mut cy8 = Cy8cmbr3108Driver::new(MockI2c, &[]);
        cy8.write_config_to_sram();
    }
}
