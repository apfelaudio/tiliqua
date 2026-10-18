/*
   Copyright 2018 Ilya Epifanov
   Copyright 2024 Seb Holzapfel

   Licensed under the Apache License, Version 2.0, <LICENSE-APACHE or
   http://apache.org/licenses/LICENSE-2.0> or the MIT license <LICENSE-MIT or
   http://opensource.org/licenses/MIT>, at your option. This file may not be
   copied, modified, or distributed except according to those terms.
*/

use embedded_hal::i2c::I2c;
use core::fmt;

#[cfg(test)]
use log;

#[derive(Debug)]
pub enum Error {
    CommunicationError,
    InvalidParameter,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self {
            Self::CommunicationError => write!(f, "Communication Error"),
            Self::InvalidParameter => write!(f, "Invalid Parameter")
        }
    }
}

#[derive(Debug, Copy, Clone)]
pub enum CrystalLoad {
    _6,
    _8,
    _10,
}

#[derive(Debug, Copy, Clone)]
pub enum PLL {
    A,
    B,
}

#[derive(Debug, Copy, Clone)]
pub enum FeedbackMultisynth {
    MSNA,
    MSNB,
}

#[derive(Debug, Copy, Clone)]
pub enum Multisynth {
    MS0,
    MS1,
    MS2,
    MS3,
    MS4,
    MS5,
}

#[derive(Debug, Copy, Clone)]
pub enum SimpleMultisynth {
    MS6,
    MS7,
}

#[derive(Debug, Copy, Clone)]
pub enum ClockOutput {
    Clk0 = 0,
    Clk1,
    Clk2,
    Clk3,
    Clk4,
    Clk5,
    Clk6,
    Clk7,
}

#[derive(Debug, Copy, Clone)]
pub enum OutputDivider {
    Div1 = 0,
    Div2,
    Div4,
    Div8,
    Div16,
    Div32,
    Div64,
    Div128,
}

const ADDRESS: u8 = 0b0110_0000;

impl PLL {
    pub fn multisynth(&self) -> FeedbackMultisynth {
        match *self {
            Self::A => FeedbackMultisynth::MSNA,
            Self::B => FeedbackMultisynth::MSNB,
        }
    }
}

trait FractionalMultisynth {
    fn base_addr(&self) -> u8;
    fn ix(&self) -> u8;
}

impl FractionalMultisynth for FeedbackMultisynth {
    fn base_addr(&self) -> u8 {
        match *self {
            Self::MSNA => 26,
            Self::MSNB => 34,
        }
    }
    fn ix(&self) -> u8 {
        match *self {
            Self::MSNA => 6,
            Self::MSNB => 7,
        }
    }
}

impl FractionalMultisynth for Multisynth {
    fn base_addr(&self) -> u8 {
        match *self {
            Self::MS0 => 42,
            Self::MS1 => 50,
            Self::MS2 => 58,
            Self::MS3 => 66,
            Self::MS4 => 74,
            Self::MS5 => 82,
        }
    }
    fn ix(&self) -> u8 {
        match *self {
            Self::MS0 => 0,
            Self::MS1 => 1,
            Self::MS2 => 2,
            Self::MS3 => 3,
            Self::MS4 => 4,
            Self::MS5 => 5,
        }
    }
}

impl SimpleMultisynth {
    pub fn base_addr(&self) -> u8 {
        match *self {
            Self::MS6 => 90,
            Self::MS7 => 91,
        }
    }
}

#[derive(Debug, Copy, Clone)]
enum Register {
    DeviceStatus = 0,
    OutputEnable = 3,
    Clk0 = 16,
    Clk1 = 17,
    Clk2 = 18,
    Clk3 = 19,
    Clk4 = 20,
    Clk5 = 21,
    Clk6 = 22,
    Clk7 = 23,
    Clk0PhOff = 165,
    Clk1PhOff = 166,
    Clk2PhOff = 167,
    Clk3PhOff = 168,
    Clk4PhOff = 169,
    Clk5PhOff = 170,
    PLLReset = 177,
    CrystalLoad = 183,
}

