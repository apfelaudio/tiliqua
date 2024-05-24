#!/bin/bash
verilator -Wno-COMBDLY -Wno-CASEINCOMPLETE -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-TIMESCALEMOD -Wno-PINMISSING -cc vectorscope.v lxvid_sim.v
