#![no_std]
#![no_main]

use core::panic::PanicInfo;

use tiliqua_pac as pac;
use lunasoc_hal as hal;

use hal::hal::delay::DelayUs;

use tiliqua_fw::Serial0;
use tiliqua_fw::Timer0;

use log::{info, error};

use riscv_rt::entry;

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


pub struct I2cDevice {
    inner: pac::I2C0,
}

use embedded_hal::i2c::{Operation, ErrorKind};

impl I2cDevice {

    fn transaction(
        &mut self,
        address: u8,
        operations: &mut [Operation<'_>],
    ) -> Result<(), ErrorKind> {

        self.inner.address().write(|w| unsafe { w.address().bits(address) } );
        for op in operations.iter() {
            match op {
                Operation::Write(bytes) => {
                    for b in bytes.iter() {
                        self.inner.transaction_data().write(
                            |w| unsafe { w.transaction_data().bits(0x0000u16 | *b as u16) } );
                    }
                }
                Operation::Read(bytes) => {
                    for b in bytes.iter() {
                        self.inner.transaction_data().write(
                            |w| unsafe { w.transaction_data().bits(0x0100u16 | *b as u16) } );
                    }
                },
            }
        }

        // Start executing transactions
        self.inner.start().write(|w| w.start().bit(true) );

        // Wait for completion
        while self.inner.busy().read().busy().bit() { }

        // Copy out recieved bytes
        for op in operations.iter_mut() {
            match op {
                Operation::Read(bytes) => {
                    for b in bytes.iter_mut() {
                        *b = self.inner.rx_data().read().bits() as u8;
                    }
                },
                _ => {}
            }
        }


        Ok(())
    }
}

#[entry]
fn main() -> ! {
    let peripherals = pac::Peripherals::take().unwrap();

    // initialize logging
    let serial = Serial0::new(peripherals.UART);
    tiliqua_fw::log::init(serial);

    let mut timer = Timer0::new(peripherals.TIMER, pac::clock::sysclk());
    let mut counter = 0;
    let mut direction = true;
    let mut led_state = 0xc000u16;

    info!("Peripherals initialized.");

    info!("PSRAM memtest...");

    // PSRAM memtest

    unsafe {
        const HRAM_BASE: usize = 0x20000000;
        let hram_ptr = HRAM_BASE as *mut u32;

        timer.enable();
        timer.set_timeout_ticks(0xFFFFFFFF);

        let start = timer.counter();

        for i in 0..(1024*1024*4) {
            hram_ptr.offset(i).write_volatile(i as u32);
        }

        let endwrite = timer.counter();

        for i in 0..(1024*1024*4) {
            if (i as u32) != hram_ptr.offset(i).read_volatile() {
                info!("hyperram FL @ {:#x}", i);
            }
        }

        let endread = timer.counter();

        let write_ticks = start-endwrite;
        let read_ticks = endwrite-endread;

        let sysclk = pac::clock::sysclk();

        info!("write speed {} KByte/sec", ((sysclk as u64) * (16*1024) as u64) / write_ticks as u64);

        info!("read speed {} KByte/sec", ((sysclk as u64) * (16*1024 as u64)) / (read_ticks as u64));

    }

    let mut i2cdev = I2cDevice {
        inner: peripherals.I2C0
    };

    loop {

        let bytes = [
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


        /*

        i2c0.address().write(|w| unsafe { w.address().bits(0x5) } );

        for b in bytes {
            // MSB is r=1 / w=0
            i2c0.transaction_data().write(
                |w| unsafe { w.transaction_data().bits(0x0000u16 | b as u16) } );
        }

        i2c0.start().write(|w| w.start().bit(true) );

        while i2c0.busy().read().busy().bit() {
            timer.delay_ms(1).unwrap();
        }

        info!("wrote to leds");
        info!("err: {}", i2c0.err().read().err().bit());
        i2c0.err().write(|w| w.err().bit(false) );

        timer.delay_ms(100).unwrap();

        // read mfg ID from EEPROM

        i2c0.address().write(|w| unsafe { w.address().bits(0x52) } );

        i2c0.transaction_data().write(
            |w| unsafe { w.transaction_data().bits(0x00FAu16) } );

        i2c0.transaction_data().write(
            |w| unsafe { w.transaction_data().bits(0x0100u16) } );

        i2c0.transaction_data().write(
            |w| unsafe { w.transaction_data().bits(0x0100u16) } );

        i2c0.start().write(|w| w.start().bit(true) );

        while i2c0.busy().read().busy().bit() {
            timer.delay_ms(1).unwrap();
        }

        info!("eeprom0: 0x{:x}", i2c0.rx_data().read().bits());
        info!("eeprom1: 0x{:x}", i2c0.rx_data().read().bits());
        info!("err: {}", i2c0.err().read().err().bit());
        i2c0.err().write(|w| w.err().bit(false) );

        */

        // write to the LED expander
        i2cdev.transaction(0x5, &mut [Operation::Write(&bytes)]);

        // read some data from EEPROM
        let mut eeprom_bytes: [u8; 8] = [0; 8];
        i2cdev.transaction(0x52, &mut [Operation::Write(&[0xFAu8]),
                                       Operation::Read(&mut eeprom_bytes)]);
        let mut ix = 0;
        for byte in eeprom_bytes {
            info!("eeprom{}: 0x{:x}", ix, byte);
            ix += 1;
        }

        if direction {
            led_state >>= 1;
            if led_state == 0x0003 {
                direction = false;
                info!("left: {}", counter);
            }
        } else {
            led_state <<= 1;
            if led_state == 0xc000 {
                direction = true;
                info!("right: {}", counter);
            }
        }

        counter += 1;
    }
}