impl Register {
    pub fn addr(self) -> u8 {
        self as u8
    }
}

bitflags! {
    #[derive(Debug)]
    pub struct DeviceStatusBits: u8 {
        const SYS_INIT  = 0b1000_0000;
        const LOL_B     = 0b0100_0000;
        const LOL_A     = 0b0010_0000;
        const LOS_CLKIN = 0b0001_0000;
        const LOS_XTAL  = 0b0000_1000;
    }
}

bitflags! {
    struct CrystalLoadBits: u8 {
        const RESERVED = 0b00_010010;
        const CL_MASK = 0b11_000000;
        const CL_6 = 0b01_000000;
        const CL_8 = 0b10_000000;
        const CL_10 = 0b11_000000;
    }
}

bitflags! {
    struct ClockControlBits: u8 {
        const CLK_PDN = 0b1000_0000;
        const MS_INT = 0b0100_0000;
        const MS_SRC = 0b0010_0000;
        const CLK_INV = 0b0001_0000;
        const CLK_SRC_MASK = 0b0000_1100;
        const CLK_SRC_XTAL = 0b0000_0000;
        const CLK_SRC_CLKIN = 0b0000_0100;
        const CLK_SRC_MS_ALT = 0b0000_1000;
        const CLK_SRC_MS = 0b0000_1100;
        const CLK_DRV_MASK = 0b0000_0011;
        const CLK_DRV_2 = 0b0000_0000;
        const CLK_DRV_4 = 0b0000_0001;
        const CLK_DRV_6 = 0b0000_0010;
        const CLK_DRV_8 = 0b0000_0011;
    }
}

bitflags! {
    struct PLLResetBits: u8 {
        const PLLB_RST = 0b1000_0000;
        const PLLA_RST = 0b0010_0000;
    }
}

impl ClockOutput {
    fn register(self) -> Register {
        match self {
            Self::Clk0 => Register::Clk0,
            Self::Clk1 => Register::Clk1,
            Self::Clk2 => Register::Clk2,
            Self::Clk3 => Register::Clk3,
            Self::Clk4 => Register::Clk4,
            Self::Clk5 => Register::Clk5,
            Self::Clk6 => Register::Clk6,
            Self::Clk7 => Register::Clk7,
        }
    }

    fn ix(self) -> u8 {
        self as u8
    }
}

impl OutputDivider {
    fn bits(self) -> u8 {
        self as u8
    }

    fn min_divider(desired_divider: u16) -> Result<Self, Error> {
        match 16 - (desired_divider.max(1) - 1).leading_zeros() {
            0 => Ok(Self::Div1),
            1 => Ok(Self::Div2),
            2 => Ok(Self::Div4),
            3 => Ok(Self::Div8),
            4 => Ok(Self::Div16),
            5 => Ok(Self::Div32),
            6 => Ok(Self::Div64),
            7 => Ok(Self::Div128),
            _ => Err(Error::InvalidParameter),
        }
    }

    fn denominator_u8(self) -> u8 {
        match self {
            Self::Div1 => 1,
            Self::Div2 => 2,
            Self::Div4 => 4,
            Self::Div8 => 8,
            Self::Div16 => 16,
            Self::Div32 => 32,
            Self::Div64 => 64,
            Self::Div128 => 128,
        }
    }
}

fn i2c_error<E>(_: E) -> Error {
    Error::CommunicationError
}

pub enum Current {
    Output2mA = 0b00,
    Output4mA = 0b01,
    Output6mA = 0b10,
    Output8mA = 0b11,
}

/// Si5351 driver
pub struct Si5351Device<I2C> {
    i2c: I2C,
    address: u8,
    xtal_freq: u32,
    clk_enabled_mask: u8,
    ms_int_mode_mask: u8,
    ms_src_mask: u8,
}

