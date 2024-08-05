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

use log::{info};

use riscv_rt::entry;

use tiliqua_hal::pca9635::*;

use core::convert::TryInto;

use embedded_graphics::{
    mono_font::{ascii::FONT_9X15_BOLD, MonoTextStyle},
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
    text::{Alignment, Text},
};

use tiliqua_fw::opts;
use tiliqua_lib::opt::*;
use tiliqua_lib::draw;

use tiliqua_lib::generated_constants::*;

tiliqua_hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE);

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
        pac::cpu::vexriscv::flush_dcache();
    };

    let mut uptime_ms = 0u32;
    let period_ms = 10u32;

    let mut toggle_encoder_leds = false;

    let mut time_since_encoder_touched: u32 = 0;

    let mut opts = opts::Options::new();

    let sid = peripherals.SID_PERIPH;

    let sid_poke = |_sid: &pac::SID_PERIPH, addr: u8, data: u8| {
        _sid.transaction_data().write(
            |w| unsafe { w.transaction_data().bits(((data as u16) << 5) | (addr as u16)) } );
    };

    sid_poke(&sid, 24,15);    /* Turn up the volume */
    sid_poke(&sid, 5,0);      /* Fast Attack, Decay */
    sid_poke(&sid, 5+7,0);      /* Fast Attack, Decay */
    sid_poke(&sid, 5+14,0);      /* Fast Attack, Decay */
    sid_poke(&sid, 6,0xF0);      /* Full volume on sustain, quick release */
    sid_poke(&sid, 6+7,0xF0);    /* Full volume on sustain, quick release */
    sid_poke(&sid, 6+14,0xF0);   /* Full volume on sustain, quick release */

    let freq: u16 = 2000;
    sid_poke(&sid, 0, freq as u8);
    sid_poke(&sid, 1, (freq>>8) as u8);
    sid_poke(&sid, 4, 0x11);   /* Enable gate, triangel waveform. */

    loop {

        draw::draw_options(&mut display, &opts, H_ACTIVE-200, V_ACTIVE-200, 0).ok();

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

        {
            sid_poke(&sid, 0, opts.voice1.freq.value as u8);
            sid_poke(&sid, 1, (opts.voice1.freq.value>>8) as u8);
            sid_poke(&sid, 2, opts.voice1.pw.value as u8);
            sid_poke(&sid, 3, (opts.voice1.pw.value>>8) as u8);

            let mut reg04 = 0u8;
            use crate::opts::Wave;
            match opts.voice1.wave.value {
                Wave::Triangle => { reg04 |= 0x10; }
                Wave::Saw      => { reg04 |= 0x20; }
                Wave::Pulse    => { reg04 |= 0x40; }
                Wave::Noise    => { reg04 |= 0x80; }
            }

            reg04 |= opts.voice1.gate.value;
            reg04 |= opts.voice1.sync.value << 1;
            reg04 |= opts.voice1.ring.value << 2;

            sid_poke(&sid, 4, reg04);

            sid_poke(&sid, 5,
                opts.voice1.decay.value |
                (opts.voice1.attack.value << 4));

            sid_poke(&sid, 6,
                opts.voice1.release.value |
                (opts.voice1.sustain.value << 4));
        }

        {
            sid_poke(&sid, 7+0, opts.voice2.freq.value as u8);
            sid_poke(&sid, 7+1, (opts.voice2.freq.value>>8) as u8);
            sid_poke(&sid, 7+2, opts.voice2.pw.value as u8);
            sid_poke(&sid, 7+3, (opts.voice2.pw.value>>8) as u8);

            let mut reg04 = 0u8;
            use crate::opts::Wave;
            match opts.voice2.wave.value {
                Wave::Triangle => { reg04 |= 0x10; }
                Wave::Saw      => { reg04 |= 0x20; }
                Wave::Pulse    => { reg04 |= 0x40; }
                Wave::Noise    => { reg04 |= 0x80; }
            }

            reg04 |= opts.voice2.gate.value;
            reg04 |= opts.voice2.sync.value << 1;
            reg04 |= opts.voice2.ring.value << 2;

            sid_poke(&sid, 7+4, reg04);

            sid_poke(&sid, 7+5,
                opts.voice2.decay.value |
                (opts.voice2.attack.value << 4));

            sid_poke(&sid, 7+6,
                opts.voice2.release.value |
                (opts.voice2.sustain.value << 4));
        }

        {
            sid_poke(&sid, 14+0, opts.voice3.freq.value as u8);
            sid_poke(&sid, 14+1, (opts.voice3.freq.value>>8) as u8);
            sid_poke(&sid, 14+2, opts.voice3.pw.value as u8);
            sid_poke(&sid, 14+3, (opts.voice3.pw.value>>8) as u8);

            let mut reg04 = 0u8;
            use crate::opts::Wave;
            match opts.voice3.wave.value {
                Wave::Triangle => { reg04 |= 0x10; }
                Wave::Saw      => { reg04 |= 0x20; }
                Wave::Pulse    => { reg04 |= 0x40; }
                Wave::Noise    => { reg04 |= 0x80; }
            }

            reg04 |= opts.voice3.gate.value;
            reg04 |= opts.voice3.sync.value << 1;
            reg04 |= opts.voice3.ring.value << 2;

            sid_poke(&sid, 14+4, reg04);

            sid_poke(&sid, 14+5,
                opts.voice3.decay.value |
                (opts.voice3.attack.value << 4));

            sid_poke(&sid, 14+6,
                opts.voice3.release.value |
                (opts.voice3.sustain.value << 4));
        }


        sid_poke(&sid, 0x15, (opts.filter.cutoff.value & 0x7) as u8);
        sid_poke(&sid, 0x16, (opts.filter.cutoff.value >> 3) as u8);
        sid_poke(&sid, 0x17,
            (opts.filter.filt1.value |
            (opts.filter.filt2.value << 1) |
            (opts.filter.filt3.value << 2) |
            (opts.filter.reso.value  << 4)) as u8
            );
        sid_poke(&sid, 0x18,
            ((opts.filter.lp.value     << 4) |
             (opts.filter.bp.value     << 5) |
             (opts.filter.hp.value     << 6) |
             (opts.filter.v3off.value  << 7) |
             (opts.filter.volume.value << 0)) as u8
            );
    }
}
