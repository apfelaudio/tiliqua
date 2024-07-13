use log::{Level, Metadata, Record};

use core::cell::RefCell;
use core::fmt::Write;

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
