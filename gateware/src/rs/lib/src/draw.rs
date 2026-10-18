use tiliqua_hal::embedded_graphics::{
    primitives::{PrimitiveStyleBuilder, Line, Ellipse, Rectangle, Circle},
    mono_font::{ascii::FONT_9X15, ascii::FONT_9X15_BOLD, MonoTextStyle},
    text::{Alignment, Text},
    prelude::*,
};

use crate::color::HI8;

use opts::Options;
use crate::logo_coords;

use heapless::String;
use core::fmt::Write;
use fastrand::Rng;

pub fn draw_options<D, O>(d: &mut D, opts: &O,
                       pos_x: u32, pos_y: u32, hue: u8) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
    O: Options
{
    let font_small_white = MonoTextStyle::new(&FONT_9X15_BOLD, HI8::new(hue, 15));
    let font_small_grey = MonoTextStyle::new(&FONT_9X15, HI8::new(hue, 10));

    let opts_view = opts.view().options();

    let vx = pos_x as i32;
    let vy = pos_y as usize;
    let vspace: usize = 18;
    let hspace: i32 = 150;

    let screen_hl = opts.selected().is_none();

    Text::with_alignment(
        &opts.page().value(),
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
        if let Some(n_selected) = opts.selected() {
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
        .stroke_color(HI8::new(hue, 10))
        .stroke_width(1)
        .build();
    Line::new(Point::new(vx-3, vy as i32 - 10),
              Point::new(vx-3, (vy - 13 + vspace*opts_view.len()) as i32))
              .into_styled(stroke)
              .draw(d)?;

    Ok(())
}

const NOTE_NAMES: [&str; 12] = [
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
    D: DrawTarget<Color = HI8>,
{
    let font_small_white = MonoTextStyle::new(&FONT_9X15, HI8::new(hue, 15));


    let mut stroke_gain = PrimitiveStyleBuilder::new()
        .stroke_color(HI8::new(0, 1))
        .stroke_width(1)
        .build();


    let mut s: String<16> = String::new();

    if cutoff > 0 {
        midi_note_name(&mut s, note);
        stroke_gain = PrimitiveStyleBuilder::new()
            .stroke_color(HI8::new(hue, 10))
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
    let filter_pos: i32 = ((filter_w as f32) * (f32::from(cutoff) / 256.0f32)) as i32;

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

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum AdsrPhase {
    Attack,
    Decay,
    Sustain,
    Release,
}

#[expect(clippy::too_many_arguments)]
pub fn draw_adsr<D>(d: &mut D, x: u32, y: u32, width: u32, height: u32,
                    attack: u16, decay: u16, sustain: u16, release: u16,
                    hue: u8, highlight: Option<AdsrPhase>) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    let font_dim = MonoTextStyle::new(&FONT_9X15, HI8::new(hue, 10));
    let font_bright = MonoTextStyle::new(&FONT_9X15_BOLD, HI8::new(hue, 15));

    // Convert UI values (0..32768) to milliseconds (1..2000)
    let a_ms = 1.0f32 + (f32::from(attack) / 32768.0f32) * 1999.0f32;
    let d_ms = 1.0f32 + (f32::from(decay) / 32768.0f32) * 1999.0f32;
    let s_ms = 1000.0f32; // fixed sustain section
    let r_ms = 1.0f32 + (f32::from(release) / 32768.0f32) * 1999.0f32;

    let total_ms = a_ms + d_ms + s_ms + r_ms;
    let w = width as f32;

    let a_w = (a_ms / total_ms * w) as i32;
    let d_w = (d_ms / total_ms * w) as i32;
    let s_w = (s_ms / total_ms * w) as i32;
    let r_w = width as i32 - a_w - d_w - s_w; // remainder avoids rounding gaps

    let s_level = (u32::from(sustain) * height / 32768) as i32;
    let h = height as i32;
    let x = x as i32;
    let y = y as i32;

    // Envelope vertices
    let p0 = Point::new(x, y + h);
    let p1 = Point::new(x + a_w, y);
    let p2 = Point::new(x + a_w + d_w, y + h - s_level);
    let p3 = Point::new(x + a_w + d_w + s_w, y + h - s_level);
    let p4 = Point::new(x + a_w + d_w + s_w + r_w, y + h);

    let stroke = PrimitiveStyleBuilder::new()
        .stroke_color(HI8::new(hue, 15))
        .stroke_width(1)
        .build();

    let stroke_dim = PrimitiveStyleBuilder::new()
        .stroke_color(HI8::new(hue, 6))
        .stroke_width(1)
        .build();

    // Baseline
    Line::new(Point::new(x, y + h), Point::new(x + width as i32, y + h))
        .into_styled(stroke_dim).draw(d)?;

    // Section separators
    for sep_x in [p1.x, p2.x, p3.x] {
        Line::new(Point::new(sep_x, y), Point::new(sep_x, y + h))
            .into_styled(stroke_dim).draw(d)?;
    }

    // Envelope lines
    Line::new(p0, p1).into_styled(stroke).draw(d)?;
    Line::new(p1, p2).into_styled(stroke).draw(d)?;
    Line::new(p2, p3).into_styled(stroke).draw(d)?;
    Line::new(p3, p4).into_styled(stroke).draw(d)?;

    // Section labels
    let label_y = y + h + 14;
    let font = |phase| if highlight == Some(phase) { font_bright } else { font_dim };
    Text::with_alignment("A", Point::new(x + a_w / 2, label_y),
        font(AdsrPhase::Attack), Alignment::Center).draw(d)?;
    Text::with_alignment("D", Point::new(x + a_w + d_w / 2, label_y),
        font(AdsrPhase::Decay), Alignment::Center).draw(d)?;
    Text::with_alignment("S", Point::new(x + a_w + d_w + s_w / 2, label_y),
        font(AdsrPhase::Sustain), Alignment::Center).draw(d)?;
    Text::with_alignment("R", Point::new(x + a_w + d_w + s_w + r_w / 2, label_y),
        font(AdsrPhase::Release), Alignment::Center).draw(d)?;

    Ok(())
}

pub fn draw_waveform_preview<D>(d: &mut D, x: u32, y: u32, width: u32, height: u32,
                                hue: u8, samples: &[i16]) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    let stroke = PrimitiveStyleBuilder::new()
        .stroke_color(HI8::new(hue, 15))
        .stroke_width(1)
        .build();

    let stroke_dim = PrimitiveStyleBuilder::new()
        .stroke_color(HI8::new(hue, 6))
        .stroke_width(1)
        .build();

    let h = height as i32;
    let half_h = h / 2;
    let x = x as i32;
    let y = y as i32;
    let center_y = y + half_h;

    // Baseline
    Line::new(Point::new(x, center_y), Point::new(x + width as i32, center_y))
        .into_styled(stroke_dim).draw(d)?;

    let n = samples.len();
    if n < 2 {
        return Ok(());
    }

    for i in 1..n {
        let x0 = x + (i - 1) as i32 * width as i32 / (n - 1) as i32;
        let x1 = x + i as i32 * width as i32 / (n - 1) as i32;
        let y0 = center_y - (i32::from(samples[i - 1]) * half_h / 32767);
        let y1 = center_y - (i32::from(samples[i]) * half_h / 32767);
        Line::new(Point::new(x0, y0), Point::new(x1, y1))
            .into_styled(stroke).draw(d)?;
    }

    Ok(())
}

pub fn draw_boot_logo<D>(d: &mut D, sx: i32, sy: i32, ix: u32) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    use logo_coords::BOOT_LOGO_COORDS;
    let stroke_white = PrimitiveStyleBuilder::new()
        .stroke_color(HI8::WHITE)
        .stroke_width(1)
        .build();
    let p = ((ix % ((BOOT_LOGO_COORDS.len() as u32)-1)) + 1) as usize;
    let x = BOOT_LOGO_COORDS[p].0/2;
    let y = -BOOT_LOGO_COORDS[p].1/2;
    let xl = BOOT_LOGO_COORDS[p-1].0/2;
    let yl = -BOOT_LOGO_COORDS[p-1].1/2;
    Line::new(Point::new(sx+i32::from(xl), sy+i32::from(yl)),
              Point::new(sx+i32::from(x), sy+i32::from(y)))
              .into_styled(stroke_white)
              .draw(d)?;
    Ok(())
}

use tiliqua_hal::dma_framebuffer::DVIModeline;
pub fn draw_name<D>(d: &mut D, pos_x: u32, pos_y: u32, hue: u8, name: &str, tag: &str, modeline: &DVIModeline) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    let font_small_white = MonoTextStyle::new(&FONT_9X15_BOLD, HI8::new(hue, 15));
    let font_small_grey = MonoTextStyle::new(&FONT_9X15, HI8::new(hue, 10));

    Text::with_alignment(
        name,
        Point::new(pos_x as i32, pos_y as i32),
        font_small_white,
        Alignment::Center
    ).draw(d)?;

    let mut modeline_text: String<32> = String::new();
    if modeline.fixed() {
        // Fixed modeline doesn't have all the info needed to calculate refresh rate.
        write!(modeline_text, "{}/{}x{}(fxd)\r\n",
               tag, modeline.h_active, modeline.v_active
               ).ok();
    } else {
        write!(modeline_text, "{}/{}x{}@{:.1}Hz\r\n",
               tag,
               modeline.h_active, modeline.v_active, modeline.refresh_rate()
               ).ok();
    }

    Text::with_alignment(
        &modeline_text,
        Point::new(pos_x as i32, (pos_y + 18) as i32),
        font_small_grey,
        Alignment::Center
    ).draw(d)?;

    Ok(())
}

pub fn draw_help<D>(d: &mut D, x: u32, y: u32, scroll: u8, help_text: &str, hue: u8) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    // Draw a sliding window of a long multiline docstring.
    //
    // 2 fonts are used here. A larger one for text, and a smaller one with
    // some unicode box drawing characters for flowcharts / diagrams.
    //
    // The goal is for flowcharts to be small enough to fit on the screen,
    // and for the rest of the text to still be readable.
    //
    // Be careful that the unicode font fits inside the blitter peripheral's
    // sprite memory, otherwise the HAL falls back to per-pixel drawing, which
    // is massively slower.

    use crate::mono_6x12_optimized::MONO_6X12_OPTIMIZED;
    use tiliqua_hal::embedded_graphics::mono_font::ascii::FONT_7X13;

    let font_normal = MonoTextStyle::new(&FONT_7X13, HI8::new(hue, 10));
    let font_small = MonoTextStyle::new(&MONO_6X12_OPTIMIZED, HI8::new(hue, 10));
    let font_small_white = MonoTextStyle::new(&MONO_6X12_OPTIMIZED, HI8::new(hue, 15));

    let skip_lines = scroll as usize;
    let line_spacing_normal = 13;  // Spacing for FONT_8X13
    let line_spacing_small = 12;   // Spacing for MONO_6X12_OPTIMIZED
    let max_visible_lines = 28;

    let lines_iter = help_text.lines();

    // Skip to the starting line
    let mut lines_iter = lines_iter.skip(skip_lines);

    let mut current_y = y;

    for _i in 0..max_visible_lines {
        if let Some(line) = lines_iter.next() {
            // Dumb heuristic for sphinx `.. note::` or `.. text::` blocks --
            // - Lines indented 8+ spaces
            // - Lines that start with indent + ".."
            // Small font is selected for these blocks.
            let leading_spaces = line.len() - line.trim_start().len();
            let trimmed = line.trim_start();
            let use_small_font = leading_spaces >= 8 ||
                                 (leading_spaces > 0 && trimmed.starts_with(".."));

            let (font, line_spacing) = if use_small_font {
                (font_small, line_spacing_small)
            } else {
                (font_normal, line_spacing_normal)
            };

            Text::new(
                line,
                Point::new(x as i32, current_y as i32),
                font,
            ).draw(d)?;

            current_y += line_spacing;
        } else {
            break;
        }
    }

    let has_lines_above = skip_lines > 0;
    let has_lines_below = lines_iter.next().is_some();

    let text_width = 80 * 7;
    let arrow_x = x + (text_width / 2);

    let stroke = PrimitiveStyleBuilder::new()
        .stroke_color(HI8::new(hue, 10))
        .stroke_width(1)
        .build();

    // If there is unseen text, show an up or down arrow like -- ^ --
    // at top and bottom of the sliding window.

    if has_lines_above {
        let arrow_y = y.saturating_sub(line_spacing_small);
        Text::with_alignment(
            "▴",
            Point::new(arrow_x as i32, arrow_y as i32),
            font_small_white,
            Alignment::Center,
        ).draw(d)?;
        Line::new(
            Point::new((arrow_x - 60) as i32, arrow_y as i32 - 3),
            Point::new((arrow_x - 10) as i32, arrow_y as i32 - 3)
        ).into_styled(stroke).draw(d)?;
        Line::new(
            Point::new((arrow_x + 10) as i32, arrow_y as i32 - 3),
            Point::new((arrow_x + 60) as i32, arrow_y as i32 - 3)
        ).into_styled(stroke).draw(d)?;
    }

    if has_lines_below {
        let arrow_y = y + 13*max_visible_lines-8;
        Text::with_alignment(
            "▾",
            Point::new(arrow_x as i32, arrow_y as i32),
            font_small_white,
            Alignment::Center,
        ).draw(d)?;
        Line::new(
            Point::new((arrow_x - 60) as i32, arrow_y as i32 - 2),
            Point::new((arrow_x - 10) as i32, arrow_y as i32 - 2)
        ).into_styled(stroke).draw(d)?;
        Line::new(
            Point::new((arrow_x + 10) as i32, arrow_y as i32 - 2),
            Point::new((arrow_x + 60) as i32, arrow_y as i32 - 2)
        ).into_styled(stroke).draw(d)?;
    }

    Ok(())
}

