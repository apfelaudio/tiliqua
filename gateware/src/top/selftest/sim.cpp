// A (quite dirty) simulation harness that simulates the tiliqua_soc core
// and uses it to generate some full FST traces for examination.

#include <cmath>

#if VM_TRACE_FST == 1
#include <verilated_fst_c.h>
#endif

#include "Vtiliqua_soc.h"
#include "verilated.h"

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

#include <fstream>

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

    uint64_t sim_time =  5000e9;

    uint64_t ns_in_s = 1e9;
    uint64_t ns_in_sync_cycle   = ns_in_s /  SYNC_CLK_HZ;
    uint64_t  ns_in_dvi_cycle   = ns_in_s /   DVI_CLK_HZ;
    uint64_t  ns_in_audio_cycle = ns_in_s / AUDIO_CLK_HZ;
    printf("sync domain is: %i KHz (%i ns/cycle)\n",  SYNC_CLK_HZ/1000,  ns_in_sync_cycle);
    printf("pixel clock is: %i KHz (%i ns/cycle)\n",   DVI_CLK_HZ/1000,   ns_in_dvi_cycle);
    printf("audio clock is: %i KHz (%i ns/cycle)\n", AUDIO_CLK_HZ/1000, ns_in_audio_cycle);

    contextp->timeInc(1);
    top->rst_sync = 1;
    top->rst_dvi  = 1;
    top->rst_audio = 1;
    top->eval();

#if VM_TRACE_FST == 1
    tfp->dump(contextp->time());
#endif

    contextp->timeInc(1);
    top->rst_sync = 0;
    top->rst_dvi = 0;
    top->rst_audio = 0;
    top->eval();

#if VM_TRACE_FST == 1
    tfp->dump(contextp->time());
#endif


    uint32_t spiflash_size_bytes = 1024*1024*32;
    uint32_t spiflash_offset = 0x00100000; // fw base
    char *spiflash_data = (char*)malloc(spiflash_size_bytes);
    memset(spiflash_data, 0, spiflash_size_bytes);

#ifdef SPIFLASH_FW_OFFSET
    std::ifstream fin(FIRMWARE_BIN_PATH, std::ios::in | std::ios::binary);
    fin.read(spiflash_data + SPIFLASH_FW_OFFSET, spiflash_size_bytes);
#endif

    uint32_t psram_size_bytes = 1024*1024*32;
    uint8_t *psram_data = (uint8_t*)malloc(psram_size_bytes);
    memset(psram_data, 0, psram_size_bytes);

#ifdef PSRAM_FW_OFFSET
    std::ifstream fin(FIRMWARE_BIN_PATH, std::ios::in | std::ios::binary);
    fin.read((char*)psram_data + PSRAM_FW_OFFSET, psram_size_bytes);
#endif

    uint32_t im_stride = 3;
    uint8_t *image_data = (uint8_t*)malloc(DVI_H_ACTIVE*DVI_V_ACTIVE*im_stride);
    memset(image_data, 0, DVI_H_ACTIVE*DVI_V_ACTIVE*im_stride);

    uint32_t frames = 0;

    uint32_t mod_pmod;
    uint32_t pmod_clocks = 0;

    while (contextp->time() < sim_time && !contextp->gotFinish()) {

        uint64_t timestamp_ns = contextp->time() / 1000;

        top->spiflash_data = ((uint32_t*)spiflash_data)[top->spiflash_addr];

        // DVI clock domain (PHY output simulation to bitmap image)
        if (timestamp_ns % (ns_in_dvi_cycle/2) == 0) {
            top->clk_dvi = !top->clk_dvi;
            if (top->clk_dvi) {
                uint32_t x = top->dvi_x;
                uint32_t y = top->dvi_y;
                if (x < DVI_H_ACTIVE && y < DVI_V_ACTIVE) {
                    image_data[y*DVI_H_ACTIVE*3 + x*3 + 0] = top->dvi_r;
                    image_data[y*DVI_H_ACTIVE*3 + x*3 + 1] = top->dvi_g;
                    image_data[y*DVI_H_ACTIVE*3 + x*3 + 2] = top->dvi_b;
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

        // Sync clock domain (PSRAM read/write simulation, UART printouts)
        if (timestamp_ns % (ns_in_sync_cycle/2) == 0) {

            top->clk_sync = !top->clk_sync;

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

        // Audio clock domain (Audio stimulation)
        if (timestamp_ns % (ns_in_audio_cycle/2) == 0) {
            top->clk_audio = !top->clk_audio;
            if (top->clk_audio) {
                // 256x I2S clock divider
                if (mod_pmod % 256 == 0) {
                    ++pmod_clocks;
                    top->fs_strobe = 1;
                    /*
                    // audio signals
                    top->fs_inject0 = (int16_t)20000.0*sin((float)pmod_clocks / 6000.0);
                    top->fs_inject1 = (int16_t)20000.0*cos((float)pmod_clocks /  300.0);
                    // color
                    top->fs_inject3 = (int16_t)20000.0*cos((float)pmod_clocks /  600.0);
                    */
                } else {
                    if (top->fs_strobe) {
                        top->fs_strobe = 0;
                    }
                }
                mod_pmod += 1;
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
