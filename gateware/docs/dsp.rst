Tiliqua DSP Library
###################

Overview
--------

TODO short overview of the DSP library philosophy.

TODO link to Amaranth documentation on streams.

Delay Lines
-----------

.. autoclass:: tiliqua.delay_line.DelayLine

Filters
-------

.. autoclass:: tiliqua.dsp.SVF
.. autoclass:: tiliqua.dsp.FIR
.. autoclass:: tiliqua.dsp.Boxcar


Oscillators
-----------

.. autoclass:: tiliqua.dsp.SawNCO

Effects
-------

.. autoclass:: tiliqua.dsp.WaveShaper
.. autoclass:: tiliqua.dsp.PitchShift

VCAs
----

.. autoclass:: tiliqua.dsp.VCA
.. autoclass:: tiliqua.dsp.GainVCA

Mixing
------

.. autoclass:: tiliqua.dsp.MatrixMix

Resampling
----------

.. autoclass:: tiliqua.dsp.Resample

One-shot
--------

.. autoclass:: tiliqua.dsp.Trigger
.. autoclass:: tiliqua.dsp.Ramp

Stream utilities
----------------

Splitting / merging streams
^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: tiliqua.dsp.Split
.. autoclass:: tiliqua.dsp.Merge

Connecting and remapping streams
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
.. autofunction:: tiliqua.dsp.connect_remap
.. autofunction:: tiliqua.dsp.channel_remap

Connecting streams in feedback loops
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: tiliqua.dsp.KickFeedback
.. autofunction:: tiliqua.dsp.connect_feedback_kick

Other utilities
---------------

.. autofunction:: tiliqua.dsp.named_submodules
