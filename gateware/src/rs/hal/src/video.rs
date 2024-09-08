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
                pub fn set_palette_rgb(&mut self, intensity: u8, hue: u8, r: u8, g: u8, b: u8)  {
                    /* wait until last coefficient written */ 
                    while self.registers.palette_busy().read().bits() == 1 { }
                    self.registers.palette().write(|w| unsafe {
                        w.position().bits(((intensity&0xF) << 4) | (hue&0xF));
                        w.red()     .bits(r);
                        w.green()   .bits(g);
                        w.blue()    .bits(b)
                    } );
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
