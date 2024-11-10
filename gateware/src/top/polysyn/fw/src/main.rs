#![no_std]
#![no_main]

use critical_section::Mutex;
use core::convert::TryInto;
use log::info;
use riscv_rt::entry;
use irq::handler;
use core::cell::RefCell;

use micromath::F32Ext;
use midi_types::*;
use midi_convert::render_slice::MidiRenderSlice;

use tiliqua_pac as pac;
use tiliqua_hal as hal;
use tiliqua_lib::*;
use tiliqua_lib::draw;
use tiliqua_lib::palette;
use tiliqua_lib::leds;
use tiliqua_lib::dsp::OnePoleSmoother;
use tiliqua_lib::midi::MidiTouchController;
use tiliqua_lib::generated_constants::*;
use tiliqua_fw::*;
use tiliqua_fw::opts::TouchControl;

use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
};

use opts::Options;
use hal::pca9635::Pca9635Driver;

impl_optif!(OptInterface,
            Options,
            Encoder0,
            Pca9635Driver<I2c0>,
            EurorackPmod0);

tiliqua_hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE, VIDEO_ROTATE_90);

pub const TIMER0_ISR_PERIOD_MS: u32 = 5;

fn timer0_handler(app: &Mutex<RefCell<App>>) {

    critical_section::with(|cs| {

        let mut app = app.borrow_ref_mut(cs);

        //
        // Update UI and options
        //

        app.optif.update();

        if app.synth.midi_read() != 0 {
            app.optif.midi_activity()
        }

        //
        // Update synthesizer
        //

        let opts = app.optif.opts.clone();

        let drive_smooth = app.drive_smoother.proc_u16(opts.poly.drive.value);
        app.synth.set_drive(drive_smooth);

        let reso_smooth = app.reso_smoother.proc_u16(opts.poly.reso.value);
        app.synth.set_reso(reso_smooth);

        let diffuse_smooth = app.diffusion_smoother.proc_u16(opts.poly.diffuse.value);
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


        // Touch controller logic (sends MIDI to internal polysynth)
        if opts.poly.interface.value == TouchControl::On {
            app.optif.touch_led_mask(0b00111111);
            let touch = app.optif.pmod.touch();
            let jack = app.optif.pmod.jack();
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

pub fn write_palette(video: &mut Video0, p: palette::ColorPalette) {
    for i in 0..PX_INTENSITY_MAX {
        for h in 0..PX_HUE_MAX {
            let rgb = palette::compute_color(i, h, p);
            video.set_palette_rgb(i as u8, h as u8, rgb.r, rgb.g, rgb.b);
        }
    }
}

struct App {
    optif: OptInterface,
    synth: Polysynth0,
    drive_smoother: OnePoleSmoother,
    reso_smoother: OnePoleSmoother,
    diffusion_smoother: OnePoleSmoother,
    touch_controller: MidiTouchController,
}

impl App {
    pub fn new(opts: Options) -> Self {
        let peripherals = unsafe { pac::Peripherals::steal() };
        let encoder = Encoder0::new(peripherals.ENCODER0);
        let pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
        let i2cdev = I2c0::new(peripherals.I2C0);
        let pca9635 = Pca9635Driver::new(i2cdev);
        let synth = Polysynth0::new(peripherals.SYNTH_PERIPH);
        let drive_smoother = OnePoleSmoother::new(0.05f32);
        let reso_smoother = OnePoleSmoother::new(0.05f32);
        let diffusion_smoother = OnePoleSmoother::new(0.05f32);
        let touch_controller = MidiTouchController::new();
        Self {
            optif: OptInterface::new(opts, TIMER0_ISR_PERIOD_MS,
                                     encoder, pca9635, pmod),
            synth,
            drive_smoother,
            reso_smoother,
            diffusion_smoother,
            touch_controller,
        }
    }
}

#[entry]
fn main() -> ! {
    let peripherals = pac::Peripherals::take().unwrap();
    let sysclk = pac::clock::sysclk();
    let serial = Serial0::new(peripherals.UART0);
    let mut timer = Timer0::new(peripherals.TIMER0, sysclk);
    let mut video = Video0::new(peripherals.VIDEO_PERIPH);
    let mut display = DMADisplay {
        framebuffer_base: PSRAM_FB_BASE as *mut u32,
    };
    crate::handlers::logger_init(serial);

    info!("Hello from Tiliqua POLYSYN!");

    let opts = opts::Options::new();
    let mut last_palette = opts.beam.palette.value.clone();
    let app = Mutex::new(RefCell::new(App::new(opts)));

    handler!(timer0 = || timer0_handler(&app));

    irq::scope(|s| {

        s.register(handlers::Interrupt::TIMER0, timer0);

        //
        // Set up timer ISR
        //

        use core::time::Duration;
        use crate::hal::timer;
        timer.listen(timer::Event::TimeOut);
        timer.set_timeout(Duration::from_millis(TIMER0_ISR_PERIOD_MS.into()));
        timer.enable();
        unsafe {
                pac::csr::interrupt::enable(pac::Interrupt::TIMER0);
                riscv::register::mie::set_mext();
                // WARN: Don't do this before IRQs are registered for this scope,
                // otherwise you'll hang forever :)
                riscv::interrupt::enable();
        }

        let vscope  = peripherals.VECTOR_PERIPH;
        let mut first = true;

        loop {

            let (opts, notes, cutoffs) = critical_section::with(|cs| {
                let app = app.borrow_ref(cs);
                (app.optif.opts.clone(),
                 app.synth.voice_notes().clone(),
                 app.synth.voice_cutoffs().clone())
            });

            if opts.beam.palette.value != last_palette || first {
                write_palette(&mut video, opts.beam.palette.value);
                last_palette = opts.beam.palette.value;
            }

            if opts.draw {
                draw::draw_options(&mut display, &opts, H_ACTIVE-200, V_ACTIVE/2,
                                   opts.beam.hue.value).ok();
            }

            video.set_persist(opts.beam.persist.value);
            video.set_decay(opts.beam.decay.value);

            vscope.en().write(|w| w.enable().bit(true) );
            vscope.hue().write(|w| unsafe { w.hue().bits(opts.beam.hue.value) } );
            vscope.intensity().write(|w| unsafe { w.intensity().bits(opts.beam.intensity.value) } );
            vscope.xscale().write(|w| unsafe { w.xscale().bits(opts.vector.xscale.value) } );
            vscope.yscale().write(|w| unsafe { w.yscale().bits(opts.vector.yscale.value) } );

            let n_voices = 8usize;
            for ix in 0usize..n_voices {
                let j = 7-ix;
                draw::draw_voice(&mut display,
                                 ((H_ACTIVE as f32)/2.0f32 + 330.0f32*f32::cos(2.3f32 + 2.0f32 * j as f32 / 8.0f32)) as i32,
                                 ((V_ACTIVE as f32)/2.0f32 + 330.0f32*f32::sin(2.3f32 + 2.0f32 * j as f32 / 8.0f32)) as u32 - 15,
                                 notes[ix], cutoffs[ix], opts.beam.hue.value).ok();
            }

            first = false;
        }
    })
}
