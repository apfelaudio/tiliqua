use heapless::String;

use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    primitives::{PrimitiveStyle, PrimitiveStyleBuilder, Rectangle, Line, Polyline},
    mono_font::{ascii::FONT_9X15, ascii::FONT_9X15_BOLD, MonoTextStyle},
    prelude::*,
    text::{Alignment, Text, renderer::TextRenderer},
};

use crate::opt;

pub fn draw_options<D, O>(d: &mut D, opts: &O,
                       pos_x: u32, pos_y: u32, hue: u8) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Gray8>,
    O: opt::OptionPage
{
    let font_small_white = MonoTextStyle::new(&FONT_9X15_BOLD, Gray8::WHITE);
    let font_small_grey = MonoTextStyle::new(&FONT_9X15, Gray8::new(0xB0 + hue));

    let opts_view = opts.view().options();

    let vx = pos_x as i32;
    let vy = pos_y as usize;
    let vspace: usize = 18;
    let hspace: i32 = 150;

    let screen_hl = match (opts.view().selected(), opts.modify()) {
        (None, _) => true,
        _ => false,
    };

    Text::with_alignment(
        &opts.screen().value(),
        Point::new(vx-12, vy as i32),
        if screen_hl { font_small_white } else { font_small_grey },
        Alignment::Right
    ).draw(d)?;

    if screen_hl && opts.modify() {
        Text::with_alignment(
            "^",
            Point::new(vx-12, (vy + vspace) as i32),
            font_small_white,
            Alignment::Right,
        ).draw(d)?;
    }

    let vx = vx-2;

    for (n, opt) in opts_view.iter().enumerate() {
        let mut font = font_small_grey;
        if let Some(n_selected) = opts.view().selected() {
            if n_selected == n {
                font = font_small_white;
                if opts.modify() {
                    Text::with_alignment(
                        "<",
                        Point::new(vx+hspace+2, (vy+vspace*n) as i32),
                        font,
                        Alignment::Left,
                    ).draw(d)?;
                }
            }
        }
        Text::with_alignment(
            opt.name(),
            Point::new(vx+5, (vy+vspace*n) as i32),
            font,
            Alignment::Left,
        ).draw(d)?;
        Text::with_alignment(
            &opt.value(),
            Point::new(vx+hspace, (vy+vspace*n) as i32),
            font,
            Alignment::Right,
        ).draw(d)?;
    }

    let mut stroke = PrimitiveStyleBuilder::new()
        .stroke_color(Gray8::new(0xB0 + hue))
        .stroke_width(1)
        .build();
    Line::new(Point::new(vx-3, vy as i32 - 10),
              Point::new(vx-3, (vy - 13 + vspace*opts_view.len()) as i32))
              .into_styled(stroke)
              .draw(d)?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    use image::{ImageBuffer, RgbImage, Rgb};
    use image::imageops::{rotate90, resize, FilterType};

    use crate::opt::Screen;

    struct FakeDisplay {
        img: RgbImage,
    }

    impl DrawTarget for FakeDisplay {
        type Color = Gray8;
        type Error = core::convert::Infallible;

        fn draw_iter<I>(&mut self, pixels: I) -> Result<(), Self::Error>
        where
            I: IntoIterator<Item = Pixel<Self::Color>>,
        {
            for Pixel(coord, color) in pixels.into_iter() {
                if let Ok((x @ 0..=800, y @ 0..=600)) = coord.try_into() {
                    *self.img.get_pixel_mut(x, y) = Rgb([
                        color.luma(),
                        color.luma(),
                        color.luma()
                    ]);
                }
            }

            Ok(())
        }
    }

    impl OriginDimensions for FakeDisplay {
        fn size(&self) -> Size {
            Size::new(800, 600)
        }
    }

    #[test]
    fn draw_screen() {
        let mut disp = FakeDisplay {
            img: ImageBuffer::new(800, 600)
        };

        let mut opts = opt::Options::new();
        opts.tick_up();
        opts.toggle_modify();
        opts.tick_up();
        opts.toggle_modify();

        let screens = [
            (Screen::Adsr, "adsr.png"),
            //(Screen::Scope, "scope.png"),
            //(Screen::Touch, "touch.png"),
        ];

        for (screen, filename) in screens {
            disp.img = ImageBuffer::new(800, 600);
            draw_options(&mut disp, &opts, 800-200, 600-100);
            disp.img.save(filename).unwrap();
        }
    }

}