pub struct SpreadParams {
    f_pfd: f32,      // Input frequency to PLLA in Hz
    a: f32,          // PLLA Multisynth ratio components
    b: f32,
    c: f32,
    ssc_amp: f32,    // Spread amplitude (e.g., 0.01 for 1%)
}

impl SpreadParams {

    fn calc_ssudp(&self) -> u16 {
        ((self.f_pfd / (4.0 * 31500.0)) as u16) & 0x7FF
    }

    fn calc_center_spread(&self) -> (u16, u16, u16, u16, u16, u16) {
        let ssudp = self.calc_ssudp();

        // Calculate intermediate SSUP value
        let ssup = 128.0 * (self.a + self.b / self.c) *
            (self.ssc_amp / ((1.0 - self.ssc_amp) * f32::from(ssudp)));

        // Calculate intermediate SSDN value
        let ssdn = 128.0 * (self.a + self.b / self.c) *
            (self.ssc_amp / ((1.0 + self.ssc_amp) * f32::from(ssudp)));

        // Calculate final register values
        let ssup_p1 = ssup as u16 & 0x7FF;
        let ssup_p2 = ((32767.0f32 * (ssup - f32::from(ssup_p1))) as u16) & 0x3FFF;
        let ssup_p3 = 0x7FFF;

        let ssdn_p1 = ssdn as u16 & 0x7FF;
        let ssdn_p2 = ((32767.0f32 * (ssdn - f32::from(ssdn_p1))) as u16) & 0x3FFF;
        let ssdn_p3 = 0x7FFF;

        (ssup_p1, ssup_p2, ssup_p3, ssdn_p1, ssdn_p2, ssdn_p3)
    }
}

pub trait Si5351 {
    fn init_adafruit_module(&mut self) -> Result<(), Error>;
    fn init(&mut self, xtal_load: CrystalLoad) -> Result<(), Error>;
    fn read_device_status(&mut self) -> Result<DeviceStatusBits, Error>;

    fn find_int_dividers_for_max_pll_freq(
        &self,
        max_pll_freq: u32,
        freq: u32,
    ) -> Result<(u16, OutputDivider), Error>;
    fn find_pll_coeffs_for_dividers(
        &self,
        total_div: u32,
        denom: u32,
        freq: u32,
    ) -> Result<(u8, u32), Error>;

    fn set_frequency(&mut self, pll: PLL, clk: ClockOutput, freq: u32, spread: Option<f32>) -> Result<(), Error>;
    fn set_frequencies(&mut self, pll: PLL, clks: &[ClockOutput], freqs: &[u32], spread: Option<f32>) -> Result<(), Error>;
    fn set_clock_enabled(&mut self, clk: ClockOutput, enabled: bool);
    fn setup_spread_spectrum(&mut self, pll: PLL, params: &SpreadParams) -> Result<u8, Error>;
    fn clear_spread_spectrum(&mut self) -> Result<(), Error>;

    fn flush_output_enabled(&mut self) -> Result<(), Error>;
    fn flush_clock_control(&mut self, clk: ClockOutput) -> Result<(), Error>;

    fn setup_pll_int(&mut self, pll: PLL, mult: u8) -> Result<(), Error>;
    fn setup_pll(&mut self, pll: PLL, mult: u8, num: u32, denom: u32) -> Result<(), Error>;
    fn setup_multisynth_int(
        &mut self,
        ms: Multisynth,
        mult: u16,
        r_div: OutputDivider,
    ) -> Result<(), Error>;
    fn setup_multisynth(
        &mut self,
        ms: Multisynth,
        div: u16,
        num: u32,
        denom: u32,
        r_div: OutputDivider,
    ) -> Result<(), Error>;
    fn select_clock_pll(&mut self, clocl: ClockOutput, pll: PLL);

    fn set_phase_offset(&mut self, clk: ClockOutput, offset: u8) -> Result<(), Error>;
    fn set_current(&mut self, clk: ClockOutput, current: Current) -> Result<(), Error>;
}

