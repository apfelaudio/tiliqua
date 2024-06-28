`default_nettype none
`timescale 1ns / 1ps

module vtg (
    input  wire          clk_dvi,
    input  wire          clk_dvi5x,
    input  wire          clk_sys,
    output wire          gpdi_clk_p,
    output wire          gpdi_data0_p,
    output wire          gpdi_data1_p,
    output wire          gpdi_data2_p,
    input  wire    [7:0] phy_b,
    output wire          phy_de,
    input  wire    [7:0] phy_g,
    output wire          phy_hsync,
    input  wire    [7:0] phy_r,
    output wire          phy_vsync,
    input  wire          rst_dvi,
    input  wire          rst_dvi5x,
    input  wire          rst_sys,
    output wire   [11:0] vtg_hcount,
    output wire   [11:0] vtg_vcount
);

logic [11:0] sx, sy;
logic hsync, vsync, de;
simple_720p display_inst (
    .clk_pix(clk_dvi),
    .rst_pix(rst_dvi),  // wait for clock lock
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

// DVI signals (8 bits per colour channel)
logic [7:0] dvi_r, dvi_g, dvi_b;
logic dvi_hsync, dvi_vsync, dvi_de;
always_ff @(posedge clk_dvi) begin
    dvi_hsync <= hsync;
    dvi_vsync <= vsync;
    dvi_de <= de;
    dvi_r <= phy_r;
    dvi_g <= phy_g;
    dvi_b <= phy_b;
end

dvi_generator dvi_out (
    .clk_pix(clk_dvi),
    .clk_pix_5x(clk_dvi5x),
    .rst_pix(rst_dvi),
    .de(dvi_de),
    .data_in_ch0(dvi_b),
    .data_in_ch1(dvi_g),
    .data_in_ch2(dvi_r),
    .ctrl_in_ch0({dvi_vsync, dvi_hsync}),
    .ctrl_in_ch1(2'b00),
    .ctrl_in_ch2(2'b00),
    .tmds_ch0_serial(gpdi_data0_p),
    .tmds_ch1_serial(gpdi_data1_p),
    .tmds_ch2_serial(gpdi_data2_p),
    .tmds_clk_serial(gpdi_clk_p),
);

endmodule
