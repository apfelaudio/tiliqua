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
                        self.registers.voice0_note().read().bits() as u8,
                        self.registers.voice1_note().read().bits() as u8,
                        self.registers.voice2_note().read().bits() as u8,
                        self.registers.voice3_note().read().bits() as u8,
                        self.registers.voice4_note().read().bits() as u8,
                        self.registers.voice5_note().read().bits() as u8,
                        self.registers.voice6_note().read().bits() as u8,
                        self.registers.voice7_note().read().bits() as u8,
                    ]
                }

                pub fn voice_cutoffs(&self) -> [u8; 8] {
                    [
                        self.registers.voice0_cutoff().read().bits() as u8,
                        self.registers.voice1_cutoff().read().bits() as u8,
                        self.registers.voice2_cutoff().read().bits() as u8,
                        self.registers.voice3_cutoff().read().bits() as u8,
                        self.registers.voice4_cutoff().read().bits() as u8,
                        self.registers.voice5_cutoff().read().bits() as u8,
                        self.registers.voice6_cutoff().read().bits() as u8,
                        self.registers.voice7_cutoff().read().bits() as u8,
                    ]
                }

                pub fn set_drive(&mut self, value: u16)  {
                    self.registers.drive().write(|w| unsafe { w.drive().bits(value) } );
                }

                pub fn set_reso(&mut self, value: u16)  {
                    self.registers.reso().write(|w| unsafe { w.reso().bits(value) } );
                }
            }
        )+
    };
}
