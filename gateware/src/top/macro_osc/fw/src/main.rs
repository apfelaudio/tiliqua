#![no_std]
#![no_main]

use tiliqua_pac as pac;
use tiliqua_hal as hal;

use hal::hal::delay::DelayNs;

use tiliqua_fw::*;

use log::info;

use riscv_rt::entry;

use tiliqua_hal::pca9635::*;

use core::convert::TryInto;

use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
};

use tiliqua_lib::*;
use tiliqua_lib::opt::*;
use tiliqua_lib::generated_constants::*;

use mi_plaits_dsp::dsp::voice::{Modulations, Patch, Voice};

const SAMPLE_RATE: u32 = 48000;
const BLOCK_SIZE: usize = 128;

tiliqua_hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE, VIDEO_ROTATE_90);

use embedded_alloc::LlffHeap as Heap;

static HEAP: Heap = Heap::empty();

const HEAP_START: usize = (PSRAM_BASE + (PSRAM_SZ_BYTES / 2));
const HEAP_SIZE: usize = (PSRAM_SZ_BYTES / 2);

pub fn write_palette(video: &mut Video0, p: palette::ColorPalette) {
    for i in 0..PX_INTENSITY_MAX {
        for h in 0..PX_HUE_MAX {
            let rgb = palette::compute_color(i, h, p);
            video.set_palette_rgb(i as u8, h as u8, rgb.r, rgb.g, rgb.b);
        }
    }
}

struct MacroOsc<'a> {
    voice: Voice<'a>,
    patch: Patch,
    modulations: Modulations,
    volume: f32,
    balance: f32,
}

impl<'a> MacroOsc<'a> {
    pub fn new() -> Self {
        Self {
            voice: Voice::new(&HEAP, BLOCK_SIZE),
            patch: Patch::default(),
            modulations: Modulations::default(),
            volume: 1.0,
            balance: 0.0,
        }
    }

    pub fn init(&mut self) {
        self.patch.engine = 0;
        self.patch.harmonics = 0.5;
        self.patch.timbre = 0.5;
        self.patch.morph = 0.5;
    }
}


#[entry]
fn main() -> ! {
    pac::cpu::vexriscv::flush_icache();
    pac::cpu::vexriscv::flush_dcache();

    let peripherals = pac::Peripherals::take().unwrap();

    // initialize logging
    let serial = Serial0::new(peripherals.UART0);
    tiliqua_fw::handlers::logger_init(serial);

    let sysclk = pac::clock::sysclk();
    let mut timer = Timer0::new(peripherals.TIMER0, sysclk);

    info!("Hello from Tiliqua MACRO-OSCILLATOR!");

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
    };

    let mut uptime_ms = 0u32;
    let period_ms = 10u32;

    let mut opts = opts::Options::new();

    let vscope  = peripherals.VECTOR_PERIPH;
    let scope  = peripherals.SCOPE_PERIPH;

    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);

    let mut video = Video0::new(peripherals.VIDEO_PERIPH);

    let mut toggle_encoder_leds = false;

    let mut time_since_encoder_touched: u32 = 0;

    // Write default palette setting
    write_palette(&mut video, opts.beam.palette.value);
    let mut last_palette = opts.beam.palette.value;

    {
        use core::mem::MaybeUninit;
        unsafe { HEAP.init(HEAP_START, HEAP_SIZE) }
    }

    let mut osc = MacroOsc::new();
    osc.init();

    info!("MacroOsc: heap usage {} KiB", HEAP.used()/1024);

    /*
    let mut out = [0.0f32; BLOCK_SIZE];
    let mut aux = [0.0f32; BLOCK_SIZE];
    osc.voice
       .render(&osc.patch, &osc.modulations, &mut out, &mut aux);
    */

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

        vscope.hue().write(|w| unsafe { w.hue().bits(opts.beam.hue.value) } );
        vscope.intensity().write(|w| unsafe { w.intensity().bits(opts.beam.intensity.value) } );
        vscope.xscale().write(|w| unsafe { w.xscale().bits(opts.vector.xscale.value) } );
        vscope.yscale().write(|w| unsafe { w.yscale().bits(opts.vector.yscale.value) } );

        scope.hue().write(|w| unsafe { w.hue().bits(opts.beam.hue.value) } );
        scope.intensity().write(|w| unsafe { w.intensity().bits(opts.beam.intensity.value) } );

        scope.trigger_lvl().write(|w| unsafe { w.trigger_level().bits(opts.scope.trigger_lvl.value as u16) } );
        scope.xscale().write(|w| unsafe { w.xscale().bits(opts.scope.xscale.value) } );
        scope.yscale().write(|w| unsafe { w.yscale().bits(opts.scope.yscale.value) } );
        scope.timebase().write(|w| unsafe { w.timebase().bits(opts.scope.timebase.value) } );

        scope.ypos0().write(|w| unsafe { w.ypos().bits(opts.scope.ypos0.value as u16) } );
        scope.ypos1().write(|w| unsafe { w.ypos().bits(opts.scope.ypos1.value as u16) } );
        scope.ypos2().write(|w| unsafe { w.ypos().bits(opts.scope.ypos2.value as u16) } );
        scope.ypos3().write(|w| unsafe { w.ypos().bits(opts.scope.ypos3.value as u16) } );

        scope.trigger_always().write(
            |w| w.trigger_always().bit(opts.scope.trigger_mode.value == opts::TriggerMode::Always) );

        if opts.screen.value == opts::Screen::Vector {
            scope.en().write(|w| w.enable().bit(false) );
            vscope.en().write(|w| w.enable().bit(true) );
        }

        if opts.screen.value == opts::Screen::Scope {
            scope.en().write(|w| w.enable().bit(true) );
            vscope.en().write(|w| w.enable().bit(false) );
        }

        for n in 0..16 {
            pca9635.leds[n] = 0u8;
        }

        if uptime_ms % 50 == 0 {
            toggle_encoder_leds = !toggle_encoder_leds;
        }

        leds::mobo_pca9635_set_bargraph(&opts, &mut pca9635.leds,
                                        toggle_encoder_leds);

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
    }
}
