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
// 0x18 : Data signal of in_a
//        bit 31~0 - in_a[31:0] (Read/Write)
// 0x1c : Data signal of in_a
//        bit 31~0 - in_a[63:32] (Read/Write)
// 0x20 : reserved
// 0x24 : Data signal of in_b
//        bit 31~0 - in_b[31:0] (Read/Write)
// 0x28 : Data signal of in_b
//        bit 31~0 - in_b[63:32] (Read/Write)
// 0x2c : reserved
// 0x30 : Data signal of out_r
//        bit 31~0 - out_r[31:0] (Read/Write)
// 0x34 : Data signal of out_r
//        bit 31~0 - out_r[63:32] (Read/Write)
// 0x38 : reserved
// 0x3c : Data signal of mode
//        bit 31~0 - mode[31:0] (Read/Write)
// 0x40 : reserved
// 0x44 : Data signal of ca
//        bit 31~0 - ca[31:0] (Read/Write)
// 0x48 : reserved
// 0x4c : Data signal of cb
//        bit 31~0 - cb[31:0] (Read/Write)
// 0x50 : reserved
// 0x54 : Data signal of h
//        bit 31~0 - h[31:0] (Read/Write)
// 0x58 : reserved
// 0x5c : Data signal of w
//        bit 31~0 - w[31:0] (Read/Write)
// 0x60 : reserved
// 0x64 : Data signal of m0
//        bit 31~0 - m0[31:0] (Read/Write)
// 0x68 : reserved
// 0x6c : Data signal of m1
//        bit 31~0 - m1[31:0] (Read/Write)
// 0x70 : reserved
// 0x74 : Data signal of shift
//        bit 31~0 - shift[31:0] (Read/Write)
// 0x78 : reserved
// 0x7c : Data signal of qmin
//        bit 31~0 - qmin[31:0] (Read/Write)
// 0x80 : reserved
// 0x84 : Data signal of qmax
//        bit 31~0 - qmax[31:0] (Read/Write)
// 0x88 : reserved
// (SC = Self Clear, COR = Clear on Read, TOW = Toggle on Write, COH = Clear on Handshake)

#define XELTWISE_LAYER_CONTROL_ADDR_AP_CTRL    0x00
#define XELTWISE_LAYER_CONTROL_ADDR_GIE        0x04
#define XELTWISE_LAYER_CONTROL_ADDR_IER        0x08
#define XELTWISE_LAYER_CONTROL_ADDR_ISR        0x0c
#define XELTWISE_LAYER_CONTROL_ADDR_AP_RETURN  0x10
#define XELTWISE_LAYER_CONTROL_BITS_AP_RETURN  32
#define XELTWISE_LAYER_CONTROL_ADDR_IN_A_DATA  0x18
#define XELTWISE_LAYER_CONTROL_BITS_IN_A_DATA  64
#define XELTWISE_LAYER_CONTROL_ADDR_IN_B_DATA  0x24
#define XELTWISE_LAYER_CONTROL_BITS_IN_B_DATA  64
#define XELTWISE_LAYER_CONTROL_ADDR_OUT_R_DATA 0x30
#define XELTWISE_LAYER_CONTROL_BITS_OUT_R_DATA 64
#define XELTWISE_LAYER_CONTROL_ADDR_MODE_DATA  0x3c
#define XELTWISE_LAYER_CONTROL_BITS_MODE_DATA  32
#define XELTWISE_LAYER_CONTROL_ADDR_CA_DATA    0x44
#define XELTWISE_LAYER_CONTROL_BITS_CA_DATA    32
#define XELTWISE_LAYER_CONTROL_ADDR_CB_DATA    0x4c
#define XELTWISE_LAYER_CONTROL_BITS_CB_DATA    32
#define XELTWISE_LAYER_CONTROL_ADDR_H_DATA     0x54
#define XELTWISE_LAYER_CONTROL_BITS_H_DATA     32
#define XELTWISE_LAYER_CONTROL_ADDR_W_DATA     0x5c
#define XELTWISE_LAYER_CONTROL_BITS_W_DATA     32
#define XELTWISE_LAYER_CONTROL_ADDR_M0_DATA    0x64
#define XELTWISE_LAYER_CONTROL_BITS_M0_DATA    32
#define XELTWISE_LAYER_CONTROL_ADDR_M1_DATA    0x6c
#define XELTWISE_LAYER_CONTROL_BITS_M1_DATA    32
#define XELTWISE_LAYER_CONTROL_ADDR_SHIFT_DATA 0x74
#define XELTWISE_LAYER_CONTROL_BITS_SHIFT_DATA 32
#define XELTWISE_LAYER_CONTROL_ADDR_QMIN_DATA  0x7c
#define XELTWISE_LAYER_CONTROL_BITS_QMIN_DATA  32
#define XELTWISE_LAYER_CONTROL_ADDR_QMAX_DATA  0x84
#define XELTWISE_LAYER_CONTROL_BITS_QMAX_DATA  32

