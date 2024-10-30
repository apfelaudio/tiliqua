# Copyright (c) 2024 Seb Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import math
import sys
import unittest

from amaranth              import *
from amaranth.sim          import *

from tiliqua.usb_host      import *

class UsbTests(unittest.TestCase):

    def test_usb_host(self):

        dut = DomainRenamer({"usb": "sync"})(
                SimpleUSBHost(sim=True))

        async def testbench(ctx):
            for _ in range(5):
                data = []
                ctx.set(dut.utmi.tx_ready, 1)
                ctx.tick()
                await ctx.tick().until(dut.utmi.tx_valid)
                while ctx.get(dut.utmi.tx_valid):
                    data.append(ctx.get(dut.utmi.tx_data))
                    ctx.tick()
                ctx.set(dut.utmi.tx_ready, 0)
                print("[packet]", data)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open("test_usb_host.vcd", "w")):
            sim.run()
