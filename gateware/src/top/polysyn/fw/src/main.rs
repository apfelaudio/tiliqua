#![no_std]
#![no_main]

use critical_section::Mutex;
use log::{info, warn};
use riscv_rt::entry;
use irq::handler;
use core::cell::RefCell;

use micromath::F32Ext;
use midi_types::*;
use midi_convert::render_slice::MidiRenderSlice;
use midi_convert::parse::MidiTryParseSlice;

use tiliqua_pac as pac;
use tiliqua_hal as hal;
use tiliqua_lib::*;
use tiliqua_lib::draw;
use tiliqua_lib::dsp::OnePoleSmoother;
use tiliqua_lib::midi::MidiTouchController;
use pac::constants::*;
use tiliqua_hal::persist::Persist;
use tiliqua_fw::*;
use tiliqua_fw::options::*;
use opts::{Options, OptionTrait};
use opts::cc_map::{MidiCcMapper, CcMapMode};
use tiliqua_hal::pmod::EurorackPmod;

use tiliqua_hal::embedded_graphics::prelude::*;

use opts::persistence::*;
use hal::pca9635::Pca9635Driver;
use hal::tusb322::{TUSB322Driver, TUSB322Mode, AttachedState, AccessoryType};

use tiliqua_fw::wavetable;

pub const TIMER0_ISR_PERIOD_MS: u32 = 5;

fn adsr_ui_to_rate(ui_value: u16) -> u16 {
    // 0..32768 -> 1ms..2000ms -> hardware rate
    let ms_x32k = 32768u32 + ui_value as u32 * 1999;
    let rate = (45_000_000u64 / ms_x32k as u64) as u32;
    rate.min(65535) as u16
}

