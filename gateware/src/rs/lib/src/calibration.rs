use log::info;
use embedded_hal::i2c::I2c;
use tiliqua_hal::pmod::EurorackPmod;
use crate::eeprominfo::{EepromCalibration, EepromManager};

use heapless::String;
use core::fmt::Write;

#[derive(Debug, PartialEq)]
pub struct DefaultCalibrationConstants {
    pub adc_scale: f32,
    pub adc_zero:  f32,
    pub dac_scale: f32,
    pub dac_zero:  f32,
    pub fractional_bits: u8,
}

#[derive(Debug, PartialEq, Eq)]
pub struct CalibrationConstants {
    pub cal: EepromCalibration,
}

// These are the calibration constants with a transformation
// applied to make them easier to tweak both in the UI and by
// auto calibration. Both inputs and outputs have the form
// Ax+B, however on the ADC side, this means that scale tweaking
// performed after zero tweaking invalidates previous zero
// tweaking. A linear transformation is performed on all calibration
// constants to achieve the following:
//
//  - Default settings are centered at zero (i.e. zero for all numbers
//    in this struct represents a 'default calibration')
//  - ADC Ax+B is transformed so that we are tweaking 'gamma' and 'delta',
//    where gamma is 1/A and delta is B/A, such that changing gamma (new
//    scale mapping) does not invalidate a previous ADC zeroing operation.
//
// These numbers are what is shown in the tweakable ADC/DAC calibration screen
// as they are much easier to tweak by hand compared to the raw cal constants.
#[derive(Debug)]
pub struct TweakableConstants {
    pub adc_scale: [i16; 4],
    pub adc_zero:  [i16; 4],
    pub dac_scale: [i16; 4],
    pub dac_zero:  [i16; 4],
}

impl DefaultCalibrationConstants {
    pub fn from_array(c: &[f32; 4], fractional_bits: u8) -> Self {
        Self {
            adc_scale: c[0],
            adc_zero:  c[1],
            dac_scale: c[2],
            dac_zero:  c[3],
            fractional_bits
        }
    }
}

impl CalibrationConstants {
    fn fixed_to_f32(&self, x: i32) -> f32 {
        let divisor = (1 << self.cal.fractional_bits) as f32;
        (x as f32) / divisor
    }

    fn f32_to_fixed(&self, x: f32) -> i32 {
        let multiplier = (1 << self.cal.fractional_bits) as f32;
        (x * multiplier) as i32
    }

    pub fn from_defaults(d: &DefaultCalibrationConstants) -> Self {
        let mut result = Self {
            cal: EepromCalibration {
                adc_scale: [0; 4],
                adc_zero:  [0; 4],
                dac_scale: [0; 4],
                dac_zero:  [0; 4],
                fractional_bits: d.fractional_bits,
            },
        };
        for i in 0..4 {
            result.cal.adc_scale[i] = result.f32_to_fixed(d.adc_scale);
            result.cal.adc_zero[i]  = result.f32_to_fixed(d.adc_zero);
            result.cal.dac_scale[i] = result.f32_to_fixed(d.dac_scale);
            result.cal.dac_zero[i]  = result.f32_to_fixed(d.dac_zero);
        }
        result
    }

    pub fn write_to_pmod<Pmod>(&self, pmod: &mut Pmod)
    where
        Pmod: EurorackPmod
    {
        let hw_f_bits = pmod.f_bits() as i8;
        let cal_f_bits = self.cal.fractional_bits as i8;
        let shift = hw_f_bits - cal_f_bits;
        if shift != 0 {
            info!("audio/calibration: calibration (f_bits={cal_f_bits}) != hardware (f_bits={hw_f_bits}), shift={shift}");
        }
        let rescale = |v: i32| -> i32 {
            if shift > 0 { v << shift } else { v >> (-shift) }
        };

        for ch in 0..4usize {
            pmod.write_calibration_constant(
                ch as u8,
                rescale(self.cal.adc_scale[ch]),
                rescale(self.cal.adc_zero[ch]),
            );
            pmod.write_calibration_constant(
                (ch+4) as u8,
                rescale(self.cal.dac_scale[ch]),
                rescale(self.cal.dac_zero[ch]),
            );
        }
    }

    pub fn from_eeprom<EepromI2c>(i2cdev: EepromI2c) -> Option<Self>
    where
        EepromI2c: I2c
    {
        let mut eeprom_manager = EepromManager::new(i2cdev);
        match eeprom_manager.read_calibration() {
            Ok(cal) => Some(Self { cal }),
            Err(_) => None,
        }
    }

    pub fn load_or_default<EepromI2c, Pmod>(i2cdev: EepromI2c, pmod: &mut Pmod)
    where
        EepromI2c: I2c,
        Pmod: EurorackPmod
    {
        if let Some(cal_constants) = Self::from_eeprom(i2cdev) {
            info!("audio/calibration: looks good! switch to it.");
            cal_constants.write_to_pmod(pmod);
        } else {
            info!("audio/calibration: invalid! using default.");
            // Defaults assumed already programmed in by gateware.
        }
    }

