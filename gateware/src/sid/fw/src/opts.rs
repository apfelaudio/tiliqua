use tiliqua_lib::opt::*;
use tiliqua_lib::impl_option_view;
use tiliqua_lib::impl_option_page;

use heapless::String;

use core::str::FromStr;

use strum_macros::{EnumIter, IntoStaticStr};


#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
pub enum Screen {
    Voice1,
    Voice2,
    Voice3,
    Filter,
}

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "kebab-case")]
pub enum Wave {
    Triangle,
    Saw,
    Pulse,
    Noise,
}

#[derive(Clone)]
pub struct VoiceOptions {
    pub selected: Option<usize>,
    pub freq:    NumOption<u16>,
    pub pw:      NumOption<u16>,
    pub wave:    EnumOption<Wave>,
    pub gate:    NumOption<u8>,
    pub sync:    NumOption<u8>,
    pub ring:    NumOption<u8>,
    pub attack:  NumOption<u8>,
    pub decay:   NumOption<u8>,
    pub sustain: NumOption<u8>,
    pub release: NumOption<u8>,
}

impl_option_view!(VoiceOptions,
                  freq,
                  pw,
                  wave,
                  gate,
                  sync,
                  ring,
                  attack,
                  decay,
                  sustain,
                  release);

#[derive(Clone)]
pub struct FilterOptions {
    pub selected:  Option<usize>,
    pub cutoff:    NumOption<u16>,
    pub reso:      NumOption<u8>,
    pub filt1:     NumOption<u8>,
    pub filt2:     NumOption<u8>,
    pub filt3:     NumOption<u8>,
    pub lp:        NumOption<u8>,
    pub bp:        NumOption<u8>,
    pub hp:        NumOption<u8>,
    pub v3off:     NumOption<u8>,
    pub volume:    NumOption<u8>,
}

impl_option_view!(FilterOptions,
                  cutoff,
                  reso,
                  filt1,
                  filt2,
                  filt3,
                  lp,
                  bp,
                  hp,
                  v3off,
                  volume);

#[derive(Clone)]
pub struct Options {
    pub modify: bool,
    pub screen: EnumOption<Screen>,

    pub voice1: VoiceOptions,
    pub voice2: VoiceOptions,
    pub voice3: VoiceOptions,
    pub filter: FilterOptions,
}

impl_option_page!(Options,
                  (Screen::Voice1, voice1),
                  (Screen::Voice2, voice2),
                  (Screen::Voice3, voice3),
                  (Screen::Filter, filter)
                  );

impl Options {
    pub fn new() -> Options {
        Options {
            modify: false,
            screen: EnumOption {
                name: String::from_str("screen").unwrap(),
                value: Screen::Voice1,
            },
            voice1: VoiceOptions {
                selected: None,
                freq: NumOption{
                    name: String::from_str("freq").unwrap(),
                    value: 1000,
                    step: 250,
                    min: 0,
                    max: 65500,
                },
                pw: NumOption{
                    name: String::from_str("pw").unwrap(),
                    value: 2048,
                    step: 128,
                    min: 0,
                    max: 4096,
                },
                wave: EnumOption{
                    name: String::from_str("wave").unwrap(),
                    value: Wave::Noise,
                },
                gate: NumOption{
                    name: String::from_str("gate").unwrap(),
                    value: 1,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                sync: NumOption{
                    name: String::from_str("sync").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                ring: NumOption{
                    name: String::from_str("ring").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                attack: NumOption{
                    name: String::from_str("attack").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                decay: NumOption{
                    name: String::from_str("decay").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                sustain: NumOption{
                    name: String::from_str("sustain").unwrap(),
                    value: 15,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                release: NumOption{
                    name: String::from_str("release").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
            },
            voice2: VoiceOptions {
                selected: None,
                freq: NumOption{
                    name: String::from_str("freq").unwrap(),
                    value: 1750,
                    step: 250,
                    min: 0,
                    max: 65500,
                },
                pw: NumOption{
                    name: String::from_str("pw").unwrap(),
                    value: 2048,
                    step: 128,
                    min: 0,
                    max: 4096,
                },
                wave: EnumOption{
                    name: String::from_str("wave").unwrap(),
                    value: Wave::Triangle,
                },
                gate: NumOption{
                    name: String::from_str("gate").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                sync: NumOption{
                    name: String::from_str("sync").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                ring: NumOption{
                    name: String::from_str("ring").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                attack: NumOption{
                    name: String::from_str("attack").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                decay: NumOption{
                    name: String::from_str("decay").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                sustain: NumOption{
                    name: String::from_str("sustain").unwrap(),
                    value: 15,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                release: NumOption{
                    name: String::from_str("release").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
            },
            voice3: VoiceOptions {
                selected: None,
                freq: NumOption{
                    name: String::from_str("freq").unwrap(),
                    value: 2000,
                    step: 250,
                    min: 0,
                    max: 65500,
                },
                pw: NumOption{
                    name: String::from_str("pw").unwrap(),
                    value: 2048,
                    step: 128,
                    min: 0,
                    max: 4096,
                },
                wave: EnumOption{
                    name: String::from_str("wave").unwrap(),
                    value: Wave::Triangle,
                },
                gate: NumOption{
                    name: String::from_str("gate").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                sync: NumOption{
                    name: String::from_str("sync").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                ring: NumOption{
                    name: String::from_str("ring").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                attack: NumOption{
                    name: String::from_str("attack").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                decay: NumOption{
                    name: String::from_str("decay").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                sustain: NumOption{
                    name: String::from_str("sustain").unwrap(),
                    value: 15,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                release: NumOption{
                    name: String::from_str("release").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
            },
            filter: FilterOptions {
                selected: None,
                cutoff: NumOption{
                    name: String::from_str("cutoff").unwrap(),
                    value: 1500,
                    step: 100,
                    min: 0,
                    max: 2000,
                },
                reso: NumOption{
                    name: String::from_str("reso").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                filt1: NumOption{
                    name: String::from_str("filt1").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                filt2: NumOption{
                    name: String::from_str("filt2").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                filt3: NumOption{
                    name: String::from_str("filt3").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                lp: NumOption{
                    name: String::from_str("lp").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                bp: NumOption{
                    name: String::from_str("bp").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                hp: NumOption{
                    name: String::from_str("hp").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                v3off: NumOption{
                    name: String::from_str("3off").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 1,
                },
                volume: NumOption{
                    name: String::from_str("volume").unwrap(),
                    value: 15,
                    step: 1,
                    min: 0,
                    max: 15,
                },
            },
        }
    }
}
