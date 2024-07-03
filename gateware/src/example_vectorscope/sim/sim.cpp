// A (quite dirty) simulation harness that simulates the vectorscope core
// and uses it to generate some bitmap images and full FST traces for examination.

#include <verilated_fst_c.h>

#include "Vvectorscope.h"
#include "verilated.h"

#include <cmath>

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

int gcd(int a, int b)
{
    int temp;
    while (b != 0)
    {
        temp = a % b;

        a = b;
        b = temp;
    }
    return a;
}

int main(int argc, char** argv) {
    VerilatedContext* contextp = new VerilatedContext;
    contextp->commandArgs(argc, argv);
    Vvectorscope* top = new Vvectorscope{contextp};

    Verilated::traceEverOn(true);
    VerilatedFstC* tfp = new VerilatedFstC;
    top->trace(tfp, 99);  // Trace 99 levels of hierarchy (or see below)
    tfp->open("simx.fst");

    uint64_t sim_time =  75e9; // 75msec is ~ 4 frames

    uint64_t ns_in_s = 1e9;
    uint64_t ns_in_sync_cycle   = ns_in_s /  SYNC_CLK_HZ;
    uint64_t  ns_in_dvi_cycle   = ns_in_s /   DVI_CLK_HZ;
    uint64_t  ns_in_audio_cycle = ns_in_s / AUDIO_CLK_HZ;

    printf("sync domain is: %i KHz (%i ns/cycle)\n",  SYNC_CLK_HZ/1000,  ns_in_sync_cycle);
    printf("pixel clock is: %i KHz (%i ns/cycle)\n",   DVI_CLK_HZ/1000,   ns_in_dvi_cycle);
    printf("audio clock is: %i KHz (%i ns/cycle)\n", AUDIO_CLK_HZ/1000, ns_in_audio_cycle);

    uint64_t clk_gcd = gcd(SYNC_CLK_HZ, DVI_CLK_HZ);
    uint64_t ns_in_gcd = ns_in_s / clk_gcd;
    printf("GCD is: %i KHz (%i ns/cycle)\n", clk_gcd/1000, ns_in_gcd);

    contextp->timeInc(1);
    top->rst = 1;
    top->dvi_rst = 1;
    top->audio_rst = 1;
    top->eval();

    tfp->dump(contextp->time());

    contextp->timeInc(1);
    top->rst = 0;
    top->dvi_rst = 0;
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

    uint32_t im_stride = 3;
    uint8_t *image_data = (uint8_t*)malloc(DVI_H_ACTIVE*DVI_V_ACTIVE*im_stride);
    memset(image_data, 0, DVI_H_ACTIVE*DVI_V_ACTIVE*im_stride);

    uint32_t pmod_clocks = 0;

    uint32_t frames = 0;

    while (contextp->time() < sim_time && !contextp->gotFinish()) {

        uint64_t timestamp_ns = contextp->time() / 1000;

        // DVI clock domain (PHY output simulation to bitmap image)
        if (timestamp_ns % (ns_in_dvi_cycle/2) == 0) {
            top->dvi_clk = !top->dvi_clk;
            if (top->dvi_clk) {
                uint32_t x = top->x;
                uint32_t y = top->y;
                if (x < DVI_H_ACTIVE && y < DVI_V_ACTIVE) {
                    image_data[y*DVI_H_ACTIVE*3 + x*3 + 0] = top->phy_r;
                    image_data[y*DVI_H_ACTIVE*3 + x*3 + 1] = top->phy_g;
                    image_data[y*DVI_H_ACTIVE*3 + x*3 + 2] = top->phy_b;
                }
                if (x == DVI_H_ACTIVE-1 && y == DVI_V_ACTIVE-1) {
                    char name[64];
                    sprintf(name, "frame%02d.bmp", frames);
                    printf("out %s\n", name);
                    stbi_write_bmp(name, DVI_H_ACTIVE, DVI_V_ACTIVE, 3, image_data);
                    ++frames;
                }
            }
        }

        // Sync clock domain (PSRAM read/write simulation)
        if (timestamp_ns % (ns_in_sync_cycle/2) == 0) {
            top->clk = !top->clk;
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

            }
        }

        // Audio clock domain (Audio stimulation)
        if (timestamp_ns % (ns_in_audio_cycle/2) == 0) {
            top->audio_clk = !top->audio_clk;
            if (top->audio_clk) {
                // 256x I2S clock divider
                if (mod_pmod % 256 == 0) {
                    ++pmod_clocks;
                    top->fs_strobe = 1;
                    // audio signals
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

        // Track PSRAM usage to see how close we are to saturation
        if (top->idle == 1) {
            idle_hi += 1;
        } else {
            idle_lo += 1;
        }

        contextp->timeInc(1000);
        top->eval();
        tfp->dump(contextp->time());
        mod += 1;
    }
    printf("RAM bandwidth: idle: %i, !idle: %i, percent_used: %f\n", idle_hi, idle_lo,
            100.0f * (float)idle_lo / (float)(idle_hi + idle_lo));

    tfp->close();
    return 0;
}
