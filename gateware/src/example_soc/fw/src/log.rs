//! A simple logger for the `log` crate which can log to any object
//! implementing `Write`

#![allow(unused_imports, unused_mut, unused_variables)]

use crate::{hal, pac};

use log::{Level, LevelFilter, Metadata, Record};

use core::cell::RefCell;
use core::fmt::Write;
use crate::Serial0;

// - initialization -----------------------------------------------------------

static LOGGER: WriteLogger<Serial0> = WriteLogger {
    writer: RefCell::new(None),
    level: Level::Trace,
};

pub fn init(writer: Serial0) {
    LOGGER.writer.replace(Some(writer));
    match log::set_logger(&LOGGER).map(|()| log::set_max_level(LevelFilter::Trace)) {
        Ok(()) => (),
        Err(_e) => {
            panic!("Failed to set logger");
        }
    }
}

// - implementation -----------------------------------------------------------

/// WriteLogger
pub struct WriteLogger<W>
where
    W: Write + Send,
{
    pub writer: RefCell<Option<W>>,
    pub level: Level,
}

impl<W> log::Log for WriteLogger<W>
where
    W: Write + Send,
{
    fn enabled(&self, metadata: &Metadata) -> bool {
        metadata.level() <= self.level
    }

    fn log(&self, record: &Record) {

        if !self.enabled(record.metadata()) {
            return;
        }

        let color = match record.level() {
            Level::Error => "31", // red
            Level::Warn  => "33", // yellow
            _            => "32", // green
        };

        match self.writer.borrow_mut().as_mut() {
            Some(writer) => match writeln!(writer, "[\x1B[{}m{}\x1B[0m] {}\r", color, record.level(), record.args()) {
                Ok(()) => (),
                Err(_e) => {
                    panic!("Logger failed to write to device");
                }
            },
            None => {
                panic!("Logger has not been initialized");
            }
        }
    }

    fn flush(&self) {}
}

unsafe impl<W: Write + Send> Sync for WriteLogger<W> {}
