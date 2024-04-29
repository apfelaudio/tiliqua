# `tiliqua`

<sup>WARN: 🚧 under construction! 🚧 - this module is in early development</sup>

**[Tiliqua](https://en.wikipedia.org/wiki/Blue-tongued_skink) is a powerful FPGA-based audio multitool for Eurorack.**

**Goal:** make it easier to get started in FPGAs in the context of audio.

## Technical
- Based on Lattice ECP5 FPGA, supported by open-source FPGA toolchains.
- 128MBit SPI flash + 128MBit HyperRAM (for long audio buffers!)
- USB C `usr`: USB2 PHY connected directly to FPGA for high-speed USB Audio support.
- USB C `dbg`: Included RP2040-based JTAG debugger.
- MIDI IN + MIDI OUT jacks.
- 8 (4 in + 4 out) DC-coupled audio channels, 192KHz / 32bit sampling supported.
- Touch and proximity sensing on all unused audio jacks (8 max).
- PWM-controlled, user-programmable red/green LEDs on each audio channel.
- Jack insertion detection on input & output jacks.
- 2x expansion ports (PMOD compatible) for up to 24 simultaneous audio channels (with extra eurorack-pmods).
