#
# This file was lifted from LUNA and modified to support sending compressed
# ILA bitstreams over the serial port, which is necessary to be able to
# analyze audio-frequency signals at a high system clock rate (i.e 60MHz USB).
#
# Basically we just use RLE and only sample signals to the BRAM when they change.
# The timestamp of each sample is stored alongside its reading in BRAM.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: BSD-3-Clause
#

import io
import os
import sys
import math
import tempfile
import subprocess

from abc               import ABCMeta, abstractmethod

from amaranth          import Signal, Module, Cat, Elaboratable, Memory, ClockDomain, DomainRenamer
from amaranth.lib.cdc  import FFSynchronizer
from amaranth.lib.fifo import AsyncFIFOBuffered

from luna.gateware.debug.ila import ILAFrontend
from luna.gateware.stream         import StreamInterface
from luna.gateware.interface.uart import UARTMultibyteTransmitter

from vendor.bits       import bits
from vcd               import VCDWriter
from vcd.gtkw          import GTKWSave

class IntegratedLogicAnalyzer(Elaboratable):
    """ Super-simple integrated-logic-analyzer generator class for LUNA.

    Attributes
    ----------
    trigger: Signal(), input
        A strobe that determines when we should start sampling.
    sampling: Signal(), output
        Indicates when sampling is in progress.

    complete: Signal(), output
        Indicates when sampling is complete and ready to be read.

    captured_sample_number: Signal(), input
        Selects which sample the ILA will output. Effectively the address for the ILA's
        sample buffer.
    captured_sample: Signal(), output
        The sample corresponding to the relevant sample number.
        Can be broken apart by using Cat(*signals).

    Parameters
    ----------
    signals: iterable of Signals
        An iterable of signals that should be captured by the ILA.
    sample_depth: int
        The depth of the desired buffer, in samples.

    domain: string
        The clock domain in which the ILA should operate.
    sample_rate: float
        Cosmetic indication of the sample rate. Used to format output.
    samples_pretrigger: int
        The number of our samples which should be captured _before_ the trigger.
        This also can act like an implicit synchronizer; so asynchronous inputs
        are allowed if this number is >= 2. Note that the trigger strobe is read
        on the rising edge of the clock.
    """

    def __init__(self, *, signals, sample_depth, domain="sync", sample_rate=60e6, samples_pretrigger=1, timestamp_bits=24):
        self.domain             = domain
        self.signals            = signals
        self.inputs             = Cat(*signals)
        self.sample_width       = len(self.inputs) + timestamp_bits
        self.sample_depth       = sample_depth
        self.samples_pretrigger = samples_pretrigger
        self.sample_rate        = sample_rate
        self.sample_period      = 1 / sample_rate
        self.timestamp_bits     = timestamp_bits
        self.timestamp_max      = 2**timestamp_bits - 1
        self.timestamp          = Signal(timestamp_bits, reset=0)

        #
        # Create a backing store for our samples.
        #
        self.mem = Memory(width=self.sample_width, depth=sample_depth, name="ila_buffer")


        #
        # I/O port
        #
        self.trigger  = Signal()
        self.sampling = Signal()
        self.complete = Signal()

        self.captured_sample_number = Signal(range(0, self.sample_depth))
        self.captured_sample        = Signal(self.sample_width)


    def elaborate(self, platform):
        m  = Module()

        # Memory ports.
        write_port = self.mem.write_port()
        read_port  = self.mem.read_port(domain="sync")
        m.submodules += [write_port, read_port]

        # If necessary, create synchronized versions of the relevant signals.
        if self.samples_pretrigger >= 2:
            delayed_inputs = Signal.like(self.inputs)
            m.submodules += FFSynchronizer(self.inputs,  delayed_inputs,
                stages=self.samples_pretrigger)
        elif self.samples_pretrigger == 1:
            delayed_inputs = Signal.like(self.inputs)
            m.d.sync += delayed_inputs.eq(self.inputs)
        else:
            delayed_inputs  = self.inputs

        # Counter that keeps track of our write position.
        write_position = Signal(range(0, self.sample_depth))

        last_inputs = Signal.like(self.inputs)
        m.d.sync += last_inputs.eq(delayed_inputs)

        # Set up our write port to capture the input signals,
        # and our read port to provide the output.
        m.d.comb += [
            write_port.data        .eq(Cat(last_inputs, self.timestamp)),
            write_port.addr        .eq(write_position),

            self.captured_sample   .eq(read_port.data),
            read_port.addr         .eq(self.captured_sample_number)
        ]

        # Don't sample unless our FSM asserts our sample signal explicitly.
        m.d.sync += write_port.en.eq(0)

        with m.FSM(name="ila_state") as fsm:

            m.d.comb += self.sampling.eq(~fsm.ongoing("IDLE"))

            # IDLE: wait for the trigger strobe
            with m.State('IDLE'):

                with m.If(self.trigger):
                    m.next = 'SAMPLE'

                    # Grab a sample as our trigger is asserted.
                    m.d.sync += [
                        write_port.en  .eq(1),
                        write_position .eq(0),
                        self.timestamp .eq(0),

                        self.complete  .eq(0),
                    ]

            # SAMPLE: do our sampling
            with m.State('SAMPLE'):

                m.d.sync += self.timestamp.eq(self.timestamp + 1),

                with m.If(last_inputs != delayed_inputs):
                    # Write any unique samples. Timestamp always increases.
                    m.d.sync += [
                        write_port.en  .eq(1),
                        write_position .eq(write_position + 1),
                    ]
                with m.Else():
                    m.d.sync += write_port.en.eq(0),

                # If this is the last sample, we're done. Finish up.
                with m.If((write_position + 1 == self.sample_depth) |
                          (self.timestamp == self.timestamp_max)):
                    m.next = "IDLE"

                    m.d.sync += [
                        self.complete .eq(1),
                        write_port.en .eq(0)
                    ]


        # Convert our sync domain to the domain requested by the user, if necessary.
        if self.domain != "sync":
            m = DomainRenamer(self.domain)(m)

        return m

