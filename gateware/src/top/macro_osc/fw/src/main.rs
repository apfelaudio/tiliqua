#![no_std]
#![no_main]

use tiliqua_pac as pac;
use tiliqua_hal as hal;

use tiliqua_fw::*;

use log::info;

use riscv_rt::entry;

use core::cell::RefCell;
use critical_section::Mutex;

use tiliqua_hal::pca9635::*;

use core::convert::TryInto;

use embedded_graphics::{
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
};

use tiliqua_lib::*;
use tiliqua_lib::opt::*;
use tiliqua_lib::generated_constants::*;

use irq::{handler, scoped_interrupts};
use amaranth_soc_isr::return_as_is;

use mi_plaits_dsp::dsp::voice::{Modulations, Patch, Voice};

const SAMPLE_RATE: u32 = 48000;
const BLOCK_SIZE: usize = 128;

tiliqua_hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE, VIDEO_ROTATE_90);

use embedded_alloc::LlffHeap as Heap;

static HEAP: Heap = Heap::empty();

const HEAP_START: usize = PSRAM_BASE + (PSRAM_SZ_BYTES / 2);
const HEAP_SIZE: usize = 128*1024;
const TIMER0_ISR_PERIOD_MS: u32 = 10;

scoped_interrupts! {
    #[allow(non_camel_case_types)]
    enum Interrupt {
        TIMER0,
    }
    use #[return_as_is];
}

struct MacroOsc<'a> {
    voice: Voice<'a>,
    patch: Patch,
    modulations: Modulations,
}

impl<'a> MacroOsc<'a> {
    pub fn new() -> Self {
        Self {
            voice: Voice::new(&HEAP, BLOCK_SIZE/2),
            patch: Patch::default(),
            modulations: Modulations::default(),
        }
    }

    pub fn init(&mut self) {
        self.patch.engine = 0;
        self.patch.harmonics = 0.5;
        self.patch.timbre = 0.5;
        self.patch.morph = 0.5;
        self.voice.init();
    }
}

fn timer0_handler(opts: &Mutex<RefCell<opts::Options>>, osc: &mut MacroOsc, encoder: &mut Encoder0,
                  time_since_encoder_touched: &mut u32, uptime_ms: &mut u32,
                  toggle_encoder_leds: &mut bool) {

    let peripherals = unsafe { pac::Peripherals::steal() };
    let audio_fifo = peripherals.AUDIO_FIFO;
    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);

    let i2cdev = I2c0::new(peripherals.I2C0);
    let mut pca9635 = Pca9635Driver::new(i2cdev);

    critical_section::with(|cs| {

        let mut opts = opts.borrow_ref_mut(cs);

        //
        // Update options
        //

        encoder.update();

        *time_since_encoder_touched += TIMER0_ISR_PERIOD_MS;

        let ticks = encoder.poke_ticks();
        if ticks != 0 {
            opts.consume_ticks(ticks);
            *time_since_encoder_touched = 0;
        }
        if encoder.poke_btn() {
            opts.toggle_modify();
            *time_since_encoder_touched = 0;
        }

        //
        // Update LEDs
        //

        if *uptime_ms % (5*TIMER0_ISR_PERIOD_MS) == 0 {
            *toggle_encoder_leds = !*toggle_encoder_leds;
        }

        for n in 0..16 {
            pca9635.leds[n] = 0u8;
        }

        leds::mobo_pca9635_set_bargraph(&*opts, &mut pca9635.leds,
                                        *toggle_encoder_leds);

        if opts.modify() {
            if *toggle_encoder_leds {
                if let Some(n) = opts.view().selected() {
                    if n < 8 {
                        pmod.led_set_manual(n, i8::MAX);
                    }
                }
            } else {
                pmod.led_all_auto();
            }
        } else {
            if *time_since_encoder_touched < 1000 {
                for n in 0..8 {
                    pmod.led_set_manual(n, 0i8);
                }
                if let Some(n) = opts.view().selected() {
                    if n < 8 {
                        pmod.led_set_manual(n, (((1000-*time_since_encoder_touched) * 120) / 1000) as i8);
                    }
                }
            } else {
                pmod.led_all_auto();
            }
        }

        pca9635.push().ok();

        //
        // Render audio
        //

        let mut out = [0.0f32; BLOCK_SIZE/2];
        let mut aux = [0.0f32; BLOCK_SIZE/2];

        osc.patch.engine    = opts.osc.engine.value as usize;
        osc.patch.note      = opts.osc.note.value as f32;
        osc.patch.harmonics = (opts.osc.harmonics.value as f32) / 256.0f32;
        osc.patch.timbre    = (opts.osc.timbre.value as f32) / 256.0f32;
        osc.patch.morph     = (opts.osc.morph.value as f32) / 256.0f32;

        let mut n_attempts = 0;
        while (audio_fifo.fifo_len().read().bits() as usize) < 4096 - 256 {
            n_attempts += 1;
            if n_attempts > 30 {
                // TODO set underrun flag
                break
            }
            osc.voice
               .render(&osc.patch, &osc.modulations, &mut out, &mut aux);
            for i in 0..(BLOCK_SIZE/2) {
                let out16 = ((out[i]*16000.0f32) as i16) as u32;
                let aux16 = ((aux[i]*16000.0f32) as i16) as u32;
                for _ in 0..2 {
                    audio_fifo.sample().write(|w| unsafe { w.sample().bits(
                        out16 | (aux16 << 16)) } );
                }
            }
        }

    });
}

