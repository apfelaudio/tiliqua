#![no_std]
#![no_main]

use tiliqua_pac as pac;
use tiliqua_hal as hal;

use hal::hal::delay::DelayNs;

use tiliqua_fw::Serial0;
use tiliqua_fw::Timer0;
use tiliqua_fw::I2c0;
use tiliqua_fw::Encoder0;

use log::{info, error};

use riscv_rt::entry;

use tiliqua_hal::pca9635::*;

use core::convert::TryInto;

use embedded_graphics::{
    mono_font::{ascii::FONT_6X10, ascii::FONT_9X15_BOLD, MonoTextStyle},
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
    text::{Alignment, Text},
};

use embedded_hal::i2c::Operation;
use embedded_hal::i2c::I2c;

use heapless::String;
use core::fmt::Write;

use micromath::F32Ext;

// TODO: fetch these from SVF
const PSRAM_BASE:     usize = 0x20000000;
const H_ACTIVE:       u32   = 800;
const V_ACTIVE:       u32   = 600;

// 16MiB, 4 bytes per word.
const PSRAM_SZ_WORDS: usize = 1024 * 1024 * (16 / 4); 
const PSRAM_FB_BASE:  usize = PSRAM_BASE;

const TUSB322I_ADDR:  u8 = 0x47;

tiliqua_hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE);

fn psram_memtest(timer: &mut Timer0) {

    info!("PSRAM memtest (this will be slow if video is also active)...");

    // WARN: assume framebuffer is at the start of PSRAM - don't try memtesting that section.

    let psram_ptr = PSRAM_BASE as *mut u32;
    let psram_sz_test = 1024*1024;

    timer.enable();
    timer.set_timeout_ticks(0xFFFFFFFF);

    let start = timer.counter();

    unsafe {
        for i in (PSRAM_SZ_WORDS - psram_sz_test)..PSRAM_SZ_WORDS {
            psram_ptr.offset(i as isize).write_volatile(i as u32);
        }
    }

    pac::cpu::vexriscv::flush_dcache();

    let endwrite = timer.counter();

    unsafe {
        for i in (PSRAM_SZ_WORDS - psram_sz_test)..PSRAM_SZ_WORDS {
            let value = psram_ptr.offset(i as isize).read_volatile();
            if (i as u32) != value {
                error!("FAIL: PSRAM selftest @ {:#x} is {:#x}", i, value);
                panic!();
            }
        }
    }

    let endread = timer.counter();

    let write_ticks = start-endwrite;
    let read_ticks = endwrite-endread;

    let sysclk = pac::clock::sysclk();
    info!("write speed {} KByte/sec", ((sysclk as u64) * (psram_sz_test/1024) as u64) / write_ticks as u64);
    info!("read speed {} KByte/sec", ((sysclk as u64) * (psram_sz_test/1024) as u64) / (read_ticks as u64));

    info!("PASS: PSRAM memtest");
}

fn tusb322i_id_test(i2cdev: &mut I2c0) {
    info!("Read TUSB322I Device ID...");

    // Read TUSB322I device ID
    let mut tusb322i_id: [u8; 8] = [0; 8];
    let _ = i2cdev.transaction(TUSB322I_ADDR, &mut [Operation::Write(&[0x00u8]),
                                                    Operation::Read(&mut tusb322i_id)]);
    if tusb322i_id != [0x32, 0x32, 0x33, 0x42, 0x53, 0x55, 0x54, 0x0] {
        let mut ix = 0;
        for byte in tusb322i_id {
            info!("tusb322i_id{}: 0x{:x}", ix, byte);
            ix += 1;
        }
        panic!("FAIL: TUSB322I ID");
    }

    info!("PASS: TUSB322I Device ID.");
}

fn print_encoder_state<D>(d: &mut D, rotation: i16, button: bool)
where
    D: DrawTarget<Color = Gray8>,
{
    let mut s = String::<64>::new();
    write!(s, "encoder        - btn={} rot={}",
          button,
          rotation).ok();
    info!("{}", s);
    let style = MonoTextStyle::new(&FONT_6X10, Gray8::WHITE);
    Text::with_alignment(
        &s,
        d.bounding_box().center() + Point::new(-140, -24),
        style,
        Alignment::Left,
    )
    .draw(d).ok();
}

fn print_codec_state<D>(d: &mut D, pmod: &pac::PMOD0_PERIPH)
where
    D: DrawTarget<Color = Gray8>,
{
    let mut s = String::<64>::new();
    write!(s, "codec_raw_adc  - ch0={:06} ch1={:06} ch2={:06} ch3={:06}",
          pmod.sample_adc0().read().bits() as i16,
          pmod.sample_adc1().read().bits() as i16,
          pmod.sample_adc2().read().bits() as i16,
          pmod.sample_adc3().read().bits() as i16).ok();
    info!("{}", s);
    let style = MonoTextStyle::new(&FONT_6X10, Gray8::WHITE);
    Text::with_alignment(
        &s,
        d.bounding_box().center() + Point::new(-140, -12),
        style,
        Alignment::Left,
    )
    .draw(d).ok();
}

