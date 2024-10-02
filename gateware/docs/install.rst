Installation
############

Prerequisites
=============

On an Ubuntu system, the following are the main dependencies:
- The build system: install [pdm](https://github.com/pdm-project/pdm)
- For synthesis: install [oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build)
- For examples that include a softcore: [rust](https://rustup.rs/).
    - To build stripped images for RISC-V, you also need:
        - `rustup target add riscv32imac-unknown-none-elf`
        - `rustup component add rustfmt clippy llvm-tools`
        - `cargo install cargo-binutils svd2rust form`

To set up the environment:
```bash
cd gateware
git submodule update --init --recursive
pdm install
```