pub fn draw_help_page<D>(
    d: &mut D,
    help_text: &str,
    manifest_help: Option<&tiliqua_manifest::BitstreamHelp>,
    h_active: u32,
    v_active: u32,
    scroll: u8,
    hue: u8,
) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    // Draw the sliding help window with the 'mini tiliqua' above it that
    // shows the IO mapping.
    draw_help(d, h_active/2-280, v_active/2-150, scroll, help_text, hue)?;
    if let Some(help) = manifest_help {
        draw_tiliqua(
            d,
            (h_active/2-80) as i32,
            (v_active/2) as i32 - 330,
            hue,
            help.io_left.each_ref().map(heapless::String::as_str),
            help.io_right.each_ref().map(heapless::String::as_str)
        )?;
    }
    Ok(())
}

pub fn draw_cal<D>(d: &mut D, x: u32, y: u32, hue: u8, dac: &[i32; 4], adc: &[i32; 4], counts_per_v: i32) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    let font_small_white = MonoTextStyle::new(&FONT_9X15_BOLD, HI8::new(hue, 15));
    let font_small_grey = MonoTextStyle::new(&FONT_9X15, HI8::new(hue, 10));
    let stroke_grey = PrimitiveStyleBuilder::new()
           .stroke_color(HI8::new(hue, 10))
           .stroke_width(1)
           .build();
    let stroke_white = PrimitiveStyleBuilder::new()
           .stroke_color(HI8::new(hue, 15))
           .stroke_width(2)
           .build();

    let line = |disp: &mut D, x1: u32, y1: u32, x2: u32, y2: u32, hl: bool| {
        Line::new(Point::new((x+x1) as i32, (y+y1) as i32),
                  Point::new((x+x2) as i32, (y+y2) as i32))
                  .into_styled(if hl { stroke_white } else { stroke_grey } )
                  .draw(disp).ok()
    };

    let spacing = 30;
    let s_y     = spacing;
    let width   = 256;

    for ch in 0..4 {
        line(d, 0, s_y+ch*spacing, width, s_y+ch*spacing, false);
        line(d, 0, ch*spacing+s_y/2, 0, s_y+ch*spacing, false);
        line(d, width, ch*spacing+s_y/2, width, s_y+ch*spacing, false);
        line(d, width/2, ch*spacing+s_y-spacing/2, width/2, s_y+ch*spacing, false);
        let counts_per_mv = counts_per_v / 1000;
        let delta = (adc[ch as usize] - dac[ch as usize]) / counts_per_mv;
        if delta.abs() < (width/2) as i32 {
            let pos = (delta + (width/2) as i32) as u32;
            line(d, pos, ch*spacing+s_y-spacing/4, pos, s_y+ch*spacing, true);
        }

        let mut adc_text: String<8> = String::new();
        write!(adc_text, "{}", adc[ch as usize]/counts_per_mv).ok();
        Text::with_alignment(
            &adc_text,
            Point::new((x-10) as i32, (y+(ch+1)*spacing-3) as i32),
            font_small_grey,
            Alignment::Right
        ).draw(d)?;

        let mut dac_text: String<8> = String::new();
        write!(dac_text, "{}", dac[ch as usize]/counts_per_mv).ok();
        Text::with_alignment(
            &dac_text,
            Point::new((x+width+10) as i32, (y+(ch+1)*spacing-3) as i32),
            font_small_grey,
            Alignment::Left
        ).draw(d)?;
    }

    Text::with_alignment(
        "in (ADC mV)             delta           ref (DAC mV)",
        Point::new((x+width/2) as i32, y as i32),
        font_small_white,
        Alignment::Center
    ).draw(d)?;

    Text::with_alignment(
        "-128mV                     128mV",
        Point::new((x+width/2) as i32, (y+spacing*5-10) as i32),
        font_small_grey,
        Alignment::Center
    ).draw(d)?;

    Ok(())
}

