# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import math
import sys
import unittest

from amaranth              import *
from amaranth.sim          import *
from amaranth.lib          import wiring
from tiliqua               import midi, test_util

from amaranth_soc          import csr
from amaranth_soc.csr      import wishbone

class MidiTests(unittest.TestCase):

    def test_midi(self):

        dut = midi.MidiDecode()

        async def testbench(ctx):
            ctx.set(dut.i.valid,   1)
            ctx.set(dut.i.payload, 0x92)
            await ctx.tick()
            ctx.set(dut.i.payload, 0x48)
            await ctx.tick()
            ctx.set(dut.i.payload, 0x96)
            await ctx.tick()
            p = ctx.get(dut.o.payload)
            self.assertEqual(p.midi_type, midi.MessageType.NOTE_ON)
            self.assertEqual(p.midi_channel, 2)
            self.assertEqual(p.midi_payload.note_on.note, 0x48)
            self.assertEqual(p.midi_payload.note_on.velocity, 0x96)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_midi.vcd", "w")):
            sim.run()
