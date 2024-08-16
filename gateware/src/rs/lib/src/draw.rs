use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    primitives::{PrimitiveStyleBuilder, Line, Rectangle, Circle},
    mono_font::{ascii::FONT_9X15, ascii::FONT_9X15_BOLD, MonoTextStyle},
    text::{Alignment, Text},
    prelude::*,
};

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

pub fn draw_sid<D>(d: &mut D, x: u32, y: u32, hue: u8,
                   wfm:    Option<u8>,
                   gates:  [bool; 3],
                   filter: bool,
                   switches: [bool; 3],
                   filter_types: [bool; 3],
                   ) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Gray8>,
{
     let stroke_grey = PrimitiveStyleBuilder::new()
            .stroke_color(Gray8::new(0xB0 + hue))
            .stroke_width(1)
            .build();

     let stroke_white = PrimitiveStyleBuilder::new()
            .stroke_color(Gray8::WHITE)
            .stroke_width(1)
            .build();

    let mut line = |disp: &mut D, x1: u32, y1: u32, x2: u32, y2: u32, hl: bool| {
        Line::new(Point::new((x+x1) as i32, (y+y1) as i32),
                  Point::new((x+x2) as i32, (y+y2) as i32))
                  .into_styled(if hl { stroke_white } else { stroke_grey } )
                  .draw(disp).ok()
    };

    let mut rect = |disp: &mut D, x1: u32, y1: u32, sx: u32, sy: u32, hl: bool| {
        Rectangle::new(Point::new((x+x1) as i32, (y+y1) as i32),
                       Size::new(sx, sy))
                       .into_styled(if hl { stroke_white } else { stroke_grey } )
                       .draw(disp).ok()
    };

    let mut circle = |disp: &mut D, x1: u32, y1: u32, radius: u32| {
        Circle::new(Point::new((x+x1-radius) as i32, (y+y1-radius) as i32), radius*2+1)
                    .into_styled(stroke_grey)
                    .draw(disp).ok()
    };

    let spacing = 32;
    for n in 0..3 {
        let ys = n * spacing;
        // wfm
        let hl_wfm = wfm == Some(n as u8);
        rect(d,  3,  3+ys, 30,    15, hl_wfm);
        line(d,  9, 14+ys, 16,  7+ys, hl_wfm);
        line(d, 17,  7+ys, 17, 14+ys, hl_wfm);
        line(d, 17, 14+ys, 24,  7+ys, hl_wfm);
        line(d, 25,  7+ys, 25, 14+ys, hl_wfm);

        // adsr / gate
        let hl_adsr = gates[n as usize];
        rect(d, 3,  19+ys, 30,    15, hl_adsr);
        line(d, 7,  31+ys, 12, 21+ys, hl_adsr);
        line(d, 13, 22+ys, 15, 27+ys, hl_adsr);
        line(d, 16, 27+ys, 24, 27+ys, hl_adsr);
        line(d, 25, 27+ys, 29, 31+ys, hl_adsr);

        // wiring
        circle(d, 51, 10+ys, 8);
        line(d,   33, 10+ys, 42, 10+ys, false);
        line(d,   32, 26+ys, 50, 26+ys, false);
        line(d,   51, 19+ys, 51, 26+ys, false);
        line(d,   46, 5+ys,  56, 15+ys, false);
        line(d,   46, 15+ys, 56, 5+ys,  false);
        line(d,   60, 10+ys, 69, 10+ys, false);

        // switch
        let switch_pos = if switches[n as usize] { 8 } else { 0 };
        line(d, 70, 10+ys, 79, 6+ys+switch_pos, filter);
    }

    // right wiring
    line(d, 80,  6,  85,  6,  false);
    line(d, 80,  14, 83,  14, false);
    line(d, 83,  13, 87,  13, false);
    line(d, 87,  14, 90,  14, false);
    line(d, 80,  38, 85,  38, false);
    line(d, 85,  6,  85,  90, false);
    line(d, 80,  70, 85,  70, false);
    line(d, 80,  46, 83,  46, false);
    line(d, 80,  78, 83,  78, false);
    line(d, 83,  45, 87,  45, false);
    line(d, 83,  77, 87,  77, false);
    line(d, 87,  46, 90,  46, false);
    line(d, 87,  78, 90,  78, false);
    line(d, 90,  78, 90,  14, false);
    line(d, 90,  46, 95,  46, false);
    line(d, 108, 86, 108, 94, false);
    line(d, 104, 90, 112, 90, false);
    line(d, 86,  90, 100, 90, false);
    line(d, 108, 61, 108, 81, false);
    line(d, 117, 90, 123, 90, false);
    line(d, 123, 90, 120, 87, false);
    line(d, 123, 90, 120, 93, false);

    // lpf
    line(d,   98,  31, 104, 31, filter_types[0]);
    line(d,   104, 31, 109, 36, filter_types[0]);
    line(d,   110, 36, 116, 36, filter_types[0]);
    // bpf
    line(d,   98,  46, 103, 46, filter_types[1]);
    line(d,   106, 41, 104, 46, filter_types[1]);
    line(d,   106, 41, 108, 45, filter_types[1]);
    line(d,   108, 46, 116, 46, filter_types[1]);
    // hpf
    line(d,   98,  59, 104, 59, filter_types[2]);
    line(d,   110, 54, 105, 59, filter_types[2]);
    line(d,   110, 54, 116, 54, filter_types[2]);

    rect(d,   96,  29, 23,  33, filter);

    circle(d, 108, 90, 8);

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

#[cfg(test)]
mod tests {
    use super::*;

    use image::{ImageBuffer, RgbImage, Rgb};

    const H_ACTIVE: u32 = 800;
    const V_ACTIVE: u32 = 600;

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

        let n_voices = 8;
        for n in 0..8 {
            draw_voice(&mut disp,
                       ((H_ACTIVE as f32)/2.0f32 + 250.0f32*f32::cos(2.3f32 + 2.0f32 * n as f32 / 8.0f32)) as i32,
                       ((V_ACTIVE as f32)/2.0f32 + 250.0f32*f32::sin(2.3f32 + 2.0f32 * n as f32 / 8.0f32)) as u32,
                       12, 127, 0).ok();
        }

        draw_sid(&mut disp, H_ACTIVE/2, V_ACTIVE/2, 0);

        disp.img.save("draw_opt_test.png").unwrap();
    }

}
