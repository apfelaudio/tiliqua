use tiliqua_lib::opt::*;
use tiliqua_lib::impl_option_view;
use tiliqua_lib::impl_option_page;

use heapless::String;

use core::str::FromStr;

use strum_macros::{EnumIter, IntoStaticStr};


#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
pub enum Screen {
    Modulate,
    Voice1,
    Voice2,
    Voice3,
    Filter,
    Scope,
}

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "kebab-case")]
pub enum TriggerMode {
    Always,
    Rising,
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
    pub freq_os: NumOption<u16>,
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
                  freq_os,
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
pub struct ScopeOptions {
    pub selected: Option<usize>,
    pub timebase: NumOption<u16>,
    pub trigger_mode: EnumOption<TriggerMode>,
    pub trigger_lvl: NumOption<i16>,
    pub ypos0: NumOption<i16>,
    pub ypos1: NumOption<i16>,
    pub ypos2: NumOption<i16>,
    pub ypos3: NumOption<i16>,
    pub yscale: NumOption<u8>,
    pub xscale: NumOption<u8>,
    pub xpos:  NumOption<i16>,
}

impl_option_view!(ScopeOptions,
                  timebase, trigger_mode, trigger_lvl,
                  ypos0, ypos1, ypos2, ypos3, yscale, xscale, xpos);

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "kebab-case")]
pub enum ModulationTarget {
    Nothing,
    Freq1,
    Freq2,
    Freq3,
    Freq12,
    Gate1,
    Gate2,
    Gate3,
    Gate12,
    PWidth1,
    PWidth2,
    PWidth3,
    FiltCut,
}

pub enum VoiceModulationType {
    Frequency,
    Gate,
    PulseWidth
}

impl ModulationTarget {
    pub fn modulates_voice(&self, n: usize) -> Option<VoiceModulationType> {
        use ModulationTarget::*;
        use VoiceModulationType::*;
        match (n, *self) {
            (0, Freq1)  => Some(Frequency),
            (1, Freq2)  => Some(Frequency),
            (2, Freq3)  => Some(Frequency),
            (0, Freq12) => Some(Frequency),
            (1, Freq12) => Some(Frequency),
            (0, Gate1)  => Some(Gate),
            (1, Gate2)  => Some(Gate),
            (2, Gate3)  => Some(Gate),
            (0, Gate12) => Some(Gate),
            (1, Gate12) => Some(Gate),
            (0, PWidth1)  => Some(PulseWidth),
            (1, PWidth2)  => Some(PulseWidth),
            (2, PWidth3)  => Some(PulseWidth),
            _ =>          None
        }
    }
}

#[derive(Clone)]
pub struct ModulateOptions {
    pub selected:  Option<usize>,
    pub in0:       EnumOption<ModulationTarget>,
    pub in1:       EnumOption<ModulationTarget>,
    pub in2:       EnumOption<ModulationTarget>,
    pub in3:       EnumOption<ModulationTarget>,
}

impl_option_view!(ModulateOptions,
                  in0,
                  in1,
                  in2,
                  in3);

#[derive(Clone)]
pub struct Options {
    pub modify: bool,
    pub screen: EnumOption<Screen>,
    pub modulate: ModulateOptions,
    pub voice1: VoiceOptions,
    pub voice2: VoiceOptions,
    pub voice3: VoiceOptions,
    pub filter: FilterOptions,
    pub scope:  ScopeOptions,
}

impl_option_page!(Options,
                  (Screen::Modulate, modulate),
                  (Screen::Voice1, voice1),
                  (Screen::Voice2, voice2),
                  (Screen::Voice3, voice3),
                  (Screen::Filter, filter),
                  (Screen::Scope,   scope)
                  );

impl VoiceOptions {
    fn new() -> VoiceOptions {
        VoiceOptions {
            selected: None,
            freq: NumOption{
                name: String::from_str("f-base").unwrap(),
                value: 1000,
                step: 125,
                min: 0,
                max: 65500,
            },
            freq_os: NumOption{
                name: String::from_str("f-offs").unwrap(),
                value: 1000,
                step: 10,
                min: 500,
                max: 2000,
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
        }
    }
}

impl Options {
    pub fn new() -> Options {
        Options {
            modify: false,
            screen: EnumOption {
                name: String::from_str("screen").unwrap(),
                value: Screen::Voice1,
            },
            modulate: ModulateOptions {
                selected: None,
                in0: EnumOption {
                    name: String::from_str("in0").unwrap(),
                    value: ModulationTarget::Nothing,
                },
                in1: EnumOption {
                    name: String::from_str("in1").unwrap(),
                    value: ModulationTarget::Nothing,
                },
                in2: EnumOption {
                    name: String::from_str("in2").unwrap(),
                    value: ModulationTarget::Nothing,
                },
                in3: EnumOption {
                    name: String::from_str("in3").unwrap(),
                    value: ModulationTarget::Nothing,
                },
            },
            voice1: VoiceOptions::new(),
            voice2: VoiceOptions::new(),
            voice3: VoiceOptions::new(),
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
            scope: ScopeOptions {
                selected: None,
                timebase: NumOption{
                    name: String::from_str("timebase").unwrap(),
                    value: 32,
                    step: 128,
                    min: 32,
                    max: 3872,
                },
                trigger_mode: EnumOption {
                    name: String::from_str("trig-mode").unwrap(),
                    value: TriggerMode::Always,
                },
                trigger_lvl: NumOption{
                    name: String::from_str("trig-lvl").unwrap(),
                    value: 0,
                    step: 512,
                    min: -512*32,
                    max: 512*32,
                },
                ypos0: NumOption{
                    name: String::from_str("ypos0").unwrap(),
                    value: 150,
                    step: 25,
                    min: -500,
                    max: 500,
                },
                ypos1: NumOption{
                    name: String::from_str("ypos1").unwrap(),
                    value: -150,
                    step: 25,
                    min: -500,
                    max: 500,
                },
                ypos2: NumOption{
                    name: String::from_str("ypos2").unwrap(),
                    value: -50,
                    step: 25,
                    min: -500,
                    max: 500,
                },
                ypos3: NumOption{
                    name: String::from_str("ypos3").unwrap(),
                    value: 50,
                    step: 25,
                    min: -500,
                    max: 500,
                },
                yscale: NumOption{
                    name: String::from_str("yscale").unwrap(),
                    value: 8,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                xscale: NumOption{
                    name: String::from_str("xscale").unwrap(),
                    value: 7,
                    step: 1,
                    min: 0,
                    max: 15,
                },
                xpos: NumOption{
                    name: String::from_str("xpos").unwrap(),
                    value: 175,
                    step: 25,
                    min: -500,
                    max: 500,
                },
            },
        }
    }
}
