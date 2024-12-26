Bootloader
##########

.. note::

    'Bootloader' is a bit of a misnomer, as what is currently implemented is more like a 'bitstream selector' (although a true USB bootloader is something in the works).

Interface
---------

The bitstream selector allows you to arbitrarily select from one of 8 bitstreams after the Tiliqua powers on, without needing to connect a computer.

Put simply:

- When tiliqua boots, you can select a bitstream with the encoder (either using the display output, or by reading the currently lit LED if no display is connected).
- When you select a bitstream (press encoder), the FPGA reconfigures itself and enters the selected bitstream.
- From the new bitstream, you can always go back to the bitstream selector by holding the encoder for 3sec (this is built into the logic of every bitstream).

.. warning::

    The bitstream selector will not reboot correctly if you have
    the :py:`dbg` USB port connected. The assumption is you're flashing
    development bitstreams to SRAM in that scenario anyway.

Setup and implementation
------------------------

The bitstream selector consists of 2 key components that work together:

- The RP2040 firmware (`apfelbug - fork of dirtyJTAG <https://github.com/apfaudio/apfelbug>`_)
- The `bootloader <https://github.com/apfaudio/tiliqua/tree/main/gateware/src/top/bootloader>`_ top-level bitstream.

The difficulty here is that the ECP5 multiboot mechanism only supports rebooting from one bitstream to a single fixed new address :code:`bootaddr`, which is not sufficient for arbitrary bitstream selection. It would in theory have been possible to set up a 'bitstream chain', however that would require all bitstreams to be correctly flashed with the correct addresses at all times, which is not ideal for development. So, the solution here is more complicated, but hopefully more robust to 'less trustworthy' bitstreams.

First-time setup
^^^^^^^^^^^^^^^^

- Build and flash the `apfelbug <https://github.com/apfaudio/apfelbug>`_ project to the RP2040 (hold RP2040 BOOTSEL during power on, copy the :code:`build/*.uf2` to the usb storage device and reset)
- On the Tiliqua ECP5 SPI flash:
    - Using :code:`openFPGALoader -c dirtyJtag <bitstream> -f o <offset>`
    - flash :code:`bootloader` bitstream to offset 0x0
    - flash user bitstreams to 0x100000, 0x200000, 0x300000 ... and so on (1MB spacing)
- DISCONNECT USB DBG port, reboot Tiliqua
    - Currently :code:`apfelbug` only works correctly with the DBG connector DISCONNECTED or with the UART port open on Linux and CONNECTED. Do not have the USB DBG connected without the UART0 open with :code:`picocom` or so.
- Now when Tiliqua boots you will enter the bootloader. Use the encoder to select an image. Hold the encoder for >3sec in any image to go back to the bootloader.

Bitstream Manifest
^^^^^^^^^^^^^^^^^^

By default the bootloader screen will report all bitstream names as :code:`<unknown>`. The :code:`bootloader` bitstream can optionally read from a JSON manifest stored at the end of SPI flash called a 'Bitstream Manifest'. Such a :code:`manifest.json` file can be copied from :code:`gateware/src/rs/lib/example-manifest.json`. You can flash this to the end of the SPI flash so the bootloader knows what each bitstream should be called.

.. code-block:: bash

    sudo openFPGALoader -c dirtyJtag -f -o 0xfff000 --file-type raw manifest.json

Note: this address :code:`0xfff000` comes from :code:`src/tiliqua/tiliqua_soc.py`.

Assuming the bootloader bitstream is correctly already flashed to 0x0, this command will also reset the FPGA, re-enter the bootloader, and display the updated bitstream manifest.

At the moment, the bootloader itself does no verification that there are actually bitstreams flashed to each slot corresponding to the names. If a bitstream is not actually in a designated slot or is corrupt, selecting the bad slot will simply reboot the FPGA and re-enter the bootloader.

ECP5 implementation
^^^^^^^^^^^^^^^^^^^

The ECP5 :code:`bootloader` bitstream does nothing except tell the RP2040 that it wants to be rebooted into a new bitstream (over UART). However, the user bitstreams are responsible for asserting PROGRAMN when the encoder is held, to reconfigure back to the bootloader bitstream.

RP2040 implementation
^^^^^^^^^^^^^^^^^^^^^

:code:`apfelbug` firmware includes the same features as :code:`pico-dirtyjtag` (USB-JTAG and USB-UART bridge), with some additions:

- UART traffic is inspected to look for keywords.
- If a keyword is encountered e.g. :code:`BITSTREAM1`, a pre-recorded JTAG stream stored on the RP2040's SPI flash is decompressed and replayed. The JTAG streams are instances of the `bootstub <https://github.com/apfaudio/tiliqua/blob/main/gateware/src/top/bootstub/top.py>`_ top-level bitstream. These are tiny bitstreams that are programmed directly into SRAM with the target :code:`bootaddr` and PROGRAMN assertion.
- This facilitates ECP5 multiboot (jumping to arbitrary bitstreams) without needing to write to the ECP5's SPI flash and exhausting write cycles.


Recording new JTAG streams for RP2040
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

TODO documentation, not necessary to change this for any ordinary usecase. Update this if needed for SoldierCrab R3.