impl<I2C: I2c> Si5351Device<I2C> {
    /// Creates a new driver from a I2C peripheral
    pub fn new(i2c: I2C, address_bit: bool, xtal_freq: u32) -> Self {
        

        Self {
            i2c,
            address: ADDRESS | u8::from(address_bit),
            xtal_freq,
            clk_enabled_mask: 0,
            ms_int_mode_mask: 0,
            ms_src_mask: 0,
        }
    }

    pub fn new_adafruit_module(i2c: I2C) -> Self {
        Self::new(i2c, false, 25_000_000)
    }

    fn write_ms_config<MS: FractionalMultisynth + Copy>(
        &mut self,
        ms: MS,
        int: u16,
        frac_num: u32,
        frac_denom: u32,
        r_div: OutputDivider,
    ) -> Result<(), Error> {
        if frac_denom == 0 {
            return Err(Error::InvalidParameter);
        }
        if frac_num > 0xfffff {
            return Err(Error::InvalidParameter);
        }
        if frac_denom > 0xfffff {
            return Err(Error::InvalidParameter);
        }

        let (p1, p2, p3) = if frac_num == 0 {
            (128 * u32::from(int) - 512, 0, 1)
        } else {
            let ratio = (128u64 * u64::from(frac_num) / u64::from(frac_denom)) as u32;
            (128 * u32::from(int) + ratio - 512, 128 * frac_num - frac_denom * ratio, frac_denom)
        };

        self.write_synth_registers(
            &ms,
            [
                ((p3 & 0x0000FF00) >> 8) as u8,
                p3 as u8,
                ((p1 & 0x00030000) >> 16) as u8 | r_div.bits(),
                ((p1 & 0x0000FF00) >> 8) as u8,
                p1 as u8,
                (((p3 & 0x000F0000) >> 12) | ((p2 & 0x000F0000) >> 16)) as u8,
                ((p2 & 0x0000FF00) >> 8) as u8,
                p2 as u8,
            ],
        )?;

        if frac_num == 0 {
            self.ms_int_mode_mask |= ms.ix();
        } else {
            self.ms_int_mode_mask &= !ms.ix();
        }

        Ok(())
    }

    fn reset_pll(&mut self, pll: PLL) -> Result<(), Error> {
        self.write_register(
            Register::PLLReset,
            match pll {
                PLL::A => PLLResetBits::PLLA_RST.bits(),
                PLL::B => PLLResetBits::PLLB_RST.bits(),
            },
        )?;

        Ok(())
    }

    fn read_register(&mut self, reg: Register) -> Result<u8, Error> {
        let mut buffer: [u8; 1] = [0];
        self.i2c
            .write_read(self.address, &[reg.addr()], &mut buffer)
            .map_err(i2c_error)?;
        Ok(buffer[0])
    }

    fn write_register(&mut self, reg: Register, byte: u8) -> Result<(), Error> {
        self.i2c
            .write(self.address, &[reg.addr(), byte])
            .map_err(i2c_error)
    }

    fn write_ssc_registers(
        &mut self,
        params: [u8; 13],
    ) -> Result<(), Error> {
        self.i2c
            .write(
                self.address,
                &[
                    0x95,
                    params[0],
                    params[1],
                    params[2],
                    params[3],
                    params[4],
                    params[5],
                    params[6],
                    params[7],
                    params[8],
                    params[9],
                    params[10],
                    params[11],
                    params[12],
                ],
            )
            .map_err(i2c_error)
    }

    fn write_synth_registers<MS: FractionalMultisynth>(
        &mut self,
        ms: &MS,
        params: [u8; 8],
    ) -> Result<(), Error> {
        self.i2c
            .write(
                self.address,
                &[
                    ms.base_addr(),
                    params[0],
                    params[1],
                    params[2],
                    params[3],
                    params[4],
                    params[5],
                    params[6],
                    params[7],
                ],
            )
            .map_err(i2c_error)
    }
}

impl<I2C: I2c> Si5351 for Si5351Device<I2C>
{
    fn init_adafruit_module(&mut self) -> Result<(), Error> {
        self.init(CrystalLoad::_10)
    }

