# Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
#

"""Utilities for simulating Tiliqua designs."""

from amaranth              import *
from amaranth.build        import *
from amaranth.lib          import wiring, data

from tiliqua.eurorack_pmod import ASQ

class FakeEurorackPmod(Elaboratable):
    """ Fake EurorackPmod. """

    def __init__(self):
        self.sample_i = Signal(data.ArrayLayout(ASQ, 4))
        self.sample_o = Signal(data.ArrayLayout(ASQ, 4))
        self.sample_inject  = [Signal(ASQ) for _ in range(4)]
        self.sample_extract = [Signal(ASQ) for _ in range(4)]
        self.fs_strobe = Signal()

    def elaborate(self, platform) -> Module:
        m = Module()

        for n in range(4):
            m.d.comb += self.sample_i[n].eq(self.sample_inject[n])
            m.d.comb += self.sample_extract[n].eq(self.sample_o[n])

        return m

class FakeTiliquaDomainGenerator(Elaboratable):
    """ Fake Clock generator for Tiliqua platform. """

    def __init__(self, *, clock_frequencies=None, clock_signal_name=None):
        pass

    def elaborate(self, platform):
        m = Module()

        m.domains.sync   = ClockDomain()
        m.domains.audio  = ClockDomain()
        m.domains.dvi    = ClockDomain()

        return m
