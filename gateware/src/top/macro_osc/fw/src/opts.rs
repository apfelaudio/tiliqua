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
    Scope,
    Osc,
    Beam,
    Vector,
}

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "kebab-case")]
pub enum TriggerMode {
    Always,
    Rising,
}

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "kebab-case")]
pub enum Engine {
    VrtAnlg1,
    PhaseDst,
    SixOp1,
    SixOp2,
    SixOp3,
    WvTrrn,
    String1,
    Chiptne,
    VrtAnlg2,
    WaveShp,
    FmEngine,
    Grain,
    Additive,
    Wavetbl,
    Chord,
    Speech,
    Swarm,
    Noise,
    Prticle,
    String2,
    Modal,
    BassDrm,
    Snare,
    Hihat,
}

#[derive(Clone)]
pub struct OscOptions {
    pub selected: Option<usize>,
    pub engine:    EnumOption<Engine>,
    pub note:      NumOption<u8>,
    pub harmonics: NumOption<u8>,
    pub timbre:    NumOption<u8>,
    pub morph:     NumOption<u8>,
}

impl_option_view!(OscOptions,
                  engine, note, harmonics, timbre, morph);

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
}

impl_option_view!(ScopeOptions,
                  timebase, trigger_mode, trigger_lvl,
                  ypos0, ypos1, yscale, xscale);

#[derive(Clone)]
pub struct Options {
    pub modify: bool,
    pub draw:   bool,
    pub screen: EnumOption<Screen>,

    pub osc:    OscOptions,
    pub vector: VectorOptions,
    pub beam:   BeamOptions,
    pub scope:  ScopeOptions,
}

impl_option_page!(Options,
                  (Screen::Scope,  scope),
                  (Screen::Osc,    osc),
                  (Screen::Beam,   beam),
                  (Screen::Vector, vector)
                  );

impl Options {
    pub fn new() -> Options {
        Options {
            modify: false,
            draw:   true,
            screen: EnumOption {
                name: String::from_str("screen").unwrap(),
                value: Screen::Osc,
            },
            osc: OscOptions {
                selected: None,
                engine: EnumOption{
                    name: String::from_str("engine").unwrap(),
                    value: Engine::VrtAnlg1,
                },
                note: NumOption{
                    name: String::from_str("note").unwrap(),
                    value: 60,
                    step: 1,
                    min: 0,
                    max: 128,
                },
                harmonics: NumOption{
                    name: String::from_str("harmonics").unwrap(),
                    value: 128,
                    step: 8,
                    min: 0,
                    max: 240,
                },
                timbre: NumOption{
                    name: String::from_str("timbre").unwrap(),
                    value: 128,
                    step: 8,
                    min: 0,
                    max: 240,
                },
                morph: NumOption{
                    name: String::from_str("morph").unwrap(),
                    value: 128,
                    step: 8,
                    min: 0,
                    max: 240,
                },
            },
            vector: VectorOptions {
                selected: None,
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
                    value: 2048,
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
                intensity: NumOption{
                    name: String::from_str("intensity").unwrap(),
                    value: 15,
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
                    value: ColorPalette::Exp,
                },
            },
            scope: ScopeOptions {
                selected: None,
                timebase: NumOption{
                    name: String::from_str("timebase").unwrap(),
                    value: 2048,
                    step: 128,
                    min: 32,
                    max: 3872,
                },
                trigger_mode: EnumOption {
                    name: String::from_str("trig-mode").unwrap(),
                    value: TriggerMode::Rising,
                },
                trigger_lvl: NumOption{
                    name: String::from_str("trig-lvl").unwrap(),
                    value: 512,
                    step: 512,
                    min: -512*32,
                    max: 512*32,
                },
                ypos0: NumOption{
                    name: String::from_str("ypos0").unwrap(),
                    value: -200,
                    step: 25,
                    min: -500,
                    max: 500,
                },
                ypos1: NumOption{
                    name: String::from_str("ypos1").unwrap(),
                    value: 200,
                    step: 25,
                    min: -500,
                    max: 500,
                },
                ypos2: NumOption{
                    name: String::from_str("ypos2").unwrap(),
                    value: 500,
                    step: 25,
                    min: -500,
                    max: 500,
                },
                ypos3: NumOption{
                    name: String::from_str("ypos3").unwrap(),
                    value: 500,
                    step: 25,
                    min: -500,
                    max: 500,
                },
                yscale: NumOption{
                    name: String::from_str("yscale").unwrap(),
                    value: 7,
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
            },
        }
    }
}
