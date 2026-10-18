#![no_std]
#![no_main]

use critical_section::Mutex;
use log::info;
use riscv_rt::entry;
use irq::handler;
use core::cell::RefCell;

use tiliqua_pac as pac;
use tiliqua_hal as hal;
use tiliqua_fw::*;
use tiliqua_lib::*;
use tiliqua_lib::color::HI8;
use pac::constants::*;
use tiliqua_hal::pmod::EurorackPmod;
use tiliqua_hal::persist::Persist;

use tiliqua_hal::embedded_graphics::{
    prelude::*,
    mono_font::{MonoTextStyle, ascii::FONT_9X15_BOLD},
    text::Text,
};

use options::Opts;
use opts::persistence::*;
use hal::pca9635::Pca9635Driver;

use micromath::F32Ext;

pub const TIMER0_ISR_PERIOD_MS: u32 = 5;

struct App {
    ui: ui::UI<Encoder0, EurorackPmod0, I2c0, Opts>,
}

impl App {
    pub fn new(opts: Opts) -> Self {
        let peripherals = unsafe { pac::Peripherals::steal() };
        let encoder = Encoder0::new(peripherals.ENCODER0);
        let i2cdev = I2c0::new(peripherals.I2C0);
        let pca9635 = Pca9635Driver::new(i2cdev);
        let pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
        Self {
            ui: ui::UI::new(opts, TIMER0_ISR_PERIOD_MS,
                            encoder, pca9635, pmod),
        }
    }
}

fn volts_to_freq(volts: f32) -> f32 {
    let a3_freq_hz: f32 = 440.0f32;
    (a3_freq_hz / 2.0f32) * (2.0f32).powf(volts + 2.0f32 - 3.0f32/4.0f32)
}

