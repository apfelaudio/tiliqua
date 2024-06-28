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

    //uint64_t sim_time = 1000000000000;
    uint64_t sim_time =  400000000000;

    contextp->timeInc(1);
    top->rst = 1;
    top->hdmi_rst = 1;
    top->audio_rst = 1;
    top->eval();

    tfp->dump(contextp->time());

    contextp->timeInc(1);
    top->rst = 0;
    top->hdmi_rst = 0;
    top->audio_rst = 0;
    top->eval();

    tfp->dump(contextp->time());

    uint32_t mod = 0;
    uint32_t mod_pmod;

    uint64_t idle_lo = 0;
    uint64_t idle_hi = 0;

    uint32_t psram_size_bytes = 1024*1024*16;
    uint8_t *psram_data = (uint8_t*)malloc(psram_size_bytes);
    memset(psram_data, 0, psram_size_bytes);

    uint32_t imx = 720;
    uint32_t imy = 720;
    uint32_t im_stride = 3;
    uint8_t *image_data = (uint8_t*)malloc(imx*imy*im_stride);
    memset(image_data, 0, imx*imy*im_stride);

    uint32_t pmod_clocks = 0;

    uint32_t frames = 0;

    while (contextp->time() < sim_time && !contextp->gotFinish()) {
        if (mod % 2 == 0) {

            top->hdmi_clk = !top->hdmi_clk;
            if (top->hdmi_clk) {
                uint32_t x = top->vtg_hcount;
                uint32_t y = top->vtg_vcount;
                if (x < imx && y < imy) {
                    image_data[y*imx*3 + x*3 + 0] = top->phy_r;
                    image_data[y*imx*3 + x*3 + 1] = top->phy_g;
                    image_data[y*imx*3 + x*3 + 2] = top->phy_b;
                }
                if (x == imx-1 && y == imy-1) {
                    char name[64];
                    sprintf(name, "frame%02d.bmp", frames);
                    printf("out %s\n", name);
                    stbi_write_bmp(name, imx, imy, 3, image_data);
                    ++frames;
                }
            }

            top->clk = !top->clk;
            top->audio_clk = !top->audio_clk;

            if (top->clk) {

                // Probably incorrect ram r/w timing is causing the visual shift
                // Switch these assignments to use internal comb do_read / do_write?
                // put these inside the ram simulation component

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

                if (mod_pmod % 312 == 0) {
                    ++pmod_clocks;
                    top->fs_strobe = 1;
                    top->inject0 = (int16_t)20000.0*sin((float)pmod_clocks / 6000.0);
                    top->inject1 = (int16_t)20000.0*cos((float)pmod_clocks /  300.0);
                    // color
                    top->inject3 = (int16_t)20000.0*cos((float)pmod_clocks /  600.0);
                } else {
                    if (top->fs_strobe) {
                        top->fs_strobe = 0;
                    }
                }
                mod_pmod += 1;
            }
        }
        if (top->idle == 1) {
            idle_hi += 1;
        } else {
            idle_lo += 1;
        }
        contextp->timeInc(8333);
        top->eval();
        tfp->dump(contextp->time());
        mod += 1;
    }
    printf("hi: %i, lo: %i, perc: %f\n", idle_hi, idle_lo,
            (float)idle_lo / (float)(idle_hi + idle_lo));

    tfp->close();
    return 0;
}
