// Copyright (c) 2024 S. Holzapfel, apfelaudio UG <info@apfelaudio.com>
//
// SPDX-License-Identifier: CERN-OHL-S-2.0
//

// Simple verilator wrapper for simulating self-contained Tiliqua DSP core.

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
#include <verilated_fst_c.h>
#endif

#include "Vcore.h"
#include "verilated.h"

#include <cmath>

int main(int argc, char** argv) {

    VerilatedContext* contextp = new VerilatedContext;
    contextp->commandArgs(argc, argv);
    Vcore* top = new Vcore{contextp};

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    Verilated::traceEverOn(true);
    VerilatedFstC* tfp = new VerilatedFstC;
    top->trace(tfp, 99);  // Trace 99 levels of hierarchy (or see below)
    tfp->open("simx.fst");
#endif
    uint64_t sim_time =  100000000000;

    contextp->timeInc(1);
    top->rst = 1;
    top->audio_rst = 1;
    top->eval();

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    tfp->dump(contextp->time());
#endif

    contextp->timeInc(1);
    top->rst = 0;
    top->audio_rst = 0;
    top->eval();

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    tfp->dump(contextp->time());
#endif

    uint32_t clkdiv = 0;
    uint32_t n_clk_audio = 0;
    uint32_t n_samples = 0;

    while (contextp->time() < sim_time && !contextp->gotFinish()) {
        // clk_sync  ~= 60MHz
        top->clk = !top->clk;
        // clk_audio ~= 12MHz
        if (clkdiv % 5 == 0) {
            top->audio_clk = !top->audio_clk;
            if (top->audio_clk) {
                if (n_clk_audio % 256 == 0) {
                    top->fs_strobe = 1;
                    /*
                    top->pmod0_sample_i0 = (int16_t)20000.0*sin((float)pmod_clocks / 2000.0);
                    top->pmod0_sample_i1 = (int16_t)20000.0*cos((float)pmod_clocks /   50.0);
                    */
                    //top->__024signal = 1000;
                    top->__024signal = (int16_t)10000.0*sin((float)n_samples / 50.0);
                    top->__024signal__0241 = (int16_t)10000.0*cos((float)n_samples / 10.0);
                    ++n_samples;
                } else {
                    if (top->fs_strobe) {
                        top->fs_strobe = 0;
                    }
                }
                ++n_clk_audio;
            }
        }
        contextp->timeInc(8333);
        top->eval();
#if defined VM_TRACE_FST && VM_TRACE_FST == 1
        tfp->dump(contextp->time());
#endif
        clkdiv += 1;
    }

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    tfp->close();
#endif
    return 0;
}
