Example Projects
################

Top-level projects are located in ``gateware/src/top``.

.. note::

    Projects are split into ``SoC`` and ``no-SoC`` categories. In general, ``SoC`` projects contain a CPU and menu system, are more sophisticated, and take a while to synthesize. ``no-SoC`` projects are much smaller and simpler, often with no video output, and are very quick to synthesize.

Each top-level project has a command-line interface. See the 'Getting started' section for how to build and flash these top-level designs.

.. toctree::
   soc_examples
   no_soc_examples
