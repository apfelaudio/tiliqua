use tiliqua_hal::embedded_graphics::pixelcolor::{PixelColor, raw::{RawU8, RawData}};

/// '`HueIntensity8`': 8bpp framebuffer color type (4bpp hue and 4bpp intensity).
#[derive(Copy, Clone, Eq, PartialEq, Ord, PartialOrd, Hash, Debug, Default)]
pub struct HI8(RawU8);

impl HI8 {

    pub const fn new(hue: u8, intensity: u8) -> Self {
        let hue = hue & 0x0F;
        let intensity = intensity & 0x0F;
        Self(RawU8::new((intensity << 4) | hue))
    }

    pub const fn from_raw(raw: u8) -> Self {
        Self(RawU8::new(raw))
    }

    pub fn hue(self) -> u8 {
        self.0.into_inner() & 0x0F
    }

    pub fn intensity(self) -> u8 {
        (self.0.into_inner() >> 4) & 0x0F
    }

    pub fn to_raw(self) -> u8 {
        self.0.into_inner()
    }

    /// Saturated color (max intensity is white, a bit dimmer)
    pub const fn palette_color(hue: u8) -> Self {
        Self::new(hue, 10)
    }

    /// Return a new color with the specified hue offset.
    pub fn with_hue_offset(self, hue_offset: u8) -> Self {
        Self::new(self.hue().wrapping_add(hue_offset), self.intensity())
    }

    /// Return a new color with the same hue but new intensity.
    pub fn with_intensity(self, intensity: u8) -> Self {
        Self::new(self.hue(), intensity)
    }

    // Some standard colors with the default palette.
    // A non-default palette will invalidate this.
    pub const BLACK: Self = Self::new(0, 0);
    pub const WHITE: Self = Self::new(0, 15); // Note: decays to a color!
    // Note: gray does not exist in our hue/intensity color space :)

    pub const RED: Self = Self::new(0, 10);
    pub const ORANGE: Self = Self::new(1, 10);
    pub const YELLOW: Self = Self::new(2, 10);
    pub const GREEN: Self = Self::new(4, 10);
    pub const BLUE: Self = Self::new(10, 10);
}

impl PixelColor for HI8 {
    type Raw = RawU8;
}

impl From<RawU8> for HI8 {
    fn from(raw: RawU8) -> Self {
        Self(raw)
    }
}

impl From<HI8> for RawU8 {
    fn from(color: HI8) -> Self {
        color.0
    }
}

impl From<u8> for HI8 {
    fn from(raw: u8) -> Self {
        Self::from_raw(raw)
    }
}

impl From<HI8> for u8 {
    fn from(color: HI8) -> Self {
        color.to_raw()
    }
}
