Expander / Euro-PMOD [``TLQ-EXPANDER``]
#######################################

A eurorack-friendly audio frontend compatible with many FPGA boards (including Tiliqua), based on the AK4619VN audio CODEC

.. figure:: /_static/cards/pmod_photos.jpeg

Use Cases
^^^^^^^^^

This board needs a ``TLQ-MODULE`` or any FPGA board that has a PMOD port in order to do something. Some examples of how you can use it:

- **Expander for TLQ-MODULE**
    - Add more audio channels to Tiliqua, by plugging it into either the ``ex0`` or ``ex1`` PMOD expansion ports. An example of instantiating extra audio channels inside Tiliqua bitstreams can be found in the :class:`TRIPLE-MIRROR <top.dsp.top.TripleMirror>` example project.
    - **NEW:** I now have an experimental build called ``xbeam-ex`` which is similar to ``xbeam``, but exposes a 12in/12out USB interface, expecting 1 or 2 expanders connected. `See here for detailed instructions <https://github.com/apfaudio/tiliqua/issues/155>`_ !
- **Audio IO for different FPGA board**
    - **For Amaranth:** I would suggest re-using the ``EurorackPmod`` component in ``src/tiliqua/periph/eurorack_pmod.py``. It has drivers for every chip on this board. You can use it in the same way as the :class:`TRIPLE-MIRROR <top.dsp.top.TripleMirror>` example linked above, on any FPGA board.
    - **For Verilog:** take a look at the `eurorack-pmod repository <https://github.com/apfaudio/eurorack-pmod>`_, where I provide some DSP examples for different FPGA boards. *Note: at this time, the verilog repo is a bit out of date and refers to R3.3, even though I am shipping R3.5. The hardware is fully backward compatible excluding the touch sensing, which had its mapping changed. So if you compile bitstreams for R3.3 in that repository, they will also work on R3.5, except the touch sensing, which I am happy to update if there is interest.*

Cables
^^^^^^

In the box, you will find 2 cables:

- One for the +/- 12V power supply (standard 10-pin to 16-pin Eurorack cable, brown is -12V).
- One male-to-female PMOD expansion ribbon cable.

Both of these need to be connected for the board to work. The board uses 3.3V from the PMOD input to power some internal digital circuitry, but all the analog circuitry is powered from the +/- 12V rails (including a 3.3V LDO that powers the analog half of the audio codec, to reduce noise coupling from the digital side).

Connecting to Tiliqua
^^^^^^^^^^^^^^^^^^^^^

To use this board on ``ex0`` or ``ex1``, make sure your system is powered off. Both ``TLQ-MODULE`` and ``TLQ-EXPANDER`` should have their own Eurorack power cables connected. Then, using the PMOD ribbon cable, connect the ``TLQ-EXPANDER`` to one of the Tiliqua expansion ports, **making sure sure the +3.3V line is on the correct side of the expander and the expansion port!!**.

Hardware Block Diagram
^^^^^^^^^^^^^^^^^^^^^^

For deeper details (and schematics) of the hardware, see the `eurorack-pmod README <https://github.com/apfaudio/eurorack-pmod>`_.

.. figure:: /_static/cards/pmod_block.jpeg


Instructions for Safe Use
^^^^^^^^^^^^^^^^^^^^^^^^^

- Do not supply power to the product through any receptacle except the main +/- 12V power input and +3.3v PMOD power pins. The incorrect usage or connection of unapproved devices to any receptacle may affect compliance or result in damage to the unit and invalidate the warranty.
- Use with the ribbon cables connected to any hardware other than TLQ-MODULE, or at clock frequencies outside those supported by manufacturer-supplied TLQ-MODULE gateware is to be done at your own risk, in a controlled laboratory environment. Any external FPGA board, case, power supply, cables, or modules shall comply with relevant regulations and standards applicable in the country of intended use.
- The product shall only be used in a Eurorack system, providing a standard Eurorack power supply not exceeding +/- 12V, and at least 500mA.
- To reach electromagnetic compatibility in a Eurorack system, the product shall be mounted securely in a fully enclosed housing made of conductive material (metal). Close all unused spaces with blind panels. Keep all cables, power or patch cables as short as possible.
- The product should only be operated in a well ventilated case and should not be covered, except by the Eurorack case itself or other module panels. The product is designed for reliable operation at normal ambient room temperature. Do not expose the module to water, moisture or bring the circuit boards in contact with any conductive materials.
- Take care whilst handling to avoid mechanical or electrical damage. When the product is powered on, do not touch any of the circuit boards behind the front panel. When the product is outside a case, only handle it unpowered and by the edges to minimize the risk of damage from electrostatic discharge.
