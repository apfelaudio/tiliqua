#![no_std]
#![no_main]

use tiliqua_pac as pac;
use tiliqua_hal as hal;

use hal::hal::delay::DelayNs;

use tiliqua_fw::Serial0;
use tiliqua_fw::Timer0;
use tiliqua_fw::I2c0;
use tiliqua_fw::Encoder0;
use tiliqua_fw::EurorackPmod0;
use tiliqua_fw::Video0;

use log::info;

use riscv_rt::entry;

use tiliqua_hal::pca9635::*;

use core::convert::TryInto;

use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
};

use tiliqua_fw::opts;
use tiliqua_fw::opts::ColorPalette;
use tiliqua_lib::draw;

use tiliqua_lib::opt::*;

use tiliqua_lib::generated_constants::*;

use micromath::F32Ext;

tiliqua_hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE, VIDEO_ROTATE_90);

const PCA9635_BAR_GREEN: [usize; 6] = [0, 2, 14, 12, 6, 4];
const PCA9635_BAR_RED:   [usize; 6] = [1, 3, 15, 13, 7, 5];
const _PCA9635_MIDI:     [usize; 2] = [8, 9];

fn hue2rgb(p: f32, q: f32, mut t: f32) -> f32 {
    if t < 0.0 {
        t += 1.0;
    }
    if t > 1.0 {
        t -= 1.0;
    }
    if t < 1.0 / 6.0 {
        return p + (q - p) * 6.0 * t;
    }
    if t < 0.5 {
        return q;
    }
    if t < 2.0 / 3.0 {
        return p + (q - p) * (2.0 / 3.0 - t) * 6.0;
    }
    p
}

struct RGB {
    r: u8,
    g: u8,
    b: u8,
}

/// Converts an HSL color value to RGB. Conversion formula
/// adapted from http://en.wikipedia.org/wiki/HSL_color_space.
/// Assumes h, s, and l are contained in the set [0, 1] and
/// returns RGB in the set [0, 255].
fn hsl2rgb(h: f32, s: f32, l: f32) -> RGB {
    if s == 0.0 {
        // achromatic
        let gray = (l * 255.0) as u8;
        return RGB { r: gray, g: gray, b: gray };
    }

    let q = if l < 0.5 {
        l * (1.0 + s)
    } else {
        l + s - l * s
    };
    let p = 2.0 * l - q;

    RGB {
        r: (hue2rgb(p, q, h + 1.0 / 3.0) * 255.0) as u8,
        g: (hue2rgb(p, q, h) * 255.0) as u8,
        b: (hue2rgb(p, q, h - 1.0 / 3.0) * 255.0) as u8,
    }
}

fn write_palette(video: &mut Video0, p: ColorPalette) {
    let n_i = 16i32;
    let n_h = 16i32;
    for i in 0..n_i {
        for h in 0..n_h {
            match p {
                ColorPalette::Exp => {
                    let fac = 1.35f32;
                    let hue = (h as f32)/(n_h as f32);
                    let saturation = 0.9f32;
                    let intensity = fac.powi(i+1) / fac.powi(n_i);
                    let rgb = hsl2rgb(hue, saturation, intensity);
                    video.set_palette_rgb(i as u32, h as u32, rgb.r, rgb.g, rgb.b);
                },
                ColorPalette::Linear => {
                    let rgb = hsl2rgb((h as f32)/(n_h as f32), 0.9f32, (i as f32)/(n_h as f32));
                    video.set_palette_rgb(i as u32, h as u32, rgb.r, rgb.g, rgb.b);
                },
                ColorPalette::Gray => {
                    let gray: u8 = (i * 16) as u8;
                    video.set_palette_rgb(i as u32, h as u32, gray, gray, gray);
                },
                ColorPalette::InvGray => {
                    let gray: u8 = 255u8 - (i * 16) as u8;
                    video.set_palette_rgb(i as u32, h as u32, gray, gray, gray);
                }
            }
        }
    }
}

