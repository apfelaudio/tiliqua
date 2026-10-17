use serde_derive::{Serialize, Deserialize};
use strum_macros::{EnumIter, IntoStaticStr};

#[derive(Default, Debug, PartialEq, Eq, PartialOrd, Clone, Copy, Serialize, Deserialize, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "kebab-case")]
pub enum Rotate {
    #[default]
    Normal = 0,
    Left = 1,
    Inverted = 2,
    Right = 3,
}

#[derive(Debug, Clone, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct DVIModeline {
   pub h_active:      u16,
   pub h_sync_start:  u16,
   pub h_sync_end:    u16,
   pub h_total:       u16,
   pub h_sync_invert: bool,
   pub v_active:      u16,
   pub v_sync_start:  u16,
   pub v_sync_end:    u16,
   pub v_total:       u16,
   pub v_sync_invert: bool,
   pub pixel_clk_mhz: f32,
   pub rotate:        Rotate,
}

impl DVIModeline {
    pub fn refresh_rate(&self) -> f32 {
        1e6f32 * self.pixel_clk_mhz / (u32::from(self.h_total) * u32::from(self.v_total)) as f32
    }

    pub fn fixed(&self) -> bool {
        self.v_total == 0
    }

    #[must_use]
    pub fn maybe_override_fixed(self, fixed: Option<(u16, u16)>, fixed_pclk_hz: u32) -> Self {
        if let Some((h_active, v_active)) = fixed {
            let rotate = match (h_active, v_active) {
                (720, 720) => Rotate::Left, // ... XXX hack for round screen :)
                _ => Rotate::Normal
            };
            Self {
                h_active,
                h_sync_start  : 0,
                h_sync_end    : 0,
                h_total       : 0,
                h_sync_invert : false,
                v_active,
                v_sync_start  : 0,
                v_sync_end    : 0,
                v_total       : 0,
                v_sync_invert : false,
                pixel_clk_mhz : (fixed_pclk_hz as f32) / 1e6f32,
                rotate
            }
        } else {
            self
        }
    }
}

impl Default for DVIModeline {
    fn default() -> Self {
        Self {
            h_active      : 1280,
            h_sync_start  : 1390,
            h_sync_end    : 1430,
            h_total       : 1650,
            h_sync_invert : false,
            v_active      : 720,
            v_sync_start  : 725,
            v_sync_end    : 730,
            v_total       : 750,
            v_sync_invert : false,
            pixel_clk_mhz : 74.25,
            rotate        : Rotate::Normal,
        }
    }
}

pub trait DMAFramebuffer {
    fn update_fb_base(&mut self, fb_base: u32);
    fn set_palette_rgb(&mut self, intensity: u8, hue: u8, r: u8, g: u8, b: u8);
    fn get_hpd(&mut self) -> bool;
}

