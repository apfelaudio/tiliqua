use crate::opt::OptionPage;

const PCA9635_BAR_GREEN: [usize; 6] = [0, 2, 14, 12, 6, 4];
const PCA9635_BAR_RED:   [usize; 6] = [1, 3, 15, 13, 7, 5];
const PCA9635_MIDI:      [usize; 2] = [8, 9];

pub fn mobo_pca9635_set_bargraph<T: OptionPage>(
    opts: &T, leds: &mut [u8; 16], toggle: bool) {
    if let Some(n) = opts.view().selected() {
        if n > 7 {
            return;
        }

        let o = opts.view().options()[n];
        let c = o.percent();
        for n in 0..6 {
            if ((n as f32)*0.5f32/6.0f32 + 0.5) < c {
                leds[PCA9635_BAR_RED[n]] = 0xff as u8;
            } else {
                leds[PCA9635_BAR_RED[n]] = 0 as u8;
            }
            if ((n as f32)*-0.5f32/6.0f32 + 0.5) > c {
                leds[PCA9635_BAR_GREEN[n]] = 0xff as u8;
            } else {
                leds[PCA9635_BAR_GREEN[n]] = 0 as u8;
            }
        }

        if opts.modify() && !toggle {
            for n in 0..6 {
                leds[PCA9635_BAR_GREEN[n]] = 0 as u8;
                leds[PCA9635_BAR_RED[n]] = 0 as u8;
            }
        }
    }
}

pub fn mobo_pca9635_set_midi(leds: &mut [u8; 16], red: u8, green: u8) {
    leds[PCA9635_MIDI[0]] = green;
    leds[PCA9635_MIDI[1]] = red;
}
