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
    while (contextp->time() < sim_time && !contextp->gotFinish()) {
        contextp->timeInc(1);
        top->eval();
        tfp->dump(contextp->time());
    }
    tfp->close();
    return 0;
}