    fn init(&mut self, xtal_load: CrystalLoad) -> Result<(), Error> {
        loop {
            let device_status = self.read_device_status()?;
            if !device_status.contains(DeviceStatusBits::SYS_INIT) {
                break;
            }
        }

        self.write_register(
            Register::CrystalLoad,
            (CrystalLoadBits::RESERVED
                | match xtal_load {
                    CrystalLoad::_6 => CrystalLoadBits::CL_6,
                    CrystalLoad::_8 => CrystalLoadBits::CL_8,
                    CrystalLoad::_10 => CrystalLoadBits::CL_10,
                })
            .bits(),
        )?;

        self.clear_spread_spectrum()?;
        self.set_clock_enabled(ClockOutput::Clk0, false);
        self.flush_clock_control(ClockOutput::Clk0)?;
        self.set_clock_enabled(ClockOutput::Clk1, false);
        self.flush_clock_control(ClockOutput::Clk1)?;
        self.flush_output_enabled()?;
        self.reset_pll(PLL::A)?;

        Ok(())
    }

    fn read_device_status(&mut self) -> Result<DeviceStatusBits, Error> {
        Ok(DeviceStatusBits::from_bits_truncate(
            self.read_register(Register::DeviceStatus)?,
        ))
    }

    fn find_int_dividers_for_max_pll_freq(
        &self,
        max_pll_freq: u32,
        freq: u32,
    ) -> Result<(u16, OutputDivider), Error> {
        let total_divider = (max_pll_freq / freq) as u16;

        let r_div = OutputDivider::min_divider(total_divider / 900)?;

        let ms_div = (total_divider / (2 * u16::from(r_div.denominator_u8())) * 2).max(6);
        if ms_div > 1800 {
            return Err(Error::InvalidParameter);
        }

        Ok((ms_div, r_div))
    }

    fn find_pll_coeffs_for_dividers(
        &self,
        total_div: u32,
        denom: u32,
        freq: u32,
    ) -> Result<(u8, u32), Error> {
        if denom == 0 || denom > 0xfffff {
            return Err(Error::InvalidParameter);
        }

        let pll_freq = freq * total_div;

        let mult = (pll_freq / self.xtal_freq) as u8;
        let f = (u64::from(pll_freq % self.xtal_freq) * u64::from(denom) / u64::from(self.xtal_freq)) as u32;

        Ok((mult, f))
    }

    fn setup_spread_spectrum(&mut self, _pll: PLL, params: &SpreadParams) -> Result<u8, Error> {
        let ssc_en = 0x00;
        let ssc_mode = 0x80;
        let ss_nclk = 0x0;
        let ssudp = params.calc_ssudp();
        let (ssup_p1, ssup_p2, ssup_p3, ssdn_p1, ssdn_p2, ssdn_p3) = params.calc_center_spread();
        let reg0 = (ssc_en | (ssdn_p2 >> 8)) as u8;
        self.write_ssc_registers(
            [
                reg0,
                (ssdn_p2 & 0xff) as u8,
                (ssc_mode | (ssdn_p3 >> 8)) as u8,
                (ssdn_p3 & 0xff) as u8,
                (ssdn_p1 & 0xff) as u8,
                (((ssudp >> 4) & 0xf0) | ((ssdn_p1 >> 8) & 0x0f)) as u8,
                (ssudp & 0xff) as u8,
                (ssup_p2 >> 8) as u8,
                (ssup_p2 & 0xff) as u8,
                (ssup_p3 >> 8) as u8,
                (ssup_p3 & 0xff) as u8,
                (ssup_p1 & 0xff) as u8,
                (ss_nclk | ((ssup_p1 >> 8) & 0x0f)) as u8,
            ]
        )?;
        Ok(reg0)
    }

    fn clear_spread_spectrum(&mut self) -> Result<(), Error> {
        self.write_ssc_registers(
            [
                0u8,
                0u8,
                0u8,
                0u8,
                0u8,
                0u8,
                0u8,
                0u8,
                0u8,
                0u8,
                0u8,
                0u8,
                0u8,
            ]
        )
    }

