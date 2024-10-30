#![no_std]
#![no_main]

use tiliqua_pac as pac;
use tiliqua_hal as hal;

use hal::hal::delay::DelayNs;

use tiliqua_fw::*;
use tiliqua_fw::opts::ControlInterface;

use log::info;

use riscv_rt::entry;

use tiliqua_hal::pca9635::*;

use core::convert::TryInto;

use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
};


use tiliqua_lib::draw;
use tiliqua_lib::palette;
use tiliqua_lib::leds;

use tiliqua_lib::opt::*;

use tiliqua_lib::generated_constants::*;

use fixed::{FixedI32, types::extra::U16};

use micromath::F32Ext;

use midi_types::*;

use midi_convert::render_slice::MidiRenderSlice;

/// Fixed point DSP below should use 32-bit integers with a 16.16 split.
/// This could be made generic below, but isn't to reduce noise...
pub type Fix = FixedI32<U16>;

struct OnePoleSmoother {
    y_k1: Fix,
}

impl OnePoleSmoother {
    fn new() -> Self {
        OnePoleSmoother {
            y_k1: Fix::from_num(0),
        }
    }

    fn proc(&mut self, x_k: Fix) -> Fix {
        self.y_k1 = self.y_k1 * Fix::from_num(0.95f32) + x_k * Fix::from_num(0.05f32);
        self.y_k1
    }
}

const N_TOUCH: usize = 8;

struct MidiTouchController {
    notes:   [Note; N_TOUCH],
    l_touch: [u8; N_TOUCH],
}

impl MidiTouchController {
    fn new() -> Self {
        MidiTouchController {
            // Notes hard-coded for now, should be switchable at
            // runtime as long as we do a KILLALL before switching.
            notes:   [Note::C2,
                      Note::G2,
                      Note::C3,
                      Note::Ds3,
                      Note::G3,
                      Note::C4,
                      Note::C0, // last 2 notes are the output jacks
                      Note::C0],
            l_touch: [0u8; N_TOUCH],
        }
    }

    fn update(&mut self, touch: &[u8; N_TOUCH], jack: u8) -> [MidiMessage; N_TOUCH] {
        let mut out: [MidiMessage; N_TOUCH] = [MidiMessage::Stop; N_TOUCH];
        let channel = Channel::C1;
        for i in 0..N_TOUCH {
            let v = Value7::new(touch[i]>>1);
            // warn: note off logic currently assumes note ids don't change
            out[i] = MidiMessage::NoteOff(channel, self.notes[i], v);
            // if jack is not inserted
            if ((1 << i) & !jack) != 0 {
                // emit NOTE_ON once after the touch starts, and
                // POLY_PRESSURE for all cycles afterward.
                if self.l_touch[i] == 0 && touch[i] > 0 {
                    out[i] = MidiMessage::NoteOn(channel, self.notes[i], v);
                } else if touch[i] != 0 {
                    out[i] = MidiMessage::KeyPressure(channel, self.notes[i], v);
                }
            }
        }
        self.l_touch = *touch;
        out
    }
}

tiliqua_hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE, VIDEO_ROTATE_90);

pub fn write_palette(video: &mut Video0, p: palette::ColorPalette) {
    for i in 0..PX_INTENSITY_MAX {
        for h in 0..PX_HUE_MAX {
            let rgb = palette::compute_color(i, h, p);
            video.set_palette_rgb(i as u8, h as u8, rgb.r, rgb.g, rgb.b);
        }
    }
}

