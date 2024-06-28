// Project F: FPGA Graphics - Simple 1280x720p60 Display
// (C)2023 Will Green, open source hardware released under the MIT License
// Learn more at https://projectf.io/posts/fpga-graphics/

`default_nettype none
`timescale 1ns / 1ps

module simple_720p (
    input  wire logic clk_pix,   // pixel clock
    input  wire logic rst_pix,   // reset in pixel clock domain
    output      logic [11:0] sx, // horizontal screen position
    output      logic [11:0] sy, // vertical screen position
    output      logic hsync,     // horizontal sync
    output      logic vsync,     // vertical sync
    output      logic de         // data enable (low in blanking interval)
    );

    // horizontal timings
    parameter HA_END = 719;           // end of active pixels
    parameter HS_STA = 759;           // sync starts after front porch
    parameter HS_END = 799;           // sync ends
    parameter LINE   = 999;           // last pixel on line (after back porch)

    // vertical timings
    parameter VA_END = 719;           // end of active pixels
    parameter VS_STA = 743;           // sync starts after front porch
    parameter VS_END = 747;           // sync ends
    parameter SCREEN = 759;           // last line on screen (after back porch)

    always_comb begin
        hsync = (sx >= HS_STA && sx < HS_END);  // negative polarity
        vsync = (sy >= VS_STA && sy < VS_END);  // negative polarity
        de = (sx <= HA_END && sy <= VA_END);
    end

    // calculate horizontal and vertical screen position
    always_ff @(posedge clk_pix) begin
        if (sx == LINE) begin  // last pixel on line?
            sx <= 0;
            sy <= (sy == SCREEN) ? 0 : sy + 1;  // last line on screen?
        end else begin
            sx <= sx + 1;
        end
        if (rst_pix) begin
            sx <= 0;
            sy <= 0;
        end
    end
endmodule