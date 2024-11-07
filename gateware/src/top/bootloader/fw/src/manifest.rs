use tiliqua_lib::generated_constants::*;

use heapless::String;
use core::str::FromStr;
use serde::{Deserialize};
use log::info;
use tiliqua_lib::opt::OptionString;

#[derive(Deserialize)]
pub struct BitstreamManifest {
    pub magic: u32,
    pub names: [OptionString; N_BITSTREAMS],
}

impl BitstreamManifest {
    pub fn unknown_manifest() -> Self {
        let unknown = String::from_str("<unknown>").unwrap();
        BitstreamManifest {
            magic: 0xDEADBEEFu32,
            names: [
                unknown.clone(),
                unknown.clone(),
                unknown.clone(),
                unknown.clone(),
                unknown.clone(),
                unknown.clone(),
                unknown.clone(),
                unknown.clone(),
            ],
        }
    }

    pub fn valid_magic(&self) -> bool {
        self.magic == 0xDEADBEEFu32
    }

    pub fn find() -> Option<BitstreamManifest> {
        let manifest_slice = unsafe {
            core::slice::from_raw_parts(
                MANIFEST_BASE as *mut u8,
                MANIFEST_SZ_BYTES,
            )
        };

        // Erasing flash should always set bytes to 0xff. Count back from the
        // end of the manifest region to find where there is data. Otherwise,
        // Serde will fail out with a TrailingCharacters error.
        let mut last_byte = MANIFEST_SZ_BYTES;
        for i in (0..MANIFEST_SZ_BYTES).rev() {
            if manifest_slice[i] != 0xff {
                last_byte = i+1;
                break;
            }
        }

        let manifest_slice = &manifest_slice[0..last_byte];

        let manifest_de = serde_json_core::from_slice::<BitstreamManifest>(manifest_slice);
        match manifest_de {
            Ok((contents, _rest)) => {
                if contents.valid_magic() {
                    info!("BitstreamManifest: magic OK");
                    Some(contents)
                } else {
                    info!("BitstreamManifest: magic NOT OK, ignoring");
                    None
                }
            }
            Err(err) => {
                info!("BitstreamManifest: parse failed with {:?}", err);
                info!("BitstreamManifest: bad or nonexisting manifest");
                None
            }
        }
    }
}

