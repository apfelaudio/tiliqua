#[macro_export]
macro_rules! impl_encoder {
    ($(
        $ENCODERX:ident: $PACENCODERX:ty,
    )+) => {
        $(
            #[derive(Debug)]
            pub struct $ENCODERX {
                registers: $PACENCODERX,

                rot: i16,
                lrot: i16,
                lbtn: bool,

                pending_ticks: i8,
                pending_press: bool,
            }

            impl $ENCODERX {
                pub fn new(registers: $PACENCODERX) -> Self {
                    Self { registers,
                           rot: 0,
                           lrot: 0,
                           lbtn: false,
                           pending_ticks: 0,
                           pending_press: false
                    }
                }

                /// Check for pending ticks and clear them.
                pub fn poke_ticks(&mut self) -> i8 {
                    let ticks = self.pending_ticks;
                    self.pending_ticks = 0;
                    ticks
                }

                /// Check for pending presses and erase it.
                pub fn poke_btn(&mut self) -> bool {
                    let btn = self.pending_press;
                    self.pending_press = false;
                    btn
                }

                pub fn update(&mut self) {

                    self.rot += (self.registers.step().read().bits() as i8) as i16;
                    let btn = self.registers.button().read().bits() != 0;
                    let mut delta_ticks = self.rot - self.lrot;

                    // This logic is dumb. Move it into RTL.

                    while delta_ticks > 1 {
                        self.pending_ticks += 1;
                        delta_ticks -= 2;
                    }

                    while delta_ticks < -1 {
                        self.pending_ticks -= 1;
                        delta_ticks += 2;
                    }

                    // button just released
                    if self.lbtn != btn && !btn {
                        self.pending_press = true;
                    }

                    self.lrot = self.rot - delta_ticks;
                    self.lbtn = btn;
                }
            }

            impl From<$PACENCODERX> for $ENCODERX {
                fn from(registers: $PACENCODERX) -> $ENCODERX {
                    $ENCODERX::new(registers)
                }
            }
        )+
    }
}