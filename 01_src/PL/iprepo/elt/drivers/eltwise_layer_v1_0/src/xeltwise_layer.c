// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2025 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
/***************************** Include Files *********************************/
#include "xeltwise_layer.h"

/************************** Function Implementation *************************/
#ifndef __linux__
int XEltwise_layer_CfgInitialize(XEltwise_layer *InstancePtr, XEltwise_layer_Config *ConfigPtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(ConfigPtr != NULL);

    InstancePtr->Control_BaseAddress = ConfigPtr->Control_BaseAddress;
    InstancePtr->IsReady = XIL_COMPONENT_IS_READY;

    return XST_SUCCESS;
}
#endif

void XEltwise_layer_Start(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_AP_CTRL) & 0x80;
    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_AP_CTRL, Data | 0x01);
}

u32 XEltwise_layer_IsDone(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_AP_CTRL);
    return (Data >> 1) & 0x1;
}

u32 XEltwise_layer_IsIdle(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_AP_CTRL);
    return (Data >> 2) & 0x1;
}

u32 XEltwise_layer_IsReady(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_AP_CTRL);
    // check ap_start to see if the pcore is ready for next input
    return !(Data & 0x1);
}

void XEltwise_layer_EnableAutoRestart(XEltwise_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_AP_CTRL, 0x80);
}

void XEltwise_layer_DisableAutoRestart(XEltwise_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_AP_CTRL, 0);
}

u32 XEltwise_layer_Get_return(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_AP_RETURN);
    return Data;
}
void XEltwise_layer_Set_in_a(XEltwise_layer *InstancePtr, u64 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IN_A_DATA, (u32)(Data));
    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IN_A_DATA + 4, (u32)(Data >> 32));
}

u64 XEltwise_layer_Get_in_a(XEltwise_layer *InstancePtr) {
    u64 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IN_A_DATA);
    Data += (u64)XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IN_A_DATA + 4) << 32;
    return Data;
}

void XEltwise_layer_Set_in_b(XEltwise_layer *InstancePtr, u64 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IN_B_DATA, (u32)(Data));
    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IN_B_DATA + 4, (u32)(Data >> 32));
}

u64 XEltwise_layer_Get_in_b(XEltwise_layer *InstancePtr) {
    u64 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IN_B_DATA);
    Data += (u64)XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IN_B_DATA + 4) << 32;
    return Data;
}

void XEltwise_layer_Set_out_r(XEltwise_layer *InstancePtr, u64 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_OUT_R_DATA, (u32)(Data));
    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_OUT_R_DATA + 4, (u32)(Data >> 32));
}

u64 XEltwise_layer_Get_out_r(XEltwise_layer *InstancePtr) {
    u64 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_OUT_R_DATA);
    Data += (u64)XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_OUT_R_DATA + 4) << 32;
    return Data;
}

void XEltwise_layer_Set_mode(XEltwise_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_MODE_DATA, Data);
}

u32 XEltwise_layer_Get_mode(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_MODE_DATA);
    return Data;
}

void XEltwise_layer_Set_ca(XEltwise_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_CA_DATA, Data);
}

u32 XEltwise_layer_Get_ca(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_CA_DATA);
    return Data;
}

void XEltwise_layer_Set_cb(XEltwise_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_CB_DATA, Data);
}

u32 XEltwise_layer_Get_cb(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_CB_DATA);
    return Data;
}

void XEltwise_layer_Set_h(XEltwise_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_H_DATA, Data);
}

u32 XEltwise_layer_Get_h(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_H_DATA);
    return Data;
}

void XEltwise_layer_Set_w(XEltwise_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_W_DATA, Data);
}

u32 XEltwise_layer_Get_w(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_W_DATA);
    return Data;
}

void XEltwise_layer_Set_m0(XEltwise_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_M0_DATA, Data);
}

u32 XEltwise_layer_Get_m0(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_M0_DATA);
    return Data;
}

void XEltwise_layer_Set_m1(XEltwise_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_M1_DATA, Data);
}

u32 XEltwise_layer_Get_m1(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_M1_DATA);
    return Data;
}

void XEltwise_layer_Set_shift(XEltwise_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_SHIFT_DATA, Data);
}

u32 XEltwise_layer_Get_shift(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_SHIFT_DATA);
    return Data;
}

void XEltwise_layer_Set_qmin(XEltwise_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_QMIN_DATA, Data);
}

u32 XEltwise_layer_Get_qmin(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_QMIN_DATA);
    return Data;
}

void XEltwise_layer_Set_qmax(XEltwise_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_QMAX_DATA, Data);
}

u32 XEltwise_layer_Get_qmax(XEltwise_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_QMAX_DATA);
    return Data;
}

void XEltwise_layer_InterruptGlobalEnable(XEltwise_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_GIE, 1);
}

void XEltwise_layer_InterruptGlobalDisable(XEltwise_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_GIE, 0);
}

void XEltwise_layer_InterruptEnable(XEltwise_layer *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IER);
    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IER, Register | Mask);
}

void XEltwise_layer_InterruptDisable(XEltwise_layer *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IER);
    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IER, Register & (~Mask));
}

void XEltwise_layer_InterruptClear(XEltwise_layer *InstancePtr, u32 Mask) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XEltwise_layer_WriteReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_ISR, Mask);
}

u32 XEltwise_layer_InterruptGetEnabled(XEltwise_layer *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    return XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_IER);
}

u32 XEltwise_layer_InterruptGetStatus(XEltwise_layer *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    return XEltwise_layer_ReadReg(InstancePtr->Control_BaseAddress, XELTWISE_LAYER_CONTROL_ADDR_ISR);
}

