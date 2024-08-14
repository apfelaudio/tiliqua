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

use log::info;

use riscv_rt::entry;

use tiliqua_hal::pca9635::*;

use core::convert::TryInto;

use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
};

use tiliqua_fw::opts;
use tiliqua_lib::draw;

use tiliqua_lib::opt::*;

use tiliqua_lib::generated_constants::*;

tiliqua_hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE, VIDEO_ROTATE_90);

const PCA9635_BAR_GREEN: [usize; 6] = [0, 2, 14, 12, 6, 4];
const PCA9635_BAR_RED:   [usize; 6] = [1, 3, 15, 13, 7, 5];
const _PCA9635_MIDI:     [usize; 2] = [8, 9];

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

    let persist = peripherals.PERSIST_PERIPH;
    let vscope  = peripherals.VECTOR_PERIPH;
    let scope  = peripherals.SCOPE_PERIPH;

    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);

    let mut toggle_encoder_leds = false;

    let mut time_since_encoder_touched: u32 = 0;

    loop {

        if time_since_encoder_touched < 1000 || opts.modify() {

            draw::draw_options(&mut display, &opts, H_ACTIVE-200, V_ACTIVE/2, opts.beam.ui_hue.value).ok();

        }

        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        encoder.update();

        time_since_encoder_touched += period_ms;

        match encoder.poke_ticks() {
            1 => {
                opts.tick_up();
                time_since_encoder_touched = 0;
            }
            -1 => {
                opts.tick_down();
                time_since_encoder_touched = 0;
            }
            _ => {},
        }

        if encoder.poke_btn() {
            opts.toggle_modify();
            time_since_encoder_touched = 0;
        }

        persist.persist().write(|w| unsafe { w.persist().bits(opts.beam.persist.value) } );
        persist.decay().write(|w| unsafe { w.decay().bits(opts.beam.decay.value) } );

        vscope.hue().write(|w| unsafe { w.hue().bits(opts.beam.hue.value) } );
        vscope.intensity().write(|w| unsafe { w.intensity().bits(opts.beam.intensity.value) } );
        vscope.xscale().write(|w| unsafe { w.xscale().bits(opts.vector.xscale.value) } );
        vscope.yscale().write(|w| unsafe { w.yscale().bits(opts.vector.yscale.value) } );

        scope.hue().write(|w| unsafe { w.hue().bits(opts.beam.hue.value) } );
        scope.intensity().write(|w| unsafe { w.intensity().bits(opts.beam.intensity.value) } );

        scope.trigger_lvl().write(|w| unsafe { w.trigger_lvl().bits(opts.scope.trigger_lvl.value as u16) } );
        scope.yscale().write(|w| unsafe { w.yscale().bits(opts.scope.yscale.value) } );
        scope.timebase().write(|w| unsafe { w.timebase().bits(opts.scope.timebase.value) } );

        scope.ypos0().write(|w| unsafe { w.ypos0().bits(opts.scope.ypos0.value as u16) } );
        scope.ypos1().write(|w| unsafe { w.ypos1().bits(opts.scope.ypos1.value as u16) } );
        scope.ypos2().write(|w| unsafe { w.ypos2().bits(opts.scope.ypos2.value as u16) } );
        scope.ypos3().write(|w| unsafe { w.ypos3().bits(opts.scope.ypos3.value as u16) } );

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
                    pmod.led_set_manual(n, i8::MAX);
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
