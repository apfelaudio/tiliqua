use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    primitives::{PrimitiveStyleBuilder, Line},
    mono_font::{ascii::FONT_9X15, ascii::FONT_9X15_BOLD, MonoTextStyle},
    text::{Alignment, Text},
    prelude::*,
};

use crate::generated_constants::*;

use crate::opt;

use heapless::String;
use core::fmt::Write;

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

    let stroke = PrimitiveStyleBuilder::new()
        .stroke_color(Gray8::new(0xB0 + hue))
        .stroke_width(1)
        .build();
    Line::new(Point::new(vx-3, vy as i32 - 10),
              Point::new(vx-3, (vy - 13 + vspace*opts_view.len()) as i32))
              .into_styled(stroke)
              .draw(d)?;

    Text::with_alignment(
        BITSTREAM_NAME,
        Point::new(360, 700),
        font_small_white,
        Alignment::Center
    ).draw(d)?;

    Ok(())
}

const NOTE_NAMES: [&'static str; 12] = [
    "C",
    "C#",
    "D",
    "D#",
    "E",
    "F",
    "F#",
    "G",
    "G#",
    "A",
    "A#",
    "B",
];

fn midi_note_name<const N: usize>(s: &mut String<N>, note: u8) {
    if note >= 12 {
        write!(s, "{}{}", NOTE_NAMES[(note%12) as usize],
               (note / 12) - 1).ok();
    }
}

pub fn draw_voice<D>(d: &mut D, sx: i32, sy: u32, note: u8, cutoff: u8, hue: u8) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Gray8>,
{
    let font_small_white = MonoTextStyle::new(&FONT_9X15, Gray8::WHITE);


    let mut stroke_gain = PrimitiveStyleBuilder::new()
        .stroke_color(Gray8::new(0x1))
        .stroke_width(1)
        .build();


    let mut s: String<16> = String::new();

    if cutoff > 0 {
        midi_note_name(&mut s, note);
        stroke_gain = PrimitiveStyleBuilder::new()
            .stroke_color(Gray8::new(0xA0 + hue))
            .stroke_width(1)
            .build();
    }

    // Pitch text + box

    Text::new(
        &s,
        Point::new(sx+11, sy as i32 + 14),
        font_small_white,
    )
    .draw(d)?;

    // LPF visualization

    let filter_x = sx+2;
    let filter_y = (sy as i32) + 19;
    let filter_w = 40;
    let filter_h = 16;
    let filter_skew = 2;
    let filter_pos: i32 = ((filter_w as f32) * (cutoff as f32 / 256.0f32)) as i32;

    Line::new(Point::new(filter_x,            filter_y),
              Point::new(filter_x+filter_pos, filter_y))
              .into_styled(stroke_gain)
              .draw(d)?;

    Line::new(Point::new(filter_x+filter_skew+filter_pos, filter_y+filter_h),
              Point::new(filter_x+filter_w+filter_skew,               filter_y+filter_h))
              .into_styled(stroke_gain)
              .draw(d)?;

    Line::new(Point::new(filter_x+filter_pos, filter_y),
              Point::new(filter_x+filter_pos+filter_skew, filter_y+filter_h))
              .into_styled(stroke_gain)
              .draw(d)?;


    Ok(())
}

#[cfg(test)]
mod test_data {

    // Fake set of options for quick render testing

    use heapless::String;
    use core::str::FromStr;
    use strum_macros::{EnumIter, IntoStaticStr};

    use crate::opt::*;
    use crate::impl_option_view;
    use crate::impl_option_page;

    #[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
    #[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
    pub enum Screen {
        Xbeam,
    }

    #[derive(Clone)]
    pub struct XbeamOptions {
        pub selected: Option<usize>,
        pub persist: NumOption<u16>,
        pub hue: NumOption<u8>,
        pub intensity: NumOption<u8>,
    }

    impl_option_view!(XbeamOptions,
                      persist, hue, intensity);

    #[derive(Clone)]
    pub struct Options {
        pub modify: bool,
        pub screen: EnumOption<Screen>,

        pub xbeam: XbeamOptions,
    }


    impl_option_page!(Options,
                      (Screen::Xbeam, xbeam));

    impl Options {
        pub fn new() -> Options {
            Options {
                modify: true,
                screen: EnumOption {
                    name: String::from_str("screen").unwrap(),
                    value: Screen::Xbeam,
                },
                xbeam: XbeamOptions {
                    selected: None,
                    persist: NumOption{
                        name: String::from_str("persist").unwrap(),
                        value: 1024,
                        step: 256,
                        min: 512,
                        max: 32768,
                    },
                    hue: NumOption{
                        name: String::from_str("hue").unwrap(),
                        value: 0,
                        step: 1,
                        min: 0,
                        max: 15,
                    },
                    intensity: NumOption{
                        name: String::from_str("intensity").unwrap(),
                        value: 6,
                        step: 1,
                        min: 0,
                        max: 15,
                    },
                },
            }
        }
    }
}

