#include <verilated_fst_c.h>

#include "Vvectorscope.h"
#include "verilated.h"

int main(int argc, char** argv) {
    VerilatedContext* contextp = new VerilatedContext;
    contextp->commandArgs(argc, argv);
    Vvectorscope* top = new Vvectorscope{contextp};
    Verilated::traceEverOn(true);
    VerilatedFstC* tfp = new VerilatedFstC;
    top->trace(tfp, 99);  // Trace 99 levels of hierarchy (or see below)
    tfp->open("simx.fst");
    uint64_t sim_time = 100000000000;

    contextp->timeInc(1);
    top->rst_sync = 1;
    top->rst_hdmi = 1;
    top->eval();
    tfp->dump(contextp->time());

    contextp->timeInc(1);
    top->rst_sync = 0;
    top->rst_hdmi = 0;
    top->eval();
    tfp->dump(contextp->time());

    uint32_t mod = 0;
    uint32_t mod_pmod;

    uint64_t idle_lo = 0;
    uint64_t idle_hi = 0;

    uint8_t *psram_data = (uint8_t*)malloc(1024*1024*16);
    memset(psram_data, 0xff, 1024*1024*16);

    while (contextp->time() < sim_time && !contextp->gotFinish()) {
        contextp->timeInc(8333);
        if (mod % 3 == 0) {
            top->clk_hdmi = !top->clk_hdmi;
        }
        if (mod % 2 == 0) {
            top->clk_sync = !top->clk_sync;

            if (top->clk_sync) {

                top->psram_read_data_view =
                    ((uint32_t)psram_data[top->psram_address_ptr+0] << 0)  |
                    ((uint32_t)psram_data[top->psram_address_ptr+1] << 8)  |
                    ((uint32_t)psram_data[top->psram_address_ptr+2] << 16) |
                    ((uint32_t)psram_data[top->psram_address_ptr+3] << 24);

                if (top->psram_write_ready) {
                    psram_data[top->psram_address_ptr+0] = top->psram_write_data & 0x000000ff >> 0;
                    psram_data[top->psram_address_ptr+1] = top->psram_write_data & 0x0000ff00 >> 8;
                    psram_data[top->psram_address_ptr+2] = top->psram_write_data & 0x00ff0000 >> 16;
                    psram_data[top->psram_address_ptr+3] = top->psram_write_data & 0xff000000 >> 24;
                }

                if (mod_pmod % 312 == 0) {
                    top->pmod0_fs_strobe = 1;
                    // TODO
                    top->pmod0_sample_i0 = 0;
                    top->pmod0_sample_i1 = 0;
                } else {
                    if (top->pmod0_fs_strobe) {
                        top->pmod0_fs_strobe = 0;
                    }
                }
                mod_pmod += 1;
            }
        }
        if (top->psram_idle == 1) {
            idle_hi += 1;
        } else {
            idle_lo += 1;
        }
        top->eval();
        tfp->dump(contextp->time());
        mod += 1;
    }
    printf("hi: %i, lo: %i, perc: %f\n", idle_hi, idle_lo,
            (float)idle_lo / (float)(idle_hi + idle_lo));
    tfp->close();
    return 0;
}
