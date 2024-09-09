// A (quite dirty) simulation harness that simulates the tiliqua_soc core
// and uses it to generate some full FST traces for examination.

#if VM_TRACE_FST == 1
#include <verilated_fst_c.h>
#endif

#include "Vtiliqua_soc.h"
#include "verilated.h"

#include <cmath>

int main(int argc, char** argv) {
    VerilatedContext* contextp = new VerilatedContext;
    contextp->commandArgs(argc, argv);
    Vtiliqua_soc* top = new Vtiliqua_soc{contextp};

#if VM_TRACE_FST == 1
    Verilated::traceEverOn(true);
    VerilatedFstC* tfp = new VerilatedFstC;
    top->trace(tfp, 99);  // Trace 99 levels of hierarchy (or see below)
    tfp->open("simx.fst");
#endif

    uint64_t sim_time =  500e9;

    uint64_t ns_in_s = 1e9;
    uint64_t ns_in_sync_cycle   = ns_in_s /  SYNC_CLK_HZ;
    uint64_t  ns_in_dvi_cycle   = ns_in_s /   DVI_CLK_HZ;
    printf("sync domain is: %i KHz (%i ns/cycle)\n",  SYNC_CLK_HZ/1000,  ns_in_sync_cycle);
    printf("pixel clock is: %i KHz (%i ns/cycle)\n",   DVI_CLK_HZ/1000,   ns_in_dvi_cycle);

    contextp->timeInc(1);
    top->rst_sync = 1;
    top->rst_dvi  = 1;
    top->eval();

#if VM_TRACE_FST == 1
    tfp->dump(contextp->time());
#endif

    contextp->timeInc(1);
    top->rst_sync = 0;
    top->rst_dvi = 0;
    top->eval();

#if VM_TRACE_FST == 1
    tfp->dump(contextp->time());
#endif

    uint32_t psram_size_bytes = 1024*1024*16;
    uint8_t *psram_data = (uint8_t*)malloc(psram_size_bytes);
    memset(psram_data, 0, psram_size_bytes);

    while (contextp->time() < sim_time && !contextp->gotFinish()) {

        uint64_t timestamp_ns = contextp->time() / 1000;

        if (timestamp_ns % (ns_in_sync_cycle/2) == 0) {

            top->clk_sync = !top->clk_sync;

            // Sync clock domain (PSRAM read/write simulation)
            if (top->clk_sync) {

                if (top->read_ready) {
                    top->read_data_view =
                        (psram_data[top->address_ptr+3] << 24)  |
                        (psram_data[top->address_ptr+2] << 16)  |
                        (psram_data[top->address_ptr+1] << 8)   |
                        (psram_data[top->address_ptr+0] << 0);
                }

                if (top->write_ready) {
                    psram_data[top->address_ptr+0] = (uint8_t)(top->write_data >> 0);
                    psram_data[top->address_ptr+1] = (uint8_t)(top->write_data >> 8);
                    psram_data[top->address_ptr+2] = (uint8_t)(top->write_data >> 16);
                    psram_data[top->address_ptr+3] = (uint8_t)(top->write_data >> 24);
                }

                top->eval();

                if (top->uart0_w_stb) {
                    putchar(top->uart0_w_data);
                }
            }
        }

        contextp->timeInc(1000);
        top->eval();
#if VM_TRACE_FST == 1
        tfp->dump(contextp->time());
#endif
    }
#if VM_TRACE_FST == 1
    tfp->close();
#endif
    return 0;
}
