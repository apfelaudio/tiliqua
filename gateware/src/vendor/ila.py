from luna.gateware.debug.ila import ILAFrontend
from amaranth                import Cat
from vendor.bits             import bits

class AsyncSerialILAFrontend(ILAFrontend):
    """ UART-based ILA transport.

    Parameters
    ------------
    port: string
        The serial port to use to connect. This is typically a path on *nix systems.
    ila: IntegratedLogicAnalyzer
        The ILA object to work with.
    """

    def __init__(self, *args, ila, **kwargs):
        import serial

        self._port = serial.Serial(*args, **kwargs)
        self._port.reset_input_buffer()

        super().__init__(ila)


    def _split_samples(self, all_samples):
        """ Returns an iterator that iterates over each sample in the raw binary of samples. """

        sample_width_bytes = self.ila.bytes_per_sample

        print(all_samples[0:100])
        while all_samples[0:4] != b'\xef\xbe\xad\xde':
            all_samples = all_samples[1:]

        # Iterate over each sample, and yield its value as a bits object.
        for i in range(0, len(all_samples), sample_width_bytes):
            raw_sample    = all_samples[i:i + sample_width_bytes]
            sample_length = len(Cat(self.ila.signals))

            yield bits.from_bytes(raw_sample, length=sample_length, byteorder='little')


    def _read_samples(self):
        """ Reads a set of ILA samples, and returns them. """

        sample_width_bytes = self.ila.bytes_per_sample
        total_to_read      = self.ila.sample_depth * sample_width_bytes

        # Fetch all of our samples from the given device.
        all_samples = self._port.read(total_to_read)
        return list(self._split_samples(all_samples))
