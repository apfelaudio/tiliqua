use tiliqua_lib::opt::*;
use tiliqua_lib::impl_option_view;
use tiliqua_lib::impl_option_page;
use tiliqua_lib::palette::ColorPalette;

use heapless::String;

use core::str::FromStr;

use strum_macros::{EnumIter, IntoStaticStr};

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
pub enum Screen {
    Help,
    Poly,
    Beam,
    Vector,
}

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "kebab-case")]
pub enum TouchControl {
    On,
    Off,
}

#[derive(Clone)]
pub struct HelpOptions {
    pub selected:  Option<usize>,
    pub page:      NumOption<u16>,
}

impl_option_view!(HelpOptions,
                  page);

#[derive(Clone)]
pub struct PolyOptions {
    pub selected: Option<usize>,
    pub interface: EnumOption<TouchControl>,
    pub drive:     NumOption<u16>,
    pub reso:      NumOption<u16>,
    pub diffuse:   NumOption<u16>,
}

impl_option_view!(PolyOptions,
                  interface,
                  drive,
                  reso,
                  diffuse);

#[derive(Clone)]
pub struct VectorOptions {
    pub selected: Option<usize>,
    pub xscale: NumOption<u8>,
    pub yscale: NumOption<u8>,
}

impl_option_view!(VectorOptions,
                  xscale, yscale);

#[derive(Clone)]
pub struct BeamOptions {
    pub selected: Option<usize>,
    pub persist: NumOption<u16>,
    pub decay: NumOption<u8>,
    pub intensity: NumOption<u8>,
    pub hue: NumOption<u8>,
    pub palette: EnumOption<ColorPalette>,
}

impl_option_view!(BeamOptions,
                  persist, decay, intensity, hue, palette);

#[derive(Clone)]
pub struct Options {
    pub modify: bool,
    pub draw: bool,
    pub screen: EnumOption<Screen>,

    pub help:   HelpOptions,
    pub poly:   PolyOptions,
    pub beam:   BeamOptions,
    pub vector: VectorOptions,
}

impl_option_page!(Options,
                  (Screen::Help,   help),
                  (Screen::Poly,   poly),
                  (Screen::Beam,   beam),
                  (Screen::Vector, vector));

impl Options {
    pub fn new() -> Options {
        Options {
            modify: true,
            draw: true,
            screen: EnumOption {
                name: String::from_str("screen").unwrap(),
                value: Screen::Help,
            },
            help: HelpOptions {
                selected: None,
                page: NumOption{
                    name: String::from_str("page").unwrap(),
                    value: 0,
                    step: 0,
                    min: 0,
                    max: 0,
                },
            },
            poly: PolyOptions {
                selected: None,
                interface: EnumOption{
                    name: String::from_str("touch").unwrap(),
                    value: TouchControl::On,
                },
                drive: NumOption{
                    name: String::from_str("overdrive").unwrap(),
                    value: 16384,
                    step: 2048,
                    min: 0,
                    max: 32768,
                },
                reso: NumOption{
                    name: String::from_str("resonance").unwrap(),
                    value: 16384,
                    step: 2048,
                    min: 8192,
                    max: 32768,
                },
                diffuse: NumOption{
                    name: String::from_str("diffusion").unwrap(),
                    value: 12288,
                    step: 2048,
                    min: 0,
                    max: 32768,
                },
            },
            beam: BeamOptions {
                selected: None,
                persist: NumOption{
                    name: String::from_str("persist").unwrap(),
                    value: 512,
                    step: 256,
                    min: 256,
                    max: 32768,
                },
                decay: NumOption{
                    name: String::from_str("decay").unwrap(),
                    value: 1,
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
                hue: NumOption{
                    name: String::from_str("hue").unwrap(),
                    value: 10,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                palette: EnumOption {
                    name: String::from_str("palette").unwrap(),
                    value: ColorPalette::Linear,
                },
            },
            vector: VectorOptions {
                selected: None,
                xscale: NumOption{
                    name: String::from_str("xscale").unwrap(),
                    value: 7,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                yscale: NumOption{
                    name: String::from_str("yscale").unwrap(),
                    value: 7,
                    step: 1,
                    min: 0,
                    max: 15,
                },
            },
        }
    }
}
