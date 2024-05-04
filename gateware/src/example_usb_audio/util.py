# Copyright (c) 2021 Hans Baier <hansfbaier@gmail.com>
# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: BSD--3-Clause

from amaranth             import *

# some things lifted from `amlib`, given we don't need anyting else from there.

class EdgeToPulse(Elaboratable):
    """
        each rising edge of the signal edge_in will be
        converted to a single clock pulse on pulse_out
    """
    def __init__(self):
        self.edge_in          = Signal()
        self.pulse_out        = Signal()

    def elaborate(self, platform) -> Module:
        m = Module()

        edge_last = Signal()

        m.d.sync += edge_last.eq(self.edge_in)
        with m.If(self.edge_in & ~edge_last):
            m.d.comb += self.pulse_out.eq(1)
        with m.Else():
            m.d.comb += self.pulse_out.eq(0)

        return m

def connect_fifo_to_stream(fifo, stream, firstBit: int=None, lastBit: int=None) -> None:
    """Connects the output of the FIFO to the of the stream. Data flows from the fifo the stream.
       It is assumed the payload occupies the lowest significant bits
       This function connects first/last signals if their bit numbers are given
    """

    result = [
        stream.valid.eq(fifo.r_rdy),
        fifo.r_en.eq(stream.ready),
        stream.payload.eq(fifo.r_data),
    ]

    if firstBit:
        result.append(stream.first.eq(fifo.r_data[firstBit]))
    if lastBit:
        result.append(stream.last.eq(fifo.r_data[lastBit]))

    return result


def connect_stream_to_fifo(stream, fifo, firstBit: int=None, lastBit: int=None) -> None:
    """Connects the stream to the input of the FIFO. Data flows from the stream to the FIFO.
       It is assumed the payload occupies the lowest significant bits
       This function connects first/last signals if their bit numbers are given
    """

    result = [
        fifo.w_en.eq(stream.valid),
        stream.ready.eq(fifo.w_rdy),
        fifo.w_data.eq(stream.payload),
    ]

    if firstBit:
        result.append(fifo.w_data[firstBit].eq(stream.first))
    if lastBit:
        result.append(fifo.w_data[lastBit].eq(stream.last))

    return result
