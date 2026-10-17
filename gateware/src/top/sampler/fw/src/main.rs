#![no_std]
#![no_main]

use critical_section::Mutex;
use log::info;
use riscv_rt::entry;
use irq::handler;
use core::cell::RefCell;
use core::fmt::Write;

use tiliqua_fw::*;
use tiliqua_lib::*;
use pac::constants::*;
use tiliqua_lib::calibration::*;

use tiliqua_hal::embedded_graphics::prelude::*;
use tiliqua_hal::embedded_graphics::mono_font::{ascii::FONT_9X15, MonoTextStyle};
use tiliqua_hal::embedded_graphics::text::{Alignment, Text};
use tiliqua_lib::color::HI8;

use options::*;
use opts::persistence::*;
use channel::{Channel, ChannelView};
use flash::DelaylineFlash;
use hal::pca9635::Pca9635Driver;
use tiliqua_hal::delay_line::DelayLine;
use tiliqua_hal::persist::Persist;
use tiliqua_hal::pmod::EurorackPmod;

pub const TIMER0_ISR_PERIOD_MS: u32 = 5;

// samples to draw in waveform peak/line displays
pub const WAVEFORM_SAMPLES: usize = 240;

// color of each grainreader (head and peaks on each page)
pub const CHANNEL_HUES: [u8; 3] = [0, 5, 10];

// little helper for drawing waveform peaks in the correct spot
struct WaveformLayout {
    x: u32,
    y: u32,
    sample_width: u32,
    height: u32,
}

impl WaveformLayout {
    fn new(h_active: u32, v_active: u32) -> Self {
        let height = 300u32;
        // fixed for now so it resizes nicely
        let sample_width = 720u32 / WAVEFORM_SAMPLES as u32;
        let span = (WAVEFORM_SAMPLES as u32 - 1) * sample_width;
        Self {
            x: h_active / 2 - span / 2,
            y: v_active / 2 - height / 2,
            sample_width,
            height,
        }
    }

    // span: distance (px) from first to last sample = (n-1) * sample_width
    // (draw_width used in some places is n * sample_width)
    fn span(&self) -> u32 {
        (WAVEFORM_SAMPLES as u32 - 1) * self.sample_width
    }

    fn draw_waveform(&self, display: &mut DMAFramebuffer0, view: WaveformView, hue: u8, waveform: &[i16]) {
        let draw_width = WAVEFORM_SAMPLES as u32 * self.sample_width;
        match view {
            WaveformView::Peaks => draw::draw_waveform_peaks(display, self.x, self.y, draw_width, self.height, hue, waveform).ok(),
            WaveformView::Lines => draw::draw_waveform_lines(display, self.x, self.y, draw_width, self.height, hue, waveform).ok(),
        };
    }
}

// little helper to draw a progress string during spiflash audio save/load as it takes ages
fn draw_flash_progress(
    display: &mut DMAFramebuffer0,
    hue: u8,
    label: &str,
    done_kb: usize,
    total_kb: usize,
) {
    let Size { width: h, height: v } = display.size();
    let mut buf = heapless::String::<64>::new();
    write!(buf, "{} ({} / {} KiB)", label, done_kb, total_kb).ok();
    let font = MonoTextStyle::new(&FONT_9X15, HI8::new(hue, 0xf));
    Text::with_alignment(
        &buf,
        Point::new((h / 2) as i32, (v / 2) as i32),
        font,
        Alignment::Center,
    ).draw(display).ok();
}

pub type Channels = (
    Channel<GrainPlayer0>,
    Channel<GrainPlayer1>,
    Channel<GrainPlayer2>,
);

struct App {
    ui: ui::UI<Encoder0, EurorackPmod0, I2c0, Opts>,
    channels: Channels,
    delayln: DelayLine0,
}