#[expect(clippy::too_many_arguments)]
pub fn draw_cal_constants<D>(
    d: &mut D, x: u32, y: u32, hue: u8,
    adc_scale: &[i32; 4],
    adc_zero:  &[i32; 4],
    dac_scale: &[i32; 4],
    dac_zero:  &[i32; 4],
    f_bits: u8,
    ) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    let font_small_grey = MonoTextStyle::new(&FONT_9X15, HI8::new(hue, 10));

    let spacing = 30;
    let width   = 256;
    let divisor = (1u32 << f_bits) as f32;

    for ch in 0..4 {
        let mut s: String<32> = String::new();
        write!(s, "O{} = {:.4} * o{} + {:.4}",
              ch,
              dac_scale[ch as usize] as f32 / divisor,
              ch,
              dac_zero[ch as usize] as f32 / divisor).ok();
        Text::with_alignment(
            &s,
            Point::new((x+width/2+20) as i32, (y+(ch+1)*spacing-3) as i32),
            font_small_grey,
            Alignment::Left
        ).draw(d)?;
    }

    for ch in 0..4 {
        let mut s: String<32> = String::new();
        write!(s, "i{} = {:.4} * I{} + {:.4}",
              ch,
              adc_scale[ch as usize] as f32 / divisor,
              ch,
              adc_zero[ch as usize] as f32 / divisor).ok();
        Text::with_alignment(
            &s,
            Point::new((x+width/2-20) as i32, (y+(ch+1)*spacing-3) as i32),
            font_small_grey,
            Alignment::Right
        ).draw(d)?;
    }

    Ok(())
}

