#!/bin/bash
verilator -Wno-COMBDLY -Wno-CASEINCOMPLETE -Wno-CASEOVERLAP -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-TIMESCALEMOD -Wno-PINMISSING -cc --trace-fst --exe --build -j 0 sim.cpp nco.v
