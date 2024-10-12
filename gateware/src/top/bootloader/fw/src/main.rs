#![no_std]
#![no_main]

use tiliqua_pac as pac;
use tiliqua_hal as hal;

use hal::hal::delay::DelayNs;

use tiliqua_fw::*;

use log::info;

use riscv_rt::entry;

use tiliqua_hal::pca9635::*;

use core::convert::TryInto;

use embedded_graphics::{
    mono_font::{ascii::FONT_9X15_BOLD, MonoTextStyle},
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
    text::{Alignment, Text},
};

use tiliqua_lib::*;
use tiliqua_lib::opt::*;
use tiliqua_lib::generated_constants::*;

tiliqua_hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE, VIDEO_ROTATE_90);

fn print_rebooting<D>(d: &mut D, rng: &mut fastrand::Rng)
where
    D: DrawTarget<Color = Gray8>,
{
    let style = MonoTextStyle::new(&FONT_9X15_BOLD, Gray8::WHITE);
    Text::with_alignment(
        "REBOOTING",
        Point::new(rng.i32(0..H_ACTIVE as i32), rng.i32(0..V_ACTIVE as i32)),
        style,
        Alignment::Center,
    )
    .draw(d).ok();
}

#[entry]
fn main() -> ! {
    let peripherals = pac::Peripherals::take().unwrap();

    // initialize logging
    let serial = Serial0::new(peripherals.UART0);
    tiliqua_fw::handlers::logger_init(serial);

    let sysclk = pac::clock::sysclk();
    let mut timer = Timer0::new(peripherals.TIMER0, sysclk);

    info!("Hello from Tiliqua bootloader!");

    let i2cdev = I2c0::new(peripherals.I2C0);
    let mut pca9635 = Pca9635Driver::new(i2cdev);

    let mut encoder = Encoder0::new(peripherals.ENCODER0);

    //let pmod = peripherals.PMOD0_PERIPH;
    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);

    let mut display = DMADisplay {
        framebuffer_base: PSRAM_FB_BASE as *mut u32,
    };

    // Must flush the dcache for framebuffer writes to go through
    // TODO: put the framebuffer in the DMA section of Vex address space?
    let pause_flush = |timer: &mut Timer0, uptime_ms: &mut u32, period_ms: u32| {
        timer.delay_ms(period_ms);
        *uptime_ms += period_ms;
        //pac::cpu::vexriscv::flush_dcache();
    };

    let mut uptime_ms = 0u32;
    let period_ms = 2u32;

    let mut rng = fastrand::Rng::with_seed(0);

    let mut toggle_encoder_leds = false;

    let mut time_since_encoder_touched: u32 = 0;

    let mut opts = opts::Options::new();

    let mut logo_coord_ix = 0u32;

    let mut video = Video0::new(peripherals.VIDEO_PERIPH);

    video.set_persist(2048);

    loop {

        draw::draw_options(&mut display, &opts, H_ACTIVE/2-50, V_ACTIVE/2-50, 0).ok();

        for _ in 0..5 {
            let _ = draw::draw_boot_logo(&mut display, (H_ACTIVE/2) as i32, (V_ACTIVE/2+200) as i32, logo_coord_ix);
            logo_coord_ix += 1;
        }

        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        encoder.update();

        time_since_encoder_touched += period_ms;

        let ticks = encoder.poke_ticks();
        if ticks != 0 {
            opts.consume_ticks(ticks);
            time_since_encoder_touched = 0;
        }
        if encoder.poke_btn() {
            opts.toggle_modify();
            time_since_encoder_touched = 0;
        }

        for n in 0..16 {
            pca9635.leds[n] = 0u8;
        }

        if uptime_ms % 50 == 0 {
            toggle_encoder_leds = !toggle_encoder_leds;
        }

        leds::mobo_pca9635_set_bargraph(&opts, &mut pca9635.leds,
                                        toggle_encoder_leds);

        if opts.modify() {
            print_rebooting(&mut display, &mut rng);
            if toggle_encoder_leds {
                if let Some(n) = opts.view().selected() {
                    pmod.led_set_manual(n, i8::MAX);
                    if time_since_encoder_touched > 150 {
                        info!("BITSTREAM{}\n\r", n);
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
                    pmod.led_set_manual(n, (((1000-time_since_encoder_touched) * 120) / 1000) as i8);
                }
            } else {
                pmod.led_all_auto();
            }
        }

        pca9635.push().ok();

    }
}
