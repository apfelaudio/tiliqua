Tutorial 3: Video cores (``top.beamrace``)
==========================================

.. warning::

   This tutorial is not finished yet.

'Beamracing' is a simple method for creating interesting video patterns, by calculating the color of each pixel right before it is needed. It's especially well-suited to FPGAs as we can build custom logic just for computing the value of each pixel, at the full video clock rate.

In `gateware/src/top/beamrace <https://github.com/apfaudio/tiliqua/tree/main/gateware/src/top/beamrace>`_, we have 2 files:

    - ``top.py``: example gateware for a few different beamracing bitstreams.
    - ``sim.cpp``: a Verilator testbench for simulating ``beamrace`` bitstreams. This one writes each frame as an image to a bitmap file for inspection

Simulation
----------

If you go ahead and run:

.. code-block:: bash

    $ pdm beamrace sim --core=balls

You will notice the simulation emits some images like ``frameXX.bmp``. If you open one up, you will see an interesting pattern. These images have been calculated by the ``Balls()`` core present in ``top.py``, in response to some simulated audio inputs.

Building and ``--modeline``
---------------------------

As these simple ``beamrace`` cores do not include a CPU (part of the reason they synthesize so quickly!), they also do not support dynamic framebuffer resizing.

For this reason, you'll want to supply a ``--modeline`` argument depending on what screen you want to use. For example, for Tiliqua's round screen, you will want something like:

.. code-block:: bash

    $ pdm beamrace build --core=balls --modeline=720x720p60r2

Structure
---------

Each ``beamrace`` core shares the same input and output interface, so they can all share the same ``BeamRaceTop`` wrapper core (which handles all the auxiliary logic outside the actual pixel color calculations).

You will notice each pattern has attributes like:

.. code-block:: python

    i: In(BeamRaceInputs())
    o: Out(BeamRaceOutputs())

These have the following structure:

.. code-block:: python

    class BeamRaceInputs(wiring.Signature):
        """
        Inputs into a beamracing core, all in the 'dvi' domain (at the pixel clock).
        """
        def __init__(self):
            super().__init__({
                # Video timing inputs
                "hsync":     Out(1),
                "vsync":     Out(1),
                "de":        Out(1),
                "x":         Out(signed(12)),
                "y":         Out(signed(12)),
                # Audio samples (already synchronized to DVI domain)
                "audio_in0": Out(signed(16)),
                "audio_in1": Out(signed(16)),
                "audio_in2": Out(signed(16)),
                "audio_in3": Out(signed(16)),
            })

    class BeamRaceOutputs(wiring.Signature):
        """
        Outputs from a beamracing core, all in the 'dvi' domain (at the pixel clock).
        """
        def __init__(self):
            super().__init__({
                "r":     Out(8),
                "g":     Out(8),
                "b":     Out(8),
            })

In this fashion, each core already has the current audio sample and position in the frame, as well as timing signals, to use for pattern generation.

Note that all signals are already synchronized into the video domain, with the ``dvi`` domain already remapped to ``sync``, so from the perspective of your own pattern-generating core, you can do everything in the ``sync`` domain.

TODO
----

Finish writing this.
