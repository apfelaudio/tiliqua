#[macro_export]
macro_rules! impl_ui {
    ($(
        $UI: ident, $OPTIONS:ty,
        $ENCODER:ty, $PCA9635:ty, $PMOD: ty
    )+) => {
        $(
            pub struct $UI {
                pub opts: $OPTIONS,
                encoder: $ENCODER,
                pca9635: $PCA9635,
                pub pmod: $PMOD,
                uptime_ms: u32,
                time_since_encoder_touched: u32,
                time_since_midi_activity: u32,
                toggle_leds: bool,
                period_ms: u32,
                encoder_fade_ms: u32,
                touch_led_mask: u8,
            }

            impl $UI {
                pub fn new(opts: $OPTIONS, period_ms: u32, encoder: $ENCODER,
                           pca9635: $PCA9635, pmod: $PMOD) -> Self {
                    Self {
                        opts,
                        encoder,
                        pca9635,
                        pmod,
                        uptime_ms: 0u32,
                        time_since_encoder_touched: 0u32,
                        time_since_midi_activity: 0u32,
                        toggle_leds: false,
                        period_ms,
                        encoder_fade_ms: 1000u32,
                        touch_led_mask: 0u8,
                    }
                }

                pub fn midi_activity(&mut self) {
                    self.time_since_midi_activity = 0;
                }

                pub fn touch_led_mask(&mut self, mask: u8) {
                    self.touch_led_mask = mask;
                }

                pub fn update(&mut self) {
                    use tiliqua_lib::opt::*;

                    //
                    // Consume encoder, update options
                    //

                    self.encoder.update();

                    self.time_since_encoder_touched += self.period_ms;
                    self.time_since_midi_activity += self.period_ms;
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

                    if self.time_since_midi_activity < 100 {
                        leds::mobo_pca9635_set_midi(&mut self.pca9635.leds, 0xff, 0xff);
                    } else {
                        leds::mobo_pca9635_set_midi(&mut self.pca9635.leds, 0x0, 0x0);
                    }

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
                            let touch = self.pmod.touch();
                            for n in 0..8 {
                                if (self.touch_led_mask & (1<<n)) != 0 {
                                    self.pmod.led_set_manual(n,(touch[n]>>2) as i8);
                                }
                            }
                        }
                    }

                    self.pca9635.push().ok();

                    self.opts.draw = self.time_since_encoder_touched < self.encoder_fade_ms ||
                                     self.opts.modify();
                }
            }
        )+
    }
}
