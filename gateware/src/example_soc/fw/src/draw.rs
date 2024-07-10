use heapless::String;

use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    primitives::{PrimitiveStyle, PrimitiveStyleBuilder, Rectangle, Line, Polyline},
    mono_font::{ascii::FONT_4X6, ascii::FONT_5X7, MonoTextStyle},
    prelude::*,
    text::{Alignment, Text, renderer::TextRenderer},
};

use crate::opt;

fn draw_title_box<D>(d: &mut D, title: &String<16>, top_left: Point, size: Size) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Gray8>,
{
    let title_y = 10u32;
    let font_height = 7i32;

    let character_style_h = MonoTextStyle::new(&FONT_5X7, Gray8::WHITE);
    let thin_stroke = PrimitiveStyle::with_stroke(Gray8::WHITE, 1);
    let thin_stroke_grey = PrimitiveStyleBuilder::new()
        .stroke_color(Gray8::new(0x3))
        .stroke_width(1)
        .build();

    // Outer box
    Rectangle::new(top_left, size)
        .into_styled(thin_stroke_grey)
        .draw(d)?;

    // Title box
    Rectangle::new(top_left, Size::new(size.width, title_y))
        .into_styled(thin_stroke)
        .draw(d)?;

    // Channel title
    Text::with_alignment(
        title,
        Point::new(top_left.x + (size.width as i32)/2, top_left.y + font_height),
        character_style_h,
        Alignment::Center,
    )
    .draw(d)?;

    Ok(())
}

pub fn draw_options<D>(d: &mut D, opts: &opt::Options) -> Result<(), D::Error>
where
    D: DrawTarget<Color = Gray8>,
{
    let font_small_white = MonoTextStyle::new(&FONT_4X6, Gray8::WHITE);
    let font_small_grey = MonoTextStyle::new(&FONT_4X6, Gray8::new(0xDF));

    let opts_view = opts.view().options();

    let vx: i32 = 128+64;
    let vy: usize = 17;

    let screen_hl = match (opts.view().selected(), opts.modify) {
        (None, _) => true,
        _ => false,
    };

    draw_title_box(d, &String::new(), Point::new(vx, (vy-17) as i32), Size::new(64, 64))?;

    Text::with_alignment(
        opts.screen.value.into(),
        Point::new(vx+40, (vy-10) as i32),
        if screen_hl { font_small_white } else { font_small_grey },
        Alignment::Left
    ).draw(d)?;

    Text::with_alignment(
        "OPTIONS: ",
        Point::new(vx+4, (vy-10) as i32),
        if screen_hl { font_small_white } else { font_small_grey },
        Alignment::Left
    ).draw(d)?;

    let vx = vx-2;

    for (n, opt) in opts_view.iter().enumerate() {
        let mut font = font_small_grey;
        if let Some(n_selected) = opts.view().selected() {
            if n_selected == n {
                font = font_small_white;
                if opts.modify {
                    Text::with_alignment(
                        "-",
                        Point::new(vx+62, (vy+7*n) as i32),
                        font,
                        Alignment::Left,
                    ).draw(d)?;
                }
            }
        }
        Text::with_alignment(
            opt.name(),
            Point::new(vx+5, (vy+7*n) as i32),
            font,
            Alignment::Left,
        ).draw(d)?;
        Text::with_alignment(
            &opt.value(),
            Point::new(vx+60, (vy+7*n) as i32),
            font,
            Alignment::Right,
        ).draw(d)?;
    }

    Ok(())
}
