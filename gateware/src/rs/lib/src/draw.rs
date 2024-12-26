use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    primitives::{PrimitiveStyleBuilder, Line, Ellipse},
    mono_font::{ascii::FONT_9X15, ascii::FONT_9X15_BOLD, MonoTextStyle},
    text::{Alignment, Text},
    prelude::*,
};

use crate::opt;
use crate::logo_coords;

use heapless::String;
use core::fmt::Write;

pub fn draw_options<D, O>(d: &mut D, opts: &O,
                       pos_x: u32, pos_y: u32, hue: u8) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Gray8>,
    O: opt::OptionPage
{
    let font_small_white = MonoTextStyle::new(&FONT_9X15_BOLD, Gray8::new(0xF0 + hue));
    let font_small_grey = MonoTextStyle::new(&FONT_9X15, Gray8::new(0xA0 + hue));

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
        .stroke_color(Gray8::new(0xA0 + hue))
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
    let font_small_white = MonoTextStyle::new(&FONT_9X15, Gray8::new(0xF0 + hue));


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

pub fn draw_boot_logo<D>(d: &mut D, sx: i32, sy: i32, ix: u32) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Gray8>,
{
    use logo_coords::BOOT_LOGO_COORDS;
    let stroke_white = PrimitiveStyleBuilder::new()
        .stroke_color(Gray8::WHITE)
        .stroke_width(1)
        .build();
    let p = ((ix % ((BOOT_LOGO_COORDS.len() as u32)-1)) + 1) as usize;
    let x = BOOT_LOGO_COORDS[p].0/2;
    let y = -BOOT_LOGO_COORDS[p].1/2;
    let xl = BOOT_LOGO_COORDS[p-1].0/2;
    let yl = -BOOT_LOGO_COORDS[p-1].1/2;
    Line::new(Point::new(sx+xl as i32, sy+yl as i32),
              Point::new(sx+x as i32, sy+y as i32))
              .into_styled(stroke_white)
              .draw(d)?;
    Ok(())
}

pub fn draw_name<D>(d: &mut D, pos_x: u32, pos_y: u32, hue: u8, name: &str, sha: &str) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Gray8>,
{
    let font_small_white = MonoTextStyle::new(&FONT_9X15_BOLD, Gray8::new(0xF0 + hue));
    let font_small_grey = MonoTextStyle::new(&FONT_9X15, Gray8::new(0xA0 + hue));

    Text::with_alignment(
        name,
        Point::new(pos_x as i32, pos_y as i32),
        font_small_white,
        Alignment::Center
    ).draw(d)?;

    Text::with_alignment(
        sha,
        Point::new(pos_x as i32, (pos_y + 18) as i32),
        font_small_grey,
        Alignment::Center
    ).draw(d)?;

    Ok(())
}

pub fn draw_tiliqua<D>(d: &mut D, x: u32, y: u32, hue: u8,
                       str_l: [&str; 8], str_r: [&str; 6], text_title: &str, text_desc: &str) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Gray8>,
{
     let stroke_grey = PrimitiveStyleBuilder::new()
            .stroke_color(Gray8::new(0xA0 + hue))
            .stroke_width(1)
            .build();

    let font_small_grey = MonoTextStyle::new(&FONT_9X15, Gray8::new(0xA0 + hue));
    let font_small_white = MonoTextStyle::new(&FONT_9X15_BOLD, Gray8::new(0xF0 + hue));

    let line = |disp: &mut D, x1: u32, y1: u32, x2: u32, y2: u32| {
        Line::new(Point::new((x+x1) as i32, (y+y1) as i32),
                  Point::new((x+x2) as i32, (y+y2) as i32))
                  .into_styled(stroke_grey)
                  .draw(disp).ok()
    };

    let ellipse = |disp: &mut D, x1: u32, y1: u32, sx: u32, sy: u32| {
        Ellipse::new(Point::new((x+x1-sx) as i32, (y+y1-sy) as i32),
                  Size::new(sx<<1, sy<<1))
                  .into_styled(stroke_grey)
                  .draw(disp).ok()
    };

    ellipse(d, 70, 19, 4, 2);
    ellipse(d, 90, 19, 4, 2);
    ellipse(d, 70, 142, 4, 2);
    ellipse(d, 90, 142, 4, 2);
    ellipse(d, 88, 33, 6, 6);
    ellipse(d, 88, 46, 5, 2);
    ellipse(d, 88, 55, 5, 2);
    ellipse(d, 89, 129, 4, 4);
    ellipse(d, 71, 129, 4, 4);
    ellipse(d, 71, 115, 4, 4);
    ellipse(d, 71, 101, 4, 4);
    ellipse(d, 71, 87, 4, 4);
    ellipse(d, 71, 73, 4, 4);
    ellipse(d, 71, 59, 4, 4);
    ellipse(d, 71, 45, 4, 4);
    ellipse(d, 71, 31, 4, 4);

    line(d, 63, 14, 63, 146);
    line(d, 97, 14, 97, 146);
    line(d, 63, 14, 97, 14);
    line(d, 63, 147, 97, 147);
    line(d, 90, 62, 90, 77);
    line(d, 85, 65, 85, 74);
    line(d, 85, 64, 90, 62);
    line(d, 85, 75, 90, 77);
    line(d, 85, 84, 85, 98);
    line(d, 90, 83, 90, 98);
    line(d, 85, 83, 90, 83);
    line(d, 86, 98, 89, 98);
    line(d, 90, 105, 90, 119);
    line(d, 85, 105, 85, 119);
    line(d, 85, 104, 90, 104);
    line(d, 86, 119, 89, 119);
    line(d, 66, 24, 94, 24);
    line(d, 66, 136, 94, 136);
    line(d, 58, 33, 60, 31);
    line(d, 60, 31, 58, 29);
    line(d, 58, 47, 60, 45);
    line(d, 58, 61, 60, 59);
    line(d, 60, 45, 58, 43);
    line(d, 60, 59, 58, 57);
    line(d, 58, 75, 60, 73);
    line(d, 60, 73, 58, 71);
    line(d, 45, 101, 47, 103);
    line(d, 45, 101, 47, 99);
    line(d, 45, 87, 47, 89);
    line(d, 45, 87, 47, 85);
    line(d, 45, 115, 47, 117);
    line(d, 45, 115, 47, 113);
    line(d, 45, 129, 47, 131);
    line(d, 45, 129, 47, 127);
    line(d, 101, 129, 103, 131);
    line(d, 101, 129, 103, 127);
    line(d, 60, 31, 45, 31);     // in0
    line(d, 60, 45, 45, 45);     // in1
    line(d, 60, 59, 45, 59);     // in2
    line(d, 60, 73, 45, 73);     // in3
    line(d, 59, 87, 45, 87);     // out0
    line(d, 59, 101, 45, 101);   // out1
    line(d, 59, 115, 45, 115);   // out2
    line(d, 59, 129, 45, 129);   // out3
    line(d, 115, 33, 101, 33);   // encoder
    line(d, 115, 55, 101, 55);   // usb2
    line(d, 115, 69, 101, 69);   // dvi
    line(d, 115, 90, 101, 90);   // ex1
    line(d, 115, 111, 101, 111); // ex2
    line(d, 115, 129, 101, 129); // TRS midi

    let mut text_l = [[0u32; 2]; 8];
    text_l[0][1] = 31;
    text_l[1][1] = 45;
    text_l[2][1] = 59;
    text_l[3][1] = 73;
    text_l[4][1] = 87;
    text_l[5][1] = 101;
    text_l[6][1] = 115;
    text_l[7][1] = 129;
    for n in 0..text_l.len() { text_l[n][0] = 45 };

    Text::with_alignment(
        "touch  jack".into(),
        Point::new((x+45-15) as i32, (y+15+5) as i32),
        font_small_white,
        Alignment::Right
    ).draw(d)?;

    for n in 0..text_l.len() {
        Text::with_alignment(
            str_l[n],
            Point::new((x+text_l[n][0]-6) as i32, (y+text_l[n][1]+5) as i32),
            font_small_grey,
            Alignment::Right
        ).draw(d)?;
    }

    let mut text_r = [[0u32; 2]; 6];
    text_r[0][1] = 33;
    text_r[1][1] = 55;
    text_r[2][1] = 69;
    text_r[3][1] = 90;
    text_r[4][1] = 111;
    text_r[5][1] = 129;
    for n in 0..text_r.len() { text_r[n][0] = 115 };

    for n in 0..text_r.len() {
        Text::with_alignment(
            str_r[n],
            Point::new((x+text_r[n][0]+7) as i32, (y+text_r[n][1]+3) as i32),
            font_small_grey,
            Alignment::Left
        ).draw(d)?;
    }

    Text::with_alignment(
        text_title,
        Point::new((x + 80) as i32, (y-10) as i32),
        font_small_white,
        Alignment::Center
    ).draw(d)?;

    Text::with_alignment(
        text_desc,
        Point::new((x - 120) as i32, (y + 180) as i32),
        font_small_grey,
        Alignment::Left
    ).draw(d)?;


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
        draw_options(&mut disp, &opts, H_ACTIVE/2-30, 70, 0).ok();

        let n_voices = 8;
        for n in 0..n_voices {
            draw_voice(&mut disp,
                       ((H_ACTIVE as f32)/2.0f32 + 250.0f32*f32::cos(2.3f32 + 2.0f32 * n as f32 / 8.0f32)) as i32,
                       ((V_ACTIVE as f32)/2.0f32 + 250.0f32*f32::sin(2.3f32 + 2.0f32 * n as f32 / 8.0f32)) as u32,
                       12, 127, 0).ok();
        }

        draw_tiliqua(&mut disp, H_ACTIVE/2-80, V_ACTIVE/2-200, 0,
            [
            //  "touch  jack "
                "C0     phase",
                "G0     -    ",
                "E0     -    ",
                "D0     -    ",
                "E0     -    ",
                "F0     -    ",
                "-      out L",
                "-      out R",
            ],
            [
                "menu",
                "-",
                "video",
                "-",
                "-",
                "midi notes (+mod, +pitch)",
            ],
            "[8-voice polyphonic synthesizer]",
            "The synthesizer can be controlled by touching\n\
            jacks 0-5 or using a MIDI keyboard through TRS\n\
            midi. Control source is selected in the menu.\n\
            \n\
            In touch mode, the touch magnitude controls the\n\
            filter envelopes of each voice. In MIDI mode\n\
            the velocity of each note as well as the value\n\
            of the modulation wheel affects the filter\n\
            envelopes.\n\
            \n\
            Output audio is sent to output channels 2 and\n\
            3 (last 2 jacks). Input jack 0 also controls\n\
            phase modulation of all oscillators, so you\n\
            can patch input jack 0 to an LFO for retro-sounding\n\
            slow vibrato, or to an oscillator for some wierd\n\
            FM effects.\n\
            ",
            ).ok();

        draw_name(&mut disp, H_ACTIVE/2, 30, 0, "MACRO-OSC", "b2d3aa").ok();

        disp.img.save("draw_opt_test.png").unwrap();
    }

}
