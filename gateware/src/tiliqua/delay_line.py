# Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""PSRAM- or SRAM-backed streaming audio delay lines."""

from amaranth              import *
from amaranth.lib          import wiring, data, stream
from amaranth.lib.wiring   import In, Out
from amaranth.utils        import exact_log2

from amaranth_future       import fixed
from amaranth_soc          import wishbone

from vendor.soc.cores      import sram

from tiliqua.eurorack_pmod import ASQ
from tiliqua.cache         import WishboneL2Cache

from tiliqua.dsp           import *

class DelayLine(wiring.Component):

    """SRAM- or PSRAM-backed audio delay line.

    This forms the backbone of many different types of effects - echoes,
    pitch shifting, chorus, feedback synthesis etc.

    Usage
    -----

    Each :class:`DelayLine` instance operates in a single-writer, multiple-reader
    fashion - that is, for each :class:`DelayLine`, there may be only one stream
    of samples being *written*, however from each :class:`DelayLine` you may
    create N instances of :class:`DelayLineTap`, which are submodules of :class:`DelayLine`
    used to produce output streams (read operations) on the :class:`DelayLine`.

    For a simple, SRAM-backed delay line, the following is sufficient:

    .. code-block:: python

        delayln = DelayLine(
            max_delay=8192,
            write_triggers_read=False,
        )

    From this, you can create some read taps:

    .. code-block:: python

        tap1 = delayln.add_tap()
        tap2 = delayln.add_tap()

    The :class:`DelayLine` instance requires a single incoming stream :py:`DelayLine.i`,
    on which incoming samples are taken and written to the backing store.

    Each :class:`DelayLineTap` instance requires both an incoming *and* outgoing
    stream, :py:`DelayLineTap.i`, :py:`DelayLineTap.o`, where an output sample is
    *only* produced some time after the requested delay count has arrived on
    :py:`DelayLineTap.i`.

    This gives applications the flexibility to read multiple times per
    write sample (useful for example for fractional delay lines where
    we want to interpolate between two adjacent samples).

    Fixed (simple) delay taps
    -------------------------

    It can be a bit cumbersome to need to provide each tap with an
    input stream if you just want some taps with fixed delays.

    So, if you want a simple fixed delay tap, you can use the
    :py:`write_triggers_read=True` option when creating the :class:`DelayLine`. Then,
    you can specify explicit fixed delay taps as follows:

    .. code-block:: python

        delayln = DelayLine(max_delay=8192, write_triggers_read=True)
        tap1    = delayln.add_tap(fixed_delay=5000)
        tap2    = delayln.add_tap(fixed_delay=7000)

    .. note::

        When used in this mode, :py:`tap1` and :py:`tap2` will internally have their
        inputs (sample request streams) hooked up to the write strobe. This
        means you no longer need to hook up :py:`tapX.i` and will automatically
        get a single sample on each :py:`tapX.o` after every write to :py:`delayln`.

    Backing store
    -------------

    The backing store is a contiguous region of memory where samples are
    written to a wrapped incrementing index (i.e circular buffer fashion).

    The same memory space is shared by all read & write operations, however
    the way this works is slightly different when comparing SRAM- and PSRAM-
    backed delay lines. In both cases, all read & write operations go through
    an arbiter and share the same memory bus.

    - **SRAM-backed delay line**
        The memory bus is connected directly to an
        FPGA DPRAM instantiation and does not need to be connected to any external
        memory bus.

    - **PSRAM-backed delay line**
        Due to the memory access latency of PSRAM,
        simply forwarding each read/write access would quickly consume memory
        bandwidth simply due to the access latency. So, in the PSRAM case, a
        small cache is inserted between the internal delay line R/W bus and
        the memory bus exposed by `DelayLine.bus` (normally hooked up to the PSRAM).
        The purpose of this cache is to collect as many read & write operations into
        burstable transactions as possible.

    .. note::
        As each delayline contains completely different samples and individually
        has quite a predictable access pattern, it makes sense to have one cache
        per :class:`DelayLine`, rather than one larger shared cache (which would likely
        perform worse considering area/bandwidth). The important factor is that
        all writes and reads on the same delayline share the same cache, as
        the write and read taps have the same working set.

    Members
    -------
    i : :py:`In(stream.Signature(ASQ))`
        Input stream for writing samples to the delay line.
    bus : :py:`Out(wishbone.Signature)`
        Wishbone bus for connecting to external PSRAM (usually through an arbiter).
        *Only present for PSRAM-backed delay lines.*
    """

    INTERNAL_BUS_DATA_WIDTH  = 16
    INTERNAL_BUS_GRANULARITY = 8

    def __init__(self, max_delay, psram_backed=False, addr_width_o=None, base=None,
                 write_triggers_read=True, cache_kwargs=None):
        """
        max_delay : int
            The maximum delay in samples. This exactly corresponds to the memory
            required in the backing store. Must be a power of 2.
        psram_backed : bool, optional
            If True, the delay line is backed by PSRAM. Otherwise, it is backed
            by SRAM.
        addr_width_o : int, optional
            *Required for PSRAM-backed delay lines.*
            The address width of the external memory bus.
        base : int, optional
            *Required for PSRAM-backed delay lines.*
            The memory slice base address. This is the physical address offset in bytes.
        write_triggers_read : bool, optional
            If True, writing to the delay line triggers a read. This means the
            :py:`DelayLineTap.i` stream does not need to be connected
        cache_kwargs : dict, optional
            *Relevant only for PSRAM-backed delay lines.*
            Arguments to forward to creation of the internal memory cache.
        """

        if psram_backed:
            assert base is not None
            assert addr_width_o is not None
        else:
            assert base is None
            assert addr_width_o is None

        self.max_delay = max_delay
        self.address_width = exact_log2(max_delay)
        self.write_triggers_read = write_triggers_read
        self.psram_backed = psram_backed

        # reader taps that may read from this delay line
        self.taps = []

        # internal bus is lower footprint than the SoC bus.
        data_width  = self.INTERNAL_BUS_DATA_WIDTH
        granularity = self.INTERNAL_BUS_GRANULARITY

        # bus that this delayline writes samples to
        self.internal_writer_bus = wishbone.Signature(
            addr_width=self.address_width,
            data_width=data_width,
            granularity=granularity
        ).create()

        # arbiter to round-robin between write transactions (from this
        # DelayLine) and read transactions (from children DelayLineTap)
        self._arbiter = wishbone.Arbiter(addr_width=self.address_width,
                                         data_width=data_width,
                                         granularity=granularity)
        self._arbiter.add(self.internal_writer_bus)

        # internal signal between DelayLine and DelayLineTap
        self._wrpointer = Signal(unsigned(self.address_width))
        self._mem_zeroed = Signal(init=0 if self.psram_backed else 1)

        # ports exposed to the outside world
        ports = {
            "i":   In(stream.Signature(ASQ)),
        }

        if psram_backed:

            ports |= {
                "bus": Out(wishbone.Signature(addr_width=addr_width_o,
                                              data_width=32,
                                              granularity=8,
                                              features={'bte', 'cti'})),
            }

            self._adapter = WishboneAdapter(
                addr_width_i=self.address_width,
                addr_width_o=addr_width_o,
                base=base
            )

            if cache_kwargs is None:
                self._cache = WishboneL2Cache(
                    addr_width=addr_width_o,
                )
            else:
                self._cache = WishboneL2Cache(
                    addr_width=addr_width_o,
                    **cache_kwargs,
                )

        super().__init__(ports)

    def add_tap(self, fixed_delay=None):
        """
        Add and return a new :class:`DelayLineTap` to stream samples read from this :class:`DelayLine`.

        .. note::

            Each tap automatically becomes a submodule of the :class:`DelayLine` instance.
            That is, you only need to add :class:`DelayLine` itself to :py:`m.submodules`.

        fixed_delay : int
            The :py:`DelayLineTap.i` is automatically set to a fixed delay.
            *Only used when* :py:`write_triggers_read=True`.
        """
        if self.write_triggers_read:
            assert fixed_delay is not None
            assert fixed_delay < self.max_delay
        tap = DelayLineTap(parent_bus=self._arbiter.bus, writer_bus=self.internal_writer_bus,
                           fixed_delay=fixed_delay)
        self.taps.append(tap)
        self._arbiter.add(tap._bus)
        return tap

    def elaborate(self, platform):
        m = Module()

        if self.write_triggers_read:
            # split the write strobe up into identical streams to be used by read taps.
            m.submodules.isplit = isplit = Split(n_channels=1+len(self.taps), replicate=True,
                                                 source=wiring.flipped(self.i))
            istream = isplit.o[0]
        else:
            # otherwise, the user wants to handle read tap synchronization themselves.
            istream = wiring.flipped(self.i)

        for n, tap in enumerate(self.taps):
            m.d.comb += tap._wrpointer.eq(self._wrpointer)
            m.d.comb += tap._mem_zeroed.eq(self._mem_zeroed)
            if self.write_triggers_read:
                # Every write sample propagates to a read sample without needing
                # to hook up the 'i' stream on delay taps.
                sync_on = isplit.o[1+n]
                m.d.comb += [
                    tap.i.valid.eq(sync_on.valid),
                    sync_on.ready.eq(tap.i.ready),
                    tap.i.payload.eq(tap.fixed_delay),
                ]

        named_submodules(m.submodules, self.taps)

        m.submodules.arbiter = self._arbiter

        if self.psram_backed:
            # adapt small internal 16-bit shared bus to external 32-bit shared bus
            # through a small L2 cache so reads + writes burst the memory accesses.
            m.submodules.adapter = self._adapter
            m.submodules.cache   = self._cache
            wiring.connect(m, self._arbiter.bus, self._adapter.i)
            wiring.connect(m, self._adapter.o, self._cache.master)
            wiring.connect(m, self._cache.slave, wiring.flipped(self.bus))
        else:
            # Local SRAM-backed delay line. No need for adapters or caches.
            sram_size = self.max_delay * (self._arbiter.bus.data_width //
                                          self._arbiter.bus.granularity)
            m.submodules.sram = sram_peripheral = sram.Peripheral(
                size=sram_size, data_width=self._arbiter.bus.data_width,
                granularity=self._arbiter.bus.granularity
            )
            wiring.connect(m, self._arbiter.bus, sram_peripheral.bus)

        # bus for sample writes which sits before the arbiter
        bus = self.internal_writer_bus

        with m.FSM() as fsm:

            if self.psram_backed:
                # PSRAM is not zeroed on boot, we must do it ourselves.
                with m.State('ZERO-MEMORY'):
                    m.d.comb += [
                        bus.stb.eq(1),
                        bus.cyc.eq(1),
                        bus.we.eq(1),
                    ]
                    m.d.sync += [
                        bus.adr.eq(self._wrpointer),
                        bus.dat_w.eq(0),
                        bus.sel.eq(0b11),
                    ]
                    with m.If(bus.ack):
                        with m.If(self._wrpointer != (self.max_delay - 1)):
                            m.d.sync += self._wrpointer.eq(self._wrpointer + 1)
                        with m.Else():
                            m.next = 'WAIT-VALID'
                            m.d.sync += self._wrpointer.eq(0)
                            m.d.sync += self._mem_zeroed.eq(1)

            with m.State('WAIT-VALID'):
                m.d.comb += istream.ready.eq(1)
                with m.If(istream.valid):
                    m.d.sync += [
                        bus.adr  .eq(self._wrpointer),
                        bus.dat_w.eq(istream.payload),
                        bus.sel  .eq(0b11),
                    ]
                    m.next = 'WRITE'

            with m.State('WRITE'):
                m.d.comb += [
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(1),
                ]
                with m.If(bus.ack):
                    with m.If(self._wrpointer != (self.max_delay - 1)):
                        m.d.sync += self._wrpointer.eq(self._wrpointer + 1)
                    with m.Else():
                        m.d.sync += self._wrpointer.eq(0)
                    m.next = 'WAIT-VALID'

        return m