#[entry]
fn main() -> ! {
    let peripherals = pac::Peripherals::take().unwrap();

    // initialize logging
    let serial = Serial0::new(peripherals.UART0);
    tiliqua_fw::handlers::logger_init(serial);

    let sysclk = pac::clock::sysclk();
    let mut timer = Timer0::new(peripherals.TIMER0, sysclk);

    info!("Hello from Tiliqua POLY!");

    let i2cdev = I2c0::new(peripherals.I2C0);

    let mut pca9635 = Pca9635Driver::new(i2cdev);

    let mut encoder = Encoder0::new(peripherals.ENCODER0);

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
    let period_ms = 5u32;

    let mut opts = opts::Options::new();

    let vscope  = peripherals.VECTOR_PERIPH;

    let mut synth = Polysynth0::new(peripherals.SYNTH_PERIPH);

    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);

    let mut video = Video0::new(peripherals.VIDEO_PERIPH);

    let mut toggle_encoder_leds = false;

    let mut time_since_encoder_touched: u32 = 0;

    let mut drive_smoother = OnePoleSmoother::new();

    let mut reso_smoother = OnePoleSmoother::new();

    let mut diffusion_smoother = OnePoleSmoother::new();

    let mut touch_controller = MidiTouchController::new();

    // Write default palette setting
    write_palette(&mut video, opts.beam.palette.value);
    let mut last_palette = opts.beam.palette.value;

    loop {

        if opts.beam.palette.value != last_palette {
            write_palette(&mut video, opts.beam.palette.value);
            last_palette = opts.beam.palette.value;
        }

        if time_since_encoder_touched < 1000 || opts.modify() {

            draw::draw_options(&mut display, &opts, H_ACTIVE-200, V_ACTIVE/2, opts.beam.hue.value).ok();

        }

        pause_flush(&mut timer, &mut uptime_ms, period_ms);

        encoder.update();

        time_since_encoder_touched += period_ms;

        let ticks = encoder.poke_ticks();
        if ticks != 0 {
            opts.consume_ticks(ticks);
            time_since_encoder_touched = 0;
        }
        if encoder.poke_btn() {
            opts.toggle_modify();
            time_since_encoder_touched = 0;
        }

        video.set_persist(opts.beam.persist.value);
        video.set_decay(opts.beam.decay.value);

        vscope.en().write(|w| w.enable().bit(true) );
        vscope.hue().write(|w| unsafe { w.hue().bits(opts.beam.hue.value) } );
        vscope.intensity().write(|w| unsafe { w.intensity().bits(opts.beam.intensity.value) } );
        vscope.xscale().write(|w| unsafe { w.xscale().bits(opts.vector.xscale.value) } );
        vscope.yscale().write(|w| unsafe { w.yscale().bits(opts.vector.yscale.value) } );

        let drive_smooth = drive_smoother.proc(Fix::from_bits(opts.poly.drive.value as i32)).to_bits() as u16;
        synth.set_drive(drive_smooth);

        let reso_smooth = reso_smoother.proc(Fix::from_bits(opts.poly.reso.value as i32)).to_bits() as u16;
        synth.set_reso(reso_smooth);

        let diffuse_smooth = diffusion_smoother.proc(Fix::from_bits(opts.poly.diffuse.value as i32)).to_bits() as u16;
        let coeff_dry: i32 = (32768 - diffuse_smooth) as i32;
        let coeff_wet: i32 = diffuse_smooth as i32;

        synth.set_matrix_coefficient(0, 0, coeff_dry);
        synth.set_matrix_coefficient(1, 1, coeff_dry);
        synth.set_matrix_coefficient(2, 2, coeff_dry);
        synth.set_matrix_coefficient(3, 3, coeff_dry);

        synth.set_matrix_coefficient(0, 4, coeff_wet);
        synth.set_matrix_coefficient(1, 5, coeff_wet);
        synth.set_matrix_coefficient(2, 6, coeff_wet);
        synth.set_matrix_coefficient(3, 7, coeff_wet);

        // Touch controller logic (sends MIDI to internal polysynth)
        if opts.poly.interface.value == ControlInterface::TouchMidi {
            let touch = pmod.touch();
            let msgs = touch_controller.update(&touch, pmod.jack());
            for msg in msgs {
                let mut bytes = [0u8; 3];
                msg.render_slice(&mut bytes);
                let v: u32 = (bytes[0] as u32) << 16 |
                             (bytes[1] as u32) << 8 |
                             (bytes[2] as u32) << 0;
                synth.midi_write(v);
            }
        }

        let notes = synth.voice_notes();
        let cutoffs = synth.voice_cutoffs();

        let n_voices = 8usize;
        for ix in 0usize..n_voices {
            let j = 7-ix;
            draw::draw_voice(&mut display,
                             ((H_ACTIVE as f32)/2.0f32 + 330.0f32*f32::cos(2.3f32 + 2.0f32 * j as f32 / 8.0f32)) as i32,
                             ((V_ACTIVE as f32)/2.0f32 + 330.0f32*f32::sin(2.3f32 + 2.0f32 * j as f32 / 8.0f32)) as u32 - 15,
                             notes[ix], cutoffs[ix], opts.beam.hue.value).ok();
        }

        for n in 0..16 {
            pca9635.leds[n] = 0u8;
        }

        if uptime_ms % 50 == 0 {
            toggle_encoder_leds = !toggle_encoder_leds;
        }

        leds::mobo_pca9635_set_bargraph(&opts, &mut pca9635.leds,
                                        toggle_encoder_leds);

        if synth.midi_read() != 0 {
            leds::mobo_pca9635_set_midi(&mut pca9635.leds, 0xff, 0xff);
        } else {
            leds::mobo_pca9635_set_midi(&mut pca9635.leds, 0x0, 0x0);
        }

        if opts.modify() {
            if toggle_encoder_leds {
                if let Some(n) = opts.view().selected() {
                    pmod.led_set_manual(n, i8::MAX);
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
                    pmod.led_set_manual(n, (((1000-time_since_encoder_touched) * 120) / 1000) as i8);
                }
            } else {
                pmod.led_all_auto();

                // output touches on 4/5  aren't automatically routed to LEDs by eurorack-pmod gateware.
                if opts.poly.interface.value == ControlInterface::TouchMidi {
                    let touch = pmod.touch();
                    pmod.led_set_manual(4,(touch[4]>>2) as i8);
                    pmod.led_set_manual(5,(touch[5]>>2) as i8);
                }
            }
        }



        pca9635.push().ok();
    }
}