    fn set_frequency(&mut self, pll: PLL, clk: ClockOutput, freq: u32, spread: Option<f32>) -> Result<(), Error> {

        let denom: u32 = 1048575;
        let (ms_divider, r_div) = self.find_int_dividers_for_max_pll_freq(900_000_000, freq)?;
        let total_div = u32::from(ms_divider) * u32::from(r_div.denominator_u8());
        let (mult, num) = self.find_pll_coeffs_for_dividers(total_div, denom, freq)?;

        let ms = match clk {
            ClockOutput::Clk0 => Multisynth::MS0,
            ClockOutput::Clk1 => Multisynth::MS1,
            ClockOutput::Clk2 => Multisynth::MS2,
            ClockOutput::Clk3 => Multisynth::MS3,
            ClockOutput::Clk4 => Multisynth::MS4,
            ClockOutput::Clk5 => Multisynth::MS5,
            _ => return Err(Error::InvalidParameter),
        };

        if let Some(spread_percent) = spread {
            let params = SpreadParams {
                f_pfd: self.xtal_freq as f32,
                a: f32::from(mult),
                b: num as f32,
                c: denom as f32,
                ssc_amp: spread_percent,
            };
            self.setup_spread_spectrum(pll, &params)?;
        }

        self.setup_pll(pll, mult, num, denom)?;
        self.setup_multisynth_int(ms, ms_divider, r_div)?;
        self.select_clock_pll(clk, pll);
        self.set_clock_enabled(clk, true);
        self.flush_clock_control(clk)?;
        self.reset_pll(pll)?;
        self.flush_output_enabled()?;

        Ok(())
    }

    fn set_frequencies(&mut self, pll: PLL, clks: &[ClockOutput], freqs: &[u32], spread: Option<f32>) -> Result<(), Error> {

        let denom0: u32 = 1048575;
        let (ms_divider, r_div) = self.find_int_dividers_for_max_pll_freq(900_000_000, freqs[0])?;
        let total_div = u32::from(ms_divider) * u32::from(r_div.denominator_u8());
        let (mult0, num0) = self.find_pll_coeffs_for_dividers(total_div, denom0, freqs[0])?;

        let reg0 = if let Some(spread_percent) = spread {
            let params = SpreadParams {
                f_pfd: self.xtal_freq as f32,
                a: f32::from(mult0),
                b: num0 as f32,
                c: denom0 as f32,
                ssc_amp: spread_percent,
            };
            self.setup_spread_spectrum(pll, &params)?
        } else {
            0
        };

        for (i, clk) in clks.iter().enumerate() {
            let ms = match clk {
                ClockOutput::Clk0 => Multisynth::MS0,
                ClockOutput::Clk1 => Multisynth::MS1,
                ClockOutput::Clk2 => Multisynth::MS2,
                ClockOutput::Clk3 => Multisynth::MS3,
                ClockOutput::Clk4 => Multisynth::MS4,
                ClockOutput::Clk5 => Multisynth::MS5,
                _ => return Err(Error::InvalidParameter),
            };
            if i == 0 {
                self.setup_multisynth_int(ms, ms_divider, r_div)?;
                self.setup_pll(pll, mult0, num0, denom0)?;
            } else {
                let pll_freq = (u64::from(self.xtal_freq) * u64::from(mult0)
                    + (u64::from(self.xtal_freq) * u64::from(num0)) / u64::from(denom0)) as f32;
                let factor = pll_freq / (freqs[i] as f32);
                let int_factor = factor as u16;
                let rem = ((factor - f32::from(int_factor)) * 1e4) as u32;
                #[cfg(test)]
                {
                    log::info!("pll_freq={pll_freq} factor={factor} int_factor={int_factor} rem={rem}");
                    log::info!("f_out={}", pll_freq / (f32::from(int_factor) + (rem as f32 / 1e4)));
                }
                self.setup_multisynth(ms, int_factor, rem, 10000, r_div)?;
            }
            self.select_clock_pll(*clk, pll);
            self.set_clock_enabled(*clk, true);
            self.flush_clock_control(*clk)?;
        }

        self.reset_pll(pll)?;

        // HACK: delay SSC_EN until AFTER PLL lock!
        self.i2c
            .write(
                self.address,
                &[
                    0x95,
                    reg0 | 0x80
                ],
            )
            .map_err(i2c_error)?;

        self.flush_output_enabled()?;

        Ok(())
    }