class DelayLineTap(wiring.Component):
    """
    A single read tap of a parent :class:`DelayLine`.
    See :class:`DelayLine` top-level comment for information on usage.
    :class:`DelayLineTap` should only be created using :py:`DelayLine.add_tap()`.

    Members
    -------
    i : :py:`In(unsigned(N))`
        Stream of delays requested to be read from the delay line.
        The unit is in number of samples *behind* the last written
        sample to the delay line.
    o : :py:`Out(stream.Signature(ASQ))`
        Stream of samples read from the delay line, one per request
        on :py:`DelayLineTap.i`.
    """
    def __init__(self, parent_bus, writer_bus, fixed_delay=None):

        self.fixed_delay = fixed_delay
        self.max_delay   = 2**parent_bus.addr_width
        self.addr_width  = parent_bus.addr_width
        self.writer_bus  = writer_bus

        # internal signals between parent DelayLine and child DelayLineTap
        self._wrpointer = Signal(unsigned(parent_bus.addr_width))
        self._mem_zeroed = Signal()
        self._bus = wishbone.Signature(addr_width=parent_bus.addr_width,
                                       data_width=parent_bus.data_width,
                                       granularity=parent_bus.granularity).create()

        super().__init__({
            "i":         In(stream.Signature(unsigned(parent_bus.addr_width))),
            "o":         Out(stream.Signature(ASQ)),
        })

    def elaborate(self, platform):
        m = Module()

        bus = self._bus

        with m.FSM() as fsm:
            with m.State('WAIT-ZERO'):
                with m.If(self._mem_zeroed):
                    m.next = 'WAIT-VALID'
            with m.State('WAIT-VALID'):
                m.d.comb += self.i.ready.eq(1)
                with m.If(self.i.valid):
                    with m.If(self.i.payload == 0):
                        m.next = 'ZDELAY'
                    with m.Else():
                        m.d.sync += bus.adr.eq(self._wrpointer - self.i.payload)
                        m.next = 'READ'
            with m.State('ZDELAY'):
                with m.If(self.writer_bus.stb):
                    m.d.sync += self.o.payload.eq(self.writer_bus.dat_w)
                    m.next = 'WAIT-READY'
            with m.State('READ'):
                m.d.comb += [
                    bus.stb.eq(1),
                    bus.cyc.eq(1),
                    bus.we.eq(0),
                    bus.sel.eq(0b11),
                ]
                with m.If(bus.ack):
                    m.d.sync += self.o.payload.eq(bus.dat_r)
                    m.next = 'WAIT-READY'
            with m.State('WAIT-READY'):
                m.d.comb += self.o.valid.eq(1)
                with m.If(self.o.ready):
                    m.next = 'WAIT-VALID'

        return m

