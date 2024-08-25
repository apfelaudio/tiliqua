#[macro_export]
macro_rules! impl_video {
    ($(
        $VIDEOX:ident: $PACVIDEOX:ty,
    )+) => {
        $(
            #[derive(Debug)]
            pub struct $VIDEOX {
                registers: $PACVIDEOX,
            }

            impl $VIDEOX {
                pub fn new(registers: $PACVIDEOX) -> Self {
                    Self { registers }
                }
            }

            impl $VIDEOX {
                pub fn set_palette_rgb(&mut self, intensity: u32, hue: u32, r: u8, g: u8, b: u8)  {
                    let reg: u32 = ((intensity & 0xF) << 28) | ((hue & 0xF) << 24) | ((r as u32) << 16) | ((g as u32) << 8) | b as u32;
                    while self.registers.palette_busy().read().bits() == 1 { /* wait until last coeff written */ }
                    self.registers.palette().write(|w| unsafe { w.palette().bits(reg) } );
                }

                pub fn set_persist(&mut self, value: u16)  {
                    self.registers.persist().write(|w| unsafe { w.persist().bits(value) } );
                }

                pub fn set_decay(&mut self, value: u8)  {
                    self.registers.decay().write(|w| unsafe { w.decay().bits(value) } );
                }
            }
        )+
    };
}