#[entry]
fn main() -> ! {
    let peripherals = pac::Peripherals::take().unwrap();

    // initialize logging
    let serial = Serial0::new(peripherals.UART);
    tiliqua_fw::handlers::logger_init(serial);

    let sysclk = pac::clock::sysclk();
    let mut timer = Timer0::new(peripherals.TIMER, sysclk);

    info!("Hello from Tiliqua XBEAM!");

    let i2cdev = I2c0::new(peripherals.I2C0);

    let mut pca9635 = Pca9635Driver::new(i2cdev);

    let mut encoder = Encoder0::new(peripherals.ENCODER0);

    let mut display = DMADisplay {
        framebuffer_base: PSRAM_FB_BASE as *mut u32,
    };

    // Must flush the dcache for framebuffer writes to go through
    // TODO: put the framebuffer in the DMA section of Vex address space?
    let pause_flush = |timer: &mut Timer0, uptime_ms: &mut u32, period_ms: u32| {
        timer.delay_ms(period_ms);
        *uptime_ms += period_ms;
        pac::cpu::vexriscv::flush_dcache();
    };

    let mut uptime_ms = 0u32;
    let period_ms = 10u32;

    let mut opts = opts::Options::new();

    let vscope  = peripherals.VECTOR_PERIPH;
    let scope  = peripherals.SCOPE_PERIPH;

    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);

    let mut video = Video0::new(peripherals.VIDEO_PERIPH);

    let mut toggle_encoder_leds = false;

    let mut time_since_encoder_touched: u32 = 0;

    // Write default palette setting
    write_palette(&mut video, opts.beam.palette.value);
    let mut last_palette = opts.beam.palette.value;

    loop {

        if opts.beam.palette.value != last_palette {
            write_palette(&mut video, opts.beam.palette.value);
            last_palette = opts.beam.palette.value;
        }

        if time_since_encoder_touched < 1000 || opts.modify() {

            draw::draw_options(&mut display, &opts, H_ACTIVE-200, V_ACTIVE/2, opts.beam.hue.value).ok();

        }

        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        encoder.update();

        time_since_encoder_touched += period_ms;

        let ticks = encoder.poke_ticks();
        if ticks >= 1 {
            for _ in 0..ticks {
                opts.tick_up();
            }
            time_since_encoder_touched = 0;
        }
        if ticks <= -1 {
            for _ in ticks..0 {
                opts.tick_down();
            }
            time_since_encoder_touched = 0;
        }

        if encoder.poke_btn() {
            opts.toggle_modify();
            time_since_encoder_touched = 0;
        }

        video.set_persist(opts.beam.persist.value);
        video.set_decay(opts.beam.decay.value);

        vscope.hue().write(|w| unsafe { w.hue().bits(opts.beam.hue.value) } );
        vscope.intensity().write(|w| unsafe { w.intensity().bits(opts.beam.intensity.value) } );
        vscope.xscale().write(|w| unsafe { w.xscale().bits(opts.vector.xscale.value) } );
        vscope.yscale().write(|w| unsafe { w.yscale().bits(opts.vector.yscale.value) } );

        scope.hue().write(|w| unsafe { w.hue().bits(opts.beam.hue.value) } );
        scope.intensity().write(|w| unsafe { w.intensity().bits(opts.beam.intensity.value) } );

        scope.trigger_lvl().write(|w| unsafe { w.trigger_lvl().bits(opts.scope.trigger_lvl.value as u16) } );
        scope.xscale().write(|w| unsafe { w.xscale().bits(opts.scope.xscale.value) } );
        scope.yscale().write(|w| unsafe { w.yscale().bits(opts.scope.yscale.value) } );
        scope.timebase().write(|w| unsafe { w.timebase().bits(opts.scope.timebase.value) } );

        scope.ypos0().write(|w| unsafe { w.ypos0().bits(opts.scope.ypos0.value as u16) } );
        scope.ypos1().write(|w| unsafe { w.ypos1().bits(opts.scope.ypos1.value as u16) } );
        scope.ypos2().write(|w| unsafe { w.ypos2().bits(opts.scope.ypos2.value as u16) } );
        scope.ypos3().write(|w| unsafe { w.ypos3().bits(opts.scope.ypos3.value as u16) } );

        scope.trigger_always().write(
            |w| w.trigger_always().bit(opts.scope.trigger_mode.value == opts::TriggerMode::Always) );

        if opts.screen.value == opts::Screen::Vector {
            scope.en().write(|w| w.en().bit(false) );
            vscope.en().write(|w| w.en().bit(true) );
        }

        if opts.screen.value == opts::Screen::Scope {
            scope.en().write(|w| w.en().bit(true) );
            vscope.en().write(|w| w.en().bit(false) );
        }

        for n in 0..16 {
            pca9635.leds[n] = 0u8;
        }

        if uptime_ms % 50 == 0 {
            toggle_encoder_leds = !toggle_encoder_leds;
        }

        if let Some(n) = opts.view().selected() {
            let o = opts.view().options()[n];
            let c = o.percent();
            for n in 0..6 {
                if ((n as f32)*0.5f32/6.0f32 + 0.5) < c {
                    pca9635.leds[PCA9635_BAR_RED[n]] = 0xff as u8;
                } else {
                    pca9635.leds[PCA9635_BAR_RED[n]] = 0 as u8;
                }
                if ((n as f32)*-0.5f32/6.0f32 + 0.5) > c {
                    pca9635.leds[PCA9635_BAR_GREEN[n]] = 0xff as u8;
                } else {
                    pca9635.leds[PCA9635_BAR_GREEN[n]] = 0 as u8;
                }
            }

            if opts.modify() && !toggle_encoder_leds {
                for n in 0..6 {
                    pca9635.leds[PCA9635_BAR_GREEN[n]] = 0 as u8;
                    pca9635.leds[PCA9635_BAR_RED[n]] = 0 as u8;
                }
            }
        }

        if opts.modify() {
            if toggle_encoder_leds {
                if let Some(n) = opts.view().selected() {
                    if n < 8 {
                        pmod.led_set_manual(n, i8::MAX);
                    }
                }
            } else {
                pmod.led_all_auto();
            }
        } else {
            if time_since_encoder_touched < 1000 {
                for n in 0..8 {
                    pmod.led_set_manual(n, 0i8);
                }
                if let Some(n) = opts.view().selected() {
                    if n < 8 {
                        pmod.led_set_manual(n, (((1000-time_since_encoder_touched) * 120) / 1000) as i8);
                    }
                }
            } else {
                pmod.led_all_auto();
            }
        }

        pca9635.push().ok();
    }
}
