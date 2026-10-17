#![no_std]
#![no_main]

use riscv_rt::entry;
use irq::handler;
use log::{info, error};

use critical_section::Mutex;
use core::cell::RefCell;
use core::fmt::Write;

use embedded_hal::i2c::{I2c, Operation};
use embedded_hal::delay::DelayNs;
use tiliqua_hal::embedded_graphics::{
    mono_font::{ascii::FONT_9X15, MonoTextStyle},
    text::{Alignment, Text},
    prelude::*,
};

use heapless::String;
use fastrand::Rng;

use tiliqua_pac as pac;
use tiliqua_fw::*;
use tiliqua_lib::*;
use pac::constants::*;
use tiliqua_lib::draw;
use tiliqua_lib::calibration::*;
use tiliqua_lib::color::HI8;
use tiliqua_fw::options::*;
use tiliqua_hal::pmod::EurorackPmod;
use tiliqua_hal::persist::Persist;
use tiliqua_hal::pca9635::Pca9635Driver;
use tiliqua_hal::dma_framebuffer::DMAFramebuffer;
use tiliqua_hal::eeprom::EepromDriver;
use tiliqua_hal::tusb322::TUSB322Driver;

pub type ReportString = String<512>;

pub const TIMER0_ISR_PERIOD_MS: u32 = 10;


fn timer0_handler(app: &Mutex<RefCell<App>>) {

    critical_section::with(|cs| {

        let mut app = app.borrow_ref_mut(cs);

        //
        // Update UI and options
        //

        app.ui.update();

        let opts_ro = app.ui.opts.clone();

        if opts_ro.autocal.autozero.value == StopRun::Run {
            let counts_per_v = app.ui.pmod.counts_per_v();
            let stimulus_raw = counts_per_v * opts_ro.autocal.volts.value as i32;
            let sample_i = app.ui.pmod.sample_i();
            let mut deltas = [0i16; 4];
            for ch in 0..4 {
                let delta = (sample_i[ch] - stimulus_raw)/4;
                if delta.abs() < counts_per_v / 4 {
                    if delta > 0 {
                        deltas[ch] = -1;
                    } else if delta < 0 {
                        deltas[ch] = 1;
                    }
                }
            }
            match opts_ro.autocal.set.value {
                AutoZero::AdcZero => {
                    app.ui.opts.caladc.zero0.value  += deltas[0];
                    app.ui.opts.caladc.zero1.value  += deltas[1];
                    app.ui.opts.caladc.zero2.value  += deltas[2];
                    app.ui.opts.caladc.zero3.value  += deltas[3];
                }
                AutoZero::AdcScale => {
                    app.ui.opts.caladc.scale0.value += deltas[0];
                    app.ui.opts.caladc.scale1.value += deltas[1];
                    app.ui.opts.caladc.scale2.value += deltas[2];
                    app.ui.opts.caladc.scale3.value += deltas[3];
                }
                AutoZero::DacZero => {
                    app.ui.opts.caldac.zero0.value  += deltas[0];
                    app.ui.opts.caldac.zero1.value  += deltas[1];
                    app.ui.opts.caldac.zero2.value  += deltas[2];
                    app.ui.opts.caldac.zero3.value  += deltas[3];
                }
                AutoZero::DacScale => {
                    app.ui.opts.caldac.scale0.value += deltas[0];
                    app.ui.opts.caldac.scale1.value += deltas[1];
                    app.ui.opts.caldac.scale2.value += deltas[2];
                    app.ui.opts.caldac.scale3.value += deltas[3];
                }
            }
        }

    });
}

