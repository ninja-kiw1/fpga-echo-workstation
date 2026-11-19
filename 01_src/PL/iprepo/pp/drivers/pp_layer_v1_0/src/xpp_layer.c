// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2025 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
/***************************** Include Files *********************************/
#include "xpp_layer.h"

/************************** Function Implementation *************************/
#ifndef __linux__
int XPp_layer_CfgInitialize(XPp_layer *InstancePtr, XPp_layer_Config *ConfigPtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(ConfigPtr != NULL);

    InstancePtr->Control_BaseAddress = ConfigPtr->Control_BaseAddress;
    InstancePtr->IsReady = XIL_COMPONENT_IS_READY;

    return XST_SUCCESS;
}
#endif

void XPp_layer_Start(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_AP_CTRL) & 0x80;
    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_AP_CTRL, Data | 0x01);
}

u32 XPp_layer_IsDone(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_AP_CTRL);
    return (Data >> 1) & 0x1;
}

u32 XPp_layer_IsIdle(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_AP_CTRL);
    return (Data >> 2) & 0x1;
}

u32 XPp_layer_IsReady(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_AP_CTRL);
    // check ap_start to see if the pcore is ready for next input
    return !(Data & 0x1);
}

void XPp_layer_EnableAutoRestart(XPp_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_AP_CTRL, 0x80);
}

void XPp_layer_DisableAutoRestart(XPp_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_AP_CTRL, 0);
}

u32 XPp_layer_Get_return(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_AP_RETURN);
    return Data;
}
void XPp_layer_Set_in_r(XPp_layer *InstancePtr, u64 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_IN_R_DATA, (u32)(Data));
    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_IN_R_DATA + 4, (u32)(Data >> 32));
}

u64 XPp_layer_Get_in_r(XPp_layer *InstancePtr) {
    u64 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_IN_R_DATA);
    Data += (u64)XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_IN_R_DATA + 4) << 32;
    return Data;
}

void XPp_layer_Set_out_r(XPp_layer *InstancePtr, u64 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_OUT_R_DATA, (u32)(Data));
    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_OUT_R_DATA + 4, (u32)(Data >> 32));
}

u64 XPp_layer_Get_out_r(XPp_layer *InstancePtr) {
    u64 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_OUT_R_DATA);
    Data += (u64)XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_OUT_R_DATA + 4) << 32;
    return Data;
}

void XPp_layer_Set_op(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_OP_DATA, Data);
}

u32 XPp_layer_Get_op(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_OP_DATA);
    return Data;
}

void XPp_layer_Set_h(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_H_DATA, Data);
}

u32 XPp_layer_Get_h(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_H_DATA);
    return Data;
}

void XPp_layer_Set_w(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_W_DATA, Data);
}

u32 XPp_layer_Get_w(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_W_DATA);
    return Data;
}

void XPp_layer_Set_x0(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_X0_DATA, Data);
}

u32 XPp_layer_Get_x0(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_X0_DATA);
    return Data;
}

void XPp_layer_Set_y0(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_Y0_DATA, Data);
}

u32 XPp_layer_Get_y0(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_Y0_DATA);
    return Data;
}

void XPp_layer_Set_cw(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_CW_DATA, Data);
}

u32 XPp_layer_Get_cw(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_CW_DATA);
    return Data;
}

void XPp_layer_Set_ch(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_CH_DATA, Data);
}

u32 XPp_layer_Get_ch(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_CH_DATA);
    return Data;
}

void XPp_layer_Set_k(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_K_DATA, Data);
}

u32 XPp_layer_Get_k(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_K_DATA);
    return Data;
}

void XPp_layer_Set_thr(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_THR_DATA, Data);
}

u32 XPp_layer_Get_thr(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_THR_DATA);
    return Data;
}

void XPp_layer_Set_tn(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_TN_DATA, Data);
}

u32 XPp_layer_Get_tn(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_TN_DATA);
    return Data;
}

void XPp_layer_Set_ya(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_YA_DATA, Data);
}

u32 XPp_layer_Get_ya(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_YA_DATA);
    return Data;
}

void XPp_layer_Set_yb(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_YB_DATA, Data);
}

u32 XPp_layer_Get_yb(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_YB_DATA);
    return Data;
}

void XPp_layer_Set_in_row0(XPp_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_IN_ROW0_DATA, Data);
}

u32 XPp_layer_Get_in_row0(XPp_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_IN_ROW0_DATA);
    return Data;
}

void XPp_layer_InterruptGlobalEnable(XPp_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_GIE, 1);
}

void XPp_layer_InterruptGlobalDisable(XPp_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_GIE, 0);
}

void XPp_layer_InterruptEnable(XPp_layer *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_IER);
    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_IER, Register | Mask);
}

void XPp_layer_InterruptDisable(XPp_layer *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_IER);
    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_IER, Register & (~Mask));
}

void XPp_layer_InterruptClear(XPp_layer *InstancePtr, u32 Mask) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XPp_layer_WriteReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_ISR, Mask);
}

u32 XPp_layer_InterruptGetEnabled(XPp_layer *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    return XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_IER);
}

u32 XPp_layer_InterruptGetStatus(XPp_layer *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    return XPp_layer_ReadReg(InstancePtr->Control_BaseAddress, XPP_LAYER_CONTROL_ADDR_ISR);
}

