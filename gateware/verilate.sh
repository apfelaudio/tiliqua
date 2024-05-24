#!/bin/bash
verilator -Wno-COMBDLY -Wno-CASEINCOMPLETE -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-TIMESCALEMOD -Wno-PINMISSING -cc --trace-fst --exe --build -j 0 sim.cpp vectorscope.v lxvid_sim.v
