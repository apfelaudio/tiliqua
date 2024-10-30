# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import math
import sys
import unittest

from amaranth              import *
from amaranth.sim          import *

from tiliqua.usb_host      import *

from luna.gateware.test.contrib.usb_packet import sof_packet

class UsbTests(unittest.TestCase):

    def test_usb_host(self):

        dut = DomainRenamer({"usb": "sync"})(
                SimpleUSBHost(sim=True))

        async def testbench(ctx):
            for i in range(1, 5):
                data = []
                ctx.set(dut.utmi.tx_ready, 1)
                while ctx.get(~dut.utmi.tx_valid):
                    await ctx.tick()
                while ctx.get(dut.utmi.tx_valid):
                    data.append(int(ctx.get(dut.utmi.tx_data)))
                    await ctx.tick()
                ctx.set(dut.utmi.tx_ready, 0)
                print("[packet]", [hex(d) for d in data])
                bs = ("{0:08b}".format(data[0])[::-1] +
                      "{0:08b}".format(data[1])[::-1] +
                      "{0:08b}".format(data[2])[::-1])
                print("[ref]", sof_packet(i))
                print("[got]", bs)
                self.assertEqual(bs, sof_packet(i))

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_usb_host.vcd", "w")):
            sim.run()
