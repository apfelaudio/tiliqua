#![no_std]
#![no_main]

use core::panic::PanicInfo;

use tiliqua_pac as pac;
use tiliqua_hal as hal;

use hal::hal::delay::DelayNs;

use tiliqua_fw::Serial0;
use tiliqua_fw::Timer0;
use tiliqua_fw::I2c0;

use micromath::F32Ext;

use log::{info, error};

use riscv_rt::entry;

use embedded_hal::i2c::{I2c, Operation};

use core::convert::TryInto;

use fastrand;

use embedded_graphics::{
    mono_font::{ascii::FONT_6X10, ascii::FONT_9X15_BOLD, MonoTextStyle},
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
    text::{Alignment, Text},
};

use heapless::String;
use core::fmt::Write;

use tiliqua_lib::opt;
use tiliqua_lib::draw;

const TUSB322I_ADDR:  u8 = 0x47;
const PCA9635_ADDR:   u8 = 0x05;

// TODO: fetch these from SVF
const PSRAM_BASE:     usize = 0x20000000;
const H_ACTIVE:       u32   = 800;
const V_ACTIVE:       u32   = 600;

// 16MiB, 4 bytes per word.
const PSRAM_SZ_WORDS: usize = 1024 * 1024 * (16 / 4); 
// 32-bit words in the framebuffer PSRAM section
const _PSRAM_FB_BYTES: usize = (H_ACTIVE as usize * V_ACTIVE as usize) / 4;
const _PSRAM_FB_WORDS: usize = _PSRAM_FB_BYTES / 4;
const PSRAM_FB_BASE:  usize = PSRAM_BASE;


#[riscv_rt::pre_init]
unsafe fn pre_main() {
    pac::cpu::vexriscv::flush_icache();
    pac::cpu::vexriscv::flush_dcache();
}

#[cfg(not(test))]
#[panic_handler]
fn panic(panic_info: &PanicInfo) -> ! {
    if let Some(location) = panic_info.location() {
        error!("panic(): file '{}' at line {}",
            location.file(),
            location.line(),
        );
    } else {
        error!("panic(): no location information");
    }
    loop {}
}

#[export_name = "ExceptionHandler"]
fn exception_handler(trap_frame: &riscv_rt::TrapFrame) -> ! {
    error!("exception_handler(): TrapFrame.ra={:x}", trap_frame.ra);
    loop {}
}

#[export_name = "DefaultHandler"]
fn default_isr_handler() -> ! {
    error!("default_isr_handler()");
    loop {}
}

struct DMADisplay {
    fb_ptr: *mut u32,
}

impl OriginDimensions for DMADisplay {
    fn size(&self) -> Size {
        Size::new(H_ACTIVE, V_ACTIVE)
    }
}

impl DrawTarget for DMADisplay {
    type Color = Gray8;
    type Error = core::convert::Infallible;
    fn draw_iter<I>(&mut self, pixels: I) -> Result<(), Self::Error>
    where
        I: IntoIterator<Item = Pixel<Self::Color>>,
    {
        for Pixel(coord, color) in pixels.into_iter() {
            if let Ok((x @ 0..=H_ACTIVE, y @ 0..=V_ACTIVE)) = coord.try_into() {
                // Calculate the index in the framebuffer.
                let index: u32 = (x + y * H_ACTIVE) / 4;
                unsafe {
                    // TODO: support anything other than WHITE
                    let px = self.fb_ptr.offset(index as isize).read_volatile();
                    self.fb_ptr.offset(index as isize).write_volatile(px | ((color.luma() as u32) << (8*(x%4))));
                }
            }
        }
        Ok(())
    }
}

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
    tiliqua_fw::log::init(serial);

    let sysclk = pac::clock::sysclk();
    let mut timer = Timer0::new(peripherals.TIMER, sysclk);
    let mut direction = true;
    let mut led_state = 0xc000u16;

    info!("Hello from Tiliqua selftest!");

    //psram_memtest(&mut timer);

    let mut i2cdev = I2c0::new(peripherals.I2C0);

    tusb322i_id_test(&mut i2cdev);

    let encoder = peripherals.ENCODER0;
    let pmod = peripherals.PMOD0_PERIPH;

    let mut display = DMADisplay {
        fb_ptr: PSRAM_FB_BASE as *mut u32,
    };

    // Must flush the dcache for framebuffer writes to go through
    // TODO: put the framebuffer in the DMA section of Vex address space?
    let pause_flush = |timer: &mut Timer0, uptime_ms: &mut u32, period_ms: u32| {
        timer.delay_ms(period_ms);
        *uptime_ms += period_ms;
        pac::cpu::vexriscv::flush_dcache();
    };

    let mut uptime_ms = 0u32;
    let mut period_ms = 10u32;
    let mut encoder_rotation: i16 = 0;
    let mut encoder_last = 0i16;
    let mut encoder_last_btn = false;
    let mut rng = fastrand::Rng::with_seed(0);

    let mut opts = opt::Options::new();

    let vs = peripherals.VS_PERIPH;

    loop {

        // Report encoder state
        encoder_rotation += (encoder.step().read().bits() as i8) as i16;

        /*
        // Make rotation control loop speed
        if encoder_rotation > -25 {
            period_ms = (25 + encoder_rotation) as u32;
        }
        print_tiliqua(&mut display, &mut rng);
        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        print_encoder_state(&mut display, encoder_rotation, encoder.button().read().bits() != 0);
        pause_flush(&mut timer, &mut uptime_ms, period_ms);
        */

        draw::draw_options(&mut display, &opts, H_ACTIVE-200, V_ACTIVE-100).ok();

        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        let mut encoder_ticks = encoder_rotation - encoder_last;
        let encoder_btn = (encoder.button().read().bits() != 0);

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


        /*

        // Report some eurorack-pmod information
        print_codec_state(&mut display, &pmod);

        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        // Report some touch sensing information
        print_touch_state(&mut display, &pmod);

        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        // Report some information about USB UFP/DFP + orientation
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

        */

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