class StreamILA(Elaboratable):
    """ Super-simple ILA that outputs its samples over a Stream.
    Create a receiver for this object by calling apollo.ila_receiver_for(<this>).

    This protocol is simple: we wait for a trigger; and then broadcast our samples.
    We broadcast one buffer of samples per each subsequent trigger.

    Attributes
    ----------
    trigger: Signal(), input
        A strobe that determines when we should start sampling.
    sampling: Signal(), output
        Indicates when sampling is in progress.
    complete: Signal(), output
        Indicates when sampling is complete and ready to be read.

    stream: output stream
        Stream output for the ILA.

    Parameters
    ----------
    signals: iterable of Signals
        An iterable of signals that should be captured by the ILA.
    sample_depth: int
        The depth of the desired buffer, in samples.

    domain: string
        The clock domain in which the ILA should operate.
    samples_pretrigger: int
        The number of our samples which should be captured _before_ the trigger.
        This also can act like an implicit synchronizer; so asynchronous inputs
        are allowed if this number is >= 2.
    """

    def __init__(self, *, signals, sample_depth, **kwargs):
        # Extract the domain from our keyword arguments, and then translate it to sync
        # before we pass it back below. We'll use a DomainRenamer at the boundary to
        # handle non-sync domains.
        self.domain = kwargs.get('domain', 'sync')
        kwargs['domain'] = 'sync'

        # Create our core integrated logic analyzer.
        self.ila = IntegratedLogicAnalyzer(
            signals=signals,
            sample_depth=sample_depth,
            **kwargs)

        # Copy some core parameters from our inner ILA.
        self.signals        = signals
        self.sample_width   = self.ila.sample_width
        self.sample_depth   = self.ila.sample_depth
        self.sample_rate    = self.ila.sample_rate
        self.sample_period  = self.ila.sample_period
        self.timestamp_bits = self.ila.timestamp_bits

        # Bolster our bits per sample "word" up to a power of two.
        self.bits_per_sample = 2 ** ((self.ila.sample_width - 1).bit_length())
        self.bytes_per_sample = (self.bits_per_sample + 7) // 8

        #
        # I/O port
        #
        self.stream  = StreamInterface(payload_width=self.bits_per_sample)
        self.trigger = Signal()


        # Expose our ILA's trigger and status ports directly.
        self.sampling = self.ila.sampling
        self.complete = self.ila.complete


    def elaborate(self, platform):
        m  = Module()
        m.submodules.ila = ila = self.ila

        in_domain_stream = self.stream

        # Count where we are in the current transmission.
        current_sample_number = Signal(range(0, ila.sample_depth))

        # Always present the current sample number to our ILA, and the current
        # sample value to the UART.
        m.d.comb += [
            ila.captured_sample_number  .eq(current_sample_number),
        ]

        # Add a synchronization word as a 'fake' first sample.
        with m.If(current_sample_number == 0):
            m.d.comb += in_domain_stream.payload.eq(0xDEADBEEF)
        with m.Else():
            m.d.comb += in_domain_stream.payload.eq(ila.captured_sample)

        with m.FSM():

            # IDLE -- we're currently waiting for a trigger before capturing samples.
            with m.State("IDLE"):

                # Always allow triggering, as we're ready for the data.
                m.d.comb += self.ila.trigger.eq(self.trigger)

                # Once we're triggered, move onto the SAMPLING state.
                with m.If(self.trigger):
                    m.next = "SAMPLING"


            # SAMPLING -- the internal ILA is sampling; we're now waiting for it to
            # complete. This state is similar to IDLE; except we block triggers in order
            # to cleanly avoid a race condition.
            with m.State("SAMPLING"):

                # Once our ILA has finished sampling, prepare to read out our samples.
                with m.If(self.ila.complete):
                    m.d.sync += [
                        current_sample_number  .eq(0),
                        in_domain_stream.first      .eq(1)
                    ]
                    m.next = "SENDING"


            # SENDING -- we now have a valid buffer of samples to send up to the host;
            # we'll transmit them over our stream interface.
            with m.State("SENDING"):
                data_valid = Signal(reset=1)
                m.d.comb += [
                    # While we're sending, we're always providing valid data to the UART.
                    in_domain_stream.valid  .eq(data_valid),

                    # Indicate when we're on the last sample.
                    in_domain_stream.last   .eq(current_sample_number == (self.sample_depth - 1))
                ]

                # Each time the UART accepts a valid word, move on to the next one.
                with m.If(in_domain_stream.ready):
                    with m.If(data_valid):
                        m.d.sync += [
                            current_sample_number   .eq(current_sample_number + 1),
                            data_valid              .eq(0),
                            in_domain_stream.first  .eq(0)
                        ]

                        # If this was the last sample, we're done! Move back to idle.
                        with m.If(in_domain_stream.last):
                            m.next = "IDLE"
                    with m.Else():
                        m.d.sync += data_valid.eq(1)

        # Convert our sync domain to the domain requested by the user, if necessary.
        if self.domain != "sync":
            m = DomainRenamer(self.domain)(m)

        return m


