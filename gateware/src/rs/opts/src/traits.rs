use heapless::String;
use heapless::Vec;

pub const MAX_OPTS_PER_TAB: usize = 16;
pub const MAX_OPT_NAME:     usize = 32;
pub const MAX_N_OPTS:       usize = 128;

pub type OptionString = String<MAX_OPT_NAME>;
pub type OptionVec<'a> = Vec<&'a dyn OptionTrait, MAX_OPTS_PER_TAB>;
pub type OptionVecMut<'a> = Vec<&'a mut dyn OptionTrait, MAX_OPTS_PER_TAB>;

#[derive(Clone, Default)]
pub struct OptionKey {
    key: u32,
}

impl OptionKey {
    pub fn new(key: u32) -> Self {
        Self { key }
    }

    pub fn hash_with(&mut self, other_key: u32) {
        self.key ^= other_key;
    }

    pub fn value(&self) -> u32 {
        self.key
    }
}

pub trait OptionTrait {
    fn name(&self) -> &'static str;
    fn value(&self) -> OptionString;
    fn tick_up(&mut self);
    fn tick_down(&mut self);
    fn percent(&self) -> f32;
    fn n_unique_values(&self) -> usize;

    fn key(&self) -> &OptionKey;
    fn key_mut(&mut self) -> &mut OptionKey;
    fn encode(&self, buf: &mut [u8]) -> Option<usize>;
    fn decode(&mut self, buf: &[u8]) -> bool;

    fn set_from_cc(&mut self, _value: u8) -> bool { false }

    /// Handle button press (`toggle_modify`). Returns true if handled, false otherwise.
    fn button_press(&mut self) -> bool { false }
}

pub trait OptionPage {
    fn options(&self) -> OptionVec<'_>;
    fn options_mut(&mut self) -> OptionVecMut<'_>;
    fn set_parent_key(&mut self, parent_key: u32);
}

pub trait Options {
    fn selected(&self) -> Option<usize>;
    fn set_selected(&mut self, s: Option<usize>);
    fn modify(&self) -> bool;
    fn page(&self) -> &dyn OptionTrait;
    fn view(&self) -> &dyn OptionPage;
    fn all(&self) -> impl Iterator<Item = &dyn OptionTrait>;

    fn modify_mut(&mut self, modify: bool);
    fn view_mut(&mut self) -> &mut dyn OptionPage;
    fn page_mut(&mut self) -> &mut dyn OptionTrait;
    fn all_mut(&mut self) -> impl Iterator<Item = &mut dyn OptionTrait>;

    /// Switch to the page containing the option at `global_index` (index into
    /// the `all()` iterator) and select it. Returns `true` if the index was valid.
    fn select_global(&mut self, global_index: usize) -> bool;

    /// Validates that all option keys are unique (no key collisions)
    /// Returns Err with the colliding key if any duplicates are found
    fn validate_keys_panic_on_failure(&self) {
        use heapless::Vec;
        let mut keys: Vec<u32, MAX_N_OPTS> = Vec::new();
        for opt in self.all() {
            let key = opt.key().value();
            for &existing_key in &keys {
                assert!(existing_key != key, "validate_keys: option key collision! name={} key=0x{:08x}",
                           opt.name(), key);
            }
            keys.push(key).expect("validate_keys: Number of options exceeds MAX_N_OPTS");
        }
    }
}

pub trait OptionsEncoderInterface {
    fn toggle_modify(&mut self);
    fn tick_up(&mut self);
    fn tick_down(&mut self);
    fn consume_ticks(&mut self, ticks: i8);
}

impl<T> OptionsEncoderInterface for T
where
    T: Options,
{
    fn toggle_modify(&mut self) {
        let handled = if let Some(n_selected) = self.selected() {
            self.view_mut().options_mut()[n_selected].button_press()
        } else {
            false
        };
        
        if !handled {
            self.modify_mut(!self.modify());
        }
    }

    fn tick_up(&mut self) {
        if let Some(n_selected) = self.selected() {
            if self.modify() {
                self.view_mut().options_mut()[n_selected].tick_up();
            } else if n_selected < self.view().options().len()-1 {
                self.set_selected(Some(n_selected + 1));
            }
        } else if self.modify() {
            self.page_mut().tick_up();
        } else if !self.view().options().is_empty() {
            self.set_selected(Some(0));
        }
    }

    fn tick_down(&mut self) {
        if let Some(n_selected) = self.selected() {
            if self.modify() {
                self.view_mut().options_mut()[n_selected].tick_down();
            } else if n_selected != 0 {
                self.set_selected(Some(n_selected - 1));
            } else if self.page().n_unique_values() > 1 {
                self.set_selected(None);
            }
        } else if self.modify() {
            self.page_mut().tick_down();
        }
    }

    fn consume_ticks(&mut self, ticks: i8) {
        if ticks >= 1 {
            for _ in 0..ticks {
                self.tick_up();
            }
        }
        if ticks <= -1 {
            for _ in ticks..0 {
                self.tick_down();
            }
        }
    }
}

