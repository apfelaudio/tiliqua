use tiliqua_lib::opt::*;
use tiliqua_lib::impl_option_view;
use tiliqua_lib::impl_option_page;
use tiliqua_lib::manifest;

use heapless::String;

use core::str::FromStr;

use strum_macros::{EnumIter, IntoStaticStr};

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
pub enum Screen {
    Boot,
}

#[derive(Clone)]
pub struct BootOptions {
    pub selected: Option<usize>,
    pub bitstream0: NumOption<u8>,
    pub bitstream1: NumOption<u8>,
    pub bitstream2: NumOption<u8>,
    pub bitstream3: NumOption<u8>,
    pub bitstream4: NumOption<u8>,
    pub bitstream5: NumOption<u8>,
    pub bitstream6: NumOption<u8>,
    pub bitstream7: NumOption<u8>,
}

impl_option_view!(BootOptions,
                  bitstream0,
                  bitstream1,
                  bitstream2,
                  bitstream3,
                  bitstream4,
                  bitstream5,
                  bitstream6,
                  bitstream7);

#[derive(Clone)]
pub struct Options {
    pub modify: bool,
    pub draw:bool,
    pub screen: EnumOption<Screen>,

    pub boot: BootOptions,
}

impl_option_page!(Options,
                  (Screen::Boot, boot));

impl Options {
    pub fn new(manifests: &[Option<manifest::BitstreamManifest>; 8]) -> Options {
        let mut names: [OptionString; 8] = [const { OptionString::new() }; 8];
        for n in 0..manifests.len() {
            if let Some(manifest) = &manifests[n] {
                names[n] = manifest.name.clone();
            }
        }
        Options {
            modify: false,
            draw: true,
            screen: EnumOption {
                name: String::from_str("screen").unwrap(),
                value: Screen::Boot,
            },
            boot: BootOptions {
                selected: Some(0),
                bitstream0: NumOption{
                    name: names[0].clone(),
                    value: 0,
                    step: 0,
                    min: 0,
                    max: 8,
                },
                bitstream1: NumOption{
                    name: names[1].clone(),
                    value: 1,
                    step: 0,
                    min: 0,
                    max: 8,
                },
                bitstream2: NumOption{
                    name: names[2].clone(),
                    value: 2,
                    step: 0,
                    min: 0,
                    max: 8,
                },
                bitstream3: NumOption{
                    name: names[3].clone(),
                    value: 3,
                    step: 0,
                    min: 0,
                    max: 8,
                },
                bitstream4: NumOption{
                    name: names[4].clone(),
                    value: 4,
                    step: 0,
                    min: 0,
                    max: 8,
                },
                bitstream5: NumOption{
                    name: names[5].clone(),
                    value: 5,
                    step: 0,
                    min: 0,
                    max: 8,
                },
                bitstream6: NumOption{
                    name: names[6].clone(),
                    value: 6,
                    step: 0,
                    min: 0,
                    max: 8,
                },
                bitstream7: NumOption{
                    name: names[7].clone(),
                    value: 7,
                    step: 0,
                    min: 0,
                    max: 8,
                },
            },
        }
    }
}
