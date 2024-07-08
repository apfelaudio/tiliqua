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

use embedded_graphics::{
    mono_font::{ascii::FONT_6X10, MonoTextStyle},
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
    primitives::{Circle, PrimitiveStyle},
    text::{Alignment, Text},
};

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
        Size::new(800, 600)
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
            if let Ok((x @ 0..=800, y @ 0..=600)) = coord.try_into() {
                // Calculate the index in the framebuffer.
                let index: u32 = (x + y * 800) / 4;
                unsafe {
                    let px = self.fb_ptr.offset(index as isize).read_volatile();
                    self.fb_ptr.offset(index as isize).write_volatile(px | ((color.luma() as u32) << (8*(x%4))));
                }
            }
        }
        Ok(())
    }
}

const TUSB322I_ADDR: u8 = 0x47;
const PCA9635_ADDR:  u8 = 0x05;

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

    info!("PSRAM memtest...");

    /*
    unsafe {
        const HRAM_BASE: usize = 0x20000000;
        let psram_ptr = HRAM_BASE as *mut u32;
        let psram_sz_words = 1024 * 1024 * (16 / 4); // 16MiB, 4 bytes per word.

        timer.enable();
        timer.set_timeout_ticks(0xFFFFFFFF);

        let start = timer.counter();

        for i in 0..psram_sz_words {
            psram_ptr.offset(i).write_volatile(i as u32);
        }

        let endwrite = timer.counter();

        for i in 0..psram_sz_words {
            if (i as u32) != psram_ptr.offset(i).read_volatile() {
                panic!("FAIL: PSRAM selftest @ {:#x}", i);
            }
        }

        let endread = timer.counter();

        let write_ticks = start-endwrite;
        let read_ticks = endwrite-endread;

        info!("write speed {} KByte/sec", ((sysclk as u64) * (16*1024) as u64) / write_ticks as u64);

        info!("read speed {} KByte/sec", ((sysclk as u64) * (16*1024 as u64)) / (read_ticks as u64));

        psram_ptr.offset(0).write_volatile(0);
        psram_ptr.offset(1).write_volatile(0xFFFFFFFF);
        let psram_ptr_u8 = HRAM_BASE as *mut u8;
        info!("read0 {:#x}", psram_ptr.offset(0).read_volatile());
        info!("read1 {:#x}", psram_ptr.offset(1).read_volatile());
        psram_ptr_u8.offset(7).write_volatile(0xFEu8);
        psram_ptr_u8.offset(6).write_volatile(0xEDu8);
        psram_ptr_u8.offset(5).write_volatile(0xBAu8);
        psram_ptr_u8.offset(4).write_volatile(0xBEu8);
        psram_ptr_u8.offset(0).write_volatile(0xEFu8);
        psram_ptr_u8.offset(1).write_volatile(0xBEu8);
        psram_ptr_u8.offset(2).write_volatile(0xADu8);
        psram_ptr_u8.offset(3).write_volatile(0xDEu8);
        info!("read0 {:#x}", psram_ptr.offset(0).read_volatile());
        info!("read1 {:#x}", psram_ptr.offset(1).read_volatile());

        let aligned_u32 = psram_ptr.offset(0).read_volatile();
        if aligned_u32 != 0xdeadbeef {
            panic!("FAIL: PSRAM unaligned access test");
        }

        for i in 0..psram_sz_words {
            psram_ptr.offset(i).write_volatile(0u32);
        }

        info!("PASS: PSRAM memtest");
    }
    */

    let mut i2cdev = I2c0::new(peripherals.I2C0);

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

    // Draw something to the display
    let mut display = DMADisplay {
        fb_ptr: 0x20000000 as *mut u32,
    };
    let circle = Circle::new(Point::new(22, 22), 20)
        .into_styled(PrimitiveStyle::with_stroke(Gray8::WHITE, 1));
    circle.draw(&mut display).ok();

    let character_style = MonoTextStyle::new(&FONT_6X10, Gray8::WHITE);
    let text = "TILIQUA SELF-TEST";

    let encoder = peripherals.ENCODER0;
    let mut encoder_rotation: i16 = 0;

    let pmod = peripherals.PMOD0_PERIPH;

    let mut uptime_ms = 0u32;

    use fastrand;

    let mut rng = fastrand::Rng::with_seed(0);

    use heapless::String;
    use core::fmt::Write;

    loop {

        let mut s = String::<64>::new();

        // Report encoder state
        encoder_rotation += (encoder.step().read().bits() as i8) as i16;
        write!(s, "ENCODER BTN={} ROT={}",
              encoder.button().read().bits(),
              encoder_rotation);

        Text::with_alignment(
            &s,
            display.bounding_box().center() + Point::new(0, 0),
            character_style,
            Alignment::Left,
        )
        .draw(&mut display).ok();


        // Make rotation control loop speed
        if encoder_rotation >= -50 {
            timer.delay_ms((50 + encoder_rotation) as u32);
            uptime_ms += 50;
        } else {
            uptime_ms += 1;
        }

        // Report some eurorack-pmod information
        s.clear();
        write!(s, "codec_raw_adc - ch0={} ch1={} ch2={} ch3={}",
              pmod.sample_adc0().read().bits() as i16,
              pmod.sample_adc1().read().bits() as i16,
              pmod.sample_adc2().read().bits() as i16,
              pmod.sample_adc3().read().bits() as i16);
        Text::with_alignment(
            &s,
            display.bounding_box().center() + Point::new(0, 12),
            character_style,
            Alignment::Left,
        )
        .draw(&mut display).ok();

        s.clear();
        write!(s, "jack_insertion - 0x{:x}", pmod.jack().read().bits() as u8);
        Text::with_alignment(
            &s,
            display.bounding_box().center() + Point::new(0, 24),
            character_style,
            Alignment::Left,
        )
        .draw(&mut display).ok();

        s.clear();
        write!(s, "touch - ch0={} ch1={} ch2={} ch3={} ch4={} ch5={} ch6={} ch7={}",
              pmod.touch0().read().bits() as u8,
              pmod.touch1().read().bits() as u8,
              pmod.touch2().read().bits() as u8,
              pmod.touch3().read().bits() as u8,
              pmod.touch4().read().bits() as u8,
              pmod.touch5().read().bits() as u8,
              pmod.touch6().read().bits() as u8,
              pmod.touch7().read().bits() as u8);
        Text::with_alignment(
            &s,
            display.bounding_box().center() + Point::new(0, 36),
            character_style,
            Alignment::Left,
        )
        .draw(&mut display).ok();

        // Write something to the CODEC outputs / LEDs
        pmod.sample_o0().write(|w| unsafe { w.sample_o0().bits(
            ((f32::sin((uptime_ms as f32)/200.0f32 + 0.0) * 16000.0f32) as i16) as u16) } );
        pmod.sample_o1().write(|w| unsafe { w.sample_o1().bits(
            ((f32::sin((uptime_ms as f32)/200.0f32 + 1.0) * 16000.0f32) as i16) as u16) } );
        pmod.sample_o2().write(|w| unsafe { w.sample_o2().bits(
            ((f32::sin((uptime_ms as f32)/200.0f32 + 2.0) * 16000.0f32) as i16) as u16) } );
        pmod.sample_o3().write(|w| unsafe { w.sample_o3().bits(
            ((f32::sin((uptime_ms as f32)/200.0f32 + 3.0) * 16000.0f32) as i16) as u16) } );


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


        // Read TUSB322I connection status register
        // We don't use this yet. But it's useful for checking for usb circuitry assembly problems.
        // (in particular the cable orientation detection registers)
        let mut tusb322_conn_status: [u8; 1] = [0; 1];
        let _ = i2cdev.transaction(TUSB322I_ADDR, &mut [Operation::Write(&[0x09u8]),
                                                        Operation::Read(&mut tusb322_conn_status)]);

        s.clear();
        write!(s, "tusb322i_conn_status: 0x{:x} (DUA={} DDC={} VF={} IS={} CD={} AS={})",
              tusb322_conn_status[0],
              tusb322_conn_status[0]        & 0x1,
              (tusb322_conn_status[0] >> 1) & 0x3,
              (tusb322_conn_status[0] >> 3) & 0x1,
              (tusb322_conn_status[0] >> 4) & 0x1,
              (tusb322_conn_status[0] >> 5) & 0x1,
              (tusb322_conn_status[0] >> 6) & 0x3,
              );
        Text::with_alignment(
            &s,
            display.bounding_box().center() + Point::new(0, 48),
            character_style,
            Alignment::Left,
        )
        .draw(&mut display).ok();

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

        pac::cpu::vexriscv::flush_dcache();
    }
}
