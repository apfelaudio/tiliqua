Tutorial 1: Audio cores (``top.dsp``)
=====================================

In this tutorial, we'll take a look at the structure of ``top.dsp`` audio-only bitstreams, as well as how to build and simulate them.

In `gateware/src/top/dsp <https://github.com/apfaudio/tiliqua/tree/main/gateware/src/top/dsp>`_, we have 2 files:

    - ``top.py``: example gateware for lots of different DSP bitstreams.
    - ``sim_dsp_core.cpp``: a Verilator testbench for simulating DSP bitstreams.

We'll go deeper into testing and simulation later in this tutorial. For now, let's focus on ``top.py``.

The Basics
----------

Let's start by taking a look at the simplest DSP core in this file, :class:`Mirror <top.dsp.top.Mirror>`:

.. code-block:: python

    class Mirror(wiring.Component):

        """
        Route audio inputs straight to outputs (in the audio domain).
        This is the simplest possible core, useful for basic tests.
        """

        i: In(stream.Signature(data.ArrayLayout(ASQ, 4)))
        o: Out(stream.Signature(data.ArrayLayout(ASQ, 4)))

        bitstream_help = BitstreamHelp(
            brief="Audio passthrough",
            io_left=['in0', 'in1', 'in2', 'in3', 'in0 (copy)', 'in1 (copy)', 'in2 (copy)', 'in3 (copy)'],
            io_right=['', '', '', '', '', '']
        )

        def elaborate(self, platform):
            m = Module()
            wiring.connect(m, wiring.flipped(self.i), wiring.flipped(self.o))
            return m

This is an Amaranth `Component <https://amaranth-lang.org/docs/amaranth/latest/stdlib/wiring.html#components>`_ which takes an incoming stream of audio samples (4 channels wide) and emits an outgoing stream (also 4 channels wide).

A couple of things are worth noting here:

- The ``i`` and ``o`` attributes define the component *signature* - that is, the input and output ports and their direction. For details, see `Interfaces and connections <https://amaranth-lang.org/docs/amaranth/latest/stdlib/wiring.html#>`_ in the Amaranth documentation.
- A ``stream.Signature`` is an Amaranth construct describing a *stream of data* that is accompanied by a ``valid``/``ready`` handshake. This is a simple protocol used commonly in digital logic. For more details, see `Data streams <https://amaranth-lang.org/docs/amaranth/latest/stdlib/stream.html>`_ in the Amaranth documentation.
- ``ASQ`` is the *native audio sample format* used by Tiliqua, which is defined as ``fixed.SQ(1, 15)`` - that is, 16-bits wide, 1 integer bit and 15 fractional bits. This ``fixed.SQ`` is a *fixed-point type* which is not quite part of the Amaranth language yet, `but will be soon <https://github.com/amaranth-lang/amaranth/pull/1578>`_.

.. note::

    The ``bitstream_help`` attribute is optional and has nothing to do with Amaranth or the gateware. If supplied, Tiliqua's build system puts the provided metadata in your final *Bitstream Archive*, and the bootloader uses this to display the IO mappings of your bitstream graphically in the bitstream selection screen.

The business logic of our core is in the ``elaborate`` method:

.. code-block:: python

        def elaborate(self, platform):
            m = Module()
            wiring.connect(m, wiring.flipped(self.i), wiring.flipped(self.o))
            return m

For this core, we are simply connecting the incoming and outgoing streams together. So, each audio input will be sent straight to each audio output.

Taking a look at the body of a different core in ``top.py``, for example ``Matrix``:

.. code-block:: python

    def elaborate(self, platform):
        m = Module()

        m.submodules.matrix_mix = matrix_mix = dsp.MatrixMix(
            i_channels=4, o_channels=4,
            coefficients=[[0.4, 0.3, 0.2, 0.1],
                          [0.1, 0.4, 0.3, 0.2],
                          [0.2, 0.1, 0.4, 0.3],
                          [0.3, 0.2, 0.1, 0.4]])

        wiring.connect(m, wiring.flipped(self.i), matrix_mix.i)
        wiring.connect(m, matrix_mix.o, wiring.flipped(self.o))

        return m

Above, we are instantiating a :class:`tiliqua.dsp.MatrixMix` component with some parameters, connecting our audio inputs ``self.i`` to the matrix mixer inputs ``matrix_mix.i`` and the mixer outputs ``matrix_mix.o`` to our audio outputs ``self.o``.

.. note::

    You will notice that we create an ``m = Module()``, all the interesting logic uses it, and then we must return the ``m`` at the end of the ``elaborate`` method. This is a pattern seen everywhere in Amaranth HDL, as operations on ``Module`` are how hardware definitions are built up.

CLI and ``CoreTop`` wrapper
---------------------------

