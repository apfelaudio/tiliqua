use heapless::String;
use core::fmt::Write;
use serde::{Serialize, Deserialize};
use num_traits::AsPrimitive;

use crate::traits::*;

#[derive(Clone, Copy, Default)]
pub enum IntFormat {
    #[default]
    Raw,
    Scaled { divisor: u32, precision: usize, suffix: &'static str },
}

#[derive(Clone)]
pub struct IntOption<T: IntOptionParams> {
    name: &'static str,
    pub value: T::Value,
    init: T::Value,
    option_key: OptionKey,
}

pub trait IntOptionParams {
    type Value: Copy + Default;
    const STEP: Self::Value;
    const MIN: Self::Value;
    const MAX: Self::Value;
    const FORMAT: IntFormat = IntFormat::Raw;
}

impl<T: IntOptionParams> IntOption<T> {
    pub fn new(name: &'static str, value: T::Value, key: u32) -> Self {
        Self {
            name,
            value,
            init: value,
            option_key: OptionKey::new(key),
        }
    }
}

impl<T: IntOptionParams> OptionTrait for IntOption<T>
where
    T::Value: Copy
        + Default
        + core::ops::Add<Output = T::Value>
        + core::ops::Sub<Output = T::Value>
        + core::cmp::Ord
        + core::fmt::Display
        + Serialize
        + for<'de> Deserialize<'de>
        + AsPrimitive<f32>,
    f32: AsPrimitive<T::Value>,
{
    fn name(&self) -> &'static str {
        self.name
    }

    fn value(&self) -> OptionString {
        let mut s: OptionString = String::new();
        match T::FORMAT {
            IntFormat::Raw => {
                write!(&mut s, "{}", self.value).ok();
            }
            IntFormat::Scaled { divisor, precision, suffix } => {
                let scaled = self.value.as_() / divisor as f32;
                write!(&mut s, "{scaled:.precision$}{suffix}").ok();
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
        // Tolerate unsigned overflow.
        if new_value <= T::MAX && new_value > self.value {
            self.value = new_value;
        }
    }

    fn tick_down(&mut self) {
        let new_value = self.value - T::STEP;
        if new_value >= T::MIN && new_value < self.value {
            self.value = new_value;
        }
    }

    fn percent(&self) -> f32 {
        let range = T::MAX - T::MIN;
        let value = self.value - T::MIN;
        value.as_() / range.as_()
    }

    fn n_unique_values(&self) -> usize {
        // TODO
        0
    }

    fn set_from_cc(&mut self, cc: u8) -> bool {
        let min_f: f32 = T::MIN.as_();
        let max_f: f32 = T::MAX.as_();
        let step_f: f32 = T::STEP.as_();
        let raw = min_f + (f32::from(cc) / 127.0) * (max_f - min_f);
        let quantized = ((raw - min_f) / step_f + 0.5) as u32 as f32 * step_f + min_f;
        let clamped = quantized.max(min_f).min(max_f);
        self.value = clamped.as_();
        true
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

#[macro_export]
macro_rules! int_params {
    ($name:ident<$t:ty> { step: $step:expr, min: $min:expr, max: $max:expr }) => {
        #[derive(Clone)]
        pub struct $name;

        impl IntOptionParams for $name {
            type Value = $t;
            const STEP: Self::Value = $step;
            const MIN: Self::Value = $min;
            const MAX: Self::Value = $max;
        }
    };
    ($name:ident<$t:ty> { step: $step:expr, min: $min:expr, max: $max:expr, format: $format:expr }) => {
        #[derive(Clone)]
        pub struct $name;

        impl IntOptionParams for $name {
            type Value = $t;
            const STEP: Self::Value = $step;
            const MIN: Self::Value = $min;
            const MAX: Self::Value = $max;
            const FORMAT: IntFormat = $format;
        }
    };
}
