#![no_std]
#![no_main]

use critical_section::Mutex;
use core::convert::TryInto;
use log::info;
use riscv_rt::entry;
use irq::handler;
use core::cell::RefCell;

use tiliqua_pac as pac;
use tiliqua_hal as hal;
use tiliqua_lib::*;
use tiliqua_lib::opt::*;
use tiliqua_lib::generated_constants::*;
use tiliqua_fw::*;

use embedded_graphics::{
    mono_font::{ascii::FONT_9X15_BOLD, MonoTextStyle},
    pixelcolor::{Gray8, GrayColor},
    prelude::*,
    text::{Alignment, Text},
};

use opts::Options;
use hal::pca9635::Pca9635Driver;

impl_ui!(UI,
         Options,
         Encoder0,
         Pca9635Driver<I2c0>,
         EurorackPmod0);

hal::impl_dma_display!(DMADisplay, H_ACTIVE, V_ACTIVE,
                       VIDEO_ROTATE_90);

pub const TIMER0_ISR_PERIOD_MS: u32 = 5;

struct App {
    ui: UI,
}

impl App {
    pub fn new(opts: Options) -> Self {
        let peripherals = unsafe { pac::Peripherals::steal() };
        let encoder = Encoder0::new(peripherals.ENCODER0);
        let i2cdev = I2c0::new(peripherals.I2C0);
        let pca9635 = Pca9635Driver::new(i2cdev);
        let pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
        Self {
            ui: UI::new(opts, TIMER0_ISR_PERIOD_MS,
                        encoder, pca9635, pmod),
        }
    }
}

fn print_rebooting<D>(d: &mut D, rng: &mut fastrand::Rng)
where
    D: DrawTarget<Color = Gray8>,
{
    let style = MonoTextStyle::new(&FONT_9X15_BOLD, Gray8::WHITE);
    Text::with_alignment(
        "REBOOTING",
        Point::new(rng.i32(0..H_ACTIVE as i32), rng.i32(0..V_ACTIVE as i32)),
        style,
        Alignment::Center,
    )
    .draw(d).ok();
}

fn timer0_handler(app: &Mutex<RefCell<App>>) {

    critical_section::with(|cs| {

        let mut app = app.borrow_ref_mut(cs);

        //
        // Update UI and options
        //

        app.ui.update();

    });
}

#[entry]
fn main() -> ! {
    let peripherals = pac::Peripherals::take().unwrap();

    let sysclk = pac::clock::sysclk();
    let serial = Serial0::new(peripherals.UART0);
    let mut timer = Timer0::new(peripherals.TIMER0, sysclk);
    let mut video = Video0::new(peripherals.VIDEO_PERIPH);

    crate::handlers::logger_init(serial);

    info!("Hello from Tiliqua bootloader!");

    let manifest = manifest::BitstreamManifest::find().unwrap_or(
        manifest::BitstreamManifest::unknown_manifest());

    info!("BitstreamManifest created with:");
    for name in &manifest.names {
        info!("- '{}'", name);
    }

    let opts = opts::Options::new(&manifest);
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

        let mut logo_coord_ix = 0u32;
        let mut rng = fastrand::Rng::with_seed(0);
        let mut display = DMADisplay {
            framebuffer_base: PSRAM_FB_BASE as *mut u32,
        };
        video.set_persist(2048);

        loop {

            let opts = critical_section::with(|cs| {
                app.borrow_ref(cs).ui.opts.clone()
            });

            draw::draw_options(&mut display, &opts, H_ACTIVE/2-50, V_ACTIVE/2-50, 0).ok();

            for _ in 0..5 {
                let _ = draw::draw_boot_logo(&mut display,
                                             (H_ACTIVE/2) as i32,
                                             (V_ACTIVE/2+200) as i32,
                                             logo_coord_ix);
                logo_coord_ix += 1;
            }

            if opts.modify() {
                if let Some(n) = opts.view().selected() {
                    print_rebooting(&mut display, &mut rng);
                    // TODO: delay before reboot.
                    info!("BITSTREAM{}\n\r", n);
                }
            }
        }
    })
}
