Prerequisites
#############

On an Ubuntu system, the following are the main dependencies:

- The build system: install `pdm <https://github.com/pdm-project/pdm>`_
- For synthesis: install `oss-cad-suite <https://github.com/YosysHQ/oss-cad-suite-build>`_
- For examples that include a softcore: `rust <https://rustup.rs/>`_

  - To build stripped images for RISC-V, you also need:

    .. code-block:: bash

       rustup target add riscv32im-unknown-none-elf
       rustup component add rustfmt clippy llvm-tools
       cargo install cargo-binutils svd2rust form

To set up the environment:

.. code-block:: bash

   cd gateware
   git submodule update --init --recursive
   pdm install

All examples are built in CI. If you're having trouble setting up your environment, it may also be worth checking the github workflow configuration.

By default, synthesis will use :code:`yowasp-yosys` and :code:`yowasp-nextpnr-ecp5` , rather than any :code:`oss-cad-suite` you have installed. When running locally, builds are usually faster if you point to your own installation - modify :code:`gateware/.env.toolchain` (simply deleting it will try to find yosys in your PATH).
