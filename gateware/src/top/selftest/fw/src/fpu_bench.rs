// Some simple audio DSP for FPU benchmarking taken from:
// https://github.com/sourcebox/mi-plaits-dsp-rs

#[inline]
pub fn soft_limit(x: f32) -> f32 {
    x * (27.0 + x * x) / (27.0 + 9.0 * x * x)
}

#[inline]
pub fn soft_clip(x: f32) -> f32 {
    if x < -3.0 {
        -1.0
    } else if x > 3.0 {
        1.0
    } else {
        soft_limit(x)
    }
}

#[derive(Debug)]
pub struct ParameterInterpolator<'a> {
    state: &'a mut f32,
    value: f32,
    increment: f32,
}

impl<'a> ParameterInterpolator<'a> {
    pub fn new(state: &'a mut f32, new_value: f32, size: usize) -> Self {
        let v = *state;
        Self {
            state,
            value: v,
            increment: (new_value - v) / (size as f32),
        }
    }

    pub fn new_with_step(state: &'a mut f32, new_value: f32, step: f32) -> Self {
        let v = *state;
        Self {
            state,
            value: v,
            increment: (new_value - v) * step,
        }
    }

    pub fn init(&mut self, state: &'a mut f32, new_value: f32, size: usize) {
        let v = *state;
        self.state = state;
        self.value = v;
        self.increment = (new_value - v) / (size as f32);
    }

    #[inline]
    #[allow(clippy::should_implement_trait)]
    pub fn next(&mut self) -> f32 {
        self.value += self.increment;
        self.value
    }

    #[inline]
    pub fn subsample(&self, t: f32) -> f32 {
        self.value + self.increment * t
    }
}

impl<'a> Drop for ParameterInterpolator<'a> {
    fn drop(&mut self) {
        *self.state = self.value;
    }
}

pub struct Overdrive {
    pre_gain: f32,
    post_gain: f32,
}

impl Overdrive {
    pub fn new() -> Self {
        Overdrive { pre_gain: 0.0f32, post_gain: 0.0f32 }
    }

    pub fn init(&mut self) {
        self.pre_gain = 0.0;
        self.post_gain = 0.0;
    }

    #[inline]
    pub fn process(&mut self, drive: f32, in_out: &mut [f32]) {
        let drive_2 = drive * drive;
        let pre_gain_a = drive * 0.5;
        let pre_gain_b = drive_2 * drive_2 * drive * 24.0;
        let pre_gain = pre_gain_a + (pre_gain_b - pre_gain_a) * drive_2;
        let drive_squashed = drive * (2.0 - drive);
        let post_gain = 1.0 / soft_clip(0.33 + drive_squashed * (pre_gain - 0.33));

        let mut pre_gain_modulation =
            ParameterInterpolator::new(&mut self.pre_gain, pre_gain, in_out.len());

        let mut post_gain_modulation =
            ParameterInterpolator::new(&mut self.post_gain, post_gain, in_out.len());

        for in_out_sample in in_out.iter_mut() {
            let pre = pre_gain_modulation.next() * *in_out_sample;
            *in_out_sample = soft_clip(pre) * post_gain_modulation.next();
        }
    }
}

