use sequential_storage::map::{fetch_item, store_item, remove_all_items};
use sequential_storage::cache::NoCache;
use embassy_futures::block_on;
use embassy_embedded_hal::adapter::BlockingAsync;

use crate::traits::Options;

const DATA_BUFFER_SZ: usize = 32;
const DEFAULT_PAGE_KEY: u32 = 0xdeadbeef;

#[derive(Debug)]
pub enum PersistenceError {
    StorageError,
    SerializationError,
    FlashRangeError,
}

fn storage_error<E: core::fmt::Debug>(e: E) -> PersistenceError {
    log::error!("opts/persistence: storage error: {e:?}");
    PersistenceError::StorageError
}

pub trait OptionsPersistence {
    type Error;

    fn save_key(&mut self, key: u32, value: &[u8]) -> Result<(), Self::Error>;
    fn save_key_retries(&mut self, key: u32, value: &[u8], retries: usize) -> Result<(), Self::Error>;
    fn load_key(&mut self, key: u32, buffer: &mut [u8]) -> Result<Option<usize>, Self::Error>;

    fn erase_all(&mut self) -> Result<(), Self::Error>;
    fn load_options<O: Options>(&mut self, opts: &mut O) -> Result<(), Self::Error>;
    fn save_options<O: Options>(&mut self, opts: &O) -> Result<(), Self::Error>;
}

pub struct FlashOptionsPersistence<F> {
    flash: BlockingAsync<F>,
    flash_range: core::ops::Range<u32>,
    data_buffer: [u8; DATA_BUFFER_SZ],
}

impl<F> FlashOptionsPersistence<F> {
    pub fn new(flash: F, flash_range: core::ops::Range<u32>) -> Self {
        Self {
            flash: BlockingAsync::new(flash),
            flash_range,
            data_buffer: [0u8; DATA_BUFFER_SZ],
        }
    }

    pub fn open<O: Options>(flash: F, flash_range: Option<core::ops::Range<u32>>, opts: &mut O) -> Option<Self>
    where
        F: embedded_storage::nor_flash::NorFlash + embedded_storage::nor_flash::MultiwriteNorFlash,
    {
        if let Some(flash_range) = flash_range {
            let mut persist = Self::new(flash, flash_range);
            if let Err(e) = persist.load_options(opts) {
                log::error!("Failed to load persisted options: {e:?}");
            }
            Some(persist)
        } else {
            log::warn!("No option storage region: disable persistent storage");
            None
        }
    }
}

impl<F> OptionsPersistence for FlashOptionsPersistence<F>
where
    F: embedded_storage::nor_flash::NorFlash + embedded_storage::nor_flash::MultiwriteNorFlash,
{
    type Error = PersistenceError;

    fn save_key(&mut self, key: u32, value: &[u8]) -> Result<(), Self::Error> {
        block_on(store_item::<u32, &[u8], _>(
            &mut self.flash,
            self.flash_range.clone(),
            &mut NoCache::new(),
            &mut self.data_buffer,
            &key,
            &value,
        )).map_err(storage_error)
    }

    fn save_key_retries(&mut self, key: u32, value: &[u8], retries: usize) -> Result<(), Self::Error> {
        let mut result = Ok(());
        for _ in 0..retries {
            result = self.save_key(key, value);
            if result.is_ok() {
                return result;
            }
            log::warn!("save_key_retries: failed once with {result:?}");
        }
        result
    }

    fn load_key(&mut self, key: u32, buffer: &mut [u8]) -> Result<Option<usize>, Self::Error> {
        let item = block_on(fetch_item::<u32, &[u8], _>(
            &mut self.flash,
            self.flash_range.clone(),
            &mut NoCache::new(),
            &mut self.data_buffer,
            &key,
        )).map_err(storage_error)?;
        if let Some(data) = item {
            let len = data.len().min(buffer.len());
            buffer[..len].copy_from_slice(&data[..len]);
            Ok(Some(len))
        } else {
            Ok(None)
        }
    }

    fn erase_all(&mut self) -> Result<(), Self::Error> {
        block_on(remove_all_items::<u32, _>(
            &mut self.flash,
            self.flash_range.clone(),
            &mut NoCache::new(),
            &mut self.data_buffer,
        )).map_err(storage_error)
    }

    fn save_options<O: Options>(&mut self, opts: &O) -> Result<(), Self::Error> {
        for opt in opts.all() {
            let mut buf: [u8; DATA_BUFFER_SZ] = [0u8; DATA_BUFFER_SZ];
            if let Some(encoded_len) = opt.encode(&mut buf) {
                log::info!("opts/save: {}={} ({:x}={:?})", 
                          opt.name(), opt.value(), opt.key().value(), &buf[..encoded_len]);
                self.save_key_retries(opt.key().value(), &buf[..encoded_len], 2)?;
            }
        }
        let mut buf: [u8; DATA_BUFFER_SZ] = [0u8; DATA_BUFFER_SZ];
        if let Some(encoded_len) = opts.page().encode(&mut buf) {
            self.save_key(DEFAULT_PAGE_KEY, &buf[..encoded_len])?;
        }
        Ok(())
    }

    fn load_options<O: Options>(&mut self, opts: &mut O) -> Result<(), Self::Error> {
        for opt in opts.all_mut() {
            let mut buf: [u8; DATA_BUFFER_SZ] = [0u8; DATA_BUFFER_SZ];
            if let Some(len) = self.load_key(opt.key().value(), &mut buf)? {
                opt.decode(&buf[..len]);
                log::info!("opts/load: {}={} ({:x}={:?})", 
                          opt.name(), opt.value(), opt.key().value(), &buf[..len]);
            }
        }
        let mut buf: [u8; DATA_BUFFER_SZ] = [0u8; DATA_BUFFER_SZ];
        if let Some(len) = self.load_key(DEFAULT_PAGE_KEY, &mut buf)? {
            opts.page_mut().decode(&buf[..len]);
        }
        Ok(())
    }
}

