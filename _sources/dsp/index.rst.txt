DSP Library
###########

Philosophy
----------

Tiliqua's DSP library is designed as a suite of DSP components - independent 'cores' which can be connected together in different ways in order to build a custom DSP pipeline. It makes heavy use of Amaranth streams (`lib.stream <https://amaranth-lang.org/docs/amaranth/latest/stdlib/stream.html>`_) for connecting components and `lib.fixed <https://github.com/amaranth-lang/amaranth/pull/1578>`_ for fixed-point types. `lib.stream <https://amaranth-lang.org/docs/amaranth/latest/stdlib/stream.html>`_ makes it possible to chain DSP components together in different ways (without components needing to know implementation details of each other), and `lib.fixed <https://github.com/amaranth-lang/amaranth/pull/1578>`_ makes it easier to write common numeric operations in Amaranth.

.. note::

    Streams are an Amaranth construct describing a *stream of data* that is accompanied by a ``valid``/``ready`` handshake. This is a simple protocol used commonly in digital logic. For more details, see `Data streams <https://amaranth-lang.org/docs/amaranth/latest/stdlib/stream.html>`_ in the Amaranth documentation.

Interconnect
------------

Building a custom DSP pipeline with the components provided here is often an act of figuring out how to massage the input and output ports of each component such that the design does what you want. In simple cases, like oscillators or filters, DSP components will often have a ``self.i`` stream for incoming and ``self.o`` stream for outgoing samples - these can be chained together in any order using `wiring.connect() <https://amaranth-lang.org/docs/amaranth/latest/stdlib/wiring.html>`_. In more complex cases, like delay lines, components may expose a memory bus (for writes to external memory), multiple input or output ports, or global registers. It is important to read the documentation of each component and take a look at some of the example cores in order to understand how each component can be used, and how exactly input and output samples are synchronized.

As of now, input and output ports of DSP components generally take on one of the following shapes:

    - ``stream.Signature(fixed.SQ)``: A stream of audio samples, one at a time.
    - ``stream.Signature(ArrayLayout(N, fixed.SQ))``: A stream of N audio samples, one 1D array at a time. This is used for multi-channel, time-synchronized inputs and outputs -- like Tiliqua's 4 inputs or 4 outputs, or the :class:`tiliqua.dsp.MatrixMix` component. These can be split into streams of single samples using :class:`tiliqua.dsp.Split` or :class:`tiliqua.dsp.Merge` (see :doc:`stream_util`).
    - ``stream.Signature(StructLayout({...}))``: A stream of N different types of data, one set at a time. This is often used when each audio sample needs a piece of metadata alongside it (e.g. realtime tweakable filters like :class:`tiliqua.dsp.SVF`).
    - ``stream.Signature(Block(...))``: Some components can only operate on blocks of samples, like :class:`tiliqua.dsp.fft.FFT` - see :doc:`block` for details.

The art is in knowing exactly which components can be used in translating between the interface styles. For example, :class:`tiliqua.dsp.fft.ComputeOverlappingBlocks` can help going from a sample stream to a block stream. :class:`tiliqua.dsp.Split` for going from an `ArrayLayout <https://amaranth-lang.org/docs/amaranth/latest/stdlib/data.html#amaranth.lib.data.ArrayLayout>`_ to independent sample streams. Depending on the application, often `StructLayout <https://amaranth-lang.org/docs/amaranth/latest/stdlib/data.html#amaranth.lib.data.StructLayout>`_ streams will need some manual handshaking logic. There is no one right answer for every adaptation, especially in cases where you have some control signals alongside synchronized audio streams.

A few components have auxiliary interfaces to the outside world. Examples are :class:`tiliqua.dsp.DelayLine`, which may have a ``bus`` port to talk to external memory (for storing audio samples), or ``usb_audio`` components which require a connection to a USB PHY to service their audio in/out streams.

'Basic' and 'Specialized' components
------------------------------------

DSP cores are split into 2 types, 'Basic' and 'Specialized'. Basic cores do not require qualified access - after a statement like ``from tiliqua import dsp``, these can be accessed through :class:`dsp.Split <tiliqua.dsp.Split>` or similar. 'Specialized' cores need qualified access and may be accessed through :class:`dsp.fft.STFTProcessor <tiliqua.dsp.fft.STFTProcessor>` or similar.

Basic DSP Components
--------------------

.. toctree::
   :maxdepth: 2

   delay_lines
   filters
   oscillators
   effects
   vca
   mix
   resample
   oneshot
   stream_util
   misc

Specialized Modules
-------------------

.. toctree::
   :maxdepth: 2

   delay_effect
   fft
   spectral
   mac
   block
   complex
   cordic
