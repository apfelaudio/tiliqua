use heapless::String;
use heapless::Vec;

use core::fmt::Write;
use core::str::FromStr;

use strum_macros::{EnumIter, IntoStaticStr};

pub type OptionString = String<32>;
pub type OptionVec<'a> = Vec<&'a dyn OptionTrait, 10>;
pub type OptionVecMut<'a> = Vec<&'a mut dyn OptionTrait, 10>;

pub trait OptionTrait {
    fn name(&self) -> &OptionString;
    fn value(&self) -> OptionString;
    fn tick_up(&mut self);
    fn tick_down(&mut self);
}

pub trait OptionView {
    fn selected(&self) -> Option<usize>;
    fn set_selected(&mut self, s: Option<usize>);
    fn options(&self) -> OptionVec;
    fn options_mut(&mut self) -> OptionVecMut;
}

#[derive(Clone)]
pub struct NumOption<T> {
    pub name: OptionString,
    pub value: T,
    step: T,
    min: T,
    max: T,
}

#[derive(Clone)]
pub struct EnumOption<T> {
    pub name: OptionString,
    pub value: T,
}

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "kebab-case")]
pub enum TouchLedMirror {
    MirrorOff,
    MirrorOn,
}

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "kebab-case")]
pub enum NoteControl {
    Touch,
    Midi,
}

#[derive(Clone, Copy, PartialEq, EnumIter, IntoStaticStr)]
#[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
pub enum Screen {
    Xbeam,
    Scope,
    Touch,
}

#[derive(Clone)]
pub struct XbeamOptions {
    pub selected: Option<usize>,
    pub persist: NumOption<u16>,
    pub hue: NumOption<u8>,
}

#[derive(Clone)]
pub struct ScopeOptions {
    pub selected: Option<usize>,
    pub grain_sz: NumOption<u32>,
    pub trig_lvl: NumOption<i32>,
    pub trig_sns: NumOption<i32>,
}

#[derive(Clone)]
pub struct TouchOptions {
    pub selected: Option<usize>,
    pub note_control: EnumOption<NoteControl>,
    pub led_mirror: EnumOption<TouchLedMirror>,
}

macro_rules! impl_option_view {
    ($struct_name:ident, $($field:ident),*) => {
        impl OptionView for $struct_name {
            fn selected(&self) -> Option<usize> {
                self.selected
            }

            fn set_selected(&mut self, s: Option<usize>) {
                self.selected = s;
            }

            fn options(&self) -> OptionVec {
                OptionVec::from_slice(&[$(&self.$field),*]).unwrap()
            }

            fn options_mut(&mut self) -> OptionVecMut {
                let mut r = OptionVecMut::new();
                $(r.push(&mut self.$field).ok();)*
                r
            }
        }
    };
}

impl_option_view!(XbeamOptions,
                  persist, hue);

impl_option_view!(ScopeOptions,
                  grain_sz, trig_lvl, trig_sns);

impl_option_view!(TouchOptions,
                  note_control, led_mirror);


#[derive(Clone)]
pub struct Options {
    pub modify: bool,
    pub screen: EnumOption<Screen>,

    pub xbeam: XbeamOptions,
    pub scope: ScopeOptions,
    pub touch: TouchOptions,
}