fn timer0_handler(app: &Mutex<RefCell<App>>) {

    critical_section::with(|cs| {

        let mut app = app.borrow_ref_mut(cs);

        //
        // Update UI and options
        //

        app.ui.update();
        let opts = app.ui.opts.clone();

        //
        // Check for TRS/USB MIDI traffic
        // (this is forwarded by the hardware to the synth
        //  for minimum possible latency, here we peek
        //  the FIFO contents for debugging purposes)
        //

        let mut last_cc_index = None;
        let midi_word = app.synth.midi_read();
        if midi_word != 0 {
            // Blink MIDI activity LED on TRS port
            app.ui.midi_activity();

            let bytes = [
                (midi_word & 0xFF) as u8,
                ((midi_word >> 8) & 0xFF) as u8,
                ((midi_word >> 16) & 0xFF) as u8,
            ];
            if let Ok(msg) = MidiMessage::try_parse_slice(&bytes) {
                if let MidiMessage::ControlChange(_, cc, val) = msg {
                    if let Some(action) = app.cc_mapper.process(cc.into(), val.into()) {
                        last_cc_index = Some(action.global_index);
                        apply_cc_action(&mut app.ui.opts, &action);
                    }
                }
            }

            // Optionally dump raw MIDI messages out serial port.
            if opts.misc.serial_debug.value == UsbMidiSerialDebug::On {
                info!("midi: 0x{:x} 0x{:x} 0x{:x}",
                      bytes[0], bytes[1], bytes[2]);
            }
        }

        // Highlight the last CC-modified option (once per cycle)
        if opts.misc.cc_highlight.value == CcHighlight::On {
            if let Some(idx) = last_cc_index {
                app.ui.opts.select_global(idx);
                app.ui.external_modify();
            }
        }

        //
        // Update synthesizer
        //

        let drive_smooth = app.drive_smoother.proc_u16(opts.effect.drive.value);
        app.synth.set_drive(drive_smooth);

        // Map 0-1 UI range to 32768-8192 hardware range (inverted)
        let reso_ui = opts.voice.reso.value as u32;
        let reso_hw = (32768 - reso_ui * 24576 / 32768) as u16;
        let reso_smooth = app.reso_smoother.proc_u16(reso_hw);
        app.synth.set_reso(reso_smooth);

        let diffuse_smooth = app.diffusion_smoother.proc_u16(opts.effect.diffuse.value);
        let coeff_dry: i32 = (32768 - diffuse_smooth) as i32;
        let coeff_wet: i32 = diffuse_smooth as i32;

        app.synth.set_matrix_coefficient(0, 0, coeff_dry);
        app.synth.set_matrix_coefficient(1, 1, coeff_dry);
        app.synth.set_matrix_coefficient(2, 2, coeff_dry);
        app.synth.set_matrix_coefficient(3, 3, coeff_dry);

        app.synth.set_matrix_coefficient(0, 4, coeff_wet);
        app.synth.set_matrix_coefficient(1, 5, coeff_wet);
        app.synth.set_matrix_coefficient(2, 6, coeff_wet);
        app.synth.set_matrix_coefficient(3, 7, coeff_wet);

        let mpe = opts.misc.mpe.value == Mpe::On;
        app.synth.set_mpe(mpe);
        app.synth.set_pitch_bend_range(opts.misc.pb_range.value.semitones());
        app.synth.set_midi_channel_filter(
            if mpe { None } else { opts.misc.midi_ch.value.to_filter() });

        // ADSR params
        app.synth.set_attack_rate(adsr_ui_to_rate(opts.adsr.attack.value));
        app.synth.set_decay_rate(adsr_ui_to_rate(opts.adsr.decay.value));
        app.synth.set_sustain_level((opts.adsr.sustain.value as u32 * 65535 / 32768) as u16);
        app.synth.set_release_rate(adsr_ui_to_rate(opts.adsr.release.value));

        // LFO -> phase modulation CSR
        {
            use wavetable::{Fix32, CYCLE_LEN};
            let isr_rate = Fix32::from_num(1000u32 / TIMER0_ISR_PERIOD_MS);
            // lfo_rate option: 0..50 -> 0..5.0 Hz
            // Q16.16 from_bits: value * 65536 / 10 = value * 6554
            let rate_hz = Fix32::from_bits(opts.voice.lfo_rate.value as i32 * 6554);
            let phase_inc = rate_hz * CYCLE_LEN as i32 / isr_rate;
            // depth option: 0..32768 -> 0..1.0
            let depth = Fix32::from_bits(opts.voice.lfo_depth.value as i32 * 2);
            let lfo_val = wavetable::wt_lfo(&mut app.lfo_phase, phase_inc, depth,
                                           Waveform::Sine);
            app.synth.set_lfo(lfo_val);
        }

        // Wavetable update on parameter change
        if opts.voice.waveform.value != app.last_waveform
            || opts.voice.proc.value != app.last_proc_mode
            || opts.voice.proc_amt.value != app.last_proc_amt
        {
            wavetable::wt_write(&mut app.synth, opts.voice.waveform.value,
                                opts.voice.proc.value, opts.voice.proc_amt.value);
            app.last_waveform = opts.voice.waveform.value;
            app.last_proc_mode = opts.voice.proc.value;
            app.last_proc_amt = opts.voice.proc_amt.value;
        }

        // Touch controller logic (sends MIDI to internal polysynth)
        if opts.misc.touch_ctrl.value == TouchControl::On {
            app.ui.touch_led_mask(0b00111111);
            let touch = app.ui.pmod.touch();
            let jack = app.ui.pmod.jack();

            // Output 1 (jack 5): auto mode when plugged (shows clock from DAC)
            if (jack & (1 << 5)) != 0 {
                app.ui.pmod.led_set_auto(5);
            }
            let msgs = app.touch_controller.update(&touch, jack);
            for msg in msgs {
                if msg != MidiMessage::Stop {
                    // TODO move MidiMessage rendering into HAL, perhaps
                    // even inside synth.midi_write.
                    let mut bytes = [0u8; 3];
                    msg.render_slice(&mut bytes);
                    let v: u32 = (bytes[2] as u32) << 16 |
                                 (bytes[1] as u32) << 8 |
                                 (bytes[0] as u32) << 0;
                    app.synth.midi_write(v);
                }
            }
        }
    });
}

fn global_index(opts: &Opts, opt: &dyn OptionTrait) -> usize {
    let key = opt.key().value();
    opts.all().enumerate()
        .find(|(_, o)| o.key().value() == key)
        .expect("cc_map: option key not found").0
}

fn apply_cc_action(opts: &mut Opts, action: &opts::cc_map::CcAction) {
    if let Some(opt) = opts.all_mut().nth(action.global_index) {
        match action.mode {
            CcMapMode::Absolute => { opt.set_from_cc(action.cc_value); }
        }
    }
}

