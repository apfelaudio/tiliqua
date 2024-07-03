# Tiliqua

<sup>WARN: 🚧 under construction! 🚧 - this module is in active development</sup>

**[Tiliqua](https://en.wikipedia.org/wiki/Blue-tongued_skink) is a powerful, hackable FPGA-based audio multitool for Eurorack.**

<img src="doc/img/tiliqua-front-left.jpg" width="500">

## Technical
- 8 (4 in + 4 out) DC-coupled audio channels, 192KHz / 32bit sampling supported.
- Display output for video synthesis (DVI-compatible, on the lowest-end ECP5 timing tops out at 1280x720p60).
- Switched rotary encoder with bargraph display.
- Touch and proximity sensing on all unused audio jacks (8 max).
- USB C `dbg`: Included RP2040-based JTAG debugger supported by `openFPGAloader`.
- USB C `usb2`: USB2 PHY connected directly to FPGA for high-speed USB Audio support.
- Based on Lattice ECP5 FPGA, supported by open-source FPGA toolchains. FPGA SoM itself is replaceable and also open hardware (codename `soldiercrab`).
- Large SPI flash and HyperRAM (for long audio buffers or video framebuffers)
- TRS MIDI IN jack.
- PWM-controlled, user-programmable red/green LEDs on each audio channel.
- Jack insertion detection on input & output jacks.
- 2x expansion ports (PMOD compatible) for up to 24 simultaneous audio channels (with extra eurorack-pmods).

<img src="doc/img/tiliqua-rear-left.jpg" width="700">

## Where do I get a Tiliqua?

We are planning to launch Tiliqua on Crowd Supply in Q3 '24.

# Getting Started

## Building example projects

On an Ubuntu system, first make sure you have [pdm](https://github.com/pdm-project/pdm) installed as well as a recent version of [oss-cad-suite](https://github.com/YosysHQ/oss-cad-suite-build), and (for SoC examples only) [rust](https://rustup.rs/). Then:

```bash
cd gateware
# install all python requirements
pdm install
# for the LUNA-based 4in + 4out USB soundcard example
pdm build_usb_audio
# for a 4-channel waveshaping oscillator
pdm build_dsp_core nco
# for a diffusion delay effect
pdm build_dsp_core diffuser
# for a polyphonic MIDI synth
pdm build_dsp_core midipoly
# for an SoC example (RISCV softcore)
pdm build_soc
# for the vectorscope / DVI example
pdm build_vectorscope
```

All examples are also built in CI - check `.github/workflows` if you need more gruesome details on how systems are built.

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
