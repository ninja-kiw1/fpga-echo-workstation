// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2025 Advanced Micro Devices, Inc. All Rights Reserved.
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
// 0x30 : Data signal of op
//        bit 31~0 - op[31:0] (Read/Write)
// 0x34 : reserved
// 0x38 : Data signal of h
//        bit 31~0 - h[31:0] (Read/Write)
// 0x3c : reserved
// 0x40 : Data signal of w
//        bit 31~0 - w[31:0] (Read/Write)
// 0x44 : reserved
// 0x48 : Data signal of x0
//        bit 31~0 - x0[31:0] (Read/Write)
// 0x4c : reserved
// 0x50 : Data signal of y0
//        bit 31~0 - y0[31:0] (Read/Write)
// 0x54 : reserved
// 0x58 : Data signal of cw
//        bit 31~0 - cw[31:0] (Read/Write)
// 0x5c : reserved
// 0x60 : Data signal of ch
//        bit 31~0 - ch[31:0] (Read/Write)
// 0x64 : reserved
// 0x68 : Data signal of k
//        bit 31~0 - k[31:0] (Read/Write)
// 0x6c : reserved
// 0x70 : Data signal of thr
//        bit 31~0 - thr[31:0] (Read/Write)
// 0x74 : reserved
// 0x78 : Data signal of tn
//        bit 31~0 - tn[31:0] (Read/Write)
// 0x7c : reserved
// 0x80 : Data signal of ya
//        bit 31~0 - ya[31:0] (Read/Write)
// 0x84 : reserved
// 0x88 : Data signal of yb
//        bit 31~0 - yb[31:0] (Read/Write)
// 0x8c : reserved
// 0x90 : Data signal of in_row0
//        bit 31~0 - in_row0[31:0] (Read/Write)
// 0x94 : reserved
// (SC = Self Clear, COR = Clear on Read, TOW = Toggle on Write, COH = Clear on Handshake)

#define XPP_LAYER_CONTROL_ADDR_AP_CTRL      0x00
#define XPP_LAYER_CONTROL_ADDR_GIE          0x04
#define XPP_LAYER_CONTROL_ADDR_IER          0x08
#define XPP_LAYER_CONTROL_ADDR_ISR          0x0c
#define XPP_LAYER_CONTROL_ADDR_AP_RETURN    0x10
#define XPP_LAYER_CONTROL_BITS_AP_RETURN    32
#define XPP_LAYER_CONTROL_ADDR_IN_R_DATA    0x18
#define XPP_LAYER_CONTROL_BITS_IN_R_DATA    64
#define XPP_LAYER_CONTROL_ADDR_OUT_R_DATA   0x24
#define XPP_LAYER_CONTROL_BITS_OUT_R_DATA   64
#define XPP_LAYER_CONTROL_ADDR_OP_DATA      0x30
#define XPP_LAYER_CONTROL_BITS_OP_DATA      32
#define XPP_LAYER_CONTROL_ADDR_H_DATA       0x38
#define XPP_LAYER_CONTROL_BITS_H_DATA       32
#define XPP_LAYER_CONTROL_ADDR_W_DATA       0x40
#define XPP_LAYER_CONTROL_BITS_W_DATA       32
#define XPP_LAYER_CONTROL_ADDR_X0_DATA      0x48
#define XPP_LAYER_CONTROL_BITS_X0_DATA      32
#define XPP_LAYER_CONTROL_ADDR_Y0_DATA      0x50
#define XPP_LAYER_CONTROL_BITS_Y0_DATA      32
#define XPP_LAYER_CONTROL_ADDR_CW_DATA      0x58
#define XPP_LAYER_CONTROL_BITS_CW_DATA      32
#define XPP_LAYER_CONTROL_ADDR_CH_DATA      0x60
#define XPP_LAYER_CONTROL_BITS_CH_DATA      32
#define XPP_LAYER_CONTROL_ADDR_K_DATA       0x68
#define XPP_LAYER_CONTROL_BITS_K_DATA       32
#define XPP_LAYER_CONTROL_ADDR_THR_DATA     0x70
#define XPP_LAYER_CONTROL_BITS_THR_DATA     32
#define XPP_LAYER_CONTROL_ADDR_TN_DATA      0x78
#define XPP_LAYER_CONTROL_BITS_TN_DATA      32
#define XPP_LAYER_CONTROL_ADDR_YA_DATA      0x80
#define XPP_LAYER_CONTROL_BITS_YA_DATA      32
#define XPP_LAYER_CONTROL_ADDR_YB_DATA      0x88
#define XPP_LAYER_CONTROL_BITS_YB_DATA      32
#define XPP_LAYER_CONTROL_ADDR_IN_ROW0_DATA 0x90
#define XPP_LAYER_CONTROL_BITS_IN_ROW0_DATA 32

