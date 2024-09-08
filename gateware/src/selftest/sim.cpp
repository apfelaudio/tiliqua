// A (quite dirty) simulation harness that simulates the tiliqua_soc core
// and uses it to generate some bitmap images and full FST traces for examination.

#include <verilated_fst_c.h>

#include "Vtiliqua_soc.h"
#include "verilated.h"

#include <cmath>

int main(int argc, char** argv) {
    VerilatedContext* contextp = new VerilatedContext;
    contextp->commandArgs(argc, argv);
    Vtiliqua_soc* top = new Vtiliqua_soc{contextp};

    Verilated::traceEverOn(true);
    VerilatedFstC* tfp = new VerilatedFstC;
    top->trace(tfp, 99);  // Trace 99 levels of hierarchy (or see below)
    tfp->open("simx.fst");

    uint64_t sim_time =  75e9; // 75msec is ~ 4 frames

    uint64_t ns_in_s = 1e9;
    uint64_t ns_in_sync_cycle   = ns_in_s /  SYNC_CLK_HZ;
    printf("sync domain is: %i KHz (%i ns/cycle)\n",  SYNC_CLK_HZ/1000,  ns_in_sync_cycle);

    contextp->timeInc(1);
    top->rst = 1;
    top->eval();

    tfp->dump(contextp->time());

    contextp->timeInc(1);
    top->rst = 0;
    top->eval();

    tfp->dump(contextp->time());

    while (contextp->time() < sim_time && !contextp->gotFinish()) {

        uint64_t timestamp_ns = contextp->time() / 1000;

        // Sync clock domain (PSRAM read/write simulation)
        if (timestamp_ns % (ns_in_sync_cycle/2) == 0) {
            top->clk = !top->clk;
            top->eval();
        }
        contextp->timeInc(1000);
        top->eval();
        tfp->dump(contextp->time());
    }
    tfp->close();
    return 0;
}