fn print_touch_state<D>(d: &mut D, pmod: &pac::PMOD0_PERIPH)
where
    D: DrawTarget<Color = Gray8>,
{
    let mut s = String::<64>::new();
    write!(s, "touch          - ch0={:03} ch1={:03} ch2={:03} ch3={:03} ch4={:03} ch5={:03} ch6={:03} ch7={:03}",
          pmod.touch0().read().bits() as u8,
          pmod.touch1().read().bits() as u8,
          pmod.touch2().read().bits() as u8,
          pmod.touch3().read().bits() as u8,
          pmod.touch4().read().bits() as u8,
          pmod.touch5().read().bits() as u8,
          pmod.touch6().read().bits() as u8,
          pmod.touch7().read().bits() as u8).ok();
    info!("{}", s);
    let style = MonoTextStyle::new(&FONT_6X10, Gray8::WHITE);
    Text::with_alignment(
        &s,
        d.bounding_box().center() + Point::new(-140, 0),
        style,
        Alignment::Left,
    )
    .draw(d).ok();
}

fn print_usb_state<D>(d: &mut D, i2cdev: &mut I2c0)
where
    D: DrawTarget<Color = Gray8>,
{
    // Read TUSB322I connection status register
    // We don't use this yet. But it's useful for checking for usb circuitry assembly problems.
    // (in particular the cable orientation detection registers)
    let mut tusb322_conn_status: [u8; 1] = [0; 1];
    let _ = i2cdev.transaction(TUSB322I_ADDR, &mut [Operation::Write(&[0x09u8]),
                                                    Operation::Read(&mut tusb322_conn_status)]);

    let mut s = String::<64>::new();
    write!(s, "tusb322i_conn  - 0x{:x} (DUA={} DDC={} VF={} IS={} CD={} AS={})",
          tusb322_conn_status[0],
          tusb322_conn_status[0]        & 0x1,
          (tusb322_conn_status[0] >> 1) & 0x3,
          (tusb322_conn_status[0] >> 3) & 0x1,
          (tusb322_conn_status[0] >> 4) & 0x1,
          (tusb322_conn_status[0] >> 5) & 0x1,
          (tusb322_conn_status[0] >> 6) & 0x3,
          ).ok();
    info!("{}", s);
    let style = MonoTextStyle::new(&FONT_6X10, Gray8::WHITE);
    Text::with_alignment(
        &s,
        d.bounding_box().center() + Point::new(-140, 12),
        style,
        Alignment::Left,
    )
    .draw(d).ok();
}

fn print_tiliqua<D>(d: &mut D, rng: &mut fastrand::Rng)
where
    D: DrawTarget<Color = Gray8>,
{
    let style = MonoTextStyle::new(&FONT_9X15_BOLD, Gray8::WHITE);
    Text::with_alignment(
        "TILIQUA SELF TEST",
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
    let serial = Serial0::new(peripherals.UART);
    tiliqua_fw::handlers::logger_init(serial);

    let sysclk = pac::clock::sysclk();
    let mut timer = Timer0::new(peripherals.TIMER, sysclk);

    info!("Hello from Tiliqua selftest!");

    let mut i2cdev = I2c0::new(peripherals.I2C0);
    // FIXME: use proper atomic bus sharing!!
    let i2cdev2 = I2c0::new(unsafe { pac::I2C0::steal() } );

    psram_memtest(&mut timer);

    tusb322i_id_test(&mut i2cdev);

    let mut pca9635 = Pca9635Driver::new(i2cdev2);

    let mut encoder = Encoder0::new(peripherals.ENCODER0);

    let pmod = peripherals.PMOD0_PERIPH;

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
    let mut encoder_toggle: bool = false;

    let mut rng = fastrand::Rng::with_seed(0);

    loop {

        encoder.update();

        encoder_rotation += encoder.poke_ticks() as i16;
        if encoder.poke_btn() {
            encoder_toggle = !encoder_toggle;
        }

        print_tiliqua(&mut display, &mut rng);
        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        print_encoder_state(&mut display, encoder_rotation, encoder_toggle);
        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        print_codec_state(&mut display, &pmod);
        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        print_touch_state(&mut display, &pmod);
        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        print_usb_state(&mut display, &mut i2cdev);
        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        // Write something to the CODEC outputs / LEDs
        pmod.sample_o0().write(|w| unsafe { w.sample_o0().bits(
            ((f32::sin((uptime_ms as f32)/200.0f32 + 0.0) * 16000.0f32) as i16) as u16) } );
        pmod.sample_o1().write(|w| unsafe { w.sample_o1().bits(
            ((f32::sin((uptime_ms as f32)/200.0f32 + 1.0) * 16000.0f32) as i16) as u16) } );
        pmod.sample_o2().write(|w| unsafe { w.sample_o2().bits(
            ((f32::sin((uptime_ms as f32)/200.0f32 + 2.0) * 16000.0f32) as i16) as u16) } );
        pmod.sample_o3().write(|w| unsafe { w.sample_o3().bits(
            ((f32::sin((uptime_ms as f32)/200.0f32 + 3.0) * 16000.0f32) as i16) as u16) } );

        for n in 0..16 {
            pca9635.leds[n] = (f32::sin((uptime_ms as f32)/200.0f32 + (n as f32)) * 255.0f32) as u8;
        }
        pca9635.push().ok();

    }
}
