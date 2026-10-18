// Minimal driver for TUSB322I USB Type-C CC line controller
//
// Enough to check if we are attached (PHY should be kept
// disconnected when we aren't) and provide device and host
// mode switching / detection.

use embedded_hal::i2c::I2c;
use embedded_hal::i2c::Operation;

const TUSB322_ADDR: u8 = 0x47;

#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub enum TUSB322Mode {
    DrpFromSnk,
    Ufp,
    Dfp,
}

#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub enum AttachedState {
    NotAttached,
    AttachedSrc,
    AttachedSnk,
    AttachedAccessory,
}

#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub enum CableDirection {
    CC1,
    CC2,
}

#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub enum CurrentModeAdvertise {
    Default,
    Mid,
    High,
    Reserved,
}

#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub enum CurrentModeDetect {
    Default,
    Medium,
    ChargeThrough500mA,
    High,
}

#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub enum AccessoryType {
    NoAccessory,
    Reserved1,
    Reserved2,
    Reserved3,
    AudioAccessory,
    AudioChargedThrough,
    DebugDfp,
    DebugUfp,
}

#[derive(Debug)]
pub struct ConnectionStatus {
    pub current_mode_advertise: CurrentModeAdvertise,
    pub current_mode_detect: CurrentModeDetect,
    pub accessory: AccessoryType,
    pub active_cable: bool,
}

#[derive(Debug)]
pub struct ConnectionStatusControl {
    pub attached_state: AttachedState,
    pub cable_dir: CableDirection,
    pub interrupt_status: bool,
    pub vconn_fault: bool,
    pub drp_duty_cycle: u8,
    pub disable_ufp_accessory: bool,
}

pub struct TUSB322Driver<I2C> {
    i2c: I2C,
}

impl<I2C: I2c> TUSB322Driver<I2C> {
    pub fn new(i2c: I2C) -> Self {
        Self { i2c }
    }

    fn read_register(&mut self, reg: u8) -> Result<u8, I2C::Error> {
        let mut buffer: [u8; 1] = [0];
        self.i2c.transaction(TUSB322_ADDR, &mut [
            Operation::Write(&[reg]),
            Operation::Read(&mut buffer)
        ])?;
        Ok(buffer[0])
    }

    fn write_register(&mut self, reg: u8, value: u8) -> Result<(), I2C::Error> {
        self.i2c.transaction(TUSB322_ADDR, &mut [
            Operation::Write(&[reg, value])
        ])
    }

    pub fn read_device_id(&mut self) -> Result<[u8; 8], I2C::Error> {
        let mut device_id: [u8; 8] = [0; 8];
        self.i2c.transaction(TUSB322_ADDR, &mut [
            Operation::Write(&[0x00u8]),
            Operation::Read(&mut device_id)
        ])?;
        Ok(device_id)
    }

    pub fn read_connection_status(&mut self) -> Result<ConnectionStatus, I2C::Error> {
        let reg = self.read_register(0x08)?;

        let current_mode_advertise = match (reg >> 6) & 0x3 {
            0b00 => CurrentModeAdvertise::Default,
            0b01 => CurrentModeAdvertise::Mid,
            0b10 => CurrentModeAdvertise::High,
            0b11 => CurrentModeAdvertise::Reserved,
            _ => CurrentModeAdvertise::Default,
        };

        let current_mode_detect = match (reg >> 4) & 0x3 {
            0b00 => CurrentModeDetect::Default,
            0b01 => CurrentModeDetect::Medium,
            0b10 => CurrentModeDetect::ChargeThrough500mA,
            0b11 => CurrentModeDetect::High,
            _ => CurrentModeDetect::Default,
        };

        let accessory = match (reg >> 1) & 0x7 {
            0b000 => AccessoryType::NoAccessory,
            0b001 => AccessoryType::Reserved1,
            0b010 => AccessoryType::Reserved2,
            0b011 => AccessoryType::Reserved3,
            0b100 => AccessoryType::AudioAccessory,
            0b101 => AccessoryType::AudioChargedThrough,
            0b110 => AccessoryType::DebugDfp,
            0b111 => AccessoryType::DebugUfp,
            _ => AccessoryType::NoAccessory,
        };

        let active_cable = (reg & 0x1) != 0;

        Ok(ConnectionStatus {
            current_mode_advertise,
            current_mode_detect,
            accessory,
            active_cable,
        })
    }

    pub fn read_connection_status_control(&mut self) -> Result<ConnectionStatusControl, I2C::Error> {
        let reg = self.read_register(0x09)?;

        let attached_state = match (reg >> 6) & 0x3 {
            0b00 => AttachedState::NotAttached,
            0b01 => AttachedState::AttachedSrc,
            0b10 => AttachedState::AttachedSnk,
            0b11 => AttachedState::AttachedAccessory,
            _ => AttachedState::NotAttached,
        };

        let cable_dir = if (reg >> 5) & 0x1 != 0 {
            CableDirection::CC2
        } else {
            CableDirection::CC1
        };

        let interrupt_status = (reg >> 4) & 0x1 != 0;
        let vconn_fault = (reg >> 3) & 0x1 != 0;
        let drp_duty_cycle = (reg >> 1) & 0x3;
        let disable_ufp_accessory = (reg & 0x1) != 0;

        Ok(ConnectionStatusControl {
            attached_state,
            cable_dir,
            interrupt_status,
            vconn_fault,
            drp_duty_cycle,
            disable_ufp_accessory,
        })
    }

    pub fn disable_term(&mut self) -> Result<(), I2C::Error> {
        self.write_register(0x0A, 0x01)
    }

    pub fn set_mode(&mut self, mode: TUSB322Mode) -> Result<(), I2C::Error> {
        self.disable_term()?;

        // Set mode (device/host)
        let mode_bits = match mode {
            TUSB322Mode::DrpFromSnk => 0b00,
            TUSB322Mode::Ufp => 0b01,
            TUSB322Mode::Dfp => 0b10,
        };

        self.write_register(0x0A, 0x01 | (mode_bits << 4))?;

        // Enable termination
        self.write_register(0x0A, mode_bits << 4)
    }

    pub fn soft_reset(&mut self) -> Result<(), I2C::Error> {
        self.write_register(0x0A, 0x08)
    }
}