fn build_cc_mapper(opts: &Opts) -> MidiCcMapper {
    let mut m = MidiCcMapper::new();
    // Voice page (CC 20-25)
    m.add(20, global_index(opts, &opts.voice.waveform),  CcMapMode::Absolute);
    m.add(21, global_index(opts, &opts.voice.proc),      CcMapMode::Absolute);
    m.add(22, global_index(opts, &opts.voice.proc_amt),  CcMapMode::Absolute);
    m.add(23, global_index(opts, &opts.voice.reso),      CcMapMode::Absolute);
    m.add(24, global_index(opts, &opts.voice.lfo_rate),  CcMapMode::Absolute);
    m.add(25, global_index(opts, &opts.voice.lfo_depth), CcMapMode::Absolute);
    // ADSR page (CC 30-33)
    m.add(30, global_index(opts, &opts.adsr.attack),  CcMapMode::Absolute);
    m.add(31, global_index(opts, &opts.adsr.decay),   CcMapMode::Absolute);
    m.add(32, global_index(opts, &opts.adsr.sustain), CcMapMode::Absolute);
    m.add(33, global_index(opts, &opts.adsr.release), CcMapMode::Absolute);
    // Effect page (CC 40-41)
    m.add(40, global_index(opts, &opts.effect.drive),    CcMapMode::Absolute);
    m.add(41, global_index(opts, &opts.effect.diffuse),  CcMapMode::Absolute);
    // Beam page (CC 50-55, CC 52 formerly decay now unused)
    m.add(50, global_index(opts, &opts.beam.scale),     CcMapMode::Absolute);
    m.add(51, global_index(opts, &opts.beam.persist),   CcMapMode::Absolute);
    m.add(53, global_index(opts, &opts.beam.intensity), CcMapMode::Absolute);
    m.add(54, global_index(opts, &opts.beam.hue),       CcMapMode::Absolute);
    m.add(55, global_index(opts, &opts.beam.palette),   CcMapMode::Absolute);
    m
}

struct App {
    ui: ui::UI<Encoder0, EurorackPmod0, I2c0, Opts>,
    synth: Polysynth0,
    drive_smoother: OnePoleSmoother,
    reso_smoother: OnePoleSmoother,
    diffusion_smoother: OnePoleSmoother,
    touch_controller: MidiTouchController,
    // wavetable state
    last_waveform: Waveform,
    last_proc_mode: ProcMode,
    last_proc_amt: u16,
    // midi cc mapper
    cc_mapper: MidiCcMapper,
    // lfo phase accumulator
    lfo_phase: wavetable::Fix32,
}

impl App {
    pub fn new(opts: Opts) -> Self {
        let peripherals = unsafe { pac::Peripherals::steal() };
        let encoder = Encoder0::new(peripherals.ENCODER0);
        let pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
        let i2cdev = I2c0::new(peripherals.I2C0);
        let pca9635 = Pca9635Driver::new(i2cdev);
        let drive_smoother = OnePoleSmoother::new(0.05f32);
        let reso_smoother = OnePoleSmoother::new(0.05f32);
        let diffusion_smoother = OnePoleSmoother::new(0.05f32);
        let touch_controller = MidiTouchController::new();
        let cc_mapper = build_cc_mapper(&opts);
        let mut synth = Polysynth0::new(peripherals.SYNTH_PERIPH);
        synth.usb_host_midi(1, 1);
        synth.usb_host_vbus(false); // default, but set anyway...
        Self {
            ui: ui::UI::new(opts, TIMER0_ISR_PERIOD_MS,
                            encoder, pca9635, pmod),
            synth,
            drive_smoother,
            reso_smoother,
            diffusion_smoother,
            touch_controller,
            last_waveform: Waveform::default(),
            last_proc_mode: ProcMode::default(),
            last_proc_amt: 0,
            cc_mapper,
            lfo_phase: wavetable::Fix32::ZERO,
        }
    }
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
    crate::handlers::logger_init(serial);

    info!("Hello from Tiliqua POLYSYN!");

    let bootinfo = unsafe { bootinfo::BootInfo::from_addr(BOOTINFO_BASE) }.unwrap();
    let modeline = bootinfo.modeline.maybe_override_fixed(
        FIXED_MODELINE, CLOCK_DVI_HZ);
    let mut display = DMAFramebuffer0::new(
        peripherals.FRAMEBUFFER_PERIPH,
        peripherals.PALETTE_PERIPH,
        peripherals.BLIT,
        peripherals.PIXEL_PLOT,
        peripherals.LINE,
        PSRAM_FB_BASE,
        modeline.clone(),
        BLIT_MEM_BASE,
    );

    let mut i2cdev1 = I2c1::new(peripherals.I2C1);
    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
    calibration::CalibrationConstants::load_or_default(&mut i2cdev1, &mut pmod);

