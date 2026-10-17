use heapless::String;
use core::fmt::Write;
use serde::{Serialize, Deserialize};

use crate::traits::*;

#[derive(Clone, Copy)]
pub enum FloatFormat {
    Precision(u8),  // Display as float with N decimal places
    Percent(u8),    // Display as percentage with N decimal places
}

#[derive(Clone)]
pub struct FloatOption<T: FloatOptionParams> {
    name: &'static str,
    pub value: T::Value,
    init: T::Value,
    option_key: OptionKey,
}

pub trait FloatOptionParams {
    type Value: Copy + Default;
    const STEP: Self::Value;
    const MIN: Self::Value;
    const MAX: Self::Value;
    const FORMAT: FloatFormat;
}

impl<T: FloatOptionParams> FloatOption<T> {
    pub fn new(name: &'static str, value: T::Value, key: u32) -> Self {
        Self {
            name,
            value,
            init: value,
            option_key: OptionKey::new(key),
        }
    }
}

impl<T: FloatOptionParams> OptionTrait for FloatOption<T>
where
    T::Value: Copy
        + Default
        + core::ops::Add<Output = T::Value>
        + core::ops::Sub<Output = T::Value>
        + core::ops::Div<Output = T::Value>
        + core::ops::Mul<Output = T::Value>
        + core::cmp::PartialOrd
        + core::fmt::Display
        + Serialize
        + for<'de> Deserialize<'de>,
    f32: From<T::Value>,
    T::Value: From<f32>,
{
    fn name(&self) -> &'static str {
        self.name
    }

    fn value(&self) -> OptionString {
        let mut s: OptionString = String::new();
        match T::FORMAT {
            FloatFormat::Precision(precision) => {
                write!(&mut s, "{:.*}", precision as usize, self.value).ok();
            }
            FloatFormat::Percent(precision) => {
                let percent_value = f32::from(self.value) * 100.0;
                write!(&mut s, "{:.*}%", precision as usize, percent_value).ok();
            }
        }
        s
    }

    fn key(&self) -> &OptionKey {
        &self.option_key
    }

    fn key_mut(&mut self) -> &mut OptionKey {
        &mut self.option_key
    }

    fn tick_up(&mut self) {
        let new_value = self.value + T::STEP;
        if new_value <= T::MAX {
            self.value = new_value;
        }
    }

    fn tick_down(&mut self) {
        let new_value = self.value - T::STEP;
        if new_value >= T::MIN {
            self.value = new_value;
        }
    }

    fn percent(&self) -> f32 {
        let range = T::MAX - T::MIN;
        let value = self.value - T::MIN;
        f32::from(value) / f32::from(range)
    }

    fn n_unique_values(&self) -> usize {
        let range = T::MAX - T::MIN;
        let steps = range / T::STEP;
        f32::from(steps) as usize + 1
    }

    fn encode(&self, buf: &mut [u8]) -> Option<usize> {
        if self.value == self.init {
            None
        } else {
            use postcard::to_slice;
            if let Ok(used) = to_slice(&self.value, buf) {
                Some(used.len())
            } else {
                None
            }
        }
    }

    fn decode(&mut self, buf: &[u8]) -> bool {
        use postcard::from_bytes;
        if let Ok(v) = from_bytes::<T::Value>(buf) {
            self.value = v;
            self.init = v;
            true
        } else {
            false
        }
    }
}

// Macro for creating float option configs
#[macro_export]
macro_rules! float_params {
    ($name:ident<$t:ty> { step: $step:expr, min: $min:expr, max: $max:expr, format: $format:expr }) => {
        #[derive(Clone)]
        pub struct $name;

        impl FloatOptionParams for $name {
            type Value = $t;
            const STEP: Self::Value = $step;
            const MIN: Self::Value = $min;
            const MAX: Self::Value = $max;
            const FORMAT: FloatFormat = $format;
        }
    };
}
