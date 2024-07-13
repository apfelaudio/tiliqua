#[macro_export]
macro_rules! impl_dma_display {
    ($(
        $DMA_DISPLAYX:ident, $H_ACTIVE:expr, $V_ACTIVE:expr
    )+) => {
        $(
            struct $DMA_DISPLAYX {
                framebuffer_base: *mut u32,
            }

            impl OriginDimensions for $DMA_DISPLAYX {
                fn size(&self) -> Size {
                    Size::new($H_ACTIVE, $V_ACTIVE)
                }
            }

            impl DrawTarget for $DMA_DISPLAYX {
                type Color = Gray8;
                type Error = core::convert::Infallible;
                fn draw_iter<I>(&mut self, pixels: I) -> Result<(), Self::Error>
                where
                    I: IntoIterator<Item = Pixel<Self::Color>>,
                {
                    let xs: u32 = $H_ACTIVE;
                    let ys: u32 = $V_ACTIVE;
                    for Pixel(coord, color) in pixels.into_iter() {
                        if let Ok((x @ 0..=$H_ACTIVE,
                                   y @ 0..=$V_ACTIVE)) = coord.try_into() {
                            // Calculate the index in the framebuffer.
                            let index: u32 = (x + y * $H_ACTIVE) / 4;
                            unsafe {
                                // TODO: support anything other than Gray8
                                let mut px = self.framebuffer_base.offset(
                                    index as isize).read_volatile();
                                px &= !(0xFFu32 << (8*(x%4)));
                                self.framebuffer_base.offset(index as isize).write_volatile(
                                    px | ((color.luma() as u32) << (8*(x%4))));
                            }
                        }
                    }
                    Ok(())
                }
            }
        )+
    }
}
