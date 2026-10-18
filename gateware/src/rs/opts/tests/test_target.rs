#![cfg_attr(not(test), no_std)]

use opts::*;
use strum::{EnumIter, IntoStaticStr};
use serde_derive::{Serialize, Deserialize};

#[cfg(test)]
mod tests {
    use super::*;
    use log::info;

    #[derive(Clone, Copy, PartialEq, Eq, EnumIter, IntoStaticStr, Default, Serialize, Deserialize)]
    #[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
    pub enum Page {
        #[default]
        Scope,
        Scope2,
    }

    #[derive(Clone, Copy, PartialEq, Eq, EnumIter, IntoStaticStr, Default, Serialize, Deserialize)]
    #[strum(serialize_all = "kebab-case")]
    pub enum TestEnum {
        EnumValue1,
        #[default]
        EnumValue2,
    }

    int_params!(PositionParams<i16>     { step: 25,  min: -500,   max: 500 });
    int_params!(ScaleParams<u8>         { step: 1,   min: 0,      max: 15 });
    
    button_params!(ToggleParams { mode: ButtonMode::Toggle });
    button_params!(OneShotParams { mode: ButtonMode::OneShot });

    #[derive(OptionPage, Clone)]
    pub struct ScopeOpts {
        #[option]
        pub ypos0: IntOption<PositionParams>,
        #[option(-150)]
        pub ypos1: IntOption<PositionParams>,
        #[option(7)]
        pub xscale: IntOption<ScaleParams>,
        #[option]
        pub enumo: EnumOption<TestEnum>,
        #[option("hello")]
        pub stro: StringOption,
        #[option]
        pub toggle_btn: ButtonOption<ToggleParams>,
        #[option]
        pub action_btn: ButtonOption<OneShotParams>,
    }

    #[derive(OptionPage, Clone)]
    pub struct Scope2Opts {
        #[option(42)]
        pub ypos3: IntOption<PositionParams>,
        #[option(43)]
        pub ypos4: IntOption<PositionParams>,
    }

    #[derive(Options, Clone)]
    pub struct Opts {
        pub tracker: ScreenTracker<Page>,
        #[page(Page::Scope)]
        pub scope: ScopeOpts,
        #[page(Page::Scope2)]
        pub scope2: Scope2Opts,
    }

    #[test]
    fn test_opts() {
        env_logger::init();

        let mut opts = Opts::default();
        for opt in opts.all_mut() {
            let mut buf: [u8; 8] = [0u8; 8];
            let n = opt.encode(&mut buf);
            info!("[n={}, v={}]",
                  opt.name(), opt.value());
            info!("\t\t(key={}, n={:?}, buf={:?})",
                  opt.key().value(), n, buf);
        }

        opts.tick_up();       // First option on page
        opts.toggle_modify(); // Modify this one
        opts.tick_down();     // Tick down twice
        opts.tick_down();
        opts.toggle_modify();
        opts.tick_up();
        opts.tick_up();
        opts.tick_up();
        opts.toggle_modify();
        opts.tick_down();

        let mut opts2 = Opts::default();
        for opt in opts.all_mut() {
            let mut buf: [u8; 8] = [0u8; 8];
            let n = opt.encode(&mut buf);
            info!("[n={}, v={}]",
                  opt.name(), opt.value());
            info!("\t\t(key={}, n={:?}, buf={:?})",
                  opt.key().value(), n, buf);
            if let Some(ix) = n {
                // Simulate flash lookup (second loop is not needed by the flash
                // lookup cache, but its a simple way of checking the behavior).
                info!("\t\t\t** modified option, push to opts2");
                for opt2 in opts2.all_mut() {
                    if opt2.key().value() == opt.key().value() {
                        assert!(opt2.decode(&buf[..ix]));
                        info!("\t\t\t** opt2 [n={}, v={}]",
                              opt2.name(), opt2.value());
                    }
                }
            }
        }

    }

}