impl App {
    pub fn new(
        opts: Opts,
        channels: Channels,
        delayln: DelayLine0,
    ) -> Self {
        let peripherals = unsafe { pac::Peripherals::steal() };
        let encoder = Encoder0::new(peripherals.ENCODER0);
        let i2cdev = I2c0::new(peripherals.I2C0);
        let pca9635 = Pca9635Driver::new(i2cdev);
        let pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
        Self {
            ui: ui::UI::new(opts, TIMER0_ISR_PERIOD_MS,
                            encoder, pca9635, pmod),
            channels,
            delayln,
        }
    }
}

fn timer0_handler(app: &Mutex<RefCell<App>>) {
    critical_section::with(|cs| {

        let peripherals = unsafe { pac::Peripherals::steal() };
        let pmod = peripherals.PMOD0_PERIPH;
        let sampler = peripherals.SAMPLER_PERIPH;

        let mut app = app.borrow_ref_mut(cs);

        let max_samples = app.delayln.size_samples() as u32;

        if let Some(ix) = app.ui.opts.tracker.page.value.channel_index() {
            // HACK: dynamic option steps based on zoom level (move up into options lib at some
            // point?).
            // Snapshot options before encoder update, so we can scale step by zoom factor.
            let opts_prev = app.ui.opts.channel_opts(ix).clone();
            app.ui.update();
            // Recalculate steps by zoom factor
            let opts = app.ui.opts.channel_opts_mut(ix);
            let zoomstep = |prev: u32, cur: u32, zoom: u8| -> u32 {
                let scale = 1i32 << (4 - zoom as i32);
                let delta = cur as i32 - prev as i32;
                (prev as i32 + delta * scale).max(0).min(max_samples as i32) as u32
            };
            opts.start.value = zoomstep(opts_prev.start.value, opts.start.value.clone(), opts.zoom.value.clone());
            opts.len.value = zoomstep(opts_prev.len.value, opts.len.value.clone(), opts.zoom.value.clone());
        } else {
            app.ui.update();
        }

        sampler.flags().write(|w| {
            w.record().bit(app.ui.opts.record.record.value)
        });

        // ScrubFast/ScrubSlow selects different onepole cutoff on scrub pos
        sampler.scrub_filter().write(|w| unsafe {
            w.ch0().bits(app.ui.opts.channel0.mode.value.scrub_filter_shift());
            w.ch1().bits(app.ui.opts.channel1.mode.value.scrub_filter_shift());
            w.ch2().bits(app.ui.opts.channel2.mode.value.scrub_filter_shift())
        });

        app.ui.touch_led_mask(0b00001110);
        let touch = app.ui.pmod.touch();
        let jack = pmod.jack().read().bits();
        let cv = app.ui.pmod.sample_i();
        let opts = app.ui.opts.clone();
        app.channels.0.update(&opts.channel0, max_samples, 1, &touch, jack, cv[1]);
        app.channels.1.update(&opts.channel1, max_samples, 2, &touch, jack, cv[2]);
        app.channels.2.update(&opts.channel2, max_samples, 3, &touch, jack, cv[3]);
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

    info!("Hello from Tiliqua SAMPLER!");

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
    CalibrationConstants::load_or_default(&mut i2cdev1, &mut pmod);

    let delayln = DelayLine0::new(peripherals.DELAYLN_PERIPH0);
    let channels = (
        Channel::new(GrainPlayer0::new(peripherals.GRAIN_PERIPH0)),
        Channel::new(GrainPlayer1::new(peripherals.GRAIN_PERIPH1)),
        Channel::new(GrainPlayer2::new(peripherals.GRAIN_PERIPH2)),
    );

    let mut opts = Opts::default();
    let mut flash_persist_opt = FlashOptionsPersistence::open(
        spiflash, bootinfo.manifest.get_option_storage_window(), &mut opts);

    // HACK: stolen spiflash instance!! be careful that delayln rd/wr is
    // never happening at the same time as other spiflash ops.
    let mut delayln_flash = DelaylineFlash::new(SPIFlash0::new(
        unsafe { pac::Peripherals::steal() }.SPIFLASH_CTRL,
        SPIFLASH_BASE,
        SPIFLASH_SZ_BYTES,
    ));

    palette::ColorPalette::default().write_to_hardware(&mut display);

    if flash_persist_opt.is_some() {
        delayln_flash.load(&delayln, |done_kb, total_kb| {
            draw_flash_progress(&mut display, 10, "Loading", done_kb, total_kb);
        });
    }

    let app = Mutex::new(RefCell::new(App::new(opts, channels, delayln)));

    handler!(timer0 = || timer0_handler(&app));

    irq::scope(|s| {

        s.register(handlers::Interrupt::TIMER0, timer0);

        timer.enable_tick_isr(TIMER0_ISR_PERIOD_MS,
                              pac::Interrupt::TIMER0);

        let hue = 10;
        let mut last_palette = palette::ColorPalette::default();

        loop {

            let h_active = display.size().width;
            let v_active = display.size().height;

            let (opts, _, channel_view, record_view, save_all, wipe_all) = critical_section::with(|cs| {
                let mut app = app.borrow_ref_mut(cs);
                let save_all = app.ui.opts.record.save_all.poll();
                let wipe_all = app.ui.opts.record.wipe_all.poll();
                let channel_view = match app.ui.opts.tracker.page.value {
                    Page::Channel0 => Some((0usize, app.channels.0.view(&app.delayln), app.ui.opts.channel0.clone())),
                    Page::Channel1 => Some((1usize, app.channels.1.view(&app.delayln), app.ui.opts.channel1.clone())),
                    Page::Channel2 => Some((2usize, app.channels.2.view(&app.delayln), app.ui.opts.channel2.clone())),
                    _ => None,
                };
                let record_view = if app.ui.opts.tracker.page.value == Page::Delayline {
                    Some((
                        ChannelView::from_delayln(&app.delayln),
                        [
                            (app.channels.0.view(&app.delayln), app.ui.opts.channel0.clone()),
                            (app.channels.1.view(&app.delayln), app.ui.opts.channel1.clone()),
                            (app.channels.2.view(&app.delayln), app.ui.opts.channel2.clone()),
                        ],
                    ))
                } else {
                    None
                };
                (app.ui.opts.clone(), app.ui.draw(), channel_view, record_view, save_all, wipe_all)
            });

            let on_help_page = opts.tracker.page.value == Page::Help;

            let (x, y) = if on_help_page {
                (h_active/2-30, v_active-100)
            } else {
                (h_active/2, 80)
            };
            draw::draw_options(&mut display, &opts, x, y, hue).ok();
            draw::draw_name(&mut display, h_active/2, v_active-50, hue,
                            &bootinfo.manifest.name, &bootinfo.manifest.tag, &modeline).ok();

            if opts.record.palette.value != last_palette {
                opts.record.palette.value.write_to_hardware(&mut display);
                last_palette = opts.record.palette.value;
            }

            if on_help_page {
                draw::draw_help_page(&mut display,
                    MODULE_DOCSTRING,
                    bootinfo.manifest.help.as_ref(),
                    h_active,
                    v_active,
                    opts.help.scroll.value,
                    hue).ok();
                persist.set_persistence(64);
            } else {
                persist.set_persistence(32);
            }


            if save_all {
                // persist all options and then the audio delay line (slow)
                if let Some(ref mut flash_persist) = flash_persist_opt {
                    flash_persist.save_options(&opts).unwrap();
                }
                let (ptr, size_bytes, wr_bytes) = critical_section::with(|cs| {
                    let app = app.borrow_ref(cs);
                    (app.delayln.data_ptr() as *const u8,
                     app.delayln.size_samples() * 2,
                     app.delayln.wrpointer() * 2)
                });
                let data = unsafe { core::slice::from_raw_parts(ptr, size_bytes) };
                delayln_flash.save(data, wr_bytes, |label, done_kb, total_kb| {
                    draw_flash_progress(&mut display, hue, label, done_kb, total_kb);
                });
            }

            if wipe_all {
                // clear all options and the audio delay line (fast, only wipe magic word)
                critical_section::with(|cs| {
                    let mut app = app.borrow_ref_mut(cs);
                    app.ui.opts = Opts::default();
                    let size_bytes = app.delayln.size_samples() * 2;
                    let data = unsafe {
                        core::slice::from_raw_parts_mut(app.delayln.data_ptr() as *mut u8, size_bytes)
                    };
                    data.fill(0);
                    if let Some(ref mut flash_persist) = flash_persist_opt {
                        flash_persist.erase_all().unwrap();
                    }
                });
                delayln_flash.wipe_magic();
            }

            // 'DELAYLINE' page: show entire delayline, vertical line for each grain
            if let Some((view, channel_views)) = record_view {
                let wf = WaveformLayout::new(h_active, v_active);
                let mut waveform: [i16; WAVEFORM_SAMPLES] = [0; WAVEFORM_SAMPLES];
                let stride = view.delayln_max_samples / WAVEFORM_SAMPLES;
                view.delayln_read_samples(&mut waveform, WAVEFORM_SAMPLES * stride, stride);
                wf.draw_waveform(&mut display, opts.record.view.value, hue, &waveform);

                for (i, (ch_view, _ch_opts)) in channel_views.iter().enumerate() {
                    let ch_hue = CHANNEL_HUES[i];
                    let playback_pos = ch_view.playback_position();
                    let pos_x = wf.x + wf.span() - (playback_pos as u32 * wf.span()) / ch_view.delayln_max_samples as u32;
                    let pos_height = wf.height / 2;
                    let pos_y = wf.y + wf.height / 4;
                    draw::draw_vline(&mut display, pos_x, pos_y, pos_height, ch_hue, 12).ok();
                }
            }

            // 'ChannelX' page: show (maybe) zoomed delayline with grain start/end points.
            if let Some((ch_idx, view, channel_opts)) = channel_view {
                let wf = WaveformLayout::new(h_active, v_active);
                let ch_hue = CHANNEL_HUES[ch_idx];

                // HACK: when hovering on 'length' menu item, center on it instead of 'start'.
                let center_on_end = opts.tracker.selected == Some(5);

                let mut waveform: [i16; WAVEFORM_SAMPLES] = [0; WAVEFORM_SAMPLES];
                view.read_samples(&channel_opts, &mut waveform, center_on_end);
                wf.draw_waveform(&mut display, opts.record.view.value, ch_hue, &waveform);

                let (start_x, end_x) = view.grain_markers_x(&channel_opts, WAVEFORM_SAMPLES, center_on_end, wf.x, wf.span());
                let marker_height = wf.height / 2;
                let marker_y = wf.y + wf.height / 4;
                draw::draw_vline(&mut display, start_x, marker_y, marker_height, ch_hue, 15).ok();
                draw::draw_vline(&mut display, end_x, marker_y, marker_height, ch_hue, 15).ok();

                let playback_pos = view.playback_position();
                let pos_x = view.delay_to_x(&channel_opts, playback_pos, WAVEFORM_SAMPLES, center_on_end, wf.x, wf.span());
                let pos_height = wf.height / 4;
                let pos_y = wf.y + wf.height * 3 / 8;
                draw::draw_vline(&mut display, pos_x, pos_y, pos_height, ch_hue, 15).ok();

                let label = ChannelView::view_label(&channel_opts, center_on_end);
                let font = MonoTextStyle::new(&FONT_9X15, HI8::new(ch_hue, 12));
                let label_y = wf.y + wf.height - 30;
                Text::with_alignment(
                    label,
                    Point::new((h_active / 2) as i32, label_y as i32),
                    font,
                    Alignment::Center
                ).draw(&mut display).ok();
            }

        }
    })
}
