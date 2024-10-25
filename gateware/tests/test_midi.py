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

    def test_midi_voice_tracker(self):

        dut = midi.MidiVoiceTracker()

        note_range = list(range(40, 43))

        async def stimulus_notes(ctx):
            """Send some MIDI NOTE_ON events."""
            for note in note_range:
                # FIXME: valid before ready in TBs EVERYWHERE!
                ctx.set(dut.i.valid, 1)
                ctx.set(dut.i.payload.midi_type, midi.MessageType.NOTE_ON)
                ctx.set(dut.i.payload.midi_channel, 1)
                ctx.set(dut.i.payload.midi_payload.note_on.note, note)
                ctx.set(dut.i.payload.midi_payload.note_on.velocity, 0x60)
                await ctx.tick().until(dut.i.ready)
                ctx.set(dut.i.valid, 0)
                await ctx.tick()

        async def testbench(ctx):
            """Check that the NOTE_ON events correspond to voice slots."""
            for n in range(dut.max_voices):
                ctx.set(dut.o[n].ready, 1)
            for ticks in range(100):
                for n in range(dut.max_voices):
                    o_sample = ctx.get(dut.o[n].valid & dut.o[n].ready)
                    if o_sample:
                        note_in_slot = ctx.get(dut.o[n].payload.note)
                        print(f"slot{n}:", note_in_slot)
                        # Verify expected notes are in voice slots.
                        if ticks > 50 and n < len(note_range):
                            self.assertEqual(note_in_slot, note_range[n])
                await ctx.tick()

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_process(stimulus_notes)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_midi_voice_tracker.vcd", "w")):
            sim.run()