const COORDS_APFELAUDIO: [(i16, i16); 294] = [
	(-332, -56),
	(-336, -56),
	(-340, -48),
	(-340, 48),
	(-336, 56),
	(-332, 56),
	(-312, 56),
	(-312, 48),
	(-328, 48),
	(-332, 44),
	(-332, -44),
	(-328, -48),
	(-312, -48),
	(-312, -56),
	(-332, -56),
	(-280, -32),
	(-292, -28),
	(-300, -24),
	(-304, -12),
	(-300, -4),
	(-292, 0),
	(-280, 0),
	(-260, 0),
	(-260, 8),
	(-264, 16),
	(-276, 20),
	(-288, 16),
	(-292, 8),
	(-300, 12),
	(-296, 20),
	(-288, 24),
	(-276, 28),
	(-260, 24),
	(-252, 4),
	(-252, -16),
	(-248, -20),
	(-244, -20),
	(-244, -28),
	(-252, -28),
	(-256, -28),
	(-260, -20),
	(-260, -20),
	(-260, -20),
	(-264, -24),
	(-272, -28),
	(-280, -32),
	(-280, -32),
	(-280, -24),
	(-268, -16),
	(-260, -4),
	(-260, 0),
	(-280, 0),
	(-292, -4),
	(-296, -12),
	(-292, -20),
	(-280, -24),
	(-280, -24),
	(-228, -52),
	(-228, 28),
	(-220, 28),
	(-220, 16),
	(-220, 16),
	(-212, 24),
	(-196, 28),
	(-184, 24),
	(-172, 12),
	(-168, 0),
	(-168, 0),
	(-172, -16),
	(-184, -28),
	(-196, -32),
	(-208, -28),
	(-216, -24),
	(-220, -20),
	(-220, -20),
	(-220, -52),
	(-228, -52),
	(-200, -24),
	(-184, -16),
	(-180, 0),
	(-180, 0),
	(-184, 12),
	(-200, 20),
	(-216, 12),
	(-220, 0),
	(-220, 0),
	(-216, -16),
	(-200, -24),
	(-200, -24),
	(-144, -28),
	(-144, 20),
	(-160, 20),
	(-160, 28),
	(-144, 28),
	(-144, 40),
	(-140, 48),
	(-132, 52),
	(-116, 52),
	(-116, 44),
	(-128, 44),
	(-132, 40),
	(-132, 28),
	(-112, 28),
	(-112, 20),
	(-132, 20),
	(-132, -28),
	(-144, -28),
	(-76, -32),
	(-92, -28),
	(-104, -16),
	(-108, 0),
	(-108, 0),
	(-104, 12),
	(-92, 24),
	(-76, 28),
	(-60, 24),
	(-52, 16),
	(-48, 0),
	(-48, 0),
	(-96, 0),
	(-92, -16),
	(-76, -24),
	(-64, -20),
	(-56, -12),
	(-48, -16),
	(-56, -20),
	(-64, -28),
	(-76, -32),
	(-76, -32),
	(-96, 4),
	(-56, 4),
	(-64, 16),
	(-76, 20),
	(-88, 16),
	(-96, 4),
	(-96, 4),
	(-32, -28),
	(-32, 52),
	(-20, 52),
	(-20, -28),
	(-32, -28),
	(12, -32),
	(0, -28),
	(-4, -24),
	(-4, -12),
	(-4, -4),
	(0, 0),
	(12, 0),
	(32, 0),
	(32, 8),
	(28, 16),
	(20, 20),
	(8, 16),
	(0, 8),
	(-4, 12),
	(0, 20),
	(4, 24),
	(20, 28),
	(36, 24),
	(44, 4),
	(44, -16),
	(44, -20),
	(52, -20),
	(52, -28),
	(44, -28),
	(36, -28),
	(36, -20),
	(36, -20),
	(32, -20),
	(28, -24),
	(24, -28),
	(12, -32),
	(12, -32),
	(12, -24),
	(28, -16),
	(32, -4),
	(32, 0),
	(12, 0),
	(4, -4),
	(0, -12),
	(4, -20),
	(12, -24),
	(12, -24),
	(84, -32),
	(72, -28),
	(64, -20),
	(64, -4),
	(64, 28),
	(72, 28),
	(72, -4),
	(76, -16),
	(88, -20),
	(104, -16),
	(108, 0),
	(108, 28),
	(116, 28),
	(116, -28),
	(108, -28),
	(108, -20),
	(108, -20),
	(100, -28),
	(84, -32),
	(84, -32),
	(164, -32),
	(148, -28),
	(140, -16),
	(136, 0),
	(136, 0),
	(140, 12),
	(148, 24),
	(164, 28),
	(172, 28),
	(180, 24),
	(184, 16),
	(184, 16),
	(184, 52),
	(196, 52),
	(196, -28),
	(188, -28),
	(188, -20),
	(184, -20),
	(176, -28),
	(164, -32),
	(164, -32),
	(164, -24),
	(180, -16),
	(184, 0),
	(184, 0),
	(180, 12),
	(164, 20),
	(152, 12),
	(144, 0),
	(144, 0),
	(152, -16),
	(164, -24),
	(164, -24),
	(216, -28),
	(216, 28),
	(224, 28),
	(224, -28),
	(216, -28),
	(220, 36),
	(216, 36),
	(212, 44),
	(216, 48),
	(220, 52),
	(224, 48),
	(228, 44),
	(224, 36),
	(220, 36),
	(220, 36),
	(272, -32),
	(256, -28),
	(248, -16),
	(244, 0),
	(244, 0),
	(248, 12),
	(256, 24),
	(272, 28),
	(288, 24),
	(300, 12),
	(304, 0),
	(304, 0),
	(300, -16),
	(288, -28),
	(272, -32),
	(272, -32),
	(272, -24),
	(288, -16),
	(292, 0),
	(292, 0),
	(288, 12),
	(272, 20),
	(256, 12),
	(252, 0),
	(252, 0),
	(256, -16),
	(272, -24),
	(272, -24),
	(312, -56),
	(312, -48),
	(328, -48),
	(332, -44),
	(332, 44),
	(328, 48),
	(312, 48),
	(312, 56),
	(332, 56),
	(336, 56),
	(340, 48),
	(340, -48),
	(336, -56),
	(332, -56),
	(312, -56),
];

