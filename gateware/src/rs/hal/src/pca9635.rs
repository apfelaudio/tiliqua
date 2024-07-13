use embedded_hal::i2c::I2c;
use embedded_hal::i2c::Operation;

const PCA9635_ADDR: u8 = 0x05;

pub struct Pca9635Driver<I2C> {
    i2c: I2C,
    pub leds: [u8; 16],
}

impl<I2C: I2c> Pca9635Driver<I2C> {

    pub fn new(i2c: I2C) -> Self {
        Self { i2c, leds: [0u8; 16] }
    }

    pub fn push(&mut self) -> Result<(), I2C::Error> {
        let pca9635_bytes = [
           0x80u8, // Auto-increment starting from MODE1
           0x81u8, // MODE1
           0x01u8, // MODE2
           self.leds[0x0], // PWM0
           self.leds[0x1], // PWM1
           self.leds[0x2], // PWM2
           self.leds[0x3], // PWM3
           self.leds[0x4], // PWM4
           self.leds[0x5], // PWM5
           self.leds[0x6], // PWM6
           self.leds[0x7], // PWM7
           self.leds[0x8], // PWM8
           self.leds[0x9], // PWM9
           self.leds[0xA], // PWM10
           self.leds[0xB], // PWM11
           self.leds[0xC], // PWM12
           self.leds[0xD], // PWM13
           self.leds[0xE], // PWM14
           self.leds[0xF], // PWM15
           0xFFu8, // GRPPWM
           0x00u8, // GRPFREQ
           0xAAu8, // LEDOUT0
           0xAAu8, // LEDOUT1
           0xAAu8, // LEDOUT2
           0xAAu8, // LEDOUT3
        ];
        self.i2c.transaction(PCA9635_ADDR, &mut [Operation::Write(&pca9635_bytes)])
    }
}
