`default_nettype none
`timescale 1ns / 1ps

module vtg (
    input  wire          clk_hdmi,
    input  wire          clk_hdmi5x,
    input  wire          clk_sys,
    output wire          gpdi_clk_n,
    output wire          gpdi_clk_p,
    output wire          gpdi_data0_n,
    output wire          gpdi_data0_p,
    output wire          gpdi_data1_n,
    output wire          gpdi_data1_p,
    output wire          gpdi_data2_n,
    output wire          gpdi_data2_p,
    input  wire    [7:0] phy_b,
    output wire          phy_de,
    input  wire    [7:0] phy_g,
    output wire          phy_hsync,
    input  wire    [7:0] phy_r,
    output wire          phy_vsync,
    input  wire          rst_hdmi,
    input  wire          rst_hdmi5x,
    input  wire          rst_sys,
    output wire   [11:0] vtg_hcount,
    output wire   [11:0] vtg_vcount
);

logic [11:0] sx, sy;
logic hsync, vsync, de;
simple_720p display_inst (
    .clk_pix(clk_hdmi),
    .rst_pix(rst_hdmi),  // wait for clock lock
    .sx,
    .sy,
    .hsync,
    .vsync,
    .de
);

assign phy_de = de;
assign phy_hsync = hsync;
assign phy_vsync = vsync;
assign vtg_hcount = sx;
assign vtg_vcount = sy;

endmodule