#[macro_export]
macro_rules! impl_dma_framebuffer {
    ($(
        $DMA_FRAMEBUFFERX:ident: $PACFRAMEBUFFERX:ty,
        $PALETTEX:ident: $PACPALETTEX:ty,
        $BLITTERX:ident: $PACBLITTERX:ty,
        $PIXEL_PLOTX:ident: $PACPIXEL_PLOTX:ty,
        $LINEX:ident: $PACLINEX:ty,
    )+) => {
        $(
            use tiliqua_hal::dma_framebuffer::{DVIModeline, Rotate};
            use tiliqua_hal::embedded_graphics::prelude::{Pixel, Size, OriginDimensions, DrawTarget};
            use tiliqua_lib::color::HI8;

            pub struct $DMA_FRAMEBUFFERX {
                registers_fb: $PACFRAMEBUFFERX,
                registers_palette: $PACPALETTEX,
                registers_blitter: $PACBLITTERX,
                registers_pixel_plot: $PACPIXEL_PLOTX,
                registers_line: $PACLINEX,
                mode: DVIModeline,
                framebuffer_base: *mut u32,
                blitter_mem_base: *mut u32,
                current_spritesheet_key: u32,
            }

            impl $DMA_FRAMEBUFFERX {
                pub fn new(registers_fb: $PACFRAMEBUFFERX, registers_palette: $PACPALETTEX, registers_blitter: $PACBLITTERX,
                       registers_pixel_plot: $PACPIXEL_PLOTX, registers_line: $PACLINEX, fb_base: usize, mode: DVIModeline, blitter_mem_base: usize) -> Self {
                    registers_fb.flags().write(|w| unsafe {
                        w.enable().bit(false)
                    });
                    registers_fb.fb_base().write(|w| unsafe {
                        w.fb_base().bits(fb_base as u32)
                    });
                    registers_fb.h_timing().write(|w| unsafe {
                        w.h_active().bits(mode.h_active);
                        w.h_sync_start().bits(mode.h_sync_start)
                    } );
                    registers_fb.h_timing2().write(|w| unsafe {
                        w.h_sync_end().bits(mode.h_sync_end);
                        w.h_total().bits(mode.h_total)
                    } );
                    registers_fb.v_timing().write(|w| unsafe {
                        w.v_active().bits(mode.v_active);
                        w.v_sync_start().bits(mode.v_sync_start)
                    } );
                    registers_fb.v_timing2().write(|w| unsafe {
                        w.v_sync_end().bits(mode.v_sync_end);
                        w.v_total().bits(mode.v_total)
                    } );
                    registers_fb.hv_timing().write(|w| unsafe {
                        w.h_sync_invert().bit(mode.h_sync_invert);
                        w.v_sync_invert().bit(mode.v_sync_invert);
                        w.active_pixels().bits(
                            mode.h_active as u32 * mode.v_active as u32)
                    } );
                    registers_fb.flags().write(|w| unsafe {
                        w.enable().bit(true);
                        w.rotation().bits(mode.rotate.clone() as u8)
                    });
                    Self {
                        registers_fb,
                        registers_palette,
                        registers_blitter,
                        registers_pixel_plot,
                        registers_line,
                        mode,
                        framebuffer_base: fb_base as *mut u32,
                        blitter_mem_base: blitter_mem_base as *mut u32,
                        current_spritesheet_key: 0, // No spritesheet loaded initially
                    }
                }

                pub fn rotate(&mut self, rotation: &Rotate) {
                    self.registers_fb.flags().write(|w| unsafe {
                        w.enable().bit(true);
                        w.rotation().bits(rotation.clone() as u8)
                    });
                    self.mode.rotate = rotation.clone();
                }

            }


            impl hal::dma_framebuffer::DMAFramebuffer for $DMA_FRAMEBUFFERX {
                fn update_fb_base(&mut self, fb_base: u32) {
                    self.registers_fb.fb_base().write(|w| unsafe {
                        w.fb_base().bits(fb_base)
                    });
                    self.framebuffer_base = fb_base as *mut u32
                }

                fn set_palette_rgb(&mut self, intensity: u8, hue: u8, r: u8, g: u8, b: u8)  {
                    /* wait until last coefficient written */ 
                    while self.registers_palette.palette_busy().read().bits() == 1 { }
                    self.registers_palette.palette().write(|w| unsafe {
                        w.position().bits(((intensity&0xF) << 4) | (hue&0xF));
                        w.red()     .bits(r);
                        w.green()   .bits(g);
                        w.blue()    .bits(b)
                    } );
                }

                fn get_hpd(&mut self) -> bool  {
                    self.registers_fb.hpd().read().hpd().bit()
                }
            }

            impl OriginDimensions for $DMA_FRAMEBUFFERX {
                fn size(&self) -> Size {
                    match self.mode.rotate {
                        Rotate::Normal | Rotate::Inverted => {
                            Size::new(self.mode.h_active as u32,
                                      self.mode.v_active as u32)
                        }
                        Rotate::Left | Rotate::Right => {
                            Size::new(self.mode.v_active as u32,
                                      self.mode.h_active as u32)
                        }
                    }
                }
            }

            impl DrawTarget for $DMA_FRAMEBUFFERX {

                type Color = HI8;
                type Error = core::convert::Infallible;

                /// Draw individual pixels to the framebuffer
                ///
                /// These draws will be rotated by the graphics hardware in modes
                /// other than `Rotate::Normal`.
                ///
                /// Draw operations are enqueued asynchronously, that is, this function
                /// may return while the pixel draws are still being executed.
                ///
                fn draw_iter<I>(&mut self, pixels: I) -> Result<(), Self::Error>
                where
                    I: IntoIterator<Item = Pixel<Self::Color>>,
                {
                    for Pixel(coord, color) in pixels.into_iter() {
                        while self.registers_pixel_plot.status().read().busy().bit() {
                            // Plotting FIFO is full. Spin.
                            riscv::asm::nop();
                        }
                        self.registers_pixel_plot.plot().write(|w| unsafe {
                            w.x().bits(coord.x as u16);
                            w.y().bits(coord.y as u16);
                            w.pixel().bits(color.to_raw())
                        });
                    }
                    Ok(())
                }

                // *** ACCELERATED DRAWING EXTENSIONS ***
                //
                // `draw_iter` above is a normal `embedded-graphics` DrawTarget method.
                //
                // The below hardware accelerator interface for blitting / lines depends on an
                // `embedded-graphics` fork with some minor changes to call into these for
                // operations that can be accelerated by them (e.g. font / line drawing).
                //

                /// Upload a new (1bpp) spritesheet to the blitter peripheral.
                ///
                /// If the spritesheet is already loaded, this does nothing and returns instantly.
                ///
                /// If the spritesheet is too large for the blitter peripheral, this panics.
                ///
                fn upload_spritesheet(&mut self, key: u32, pixels: &[u8], width: u32, height: u32, bpp: u8) -> bool {

                    if self.current_spritesheet_key == key {
                        return true; // Already loaded, NOP
                    }

                    if bpp != 1 {
                        return false;
                    }

                    let sprite_mem_words = self.registers_blitter.status().read().mem_words().bits() as usize;
                    if (pixels.len() / 4) > sprite_mem_words {
                        // TODO: panic here?
                        log::info!("upload_spritesheet: too large for hardware");
                        return false;
                    }

                    // Wait for command FIFO to be empty before changing spritesheet
                    // This ensures all pending blit operations using the current spritesheet complete
                    while !self.registers_blitter.status().read().empty().bit() {
                        riscv::asm::nop();
                    }

                    // Set new sprite sheet width (in 1bpp pixels)
                    self.registers_blitter.sheet_width().write(|w| unsafe {
                        w.width().bits(width as u16)
                    });

                    // Copy in new sprite sheet
                    unsafe {
                        let dest = core::slice::from_raw_parts_mut(
                            self.blitter_mem_base as *mut u8,
                            pixels.len()
                        );
                        dest.copy_from_slice(pixels);
                    }

                    // Ensure future writes of the same sheet are NOPs
                    self.current_spritesheet_key = key;

                    true
                }

                /// Blit a single sub-rectangle of the last spritesheet from `upload_spritesheet`.
                ///
                /// Given a source rectangle (src_{x|y}/width/height) and a destination position
                /// and color (dst_{x|y}, color), this function copies the source sub-rectangle
                /// to the destination position, with the desired color. 0s are transparent, 1s
                /// are plotted in 'replace' mode, replacing whatever pixel was previously there.
                ///
                /// Blit operations are enqueued asynchronously - that is, this function may return
                /// while the blit itself is still being executed.
                ///
                fn blit_sprite(&mut self, key: u32, src_x: u32, src_y: u32, width: u32, height: u32,
                               dst_x: i32, dst_y: i32, color: Self::Color) -> bool {

                    // Verify the correct spritesheet is loaded
                    if self.current_spritesheet_key != key {
                        // TODO: panic here?
                        log::info!("blit_sprite: attempted blit with wrong spritesheet key!");
                        return false;
                    }

                    // Spin if command FIFO is full (too many blits already enqueued)
                    while self.registers_blitter.status().read().full().bit() {
                        riscv::asm::nop();
                    }

                    // Enqueue new source sub-rectangle
                    self.registers_blitter.src().write(|w| unsafe {
                        w.src_x().bits(src_x as u8);
                        w.src_y().bits(src_y as u8);
                        w.width().bits(width as u8);
                        w.height().bits(height as u8)
                    });

                    while self.registers_blitter.status().read().full().bit() {
                        riscv::asm::nop();
                    }

                    // Enqueue new blit operation from the last source sub-rectangle.
                    self.registers_blitter.blit().write(|w| unsafe {
                        w.dst_x().bits(dst_x as u16);
                        w.dst_y().bits(dst_y as u16);
                        w.pixel().bits(color.to_raw())
                    });

                    // Command is now queued - hardware will execute asynchronously
                    // Next blit_sprite call will stall only if command FIFO is full
                    true
                }

                /// Draw a single 2D line between 2 points
                ///
                /// Only 1-pixel thick lines are supported by the hardware.
                ///
                /// Technically the hardware also supports line strips, but this is not hooked into
                /// `embedded-graphics` just yet, so for now we go one line at a time.
                ///
                /// Line draws are enqueued asynchronously - that is, this function may return
                /// while the lines are still being drawn.
                ///
                fn draw_line_solid(&mut self, start_x: i32, start_y: i32, end_x: i32, end_y: i32,
                                   stroke_width: u32, color: Self::Color) -> bool {

                    // TODO: Check bounds? Bresenham hardware might do wierd stuff
                    // or stall forever if the line endpoints are off the screen...

                    if stroke_width != 1 {
                        // Only support 1-pixel wide solid lines for now.
                        // Fall back to `embedded-graphics` software implementation.
                        return false;
                    }

                    // No space for new line commands?
                    while self.registers_line.status().read().full().bit() {
                        riscv::asm::nop();
                    }

                    let pixel_data = color.to_raw();

                    self.registers_line.point().write(|w| unsafe {
                        w.x().bits(start_x as u16);
                        w.y().bits(start_y as u16);
                        w.pixel().bits(pixel_data);
                        w.cmd().bit(false) // CONTINUE line strip (0)
                    });

                    while self.registers_line.status().read().full().bit() {
                        riscv::asm::nop();
                    }

                    self.registers_line.point().write(|w| unsafe {
                        w.x().bits(end_x as u16);
                        w.y().bits(end_y as u16);
                        w.pixel().bits(pixel_data);
                        w.cmd().bit(true) // END line strip (1)
                    });

                    // Line was enqueued and will be drawn asynchronously.
                    // `Some(Ok())` indicates the software line drawing fallback is not needed.
                    true
                }
            }
        )+
    }
}
