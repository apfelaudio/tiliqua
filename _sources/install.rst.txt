Setup
#####

1. The Basics
^^^^^^^^^^^^^

To build, simulate and flash Tiliqua bitstreams you only need:

- A build system: `pdm (LINK) <https://pdm-project.org/en/latest/#installation>`_
      - You will see installation instructions for Linux / Mac / Windows.
      - This is used for managing Python dependencies in a virtual environment, so that they are A) guaranteed to be exactly the same version as in CI and B) don't affect the installed packages on your computer.

- An FPGA tool suite: `oss-cad-suite (LINK) <https://github.com/YosysHQ/oss-cad-suite-build?tab=readme-ov-file#installation>`_
      - You will see installation instructions for Linux / Mac / Windows.
      - If you are paranoid, it might be worth installing the same version `as we are using in CI <https://github.com/apfaudio/tiliqua/blob/main/gateware/scripts/Dockerfile#L38>`_

.. note::
    By default, synthesis will use :code:`yowasp-yosys` and :code:`yowasp-nextpnr-ecp5` (python packages), rather than any :code:`oss-cad-suite` you have installed. When running locally, builds are often faster if you point to your own installation - modify :code:`gateware/.env.toolchain` (simply deleting it will try to find yosys in your PATH).

2. Check Installation
^^^^^^^^^^^^^^^^^^^^^

Before continuing, it's worth checking everything is installed okay and is correctly in your PATH:

.. code-block:: bash

    $ pdm --version
    PDM, version 2.26.0
    $ openFPGALoader -V
    openFPGALoader v0.12.1
    $ verilator --version
    Verilator 5.031 devel rev v5.030-75-gc98744b91

3. Check USB device permissions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

With Tiliqua powered on and the ``dbg`` USB port connected, you want to see something like this (without using ``sudo``!):

.. code-block:: bash

    $ openFPGALoader --scan-usb
    Bus device vid:pid       probe type      manufacturer serial               product
    007 006    0x1209:0xc0ca dirtyJtag       apf.audio    E463A8574B2D2632     Tiliqua R5 apfbug-beta4-dirty

On Linux, you may need to install a udev rule for this to work. The exact procedure depends on your distro so I can only provide an example. On Arch Linux, I use the following rule:

.. code-block:: bash

    # Placed at `/etc/udev/rules.d/50-tiliqua.rules`
    SUBSYSTEM=="usb", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="c0ca", MODE="0666", GROUP="users", TAG+="uaccess"

You want this configured correctly as some of the Tiliqua build scripts call ``openFPGALoader`` under the hood (without ``sudo``).

4. Initialize submodules
^^^^^^^^^^^^^^^^^^^^^^^^

After cloning the main repository, you should also initialize all the submodules:

.. code-block:: bash

   cd gateware
   git submodule update --init --recursive

5. Set up python environment
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Finally, install all python dependencies in a PDM-managed virtual environment:

.. code-block:: bash

   # from the `gateware` directory
   pdm install

Now you are ready to build some bitstreams!

6. (optional, for CPU Bitstreams) A Rust toolchain
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The above tools are sufficient for simple bitstreams. More advanced bitstreams include an SoC and CPU that runs Rust firmware for the menu system and graphics. To build these bitstreams, you will also need a Rust toolchain.

- Install `rustup (LINK) <https://rustup.rs/>`_

  - Install the following Rust components:

    .. code-block:: bash

       rustup toolchain install # from gateware/, installs the toolchain pinned in rust-toolchain.toml
       cargo install cargo-binutils svd2rust form

  - Make sure the tools like ``svd2rust``, ``form`` and so on are available in your PATH after installing them. You may need to add a ``.cargo/bin``-like directory `to your path <https://doc.rust-lang.org/book/ch14-04-installing-binaries.html>`_.

.. warning::

    Building bitstreams that include an SoC is currently only supported on Linux and Mac. For Windows, the easiest path is probably doing this under WSL.

.. note::

    All examples are built in CI. If you're having trouble setting up your environment or missing a dependency, it may also be worth checking the `Dockerfile <https://github.com/apfaudio/tiliqua/blob/main/gateware/scripts/Dockerfile>`_.