    use tiliqua_hal::cy8cmbr3xxx::Cy8cmbr3108Driver;
    let i2cdev_cy8 = I2c1::new(unsafe { pac::I2C1::steal() } );
    let mut cy8 = Cy8cmbr3108Driver::new(i2cdev_cy8, &TOUCH_SENSOR_ORDER);

    //
    // Create options and maybe load from persistent storage
    //

    let mut opts = Opts::default();
    let mut flash_persist_opt = if let Some(storage_window) = bootinfo.manifest.get_option_storage_window() {
        let mut flash_persist = FlashOptionsPersistence::new(spiflash, storage_window);
        flash_persist.load_options(&mut opts).unwrap();
        Some(flash_persist)
    } else {
        warn!("No option storage region: disable persistent storage");
        None
    };

    //
    // Configure TUSB322 (CC controller) in DFP/Host mode
    // This is needed if Tiliqua is connected to a device with a true USB-C to USB-C cable.
    // It is also used for detecting (and preventing) applying VBUS if we have a host/host
    // connection.
    //
    // TODO: disable terminations when host mode is disabled?
    // TODO: draw error text if this fails?
    //

    let i2cdev = I2c0::new(peripherals.I2C0);
    let mut tusb322 = TUSB322Driver::new(i2cdev);
    tusb322.soft_reset().ok();
    tusb322.disable_term().ok();

    //
    // Create App instance
    //

    let mut last_palette = opts.beam.palette.value.clone();
    let app = Mutex::new(RefCell::new(App::new(opts)));

    handler!(timer0 = || timer0_handler(&app));

