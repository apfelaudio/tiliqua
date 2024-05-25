#!/bin/bash
verilator -Wno-COMBDLY -Wno-CASEINCOMPLETE -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-TIMESCALEMOD -Wno-PINMISSING -cc --exe --build -j 0 -CFLAGS -O3 sim.cpp vectorscope.v lxvid_sim.v
