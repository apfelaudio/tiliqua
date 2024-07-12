#![no_std]
#![no_main]

use tiliqua_pac as pac;
use tiliqua_hal as hal;

use hal::hal::delay::DelayNs;

use tiliqua_fw::Serial0;
use tiliqua_fw::Timer0;
use tiliqua_fw::I2c0;

use log::info;

use riscv_rt::entry;

use embedded_hal::i2c::{I2c, Operation};

use core::convert::TryInto;

use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
};

use tiliqua_fw::opts;
use tiliqua_lib::draw;

const PCA9635_ADDR:   u8 = 0x05;

// TODO: fetch these from SVF
const PSRAM_BASE:     usize = 0x20000000;
const H_ACTIVE:       u32   = 800;
const V_ACTIVE:       u32   = 600;

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
    let mut direction = true;
    let mut led_state = 0xc000u16;

    info!("Hello from Tiliqua selftest!");

    let mut i2cdev = I2c0::new(peripherals.I2C0);

    let encoder = peripherals.ENCODER0;

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
    let mut encoder_rotation: i16 = 0;
    let mut encoder_last = 0i16;
    let mut encoder_last_btn = false;

    let mut opts = opts::Options::new();

    let vs = peripherals.VS_PERIPH;

    use tiliqua_lib::opt::OptionPageEncoderInterface;

    loop {

        // Report encoder state
        encoder_rotation += (encoder.step().read().bits() as i8) as i16;

        draw::draw_options(&mut display, &opts, H_ACTIVE-200, V_ACTIVE-100, opts.xbeam.hue.value).ok();

        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        let mut encoder_ticks = encoder_rotation - encoder_last;
        let encoder_btn = encoder.button().read().bits() != 0;

        if encoder_ticks > 1 {
            opts.tick_up();
            encoder_ticks -= 2;
        }

        if encoder_ticks < -1 {
            opts.tick_down();
            encoder_ticks += 2;
        }

        if encoder_last_btn != encoder_btn && !encoder_btn {
            opts.toggle_modify();
        }

        encoder_last = encoder_rotation - encoder_ticks;
        encoder_last_btn = encoder_btn;


        vs.persist().write(|w| unsafe { w.persist().bits(opts.xbeam.persist.value) } );

        vs.hue().write(|w| unsafe { w.hue().bits(opts.xbeam.hue.value) } );

        vs.intensity().write(|w| unsafe { w.intensity().bits(opts.xbeam.intensity.value) } );

        vs.decay().write(|w| unsafe { w.decay().bits(opts.xbeam.decay.value) } );

        // Write something interesting to the LED expander
        let pca9635_bytes = [
           0x80u8, // Auto-increment starting from MODE1
           0x81u8, // MODE1
           0x01u8, // MODE2
           (led_state >>  0) as u8, // PWM0
           (led_state >>  1) as u8, // PWM1
           (led_state >>  2) as u8, // PWM2
           (led_state >>  3) as u8, // PWM3
           (led_state >>  4) as u8, // PWM4
           (led_state >>  5) as u8, // PWM5
           (led_state >>  6) as u8, // PWM6
           (led_state >>  7) as u8, // PWM7
           (led_state >>  8) as u8, // PWM8
           (led_state >>  9) as u8, // PWM9
           (led_state >> 10) as u8, // PWM10
           (led_state >> 11) as u8, // PWM11
           (led_state >> 12) as u8, // PWM12
           (led_state >> 13) as u8, // PWM13
           (led_state >> 14) as u8, // PWM14
           (led_state >> 15) as u8, // PWM15
           0xFFu8, // GRPPWM
           0x00u8, // GRPFREQ
           0xAAu8, // LEDOUT0
           0xAAu8, // LEDOUT1
           0xAAu8, // LEDOUT2
           0xAAu8, // LEDOUT3
        ];
        let _ = i2cdev.transaction(PCA9635_ADDR, &mut [Operation::Write(&pca9635_bytes)]);

        // TODO: nicer breathing pattern
        if direction {
            led_state >>= 1;
            if led_state == 0x0003 {
                direction = false;
            }
        } else {
            led_state <<= 1;
            if led_state == 0xc000 {
                direction = true;
            }
        }

    }
}