pub fn draw_tiliqua<D>(d: &mut D, x: i32, y: i32, hue: u8,
                       str_l: [&str; 8], str_r: [&str; 6]) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
     let stroke_grey = PrimitiveStyleBuilder::new()
            .stroke_color(HI8::new(hue, 10))
            .stroke_width(1)
            .build();

    let font_small_grey = MonoTextStyle::new(&FONT_9X15, HI8::new(hue, 10));

    let line = |disp: &mut D, x1: i32, y1: i32, x2: i32, y2: i32| {
        Line::new(Point::new(x+x1, y+y1),
                  Point::new(x+x2, y+y2))
                  .into_styled(stroke_grey)
                  .draw(disp).ok()
    };

    let ellipse = |disp: &mut D, x1: i32, y1: i32, sx: u32, sy: u32| {
        Ellipse::new(Point::new(x+x1-sx as i32, y+y1-sy as i32),
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
    for t in &mut text_l { t[0] = 45 };

    for n in 0..text_l.len() {
        Text::with_alignment(
            str_l[n],
            Point::new(x + text_l[n][0] as i32 - 6, y + text_l[n][1] as i32 + 5),
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
    for t in &mut text_r { t[0] = 115 };

    for n in 0..text_r.len() {
        Text::with_alignment(
            str_r[n],
            Point::new(x + text_r[n][0] as i32 + 7, y + text_r[n][1] as i32 + 3),
            font_small_grey,
            Alignment::Left
        ).draw(d)?;
    }

    Ok(())
}

#[expect(clippy::too_many_arguments)]
pub fn draw_sid<D>(d: &mut D, x: u32, y: u32, hue: u8,
                   wfm:    Option<u8>,
                   gates:  [bool; 3],
                   filter: bool,
                   switches: [bool; 3],
                   filter_types: [bool; 3],
                   ) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
     let stroke_grey = PrimitiveStyleBuilder::new()
            .stroke_color(HI8::new(hue, 11))
            .stroke_width(1)
            .build();

     let stroke_white = PrimitiveStyleBuilder::new()
            .stroke_color(HI8::new(hue, 15))
            .stroke_width(1)
            .build();

    let line = |disp: &mut D, x1: u32, y1: u32, x2: u32, y2: u32, hl: bool| {
        Line::new(Point::new((x+x1) as i32, (y+y1) as i32),
                  Point::new((x+x2) as i32, (y+y2) as i32))
                  .into_styled(if hl { stroke_white } else { stroke_grey } )
                  .draw(disp).ok()
    };

    let rect = |disp: &mut D, x1: u32, y1: u32, sx: u32, sy: u32, hl: bool| {
        Rectangle::new(Point::new((x+x1) as i32, (y+y1) as i32),
                       Size::new(sx, sy))
                       .into_styled(if hl { stroke_white } else { stroke_grey } )
                       .draw(disp).ok()
    };

    let circle = |disp: &mut D, x1: u32, y1: u32, radius: u32| {
        Circle::new(Point::new((x+x1-radius) as i32, (y+y1-radius) as i32), radius*2+1)
                    .into_styled(stroke_grey)
                    .draw(disp).ok()
    };

    let font_small_white = MonoTextStyle::new(&FONT_9X15_BOLD, HI8::new(hue, 11));
    Text::new(
        "MOS 6581",
        Point::new((x+20) as i32, (y-10) as i32),
        font_small_white,
    )
    .draw(d)?;

    let spacing = 32;
    for n in 0..3 {
        let ys = n * spacing;

        // wiring
        circle(d, 51, 10+ys, 8);
        line(d,   33, 10+ys, 42, 10+ys, false);
        line(d,   32, 26+ys, 50, 26+ys, false);
        line(d,   51, 19+ys, 51, 26+ys, false);
        line(d,   46, 5+ys,  56, 15+ys, false);
        line(d,   46, 15+ys, 56, 5+ys,  false);
        line(d,   60, 10+ys, 69, 10+ys, false);

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

// Helper to draw waveform peaks at a certain position given
// an array of samples. No effort made to compute absolute magnitude
// based on adjacent peaks, but this seems to look fine.
pub fn draw_waveform_peaks<D>(
    d: &mut D,
    x: u32, y: u32,
    width: u32, height: u32,
    hue: u8,
    samples: &[i16],
) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    let stroke = PrimitiveStyleBuilder::new()
        .stroke_color(HI8::new(hue, 12))
        .stroke_width(1)
        .build();
    let center_y = y + height / 2;
    let half_height = (height / 2) as i32;
    let sample_width = if samples.is_empty() { 1 } else { width / samples.len() as u32 };
    for (i, &sample) in samples.iter().enumerate() {
        let x_pos = x + (i as u32 * sample_width);
        let scaled = (i32::from(sample) * half_height) / 32768;
        let y_top = center_y as i32 - scaled.abs();
        let y_bot = center_y as i32 + scaled.abs();
        Line::new(
            Point::new(x_pos as i32, y_top),
            Point::new(x_pos as i32, y_bot)
        ).into_styled(stroke).draw(d)?;
    }

    Ok(())
}

// Like `draw_waveform`, but connecting lines instead of 'peak' bars.
// This is more useful for plotting CV. It's currently only used in the
// sampler bitstream.
pub fn draw_waveform_lines<D>(
    d: &mut D,
    x: u32, y: u32,
    width: u32, height: u32,
    hue: u8,
    samples: &[i16],
) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    let stroke = PrimitiveStyleBuilder::new()
        .stroke_color(HI8::new(hue, 12))
        .stroke_width(1)
        .build();
    let center_y = y as i32 + height as i32 / 2;
    let half_height = height as i32 / 2;
    let sample_width = if samples.is_empty() { 1 } else { width / samples.len() as u32 };
    for i in 1..samples.len() {
        let x0 = x as i32 + ((i - 1) as i32 * sample_width as i32);
        let x1 = x as i32 + (i as i32 * sample_width as i32);
        let y0 = center_y - (i32::from(samples[i - 1]) * half_height) / 32768;
        let y1 = center_y - (i32::from(samples[i]) * half_height) / 32768;
        Line::new(
            Point::new(x0, y0),
            Point::new(x1, y1)
        ).into_styled(stroke).draw(d)?;
    }

    Ok(())
}

// Single vertical line useful for position marks.
pub fn draw_vline<D>(
    d: &mut D,
    x: u32, y: u32,
    height: u32,
    hue: u8,
    intensity: u8,
) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    let stroke = PrimitiveStyleBuilder::new()
        .stroke_color(HI8::new(hue, intensity))
        .stroke_width(1)
        .build();
    Line::new(
        Point::new(x as i32, y as i32),
        Point::new(x as i32, (y + height) as i32)
    ).into_styled(stroke).draw(d)?;
    Ok(())
}

pub fn draw_benchmark_lines<D>(
    d: &mut D, count: u32, rng: &mut Rng) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    let size = d.bounding_box().size;
    let stroke = PrimitiveStyleBuilder::new()
        .stroke_color(HI8::WHITE.with_hue_offset(rng.u8(0..16)))
        .stroke_width(1)
        .build();
    for _ in 0..count {
        let x1 = rng.u32(0..size.width);
        let y1 = rng.u32(0..size.height);
        let x2 = rng.u32(0..size.width);
        let y2 = rng.u32(0..size.height);
        Line::new(Point::new(x1 as i32, y1 as i32),
                  Point::new(x2 as i32, y2 as i32))
                  .into_styled(stroke)
                  .draw(d)?;
    }
    Ok(())
}

