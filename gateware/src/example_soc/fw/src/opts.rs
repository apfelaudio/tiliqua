use tiliqua_lib::opt::*;
use tiliqua_lib::impl_option_view;
use tiliqua_lib::impl_option_page;

use heapless::String;

use core::str::FromStr;

use strum_macros::{EnumIter, IntoStaticStr};

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "kebab-case")]
pub enum TouchLedMirror {
    MirrorOff,
    MirrorOn,
}

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "kebab-case")]
pub enum NoteControl {
    Touch,
    Midi,
}

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
pub enum Screen {
    Xbeam,
    Scope,
    Touch,
}

#[derive(Clone)]
pub struct XbeamOptions {
    pub selected: Option<usize>,
    pub persist: NumOption<u16>,
    pub hue: NumOption<u8>,
    pub intensity: NumOption<u8>,
    pub decay: NumOption<u8>,
}

#[derive(Clone)]
pub struct ScopeOptions {
    pub selected: Option<usize>,
    pub grain_sz: NumOption<u32>,
    pub trig_lvl: NumOption<i32>,
    pub trig_sns: NumOption<i32>,
}

#[derive(Clone)]
pub struct TouchOptions {
    pub selected: Option<usize>,
    pub note_control: EnumOption<NoteControl>,
    pub led_mirror: EnumOption<TouchLedMirror>,
}

impl_option_view!(XbeamOptions,
                  persist, hue, intensity, decay);

impl_option_view!(ScopeOptions,
                  grain_sz, trig_lvl, trig_sns);

impl_option_view!(TouchOptions,
                  note_control, led_mirror);


#[derive(Clone)]
pub struct Options {
    pub modify: bool,
    pub screen: EnumOption<Screen>,

    pub xbeam: XbeamOptions,
    pub scope: ScopeOptions,
    pub touch: TouchOptions,
}

impl_option_page!(Options,
                  (Screen::Xbeam, xbeam),
                  (Screen::Scope, scope),
                  (Screen::Touch, touch));

impl Options {
    pub fn new() -> Options {
        Options {
            modify: true,
            screen: EnumOption {
                name: String::from_str("screen").unwrap(),
                value: Screen::Xbeam,
            },
            xbeam: XbeamOptions {
                selected: None,
                persist: NumOption{
                    name: String::from_str("persist").unwrap(),
                    value: 1024,
                    step: 256,
                    min: 512,
                    max: 32768,
                },
                hue: NumOption{
                    name: String::from_str("hue").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                intensity: NumOption{
                    name: String::from_str("intensity").unwrap(),
                    value: 6,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                decay: NumOption{
                    name: String::from_str("decay").unwrap(),
                    value: 1,
                    step: 1,
                    min: 0,
                    max: 15,
                },
            },
            scope: ScopeOptions {
                selected: None,
                grain_sz: NumOption{
                    name: String::from_str("grainsz").unwrap(),
                    value: 1000,
                    step: 1,
                    min: 512,
                    max: 1000,
                },
                trig_lvl: NumOption{
                    name: String::from_str("trig lvl").unwrap(),
                    value: 0,
                    step: 100,
                    min: -10000,
                    max: 10000,
                },
                trig_sns: NumOption{
                    name: String::from_str("trig sns").unwrap(),
                    value: 1000,
                    step: 100,
                    min: 100,
                    max: 5000,
                },
            },
            touch: TouchOptions {
                selected: None,
                note_control: EnumOption{
                    name: String::from_str("control").unwrap(),
                    value: NoteControl::Touch,
                },
                led_mirror: EnumOption{
                    name: String::from_str("led").unwrap(),
                    value: TouchLedMirror::MirrorOn,
                },
            }
        }
    }
}
