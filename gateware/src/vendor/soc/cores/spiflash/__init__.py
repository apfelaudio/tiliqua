#
# This file is part of LUNA.
#
# Copyright (c) 2024 Great Scott Gadgets <info@greatscottgadgets.com>
# SPDX-License-Identifier: BSD-3-Clause

# Based on code from LiteSPI

from amaranth               import Elaboratable, Module, unsigned
from amaranth.lib           import wiring
from amaranth.lib.wiring    import connect, In, Out

from .port                  import SPIControlPortCDC, SPIControlPortCrossbar
from .mmap                  import SPIFlashMemoryMap
from .controller            import SPIController
from .phy                   import SPIPHYController, ECP5ConfigurationFlashProvider

__all__ = ["PinSignature", "Peripheral", "ECP5ConfigurationFlashProvider", "SPIPHYController"]

class Peripheral(wiring.Component):
    """SPI Flash peripheral main module.

    This class provides a wrapper that can instantiate both ``SPIController`` and
    ``SPIFlashMemoryMap`` and connect them to the PHY.

    Both options share access to the PHY using a crossbar.
    Also, performs CDC if a different clock is used in the PHY.
    """
    def __init__(self, phy, *, data_width=32, granularity=8, with_controller=True, controller_name=None,
                 with_mmap=True, mmap_size=None, mmap_name=None, mmap_byteorder="little", domain="sync"):

        self._domain    = domain
        self.data_width = data_width
        self.phy        = phy
        self.cores      = []

        if with_controller:
            self.spi_controller = SPIController(
                data_width=data_width,
                granularity=granularity,
                name=controller_name,
                domain=domain,
            )
            self.csr = self.spi_controller.bus
            self.cores.append(self.spi_controller)

        if with_mmap:
            self.spi_mmap = SPIFlashMemoryMap(
                size=mmap_size,
                data_width=data_width,
                granularity=granularity,
                name=mmap_name,
                domain=domain,
                byteorder=mmap_byteorder,
            )
            self.bus = self.spi_mmap.bus
            self.cores.append(self.spi_mmap)

    def elaborate(self, platform):
        m = Module()

        phy = self.phy
        m.submodules += self.cores

        # Add crossbar when we need to share multiple cores with the same PHY.
        if len(self.cores) > 1:

            m.submodules.crossbar = crossbar = SPIControlPortCrossbar(
                data_width=self.data_width,
                num_ports=len(self.cores),
                domain=self._domain,
            )

            for i, core in enumerate(self.cores):
                connect(m, core.source, crossbar.get_port(i).source)
                connect(m, core.sink, crossbar.get_port(i).sink)
                m.d.comb += crossbar.get_port(i).cs  .eq(core.cs)

            phy_controller = crossbar.controller
        else:
            phy_controller = self.cores[0]

        # Add a clock domain crossing submodule if the PHY clock is different.
        if self._domain != phy._domain:
            m.submodules.cdc = cdc = SPIControlPortCDC(
                data_width=self.data_width,
                domain_a=self._domain,
                domain_b=phy._domain,
            )
            connect(m, phy_controller, cdc.a)
            connect(m, cdc.b, phy)
        else:
            connect(m, phy_controller.source, phy.ctrl.source)
            connect(m, phy_controller.sink, phy.ctrl.sink)
            m.d.comb += phy.ctrl.cs.eq(phy_controller.cs)

        return m
