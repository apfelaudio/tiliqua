#![no_std]
#![no_main]

use critical_section::Mutex;
use log::info;
use riscv_rt::entry;
use irq::handler;
use core::cell::RefCell;

use midi_types::*;
use midi_convert::parse::MidiTryParseSlice;

use tiliqua_fw::*;
use tiliqua_lib::*;
use tiliqua_lib::dsp::OnePoleSmoother;
use pac::constants::*;
use tiliqua_lib::calibration::*;

use tiliqua_hal::embedded_graphics::prelude::*;

use options::*;
use opts::persistence::*;
use opts::{Options, OptionTrait};
use opts::cc_map::{MidiCcMapper, CcMapMode};
use hal::pca9635::Pca9635Driver;
use tiliqua_hal::dma_framebuffer::Rotate;
use tiliqua_hal::tusb322::{TUSB322Driver, TUSB322Mode, AttachedState};
use tiliqua_hal::persist::Persist;

pub const TIMER0_ISR_PERIOD_MS: u32 = 5;

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
    // Vector page (CC 20-27)
    m.add(20, global_index(opts, &opts.vector.x_offset),  CcMapMode::Absolute);
    m.add(21, global_index(opts, &opts.vector.x_scale),   CcMapMode::Absolute);
    m.add(22, global_index(opts, &opts.vector.y_offset),  CcMapMode::Absolute);
    m.add(23, global_index(opts, &opts.vector.y_scale),   CcMapMode::Absolute);
    m.add(24, global_index(opts, &opts.vector.i_offset),  CcMapMode::Absolute);
    m.add(25, global_index(opts, &opts.vector.i_scale),   CcMapMode::Absolute);
    m.add(26, global_index(opts, &opts.vector.c_offset),  CcMapMode::Absolute);
    m.add(27, global_index(opts, &opts.vector.c_scale),   CcMapMode::Absolute);
    // Delay page (CC 30-33)
    m.add(30, global_index(opts, &opts.delay.delay_x),    CcMapMode::Absolute);
    m.add(31, global_index(opts, &opts.delay.delay_y),    CcMapMode::Absolute);
    m.add(32, global_index(opts, &opts.delay.delay_i),    CcMapMode::Absolute);
    m.add(33, global_index(opts, &opts.delay.delay_c),    CcMapMode::Absolute);
    // Beam page (CC 40-45, CC 41 formerly decay now unused)
    m.add(40, global_index(opts, &opts.beam.persist),     CcMapMode::Absolute);
    m.add(42, global_index(opts, &opts.beam.ui_hue),      CcMapMode::Absolute);
    m.add(43, global_index(opts, &opts.beam.palette),     CcMapMode::Absolute);
    m.add(44, global_index(opts, &opts.beam.grid),        CcMapMode::Absolute);
    m.add(45, global_index(opts, &opts.beam.grid_i),      CcMapMode::Absolute);
    // Misc page (CC 50-52)
    m.add(50, global_index(opts, &opts.misc.plot_type),   CcMapMode::Absolute);
    m.add(51, global_index(opts, &opts.misc.plot_src),    CcMapMode::Absolute);
    m.add(52, global_index(opts, &opts.misc.rotation),    CcMapMode::Absolute);
    // Scope1 page (CC 60-64)
    m.add(60, global_index(opts, &opts.scope1.ypos0),     CcMapMode::Absolute);
    m.add(61, global_index(opts, &opts.scope1.ypos1),     CcMapMode::Absolute);
    m.add(62, global_index(opts, &opts.scope1.ypos2),     CcMapMode::Absolute);
    m.add(63, global_index(opts, &opts.scope1.ypos3),     CcMapMode::Absolute);
    m.add(64, global_index(opts, &opts.scope1.n_channels), CcMapMode::Absolute);
    // Scope2 page (CC 70-76)
    m.add(70, global_index(opts, &opts.scope2.yscale),    CcMapMode::Absolute);
    m.add(71, global_index(opts, &opts.scope2.timebase),  CcMapMode::Absolute);
    m.add(72, global_index(opts, &opts.scope2.xzoom),     CcMapMode::Absolute);
    m.add(73, global_index(opts, &opts.scope2.trig_mode), CcMapMode::Absolute);
    m.add(74, global_index(opts, &opts.scope2.trig_lvl),  CcMapMode::Absolute);
    m.add(75, global_index(opts, &opts.scope2.intensity), CcMapMode::Absolute);
    m.add(76, global_index(opts, &opts.scope2.hue),       CcMapMode::Absolute);
    m
}

