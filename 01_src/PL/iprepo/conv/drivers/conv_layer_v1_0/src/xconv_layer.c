// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2.1 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
/***************************** Include Files *********************************/
#include "xconv_layer.h"

/************************** Function Implementation *************************/
#ifndef __linux__
int XConv_layer_CfgInitialize(XConv_layer *InstancePtr, XConv_layer_Config *ConfigPtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(ConfigPtr != NULL);

    InstancePtr->Control_BaseAddress = ConfigPtr->Control_BaseAddress;
    InstancePtr->IsReady = XIL_COMPONENT_IS_READY;

    return XST_SUCCESS;
}
#endif

void XConv_layer_Start(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_AP_CTRL) & 0x80;
    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_AP_CTRL, Data | 0x01);
}

u32 XConv_layer_IsDone(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_AP_CTRL);
    return (Data >> 1) & 0x1;
}

u32 XConv_layer_IsIdle(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_AP_CTRL);
    return (Data >> 2) & 0x1;
}

u32 XConv_layer_IsReady(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_AP_CTRL);
    // check ap_start to see if the pcore is ready for next input
    return !(Data & 0x1);
}

void XConv_layer_EnableAutoRestart(XConv_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_AP_CTRL, 0x80);
}

void XConv_layer_DisableAutoRestart(XConv_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_AP_CTRL, 0);
}

u32 XConv_layer_Get_return(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_AP_RETURN);
    return Data;
}
void XConv_layer_Set_in_r(XConv_layer *InstancePtr, u64 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IN_R_DATA, (u32)(Data));
    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IN_R_DATA + 4, (u32)(Data >> 32));
}

u64 XConv_layer_Get_in_r(XConv_layer *InstancePtr) {
    u64 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IN_R_DATA);
    Data += (u64)XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IN_R_DATA + 4) << 32;
    return Data;
}

void XConv_layer_Set_out_r(XConv_layer *InstancePtr, u64 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_OUT_R_DATA, (u32)(Data));
    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_OUT_R_DATA + 4, (u32)(Data >> 32));
}

u64 XConv_layer_Get_out_r(XConv_layer *InstancePtr) {
    u64 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_OUT_R_DATA);
    Data += (u64)XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_OUT_R_DATA + 4) << 32;
    return Data;
}

void XConv_layer_Set_params(XConv_layer *InstancePtr, u64 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_PARAMS_DATA, (u32)(Data));
    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_PARAMS_DATA + 4, (u32)(Data >> 32));
}

u64 XConv_layer_Get_params(XConv_layer *InstancePtr) {
    u64 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_PARAMS_DATA);
    Data += (u64)XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_PARAMS_DATA + 4) << 32;
    return Data;
}

void XConv_layer_Set_mode(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_MODE_DATA, Data);
}

u32 XConv_layer_Get_mode(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_MODE_DATA);
    return Data;
}

void XConv_layer_Set_cin(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_CIN_DATA, Data);
}

u32 XConv_layer_Get_cin(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_CIN_DATA);
    return Data;
}

void XConv_layer_Set_cout(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_COUT_DATA, Data);
}

u32 XConv_layer_Get_cout(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_COUT_DATA);
    return Data;
}

void XConv_layer_Set_in_h(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IN_H_DATA, Data);
}

u32 XConv_layer_Get_in_h(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IN_H_DATA);
    return Data;
}

void XConv_layer_Set_in_w(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IN_W_DATA, Data);
}

u32 XConv_layer_Get_in_w(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IN_W_DATA);
    return Data;
}

void XConv_layer_Set_relu(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_RELU_DATA, Data);
}

u32 XConv_layer_Get_relu(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_RELU_DATA);
    return Data;
}

void XConv_layer_Set_shift(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_SHIFT_DATA, Data);
}

u32 XConv_layer_Get_shift(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_SHIFT_DATA);
    return Data;
}

void XConv_layer_Set_qmin(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_QMIN_DATA, Data);
}

u32 XConv_layer_Get_qmin(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_QMIN_DATA);
    return Data;
}

void XConv_layer_Set_qmax(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_QMAX_DATA, Data);
}

u32 XConv_layer_Get_qmax(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_QMAX_DATA);
    return Data;
}

void XConv_layer_Set_fan_on(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_ON_DATA, Data);
}

u32 XConv_layer_Get_fan_on(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_ON_DATA);
    return Data;
}

void XConv_layer_Set_fan_xa0(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_XA0_DATA, Data);
}

u32 XConv_layer_Get_fan_xa0(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_XA0_DATA);
    return Data;
}

void XConv_layer_Set_fan_xag(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_XAG_DATA, Data);
}

u32 XConv_layer_Get_fan_xag(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_XAG_DATA);
    return Data;
}

void XConv_layer_Set_fan_xb0(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_XB0_DATA, Data);
}

u32 XConv_layer_Get_fan_xb0(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_XB0_DATA);
    return Data;
}

void XConv_layer_Set_fan_xbg(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_XBG_DATA, Data);
}

u32 XConv_layer_Get_fan_xbg(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_XBG_DATA);
    return Data;
}

void XConv_layer_Set_fan_y0(XConv_layer *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_Y0_DATA, Data);
}

u32 XConv_layer_Get_fan_y0(XConv_layer *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_FAN_Y0_DATA);
    return Data;
}

void XConv_layer_InterruptGlobalEnable(XConv_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_GIE, 1);
}

void XConv_layer_InterruptGlobalDisable(XConv_layer *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_GIE, 0);
}

void XConv_layer_InterruptEnable(XConv_layer *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IER);
    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IER, Register | Mask);
}

void XConv_layer_InterruptDisable(XConv_layer *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IER);
    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IER, Register & (~Mask));
}

void XConv_layer_InterruptClear(XConv_layer *InstancePtr, u32 Mask) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XConv_layer_WriteReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_ISR, Mask);
}

u32 XConv_layer_InterruptGetEnabled(XConv_layer *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    return XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_IER);
}

u32 XConv_layer_InterruptGetStatus(XConv_layer *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    return XConv_layer_ReadReg(InstancePtr->Control_BaseAddress, XCONV_LAYER_CONTROL_ADDR_ISR);
}

