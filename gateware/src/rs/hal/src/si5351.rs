/*
   Copyright 2018 Ilya Epifanov
   Copyright 2024 Seb Holzapfel

   Licensed under the Apache License, Version 2.0, <LICENSE-APACHE or
   http://apache.org/licenses/LICENSE-2.0> or the MIT license <LICENSE-MIT or
   http://opensource.org/licenses/MIT>, at your option. This file may not be
   copied, modified, or distributed except according to those terms.
*/
/*!
A platform agnostic Rust driver for the [Si5351], based on the
[`embedded-hal`] traits.

## The Device

The Silicon Labs [Si5351] is an any-frequency CMOS clock generator.

The device has an I²C interface.

## Usage

Import this crate and an `embedded_hal` implementation:

```
extern crate stm32f103xx_hal as hal;
extern crate si5351;
```

Initialize I²C bus (differs between `embedded_hal` implementations):

```no_run
# extern crate stm32f103xx_hal as hal;
use hal::i2c::I2c;
type I2C = ...;

# fn main() {
let i2c: I2C = initialize_i2c();
# }
```

Then instantiate the device:

```no_run
# extern crate stm32f103xx_hal as hal;
# extern crate si5351;
use si5351;
use si5351::{Si5351, Si5351Device};

# fn main() {
let mut clock = Si5351Device<I2C>::new(i2c, false, 25_000_000);
clock.init(si5351::CrystalLoad::_10)?;
# }
```

Or, if you have an [Adafruit module], you can use shortcut functions to initializate it:
```no_run
# extern crate stm32f103xx_hal as hal;
# extern crate si5351;
use si5351;
use si5351::{Si5351, Si5351Device};

# fn main() {
let mut clock = Si5351Device<I2C>::new_adafruit_module(i2c);
clock.init_adafruit_module()?;
# }
```

And set frequency on one of the outputs:

```no_run
use si5351;

clock.set_frequency(si5351::PLL::A, si5351::ClockOutput::Clk0, 14_175_000)?;
```

[Si5351]: https://www.silabs.com/documents/public/data-sheets/Si5351-B.pdf
[`embedded-hal`]: https://github.com/japaric/embedded-hal
[Adafruit module]: https://www.adafruit.com/product/2045
*/

use embedded_hal::i2c::I2c;
use core::fmt;

#[derive(Debug)]
pub enum Error {
    CommunicationError,
    InvalidParameter,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self {
            Error::CommunicationError => write!(f, "Communication Error"),
            Error::InvalidParameter => write!(f, "Invalid Parameter")
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
            PLL::A => FeedbackMultisynth::MSNA,
            PLL::B => FeedbackMultisynth::MSNB,
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
            FeedbackMultisynth::MSNA => 26,
            FeedbackMultisynth::MSNB => 34,
        }
    }
    fn ix(&self) -> u8 {
        match *self {
            FeedbackMultisynth::MSNA => 6,
            FeedbackMultisynth::MSNB => 7,
        }
    }
}

impl FractionalMultisynth for Multisynth {
    fn base_addr(&self) -> u8 {
        match *self {
            Multisynth::MS0 => 42,
            Multisynth::MS1 => 50,
            Multisynth::MS2 => 58,
            Multisynth::MS3 => 66,
            Multisynth::MS4 => 74,
            Multisynth::MS5 => 82,
        }
    }
    fn ix(&self) -> u8 {
        match *self {
            Multisynth::MS0 => 0,
            Multisynth::MS1 => 1,
            Multisynth::MS2 => 2,
            Multisynth::MS3 => 3,
            Multisynth::MS4 => 4,
            Multisynth::MS5 => 5,
        }
    }
}

impl SimpleMultisynth {
    pub fn base_addr(&self) -> u8 {
        match *self {
            SimpleMultisynth::MS6 => 90,
            SimpleMultisynth::MS7 => 91,
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
    pub fn addr(&self) -> u8 {
        *self as u8
    }
}

bitflags! {
    #[derive(Debug)]
    pub struct DeviceStatusBits: u8 {
        const SYS_INIT = 0b1000_0000;
        const LOL_B = 0b0100_0000;
        const LOL_A = 0b0010_0000;
        const LOS = 0b0001_0000;
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
            ClockOutput::Clk0 => Register::Clk0,
            ClockOutput::Clk1 => Register::Clk1,
            ClockOutput::Clk2 => Register::Clk2,
            ClockOutput::Clk3 => Register::Clk3,
            ClockOutput::Clk4 => Register::Clk4,
            ClockOutput::Clk5 => Register::Clk5,
            ClockOutput::Clk6 => Register::Clk6,
            ClockOutput::Clk7 => Register::Clk7,
        }
    }

    fn ix(&self) -> u8 {
        *self as u8
    }
}

impl OutputDivider {
    fn bits(&self) -> u8 {
        *self as u8
    }

