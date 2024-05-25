#include <verilated_fst_c.h>

#include "Vvectorscope.h"
#include "verilated.h"

#include <cmath>

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

int main(int argc, char** argv) {
    VerilatedContext* contextp = new VerilatedContext;
    contextp->commandArgs(argc, argv);
    Vvectorscope* top = new Vvectorscope{contextp};
    Verilated::traceEverOn(true);
    VerilatedFstC* tfp = new VerilatedFstC;
    top->trace(tfp, 99);  // Trace 99 levels of hierarchy (or see below)
    tfp->open("simx.fst");
    uint64_t sim_time = 50000000000;

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

    uint32_t imx = 720;
    uint32_t imy = 720;
    uint8_t *image_data = (uint8_t*)malloc(imx*imy*3);
    memset(image_data, 0, 720*720*3);

    uint32_t pmod_clocks = 0;

    while (contextp->time() < sim_time && !contextp->gotFinish()) {
        contextp->timeInc(8333);
        if (mod % 3 == 0) {
            top->clk_hdmi = !top->clk_hdmi;
            if (top->clk_hdmi) {
                uint32_t x = top->video_hcount;
                uint32_t y = top->video_vcount;
                if (x < imx && y < imy) {
                    image_data[y*imx*3 + x*3 + 0] = top->video_r;
                    image_data[y*imx*3 + x*3 + 1] = top->video_g;
                    image_data[y*imx*3 + x*3 + 2] = top->video_b;
                }
            }
        }
        if (mod % 2 == 0) {
            top->clk_sync = !top->clk_sync;

            if (top->clk_sync) {

                top->psram_read_data_view =
                    (psram_data[top->psram_address_ptr+0] << 24)  |
                    (psram_data[top->psram_address_ptr+1] << 16)  |
                    (psram_data[top->psram_address_ptr+2] << 8)   |
                    (psram_data[top->psram_address_ptr+3] << 0);

                // Probably incorrect ram r/w timing is causing the visual shift

                if (top->psram_write_ready) {
                    psram_data[top->psram_address_ptr+3] = (uint8_t)(top->psram_write_data >> 0);
                    psram_data[top->psram_address_ptr+2] = (uint8_t)(top->psram_write_data >> 8);
                    psram_data[top->psram_address_ptr+1] = (uint8_t)(top->psram_write_data >> 16);
                    psram_data[top->psram_address_ptr+0] = (uint8_t)(top->psram_write_data >> 24);
                    //printf("%x\n", top->psram_address_ptr);
                }

                if (mod_pmod % 312 == 0) {
                    ++pmod_clocks;
                    top->pmod0_fs_strobe = 1;
                    top->pmod0_sample_i0 = (int16_t)5000.0*sin((float)pmod_clocks / 100.0);
                    top->pmod0_sample_i1 = (int16_t)5000.0*cos((float)pmod_clocks / 100.0);
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

    stbi_write_bmp("out.bmp", imx, imy, 3, image_data);

    tfp->close();
    return 0;
}
