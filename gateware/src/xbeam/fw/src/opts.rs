use tiliqua_lib::opt::*;
use tiliqua_lib::impl_option_view;
use tiliqua_lib::impl_option_page;

use heapless::String;

use core::str::FromStr;

use strum_macros::{EnumIter, IntoStaticStr};

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
pub enum Screen {
    Vector,
    Beam,
}

#[derive(Clone)]
pub struct VectorOptions {
    pub selected: Option<usize>,
    pub hue: NumOption<u8>,
    pub intensity: NumOption<u8>,
    pub xscale: NumOption<u8>,
    pub yscale: NumOption<u8>,
}

impl_option_view!(VectorOptions,
                  hue, intensity, xscale, yscale);

#[derive(Clone)]
pub struct BeamOptions {
    pub selected: Option<usize>,
    pub persist: NumOption<u16>,
    pub decay: NumOption<u8>,
    pub ui_hue: NumOption<u8>,
}

impl_option_view!(BeamOptions,
                  persist, decay, ui_hue);

#[derive(Clone)]
pub struct Options {
    pub modify: bool,
    pub screen: EnumOption<Screen>,

    pub vector: VectorOptions,
    pub beam:   BeamOptions,
}

impl_option_page!(Options,
                  (Screen::Vector, vector),
                  (Screen::Beam,     beam)
                  );

impl Options {
    pub fn new() -> Options {
        Options {
            modify: false,
            screen: EnumOption {
                name: String::from_str("screen").unwrap(),
                value: Screen::Vector,
            },
            vector: VectorOptions {
                selected: None,
                hue: NumOption{
                    name: String::from_str("hue").unwrap(),
                    value: 10,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                intensity: NumOption{
                    name: String::from_str("intensity").unwrap(),
                    value: 8,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                xscale: NumOption{
                    name: String::from_str("xscale").unwrap(),
                    value: 6,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                yscale: NumOption{
                    name: String::from_str("yscale").unwrap(),
                    value: 6,
                    step: 1,
                    min: 0,
                    max: 15,
                },
            },
            beam: BeamOptions {
                selected: None,
                persist: NumOption{
                    name: String::from_str("persist").unwrap(),
                    value: 1024,
                    step: 256,
                    min: 512,
                    max: 32768,
                },
                decay: NumOption{
                    name: String::from_str("decay").unwrap(),
                    value: 1,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                ui_hue: NumOption{
                    name: String::from_str("ui-hue").unwrap(),
                    value: 10,
                    step: 1,
                    min: 0,
                    max: 15,
                },
            },
        }
    }
}