class AsyncSerialILA(Elaboratable):
    """ Super-simple ILA that reads samples out over a UART connection.
    Create a receiver for this object by calling apollo_fpga.ila_receiver_for(<this>).

    This protocol is simple: we wait for a trigger; and then broadcast our samples.
    We broadcast one buffer of samples per each subsequent trigger.

    Attributes
    ----------
    trigger: Signal(), input
        A strobe that determines when we should start sampling.
    sampling: Signal(), output
        Indicates when sampling is in progress.
    complete: Signal(), output
        Indicates when sampling is complete and ready to be read.

    tx: Signal(), output
        Serial output for the ILA.

    Parameters
    ----------
    signals: iterable of Signals
        An iterable of signals that should be captured by the ILA.
    sample_depth: int
        The depth of the desired buffer, in samples.

    divisor: int
        The number of `sync` clock cycles per bit period.

    domain: string
        The clock domain in which the ILA should operate.
    samples_pretrigger: int
        The number of our samples which should be captured _before_ the trigger.
        This also can act like an implicit synchronizer; so asynchronous inputs
        are allowed if this number is >= 2.
    """

    def __init__(self, *, signals, sample_depth, divisor, **kwargs):
        self.divisor = divisor

        #
        # I/O port
        #
        self.tx      = Signal()

        # Extract the domain from our keyword arguments, and then translate it to sync
        # before we pass it back below. We'll use a DomainRenamer at the boundary to
        # handle non-sync domains.
        self.domain = kwargs.get('domain', 'sync')
        kwargs['domain'] = 'sync'

        # Create our core integrated logic analyzer.
        self.ila = StreamILA(
            signals=signals,
            sample_depth=sample_depth,
            **kwargs)

        # Copy some core parameters from our inner ILA.
        self.signals          = signals
        self.sample_width     = self.ila.sample_width
        self.sample_depth     = self.ila.sample_depth
        self.sample_rate      = self.ila.sample_rate
        self.sample_period    = self.ila.sample_period
        self.bits_per_sample  = self.ila.bits_per_sample
        self.bytes_per_sample = self.ila.bytes_per_sample
        self.timestamp_bits   = self.ila.timestamp_bits

        # Expose our ILA's trigger and status ports directly.
        self.trigger  = self.ila.trigger
        self.sampling = self.ila.sampling
        self.complete = self.ila.complete


    def elaborate(self, platform):
        m  = Module()
        m.submodules.ila = ila = self.ila

        # Create our UART transmitter, and connect it to our stream interface.
        m.submodules.uart = uart = UARTMultibyteTransmitter(
            byte_width=self.bytes_per_sample,
            divisor=self.divisor
        )
        m.d.comb +=[
            uart.stream  .stream_eq(ila.stream),
            self.tx      .eq(uart.tx)
        ]


        # Convert our sync domain to the domain requested by the user, if necessary.
        if self.domain != "sync":
            m = DomainRenamer({"sync": self.domain})(m)

        return m

