// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2.1 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
// control
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

#define XCONV_LAYER_CONTROL_ADDR_AP_CTRL      0x00
#define XCONV_LAYER_CONTROL_ADDR_GIE          0x04
#define XCONV_LAYER_CONTROL_ADDR_IER          0x08
#define XCONV_LAYER_CONTROL_ADDR_ISR          0x0c
#define XCONV_LAYER_CONTROL_ADDR_AP_RETURN    0x10
#define XCONV_LAYER_CONTROL_BITS_AP_RETURN    32
#define XCONV_LAYER_CONTROL_ADDR_IN_R_DATA    0x18
#define XCONV_LAYER_CONTROL_BITS_IN_R_DATA    64
#define XCONV_LAYER_CONTROL_ADDR_OUT_R_DATA   0x24
#define XCONV_LAYER_CONTROL_BITS_OUT_R_DATA   64
#define XCONV_LAYER_CONTROL_ADDR_PARAMS_DATA  0x30
#define XCONV_LAYER_CONTROL_BITS_PARAMS_DATA  64
#define XCONV_LAYER_CONTROL_ADDR_MODE_DATA    0x3c
#define XCONV_LAYER_CONTROL_BITS_MODE_DATA    32
#define XCONV_LAYER_CONTROL_ADDR_CIN_DATA     0x44
#define XCONV_LAYER_CONTROL_BITS_CIN_DATA     32
#define XCONV_LAYER_CONTROL_ADDR_COUT_DATA    0x4c
#define XCONV_LAYER_CONTROL_BITS_COUT_DATA    32
#define XCONV_LAYER_CONTROL_ADDR_IN_H_DATA    0x54
#define XCONV_LAYER_CONTROL_BITS_IN_H_DATA    32
#define XCONV_LAYER_CONTROL_ADDR_IN_W_DATA    0x5c
#define XCONV_LAYER_CONTROL_BITS_IN_W_DATA    32
#define XCONV_LAYER_CONTROL_ADDR_RELU_DATA    0x64
#define XCONV_LAYER_CONTROL_BITS_RELU_DATA    32
#define XCONV_LAYER_CONTROL_ADDR_SHIFT_DATA   0x6c
#define XCONV_LAYER_CONTROL_BITS_SHIFT_DATA   32
#define XCONV_LAYER_CONTROL_ADDR_QMIN_DATA    0x74
#define XCONV_LAYER_CONTROL_BITS_QMIN_DATA    32
#define XCONV_LAYER_CONTROL_ADDR_QMAX_DATA    0x7c
#define XCONV_LAYER_CONTROL_BITS_QMAX_DATA    32
#define XCONV_LAYER_CONTROL_ADDR_FAN_ON_DATA  0x84
#define XCONV_LAYER_CONTROL_BITS_FAN_ON_DATA  32
#define XCONV_LAYER_CONTROL_ADDR_FAN_XA0_DATA 0x8c
#define XCONV_LAYER_CONTROL_BITS_FAN_XA0_DATA 32
#define XCONV_LAYER_CONTROL_ADDR_FAN_XAG_DATA 0x94
#define XCONV_LAYER_CONTROL_BITS_FAN_XAG_DATA 32
#define XCONV_LAYER_CONTROL_ADDR_FAN_XB0_DATA 0x9c
#define XCONV_LAYER_CONTROL_BITS_FAN_XB0_DATA 32
#define XCONV_LAYER_CONTROL_ADDR_FAN_XBG_DATA 0xa4
#define XCONV_LAYER_CONTROL_BITS_FAN_XBG_DATA 32
#define XCONV_LAYER_CONTROL_ADDR_FAN_Y0_DATA  0xac
#define XCONV_LAYER_CONTROL_BITS_FAN_Y0_DATA  32

