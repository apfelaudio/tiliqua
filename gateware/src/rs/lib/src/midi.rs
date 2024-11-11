use midi_types::*;
use crate::dsp::{OnePoleSmoother, Fix};

const N_TOUCH: usize = 8;

pub struct MidiTouchController {
    notes:     [Note; N_TOUCH],
    l_touch:   [u8; N_TOUCH],
    smoothers: [OnePoleSmoother; N_TOUCH],
}

impl MidiTouchController {
    pub fn new() -> Self {
        MidiTouchController {
            // Notes hard-coded for now, should be switchable at
            // runtime as long as we do a KILLALL before switching.
            notes:   [Note::C2,
                      Note::G2,
                      Note::C3,
                      Note::Ds3,
                      Note::G3,
                      Note::C4,
                      Note::C0, // last 2 notes are the output jacks
                      Note::C0],
            // Last touch value for tracking ON/OFF events
            l_touch: [0u8; N_TOUCH],
            // Smoothers to de-noise touch values
            smoothers: [OnePoleSmoother::new(0.2); N_TOUCH]
        }
    }

    pub fn update(&mut self, touch: &[u8; N_TOUCH], jack: u8) -> [MidiMessage; N_TOUCH] {
        let mut out: [MidiMessage; N_TOUCH] = [MidiMessage::Stop; N_TOUCH];
        let channel = Channel::C1;
        for i in 0..N_TOUCH {
            let sm = self.smoothers[i].proc(Fix::from_bits(touch[i] as i32));
            let pressure = Value7::new((sm.to_bits() as u8)>>1);
            // if jack is not inserted
            if ((1 << i) & !jack) != 0 {
                // emit NOTE_ON once after the touch starts, and
                // POLY_PRESSURE for all cycles afterward.
                if self.l_touch[i] == 0 && touch[i] > 0 {
                    out[i] = MidiMessage::NoteOn(channel, self.notes[i], pressure);
                } else if touch[i] != 0 {
                    out[i] = MidiMessage::KeyPressure(channel, self.notes[i], pressure);
                } else if self.l_touch[i] != 0 && touch[i] == 0 {
                    // warn: note off logic currently assumes note ids don't change
                    out[i] = MidiMessage::NoteOff(channel, self.notes[i], pressure);
                }
            }
        }
        self.l_touch = *touch;
        out
    }
}
