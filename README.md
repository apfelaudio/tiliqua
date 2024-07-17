# Tiliqua

<sup>WARN: 🚧 under construction! 🚧 - this module is in active development</sup>

**[Tiliqua](https://en.wikipedia.org/wiki/Blue-tongued_skink) is a powerful, hackable FPGA-based audio multitool for Eurorack. It will launch soon [on Crowd Supply](https://www.crowdsupply.com/apfelaudio/tiliqua)**

<img src="doc/img/tiliqua-front-left.jpg" width="500">

## Technical

#### Audio Interface
- 8 (4 in + 4 out) DC-coupled audio channels, 192 KHz / 24-bit sampling supported
- Touch and proximity sensing on all 8 audio jacks (if unused)
- PWM-controlled, user-programmable red/green LEDs on each audio channel
- Jack insertion detection on all 8 jacks

#### Motherboard
- Switched rotary encoder with bar graph display.
- Dual USB ports:
    - `dbg`: Included RP2040-based JTAG debugger supported by `openFPGAloader`
    - `usb2`: USB PHY connected directly to FPGA for high-speed USB Audio support
- Display output for video synthesis (maximum resolution 720/60P)
- 2x expansion ports for up to 24 simultaneous audio channels (PMOD-compatible)
- MIDI-In jack (TRS-A standard)

#### Embedded FPGA SoM (`soldiercrab`)

- Lattice ECP5 (25 K) FPGA, supported by open-source FPGA toolchains
- 128 Mbit (16 MByte) HyperRAM / oSPI RAM (for long audio buffers or video framebuffers)
- 128 Mbit (16 MByte) SPI flash for user bitstreams
- High-speed USB HS PHY (ULPI)
<img src="doc/img/tiliqua-rear-left.jpg" width="700">

## Where do I get a Tiliqua?

Tiliqua is launching on [Crowd Supply](https://www.crowdsupply.com/apfelaudio/tiliqua). Subscribing there is the best place to get updates.

# Getting Started

## Building example projects

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
# fetch eurorack-pmod verilog sources
git submodule update --init --recursive
# install all python requirements to a local .venv
pdm install
```

To build some examples:
```bash
# for the LUNA-based 4in + 4out USB soundcard example
pdm build_usb_audio
# for a 4-channel waveshaping oscillator
pdm build_dsp_core nco
# for a diffusion delay effect
pdm build_dsp_core diffuser
# for a polyphonic MIDI synth
pdm build_dsp_core midipoly
# for the vectorscope / DVI example
pdm build_vectorscope
# for an SoC example (selftest with RISCV softcore)
pdm build_selftest
# (WIP) vectorscope + SoC + menu system
pdm build_xbeam
```

Generally, bitstreams are also built in CI - check `.github/workflows` if you need more gruesome details on how systems are built.

## Flashing example projects

The built-in RP2040 JTAG debugger is based on the `dirtyJtag` project. You can flash the bitstreams above to the SRAM of the FPGA like so (add an `-f` to instead flash it to SPI flash permanently):

```bash
sudo openFPGALoader -c dirtyJtag build/top.bit
```

If you are running an SoC, it will give you serial output that you can monitor like so:

```bash
sudo picocom -b 115200 /dev/ttyACM0
```

## Simulating DSP cores

The easiest way to debug the internals of a DSP project is to simulate it. This project provides some shortcuts to enable simulating designs end-to-end with Verilator, which is much faster at crunching numerically heavy designs than Amaranth's built-in simulator.

For example, to simulate the waveshaping oscillator example:

```bash
pdm sim_dsp_core nco
```

A lot happens under the hood! In short this command:
- Elaborates your Amaranth HDL and convert it to Verilog
- Verilates your verilog into a C++ implementation, compiling it against `sim_dsp_core.cpp` provided in `gateware/example_dsp` that excites the audio inputs (you can modify this).
- Runs the verilated binary itself and spits out a trace you can view with `gtkwave` to see exactly what every net in the whole design is doing.

## Builds on the following (awesome) open-hardware projects
- Audio interface and gateware from my existing [eurorack-pmod](https://github.com/apfelaudio/eurorack-pmod) project.
- USB interface and gateware based on [LUNA and Cynthion](https://github.com/greatscottgadgets/luna/) projects.
- USB Audio gateware and descriptors based on [adat-usb2-audio-interface](https://github.com/hansfbaier/adat-usb2-audio-interface).

# License

The hardware and gateware in this project is largely covered under the CERN Open-Hardware License V2 CERN-OHL-S, mirrored in the LICENSE text in this repository. Some gateware and software is covered under the BSD 3-clause license - check the header of the individual source files for specifics.

**Copyright (C) 2024 Sebastian Holzapfel**

The above LICENSE and copyright notice do NOT apply to imported artifacts in this repository (i.e datasheets, third-party footprints), or dependencies released under a different (but compatible) open-source license.