class ILAFrontend(metaclass=ABCMeta):
    """ Class that communicates with an ILA module and emits useful output. """

    def __init__(self, ila):
        """
        Parameters:
            ila -- The ILA object to work with.
        """
        self.ila = ila
        self.samples = None


    @abstractmethod
    def _read_samples(self):
        """ Read samples from the target ILA. Should return an iterable of samples. """


    def _parse_sample(self, raw_sample):
        """ Converts a single binary sample to a dictionary of names -> sample values. """

        position = 0
        sample   = {}

        # Split our raw, bits(0) signal into smaller slices, and associate them with their names.
        for signal in self.ila.signals:
            signal_width = len(signal)
            signal_bits  = raw_sample[position : position + signal_width]
            position += signal_width

            sample[signal.name] = signal_bits

        sample["__timestamp"] = raw_sample[position : position + self.ila.timestamp_bits]

        return sample


    def _parse_samples(self, raw_samples):
        """ Converts raw, binary samples to dictionaries of name -> sample. """
        return [self._parse_sample(sample) for sample in raw_samples]


    def refresh(self):
        """ Fetches the latest set of samples from the target ILA. """
        self.samples = self._parse_samples(self._read_samples())


    def enumerate_samples(self):
        """ Returns an iterator that returns pairs of (timestamp, sample). """

        # If we don't have any samples, fetch samples from the ILA.
        if self.samples is None:
            self.refresh()

        # Iterate over each sample...
        for sample in self.samples:
            yield (self.ila.sample_period * sample["__timestamp"].to_int()), sample


    def print_samples(self):
        """ Simple method that prints each of our samples; for simple CLI debugging."""

        for timestamp, sample in self.enumerate_samples():
            timestamp_scaled = 1000000 * timestamp
            print(f"{timestamp_scaled:08f}us: {sample}")



    def emit_vcd(self, filename, *, gtkw_filename=None, add_clock=True):
        """ Emits a VCD file containing the ILA samples.

        Parameters:
            filename      -- The filename to write to, or '-' to write to stdout.
            gtkw_filename -- If provided, a gtkwave save file will be generated that
                             automatically displays all of the relevant signals in the
                             order provided to the ILA.
            add_clock     -- If true or not provided, adds a replica of the ILA's sample
                             clock to make change points easier to see.
        """

        # Select the file-like object we're working with.
        if filename == "-":
            stream = sys.stdout
            close_after = False
        else:
            stream = open(filename, 'w')
            close_after = True

        # Create our basic VCD.
        with VCDWriter(stream, timescale=f"1 ns", date='today') as writer:
            first_timestamp = math.inf
            last_timestamp  = 0

            signals = {}

            # If we're adding a clock...
            if add_clock:
                clock_value  = 1
                clock_signal = writer.register_var('ila', 'ila_clock', 'integer', size=1, init=clock_value ^ 1)

            # Create named values for each of our signals.
            for signal in self.ila.signals:
                signals[signal.name] = writer.register_var('ila', signal.name, 'integer', size=len(signal))

            signals["__timestamp"] = writer.register_var(
                    'ila', '__timestamp', 'integer', size=self.ila.timestamp_bits)

            # Dump the each of our samples into the VCD.
            clock_time = 0
            l_timestamp = 0
            for timestamp, sample in self.enumerate_samples():
                if timestamp < l_timestamp:
                    continue
                l_timestamp = timestamp
                for signal_name, signal_value in sample.items():

                    # If we're adding a clock signal, add any changes necessary since
                    # the last value-change.
                    if add_clock:
                        while clock_time < timestamp:
                            writer.change(clock_signal, clock_time / 1e-9, clock_value)

                            clock_value ^= 1
                            clock_time  += (self.ila.sample_period / 2)

                    # Register the signal change.
                    writer.change(signals[signal_name], timestamp / 1e-9, signal_value.to_int())


        # If we're generating a GTKW, delegate that to our helper function.
        if gtkw_filename:
            assert(filename != '-')
            self._emit_gtkw(gtkw_filename, filename, add_clock=add_clock)


    def _emit_gtkw(self, filename, dump_filename, *, add_clock=True):
        """ Emits a GTKWave save file to accompany a generated VCD.

        Parameters:
            filename      -- The filename to write the GTKW save to.
            dump_filename -- The filename of the VCD that should be opened with this save.
            add_clock     -- True iff a clock signal should be added to the GTKW save.
        """

        with open(filename, 'w') as f:
            gtkw = GTKWSave(f)

            # Comments / context.
            gtkw.comment("Generated by the LUNA ILA.")

            # Add a reference to the dumpfile we're working with.
            gtkw.dumpfile(dump_filename)

            # If we're adding a clock, add it to the top of the view.
            gtkw.trace('ila.ila_clock')

            # Add each of our signals to the file.
            for signal in self.ila.signals:
                gtkw.trace(f"ila.{signal.name}")


    def interactive_display(self, *, add_clock=True):
        """ Attempts to spawn a GTKWave instance to display the ILA results interactively. """

        # Hack: generate files in a way that doesn't trip macOS's fancy guards.
        try:
            vcd_filename = os.path.join(tempfile.gettempdir(), os.urandom(24).hex() + '.vcd')
            gtkw_filename = os.path.join(tempfile.gettempdir(), os.urandom(24).hex() + '.gtkw')

            self.emit_vcd(vcd_filename, gtkw_filename=gtkw_filename)
            subprocess.run(["gtkwave", "-f", vcd_filename, "-a", gtkw_filename])
        finally:
            os.remove(vcd_filename)
            os.remove(gtkw_filename)



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
            sample_length_bits = self.ila.sample_width
            yield bits.from_bytes(raw_sample, length=sample_length_bits,
                                  byteorder='little')


    def _read_samples(self):
        """ Reads a set of ILA samples, and returns them. """

        sample_width_bytes = self.ila.bytes_per_sample
        total_to_read      = self.ila.sample_depth * sample_width_bytes

        # Fetch all of our samples from the given device.
        all_samples = self._port.read(total_to_read)
        return list(self._split_samples(all_samples))
