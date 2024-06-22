#!/bin/bash
verilator -Wno-COMBDLY -Wno-CASEINCOMPLETE -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-TIMESCALEMOD -Wno-PINMISSING -cc --trace-fst --exe --build -j 0 sim.cpp vectorscope.v src/example_vectorscope/vtg.sv src/example_vectorscope/simple_720p.sv src/example_vectorscope/tmds_encoder_dvi.sv src/example_vectorscope/dvi_generator_sim.sv
