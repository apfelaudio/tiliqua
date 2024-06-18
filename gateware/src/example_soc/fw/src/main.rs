#![no_std]
#![no_main]

use tiliqua_pac as pac;
use lunasoc_hal as hal;

use hal::hal::delay::DelayUs;

use tiliqua_fw::Serial0;
use tiliqua_fw::Timer0;

use log::info;

use panic_halt as _;
use riscv_rt::entry;

#[riscv_rt::pre_init]
unsafe fn pre_main() {
    pac::cpu::vexriscv::flush_icache();
    pac::cpu::vexriscv::flush_dcache();
}

#[entry]
fn main() -> ! {
    let peripherals = pac::Peripherals::take().unwrap();
    let leds = &peripherals.LEDS;

    // initialize logging
    let serial = Serial0::new(peripherals.UART);
    tiliqua_fw::log::init(serial);

    let mut timer = Timer0::new(peripherals.TIMER, pac::clock::sysclk());
    let mut counter = 0;
    let mut direction = true;
    let mut led_state = 0b110000;

    info!("Peripherals initialized, entering main loop.");

    loop {
        timer.delay_ms(100).unwrap();

        if direction {
            led_state >>= 1;
            if led_state == 0b000011 {
                direction = false;
                info!("left: {}", counter);
            }
        } else {
            led_state <<= 1;
            if led_state == 0b110000 {
                direction = true;
                info!("right: {}", counter);
            }
        }

        leds.output().write(|w| unsafe { w.output().bits(led_state) });
        counter += 1;
    }
}
