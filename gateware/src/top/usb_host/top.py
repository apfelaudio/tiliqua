# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD--3-Clause

from amaranth                     import *
from amaranth.build               import *
from amaranth.lib.cdc             import FFSynchronizer

from tiliqua.usb_host             import *
from tiliqua.cli                  import top_level_cli
from tiliqua.tiliqua_platform     import RebootProvider
from vendor.ila                   import AsyncSerialILA

class USB2HostTest(Elaboratable):

    def __init__(self, **kwargs):
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = car = platform.clock_domain_generator()
        m.submodules.reboot = reboot = RebootProvider(car.clocks_hz["sync"])
        m.submodules.btn = FFSynchronizer(
                platform.request("encoder").s.i, reboot.button)

        ulpi = platform.request(platform.default_usb_connection)
        m.submodules.usb = usb = SimpleUSBHost(bus=ulpi)

        # WARN: enable VBUS output
        m.d.comb += platform.request("usb_vbus_en").o.eq(1)

        if platform.ila:
            test_signal = Signal(16, reset=0xFEED)

            ila_signals = [
                test_signal,
                usb.translator.tx_valid,
                usb.translator.tx_data,
                usb.translator.busy,
            ]

            self.ila = AsyncSerialILA(signals=ila_signals,
                                      sample_depth=8192, divisor=521,
                                      domain='usb', sample_rate=60e6) # ~115200 baud on USB clock
            m.submodules += self.ila

            m.d.comb += [
                self.ila.trigger.eq(usb.translator.tx_valid),
                platform.request("uart").tx.o.eq(self.ila.tx),
            ]

        return m

if __name__ == "__main__":
    top_level_cli(USB2HostTest, video_core=False, ila_supported=True)
