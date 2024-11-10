use crate::*;

use tiliqua_lib::*;
use tiliqua_lib::opt::*;

use tiliqua_pac as pac;
use tiliqua_hal as hal;

use hal::pca9635::*;


pub struct OptInterface {
    pub opts: opts::Options,
    encoder: Encoder0,
    pca9635: Pca9635Driver<I2c0>,
    pmod: EurorackPmod0,
    uptime_ms: u32,
    time_since_encoder_touched: u32,
    toggle_leds: bool,
    period_ms: u32,
    encoder_fade_ms: u32,
}

impl OptInterface {
    pub fn new(period_ms: u32) -> Self {
        let peripherals = unsafe { pac::Peripherals::steal() };
        let encoder = Encoder0::new(peripherals.ENCODER0);
        let i2cdev = I2c0::new(peripherals.I2C0);
        let pca9635 = Pca9635Driver::new(i2cdev);
        let pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
        Self {
            opts: opts::Options::new(),
            encoder,
            pca9635,
            pmod,
            uptime_ms: 0u32,
            time_since_encoder_touched: 0u32,
            toggle_leds: false,
            period_ms,
            encoder_fade_ms: 1000u32,
        }
    }

    pub fn update(&mut self) {

        //
        // Consume encoder, update options
        //

        self.encoder.update();

        self.time_since_encoder_touched += self.period_ms;
        self.uptime_ms += self.period_ms;

        let ticks = self.encoder.poke_ticks();
        if ticks != 0 {
            self.opts.consume_ticks(ticks);
            self.time_since_encoder_touched = 0;
        }
        if self.encoder.poke_btn() {
            self.opts.toggle_modify();
            self.time_since_encoder_touched = 0;
        }

        //
        // Update LEDs
        //

        if self.uptime_ms % (10*self.period_ms) == 0 {
            self.toggle_leds = !self.toggle_leds;
        }


        for n in 0..16 {
            self.pca9635.leds[n] = 0u8;
        }

        leds::mobo_pca9635_set_bargraph(&self.opts, &mut self.pca9635.leds,
                                        self.toggle_leds);

        if self.opts.modify() {
            // Flashing if we're modifying something
            if self.toggle_leds {
                if let Some(n) = self.opts.view().selected() {
                    // red for option selection
                    if n < 8 {
                        self.pmod.led_set_manual(n, i8::MAX);
                    }
                } else {
                    // green for screen selection
                    let n = (self.opts.screen.percent() * (self.opts.screen.n_unique_values() as f32)) as usize;
                    if n < 8 {
                        self.pmod.led_set_manual(n, i8::MIN);
                    }
                }
            } else {
                self.pmod.led_all_auto();
            }
        } else {
            // Not flashing with fade-out if we stopped modifying something
            if self.time_since_encoder_touched < self.encoder_fade_ms {
                for n in 0..8 {
                    self.pmod.led_set_manual(n, 0i8);
                }
                let fade: i8 = (((self.encoder_fade_ms-self.time_since_encoder_touched) * 120) /
                                 self.encoder_fade_ms) as i8;
                if let Some(n) = self.opts.view().selected() {
                    // red for option selection
                    if n < 8 {
                        self.pmod.led_set_manual(n, fade);
                    }
                } else {
                    // green for screen selection
                    self.pmod.led_set_manual(0, -fade);
                }
            } else {
                self.pmod.led_all_auto();
            }
        }

        self.pca9635.push().ok();

        self.opts.draw = self.time_since_encoder_touched < self.encoder_fade_ms || self.opts.modify();
    }
}