class WishboneAdapter(wiring.Component):
    """
    Adapter between external (dw=32) and internal (dw=16) buses of DelayLine.
    Used to adapt the internal bus to the correct size for external memory.

    TODO: this should really be parameterized beyond 16-bit samples...
    """

    def __init__(self, addr_width_i, addr_width_o, base):
        self.base = base
        super().__init__({
            "i": In(wishbone.Signature(addr_width=addr_width_i,
                                       data_width=16,
                                       granularity=8)),
            "o": Out(wishbone.Signature(addr_width=addr_width_o,
                                        data_width=32,
                                        granularity=8)),
        })

    def elaborate(self, platform):
        m = Module()

        m.d.comb += [
            self.i.ack.eq(self.o.ack),
            self.o.adr.eq((self.base<<2) + (self.i.adr>>1)),
            self.o.we.eq(self.i.we),
            self.o.cyc.eq(self.i.cyc),
            self.o.stb.eq(self.i.stb),
        ]

        with m.If(self.i.adr[0]):
            m.d.comb += [
                self.i.dat_r.eq(self.o.dat_r>>16),
                self.o.sel  .eq(self.i.sel<<2),
                self.o.dat_w.eq(self.i.dat_w<<16),
            ]
        with m.Else():
            m.d.comb += [
                self.i.dat_r.eq(self.o.dat_r),
                self.o.sel  .eq(self.i.sel),
                self.o.dat_w.eq(self.i.dat_w),
            ]

        return m