    fn set_clock_enabled(&mut self, clk: ClockOutput, enabled: bool) {
        let bit = 1u8 << clk.ix();
        if enabled {
            self.clk_enabled_mask |= bit;
        } else {
            self.clk_enabled_mask &= !bit;
        }
    }

    fn flush_output_enabled(&mut self) -> Result<(), Error> {
        let mask = self.clk_enabled_mask;
        self.write_register(Register::OutputEnable, !mask)
    }

    fn flush_clock_control(&mut self, clk: ClockOutput) -> Result<(), Error> {
        let bit = 1u8 << clk.ix();
        let clk_control_pdn = if self.clk_enabled_mask & bit != 0 {
            ClockControlBits::empty()
        } else {
            ClockControlBits::CLK_PDN
        };

        let ms_int_mode = if self.ms_int_mode_mask & bit == 0 {
            ClockControlBits::empty()
        } else {
            ClockControlBits::MS_INT
        };

        let ms_src = if self.ms_src_mask & bit == 0 {
            ClockControlBits::empty()
        } else {
            ClockControlBits::MS_SRC
        };

        let base = ClockControlBits::CLK_SRC_MS | ClockControlBits::CLK_DRV_2;

        self.write_register(
            clk.register(),
            (clk_control_pdn | ms_int_mode | ms_src | base).bits(),
        )
    }

    fn setup_pll_int(&mut self, pll: PLL, mult: u8) -> Result<(), Error> {
        self.setup_pll(pll, mult, 0, 1)
    }

    fn setup_pll(&mut self, pll: PLL, mult: u8, num: u32, denom: u32) -> Result<(), Error> {
        if mult < 15 || mult > 90 {
            return Err(Error::InvalidParameter);
        }

        self.write_ms_config(
            pll.multisynth(),
            mult.into(),
            num,
            denom,
            OutputDivider::Div1,
        )?;

        Ok(())
    }

    fn setup_multisynth_int(
        &mut self,
        ms: Multisynth,
        mult: u16,
        r_div: OutputDivider,
    ) -> Result<(), Error> {
        self.setup_multisynth(ms, mult, 0, 1, r_div)
    }

    fn setup_multisynth(
        &mut self,
        ms: Multisynth,
        div: u16,
        num: u32,
        denom: u32,
        r_div: OutputDivider,
    ) -> Result<(), Error> {
        if div < 6 || div > 1800 {
            return Err(Error::InvalidParameter);
        }

        self.write_ms_config(ms, div, num, denom, r_div)?;

        Ok(())
    }

    fn select_clock_pll(&mut self, clock: ClockOutput, pll: PLL) {
        let bit = 1u8 << clock.ix();
        match pll {
            PLL::A => self.ms_src_mask &= !bit,
            PLL::B => self.ms_src_mask |= bit,
        }
    }

    fn set_phase_offset(&mut self, clk: ClockOutput, offset: u8) -> Result<(), Error> {
        let reg = match clk {
            ClockOutput::Clk0 => Register::Clk0PhOff,
            ClockOutput::Clk1 => Register::Clk1PhOff,
            ClockOutput::Clk2 => Register::Clk2PhOff,
            ClockOutput::Clk3 => Register::Clk3PhOff,
            ClockOutput::Clk4 => Register::Clk4PhOff,
            ClockOutput::Clk5 => Register::Clk5PhOff,
            _ => return Err(Error::InvalidParameter),
        };
        if offset & 0b00000001 == 1 {
            return Err(Error::InvalidParameter);
        }
        self.write_register(reg, offset)?;
        Ok(())
    }

