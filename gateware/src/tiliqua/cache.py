# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
Cache components, for accelerating memory accesses to a backing store.
"""

from amaranth                    import *
from amaranth.lib                import data, wiring
from amaranth.lib.wiring         import Component, In, Out, flipped, connect
from amaranth.utils              import exact_log2
from amaranth.lib.memory         import Memory

from amaranth_soc                import wishbone

class WishboneL2Cache(wiring.Component):

    """
    Wishbone cache, designed to go between a wishbone master and backing store.

    This cache is direct-mapped and write-back.
    - 'direct-mapped': https://en.wikipedia.org/wiki/Cache_placement_policies
    - 'write-back': https://en.wikipedia.org/wiki/Cache_(computing)#Writing_policies

    The 'master' bus is for the wishbone master that uses the cache.
    The 'slave' bus is for the backing store. The cache acts as a master on
    this bus in order to fill / evict cache lines. The cache will issue burst
    transactions of length `burst_len` whenever a cache line is to be evicted
    (written to the backing store) or refilled (read from the backing store).

    `cachesize_words` (in `data_width` words) is the size of the data store
    and must be a power of 2.

    This cache is a partial rewrite of the equivalent LiteX component:
    https://github.com/enjoy-digital/litex/blob/master/litex/soc/interconnect/wishbone.py

    Key differences to LiteX implementation:
    - Tags now include a 'valid' bit, so every cache line must be refilled
      after reset before it can be used (imporant for any component that is
      reading from external memory, particularly if contains data at boot).
    - Translation of bus data widths is removed and replaced with wishbone burst
      transactions of length matching the cache line. Cache lines themselves have
      have size (in bits) of `data_width*burst_len`.
    """

    def __init__(self, cachesize_words=64, addr_width=22, data_width=32,
                 granularity=8, burst_len=4, lutram_backed=False):

        # Technically we should issue classic transactions to the backing
        # store if burst_len == 1, but this cache will always issue bursts.
        assert burst_len > 1

        self.cachesize_words = cachesize_words
        self.data_width      = data_width
        self.burst_len       = burst_len
        self.granularity     = granularity
        self.lutram_backed    = lutram_backed

        super().__init__({
            "master": In(wishbone.Signature(addr_width=addr_width,
                                            data_width=data_width,
                                            granularity=granularity)),
            "slave": Out(wishbone.Signature(addr_width=addr_width,
                                            data_width=data_width,
                                            granularity=granularity,
                                            features={"cti", "bte"})),
        })

    def elaborate(self, platform):
        m = Module()

        master = self.master
        slave  = self.slave

        dw_from = dw_to = self.data_width

        # Slice master.addr into 3 fields:
        # (MSB) adr_tag .. adr_line .. adr_offset (LSB)
        addressbits = len(slave.adr)
        offsetbits  = exact_log2(self.burst_len)
        linebits    = exact_log2(self.cachesize_words // self.burst_len)
        tagbits     = addressbits - linebits - offsetbits
        adr_offset  = master.adr.bit_select(0, offsetbits)
        adr_line    = master.adr.bit_select(offsetbits, linebits)
        adr_tag     = master.adr.bit_select(offsetbits+linebits, tagbits)

        # Similar usage as adr_offset, iterates from 0..burst_len when
        # refilling/evicting cache lines.
        burst_offset = Signal.like(adr_offset)
        burst_offset_lookahead = Signal.like(burst_offset)

        # Cache line (data) memory. Each line has (virtual) size `data_width*burst_len`.
        # 'burst_offset'/'adr_offset' index are just extra concatenated address lines.
        # This ensures DPRAM inference still works (it doesn't for shape > 32bits).
        m.submodules.data_mem = data_mem = Memory(
            shape=unsigned(self.data_width), depth=2**linebits*self.burst_len, init=[])
        wr_port = data_mem.write_port(granularity=self.granularity)

        if self.lutram_backed:
            rd_port = data_mem.read_port(domain='comb')
        else:
            rd_port = data_mem.read_port(transparent_for=(wr_port,))


        write_from_slave = Signal()

        word_select = Const(1).replicate(dw_to//self.granularity)

        m.d.comb += [
            rd_port.addr.eq(Cat(adr_offset, adr_line)),
            slave.dat_w.eq(rd_port.data),
            slave.sel.eq(word_select),
            master.dat_r.eq(rd_port.data),
        ]

        with m.If(write_from_slave):
            m.d.comb += [
                wr_port.addr.eq(Cat(burst_offset, adr_line)),
                wr_port.data.eq(slave.dat_r),
                wr_port.en.eq(word_select),
            ]
        with m.Else():
            m.d.comb += wr_port.addr.eq(Cat(adr_offset, adr_line)),
            m.d.comb += wr_port.data.eq(master.dat_w),
            with m.If(master.cyc & master.stb & master.we & master.ack):
                m.d.comb += wr_port.en.eq(master.sel)

        # Tag storage memory. Maps addr_line (cache line address) to the higher order
        # bits of master.adr (adr_tag). If the adr_tag in the tag storage matches
        # the requested adr_tag, we know the cache line has the data we want.
        tag_layout = data.StructLayout({
            "tag": unsigned(tagbits),
            "dirty": unsigned(1),
            "valid": unsigned(1),
        })
        m.submodules.tag_mem = tag_mem= Memory(shape=tag_layout, depth=2**linebits, init=[])
        tag_wr_port = tag_mem.write_port()
        tag_rd_port = tag_mem.read_port(domain='comb')
        tag_do = Signal(shape=tag_layout)
        tag_di = Signal(shape=tag_layout)
        m.d.comb += [
            tag_do.eq(tag_rd_port.data),
            tag_wr_port.data.eq(tag_di),
        ]

        m.d.comb += [
            tag_wr_port.addr.eq(adr_line),
            tag_rd_port.addr.eq(adr_line),
            tag_di.tag.eq(adr_tag)
        ]

        m.d.comb += slave.adr.eq(Cat(Const(0).replicate(offsetbits), adr_line, tag_do.tag))

        with m.FSM() as fsm:

            with m.State("IDLE"):
                with m.If(master.cyc & master.stb):
                    m.next = "TEST_HIT"

            with m.State("TEST_HIT"):
                with m.If((tag_do.tag == adr_tag) & tag_do.valid):
                    m.d.comb += master.ack.eq(1)
                    with m.If(master.we):
                        m.d.comb += [
                            tag_di.valid.eq(1),
                            tag_di.dirty.eq(1),
                            tag_wr_port.en.eq(1)
                        ]
                    m.next = "IDLE"
                with m.Else():
                    with m.If(tag_do.dirty):
                        m.next = "EVICT"
                    with m.Else():
                        # Write the tag first to set the slave address
                        m.d.comb += [
                            tag_di.valid.eq(1),
                            tag_wr_port.en.eq(1),
                        ]
                        m.next = "REFILL"

            with m.State("EVICT"):

                m.d.comb += [
                    slave.stb.eq(1),
                    slave.cyc.eq(1),
                    slave.we.eq(1),
                    slave.cti.eq(wishbone.CycleType.INCR_BURST),
                    rd_port.addr.eq(Cat(burst_offset_lookahead, adr_line)),
                ]

                if not self.lutram_backed:
                    a = Signal(self.data_width)
                    a_valid = Signal()
                    with m.If(burst_offset_lookahead == 0):
                        m.d.sync += burst_offset_lookahead.eq(burst_offset_lookahead+1)
                        m.d.sync += a_valid.eq(0)
                    with m.If(~a_valid):
                        m.d.sync += a.eq(rd_port.data)
                        m.d.sync += a_valid.eq(1)
                    m.d.comb += slave.dat_w.eq(a)

                with m.If(slave.ack):
                    # Write the tag first to set the slave address
                    m.d.comb += [
                        tag_di.valid.eq(1),
                        tag_wr_port.en.eq(1),
                    ]

                    if self.lutram_backed:
                        m.d.comb += burst_offset_lookahead.eq(burst_offset+1)
                    else:
                        skip = Signal.like(burst_offset_lookahead)
                        m.d.comb += slave.dat_w.eq(rd_port.data)
                        m.d.comb += rd_port.addr.eq(Cat(skip, adr_line)),
                        with m.If(burst_offset_lookahead != self.burst_len-1):
                            m.d.sync += burst_offset_lookahead.eq(burst_offset_lookahead+1)
                            m.d.comb += skip.eq(burst_offset_lookahead + 1)
                        with m.Else():
                            m.d.comb += skip.eq(burst_offset_lookahead)

                    m.d.sync += burst_offset.eq(burst_offset + 1)
                    with m.If(burst_offset == (self.burst_len - 1)):
                        m.d.comb += slave.cti.eq(wishbone.CycleType.END_OF_BURST)
                        m.next = "WAIT"

            with m.State("WAIT"):
                if not self.lutram_backed:
                    m.d.sync += burst_offset_lookahead.eq(0)
                # Deassert stb between EVICT/REFILL
                m.next = "REFILL"

            with m.State("REFILL"):
                m.d.comb += [
                    slave.stb.eq(1),
                    slave.cyc.eq(1),
                    slave.we.eq(0),
                    slave.cti.eq(wishbone.CycleType.INCR_BURST),
                ]
                with m.If(slave.ack):
                    m.d.comb += [
                        write_from_slave.eq(1),
                    ]
                    m.d.sync += burst_offset.eq(burst_offset + 1)
                    with m.If(burst_offset == (self.burst_len - 1)):
                        m.d.comb += slave.cti.eq(wishbone.CycleType.END_OF_BURST)
                        m.next = "TEST_HIT"

        return m
