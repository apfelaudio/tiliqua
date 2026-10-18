FFT Processing
--------------

Building blocks for frequency-domain transforms.

.. note::
   For utilities that operate on blocks in the frequency domain, see :doc:`spectral`.

FFTs
^^^^

.. autoclass:: tiliqua.dsp.fft.FFT

Windowing
^^^^^^^^^

.. autoclass:: tiliqua.dsp.fft.Window

Overlap / Add
^^^^^^^^^^^^^

.. autoclass:: tiliqua.dsp.fft.ComputeOverlappingBlocks

.. autoclass:: tiliqua.dsp.fft.OverlapAddBlocks

STFTs
^^^^^

.. autoclass:: tiliqua.dsp.fft.STFTProcessor

.. autoclass:: tiliqua.dsp.fft.STFTAnalyzer

.. autoclass:: tiliqua.dsp.fft.STFTSynthesizer

.. autoclass:: tiliqua.dsp.fft.STFTProcessorPipelined