    fn set_current(&mut self, clk: ClockOutput, current: Current) -> Result<(), Error> {
        let reg = match clk {
            ClockOutput::Clk0 => Register::Clk0,
            ClockOutput::Clk1 => Register::Clk1,
            ClockOutput::Clk2 => Register::Clk2,
            ClockOutput::Clk3 => Register::Clk3,
            ClockOutput::Clk4 => Register::Clk4,
            ClockOutput::Clk5 => Register::Clk5,
            ClockOutput::Clk6 => Register::Clk6,
            ClockOutput::Clk7 => Register::Clk7,
        };

        self.write_register(reg, current as u8)?;

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tests::{setup_logger, MockI2c};

    #[test]
    fn test_frequency_calculations() {

        setup_logger();

        let mut si = Si5351Device::new(MockI2c, false, 25_000_000);

        let test_freqs = [
            1_000_000,
            12_288_000,
            74_250_000,
        ];

        let test_spread = 0.015;

        for freq in &test_freqs {
            log::info!("\n=== Target: {freq} Hz ===");

            let (ms_divider, r_div) = si.find_int_dividers_for_max_pll_freq(900_000_000, *freq)
                .expect("Failed to find dividers");
            let total_div = u32::from(ms_divider) * u32::from(r_div.denominator_u8());
            let denom = 1048575; // Maximum denominator
            let (mult, num) = si.find_pll_coeffs_for_dividers(total_div, denom, *freq)
                .expect("Failed to find PLL coefficients");

            let pll_freq = (u64::from(si.xtal_freq) * u64::from(mult) 
                + (u64::from(si.xtal_freq) * u64::from(num)) / u64::from(denom)) as f64;
            let output_freq = pll_freq / f64::from(total_div);
            let freq_error = output_freq - f64::from(*freq);
            let relative_error = 100.0 * freq_error.abs() / f64::from(*freq);

            // Calculate spread spectrum parameters
            let spread_params = SpreadParams {
                f_pfd: si.xtal_freq as f32,
                a: f32::from(mult),
                b: num as f32,
                c: denom as f32,
                ssc_amp: test_spread,
            };

            let ssudp = spread_params.calc_ssudp();
            let (ssup_p1, ssup_p2, ssup_p3, ssdn_p1, ssdn_p2, ssdn_p3) = 
                spread_params.calc_center_spread();

            log::info!("ms_divider = {ms_divider}");
            log::info!("r_div = 1/{}", r_div.denominator_u8());
            log::info!("total_div = {total_div}");
            log::info!("denom = {denom}");
            log::info!("mult = {mult}");
            log::info!("num = {num}");
            log::info!("pll_freq = {pll_freq}");
            log::info!("output_freq = {output_freq}");
            log::info!("freq_error = {freq_error}");
            log::info!("relative_error = {relative_error}%");
            log::info!("ssudp = {ssudp}");
            log::info!("ssup_p1 = {ssup_p1}");
            log::info!("ssup_p2 = {ssup_p2}");
            log::info!("ssup_p3 = {ssup_p3}");
            log::info!("ssdn_p1 = {ssdn_p1}");
            log::info!("ssdn_p2 = {ssdn_p2}");
            log::info!("ssdn_p3 = {ssdn_p3}");

            // Try to set the frequency and observe I2C operations
            si.set_frequency(PLL::A, ClockOutput::Clk0, *freq, Some(test_spread))
                .expect("Failed to set frequency");
        }
    }

    #[test]
    fn test_frequencies() {

        setup_logger();

        let mut si = Si5351Device::new(MockI2c, false, 25_000_000);

        si.set_frequencies(
            PLL::A,
            &[
                ClockOutput::Clk0,
                ClockOutput::Clk1
            ],
            &[
                12_288_000,
                74_250_000,
            ],
            Some(0.015))
            .expect("Failed to set frequency");
    }
}
