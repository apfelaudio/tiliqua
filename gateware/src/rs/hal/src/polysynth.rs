#[macro_export]
macro_rules! impl_polysynth {
    ($(
        $POLYSYNTHX:ident: $PACPOLYSYNTHX:ty,
    )+) => {
        $(
            #[derive(Debug)]
            pub struct $POLYSYNTHX {
                registers: $PACPOLYSYNTHX,
            }

            impl $POLYSYNTHX {
                pub fn new(registers: $PACPOLYSYNTHX) -> Self {
                    Self { registers }
                }
            }

            impl $POLYSYNTHX {

                pub fn voice_notes(&self) -> [u8; 8] {
                    [
                        self.registers.voice_note0().read().bits() as u8,
                        self.registers.voice_note1().read().bits() as u8,
                        self.registers.voice_note2().read().bits() as u8,
                        self.registers.voice_note3().read().bits() as u8,
                        self.registers.voice_note4().read().bits() as u8,
                        self.registers.voice_note5().read().bits() as u8,
                        self.registers.voice_note6().read().bits() as u8,
                        self.registers.voice_note7().read().bits() as u8,
                    ]
                }

                pub fn voice_cutoffs(&self) -> [u8; 8] {
                    [
                        self.registers.voice_cutoff0().read().bits() as u8,
                        self.registers.voice_cutoff1().read().bits() as u8,
                        self.registers.voice_cutoff2().read().bits() as u8,
                        self.registers.voice_cutoff3().read().bits() as u8,
                        self.registers.voice_cutoff4().read().bits() as u8,
                        self.registers.voice_cutoff5().read().bits() as u8,
                        self.registers.voice_cutoff6().read().bits() as u8,
                        self.registers.voice_cutoff7().read().bits() as u8,
                    ]
                }

                pub fn set_matrix_coefficient(&mut self, x_o: u32, y_i: u32, value: i32)  {
                    // TODO: verify x_o, y_i both < 16. Should be true for any normal use case
                    // as matrices larger than this won't be able to process things at audio rate.
                    let reg: u32 = ((x_o & 0xF) << 28) | ((y_i & 0xF) << 24) | ((value as u32) & 0x00FFFFFF);
                    while self.registers.matrix_busy().read().bits() == 1 { /* wait until last coeff written */ }
                    self.registers.matrix().write(|w| unsafe { w.matrix().bits(reg) } );
                }

                pub fn set_drive(&mut self, value: u16)  {
                    self.registers.drive().write(|w| unsafe { w.value().bits(value) } );
                }

                pub fn set_reso(&mut self, value: u16)  {
                    self.registers.reso().write(|w| unsafe { w.value().bits(value) } );
                }

                pub fn set_touch_control(&mut self, value: bool)  {
                    self.registers.touch_control().write(
                        |w| unsafe { w.value().bit(value) } );
                }
            }
        )+
    };
}