fn timer0_handler(app: &Mutex<RefCell<App>>) {

    use tiliqua_fw::options::{VoiceOpts, ModulationTarget, VoiceModulationType};

    let peripherals = unsafe { pac::Peripherals::steal() };
    let sid = peripherals.SID_PERIPH;
    let sid_poke = |_sid: &pac::SID_PERIPH, addr: u8, data: u8| {
        _sid.transaction_data().write(
            |w| unsafe { w.transaction_data().bits(((data as u16) << 5) | (addr as u16)) } );
    };

    let (mut opts, x) = critical_section::with(|cs| {
        let mut app = app.borrow_ref_mut(cs);
        app.ui.update();
        (app.ui.opts.clone(), app.ui.pmod.sample_i())
    });

    let voices: [&mut VoiceOpts; 3] = [
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
        use crate::options::Wave;
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

    critical_section::with(|cs| {
        let mut app = app.borrow_ref_mut(cs);
        app.ui.opts.voice1.freq.value = voices[0].freq.value;
        app.ui.opts.voice1.gate.value = voices[0].gate.value;
        app.ui.opts.voice2.freq.value = voices[1].freq.value;
        app.ui.opts.voice2.gate.value = voices[1].gate.value;
        app.ui.opts.voice3.freq.value = voices[2].freq.value;
        app.ui.opts.voice3.gate.value = voices[2].gate.value;
    });
}

#[entry]
fn main() -> ! {
    let peripherals = pac::Peripherals::take().unwrap();

    let sysclk = pac::clock::sysclk();
    let serial = Serial0::new(peripherals.UART0);
    let mut timer = Timer0::new(peripherals.TIMER0, sysclk);
    let mut persist = Persist0::new(peripherals.PERSIST_PERIPH);
    let spiflash = SPIFlash0::new(
        peripherals.SPIFLASH_CTRL,
        SPIFLASH_BASE,
        SPIFLASH_SZ_BYTES
    );

    tiliqua_fw::handlers::logger_init(serial);

    info!("Hello from Tiliqua SID!");

    let bootinfo = unsafe { bootinfo::BootInfo::from_addr(BOOTINFO_BASE) }.unwrap();
    let modeline = bootinfo.modeline.maybe_override_fixed(
        FIXED_MODELINE, CLOCK_DVI_HZ);
    let mut display = hal::new_framebuffer!(peripherals, modeline.clone());

    let mut i2cdev1 = I2c1::new(peripherals.I2C1);
    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
    calibration::CalibrationConstants::load_or_default(&mut i2cdev1, &mut pmod);

    //
    // Create options and maybe load from persistent storage
    //

    let mut opts = options::Opts::default();
    let mut flash_persist_opt = FlashOptionsPersistence::open(
        spiflash, bootinfo.manifest.get_option_storage_window(), &mut opts);

    //
    // Create App instance
    //

    let app = Mutex::new(RefCell::new(App::new(opts)));
    let hue = 5u8;

    palette::ColorPalette::default().write_to_hardware(&mut display);

    handler!(timer0 = || timer0_handler(&app));

    irq::scope(|s| {

        s.register(handlers::Interrupt::TIMER0, timer0);

        timer.enable_tick_isr(TIMER0_ISR_PERIOD_MS, 
                            pac::Interrupt::TIMER0);

        let mut scope = Scope0::new(peripherals.SCOPE_PERIPH, 7);

        let h_active = display.size().width;
        let v_active = display.size().height;

        loop {
            let (opts, save_opts, wipe_opts) = critical_section::with(|cs| {
                let mut app = app.borrow_ref_mut(cs);
                let save_opts = app.ui.opts.misc.save_opts.poll();
                let wipe_opts = app.ui.opts.misc.wipe_opts.poll();
                (app.ui.opts.clone(), save_opts, wipe_opts)
            });

            if save_opts {
                if let Some(ref mut flash_persist) = flash_persist_opt {
                    flash_persist.save_options(&opts).unwrap();
                }
            }

            if wipe_opts {
                critical_section::with(|cs| {
                    let mut app = app.borrow_ref_mut(cs);
                    app.ui.opts = options::Opts::default();
                    if let Some(ref mut flash_persist) = flash_persist_opt {
                        flash_persist.erase_all().unwrap();
                    }
                });
            }

            let on_help_page = opts.tracker.page.value == options::Page::Help;

            // Draw UI elements
            if on_help_page {
                persist.set_persistence(64);
                draw::draw_options(&mut display, &opts, h_active/2-30, v_active-100, hue).ok();
                draw::draw_name(&mut display, h_active/2, v_active-50, hue,
                                &bootinfo.manifest.name, &bootinfo.manifest.tag, &modeline).ok();
                draw::draw_help_page(&mut display,
                    MODULE_DOCSTRING,
                    bootinfo.manifest.help.as_ref(),
                    h_active,
                    v_active,
                    opts.help.scroll.value,
                    hue).ok();
            } else {
                persist.set_persistence(15);
                draw::draw_options(&mut display, &opts, 100, v_active/2, hue).ok();
                draw::draw_name(&mut display, h_active/2, v_active-50, hue,
                                &bootinfo.manifest.name, &bootinfo.manifest.tag, &modeline).ok();

                // Draw SID visualization
                let hl_wfm: Option<u8> = match opts.tracker.page.value {
                    options::Page::Voice1 => Some(0),
                    options::Page::Voice2 => Some(1),
                    options::Page::Voice3 => Some(2),
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

                let hl_filter: bool = opts.tracker.page.value == options::Page::Filter;

                draw::draw_sid(&mut display, 100, v_active/4+25, hue, hl_wfm, gates, hl_filter, switches, filter_types).ok();

                // Draw channel labels
                {
                    let font_small_white = MonoTextStyle::new(&FONT_9X15_BOLD, HI8::new(hue, 0xB));
                    let hc = (h_active/2) as i16;
                    let vc = (v_active/2) as i16;

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
            }

            // Update scope settings
            scope.set_trigger_level(opts.scope.trig_lvl.value);
            scope.set_yscale(opts.scope.yscale.value);
            scope.set_timebase(opts.scope.timebase.value);
            scope.set_hue(hue);
            scope.set_ypos_px(0, opts.scope.ypos0.value);
            scope.set_ypos_px(1, opts.scope.ypos1.value);
            scope.set_ypos_px(2, opts.scope.ypos2.value);
            scope.set_ypos_px(3, opts.scope.ypos3.value);
            scope.set_xpos_px(opts.scope.xpos.value);

            if on_help_page {
                scope.set_enabled(false, false);
            } else {
                scope.set_enabled(true, opts.scope.trig_mode.value == options::TriggerMode::Always);
            }
        }
    })
}