#[export_name = "DefaultHandler"]
fn default_isr_handler() {
    let peripherals = unsafe { pac::Peripherals::steal() };
    let sysclk = pac::clock::sysclk();
    let timer = Timer0::new(peripherals.TIMER0, sysclk);
    if timer.is_pending() {
        unsafe { TIMER0(); }
        timer.clear_pending();
    }
}

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
    pac::cpu::vexriscv::flush_icache();
    pac::cpu::vexriscv::flush_dcache();

    let peripherals = pac::Peripherals::take().unwrap();

    // initialize logging
    let serial = Serial0::new(peripherals.UART0);
    tiliqua_fw::handlers::logger_init(serial);

    let sysclk = pac::clock::sysclk();
    let mut timer = Timer0::new(peripherals.TIMER0, sysclk);

    info!("Hello from Tiliqua MACRO-OSCILLATOR!");




    let mut display = DMADisplay {
        framebuffer_base: PSRAM_FB_BASE as *mut u32,
    };

    let opts = Mutex::new(RefCell::new(opts::Options::new()));

    let vscope  = peripherals.VECTOR_PERIPH;
    let scope  = peripherals.SCOPE_PERIPH;

    let mut video = Video0::new(peripherals.VIDEO_PERIPH);


    unsafe { HEAP.init(HEAP_START, HEAP_SIZE) }

    let mut osc = MacroOsc::new();
    osc.init();

    info!("MacroOsc: heap usage {} KiB", HEAP.used()/1024);


    /*
    for engine in 0..24 {

        timer.enable();
        timer.set_timeout_ticks(0xFFFFFFFF);

        let start = timer.counter();

        osc.patch.engine = engine;

        for _ in 0..2 {
            osc.voice
               .render(&osc.patch, &osc.modulations, &mut out, &mut aux);
        }

        let read_ticks = start-timer.counter();

        let sysclk = pac::clock::sysclk();
        info!("engine {} speed {} samples/sec", engine, ((sysclk as u64) * (2*512) as u64) / (read_ticks as u64));
    }
    */

    let mut last_palette = tiliqua_lib::palette::ColorPalette::Exp;
    write_palette(&mut video, last_palette);

    let mut encoder = Encoder0::new(peripherals.ENCODER0);

    handler!(timer0 = || timer0_handler(&opts, &mut osc, &mut encoder, &mut 0u32, &mut 0u32, &mut false));

    irq::scope(|s| {

        s.register(Interrupt::TIMER0, timer0);

        /* configure timer ISR */
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


        loop {

            let opts = critical_section::with(|cs| {
                opts.borrow_ref(cs).clone()
            });

            if opts.beam.palette.value != last_palette {
                write_palette(&mut video, opts.beam.palette.value);
                last_palette = opts.beam.palette.value;
            }

            draw::draw_options(&mut display, &opts, H_ACTIVE-200, V_ACTIVE/2, opts.beam.hue.value).ok();

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

        }
    })
}
