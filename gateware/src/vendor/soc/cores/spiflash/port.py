#
# This file is part of LUNA.
#
# Copyright (c) 2024 Great Scott Gadgets <info@greatscottgadgets.com>
# SPDX-License-Identifier: BSD-3-Clause

# Based on code from LiteSPI

from amaranth               import Module, Signal, Cat, EnableInserter
from amaranth.lib.fifo      import AsyncFIFO
from amaranth.lib.cdc       import FFSynchronizer
from amaranth.lib           import wiring
from amaranth.lib.wiring    import In, Out, connect

from .utils                 import RoundRobin


#
# Signatures.
#

class StreamCore2PHY(wiring.Signature):
    def __init__(self, data_width):
        self._data_width = data_width
        members = {
            "data":  Out(data_width),
            "len":   Out(6),
            "width": Out(4),
            "mask":  Out(8),
            "valid": Out(1),
            "ready": In(1),
        }
        super().__init__(members)

    @property
    def data_width(self):
        return self._data_width

    def __eq__(self, other):
        return isinstance(other, StreamCore2PHY) and self.data_width == other.data_width

    def __repr__(self):
        return f"StreamCore2PHY({self.data_width})"

    def create(self, *, path=None, src_loc_at=0):
        return StreamCore2PHYInterface(self, path=path, src_loc_at=1 + src_loc_at)

class StreamCore2PHYInterface(wiring.PureInterface):
    @property
    def payload(self):
        """ Joint signal with all data. """
        return Cat(self.data, self.len, self.width, self.mask)


class StreamPHY2Core(wiring.Signature):
    def __init__(self, data_width):
        self._data_width = data_width
        members = {
            "data":  Out(data_width),
            "valid": Out(1),
            "ready": In(1),
        }
        super().__init__(members)

    @property
    def data_width(self):
        return self._data_width

    def __eq__(self, other):
        return isinstance(other, StreamPHY2Core) and self.data_width == other.data_width

    def __repr__(self):
        return f"StreamPHY2Core({self.data_width})"

    def create(self, *, path=None, src_loc_at=0):
        return StreamPHY2CoreInterface(self, path=path, src_loc_at=1 + src_loc_at)

class StreamPHY2CoreInterface(wiring.PureInterface):
    @property
    def payload(self):
        """ Joint signal with all data. """
        return self.data


class SPIControlPort(wiring.Signature):
    def __init__(self, data_width):
        super().__init__({
            "source": Out(StreamCore2PHY(data_width)),
            "sink":   In(StreamPHY2Core(data_width)),
            "cs":     Out(1),
        })


#
# SPI control port utilities.
#

class SPIControlPortCrossbar(wiring.Component):
    """ Merge multiple SPIControlPorts with a round-robin scheduler. """

    def __init__(self, *, data_width=32, num_ports=1, domain="sync"):
        self._domain = domain
        self._num_ports = num_ports

        super().__init__(dict(
            controller=Out(SPIControlPort(data_width)),
            **{f"slave{i}": In(SPIControlPort(data_width)) for i in range(num_ports)}
        ))

    def get_port(self, index):
        return getattr(self, f"slave{index}")

    def elaborate(self, platform):
        m = Module()

        grant_update = Signal()
        m.submodules.rr = rr = EnableInserter(grant_update)(RoundRobin(count=self._num_ports))
        m.d.comb += rr.requests.eq(Cat(self.get_port(i).cs for i in range(self._num_ports)))

        # Multiplexer.
        with m.Switch(rr.grant):
            for i in range(self._num_ports):
                with m.Case(i):
                    connect(m, wiring.flipped(self.get_port(i)), wiring.flipped(self.controller))
                    m.d.comb += grant_update.eq(~rr.valid | ~rr.requests[i])

        return m


class SPIControlPortCDC(wiring.Component):
    """ Converts one SPIControlPort between clock domains. """

    def __init__(self, *, data_width=32, domain_a="sync", domain_b="sync", depth=4):
        super().__init__({
            "a": In(SPIControlPort(data_width)),
            "b": Out(SPIControlPort(data_width)),
        })
        self.domain_a = domain_a
        self.domain_b = domain_b
        self.depth    = depth

    def elaborate(self, platform):
        m = Module()
        a, b = self.a, self.b

        tx_cdc = AsyncFIFO(width=len(a.source.payload), depth=self.depth, w_domain=self.domain_a, r_domain=self.domain_b)
        rx_cdc = AsyncFIFO(width=len(b.sink.data), depth=self.depth, w_domain=self.domain_b, r_domain=self.domain_a)
        cs_cdc = FFSynchronizer(a.cs, b.cs, o_domain=self.domain_b)

        m.submodules.tx_cdc = tx_cdc
        m.submodules.rx_cdc = rx_cdc
        m.submodules.cs_cdc = cs_cdc

        m.d.comb += [
            # Wire TX path.
            tx_cdc.w_data       .eq(a.source.payload),
            tx_cdc.w_en         .eq(a.source.valid),
            a.source.ready      .eq(tx_cdc.w_rdy),
            b.source.payload    .eq(tx_cdc.r_data),
            b.source.valid      .eq(tx_cdc.r_rdy),
            tx_cdc.r_en         .eq(b.source.ready),

            # Wire RX path.
            rx_cdc.w_data       .eq(b.sink.data),
            rx_cdc.w_en         .eq(b.sink.valid),
            b.sink.ready        .eq(rx_cdc.w_rdy),
            a.sink.data         .eq(rx_cdc.r_data),
            a.sink.valid        .eq(rx_cdc.r_rdy),
            rx_cdc.r_en         .eq(a.sink.ready),
        ]

        return m
