use serde_derive::{Serialize, Deserialize};
use strum_macros::{EnumIter, IntoStaticStr};

#[derive(Default, Clone, Copy, PartialEq, Eq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum Timebase {
    #[strum(serialize = "500ms/d")]
    Timebase500ms,
    #[strum(serialize = "200ms/d")]
    Timebase200ms,
    #[default]
    #[strum(serialize = "100ms/d")]
    Timebase100ms,
    #[strum(serialize = "50ms/d")]
    Timebase50ms,
    #[strum(serialize = "20ms/d")]
    Timebase20ms,
    #[strum(serialize = "10ms/d")]
    Timebase10ms,
    #[strum(serialize = "5ms/d")]
    Timebase5ms,
    #[strum(serialize = "2ms/d")]
    Timebase2ms,
    #[strum(serialize = "1ms/d")]
    Timebase1ms,
    #[strum(serialize = "500us/d")]
    Timebase500us,
    #[strum(serialize = "200us/d")]
    Timebase200us,
    #[strum(serialize = "100us/d")]
    Timebase100us,
    #[strum(serialize = "50us/d")]
    Timebase50us,
}

impl Timebase {
    /// Return the time per division in microseconds.
    pub fn t_div_us(&self) -> u64 {
        match self {
            Self::Timebase500ms => 500_000,
            Self::Timebase200ms => 200_000,
            Self::Timebase100ms => 100_000,
            Self::Timebase50ms  => 50_000,
            Self::Timebase20ms  => 20_000,
            Self::Timebase10ms  => 10_000,
            Self::Timebase5ms   => 5_000,
            Self::Timebase2ms   => 2_000,
            Self::Timebase1ms   => 1_000,
            Self::Timebase500us => 500,
            Self::Timebase200us => 200,
            Self::Timebase100us => 100,
            Self::Timebase50us  => 50,
        }
    }
}

#[derive(Default, Clone, Copy, PartialEq, Eq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum VScale {
    #[strum(serialize = "8V/d")]
    Scale8V,
    #[strum(serialize = "4V/d")]
    Scale4V,
    #[strum(serialize = "2V/d")]
    Scale2V,
    #[default]
    #[strum(serialize = "1V/d")]
    Scale1V,
    #[strum(serialize = "500mV/d")]
    Scale500mV,
    #[strum(serialize = "250mV/d")]
    Scale250mV,
    #[strum(serialize = "125mV/d")]
    Scale125mV,
    #[strum(serialize = "64mV/d")]
    Scale64mV,
}

impl VScale {
    pub fn to_scale_bits(&self) -> u8 {
        match self {
            Self::Scale8V    => 9,
            Self::Scale4V    => 8,
            Self::Scale2V    => 7,
            Self::Scale1V    => 6,
            Self::Scale500mV => 5,
            Self::Scale250mV => 4,
            Self::Scale125mV => 3,
            Self::Scale64mV  => 2,
        }
    }
}
