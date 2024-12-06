// Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
//
// SPDX-License-Identifier: CERN-OHL-S-2.0
//

// Simple verilator wrapper for simulating self-contained Tiliqua DSP core.

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
#include <verilated_fst_c.h>
#endif

#include "Vtiliqua_soc.h"
#include "verilated.h"

#include <cmath>

int main(int argc, char** argv) {

    VerilatedContext* contextp = new VerilatedContext;
    contextp->commandArgs(argc, argv);
    Vtiliqua_soc* top = new Vtiliqua_soc{contextp};

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    Verilated::traceEverOn(true);
    VerilatedFstC* tfp = new VerilatedFstC;
    top->trace(tfp, 99);  // Trace 99 levels of hierarchy (or see below)
    tfp->open("simx.fst");
#endif
    uint64_t sim_time =  100000000000;

    contextp->timeInc(1);
    top->rst_sync = 1;
    top->rst_audio = 1;
    top->rst_fast = 1;
    top->eval();

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    tfp->dump(contextp->time());
#endif

    contextp->timeInc(1);
    top->rst_sync = 0;
    top->rst_audio = 0;
    top->rst_fast = 0;
    top->eval();

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    tfp->dump(contextp->time());
#endif

    uint64_t ns_in_s = 1e9;
    uint64_t ns_in_sync_cycle   = ns_in_s /  SYNC_CLK_HZ;
    uint64_t  ns_in_audio_cycle = ns_in_s / AUDIO_CLK_HZ;
    uint64_t  ns_in_fast_cycle  = ns_in_s / FAST_CLK_HZ;

    printf("sync domain is: %i KHz (%i ns/cycle)\n",  SYNC_CLK_HZ/1000,  ns_in_sync_cycle);
    printf("audio clock is: %i KHz (%i ns/cycle)\n", AUDIO_CLK_HZ/1000, ns_in_audio_cycle);
    printf("fast clock is: %i KHz (%i ns/cycle)\n", FAST_CLK_HZ/1000, ns_in_fast_cycle);

#ifdef PSRAM_SIM
    uint32_t psram_size_bytes = 1024*1024*16;
    uint8_t *psram_data = (uint8_t*)malloc(psram_size_bytes);
    memset(psram_data, 0, psram_size_bytes);

    uint64_t idle_lo = 0;
    uint64_t idle_hi = 0;
#endif

    uint32_t mod = 0;
    uint32_t mod_pmod;
    uint32_t pmod_clocks = 0;

    while (contextp->time() < sim_time && !contextp->gotFinish()) {

        uint64_t timestamp_ns = contextp->time() / 1000;

        // Sync clock domain (PSRAM read/write simulation)
        if (timestamp_ns % (ns_in_sync_cycle/2) == 0) {
            top->clk_sync = !top->clk_sync;
#ifdef PSRAM_SIM
            if (top->clk_sync) {
                if (top->read_ready) {
                    top->read_data_view =
                        (psram_data[top->address_ptr+3] << 24)  |
                        (psram_data[top->address_ptr+2] << 16)  |
                        (psram_data[top->address_ptr+1] << 8)   |
                        (psram_data[top->address_ptr+0] << 0);
                    /*
                    if (top->read_data_view != 0) {
                        printf("read %x@%x\n", top->read_data_view, top->address_ptr);
                    }
                    */
                    top->eval();
                }

                if (top->write_ready) {
                    psram_data[top->address_ptr+0] = (uint8_t)(top->write_data >> 0);
                    psram_data[top->address_ptr+1] = (uint8_t)(top->write_data >> 8);
                    psram_data[top->address_ptr+2] = (uint8_t)(top->write_data >> 16);
                    psram_data[top->address_ptr+3] = (uint8_t)(top->write_data >> 24);
                    //printf("write %x@%x\n", top->write_data, top->address_ptr);
                    top->eval();
                }

            }
#endif
        }


        // Audio clock domain (Audio stimulation)
        if (timestamp_ns % (ns_in_audio_cycle/2) == 0) {
            top->clk_audio = !top->clk_audio;
            if (top->clk_audio) {
                // 256x I2S clock divider
                if (mod_pmod % 256 == 0) {
                    ++pmod_clocks;
                    top->fs_strobe = 1;
                    // audio signals
                    top->fs_inject0 = (int16_t)10000.0*sin((float)pmod_clocks / 50.0);
                    top->fs_inject1 = (int16_t)10000.0*cos((float)pmod_clocks / 10.0);
                    top->fs_inject2 = (int16_t)10000.0*sin((float)pmod_clocks / 30.0);
                    top->fs_inject3 = (int16_t)10000.0*cos((float)pmod_clocks / 5.0);
                } else {
                    if (top->fs_strobe) {
                        top->fs_strobe = 0;
                    }
                }
                mod_pmod += 1;
            }
        }

        // Fast clock domain (RAM domain simulation)
        if (timestamp_ns % (ns_in_fast_cycle/2) == 0) {
            top->clk_fast = !top->clk_fast;
        }

#ifdef PSRAM_SIM
        // Track PSRAM usage to see how close we are to saturation
        if (top->idle == 1) {
            idle_hi += 1;
        } else {
            idle_lo += 1;
        }
#endif

        contextp->timeInc(1000);
        top->eval();
#if defined VM_TRACE_FST && VM_TRACE_FST == 1
        tfp->dump(contextp->time());
#endif
    }

#ifdef PSRAM_SIM
    printf("RAM bandwidth: idle: %i, !idle: %i, percent_used: %f\n", idle_hi, idle_lo,
            100.0f * (float)idle_lo / (float)(idle_hi + idle_lo));
#endif

#if defined VM_TRACE_FST && VM_TRACE_FST == 1
    tfp->close();
#endif
    return 0;
}