pub fn draw_apfelaudio<D>(d: &mut D, sx: i32, sy: i32, ix: u32) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Gray8>,
{
    let mut stroke_white = PrimitiveStyleBuilder::new()
        .stroke_color(Gray8::WHITE)
        .stroke_width(1)
        .build();
    let p = ((ix % ((COORDS_APFELAUDIO.len() as u32)-1)) + 1) as usize;
    let x = COORDS_APFELAUDIO[p].0/2;
    let y = -COORDS_APFELAUDIO[p].1/2;
    let xl = COORDS_APFELAUDIO[p-1].0/2;
    let yl = -COORDS_APFELAUDIO[p-1].1/2;
    Line::new(Point::new(sx+xl as i32, sy+yl as i32),
              Point::new(sx+x as i32, sy+y as i32))
              .into_styled(stroke_white)
              .draw(d)?;
    Ok(())
}


#[cfg(test)]
mod tests {
    use super::*;

    use image::{ImageBuffer, RgbImage, Rgb};

    const H_ACTIVE: u32 = 720;
    const V_ACTIVE: u32 = 720;

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
                if let Ok((x @ 0..=H_ACTIVE, y @ 0..=V_ACTIVE)) = coord.try_into() {
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
            Size::new(H_ACTIVE, V_ACTIVE)
        }
    }

    #[test]
    fn draw_screen() {
        use crate::opt::OptionPageEncoderInterface;

        let mut disp = FakeDisplay {
            img: ImageBuffer::new(H_ACTIVE, V_ACTIVE)
        };

        let mut opts = test_data::Options::new();
        opts.tick_up();
        opts.toggle_modify();
        opts.tick_up();
        opts.toggle_modify();

        disp.img = ImageBuffer::new(H_ACTIVE, V_ACTIVE);
        draw_options(&mut disp, &opts, H_ACTIVE-200, V_ACTIVE/2, 0).ok();

        draw_apfelaudio(&mut disp, (H_ACTIVE/2) as i32, (V_ACTIVE/2) as i32, 1u32);

        let n_voices = 8;
        for n in 0..8 {
            draw_voice(&mut disp,
                       ((H_ACTIVE as f32)/2.0f32 + 250.0f32*f32::cos(2.3f32 + 2.0f32 * n as f32 / 8.0f32)) as i32,
                       ((V_ACTIVE as f32)/2.0f32 + 250.0f32*f32::sin(2.3f32 + 2.0f32 * n as f32 / 8.0f32)) as u32,
                       12, 127, 0).ok();
        }

        disp.img.save("draw_opt_test.png").unwrap();
    }

}
