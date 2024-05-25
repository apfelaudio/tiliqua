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

    top->psram_idle = 1;
    top->psram_write_ready = 1;
    top->psram_read_ready = 1;

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

    while (contextp->time() < sim_time && !contextp->gotFinish()) {
        contextp->timeInc(8333);
        if (mod % 3 == 0) {
            top->clk_hdmi = !top->clk_hdmi;
        }
        if (mod % 2 == 0) {
            top->clk_sync = !top->clk_sync;
        }
        top->eval();
        tfp->dump(contextp->time());
        mod += 1;
    }
    tfp->close();
    return 0;
}
