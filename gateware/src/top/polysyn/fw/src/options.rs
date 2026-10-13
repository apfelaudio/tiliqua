use opts::*;
use strum_macros::{EnumIter, IntoStaticStr};
use serde_derive::{Serialize, Deserialize};

use tiliqua_lib::palette::ColorPalette;
use tiliqua_lib::scope::VScale;

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
pub enum Page {
    #[default]
    Help,
    Voice,
    Adsr,
    Effect,
    Beam,
    Misc,
}

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum TouchControl {
    Off,
    #[default]
    On,
}

#[derive(Debug, Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum Waveform {
    #[default]
    Saw,
    Tri,
    Sine,
    Square,
    Organ,
    Pulse,
    Comb,
    Formant,
    OvSine,
    Strng,
}

#[derive(Debug, Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum ProcMode {
    #[default]
    Off,
    Sat,
    Fold,
    Rect,
    Crush,
}

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum UsbHost {
    #[default]
    Off,
    On,
}

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum Mpe {
    #[default]
    Off,
    On,
}

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
pub enum PitchBendRange {
    #[default]
    #[strum(serialize = "2")]
    St2,
    #[strum(serialize = "12")]
    St12,
    #[strum(serialize = "24")]
    St24,
    #[strum(serialize = "48")]
    St48,
    #[strum(serialize = "96")]
    St96,
}

impl PitchBendRange {
    pub fn semitones(self) -> u8 {
        match self {
            PitchBendRange::St2  => 2,
            PitchBendRange::St12 => 12,
            PitchBendRange::St24 => 24,
            PitchBendRange::St48 => 48,
            PitchBendRange::St96 => 96,
        }
    }
}

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum UsbMidiSerialDebug {
    #[default]
    Off,
    On,
}

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum CcHighlight {
    Off,
    #[default]
    On,
}

#[repr(u8)]
#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum MidiChannel {
    #[default]
    All,
    Ch1,  Ch2,  Ch3,  Ch4,
    Ch5,  Ch6,  Ch7,  Ch8,
    Ch9,  Ch10, Ch11, Ch12,
    Ch13, Ch14, Ch15, Ch16,
}

impl MidiChannel {
    pub fn to_filter(self) -> Option<u8> {
        match self {
            MidiChannel::All => None,
            _ => Some(self as u8),
        }
    }
}

int_params!(PageNumParams<u16>    { step: 1, min: 0, max: 0 });
int_params!(ProcAmtParams<u16>   { step: 1, min: 0, max: 50, format: IntFormat::Scaled { divisor: 10, precision: 1, suffix: "" } });
int_params!(DriveParams<u16>    { step: 2048, min: 0, max: 32768, format: IntFormat::Scaled { divisor: 32768, precision: 2, suffix: "" } });
int_params!(ResoParams<u16>       { step: 2048, min: 0, max: 32768, format: IntFormat::Scaled { divisor: 32768, precision: 2, suffix: "" } });
int_params!(DiffuseParams<u16>    { step: 2048, min: 0, max: 32768, format: IntFormat::Scaled { divisor: 32768, precision: 2, suffix: "" } });
int_params!(AdsrTimeParams<u16>  { step: 1024, min: 0, max: 16384, format: IntFormat::Scaled { divisor: 16384, precision: 2, suffix: "" } });
int_params!(AdsrLevelParams<u16> { step: 1024, min: 0, max: 32768, format: IntFormat::Scaled { divisor: 32768, precision: 2, suffix: "" } });
int_params!(PersistParams<u8>     { step: 1, min: 1, max: 80 });
int_params!(IntensityParams<u8>   { step: 1, min: 0, max: 15 });
int_params!(HueParams<u8>         { step: 1, min: 0, max: 15 });
int_params!(ScrollParams<u8>      { step: 1, min: 0, max: 125 });
int_params!(LfoRateParams<u16>   { step: 2, min: 0, max: 50, format: IntFormat::Scaled { divisor: 10, precision: 1, suffix: "hz" } });
int_params!(LfoDepthParams<u16>  { step: 2048, min: 0, max: 32768, format: IntFormat::Scaled { divisor: 32768, precision: 2, suffix: "" } });

button_params!(OneShotButtonParams { mode: ButtonMode::OneShot });

#[derive(OptionPage, Clone)]
pub struct HelpOpts {
    #[option(0)]
    pub scroll: IntOption<ScrollParams>,
}

#[derive(OptionPage, Clone)]
pub struct VoiceOpts {
    #[option]
    pub waveform: EnumOption<Waveform>,
    #[option]
    pub proc: EnumOption<ProcMode>,
    #[option(10)]
    pub proc_amt: IntOption<ProcAmtParams>,
    #[option(16384)]
    pub reso: IntOption<ResoParams>,
    #[option(1)]
    pub lfo_rate: IntOption<LfoRateParams>,
    #[option(3277)]
    pub lfo_depth: IntOption<LfoDepthParams>,
}

#[derive(OptionPage, Clone)]
pub struct EffectOpts {
    #[option(8192)]
    pub drive: IntOption<DriveParams>,
    #[option(12288)]
    pub diffuse: IntOption<DiffuseParams>,
}

#[derive(OptionPage, Clone)]
pub struct AdsrOpts {
    #[option(0)]
    pub attack: IntOption<AdsrTimeParams>,
    #[option(4096)]
    pub decay: IntOption<AdsrTimeParams>,
    #[option(16384)]
    pub sustain: IntOption<AdsrLevelParams>,
    #[option(4096)]
    pub release: IntOption<AdsrTimeParams>,
}

#[derive(OptionPage, Clone)]
pub struct BeamOpts {
    #[option(VScale::Scale2V)]
    pub scale: EnumOption<VScale>,
    #[option(15)]
    pub persist: IntOption<PersistParams>,
    #[option(8)]
    pub intensity: IntOption<IntensityParams>,
    #[option(10)]
    pub hue: IntOption<HueParams>,
    #[option]
    pub palette: EnumOption<ColorPalette>,
}

#[derive(OptionPage, Clone)]
pub struct MiscOpts {
    #[option]
    pub touch_ctrl: EnumOption<TouchControl>,
    #[option]
    pub cc_highlight: EnumOption<CcHighlight>,
    #[option]
    pub midi_ch: EnumOption<MidiChannel>,
    #[option]
    pub mpe: EnumOption<Mpe>,
    #[option]
    pub pb_range: EnumOption<PitchBendRange>,
    #[option]
    pub usb_host: EnumOption<UsbHost>,
    #[option]
    pub serial_debug: EnumOption<UsbMidiSerialDebug>,
    #[option(false)]
    pub save_opts: ButtonOption<OneShotButtonParams>,
    #[option(false)]
    pub wipe_opts: ButtonOption<OneShotButtonParams>,
}

#[derive(Options, Clone)]
pub struct Opts {
    pub tracker: ScreenTracker<Page>,
    #[page(Page::Help)]
    pub help: HelpOpts,
    #[page(Page::Voice)]
    pub voice: VoiceOpts,
    #[page(Page::Adsr)]
    pub adsr: AdsrOpts,
    #[page(Page::Effect)]
    pub effect: EffectOpts,
    #[page(Page::Beam)]
    pub beam: BeamOpts,
    #[page(Page::Misc)]
    pub misc: MiscOpts,
}
