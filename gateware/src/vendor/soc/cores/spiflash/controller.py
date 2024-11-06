#
# This file is part of LUNA.
#
# Copyright (c) 2024 Great Scott Gadgets <info@greatscottgadgets.com>
# SPDX-License-Identifier: BSD-3-Clause

# Based on code from LiteSPI

from amaranth                           import Module, DomainRenamer, Signal, unsigned
from amaranth.lib                       import wiring
from amaranth.lib.fifo                  import SyncFIFO
from amaranth.lib.data                  import StructLayout, View
from amaranth.lib.wiring                import In, Out, flipped, connect

from amaranth_soc                       import csr

from .port                              import SPIControlPort


class SPIController(wiring.Component):
    """Wishbone generic SPI Flash Controller interface.

    Provides a generic SPI Controller that can be interfaced using CSRs.
    Supports multiple access modes with the help of ``width`` and ``mask`` registers which
    can be used to configure the PHY into any supported SDR mode (single/dual/quad/octal).
    """

    class Phy(csr.Register, access="rw"):
        """PHY control register

            length : SPI transfer length in bits.
            width  : SPI transfer bus width (1/2/4/8).
            mask   : SPI DQ output enable mask.
        """
        def __init__(self, source):
            super().__init__({
                "length" : csr.Field(csr.action.RW, unsigned(len(source.len))),
                "width"  : csr.Field(csr.action.RW, unsigned(len(source.width))),
                "mask"   : csr.Field(csr.action.RW, unsigned(len(source.mask))),
            })

    class Cs(csr.Register, access="w"):
        """SPI chip select register

            select : SPI chip select signal.
        """
        select : csr.Field(csr.action.W, unsigned(1))

    class Status(csr.Register, access="r"):
        """Status register

             rx_ready : RX FIFO contains data.
             tx_ready : TX FIFO ready to receive data.
        """
        rx_ready : csr.Field(csr.action.R, unsigned(1))
        tx_ready : csr.Field(csr.action.R, unsigned(1))

    class Data(csr.Register, access="rw"):
        """Data register

            rx : Read the next byte in the RX FIFO
            tx : Write the given byte to the TX FIFO
        """
        def __init__(self, width):
            super().__init__({
                "rx" : csr.Field(csr.action.R, unsigned(width)),
                "tx" : csr.Field(csr.action.W, unsigned(width))
            })


    def __init__(self, *, data_width=32, granularity=8, rx_depth=16, tx_depth=16, name=None, domain="sync"):
        wiring.Component.__init__(self, SPIControlPort(data_width))

        self._domain   = domain

        # layout description for writing to the tx fifo
        self.tx_fifo_layout = StructLayout({
            "data":  len(self.source.data),
            "len":   len(self.source.len),
            "width": len(self.source.width),
            "mask":  len(self.source.mask),
        })

        # fifos
        self._rx_fifo = DomainRenamer(domain)(SyncFIFO(width=len(self.sink.payload), depth=rx_depth))
        self._tx_fifo = DomainRenamer(domain)(SyncFIFO(width=len(self.source.payload), depth=tx_depth))

        # registers
        regs = csr.Builder(addr_width=5, data_width=8)
        self._phy    = regs.add("phy",    self.Phy(self.source))
        self._cs     = regs.add("cs",     self.Cs())
        self._status = regs.add("status", self.Status())
        self._data   = regs.add("data",   self.Data(data_width))

        # bridge
        self._bridge = csr.Bridge(regs.as_memory_map())

        super().__init__({
            "bus" : In(self._bridge.bus.signature),
        })
        self.bus.memory_map = self._bridge.bus.memory_map


    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge

        connect(m, self.bus, self._bridge.bus)

        # FIFOs.
        m.submodules.rx_fifo = rx_fifo = self._rx_fifo
        m.submodules.tx_fifo = tx_fifo = self._tx_fifo

        # Chip select generation.
        cs = Signal()
        with m.FSM():
            with m.State("RISE"):
                # Enable chip select when the CSR is set to 1 and the TX FIFO contains something.
                m.d.comb += cs.eq(tx_fifo.r_rdy)
                with m.If(cs == 1):
                    m.next = "FALL"
            with m.State("FALL"):
                # Only disable chip select after the current TX FIFO is emptied.
                m.d.comb += cs.eq(self._cs.f.select.w_data | tx_fifo.r_rdy)
                with m.If(cs == 0):
                    m.next = "RISE"

        # Connect FIFOs to PHY streams.
        tx_fifo_payload = View(self.tx_fifo_layout, tx_fifo.w_data)
        m.d.comb += [
            # CSRs to TX FIFO.
            tx_fifo.w_en                   .eq(self._data.f.tx.w_stb),
            tx_fifo_payload.data           .eq(self._data.f.tx.w_data),
            tx_fifo_payload.len            .eq(self._phy.f.length.data),
            tx_fifo_payload.width          .eq(self._phy.f.width.data),
            tx_fifo_payload.mask           .eq(self._phy.f.mask.data),

            # SPI chip select.
            self.cs                        .eq(cs),

            # TX FIFO to SPI PHY (PICO).
            self.source.payload            .eq(tx_fifo.r_data),
            self.source.valid              .eq(tx_fifo.r_rdy),
            tx_fifo.r_en                   .eq(self.source.ready),

            # SPI PHY (POCI) to RX FIFO.
            rx_fifo.w_data                 .eq(self.sink.payload),
            rx_fifo.w_en                   .eq(self.sink.valid),
            self.sink.ready                .eq(rx_fifo.w_rdy),

            # RX FIFO to CSRs.
            rx_fifo.r_en                   .eq(self._data.f.rx.r_stb),
            self._data.f.rx.r_data         .eq(rx_fifo.r_data),

            # FIFOs ready flags.
            self._status.f.rx_ready.r_data .eq(rx_fifo.r_rdy),
            self._status.f.tx_ready.r_data .eq(tx_fifo.w_rdy),
        ]

        # Convert our sync domain to the domain requested by the user, if necessary.
        if self._domain != "sync":
            m = DomainRenamer({"sync": self._domain})(m)

        return m