fn psram_memtest(s: &mut ReportString, timer: &mut Timer0) {

    // WARN: be careful about memtesting near:
    // - framebuffer at the start of PSRAM.
    // - firmware a couple of megabytes into PSRAM
    // - bootinfo at end of PSRAM
    // PSRAM_SZ/2 is not close to any of these

    let psram_ptr = PSRAM_BASE as *mut u32;
    let psram_sz_test = 1024*64;
    let memtest_start = (PSRAM_SZ_WORDS/2) - psram_sz_test;
    let memtest_end = PSRAM_SZ_WORDS/2;

    timer.set_timeout_ticks(0xFFFFFFFF);
    timer.enable();

    let start = timer.counter();

    unsafe {
        for i in memtest_start..memtest_end {
            psram_ptr.offset(i as isize).write_volatile(i as u32);
        }
    }

    let endwrite = timer.counter();

    let mut psram_fl = false;
    unsafe {
        for i in memtest_start..memtest_end {
            let value = psram_ptr.offset(i as isize).read_volatile();
            if (i as u32) != value {
                psram_fl = true;
                error!("FAIL: PSRAM selftest @ {:#x} is {:#x}", i, value);
            }
        }
    }

    let endread = timer.counter();

    let write_ticks = start-endwrite;
    let read_ticks = endwrite-endread;

    let sysclk = pac::clock::sysclk();
    if psram_fl {
        write!(s, "FAIL: PSRAM memtest\r\n").ok();

    } else {
        write!(s, "PASS: PSRAM memtest\r\n").ok();
    }

    write!(s, "  write {} KByte/sec\r\n", ((sysclk as u64) * (psram_sz_test/1024) as u64) / write_ticks as u64).ok();
    write!(s, "  read {} KByte/sec\r\n", ((sysclk as u64) * (psram_sz_test/1024) as u64) / (read_ticks as u64)).ok();
}

fn spiflash_memtest(s: &mut ReportString, timer: &mut Timer0) {

    let spiflash_ptr = SPIFLASH_BASE as *mut u32;
    let spiflash_sz_test = 1024;

    timer.enable();
    timer.set_timeout_ticks(0xFFFFFFFF);

    let start = timer.counter();

    let mut first_words: [u32; 8] = [0u32; 8];

    unsafe {
        for i in 0..spiflash_sz_test {
            let value = spiflash_ptr.offset(i as isize).read_volatile();
            if i < first_words.len() {
                first_words[i] = value
            }
        }
    }

    let read_ticks = start-timer.counter();

    let sysclk = pac::clock::sysclk();

    // TODO: verify there is actually a bitstream header in first N words?
    let mut spiflash_fl = true;
    for i in 0..first_words.len() {
        info!("spiflash_memtest: read @ {:#x} at {:#x}", first_words[i], i);
        if first_words[i] != 0xff && first_words[i] != 0x00 {
            spiflash_fl = false;
        }
    }

    if spiflash_fl {
        write!(s, "FAIL: SPIFLASH memtest\r\n").ok();
    } else {
        write!(s, "PASS: SPIFLASH memtest\r\n").ok();
    }
    write!(s, "  read {} KByte/sec\r\n", ((sysclk as u64) * (spiflash_sz_test/1024) as u64) / (read_ticks as u64)).ok();
}

fn tusb322_id_test(s: &mut ReportString, i2cdev: &mut I2c0) {
    // Read TUSB322 device ID
    let mut tusb322 = TUSB322Driver::new(i2cdev);
    match tusb322.read_device_id() {
        Ok(tusb322_id) => {
            if tusb322_id != [0x32, 0x32, 0x33, 0x42, 0x53, 0x55, 0x54, 0x0] {
                write!(s, "FAIL: tusb322_id ").ok();
            } else {
                write!(s, "PASS: tusb322_id ").ok();
            }
            for byte in tusb322_id {
                write!(s, "{:x} ", byte).ok();
            }
        },
        Err(_) => {
            write!(s, "FAIL: tusb322_id (nak?) ").ok();
        }
    }
    write!(s, "\r\n").ok();
}

fn eeprom_id_test(s: &mut ReportString, i2cdev: &mut I2c1) -> bool {
    let mut ok = false;
    let mut eeprom = EepromDriver::new(i2cdev);
    match eeprom.read_id() {
        Ok(eeprom_id) => {
            if eeprom_id[0] == 0x29 {
                ok = true;
                write!(s, "PASS: eeprom_id ").ok();
            } else {
                write!(s, "FAIL: eeprom_id ").ok();
            }
            for byte in eeprom_id {
                write!(s, "{:x} ", byte).ok();
            }
        },
        Err(_) => {
            write!(s, "FAIL: eeprom_id (nak?) ").ok();
        }
    }
    write!(s, "\r\n").ok();
    ok
}