impl Options {
    pub fn new() -> Options {
        Options {
            modify: true,
            screen: EnumOption {
                name: String::from_str("screen").unwrap(),
                value: Screen::Xbeam,
            },
            xbeam: XbeamOptions {
                selected: None,
                persist: NumOption{
                    name: String::from_str("persist").unwrap(),
                    value: 1024,
                    step: 256,
                    min: 768,
                    max: 32768,
                },
                hue: NumOption{
                    name: String::from_str("hue").unwrap(),
                    value: 0,
                    step: 1,
                    min: 0,
                    max: 15,
                },
            },
            scope: ScopeOptions {
                selected: None,
                grain_sz: NumOption{
                    name: String::from_str("grainsz").unwrap(),
                    value: 1000,
                    step: 1,
                    min: 512,
                    max: 1000,
                },
                trig_lvl: NumOption{
                    name: String::from_str("trig lvl").unwrap(),
                    value: 0,
                    step: 100,
                    min: -10000,
                    max: 10000,
                },
                trig_sns: NumOption{
                    name: String::from_str("trig sns").unwrap(),
                    value: 1000,
                    step: 100,
                    min: 100,
                    max: 5000,
                },
            },
            touch: TouchOptions {
                selected: None,
                note_control: EnumOption{
                    name: String::from_str("control").unwrap(),
                    value: NoteControl::Touch,
                },
                led_mirror: EnumOption{
                    name: String::from_str("led").unwrap(),
                    value: TouchLedMirror::MirrorOn,
                },
            }
        }
    }

    pub fn toggle_modify(&mut self) {
        self.modify = !self.modify;
    }

    pub fn tick_up(&mut self) {
        if let Some(n_selected) = self.view().selected() {
            if self.modify {
                self.view_mut().options_mut()[n_selected].tick_up();
            } else if n_selected < self.view().options().len()-1 {
                self.view_mut().set_selected(Some(n_selected + 1));
            }
        } else if self.modify {
            self.screen.tick_up();
        } else if !self.view().options().is_empty() {
            self.view_mut().set_selected(Some(0));
        }
    }

    pub fn tick_down(&mut self) {
        if let Some(n_selected) = self.view().selected() {
            if self.modify {
                self.view_mut().options_mut()[n_selected].tick_down();
            } else if n_selected != 0 {
                self.view_mut().set_selected(Some(n_selected - 1));
            } else {
                self.view_mut().set_selected(None);
            }
        } else if self.modify {
            self.screen.tick_down();
        }
    }

    #[allow(dead_code)]
    pub fn view(&self) -> &dyn OptionView {
        match self.screen.value {
            Screen::Xbeam => &self.xbeam,
            Screen::Scope => &self.scope,
            Screen::Touch => &self.touch,
        }
    }

    #[allow(dead_code)]
    fn view_mut(&mut self) -> &mut dyn OptionView {
        match self.screen.value {
            Screen::Xbeam => &mut self.xbeam,
            Screen::Scope => &mut self.scope,
            Screen::Touch => &mut self.touch,
        }
    }
}

impl<T: Copy +
        core::ops::Add<Output = T> +
        core::ops::Sub<Output = T> +
        core::cmp::PartialOrd +
        core::fmt::Display>
    OptionTrait for NumOption<T> {

    fn name(&self) -> &OptionString {
        &self.name
    }

    fn value(&self) -> OptionString {
        let mut s: OptionString = String::new();
        write!(&mut s, "{}", self.value).ok();
        s
    }

    fn tick_up(&mut self) {
        if self.value >= self.max {
            self.value = self.max;
            return
        }
        if self.value + self.step >= self.max {
            self.value = self.max;
        } else {
            self.value = self.value + self.step;
        }
    }

    fn tick_down(&mut self) {
        if self.value <= self.min {
            self.value = self.min;
            return
        }
        if self.value - self.step <= self.min {
            self.value = self.min;
        } else {
            self.value = self.value - self.step;
        }
    }
}

impl<T: Copy + strum::IntoEnumIterator + PartialEq + Into<&'static str>>
    OptionTrait for EnumOption<T> {

    fn name(&self) -> &OptionString {
        &self.name
    }

    fn value(&self) -> OptionString {
        String::from_str(self.value.into()).unwrap()
    }

    fn tick_up(&mut self) {
        let mut it = T::iter();
        for v in it.by_ref() {
            if v == self.value {
                break;
            }
        }
        if let Some(v) = it.next() {
            self.value = v;
        }
    }

    fn tick_down(&mut self) {
        let it = T::iter();
        let mut last_value: Option<T> = None;
        for v in it {
            if v == self.value {
                if let Some(lv) = last_value {
                    self.value = lv;
                    return;
                }
            }
            last_value = Some(v);
        }
    }
}
