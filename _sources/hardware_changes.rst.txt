Changelist
##########

There were some Tiliqua hardware released before the production R5 version. This page serves as a brief history of the differences between these versions, as well as any changes in post-production batches.

Tiliqua R5.1
============

First production hardware release.

    - **SoldierCrab R3** FPGA SoM (LFE5U-25F, 1.8V 32MByte oSPIRAM)
    - **Tiliqua R5** motherboard and front panel.
    - **eurorack-pmod R3.5** audio interface.

R5.1 changes (compared to R4)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

    - Add DVI redriver IC between ECP5 and video port (PI3HDX511) - improves signal integrity with longer cables, improves EMC performance.
    - Add extra I2C switch between GPDI and internal I2C bus, so the GPDI level shifter can be decoupled from the internal I2C bus. Remove ``MOBO_LED_EN`` and re-use the pin for this switch:``GPDI_DDC_EN``.
    - Bump +12V ingress fuse from 350mA to 500mA to support the extra redriver current draw.
    - Swap +3V3 supply from an LDO to an SMPS to reduce power dissipation.
    - Update audio board from R3.3 to R3.5, which has improve DC tracking, noise, and soft-mute performance.
    - Add 33R series resistors on all expansion header pins.
    - Add more ESD protection to USB1/USB2 pins.
    - Pinswaps: see ``tiliqua_platform.py`` in the main repository.
    - ECOs: Added 909R resistor in parallel with C34 to prevent backward leakage current across U9. All production units have this ECO applied.

Tiliqua R4
==========

Production prototype that some beta testers have. Only 15 were produced.

    - **SoldierCrab R3** FPGA SoM (LFE5U-25F, 1.8V 32MByte oSPIRAM)
    - **Tiliqua R4** motherboard and front panel.
    - **eurorack-pmod R3.3** audio interface.

R4 changes (compared to R3)
^^^^^^^^^^^^^^^^^^^^^^^^^^^

    - Add external PLL SI5351 and route 2x clocks to ECP5 (useful for EMC as it supports spread-spectrum, also for runtime clock/resolution switching).
    - Add series 27R/33R on all FFC lines to reduce radiated emissions.
    - Pinswaps to ensure external PLL is routed to true ECP5 clock input pins:
        - FFC_SDIN1: 44 -> 42
        - ENC_B: 40 -> 12
        - ENC_A: 42 -> 8
        - PLL_CLK1 -> 40 (removed: spare FPGA to RP2040 line)
        - PLL_CLK0 -> 44 (removed: spare FPGA to RP2040 line)
    - Route 4 new ex0/ex1 pins to RP2040 spare pins (shared with expansion connectors)
    - Swap RP2040 SPI flash for 128MBit part
    - Put spare RP2040 I2C pins on main tiliqua-mobo I2C bus.
    - Switch from 4L stackup to 6L stackup to improve SI/EMC.

Tiliqua R3
==========

Another hardware revision that some beta testers have. Only 5 were produced.

    - **SoldierCrab R3** FPGA SoM (LFE5U-25F, 1.8V 32MByte oSPIRAM)
    - **Tiliqua R3** motherboard and front panel.
    - **eurorack-pmod R3.3** audio interface.


R3 changes (compared to R2)
^^^^^^^^^^^^^^^^^^^^^^^^^^^

    - Pinswap all DVI pins to true ECP5 complementary pairs.
    - Swap all tantalums for ceramics, clean up PSU routing, swap LDO for TPS7A91, add choke input capacitors
    - Delete unused LEDs (MIDI bottom-side)
    - Fix EDID +5V I2C bridge schematic and enable.
    - Adjust fuses: +12V ingress 200mA->350mA fuse, GPDI +5V NONE->50mA fuse (so USB host doesn't pop the fuse!)
    - Move encoder footprint 0.4mm in, midi 0.1mm out for 0.5mm shim washer on encoder
    - Pinswap ex0 / ex1 / ffc connectors for improved routing and SI.
    - Update LED current limiting resistors s/120R/220R
    - Swap rp2040 xtal to ABM8/15pF/1K (improve yield)
    - New M2.5 standoff footprint
    - Move 12V ingress connector in 1.5mm so we can better fit in skiffs.
    - Switch from 1.6mm to 1.2mm PCBA stackup for mechanical reasons (and improve usb2 connector yield)
    - (panel) fix DVI connector cutout
    - Add 2x spare pins for RP2040/ECP5 I2C (no pullups)
    - Layerswap In1 / In2 (move GND closer to SMPS)
    - Add flip-flop on CODEC PDN pin (allows for soft-mute when swapping bitstreams)


Tiliqua R2
==========

This was the first hardware revision that some beta testers have. Only 5 were produced.

    - **SoldierCrab R2** FPGA SoM (LFE5U-45F, 3.3V 16MByte HyperRAM)
    - **Tiliqua R2** motherboard and front panel.
    - **eurorack-pmod R3.3** audio interface.


Tiliqua R1
==========

This prototype was shown at SuperBooth 2024. It was never shipped to anyone.

    - **SoldierCrab R1** FPGA SoM (LFE5U-45F, 3.3V 16MByte HyperRAM, no USB2 PHY)
    - **Tiliqua R1** motherboard and front panel.
    - **eurorack-pmod R3.3** audio interface.