    fn min_divider(desired_divider: u16) -> Result<OutputDivider, Error> {
        match 16 - (desired_divider.max(1) - 1).leading_zeros() {
            0 => Ok(OutputDivider::Div1),
            1 => Ok(OutputDivider::Div2),
            2 => Ok(OutputDivider::Div4),
            3 => Ok(OutputDivider::Div8),
            4 => Ok(OutputDivider::Div16),
            5 => Ok(OutputDivider::Div32),
            6 => Ok(OutputDivider::Div64),
            7 => Ok(OutputDivider::Div128),
            _ => Err(Error::InvalidParameter),
        }
    }

    fn denominator_u8(&self) -> u8 {
        match *self {
            OutputDivider::Div1 => 1,
            OutputDivider::Div2 => 2,
            OutputDivider::Div4 => 4,
            OutputDivider::Div8 => 8,
            OutputDivider::Div16 => 16,
            OutputDivider::Div32 => 32,
            OutputDivider::Div64 => 64,
            OutputDivider::Div128 => 128,
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

    fn set_frequency(&mut self, pll: PLL, clk: ClockOutput, freq: u32) -> Result<(), Error>;
    fn set_clock_enabled(&mut self, clk: ClockOutput, enabled: bool);

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
        let si5351 = Si5351Device {
            i2c,
            address: ADDRESS | if address_bit { 1 } else { 0 },
            xtal_freq,
            clk_enabled_mask: 0,
            ms_int_mode_mask: 0,
            ms_src_mask: 0,
        };

        si5351
    }

    pub fn new_adafruit_module(i2c: I2C) -> Self {
        Si5351Device::new(i2c, false, 25_000_000)
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

        let p1: u32;
        let p2: u32;
        let p3: u32;

        if frac_num == 0 {
            p1 = 128 * int as u32 - 512;
            p2 = 0;
            p3 = 1;
        } else {
            let ratio = (128u64 * (frac_num as u64) / (frac_denom as u64)) as u32;

            p1 = 128 * int as u32 + ratio - 512;
            p2 = 128 * frac_num - frac_denom * ratio;
            p3 = frac_denom;
        }

        self.write_synth_registers(
            ms,
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

    fn write_synth_registers<MS: FractionalMultisynth>(
        &mut self,
        ms: MS,
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

        self.flush_output_enabled()?;
        const CLK_REGS: [Register; 8] = [
            Register::Clk0,
            Register::Clk1,
            Register::Clk2,
            Register::Clk3,
            Register::Clk4,
            Register::Clk5,
            Register::Clk6,
            Register::Clk7,
        ];
        for &reg in CLK_REGS.iter() {
            self.write_register(reg, ClockControlBits::CLK_PDN.bits())?;
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

        let ms_div = (total_divider / (2 * r_div.denominator_u8() as u16) * 2).max(6);
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
        let f = ((pll_freq % self.xtal_freq) as u64 * denom as u64 / self.xtal_freq as u64) as u32;

        Ok((mult, f))
    }

    fn set_frequency(&mut self, pll: PLL, clk: ClockOutput, freq: u32) -> Result<(), Error> {
        let denom: u32 = 1048575;

        let (ms_divider, r_div) = self.find_int_dividers_for_max_pll_freq(900_000_000, freq)?;
        let total_div = ms_divider as u32 * r_div.denominator_u8() as u32;
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

        self.setup_pll(pll, mult, num, denom)?;
        self.setup_multisynth_int(ms, ms_divider, r_div)?;
        self.select_clock_pll(clk, pll);
        self.set_clock_enabled(clk, true);
        self.flush_clock_control(clk)?;
        self.reset_pll(pll)?;
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

        let base = ClockControlBits::CLK_SRC_MS | ClockControlBits::CLK_DRV_8;

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

        if mult % 2 == 0 && num == 0 {
        } else {
        }

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