struct App {
    ui: ui::UI<Encoder0, EurorackPmod0, I2c0, Opts>,
    cc_mapper: MidiCcMapper,
}

impl App {
    pub fn new(opts: Opts) -> Self {
        let peripherals = unsafe { pac::Peripherals::steal() };
        let encoder = Encoder0::new(peripherals.ENCODER0);
        let i2cdev = I2c0::new(peripherals.I2C0);
        let pca9635 = Pca9635Driver::new(i2cdev);
        let pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
        let cc_mapper = build_cc_mapper(&opts);
        Self {
            ui: ui::UI::new(opts, TIMER0_ISR_PERIOD_MS,
                            encoder, pca9635, pmod),
            cc_mapper,
        }
    }
}

fn timer0_handler(app: &Mutex<RefCell<App>>) {
    critical_section::with(|cs| {
        let mut app = app.borrow_ref_mut(cs);
        app.ui.update();

        // Check for TRS MIDI CC traffic
        let xbeam = unsafe { pac::XBEAM_PERIPH::steal() };
        let midi_word = xbeam.midi_read().read().bits();
        if midi_word != 0 {
            app.ui.midi_activity();
            let bytes = [
                (midi_word & 0xFF) as u8,
                ((midi_word >> 8) & 0xFF) as u8,
                ((midi_word >> 16) & 0xFF) as u8,
            ];
            if let Ok(msg) = MidiMessage::try_parse_slice(&bytes) {
                if let MidiMessage::ControlChange(_, cc, val) = msg {
                    if let Some(action) = app.cc_mapper.process(cc.into(), val.into()) {
                        if app.ui.opts.misc.cc_highlight.value == CcHighlight::On {
                            app.ui.opts.select_global(action.global_index);
                            app.ui.external_modify();
                        }
                        apply_cc_action(&mut app.ui.opts, &action);
                    }
                }
            }
        }

        if app.ui.opts.misc.help.value == HelpPage::Off
            && app.ui.opts.tracker.page.value == Page::Help {
            app.ui.opts.tracker.page.value = Page::Vector;
        }
        app.ui.opts.misc.plot_type.value = match app.ui.opts.tracker.page.value {
            Page::Vector => PlotType::Vector,
            Page::Scope1 => PlotType::Scope,
            Page::Scope2 => PlotType::Scope,
            _ => app.ui.opts.misc.plot_type.value
        };
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

    info!("Hello from Tiliqua XBEAM!");

    let bootinfo = unsafe { bootinfo::BootInfo::from_addr(BOOTINFO_BASE) }.unwrap();
    let modeline = bootinfo.modeline.maybe_override_fixed(
        FIXED_MODELINE, CLOCK_DVI_HZ);
    let mut display = hal::new_framebuffer!(peripherals, modeline.clone());

    let mut i2cdev1 = I2c1::new(peripherals.I2C1);
    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
    CalibrationConstants::load_or_default(&mut i2cdev1, &mut pmod);

    //
    // Start up TUSB322 in UFP/Device mode
    //

    let i2cdev_tusb = I2c0::new(unsafe { pac::I2C0::steal() } );
    let mut tusb322 = TUSB322Driver::new(i2cdev_tusb);
    tusb322.soft_reset().ok();
    tusb322.set_mode(TUSB322Mode::Ufp).ok();

    //
    // Create options and maybe load from persistent storage
    //

    let mut opts = Opts::default();
    opts.misc.rotation.value = modeline.rotate.clone();
    let mut flash_persist_opt = FlashOptionsPersistence::open(
        spiflash, bootinfo.manifest.get_option_storage_window(), &mut opts);

    //
    // Create App instance
    //

    let mut last_palette = opts.beam.palette.value;
    let app = Mutex::new(RefCell::new(App::new(opts)));

    handler!(timer0 = || timer0_handler(&app));

    let mut delay_smoothers = [OnePoleSmoother::new(0.05f32); 4];

    irq::scope(|s| {

        s.register(handlers::Interrupt::TIMER0, timer0);

        timer.enable_tick_isr(TIMER0_ISR_PERIOD_MS,
                              pac::Interrupt::TIMER0);

        let mut vscope = Vector0::new(peripherals.VECTOR_PERIPH);
        let mut scope = Scope0::new(peripherals.SCOPE_PERIPH, 6);
        let xbeam_mux = peripherals.XBEAM_PERIPH;
        let overlay_periph = peripherals.OVERLAY_PERIPH;
        let mut first = true;

        let mut usb_cc_attached = false;

        // Grid overlay operates in DVI pixel space (pre-rotation)
        let dvi_w = modeline.h_active as u32;
        let dvi_h = modeline.v_active as u32;
        overlay_periph.grid_offset().write(|w| unsafe {
            w.offset_x().bits((dvi_w / 2) as u16);
            w.offset_y().bits((dvi_h / 2) as u16)
        });

        loop {

            let h_active = display.size().width;
            let v_active = display.size().height;

            let (opts, draw_options, save_opts, wipe_opts) = critical_section::with(|cs| {
                let mut app = app.borrow_ref_mut(cs);
                let save_opts = app.ui.opts.misc.save_opts.poll();
                let wipe_opts = app.ui.opts.misc.wipe_opts.poll();
                (app.ui.opts.clone(), app.ui.draw(), save_opts, wipe_opts)
            });

            let on_help_page = opts.tracker.page.value == Page::Help;

            if opts.beam.palette.value != last_palette || first {
                opts.beam.palette.value.write_to_hardware(&mut display);
                last_palette = opts.beam.palette.value;
            }

            if draw_options || on_help_page {
                let (x, y) = if on_help_page {
                    (h_active/2-30, v_active-100)
                } else {
                    (h_active-200, v_active/2)
                };
                draw::draw_options(&mut display, &opts, x, y, opts.beam.ui_hue.value).ok();
                draw::draw_name(&mut display, h_active/2, v_active-50, opts.beam.ui_hue.value,
                                &bootinfo.manifest.name, &bootinfo.manifest.tag, &modeline).ok();
            }

            if on_help_page {
                draw::draw_help_page(&mut display,
                    MODULE_DOCSTRING,
                    bootinfo.manifest.help.as_ref(),
                    h_active,
                    v_active,
                    opts.help.scroll.value,
                    opts.beam.ui_hue.value).ok();
                persist.set_persistence(64);
            } else {
                persist.set_persistence(opts.beam.persist.value);
            }


            if save_opts {
                if let Some(ref mut flash_persist) = flash_persist_opt {
                    flash_persist.save_options(&opts).unwrap();
                }
            }

            if wipe_opts {
                critical_section::with(|cs| {
                    let mut app = app.borrow_ref_mut(cs);
                    app.ui.opts = Opts::default();
                    app.ui.opts.misc.rotation.value = modeline.rotate.clone();
                    if let Some(ref mut flash_persist) = flash_persist_opt {
                        flash_persist.erase_all().unwrap();
                    }
                });
            }

            let (ppd_x, ppd_y) = vscope.pixels_per_div();
            vscope.set_xoffset_px(opts.vector.x_offset.value * (ppd_x / 4) as i16);
            vscope.set_yoffset_px(opts.vector.y_offset.value * (ppd_y / 4) as i16);
            vscope.set_xscale(opts.vector.x_scale.value);
            vscope.set_yscale(opts.vector.y_scale.value);
            vscope.set_pscale(opts.vector.i_scale.value);
            vscope.set_intensity(opts.vector.i_offset.value);
            vscope.set_cscale(opts.vector.c_scale.value);
            vscope.set_hue(opts.vector.c_offset.value);

            scope.set_hue(opts.scope2.hue.value);
            scope.set_intensity(opts.scope2.intensity.value);
            scope.set_trigger_level(opts.scope2.trig_lvl.value);
            scope.set_yscale(opts.scope2.yscale.value);
            let xscale_bits: u8 = match opts.scope2.xzoom.value {
                XZoom::Half   => 7,
                XZoom::Normal => 6,
                XZoom::Double => 5,
            };
            scope.set_xscale(xscale_bits);
            scope.set_timebase(opts.scope2.timebase.value);
            let (sppd_x, sppd) = scope.pixels_per_div();
            let n_ch = opts.scope1.n_channels.value;
            let ypos = [opts.scope1.ypos0.value, opts.scope1.ypos1.value,
                         opts.scope1.ypos2.value, opts.scope1.ypos3.value];
            for ch in 0..4u8 {
                let pos = if ch < n_ch {
                    ypos[ch as usize] * (sppd / 4) as i16
                } else {
                    750 // hide inactive channels off-screen
                };
                scope.set_ypos_px(ch.into(), pos);
            }

            // Only connect USB PHY if the TUSB322 Type-C controller says we are attached.
            // This fixes enumeration issues on some machines when using typec <-> typec cables.
            critical_section::with(|_| {
                if let Ok(status) = tusb322.read_connection_status_control() {
                    // Only update on valid reads to reduce risk of unintended toggling mid-stream
                    let new_state = status.attached_state == AttachedState::AttachedSnk;
                    if new_state != usb_cc_attached {
                        info!("USB CC hotplug: {:?}", status);
                        usb_cc_attached = new_state;
                    }
                }
            });

            xbeam_mux.flags().write(
                |w| { w.usb_en().bit(opts.misc.usb_mode.value == USBMode::Enable);
                      w.show_outputs().bit(opts.misc.plot_src.value == PlotSrc::Outputs);
                      w.usb_connect().bit(usb_cc_attached)
                } );

            // Update grid spacing. In scope mode use the scope's ppd_x for the
            // time axis. In portrait rotation the time/signal axes are swapped
            // relative to DVI x/y.
            let (scope_ppd_time, scope_ppd_sig) = if opts.misc.plot_type.value == PlotType::Scope {
                (sppd_x, ppd_y)
            } else {
                (ppd_x, ppd_y)
            };
            let is_portrait = matches!(opts.misc.rotation.value, Rotate::Left | Rotate::Right);
            let (dvi_ppd_x, dvi_ppd_y) = if is_portrait {
                (scope_ppd_sig, scope_ppd_time)
            } else {
                (scope_ppd_time, scope_ppd_sig)
            };
            overlay_periph.grid_spacing().write(|w| unsafe {
                w.spacing_x().bits(dvi_ppd_x as u8);
                w.spacing_y().bits(dvi_ppd_y as u8)
            });
            overlay_periph.grid_start().write(|w| unsafe {
                w.start_x().bits(((dvi_w / 2) % dvi_ppd_x) as u8);
                w.start_y().bits((((dvi_h / 2) + 1) % dvi_ppd_y) as u8)
            });

            // Grid overlay style/pixel (changes with options)
            let grid_style: u8 = if on_help_page { 0 } else {
                match opts.beam.grid.value {
                    GridOverlay::Off => 0,
                    GridOverlay::Grid => 1,
                    GridOverlay::Cross => 2,
                }
            };
            overlay_periph.flags().write(|w| unsafe {
                w.grid_style().bits(grid_style);
                w.grid_pixel().bits(((opts.beam.grid_i.value as u8) << 4) | opts.beam.ui_hue.value)
            });

            xbeam_mux.delay0().write(|w| unsafe { w.value().bits(
                    delay_smoothers[0].proc_u16(opts.delay.delay_x.value)) });
            xbeam_mux.delay1().write(|w| unsafe { w.value().bits(
                    delay_smoothers[1].proc_u16(opts.delay.delay_y.value)) });
            xbeam_mux.delay2().write(|w| unsafe { w.value().bits(
                    delay_smoothers[2].proc_u16(opts.delay.delay_i.value)) });
            xbeam_mux.delay3().write(|w| unsafe { w.value().bits(
                    delay_smoothers[3].proc_u16(opts.delay.delay_c.value)) });

            display.rotate(&opts.misc.rotation.value);


            if opts.tracker.page.value == Page::Help {
                scope.set_enabled(false, false);
                vscope.set_enabled(false);
            } else {
                if opts.misc.plot_type.value == PlotType::Vector {
                    scope.set_enabled(false, false);
                    vscope.set_enabled(true);
                } else {
                    scope.set_enabled(true, opts.scope2.trig_mode.value == TriggerMode::Always);
                    vscope.set_enabled(false);
                }
            }

            first = false;
        }
    })
}
