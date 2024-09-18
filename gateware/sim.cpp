#include <iostream>
#include <fstream>
#include <cmath>

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "src/example_vectorscope/sim/stb_image_write.h"

#include <cxxrtl/cxxrtl_vcd.h>

#include "build/vectorscope.cpp"

using namespace std;

int main()
{
    cxxrtl_design::p_top top;

    // debug_items maps the hierarchical names of signals and memories in the design
    // to a cxxrtl_object (a value, a wire, or a memory)
    cxxrtl::debug_items all_debug_items;
    cxxrtl::debug_scopes all_debug_scopes;

    // Load the debug items of the top down the whole design hierarchy
    top.debug_info(&all_debug_items, &all_debug_scopes, "");

    // vcd_writer is the CXXRTL object that's responsible of creating a string with
    // the VCD file contents.
    cxxrtl::vcd_writer vcd;
    vcd.timescale(1, "us");

    // Here we tell the vcd writer to dump all the signals of the design, except for the
    // memories, to the VCD file.
    //
    // It's not necessary to load all debug objects to the VCD. There is, for example,
    // a  vcd.add(<debug items>, <filter>)) method which allows creating your custom filter to decide
    // what to add and what not. 
    vcd.add_without_memories(all_debug_items);

    std::ofstream waves("waves.vcd");

    top.step();

    // We need to manually tell the VCD writer when sample and write out the traced items.
    // This is only a slight inconvenience and allows for complete flexibilty.
    // E.g. you could only start waveform tracing when an internal signal has reached some specific
    // value etc.
    vcd.sample(0);

    uint64_t ns_in_s = 1e9;
    uint64_t ns_in_sync_cycle   = ns_in_s /  SYNC_CLK_HZ;
    uint64_t  ns_in_dvi_cycle   = ns_in_s /   DVI_CLK_HZ;
    uint64_t  ns_in_audio_cycle = ns_in_s / AUDIO_CLK_HZ;

    printf("sync domain is: %i KHz (%lu ns/cycle)\n",  SYNC_CLK_HZ/1000,  ns_in_sync_cycle);
    printf("pixel clock is: %i KHz (%lu ns/cycle)\n",   DVI_CLK_HZ/1000,   ns_in_dvi_cycle);
    printf("audio clock is: %i KHz (%lu ns/cycle)\n", AUDIO_CLK_HZ/1000, ns_in_audio_cycle);

    uint32_t psram_size_bytes = 1024*1024*16;
    uint8_t *psram_data = (uint8_t*)malloc(psram_size_bytes);
    memset(psram_data, 0, psram_size_bytes);

    uint32_t im_stride = 3;
    uint8_t *image_data = (uint8_t*)malloc(DVI_H_ACTIVE*DVI_V_ACTIVE*im_stride);
    memset(image_data, 0, DVI_H_ACTIVE*DVI_V_ACTIVE*im_stride);
    uint32_t frames = 0;

    uint32_t mod_pmod = 0;
    uint32_t pmod_clocks = 0;

    top.p_rst__dvi.set<bool>(true);
    top.p_rst__sync.set<bool>(true);
    top.p_rst__audio.set<bool>(true);

    top.step();

    top.p_rst__dvi.set<bool>(false);
    top.p_rst__sync.set<bool>(false);
    top.p_rst__audio.set<bool>(false);

    top.step();

    for(uint64_t timestamp_ns=0;timestamp_ns<100000000;++timestamp_ns){

        if (timestamp_ns % (ns_in_dvi_cycle/2) == 0) {
            top.p_clk__dvi.set<bool>(!top.p_clk__dvi.get<bool>());
            if (!top.p_clk__dvi.get<bool>()) {
                uint32_t x = top.p_dvi__x.get<uint32_t>();
                uint32_t y = top.p_dvi__y.get<uint32_t>();
                if (x < DVI_H_ACTIVE && y < DVI_V_ACTIVE) {
                    image_data[y*DVI_H_ACTIVE*3 + x*3 + 0] = top.p_dvi__r.get<uint32_t>();
                    image_data[y*DVI_H_ACTIVE*3 + x*3 + 1] = top.p_dvi__g.get<uint32_t>();
                    image_data[y*DVI_H_ACTIVE*3 + x*3 + 2] = top.p_dvi__b.get<uint32_t>();
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

        if (timestamp_ns % (ns_in_sync_cycle/2) == 0) {
            top.p_clk__sync.set<bool>(!top.p_clk__sync.get<bool>());

            if (!top.p_clk__sync.get<bool>()) {

                if (top.p_read__ready.get<bool>()) {
                    uint32_t aptr = top.p_address__ptr.get<uint32_t>();
                    top.p_read__data__view.set<uint32_t>(
                        (psram_data[aptr+3] << 24)  |
                        (psram_data[aptr+2] << 16)  |
                        (psram_data[aptr+1] << 8)   |
                        (psram_data[aptr+0] << 0)
                    );
                    top.step();
                }

                if (top.p_write__ready.get<bool>()) {
                    uint32_t aptr = top.p_address__ptr.get<uint32_t>();
                    uint32_t wdat = top.p_write__data.get<uint32_t>();
                    psram_data[aptr+0] = (uint8_t)(wdat >> 0);
                    psram_data[aptr+1] = (uint8_t)(wdat >> 8);
                    psram_data[aptr+2] = (uint8_t)(wdat >> 16);
                    psram_data[aptr+3] = (uint8_t)(wdat >> 24);
                    top.step();
                }

            }
        }

        if (timestamp_ns % (ns_in_audio_cycle/2) == 0) {
            top.p_clk__audio.set<bool>(!top.p_clk__audio.get<bool>());
            if (!top.p_clk__audio.get<bool>()) {
                // 256x I2S clock divider
                if (mod_pmod % 256 == 0) {
                    ++pmod_clocks;
                    top.p_fs__strobe.set<bool>(true);
                    // audio signals
                    top.p_fs__inject0.set<int16_t>((int16_t)20000.0*sin((float)pmod_clocks / 6000.0));
                    top.p_fs__inject1.set<int16_t>((int16_t)20000.0*cos((float)pmod_clocks /  300.0));
                    // color
                    top.p_fs__inject3.set<int16_t>((int16_t)20000.0*cos((float)pmod_clocks /  600.0));
                } else {
                    if (top.p_fs__strobe.get<bool>()) {
                        top.p_fs__strobe.set<bool>(false);
                    }
                }
                mod_pmod += 1;
            }
        }

        top.step();
        /*
        vcd.sample(timestamp_ns);
        waves << vcd.buffer;
        vcd.buffer.clear();
        */
    }
}