The definitions of ``Mirror`` and ``Matrix`` we have seen above *are not the whole hardware design*. In fact, when we select a project, the ``Mirror`` core (for example) is being instantiated *inside* a wrapper core called ``CoreTop``. This is because interfacing with the LEDs, audio CODEC, audio clocks and resets requires a lot of peripheral logic. All of this is contained in ``CoreTop``, to reduce the amount of boilerplate in each example core. You only need to worry about processing each audio sample.

You will notice at the bottom of ``top.py`` we have a list of cores like this:

.. code-block:: python

    # Different DSP cores that can be selected at top-level CLI.
    CORES = {
        #                 (touch, class name)
        "mirror":         (False, Mirror),
        "nco":            (False, QuadNCO),
        "svf":            (False, ResonantFilter),
        "vca":            (False, DualVCA),
        # ...


Because the ``dsp/top.py`` file contains *multiple projects*, there is some extra hooks set up so that the specific project can be selected using ``--dsp-core``. You can list them using

.. code-block:: bash

    $ pdm dsp build -h
    <...>
    options:
      <...>
      --dsp-core DSP_CORE   One of ['mirror', 'nco', 'svf', 'vca', 'pitch', 'matrix', 'touchmix', 'waveshaper', 'midicv',
                            'psram_pingpong', 'sram_pingpong', 'psram_diffuser', 'sram_diffuser', 'mdiff', 'resampler',
                            'triple_mirror', 'stft_mirror', 'vocode', 'noise', 'dwo']

And then to build one:

.. code-block:: bash

    $ pdm dsp build --dsp-core=nco

.. note::

    Feel free to build some of these cores and flash / try them to Tiliqua before continuing.


Simulation
----------

Simulation is essential in order to be able to debug gateware and understand what is going on. In this repository, you will find 2 different approaches to simulation:

    - **Native Amaranth simulations**: These are small Python testbenches that you can run your gateware against, using `Amaranth's built-in simulator <https://amaranth-lang.org/docs/amaranth/latest/simulator.html>`_. The vast majority of testbenches for individual components in this repository are written in this way - as you will find in `gateware/tests <https://github.com/apfaudio/tiliqua/tree/main/gateware/tests>`_.
    - **Verilator simulations**: For simulating entire, fully-integrated toplevel designs (such as SoCs or bitstreams with video), the native python testbenches can be slow. So, most top-level bitstreams support being simulated end-to-end with ``verilator``, which is much faster. Under the hood, this involves converting the entire design to Verilog, 'verilating' it into C++, and then compiling and running the C++ implementation against a testbench.


Test Suite (amaranth.sim)
^^^^^^^^^^^^^^^^^^^^^^^^^

To run the entire Tiliqua test suite (native amaranth simulations), you can run:

.. code-block:: bash

    # note: spawns as many test threads as you have CPUs
    $ pdm test

Alternatively, to run just a single test, for example to only test the ``Resample`` core:

.. code-block:: bash

    $ pdm run python3 -m pytest tests/test_dsp.py -k resample

.. note::

    Either of these will emit a bunch of simulation ``*.vcd`` files which you can open up with ``gtkwave`` or ``surfer`` in order to inspect the behavior of every net in the design as it is being simulated.

Integration Tests (verilator)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Generally, to run an integration test of a particular project, you can provide ``sim`` rather than ``build`` on the command line. For example:

.. code-block:: bash

    pdm dsp sim --dsp-core=mirror --trace-fst

This command will simulate our ``Mirror`` DSP core, inside the ``CoreTop`` wrapper, as an entire design with simulated audio/I2S inputs as described in our ``sim_dsp_core.cpp`` testbench. Once it is finished, you will notice 2 files:

    - ``simx.fst``: the waveform trace from simulation
    - ``sim-i2s-outputs.svg``: a small SVG file containing traces of all 4 audio outputs

.. note::

    The ``*.fst`` file is only created if the ``--trace-fst`` flag is supplied as above. If you omit it, the simulation will run faster, but you won't get a waveform trace any more.

On opening up the ``*.fst`` file, if you add some signals under ``TOP->top->pmod0``, for example ``i_cal__payload[0][15:0]`` and ``o_cal__payload[0][15:0]``, press the ``Zoom Fit`` button, you will see some activity.

Then, if you right click on one of the signals in the 'Time' column, select ``Data Format->Analog->Step`` followed by ``Data Format->Signed Decimal`` and maybe also hit the ``Insert Analog Height Extension`` a few times, you will be able to see one of the sine wave excitations coming in from the testbench:

.. figure:: /_static/simx_mirror.png

Because in the ``Mirror`` case there are no unique nets inside the core (we are just connecting inputs to outputs), there are no interesting signals for us to look at inside it.

If we instead run:

.. code-block:: bash

    # `nco` is a quad VCO with 4 wave types
    pdm dsp sim --dsp-core=nco --trace-fst

And then open up ``sim-i2s-outputs.svg``, you will see sine, saw, triangle and square waves all phase modulated by the channel 0 sine wave input:

.. figure:: /_static/nco.png