fn edid_test(s: &mut ReportString, i2cdev: &mut I2c0) {
    const EDID_ADDR: u8 = 0x50;
    write!(s, "EDID: ").ok();
    let mut edid: [u8; 128] = [0; 128];
    for i in 0..16 {
        i2cdev.transaction(EDID_ADDR, &mut [Operation::Write(&[(i*8) as u8]),
                                            Operation::Read(&mut edid[i*8..i*8+8])]).ok();
    }
    let edid_parsed = edid::Edid::parse(&edid);
    match edid_parsed {
        Ok(edid::Edid { header, descriptors, .. }) => {
            write!(s, "mfg_id={:?} product={:?} serial={:?}\r\n",
                   header.manufacturer_id,
                   header.product_code,
                   header.serial_number,
                   ).ok();
            info!("EDID header: {:?}", header);
            for descriptor in descriptors.iter() {
                info!("EDID descriptor: {:?}", descriptor);
                if let edid::Descriptor::DetailedTiming(desc) = descriptor {
                    write!(s, "      detailed [sz_x={} sz_y={} clk={}kHz]\r\n",
                           desc.horizontal_active,
                           desc.vertical_active,
                           desc.pixel_clock_khz,
                           ).ok();
                }
            }
        }
        _ => {
            write!(s, "{:?}\r\n", edid_parsed).ok();
        }
    }
}

fn print_touch_err(s: &mut ReportString, pmod: &EurorackPmod0)
{
    if pmod.touch_err() != 0 {
        write!(s, "FAIL: cy8cmbr_nak\r\n").ok();
    } else {
        write!(s, "PASS: cy8cmbr_nak\r\n").ok();
    }
}

fn print_usb_state(s: &mut ReportString, i2cdev: &mut I2c0)
{
    // Read TUSB322 connection status register
    // We don't fully use this yet. But it's useful for checking for usb circuitry assembly problems.
    // (in particular the cable orientation detection registers)
    let mut tusb322 = TUSB322Driver::new(i2cdev);
    match tusb322.read_connection_status_control() {
        Ok(status) => {
            write!(s, "tusb322 [AS={:?} CD={:?}]\r\n",
                   status.attached_state,
                   status.cable_dir).ok();
        },
        Err(_) => {
            write!(s, "tusb322 NAK\r\n").ok();
        }
    }
}

fn print_pmod_state(s: &mut ReportString, pmod: &impl EurorackPmod)
{
    let si = pmod.sample_i();
    write!(s, "audio_samples [ch0={:06} ch1={:06}\r\n",
           si[0],
           si[1]).ok();
    write!(s, "               ch2={:06} ch3={:06}]\r\n",
           si[2],
           si[3]).ok();
    write!(s, "audio_if      [jack={:x} touch_err={:x}]\r\n",
           pmod.jack(),
           pmod.touch_err()).ok();
    let touch = pmod.touch();
    write!(s, "audio_touch   [t0={:x} t1={:x} t2={:x} t3={:x}\r\n",
           touch[0], touch[1], touch[2], touch[3]).ok();
    write!(s, "               t4={:x} t5={:x} t6={:x} t7={:x}]\r\n",
           touch[4], touch[5], touch[6], touch[7]).ok();
}

fn print_die_temperature(s: &mut ReportString, dtr: &pac::DTR0)
{
    // From Table 4.3 in FPGA-TN-02210-1-4
    // "Power Consumption and Management for ECP5 and ECP5-5G Devices"
    let code_to_celsius: [i16; 64] = [
        -58, -56, -54, -52, -45, -44, -43, -42,
        -41, -40, -39, -38, -37, -36, -30, -20,
        -10,  -4,   0,   4,  10,  21,  22,  23,
         24,  25,  26,  27,  28,  29,  40,  50,
         60,  70,  76,  80,  81,  82,  83,  84,
         85,  86,  87,  88,  89,  95,  96,  97,
         98,  99, 100, 101, 102, 103, 104, 105,
        106, 107, 108, 116, 120, 124, 128, 132
    ];
    let code = dtr.temperature().read().bits();
    write!(s, "die_temp [code={} celsius={}]\r\n",
           code,
           code_to_celsius[code as usize]).ok();
}