pub fn draw_benchmark_text<D>(
    d: &mut D, count: u32, rng: &mut Rng) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    const STRINGS: &[&str] = &[
        "SYNTHESIZER", "OSCILLATOR", "FILTER", "1234567890", "ADSR",
        "RESONANCE", "ENVELOPE", "LFO", "REVERB", "DELAY",
        "FPGA", "DSP", "CODEC", "BITSTREAM", "AMARANTH", "~!@#$%^&*()_+",
    ];
    let size = d.bounding_box().size;
    let font_style = MonoTextStyle::new(
        &FONT_9X15, HI8::WHITE.with_hue_offset(rng.u8(0..16)));
    for _ in 0..count {
        let x = rng.u32(0..size.width.saturating_sub(80)) as i32;
        let y = rng.u32(15..size.height) as i32;
        let text = STRINGS[rng.usize(0..STRINGS.len())];
        Text::new(text, Point::new(x, y), font_style).draw(d)?;
    }
    Ok(())
}

pub fn draw_benchmark_pixels<D>(
    d: &mut D, count: u32, rng: &mut Rng) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    let size = d.bounding_box().size;
    let color = HI8::WHITE.with_hue_offset(rng.u8(0..16));
    for _ in 0..count {
        let x = rng.u32(0..size.width);
        let y = rng.u32(0..size.height);
        d.draw_iter([Pixel(Point::new(x as i32, y as i32), color)])?;
    }
    Ok(())
}

