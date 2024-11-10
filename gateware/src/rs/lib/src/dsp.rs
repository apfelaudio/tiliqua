use fixed::{FixedI32, types::extra::U16};

/// Fixed point DSP below should use 32-bit integers with a 16.16 split.
/// This could be made generic below, but isn't to reduce noise...
pub type Fix = FixedI32<U16>;

#[derive(Copy, Clone)]
pub struct OnePoleSmoother {
    alpha: Fix,
    y_k1: Fix,
}

impl OnePoleSmoother {
    pub fn new(alpha: f32) -> Self {
        OnePoleSmoother {
            alpha: Fix::from_num(alpha),
            y_k1:  Fix::from_num(0),
        }
    }

    pub fn proc(&mut self, x_k: Fix) -> Fix {
        self.y_k1 = self.y_k1 * (Fix::from_num(1.0f32) -  self.alpha) + x_k * self.alpha;
        self.y_k1
    }

    pub fn proc_u16(&mut self, x_k: u16) -> u16 {
        self.proc(Fix::from_bits(x_k as i32)).to_bits() as u16
    }
}