fn print_psram_stats(s: &mut ReportString, psram: &pac::PSRAM_CSR)
{
    psram.ctrl().write(|w| w.collect().bit(false));
    let cycles_elapsed: u32 = psram.stats0().read().cycles_elapsed().bits();
    let cycles_idle: u32 = psram.stats1().read().cycles_idle().bits();
    let cycles_ack_r: u32 = psram.stats2().read().cycles_ack_r().bits();
    let cycles_ack_w: u32 = psram.stats3().read().cycles_ack_w().bits();
    psram.ctrl().write(|w| w.collect().bit(true));
    let sysclk = pac::clock::sysclk();
    write!(s,
           concat!("psram [busy={}%, wasted={}%, read={}%,\r\n",
                   "       write={}%, refresh={}Hz]\r\n"),
           (100.0f32 * (1.0f32 - (cycles_idle as f32 / cycles_elapsed as f32))) as u32,
           (100.0f32 * (cycles_elapsed - cycles_idle - cycles_ack_r - cycles_ack_w) as f32 / cycles_elapsed as f32) as u32,
           (100.0f32 * cycles_ack_r as f32 / cycles_elapsed as f32) as u32,
           (100.0f32 * cycles_ack_w as f32 / cycles_elapsed as f32) as u32,
           sysclk / (cycles_elapsed+1)).ok();
}

struct App {
    ui: ui::UI<Encoder0, EurorackPmod0, I2c0, Opts>,
}

impl App {
    pub fn new(opts: Opts) -> Self {
        let peripherals = unsafe { pac::Peripherals::steal() };
        let encoder = Encoder0::new(peripherals.ENCODER0);
        let pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
        let i2cdev = I2c0::new(peripherals.I2C0);
        let pca9635 = Pca9635Driver::new(i2cdev);
        Self {
            ui: ui::UI::new(opts, TIMER0_ISR_PERIOD_MS,
                            encoder, pca9635, pmod),
        }
    }
}

fn push_to_opts(constants: &CalibrationConstants, options: &mut Opts, d: &DefaultCalibrationConstants) {
    let c = constants.to_tweakable(d);
    options.caladc.scale0.value = c.adc_scale[0];
    options.caladc.scale1.value = c.adc_scale[1];
    options.caladc.scale2.value = c.adc_scale[2];
    options.caladc.scale3.value = c.adc_scale[3];
    options.caladc.zero0.value  = c.adc_zero[0];
    options.caladc.zero1.value  = c.adc_zero[1];
    options.caladc.zero2.value  = c.adc_zero[2];
    options.caladc.zero3.value  = c.adc_zero[3];
    options.caldac.scale0.value = c.dac_scale[0];
    options.caldac.scale1.value = c.dac_scale[1];
    options.caldac.scale2.value = c.dac_scale[2];
    options.caldac.scale3.value = c.dac_scale[3];
    options.caldac.zero0.value  = c.dac_zero[0];
    options.caldac.zero1.value  = c.dac_zero[1];
    options.caldac.zero2.value  = c.dac_zero[2];
    options.caldac.zero3.value  = c.dac_zero[3];
}

