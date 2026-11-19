// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2.1 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
`timescale 1ns/1ps
(* DowngradeIPIdentifiedWarnings="yes" *) module conv_layer_control_s_axi
#(parameter
    C_S_AXI_ADDR_WIDTH = 8,
    C_S_AXI_DATA_WIDTH = 32
)(
    input  wire                          ACLK,
    input  wire                          ARESET,
    input  wire                          ACLK_EN,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0] AWADDR,
    input  wire                          AWVALID,
    output wire                          AWREADY,
    input  wire [C_S_AXI_DATA_WIDTH-1:0] WDATA,
    input  wire [C_S_AXI_DATA_WIDTH/8-1:0] WSTRB,
    input  wire                          WVALID,
    output wire                          WREADY,
    output wire [1:0]                    BRESP,
    output wire                          BVALID,
    input  wire                          BREADY,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0] ARADDR,
    input  wire                          ARVALID,
    output wire                          ARREADY,
    output wire [C_S_AXI_DATA_WIDTH-1:0] RDATA,
    output wire [1:0]                    RRESP,
    output wire                          RVALID,
    input  wire                          RREADY,
    output wire                          interrupt,
    output wire                          ap_start,
    input  wire                          ap_done,
    input  wire                          ap_ready,
    input  wire                          ap_idle,
    input  wire [31:0]                   ap_return,
    output wire [63:0]                   in_r,
    output wire [63:0]                   out_r,
    output wire [63:0]                   params,
    output wire [31:0]                   mode,
    output wire [31:0]                   cin,
    output wire [31:0]                   cout,
    output wire [31:0]                   in_h,
    output wire [31:0]                   in_w,
    output wire [31:0]                   relu,
    output wire [31:0]                   shift,
    output wire [31:0]                   qmin,
    output wire [31:0]                   qmax,
    output wire [31:0]                   fan_on,
    output wire [31:0]                   fan_xa0,
    output wire [31:0]                   fan_xag,
    output wire [31:0]                   fan_xb0,
    output wire [31:0]                   fan_xbg,
    output wire [31:0]                   fan_y0
);
//------------------------Address Info-------------------
// Protocol Used: ap_ctrl_hs
//
// 0x00 : Control signals
//        bit 0  - ap_start (Read/Write/COH)
//        bit 1  - ap_done (Read/COR)
//        bit 2  - ap_idle (Read)
//        bit 3  - ap_ready (Read/COR)
//        bit 7  - auto_restart (Read/Write)
//        bit 9  - interrupt (Read)
//        others - reserved
// 0x04 : Global Interrupt Enable Register
//        bit 0  - Global Interrupt Enable (Read/Write)
//        others - reserved
// 0x08 : IP Interrupt Enable Register (Read/Write)
//        bit 0 - enable ap_done interrupt (Read/Write)
//        bit 1 - enable ap_ready interrupt (Read/Write)
//        others - reserved
// 0x0c : IP Interrupt Status Register (Read/TOW)
//        bit 0 - ap_done (Read/TOW)
//        bit 1 - ap_ready (Read/TOW)
//        others - reserved
// 0x10 : Data signal of ap_return
//        bit 31~0 - ap_return[31:0] (Read)
// 0x18 : Data signal of in_r
//        bit 31~0 - in_r[31:0] (Read/Write)
// 0x1c : Data signal of in_r
//        bit 31~0 - in_r[63:32] (Read/Write)
// 0x20 : reserved
// 0x24 : Data signal of out_r
//        bit 31~0 - out_r[31:0] (Read/Write)
// 0x28 : Data signal of out_r
//        bit 31~0 - out_r[63:32] (Read/Write)
// 0x2c : reserved
// 0x30 : Data signal of params
//        bit 31~0 - params[31:0] (Read/Write)
// 0x34 : Data signal of params
//        bit 31~0 - params[63:32] (Read/Write)
// 0x38 : reserved
// 0x3c : Data signal of mode
//        bit 31~0 - mode[31:0] (Read/Write)
// 0x40 : reserved
// 0x44 : Data signal of cin
//        bit 31~0 - cin[31:0] (Read/Write)
// 0x48 : reserved
// 0x4c : Data signal of cout
//        bit 31~0 - cout[31:0] (Read/Write)
// 0x50 : reserved
// 0x54 : Data signal of in_h
//        bit 31~0 - in_h[31:0] (Read/Write)
// 0x58 : reserved
// 0x5c : Data signal of in_w
//        bit 31~0 - in_w[31:0] (Read/Write)
// 0x60 : reserved
// 0x64 : Data signal of relu
//        bit 31~0 - relu[31:0] (Read/Write)
// 0x68 : reserved
// 0x6c : Data signal of shift
//        bit 31~0 - shift[31:0] (Read/Write)
// 0x70 : reserved
// 0x74 : Data signal of qmin
//        bit 31~0 - qmin[31:0] (Read/Write)
// 0x78 : reserved
// 0x7c : Data signal of qmax
//        bit 31~0 - qmax[31:0] (Read/Write)
// 0x80 : reserved
// 0x84 : Data signal of fan_on
//        bit 31~0 - fan_on[31:0] (Read/Write)
// 0x88 : reserved
// 0x8c : Data signal of fan_xa0
//        bit 31~0 - fan_xa0[31:0] (Read/Write)
// 0x90 : reserved
// 0x94 : Data signal of fan_xag
//        bit 31~0 - fan_xag[31:0] (Read/Write)
// 0x98 : reserved
// 0x9c : Data signal of fan_xb0
//        bit 31~0 - fan_xb0[31:0] (Read/Write)
// 0xa0 : reserved
// 0xa4 : Data signal of fan_xbg
//        bit 31~0 - fan_xbg[31:0] (Read/Write)
// 0xa8 : reserved
// 0xac : Data signal of fan_y0
//        bit 31~0 - fan_y0[31:0] (Read/Write)
// 0xb0 : reserved
// (SC = Self Clear, COR = Clear on Read, TOW = Toggle on Write, COH = Clear on Handshake)

//------------------------Parameter----------------------
localparam
    ADDR_AP_CTRL        = 8'h00,
    ADDR_GIE            = 8'h04,
    ADDR_IER            = 8'h08,
    ADDR_ISR            = 8'h0c,
    ADDR_AP_RETURN_0    = 8'h10,
    ADDR_IN_R_DATA_0    = 8'h18,
    ADDR_IN_R_DATA_1    = 8'h1c,
    ADDR_IN_R_CTRL      = 8'h20,
    ADDR_OUT_R_DATA_0   = 8'h24,
    ADDR_OUT_R_DATA_1   = 8'h28,
    ADDR_OUT_R_CTRL     = 8'h2c,
    ADDR_PARAMS_DATA_0  = 8'h30,
    ADDR_PARAMS_DATA_1  = 8'h34,
    ADDR_PARAMS_CTRL    = 8'h38,
    ADDR_MODE_DATA_0    = 8'h3c,
    ADDR_MODE_CTRL      = 8'h40,
    ADDR_CIN_DATA_0     = 8'h44,
    ADDR_CIN_CTRL       = 8'h48,
    ADDR_COUT_DATA_0    = 8'h4c,
    ADDR_COUT_CTRL      = 8'h50,
    ADDR_IN_H_DATA_0    = 8'h54,
    ADDR_IN_H_CTRL      = 8'h58,
    ADDR_IN_W_DATA_0    = 8'h5c,
    ADDR_IN_W_CTRL      = 8'h60,
    ADDR_RELU_DATA_0    = 8'h64,
    ADDR_RELU_CTRL      = 8'h68,
    ADDR_SHIFT_DATA_0   = 8'h6c,
    ADDR_SHIFT_CTRL     = 8'h70,
    ADDR_QMIN_DATA_0    = 8'h74,
    ADDR_QMIN_CTRL      = 8'h78,
    ADDR_QMAX_DATA_0    = 8'h7c,
    ADDR_QMAX_CTRL      = 8'h80,
    ADDR_FAN_ON_DATA_0  = 8'h84,
    ADDR_FAN_ON_CTRL    = 8'h88,
    ADDR_FAN_XA0_DATA_0 = 8'h8c,
    ADDR_FAN_XA0_CTRL   = 8'h90,
    ADDR_FAN_XAG_DATA_0 = 8'h94,
    ADDR_FAN_XAG_CTRL   = 8'h98,
    ADDR_FAN_XB0_DATA_0 = 8'h9c,
    ADDR_FAN_XB0_CTRL   = 8'ha0,
    ADDR_FAN_XBG_DATA_0 = 8'ha4,
    ADDR_FAN_XBG_CTRL   = 8'ha8,
    ADDR_FAN_Y0_DATA_0  = 8'hac,
    ADDR_FAN_Y0_CTRL    = 8'hb0,
    WRIDLE              = 2'd0,
    WRDATA              = 2'd1,
    WRRESP              = 2'd2,
    WRRESET             = 2'd3,
    RDIDLE              = 2'd0,
    RDDATA              = 2'd1,
    RDRESET             = 2'd2,
    ADDR_BITS                = 8;

//------------------------Local signal-------------------
    reg  [1:0]                    wstate = WRRESET;
    reg  [1:0]                    wnext;
    reg  [ADDR_BITS-1:0]          waddr;
    wire [C_S_AXI_DATA_WIDTH-1:0] wmask;
    wire                          aw_hs;
    wire                          w_hs;
    reg  [1:0]                    rstate = RDRESET;
    reg  [1:0]                    rnext;
    reg  [C_S_AXI_DATA_WIDTH-1:0] rdata;
    wire                          ar_hs;
    wire [ADDR_BITS-1:0]          raddr;
    // internal registers
    reg                           int_ap_idle = 1'b0;
    reg                           int_ap_ready = 1'b0;
    wire                          task_ap_ready;
    reg                           int_ap_done = 1'b0;
    wire                          task_ap_done;
    reg                           int_task_ap_done = 1'b0;
    reg                           int_ap_start = 1'b0;
    reg                           int_interrupt = 1'b0;
    reg                           int_auto_restart = 1'b0;
    reg                           auto_restart_status = 1'b0;
    wire                          auto_restart_done;
    reg                           int_gie = 1'b0;
    reg  [1:0]                    int_ier = 2'b0;
    reg  [1:0]                    int_isr = 2'b0;
    reg  [31:0]                   int_ap_return;
    reg  [63:0]                   int_in_r = 'b0;
    reg  [63:0]                   int_out_r = 'b0;
    reg  [63:0]                   int_params = 'b0;
    reg  [31:0]                   int_mode = 'b0;
    reg  [31:0]                   int_cin = 'b0;
    reg  [31:0]                   int_cout = 'b0;
    reg  [31:0]                   int_in_h = 'b0;
    reg  [31:0]                   int_in_w = 'b0;
    reg  [31:0]                   int_relu = 'b0;
    reg  [31:0]                   int_shift = 'b0;
    reg  [31:0]                   int_qmin = 'b0;
    reg  [31:0]                   int_qmax = 'b0;
    reg  [31:0]                   int_fan_on = 'b0;
    reg  [31:0]                   int_fan_xa0 = 'b0;
    reg  [31:0]                   int_fan_xag = 'b0;
    reg  [31:0]                   int_fan_xb0 = 'b0;
    reg  [31:0]                   int_fan_xbg = 'b0;
    reg  [31:0]                   int_fan_y0 = 'b0;

//------------------------Instantiation------------------


//------------------------AXI write fsm------------------
assign AWREADY = (wstate == WRIDLE);
assign WREADY  = (wstate == WRDATA);
assign BVALID  = (wstate == WRRESP);
assign BRESP   = 2'b00;  // OKAY
assign wmask   = { {8{WSTRB[3]}}, {8{WSTRB[2]}}, {8{WSTRB[1]}}, {8{WSTRB[0]}} };
assign aw_hs   = AWVALID & AWREADY;
assign w_hs    = WVALID & WREADY;

// wstate
always @(posedge ACLK) begin
    if (ARESET)
        wstate <= WRRESET;
    else if (ACLK_EN)
        wstate <= wnext;
end

// wnext
always @(*) begin
    case (wstate)
        WRIDLE:
            if (AWVALID)
                wnext = WRDATA;
            else
                wnext = WRIDLE;
        WRDATA:
            if (WVALID)
                wnext = WRRESP;
            else
                wnext = WRDATA;
        WRRESP:
            if (BREADY & BVALID)
                wnext = WRIDLE;
            else
                wnext = WRRESP;
        default:
            wnext = WRIDLE;
    endcase
end

// waddr
always @(posedge ACLK) begin
    if (ACLK_EN) begin
        if (aw_hs)
            waddr <= {AWADDR[ADDR_BITS-1:2], {2{1'b0}}};
    end
end

//------------------------AXI read fsm-------------------
assign ARREADY = (rstate == RDIDLE);
assign RDATA   = rdata;
assign RRESP   = 2'b00;  // OKAY
assign RVALID  = (rstate == RDDATA);
assign ar_hs   = ARVALID & ARREADY;
assign raddr   = ARADDR[ADDR_BITS-1:0];

// rstate
always @(posedge ACLK) begin
    if (ARESET)
        rstate <= RDRESET;
    else if (ACLK_EN)
        rstate <= rnext;
end

// rnext
always @(*) begin
    case (rstate)
        RDIDLE:
            if (ARVALID)
                rnext = RDDATA;
            else
                rnext = RDIDLE;
        RDDATA:
            if (RREADY & RVALID)
                rnext = RDIDLE;
            else
                rnext = RDDATA;
        default:
            rnext = RDIDLE;
    endcase
end

// rdata
always @(posedge ACLK) begin
    if (ACLK_EN) begin
        if (ar_hs) begin
            rdata <= 'b0;
            case (raddr)
                ADDR_AP_CTRL: begin
                    rdata[0] <= int_ap_start;
                    rdata[1] <= int_task_ap_done;
                    rdata[2] <= int_ap_idle;
                    rdata[3] <= int_ap_ready;
                    rdata[7] <= int_auto_restart;
                    rdata[9] <= int_interrupt;
                end
                ADDR_GIE: begin
                    rdata <= int_gie;
                end
                ADDR_IER: begin
                    rdata <= int_ier;
                end
                ADDR_ISR: begin
                    rdata <= int_isr;
                end
                ADDR_AP_RETURN_0: begin
                    rdata <= int_ap_return[31:0];
                end
                ADDR_IN_R_DATA_0: begin
                    rdata <= int_in_r[31:0];
                end
                ADDR_IN_R_DATA_1: begin
                    rdata <= int_in_r[63:32];
                end
                ADDR_OUT_R_DATA_0: begin
                    rdata <= int_out_r[31:0];
                end
                ADDR_OUT_R_DATA_1: begin
                    rdata <= int_out_r[63:32];
                end
                ADDR_PARAMS_DATA_0: begin
                    rdata <= int_params[31:0];
                end
                ADDR_PARAMS_DATA_1: begin
                    rdata <= int_params[63:32];
                end
                ADDR_MODE_DATA_0: begin
                    rdata <= int_mode[31:0];
                end
                ADDR_CIN_DATA_0: begin
                    rdata <= int_cin[31:0];
                end
                ADDR_COUT_DATA_0: begin
                    rdata <= int_cout[31:0];
                end
                ADDR_IN_H_DATA_0: begin
                    rdata <= int_in_h[31:0];
                end
                ADDR_IN_W_DATA_0: begin
                    rdata <= int_in_w[31:0];
                end
                ADDR_RELU_DATA_0: begin
                    rdata <= int_relu[31:0];
                end
                ADDR_SHIFT_DATA_0: begin
                    rdata <= int_shift[31:0];
                end
                ADDR_QMIN_DATA_0: begin
                    rdata <= int_qmin[31:0];
                end
                ADDR_QMAX_DATA_0: begin
                    rdata <= int_qmax[31:0];
                end
                ADDR_FAN_ON_DATA_0: begin
                    rdata <= int_fan_on[31:0];
                end
                ADDR_FAN_XA0_DATA_0: begin
                    rdata <= int_fan_xa0[31:0];
                end
                ADDR_FAN_XAG_DATA_0: begin
                    rdata <= int_fan_xag[31:0];
                end
                ADDR_FAN_XB0_DATA_0: begin
                    rdata <= int_fan_xb0[31:0];
                end
                ADDR_FAN_XBG_DATA_0: begin
                    rdata <= int_fan_xbg[31:0];
                end
                ADDR_FAN_Y0_DATA_0: begin
                    rdata <= int_fan_y0[31:0];
                end
            endcase
        end
    end
end


//------------------------Register logic-----------------
assign interrupt         = int_interrupt;
assign ap_start          = int_ap_start;
assign task_ap_done      = (ap_done && !auto_restart_status) || auto_restart_done;
assign task_ap_ready     = ap_ready && !int_auto_restart;
assign auto_restart_done = auto_restart_status && (ap_idle && !int_ap_idle);
assign in_r              = int_in_r;
assign out_r             = int_out_r;
assign params            = int_params;
assign mode              = int_mode;
assign cin               = int_cin;
assign cout              = int_cout;
assign in_h              = int_in_h;
assign in_w              = int_in_w;
assign relu              = int_relu;
assign shift             = int_shift;
assign qmin              = int_qmin;
assign qmax              = int_qmax;
assign fan_on            = int_fan_on;
assign fan_xa0           = int_fan_xa0;
assign fan_xag           = int_fan_xag;
assign fan_xb0           = int_fan_xb0;
assign fan_xbg           = int_fan_xbg;
assign fan_y0            = int_fan_y0;
// int_interrupt
always @(posedge ACLK) begin
    if (ARESET)
        int_interrupt <= 1'b0;
    else if (ACLK_EN) begin
        if (int_gie && (|int_isr))
            int_interrupt <= 1'b1;
        else
            int_interrupt <= 1'b0;
    end
end

// int_ap_start
always @(posedge ACLK) begin
    if (ARESET)
        int_ap_start <= 1'b0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_AP_CTRL && WSTRB[0] && WDATA[0])
            int_ap_start <= 1'b1;
        else if (ap_ready)
            int_ap_start <= int_auto_restart; // clear on handshake/auto restart
    end
end

// int_ap_done
always @(posedge ACLK) begin
    if (ARESET)
        int_ap_done <= 1'b0;
    else if (ACLK_EN) begin
            int_ap_done <= ap_done;
    end
end

// int_task_ap_done
always @(posedge ACLK) begin
    if (ARESET)
        int_task_ap_done <= 1'b0;
    else if (ACLK_EN) begin
        if (task_ap_done)
            int_task_ap_done <= 1'b1;
        else if (ar_hs && raddr == ADDR_AP_CTRL)
            int_task_ap_done <= 1'b0; // clear on read
    end
end

// int_ap_idle
always @(posedge ACLK) begin
    if (ARESET)
        int_ap_idle <= 1'b0;
    else if (ACLK_EN) begin
            int_ap_idle <= ap_idle;
    end
end

// int_ap_ready
always @(posedge ACLK) begin
    if (ARESET)
        int_ap_ready <= 1'b0;
    else if (ACLK_EN) begin
        if (task_ap_ready)
            int_ap_ready <= 1'b1;
        else if (ar_hs && raddr == ADDR_AP_CTRL)
            int_ap_ready <= 1'b0;
    end
end

// int_auto_restart
always @(posedge ACLK) begin
    if (ARESET)
        int_auto_restart <= 1'b0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_AP_CTRL && WSTRB[0])
            int_auto_restart <= WDATA[7];
    end
end

// auto_restart_status
always @(posedge ACLK) begin
    if (ARESET)
        auto_restart_status <= 1'b0;
    else if (ACLK_EN) begin
        if (int_auto_restart)
            auto_restart_status <= 1'b1;
        else if (ap_idle)
            auto_restart_status <= 1'b0;
    end
end

// int_gie
always @(posedge ACLK) begin
    if (ARESET)
        int_gie <= 1'b0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_GIE && WSTRB[0])
            int_gie <= WDATA[0];
    end
end

// int_ier
always @(posedge ACLK) begin
    if (ARESET)
        int_ier <= 1'b0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_IER && WSTRB[0])
            int_ier <= WDATA[1:0];
    end
end

// int_isr[0]
always @(posedge ACLK) begin
    if (ARESET)
        int_isr[0] <= 1'b0;
    else if (ACLK_EN) begin
        if (int_ier[0] & ap_done)
            int_isr[0] <= 1'b1;
        else if (w_hs && waddr == ADDR_ISR && WSTRB[0])
            int_isr[0] <= int_isr[0] ^ WDATA[0]; // toggle on write
    end
end

// int_isr[1]
always @(posedge ACLK) begin
    if (ARESET)
        int_isr[1] <= 1'b0;
    else if (ACLK_EN) begin
        if (int_ier[1] & ap_ready)
            int_isr[1] <= 1'b1;
        else if (w_hs && waddr == ADDR_ISR && WSTRB[0])
            int_isr[1] <= int_isr[1] ^ WDATA[1]; // toggle on write
    end
end

// int_ap_return
always @(posedge ACLK) begin
    if (ARESET)
        int_ap_return <= 0;
    else if (ACLK_EN) begin
        if (ap_done)
            int_ap_return <= ap_return;
    end
end

// int_in_r[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_in_r[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_IN_R_DATA_0)
            int_in_r[31:0] <= (WDATA[31:0] & wmask) | (int_in_r[31:0] & ~wmask);
    end
end

// int_in_r[63:32]
always @(posedge ACLK) begin
    if (ARESET)
        int_in_r[63:32] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_IN_R_DATA_1)
            int_in_r[63:32] <= (WDATA[31:0] & wmask) | (int_in_r[63:32] & ~wmask);
    end
end

// int_out_r[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_out_r[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_OUT_R_DATA_0)
            int_out_r[31:0] <= (WDATA[31:0] & wmask) | (int_out_r[31:0] & ~wmask);
    end
end

// int_out_r[63:32]
always @(posedge ACLK) begin
    if (ARESET)
        int_out_r[63:32] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_OUT_R_DATA_1)
            int_out_r[63:32] <= (WDATA[31:0] & wmask) | (int_out_r[63:32] & ~wmask);
    end
end

// int_params[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_params[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_PARAMS_DATA_0)
            int_params[31:0] <= (WDATA[31:0] & wmask) | (int_params[31:0] & ~wmask);
    end
end

// int_params[63:32]
always @(posedge ACLK) begin
    if (ARESET)
        int_params[63:32] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_PARAMS_DATA_1)
            int_params[63:32] <= (WDATA[31:0] & wmask) | (int_params[63:32] & ~wmask);
    end
end

// int_mode[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_mode[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_MODE_DATA_0)
            int_mode[31:0] <= (WDATA[31:0] & wmask) | (int_mode[31:0] & ~wmask);
    end
end

// int_cin[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_cin[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_CIN_DATA_0)
            int_cin[31:0] <= (WDATA[31:0] & wmask) | (int_cin[31:0] & ~wmask);
    end
end

// int_cout[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_cout[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_COUT_DATA_0)
            int_cout[31:0] <= (WDATA[31:0] & wmask) | (int_cout[31:0] & ~wmask);
    end
end

// int_in_h[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_in_h[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_IN_H_DATA_0)
            int_in_h[31:0] <= (WDATA[31:0] & wmask) | (int_in_h[31:0] & ~wmask);
    end
end

// int_in_w[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_in_w[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_IN_W_DATA_0)
            int_in_w[31:0] <= (WDATA[31:0] & wmask) | (int_in_w[31:0] & ~wmask);
    end
end

// int_relu[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_relu[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_RELU_DATA_0)
            int_relu[31:0] <= (WDATA[31:0] & wmask) | (int_relu[31:0] & ~wmask);
    end
end

// int_shift[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_shift[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_SHIFT_DATA_0)
            int_shift[31:0] <= (WDATA[31:0] & wmask) | (int_shift[31:0] & ~wmask);
    end
end

// int_qmin[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_qmin[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_QMIN_DATA_0)
            int_qmin[31:0] <= (WDATA[31:0] & wmask) | (int_qmin[31:0] & ~wmask);
    end
end

// int_qmax[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_qmax[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_QMAX_DATA_0)
            int_qmax[31:0] <= (WDATA[31:0] & wmask) | (int_qmax[31:0] & ~wmask);
    end
end

// int_fan_on[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_fan_on[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_FAN_ON_DATA_0)
            int_fan_on[31:0] <= (WDATA[31:0] & wmask) | (int_fan_on[31:0] & ~wmask);
    end
end

// int_fan_xa0[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_fan_xa0[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_FAN_XA0_DATA_0)
            int_fan_xa0[31:0] <= (WDATA[31:0] & wmask) | (int_fan_xa0[31:0] & ~wmask);
    end
end

// int_fan_xag[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_fan_xag[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_FAN_XAG_DATA_0)
            int_fan_xag[31:0] <= (WDATA[31:0] & wmask) | (int_fan_xag[31:0] & ~wmask);
    end
end

// int_fan_xb0[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_fan_xb0[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_FAN_XB0_DATA_0)
            int_fan_xb0[31:0] <= (WDATA[31:0] & wmask) | (int_fan_xb0[31:0] & ~wmask);
    end
end

// int_fan_xbg[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_fan_xbg[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_FAN_XBG_DATA_0)
            int_fan_xbg[31:0] <= (WDATA[31:0] & wmask) | (int_fan_xbg[31:0] & ~wmask);
    end
end

// int_fan_y0[31:0]
always @(posedge ACLK) begin
    if (ARESET)
        int_fan_y0[31:0] <= 0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_FAN_Y0_DATA_0)
            int_fan_y0[31:0] <= (WDATA[31:0] & wmask) | (int_fan_y0[31:0] & ~wmask);
    end
end

//synthesis translate_off
always @(posedge ACLK) begin
    if (ACLK_EN) begin
        if (int_gie & ~int_isr[0] & int_ier[0] & ap_done)
            $display ("// Interrupt Monitor : interrupt for ap_done detected @ \"%0t\"", $time);
        if (int_gie & ~int_isr[1] & int_ier[1] & ap_ready)
            $display ("// Interrupt Monitor : interrupt for ap_ready detected @ \"%0t\"", $time);
    end
end
//synthesis translate_on

//------------------------Memory logic-------------------

endmodule