    pub fn write_to_eeprom<EepromI2c>(&self, i2cdev: EepromI2c)
    where
        EepromI2c: I2c
    {
        // Print the calibration constants in amaranth-friendly format.
        let mut s: String<256> = String::new();
        write!(s, "[\n\r").ok();
        for ch in 0..4 {
            write!(s, "  [{:.4}, {:.4}],\n\r",
                   self.fixed_to_f32(self.cal.adc_scale[ch as usize]),
                   self.fixed_to_f32(self.cal.adc_zero[ch as usize])).ok();
        }
        for ch in 0..4 {
            write!(s, "  [{:.4}, {:.4}],\n\r",
                   self.fixed_to_f32(self.cal.dac_scale[ch as usize]),
                   self.fixed_to_f32(self.cal.dac_zero[ch as usize])).ok();
        }
        write!(s, "]\n\r").ok();
        info!("[write to eeprom] cal_constants = {s}");
        // Commit to eeprom using EepromManager
        let mut eeprom_manager = EepromManager::new(i2cdev);
        match eeprom_manager.write_calibration(&self.cal) {
            Ok(()) => info!("[write to eeprom] complete"),
            Err(_) => info!("[write to eeprom] failed"),
        }
    }

    // See comment on 'TweakableConstants' for the purpose of this.
    fn adc_default_gamma_delta(d: &DefaultCalibrationConstants) -> (f32, f32) {
        let adc_gamma_default  = 1.0f32/d.adc_scale;
        let adc_delta_default  = -d.adc_zero*adc_gamma_default;
        (adc_gamma_default, adc_delta_default)
    }

    pub fn from_tweakable(c: TweakableConstants, d: &DefaultCalibrationConstants) -> Self {
        let mut result = Self::from_defaults(d);
        // DAC
        for ch in 0..4usize {
            result.cal.dac_scale[ch] = result.f32_to_fixed(d.dac_scale) + 4*c.dac_scale[ch] as i32;
            result.cal.dac_zero[ch]  = result.f32_to_fixed(d.dac_zero)  + 2*c.dac_zero[ch] as i32; // FIXME 2x/4x
        }
        // ADC
        let (adc_gd, adc_dd) = Self::adc_default_gamma_delta(d);
        for ch in 0..4usize {
            let adc_gamma      = adc_gd + 0.00010*(c.adc_scale[ch] as f32);
            let adc_delta      = adc_dd + 0.00005*(c.adc_zero[ch] as f32);
            result.cal.adc_scale[ch] = result.f32_to_fixed(1.0f32/adc_gamma);
            result.cal.adc_zero[ch]  = result.f32_to_fixed(-adc_delta/adc_gamma);
        }
        result
    }

    pub fn to_tweakable(&self, d: &DefaultCalibrationConstants) -> TweakableConstants {
        let mut adc_scale = [0i16; 4];
        let mut adc_zero  = [0i16; 4];
        let mut dac_scale = [0i16; 4];
        let mut dac_zero  = [0i16; 4];
        let (adc_gd, adc_dd) = Self::adc_default_gamma_delta(d);
        for ch in 0..4usize {
            let adc_gamma = 1.0f32/self.fixed_to_f32(self.cal.adc_scale[ch]);
            adc_scale[ch] = ((adc_gamma - adc_gd) / 0.00010) as i16;
            let adc_delta = -self.fixed_to_f32(self.cal.adc_zero[ch])*adc_gamma;
            adc_zero[ch]  = ((adc_delta - adc_dd) / 0.00005) as i16;
            dac_scale[ch] = ((self.cal.dac_scale[ch] - self.f32_to_fixed(d.dac_scale)) / 4) as i16;
            dac_zero[ch]  = ((self.cal.dac_zero[ch]  - self.f32_to_fixed(d.dac_zero)) / 2) as i16;
        }
        TweakableConstants {
            adc_scale,
            adc_zero,
            dac_scale,
            dac_zero,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    pub fn tweakable_conversion() {
        // Verify TweakableConstants transformation reverses correctly.
        let defaults_r33 = DefaultCalibrationConstants {
            adc_scale: -1.158,
            adc_zero:  0.008,
            dac_scale: 0.97,
            dac_zero:  0.03,
            fractional_bits: 15,
        };
        let mut test = CalibrationConstants::from_defaults(&defaults_r33);
        test.cal.adc_scale[0] += 500;
        test.cal.adc_zero[0]  += 250;
        test.cal.dac_scale[0] -= 100;
        test.cal.dac_zero[0]  += 50;
        let twk = test.to_tweakable(&defaults_r33);
        let converted = CalibrationConstants::from_tweakable(twk, &defaults_r33);
        let tol = |x: i32, y: i32, t: i32| (x-y).abs() <= t;
        for ch in 0..4 {
            assert!(tol(test.cal.adc_scale[ch], converted.cal.adc_scale[ch], 1));
            assert!(tol(test.cal.adc_zero[ch], converted.cal.adc_zero[ch], 1));
            assert!(tol(test.cal.dac_scale[ch], converted.cal.dac_scale[ch], 1));
            assert!(tol(test.cal.dac_zero[ch], converted.cal.dac_zero[ch], 1));
        }
    }
}
