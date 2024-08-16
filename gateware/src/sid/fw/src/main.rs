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

tiliqua_hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE, VIDEO_ROTATE_90);

const PCA9635_BAR_GREEN: [usize; 6] = [0, 2, 14, 12, 6, 4];
const PCA9635_BAR_RED:   [usize; 6] = [1, 3, 15, 13, 7, 5];
const _PCA9635_MIDI:     [usize; 2] = [8, 9];

use micromath::F32Ext;

fn volts_to_freq(volts: f32) -> f32 {
    let a3_freq_hz: f32 = 440.0f32;
    (a3_freq_hz / 8.0f32) * (2.0f32).powf(volts + 2.0f32 - 3.0f32/4.0f32)
}

#[export_name = "DefaultHandler"]
fn isr_handler() {
    let peripherals = unsafe { pac::Peripherals::steal() };
    let sysclk = pac::clock::sysclk();
    let mut timer = Timer0::new(peripherals.TIMER, sysclk);
    if timer.is_pending() {
        info!("tick");
        let sid = peripherals.SID_PERIPH;
        let sid_poke = |_sid: &pac::SID_PERIPH, addr: u8, data: u8| {
            _sid.transaction_data().write(
                |w| unsafe { w.transaction_data().bits(((data as u16) << 5) | (addr as u16)) } );
        };
        timer.clear_pending();
    }
}