pub fn draw_benchmark_stats<D>(d: &mut D, pos_x: u32, pos_y: u32, hue: u8,
                              refresh_rate: u32, frame_count: u32) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    let font_white = MonoTextStyle::new(&FONT_9X15_BOLD, HI8::new(hue, 15));

    let mut refresh_text: String<32> = String::new();
    write!(refresh_text, "refresh: {refresh_rate}Hz").ok();
    Text::new(
        &refresh_text,
        Point::new(pos_x as i32, (pos_y + 20) as i32),
        font_white,
    ).draw(d)?;

    let mut frame_text: String<32> = String::new();
    write!(frame_text, "ops/sec: {frame_count}").ok();
    Text::new(
        &frame_text,
        Point::new(pos_x as i32, (pos_y + 40) as i32),
        font_white,
    ).draw(d)?;

    Ok(())
}

pub fn draw_benchmark_unicode<D>(
    d: &mut D, count: u32, rng: &mut Rng) -> Result<(), D::Error>
where
    D: DrawTarget<Color = HI8>,
{
    use crate::mono_6x12_optimized::MONO_6X12_OPTIMIZED;

    let font_unicode = MonoTextStyle::new(&MONO_6X12_OPTIMIZED, HI8::BLUE);

    let unicode_text = "\
in0/x ───────►┌───────┐
in1/y ───────►│Audio  │
in2/i ───────►│IN (4x)│
in3/c ───────►└───┬───┘
                  ▼
         ┌───◄─[SPLIT]─►────┐
         │        │         ▼
         │        ▼  ┌──────────────┐     ┌────────┐
         │        │  │4in/4out USB  ├────►│Computer│
         │        │  │Audio I/F     │◄────│(USB2)  │
         │        │  └──────┬───────┘     └────────┘
         │        └───┐ ┌───┘
         │ usb=bypass ▼ ▼ usb=enabled
         │           [MUX]
         │      ┌──────────────┐
         │      │4x Delay Lines│ (tunable)
         │      └──────┬───────┘
         │             ▼
         └────┐ ┌─◄─[SPLIT]─►────┐
              │ │                │
   src=inputs ▼ ▼ src=outputs    │
             [MUX]               │
               │                 ▼
         ┌─────▼──────┐     ┌────────┬──────► out0
(select w│Vectorscope/│     │Audio   ├──────► out1
plot_mode│Oscilloscope│     │OUT (4x)├──────► out2
         └────────────┘     └────────┴──────► out3";

    let size = d.bounding_box().size;
    for _ in 0..count {
        let x = rng.u32(0..size.width);
        let y = rng.u32(0..size.height);
        Text::new(
            unicode_text,
            Point::new(x as i32, y as i32),
            font_unicode,
        ).draw(d)?;
    }

    Ok(())
}


#[cfg(test)]
mod test_data {
    use opts::*;
    use crate::palette;
    use strum::{EnumIter, IntoStaticStr};
    use serde_derive::{Serialize, Deserialize};

    // Fake set of options for quick render testing
    #[derive(Clone, Copy, PartialEq, Eq, EnumIter, IntoStaticStr, Default, Serialize, Deserialize)]
    #[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
    pub enum Page {
        #[default]
        Scope,
    }

    int_params!(PositionParams<i16>     { step: 25,  min: -500,   max: 500 });
    int_params!(ScaleParams<u8>         { step: 1,   min: 0,      max: 15 });

    #[derive(OptionPage, Clone)]
    pub struct ScopeOpts {
        #[option]
        pub ypos0: IntOption<PositionParams>,
        #[option(-150)]
        pub ypos1: IntOption<PositionParams>,
        #[option(7)]
        pub xscale: IntOption<ScaleParams>,
        #[option]
        pub palette: EnumOption<palette::ColorPalette>,
    }

    #[derive(Options, Clone)]
    pub struct Opts {
        pub tracker: ScreenTracker<Page>,
        #[page(Page::Scope)]
        pub scope: ScopeOpts,
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
        type Color = HI8;
        type Error = core::convert::Infallible;

        fn draw_iter<I>(&mut self, pixels: I) -> Result<(), Self::Error>
        where
            I: IntoIterator<Item = Pixel<Self::Color>>,
        {
            for Pixel(coord, color) in pixels {
                if let Ok((x @ 0..=H_ACTIVE, y @ 0..=V_ACTIVE)) = coord.try_into() {
                    let raw = color.to_raw();
                    *self.img.get_pixel_mut(x, y) = Rgb([
                        raw,
                        raw,
                        raw
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

    // Helper function to create a new display with cleared background
    fn setup_display() -> FakeDisplay {
        let mut disp = FakeDisplay {
            img: ImageBuffer::new(H_ACTIVE, V_ACTIVE)
        };
        disp.clear(HI8::BLACK).ok();
        disp
    }

    #[test]
    fn test_draw_title_and_options() {
        use opts::OptionsEncoderInterface;
        let mut disp = setup_display();

        let mut opts = test_data::Opts::default();
        opts.tick_up();
        opts.toggle_modify();
        opts.tick_up();
        opts.toggle_modify();

        draw_name(&mut disp, H_ACTIVE/2, 30, 0, "MACRO-OSC", "b2d3aa", &DVIModeline::default()).ok();
        draw_options(&mut disp, &opts, H_ACTIVE/2-30, 70, 0).ok();
        disp.img.save("draw_options.png").unwrap();
    }

    #[test]
    fn test_draw_voices() {
        let mut disp = setup_display();
        let n_voices = 8;
        for n in 0..n_voices {
            let angle = 2.3f32 + 2.0f32 * n as f32 / 8.0f32;
            let x = ((H_ACTIVE as f32)/2.0f32 + 250.0f32 * f32::cos(angle)) as i32;
            let y = ((V_ACTIVE as f32)/2.0f32 + 250.0f32 * f32::sin(angle)) as u32;
            draw_voice(&mut disp, x, y, 12, 127, 0).ok();
        }
        disp.img.save("draw_voices.png").unwrap();
    }

    #[test]
    fn test_draw_help() {
        let mut disp = setup_display();

        let connection_labels = [
            "C0     phase",
            "G0     -    ",
            "E0     -    ",
            "D0     -    ",
            "E0     -    ",
            "F0     -    ",
            "-      out L",
            "-      out R",
        ];

        let menu_items = [
            "menu",
            "-",
            "video",
            "-",
            "-",
            "midi notes (+mod, +pitch)",
        ];

        draw_tiliqua(
            &mut disp,
            (H_ACTIVE/2-80) as i32,
            (V_ACTIVE/2-200) as i32,
            0,
            connection_labels,
            menu_items,
        ).ok();
        disp.img.save("draw_help.png").unwrap();
    }

    #[test]
    fn test_draw_calibration() {
        let mut disp = setup_display();

        draw_cal(&mut disp, H_ACTIVE/2-128, V_ACTIVE/2-128, 0,
                 &[4096, 4096, 4096, 4096],
                 &[4000, 4120, 4090, 4000], 4000).ok();
        draw_cal_constants(&mut disp, H_ACTIVE/2-128, V_ACTIVE/2+64, 0,
                 &[4096, 4096, 4096, 4096],
                 &[4000, 4120, 4090, 4000],
                 &[4096, 4096, 4096, 4096],
                 &[4000, 4120, 4090, 4000],
                 15).ok();

        disp.img.save("draw_cal.png").unwrap();
    }

    #[test]
    fn test_draw_unicode() {
        let mut disp = setup_display();
        let mut rng = Rng::with_seed(0);

        draw_benchmark_unicode(&mut disp, 1, &mut rng).ok();

        disp.img.save("draw_unicode.png").unwrap();
    }

    #[test]
    fn test_draw_xbeam_help() {
        const XBEAM_HELP_TEXT: &str = r"
Vectorscope/oscilloscope with menu system, USB audio and tunable delay lines.

    - In **vectorscope mode**, rasterize X/Y, intensity and color to a simulated
      CRT, with adjustable beam settings, scale and offset for each channel.

    - In **oscilloscope mode**, all 4 input channels are plotted simultaneosly
      with adjustable timebase, trigger settings and so on.

The channels are assigned as follows:

    .. code-block:: text

                 Vectorscope │ Oscilloscope
        ┌────┐               │
        │in0 │◄─ x           │ channel 0 + trig
        │in1 │◄─ y           │ channel 1
        │in2 │◄─ intensity   │ channel 2
        │in3 │◄─ color       │ channel 3
        └────┘

A USB audio interface, tunable delay lines, and series of switches is included
in the signal path to open up more applications. The overall signal flow looks
like this:

    .. code-block:: text

        in0/x ───────►┌───────┐
        in1/y ───────►│Audio  │
        in2/i ───────►│IN (4x)│
        in3/c ───────►└───┬───┘
                          ▼
                 ┌───◄─[SPLIT]─►────┐
                 │        │         ▼
                 │        ▼  ┌──────────────┐     ┌────────┐
                 │        │  │4in/4out USB  ├────►│Computer│
                 │        │  │Audio I/F     │◄────│(USB2)  │
                 │        │  └──────┬───────┘     └────────┘
                 │        └───┐ ┌───┘
                 │ usb=bypass ▼ ▼ usb=enabled
                 │           [MUX]
                 │      ┌──────────────┐
                 │      │4x Delay Lines│ (tunable)
                 │      └──────┬───────┘
                 │             ▼
                 └────┐ ┌─◄─[SPLIT]─►────┐
                      │ │                │
           src=inputs ▼ ▼ src=outputs    │
                     [MUX]               │
                       │                 ▼
                 ┌─────▼──────┐     ┌────────┬──────► out0
                 │Vectorscope/│     │Audio   ├──────► out1
                 │Oscilloscope│     │OUT (4x)├──────► out2
                 └────────────┘     └────────┴──────► out3

The ``[MUX]`` elements pictured above can be switched by the menu system, for
viewing different parts of the signal path (i.e inputs or outputs to delay
lines, USB streams).  Some usage ideas:

    - With ``plot_src=inputs`` and ``usb_mode=bypass``, we can visualize our
      analog audio inputs.
    - With ``plot_src=outputs`` and ``usb_mode=bypass``, we can visualize our
      analog audio inputs after being affected by the delay lines (this is fun
      to get patterns out of duplicated mono signals)
    - With ``plot_src=outputs`` and ``usb_mode=enable``, we can visualize a USB
      audio stream as it is sent to the analog outputs. This is perfect for
      visualizing oscilloscope music being streamed from a computer.
    - With ``plot_src=inputs`` and ``usb_mode=enable``, we can visualize what we
      are sending back to the computer on our analog inputs.

    .. note::

        The USB audio interface will always enumerate if it is connected to a
        computer, however it is only part of the signal flow if
        ``usb_mode=enabled`` in the menu system.

    .. note::

        By default, this core builds for ``48kHz/16bit`` sampling.  However,
        Tiliqua is shipped with ``--fs-192khz`` enabled, which provides much
        higher fidelity plots. If you're feeling adventurous, you can also
        synthesize with the environment variable ``TILIQUA_ASQ_WIDTH=24`` to use
        a completely 24-bit audio path.  This mostly works, but might break the
        scope triggering and use a bit more FPGA resources.
";

        let mut disp = setup_display();

        // Test without manifest help (Tiliqua diagram won't be drawn)
        draw_help_page(&mut disp, XBEAM_HELP_TEXT, None, H_ACTIVE, V_ACTIVE, 3, 0).ok();

        draw_name(&mut disp, H_ACTIVE/2, V_ACTIVE-50, 0, "XBEAM", "b2d3aa", &DVIModeline::default()).ok();

        let opts = test_data::Opts::default();
        draw_options(&mut disp, &opts, H_ACTIVE/2-30, V_ACTIVE-135, 0).ok();

        disp.img.save("draw_xbeam_help.png").unwrap();
    }
}