#[entry]
fn main() -> ! {
    let peripherals = pac::Peripherals::take().unwrap();

    // initialize logging
    let serial = Serial0::new(peripherals.UART0);
    tiliqua_fw::handlers::logger_init(serial);

    let sysclk = pac::clock::sysclk();
    let mut timer = Timer0::new(peripherals.TIMER0, sysclk);
    let mut persist = Persist0::new(peripherals.PERSIST_PERIPH);

    info!("Hello from Tiliqua selftest!");


    let bootinfo = unsafe { bootinfo::BootInfo::from_addr(BOOTINFO_BASE) }.unwrap();
    let modeline = bootinfo.modeline.maybe_override_fixed(
        FIXED_MODELINE, CLOCK_DVI_HZ);

    let mut i2cdev = I2c0::new(peripherals.I2C0);
    let mut i2cdev1 = I2c1::new(peripherals.I2C1);
    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
    let dtr = peripherals.DTR0;

    let mut startup_report = ReportString::new();

    psram_memtest(&mut startup_report, &mut timer);
    spiflash_memtest(&mut startup_report, &mut timer);
    tusb322_id_test(&mut startup_report, &mut i2cdev);
    print_touch_err(&mut startup_report, &pmod);
    eeprom_id_test(&mut startup_report, &mut i2cdev1);
    edid_test(&mut startup_report, &mut i2cdev);

    timer.disable();
    timer.delay_ns(0);


    let mut opts = Opts::default();
    let cal_default = DefaultCalibrationConstants::from_array(
        &PMOD_DEFAULT_CAL, pmod.f_bits());
    if let Some(cal_constants) = CalibrationConstants::from_eeprom(&mut i2cdev1) {
        push_to_opts(&cal_constants, &mut opts, &cal_default);
        write!(startup_report, "PASS: load calibration from EEPROM").ok();
    } else {
        write!(startup_report, "FAIL: load calibration from EEPROM").ok();
    }

    info!("STARTUP REPORT: {}", startup_report);

    let app = Mutex::new(RefCell::new(App::new(opts)));
    let hue = 10;

    let mut display = hal::new_framebuffer!(peripherals, modeline.clone());

    handler!(timer0 = || timer0_handler(&app));

    let psram = peripherals.PSRAM_CSR;

    let mut last_hpd = display.get_hpd();

    let mut benchmark_rng = Rng::with_seed(0);

    use tiliqua_hal::cy8cmbr3xxx::Cy8cmbr3108Driver;
    let i2cdev_cy8 = I2c1::new(unsafe { pac::I2C1::steal() } );
    let mut cy8 = Cy8cmbr3108Driver::new(i2cdev_cy8, &TOUCH_SENSOR_ORDER);

    let mut last_jack = pmod.jack();

    let gpio0 = peripherals.GPIO0;
    let gpio1 = peripherals.GPIO1;

    irq::scope(|s| {

        palette::ColorPalette::default().write_to_hardware(&mut display);
        persist.set_persistence(20);

        s.register(handlers::Interrupt::TIMER0, timer0);

        timer.enable_tick_isr(TIMER0_ISR_PERIOD_MS,
                              pac::Interrupt::TIMER0);

        let h_active = display.size().width;
        let v_active = display.size().height;

        loop {
            let dvi_hpd = display.get_hpd();
            if last_hpd != dvi_hpd {
                info!("dvi_hpd: display hotplug! new state: {}", dvi_hpd);
                last_hpd = dvi_hpd;
            }

            if pmod.jack() != last_jack {
                let _ = cy8.reset();
            }
            last_jack = pmod.jack();

            let (opts, commit_to_eeprom) = critical_section::with(|cs| {
                let mut app = app.borrow_ref_mut(cs);
                let commit_to_eeprom = app.ui.opts.autocal.write.poll();
                (app.ui.opts.clone(), commit_to_eeprom)
            });

            let counts_per_v = pmod.counts_per_v();
            let stimulus_raw = counts_per_v * opts.autocal.volts.value as i32;

            draw::draw_options(&mut display, &opts, h_active/2-30, 70,
                               hue).ok();
            draw::draw_name(&mut display, h_active/2, 30, hue,
                            &bootinfo.manifest.name, &bootinfo.manifest.tag, &modeline).ok();

            if opts.tracker.page.value == Page::Report {
                let mut status_report = ReportString::new();
                let report_str = match opts.report.page.value {
                    ReportPage::Startup => &startup_report,
                    ReportPage::Status  => {
                        critical_section::with(|_| {
                            // Devices shared with timer callback, be careful!
                            print_pmod_state(&mut status_report, &pmod);
                            print_usb_state(&mut status_report, &mut i2cdev);
                        });
                        print_die_temperature(&mut status_report, &dtr);
                        print_psram_stats(&mut status_report, &psram);
                        write!(&mut status_report, "dvi_hpd [active={}]\r\n", dvi_hpd).ok();
                        write!(&mut status_report, "ex0={:08b} ex1={:08b}\r\n",
                               gpio0.input().read().bits(),
                               gpio1.input().read().bits()).ok();
                        &status_report
                    }
                };
                if let Some(ref help) = bootinfo.manifest.help {
                    draw::draw_tiliqua(&mut display, (h_active/2-80) as i32, (v_active/2-250) as i32, hue,
                        help.io_left.each_ref().map(|s| s.as_str()),
                        help.io_right.each_ref().map(|s| s.as_str())
                    ).ok();
                }
                Text::with_alignment(
                    report_str,
                    Point::new((h_active/2-200) as i32, (v_active/2-20) as i32),
                    MonoTextStyle::new(&FONT_9X15, HI8::new(hue, 10)),
                    Alignment::Left
                ).draw(&mut display).ok();
            }

            if opts.tracker.page.value == Page::Autocal {
                pmod.registers.sample_o0().write(|w| unsafe { w.sample().bits(stimulus_raw as u32) } );
                pmod.registers.sample_o1().write(|w| unsafe { w.sample().bits(stimulus_raw as u32) } );
                pmod.registers.sample_o2().write(|w| unsafe { w.sample().bits(stimulus_raw as u32) } );
                pmod.registers.sample_o3().write(|w| unsafe { w.sample().bits(stimulus_raw as u32) } );
            }

            if opts.tracker.page.value == Page::Benchmark {
                let fps = {
                    // TODO: use the dedicated timer instead of abusing the PSRAM stats
                    // collection timer :)
                    psram.ctrl().write(|w| w.collect().bit(false));
                    let cycles_elapsed: u32 = psram.stats0().read().cycles_elapsed().bits();
                    psram.ctrl().write(|w| w.collect().bit(true));
                    sysclk / (cycles_elapsed+1)
                };
                let mut ops_per_loop = 0u32;
                if opts.benchmark.enabled.value == StopRun::Run {
                    use options::BenchmarkType;
                    match opts.benchmark.test_type.value {
                        BenchmarkType::Lines => {
                            ops_per_loop = 150;
                            draw::draw_benchmark_lines(&mut display, ops_per_loop, &mut benchmark_rng).ok();
                        },
                        BenchmarkType::Text => {
                            ops_per_loop = 150;
                            draw::draw_benchmark_text(&mut display, ops_per_loop, &mut benchmark_rng).ok();
                        },
                        BenchmarkType::Pixels => {
                            ops_per_loop = 10000;
                            draw::draw_benchmark_pixels(&mut display, ops_per_loop, &mut benchmark_rng).ok();
                        },
                        BenchmarkType::Unicode => {
                            ops_per_loop = 1;
                            draw::draw_benchmark_unicode(&mut display, ops_per_loop, &mut benchmark_rng).ok();
                        },
                    }
                }
                draw::draw_benchmark_stats(&mut display, h_active/2-50, v_active-50, hue,
                                           fps, fps*ops_per_loop).ok();
            }

            //
            // Push calibration constants to audio interface
            //
            let constants = CalibrationConstants::from_tweakable(
                TweakableConstants {
                    adc_scale: [
                        opts.caladc.scale0.value,
                        opts.caladc.scale1.value,
                        opts.caladc.scale2.value,
                        opts.caladc.scale3.value,
                    ],
                    adc_zero: [
                        opts.caladc.zero0.value,
                        opts.caladc.zero1.value,
                        opts.caladc.zero2.value,
                        opts.caladc.zero3.value,
                    ],
                    dac_scale: [
                        opts.caldac.scale0.value,
                        opts.caldac.scale1.value,
                        opts.caldac.scale2.value,
                        opts.caldac.scale3.value,
                    ],
                    dac_zero: [
                        opts.caldac.zero0.value,
                        opts.caldac.zero1.value,
                        opts.caldac.zero2.value,
                        opts.caldac.zero3.value,
                    ],
                },
                &cal_default
            );
            constants.write_to_pmod(&mut pmod);

            if commit_to_eeprom {
                critical_section::with(|_| {
                    constants.write_to_eeprom(&mut i2cdev1);
                });
            }

            if opts.tracker.page.value != Page::Report && opts.tracker.page.value != Page::Benchmark {
                draw::draw_cal(&mut display, h_active/2-128, v_active/2-128, hue,
                               &[stimulus_raw, stimulus_raw, stimulus_raw, stimulus_raw],
                               &pmod.sample_i(), counts_per_v).ok();
                draw::draw_cal_constants(
                    &mut display, h_active/2-128, v_active/2+64, hue,
                    &constants.cal.adc_scale, &constants.cal.adc_zero, &constants.cal.dac_scale, &constants.cal.dac_zero,
                    pmod.f_bits()).ok();

            }

        }
    })
}