#[entry]
fn main() -> ! {
    let peripherals = pac::Peripherals::take().unwrap();

    // initialize logging
    let serial = Serial0::new(peripherals.UART);
    tiliqua_fw::handlers::logger_init(serial);

    let sysclk = pac::clock::sysclk();
    let mut timer = Timer0::new(peripherals.TIMER, sysclk);

    info!("Hello from Tiliqua bootloader!");


    use core::time::Duration;
    use crate::hal::timer;
    timer.listen(timer::Event::TimeOut);
    timer.set_timeout(Duration::from_millis(500));
    timer.enable();

    unsafe {
            vexriscv::register::vmim::write(1 << (pac::Interrupt::TIMER as usize));

            // Enable machine external interrupts (basically everything added on by LiteX).
            riscv::register::mie::set_mext();

            // WARN: Don't do this before IRQs are registered for this scope,
            // otherwise you'll hang forever :)
            // Finally enable interrupts
            riscv::interrupt::enable();
    }


    let i2cdev = I2c0::new(peripherals.I2C0);
    let mut pca9635 = Pca9635Driver::new(i2cdev);

    let mut encoder = Encoder0::new(peripherals.ENCODER0);

    //let pmod = peripherals.PMOD0_PERIPH;
    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);

    let mut display = DMADisplay {
        framebuffer_base: PSRAM_FB_BASE as *mut u32,
    };

    let mut uptime_ms = 0u32;
    let period_ms = 1u32;

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

    let scope  = peripherals.SCOPE_PERIPH;

    let hue = 10u8;

    loop {

        draw::draw_options(&mut display, &opts, 100, V_ACTIVE/2, hue).ok();

        let hl_wfm: Option<u8> = match opts.screen.value {
            opts::Screen::Voice1 => Some(0),
            opts::Screen::Voice2 => Some(1),
            opts::Screen::Voice3 => Some(2),
            _ => None,
        };

        let gates: [bool; 3] = [
            opts.voice1.gate.value == 1,
            opts.voice2.gate.value == 1,
            opts.voice3.gate.value == 1,
        ];

        let switches: [bool; 3] = [
            opts.filter.filt1.value == 1,
            opts.filter.filt2.value == 1,
            opts.filter.filt3.value == 1,
        ];

        let filter_types: [bool; 3] = [
            opts.filter.lp.value == 1,
            opts.filter.bp.value == 1,
            opts.filter.hp.value == 1,
        ];

        let hl_filter: bool = opts.screen.value == opts::Screen::Filter;

        draw::draw_sid(&mut display, 100, V_ACTIVE/4+25, hue, hl_wfm, gates, hl_filter, switches, filter_types);

        {
            let font_small_white = MonoTextStyle::new(&FONT_9X15_BOLD, Gray8::new(0xB0 + hue));
            let hc = (H_ACTIVE/2) as i16;
            let vc = (V_ACTIVE/2) as i16;
            Text::new(
                "out3: combined, post-filter",
                Point::new((opts.scope.xpos.value + hc - 250) as i32,
                           (opts.scope.ypos0.value + vc + 50) as i32),
                font_small_white,
            )
            .draw(&mut display).ok();
            Text::new(
                "out0: voice 1, post-VCA",
                Point::new((opts.scope.xpos.value + hc - 250) as i32,
                           (opts.scope.ypos1.value + vc + 50) as i32),
                font_small_white,
            )
            .draw(&mut display).ok();
            Text::new(
                "out1: voice 2, post-VCA",
                Point::new((opts.scope.xpos.value + hc - 250) as i32,
                           (opts.scope.ypos2.value + vc + 50) as i32),
                font_small_white,
            )
            .draw(&mut display).ok();
            Text::new(
                "out2: voice 3, post-VCA",
                Point::new((opts.scope.xpos.value + hc - 250) as i32,
                           (opts.scope.ypos3.value + vc + 50) as i32),
                font_small_white,
            )
            .draw(&mut display).ok();
        }

        // Flush
        //pac::cpu::vexriscv::flush_dcache();

        encoder.update();

        time_since_encoder_touched += period_ms;

        let ticks = encoder.poke_ticks();
        if ticks >= 1 {
            for _ in 0..ticks {
                opts.tick_up();
            }
            time_since_encoder_touched = 0;
        }
        if ticks <= -1 {
            for _ in ticks..0 {
                opts.tick_down();
            }
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

        use tiliqua_fw::opts::{VoiceOptions, ModulationTarget, VoiceModulationType};

        let x = pmod.sample_i();

        let voices: [&mut VoiceOptions; 3] = [
            &mut opts.voice1,
            &mut opts.voice2,
            &mut opts.voice3,
        ];

        let mods: [ModulationTarget; 4] = [
            opts.modulate.in0.value,
            opts.modulate.in1.value,
            opts.modulate.in2.value,
            opts.modulate.in3.value,
        ];

        for n_voice in 0usize..3usize {

            let base = (7*n_voice) as u8;

            // MODULATION

            let mut freq: u16 = voices[n_voice].freq.value;
            let mut gate = voices[n_voice].gate.value;
            for (ch, m) in mods.iter().enumerate() {
                if let Some(VoiceModulationType::Frequency) = m.modulates_voice(n_voice) {
                    let volts: f32 = (x[ch] as f32) / 4096.0f32;
                    let freq_hz = volts_to_freq(volts);
                    freq = 16u16 * (0.05960464f32 * freq_hz) as u16; // assumes 1Mhz SID clk
                                                                     // http://www.sidmusic.org/sid/sidtech2.html
                }
                if let Some(VoiceModulationType::Gate) = m.modulates_voice(n_voice) {
                    if x[ch] > 2000 {
                        gate = 1;
                    }
                    if x[ch] < 1000 {
                        gate = 0;
                    }
                }
            }

            // Propagate modulation back to menu system

            voices[n_voice].freq.value = freq;
            voices[n_voice].gate.value = gate;

            freq = (freq as f32 * (voices[n_voice].freq_os.value as f32 / 1000.0f32)) as u16;

            sid_poke(&sid, base+0, freq as u8);
            sid_poke(&sid, base+1, (freq>>8) as u8);

            sid_poke(&sid, base+2, voices[n_voice].pw.value as u8);
            sid_poke(&sid, base+3, (voices[n_voice].pw.value>>8) as u8);


            let mut reg04 = 0u8;
            use crate::opts::Wave;
            match voices[n_voice].wave.value {
                Wave::Triangle => { reg04 |= 0x10; }
                Wave::Saw      => { reg04 |= 0x20; }
                Wave::Pulse    => { reg04 |= 0x40; }
                Wave::Noise    => { reg04 |= 0x80; }
            }

            reg04 |= gate;
            reg04 |= voices[n_voice].sync.value << 1;
            reg04 |= voices[n_voice].ring.value << 2;

            sid_poke(&sid, base+4, reg04);

            sid_poke(&sid, base+5,
                voices[n_voice].decay.value |
                (voices[n_voice].attack.value << 4));

            sid_poke(&sid, base+6,
                voices[n_voice].release.value |
                (voices[n_voice].sustain.value << 4));
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

        scope.hue().write(|w| unsafe { w.hue().bits(0) } );
        scope.intensity().write(|w| unsafe { w.intensity().bits(10) } );

        scope.trigger_lvl().write(|w| unsafe { w.trigger_lvl().bits(opts.scope.trigger_lvl.value as u16) } );
        scope.xscale().write(|w| unsafe { w.xscale().bits(opts.scope.xscale.value) } );
        scope.yscale().write(|w| unsafe { w.yscale().bits(opts.scope.yscale.value) } );
        scope.timebase().write(|w| unsafe { w.timebase().bits(opts.scope.timebase.value) } );

        scope.ypos0().write(|w| unsafe { w.ypos0().bits(opts.scope.ypos0.value as u16) } );
        scope.ypos1().write(|w| unsafe { w.ypos1().bits(opts.scope.ypos1.value as u16) } );
        scope.ypos2().write(|w| unsafe { w.ypos2().bits(opts.scope.ypos2.value as u16) } );
        scope.ypos3().write(|w| unsafe { w.ypos3().bits(opts.scope.ypos3.value as u16) } );

        scope.xpos().write(|w| unsafe { w.xpos().bits(opts.scope.xpos.value as u16) } );

        scope.trigger_always().write(
            |w| w.trigger_always().bit(opts.scope.trigger_mode.value == opts::TriggerMode::Always) );
    }
}
