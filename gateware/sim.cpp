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
    uint32_t sim_time = 1000000;

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

    while (contextp->time() < sim_time && !contextp->gotFinish()) {
        contextp->timeInc(1);
        top->clk_sync = !top->clk_sync;
        top->clk_hdmi = !top->clk_hdmi;
        top->eval();
        tfp->dump(contextp->time());
    }
    tfp->close();
    return 0;
}