    irq::scope(|s| {

        s.register(handlers::Interrupt::TIMER0, timer0);

        timer.enable_tick_isr(TIMER0_ISR_PERIOD_MS,
                              pac::Interrupt::TIMER0);

        let mut vscope = Vector0::new(peripherals.VECTOR_PERIPH);
        let mut first = true;

        let h_active = display.size().width;
        let v_active = display.size().height;

        let mut last_jack = pmod.jack();

        let mut last_attached_state = AttachedState::NotAttached;
        let mut last_opt_host_enabled = false;

        loop {

            let (opts, notes, cutoffs, draw_options, save_opts, wipe_opts) = critical_section::with(|cs| {
                let mut app = app.borrow_ref_mut(cs);
                if pmod.jack() != last_jack {
                    // Re-calibrate touch sensing on jack swaps.
                    let _ = cy8.reset();
                }
                last_jack = pmod.jack();
                let save_opts = app.ui.opts.misc.save_opts.poll();
                let wipe_opts = app.ui.opts.misc.wipe_opts.poll();

                // USB Host: Disable DFP terminations if `MISC->usb-host` option is off
                //
                // This stops us supplying VCONN on the CC pins for e-tagged cables, fixing an
                // edge case where we may backpower VBUS due to the following bug on
                // early Tiliqua R5.1s: https://github.com/apfaudio/tiliqua-hardware/issues/1
                //

                let opt_host_enabled = app.ui.opts.misc.usb_host.value == UsbHost::On;
                if last_opt_host_enabled != opt_host_enabled {
                    if opt_host_enabled {
                        tusb322.soft_reset().ok();
                        tusb322.set_mode(TUSB322Mode::Dfp).ok();
                    } else {
                        tusb322.disable_term().ok();
                        app.synth.usb_host_vbus(false);
                    }
                }
                last_opt_host_enabled = opt_host_enabled;

                // Type-C hotplugging detection
                //
                // Only enable VBUS if the TUSB322 Type-C controller says we are attached as:
                // - Attached.SRC
                // - Attached.ACCESSORY && AccessoryType.DebugDfp
                //
                // Due to the same hardware bug as above, Tiliqua's host port in DFP mode may
                // have the TUSB322I see some rare adapter combinations as `DebugDfp` accessories,
                // rather than `Attached.SRC`. To work around this, we still power VBUS on `DebugDfp`.
                //

                if let Ok(ctrl) = tusb322.read_connection_status_control() {
                    if let Ok(conn) = tusb322.read_connection_status() {
                        // Only update on valid reads to reduce risk of unintended toggling mid-stream
                        if ctrl.attached_state != last_attached_state {
                            info!("USB CC hotplug: {:?} | {:?}", ctrl, conn);
                            last_attached_state = ctrl.attached_state;
                        }
                        // Check how we are attached
                        let attached_src = ctrl.attached_state == AttachedState::AttachedSrc;
                        let attached_debug_dfp =
                            (ctrl.attached_state == AttachedState::AttachedAccessory) &&
                            (conn.accessory == AccessoryType::DebugDfp);
                        app.synth.usb_host_vbus(opt_host_enabled && (attached_src || attached_debug_dfp));
                    }
                }

                //
                // Copy out all the bits of state we need for drawing
                //

                (app.ui.opts.clone(),
                 app.synth.voice_notes().clone(),
                 app.synth.voice_cutoffs().clone(),
                 app.ui.draw(),
                 save_opts,
                 wipe_opts)
            });

            if save_opts {
                if let Some(ref mut flash_persist) = flash_persist_opt {
                    flash_persist.save_options(&opts).unwrap();
                }
            }

            if wipe_opts {
                critical_section::with(|cs| {
                    let mut app = app.borrow_ref_mut(cs);
                    app.ui.opts = Opts::default();
                    if let Some(ref mut flash_persist) = flash_persist_opt {
                        flash_persist.erase_all().unwrap();
                    }
                });
            }

            let on_help_page = opts.tracker.page.value == Page::Help;

            if opts.beam.palette.value != last_palette || first {
                opts.beam.palette.value.write_to_hardware(&mut display);
                last_palette = opts.beam.palette.value;
            }

            if draw_options || on_help_page {
                let (x, y) = if on_help_page {
                    (h_active/2-30, v_active-100)
                } else {
                    (h_active/2-30, 70)
                };
                draw::draw_options(&mut display, &opts, x, y,
                                   opts.beam.hue.value).ok();
                draw::draw_name(&mut display, h_active/2, v_active-50, opts.beam.hue.value,
                                &bootinfo.manifest.name, &bootinfo.manifest.tag, &modeline).ok();
                if opts.tracker.page.value == Page::Adsr {
                    use draw::AdsrPhase;
                    let highlight = opts.selected().and_then(|i| {
                        match opts.view().options()[i].key().value() {
                            v if v == opts.adsr.attack.key().value()  => Some(AdsrPhase::Attack),
                            v if v == opts.adsr.decay.key().value()   => Some(AdsrPhase::Decay),
                            v if v == opts.adsr.sustain.key().value() => Some(AdsrPhase::Sustain),
                            v if v == opts.adsr.release.key().value() => Some(AdsrPhase::Release),
                            _ => None,
                        }
                    });
                    draw::draw_adsr(&mut display,
                        h_active/2-190, 75,
                        125, 40,
                        opts.adsr.attack.value,
                        opts.adsr.decay.value,
                        opts.adsr.sustain.value,
                        opts.adsr.release.value,
                        opts.beam.hue.value,
                        highlight).ok();
                }
                if opts.tracker.page.value == Page::Voice {
                    const PREVIEW_LEN: usize = 64;
                    let mut preview = [0i16; PREVIEW_LEN];
                    wavetable::wt_preview(&mut preview,
                        opts.voice.waveform.value, opts.voice.proc.value,
                        opts.voice.proc_amt.value);
                    draw::draw_waveform_preview(&mut display,
                        h_active/2-190, 80,
                        125, 40,
                        opts.beam.hue.value,
                        &preview).ok();
                }
            }

            if on_help_page {
                draw::draw_help_page(&mut display,
                    MODULE_DOCSTRING,
                    bootinfo.manifest.help.as_ref(),
                    h_active,
                    v_active,
                    opts.help.scroll.value,
                    opts.beam.hue.value).ok();
                persist.set_persistence(64);
                vscope.set_enabled(false);
            } else {
                persist.set_persistence(opts.beam.persist.value);
                vscope.set_enabled(true);
            }

            vscope.set_hue(opts.beam.hue.value);
            vscope.set_intensity(opts.beam.intensity.value);
            vscope.set_xscale(opts.beam.scale.value);
            vscope.set_yscale(opts.beam.scale.value);

            if !on_help_page {
                for ix in 0usize..N_VOICES {
                    let j = (N_VOICES-1)-ix;
                    draw::draw_voice(&mut display,
                                     ((h_active as f32)/2.0f32 + 330.0f32*f32::cos(2.45f32 + 1.5f32 * j as f32 / (N_VOICES as f32))) as i32,
                                     ((v_active as f32)/2.0f32 + 330.0f32*f32::sin(2.45f32 + 1.5f32 * j as f32 / (N_VOICES as f32))) as u32 - 15,
                                     notes[ix], cutoffs[ix], opts.beam.hue.value).ok();
                }
            }

            first = false;
        }
    })
}
