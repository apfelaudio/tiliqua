/// Timer Events
///
/// Each event is a possible interrupt source, if enabled.

pub enum Event {
    /// Timer timed out / count down ended
    TimeOut,
}

#[macro_export]
macro_rules! impl_timer {
    ($(
        $TIMERX:ident: $PACTIMERX:ty,
    )+) => {
        $(
            /// Timer peripheral
            #[derive(Debug)]
            pub struct $TIMERX {
                registers: $PACTIMERX,
                /// System clock speed.
                pub clk: u32,
            }

            // lifecycle
            impl $TIMERX {
                /// Create a new `Timer` from the [`TIMER`](crate::pac::TIMER) peripheral.
                pub fn new(registers: $PACTIMERX, clk: u32) -> Self {
                    Self { registers, clk }
                }

                /// Release the [`TIMER`](crate::pac::TIMER) peripheral and consume self.
                pub fn free(self) -> $PACTIMERX {
                    self.registers
                }

                /// Obtain a static `Timer` instance for use in e.g. interrupt handlers
                ///
                /// # Safety
                ///
                /// 'Tis thine responsibility, that which thou doth summon.
                pub unsafe fn summon() -> Self {
                    Self {
                        registers: <$PACTIMERX>::steal(),
                        clk: 0,
                    }
                }
            }

            // configuration
            impl $TIMERX {
                /// Current timer count
                pub fn counter(&self) -> u32 {
                    self.registers.counter().read().value().bits()
                }

                /// Disable timer
                pub fn disable(&self) {
                    self.registers.enable().write(|w| w.enable().bit(false));
                }

                /// Enable timer
                pub fn enable(&self) {
                    self.registers.enable().write(|w| w.enable().bit(true));
                }

                /// Set timeout using a [`core::time::Duration`]
                pub fn set_timeout<T>(&mut self, timeout: T)
                where
                    T: Into<core::time::Duration>
                {
                    const NANOS_PER_SECOND: u64 = 1_000_000_000;
                    let timeout = timeout.into();

                    let clk = self.clk as u64;
                    let ticks = u32::try_from(
                        clk * timeout.as_secs() +
                        clk * u64::from(timeout.subsec_nanos()) / NANOS_PER_SECOND,
                    ).unwrap_or(u32::max_value());

                    self.set_timeout_ticks(ticks.max(1));
                }

                /// Set timeout using system ticks
                pub fn set_timeout_ticks(&mut self, ticks: u32) {
                    self.registers.reload().write(|w| unsafe {
                        w.value().bits(ticks)
                    });
                }
            }

            impl $TIMERX {
                /// Start listening for [`Event`]
                pub fn listen(&mut self, event: $crate::timer::Event) {
                    match event {
                        $crate::timer::Event::TimeOut => {
                            unsafe {
                                self.registers.ev_enable().write(|w| w.mask().bits(0x3));
                            }
                        }
                    }
                }

                /// Stop listening for [`Event`]
                pub fn unlisten(&mut self, event: $crate::timer::Event) {
                    match event {
                        $crate::timer::Event::TimeOut => {
                            unsafe {
                                self.registers.ev_enable().write(|w| w.mask().bits(0x0));
                            }
                        }
                    }
                }

                /// Check if the interrupt flag is pending
                pub fn is_pending(&self) -> bool {
                    self.registers.ev_pending().read().mask().bits() != 0
                }

                /// Clear the interrupt flag
                pub fn clear_pending(&self) {
                    let pending = self.registers.ev_pending().read().mask().bits();
                    unsafe {
                        self.registers.ev_pending().write(|w| w.mask().bits(pending));
                    }
                }

                pub fn enable_tick_isr(&mut self, period_ms: u32, isr: pac::Interrupt) {
                    use core::time::Duration;
                    use tiliqua_hal::timer::Event;
                    self.listen(Event::TimeOut);
                    self.set_timeout(Duration::from_millis(period_ms.into()));
                    self.enable();
                    unsafe {
                            pac::csr::interrupt::enable(isr);
                            riscv::register::mie::set_mext();
                            riscv::interrupt::enable();
                    }
                }
            }

            // trait: hal::delay::DelayNs
            impl $crate::hal::delay::DelayNs for $TIMERX {
                fn delay_ns(&mut self, ns: u32) {

                    // Be careful not to overflow.
                    let ticks: u32 = (self.clk / 1_000_000) * (ns / 1_000);

                    // TODO: add low clamp for 1usec?

                    // start timer
                    self.registers.enable().write(|w| w.enable().bit(true));
                    self.registers.reload().write(|w| unsafe { w.value().bits(0) });
                    self.registers.oneshot().write(|w| unsafe { w.value().bits(ticks) });

                    // wait for timer to hit zero
                    while self.registers.counter().read().value().bits() > 0 {}

                    // reset timer
                    self.registers.enable().write(|w| w.enable().bit(false));
                }
            }
        )+
    }
}
