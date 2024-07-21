#![no_std]
#![no_main]

use tiliqua_pac as pac;
use tiliqua_hal as hal;

use hal::hal::delay::DelayNs;

use tiliqua_fw::Serial0;
use tiliqua_fw::Timer0;
use tiliqua_fw::I2c0;
use tiliqua_fw::Encoder0;

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


// TODO: fetch these from SVF
const PSRAM_BASE:     usize = 0x20000000;
const H_ACTIVE:       u32   = 720;
const V_ACTIVE:       u32   = 720;

// 16MiB, 4 bytes per word.
const _PSRAM_SZ_WORDS: usize = 1024 * 1024 * (16 / 4); 
const PSRAM_FB_BASE:  usize = PSRAM_BASE;

tiliqua_hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE);

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

    let vs = peripherals.VS_PERIPH;

    use tiliqua_lib::opt::OptionPageEncoderInterface;

    loop {

        draw::draw_options(&mut display, &opts, H_ACTIVE-200, V_ACTIVE-100, opts.xbeam.hue.value).ok();

        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        encoder.update();

        match encoder.poke_ticks() {
            1 => opts.tick_up(),
            -1 => opts.tick_down(),
            _ => {},
        }

        if encoder.poke_btn() {
            opts.toggle_modify();
        }

        vs.persist().write(|w| unsafe { w.persist().bits(opts.xbeam.persist.value) } );
        vs.hue().write(|w| unsafe { w.hue().bits(opts.xbeam.hue.value) } );
        vs.intensity().write(|w| unsafe { w.intensity().bits(opts.xbeam.intensity.value) } );
        vs.decay().write(|w| unsafe { w.decay().bits(opts.xbeam.decay.value) } );
        vs.scale().write(|w| unsafe { w.scale().bits(opts.xbeam.scale.value) } );

        pca9635.leds[0] = opts.xbeam.hue.value << 4;
        pca9635.leds[1] = opts.xbeam.intensity.value << 4;
        pca9635.push().ok();

    }
}
