// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2.1 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
#ifndef XCONV_LAYER_H
#define XCONV_LAYER_H

#ifdef __cplusplus
extern "C" {
#endif

/***************************** Include Files *********************************/
#ifndef __linux__
#include "xil_types.h"
#include "xil_assert.h"
#include "xstatus.h"
#include "xil_io.h"
#else
#include <stdint.h>
#include <assert.h>
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <stddef.h>
#endif
#include "xconv_layer_hw.h"

/**************************** Type Definitions ******************************/
#ifdef __linux__
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#else
typedef struct {
#ifdef SDT
    char *Name;
#else
    u16 DeviceId;
#endif
    u64 Control_BaseAddress;
} XConv_layer_Config;
#endif

typedef struct {
    u64 Control_BaseAddress;
    u32 IsReady;
} XConv_layer;

typedef u32 word_type;

/***************** Macros (Inline Functions) Definitions *********************/
#ifndef __linux__
#define XConv_layer_WriteReg(BaseAddress, RegOffset, Data) \
    Xil_Out32((BaseAddress) + (RegOffset), (u32)(Data))
#define XConv_layer_ReadReg(BaseAddress, RegOffset) \
    Xil_In32((BaseAddress) + (RegOffset))
#else
#define XConv_layer_WriteReg(BaseAddress, RegOffset, Data) \
    *(volatile u32*)((BaseAddress) + (RegOffset)) = (u32)(Data)
#define XConv_layer_ReadReg(BaseAddress, RegOffset) \
    *(volatile u32*)((BaseAddress) + (RegOffset))

#define Xil_AssertVoid(expr)    assert(expr)
#define Xil_AssertNonvoid(expr) assert(expr)

#define XST_SUCCESS             0
#define XST_DEVICE_NOT_FOUND    2
#define XST_OPEN_DEVICE_FAILED  3
#define XIL_COMPONENT_IS_READY  1
#endif

/************************** Function Prototypes *****************************/
#ifndef __linux__
#ifdef SDT
int XConv_layer_Initialize(XConv_layer *InstancePtr, UINTPTR BaseAddress);
XConv_layer_Config* XConv_layer_LookupConfig(UINTPTR BaseAddress);
#else
int XConv_layer_Initialize(XConv_layer *InstancePtr, u16 DeviceId);
XConv_layer_Config* XConv_layer_LookupConfig(u16 DeviceId);
#endif
int XConv_layer_CfgInitialize(XConv_layer *InstancePtr, XConv_layer_Config *ConfigPtr);
#else
int XConv_layer_Initialize(XConv_layer *InstancePtr, const char* InstanceName);
int XConv_layer_Release(XConv_layer *InstancePtr);
#endif

void XConv_layer_Start(XConv_layer *InstancePtr);
u32 XConv_layer_IsDone(XConv_layer *InstancePtr);
u32 XConv_layer_IsIdle(XConv_layer *InstancePtr);
u32 XConv_layer_IsReady(XConv_layer *InstancePtr);
void XConv_layer_EnableAutoRestart(XConv_layer *InstancePtr);
void XConv_layer_DisableAutoRestart(XConv_layer *InstancePtr);
u32 XConv_layer_Get_return(XConv_layer *InstancePtr);

void XConv_layer_Set_in_r(XConv_layer *InstancePtr, u64 Data);
u64 XConv_layer_Get_in_r(XConv_layer *InstancePtr);
void XConv_layer_Set_out_r(XConv_layer *InstancePtr, u64 Data);
u64 XConv_layer_Get_out_r(XConv_layer *InstancePtr);
void XConv_layer_Set_params(XConv_layer *InstancePtr, u64 Data);
u64 XConv_layer_Get_params(XConv_layer *InstancePtr);
void XConv_layer_Set_mode(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_mode(XConv_layer *InstancePtr);
void XConv_layer_Set_cin(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_cin(XConv_layer *InstancePtr);
void XConv_layer_Set_cout(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_cout(XConv_layer *InstancePtr);
void XConv_layer_Set_in_h(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_in_h(XConv_layer *InstancePtr);
void XConv_layer_Set_in_w(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_in_w(XConv_layer *InstancePtr);
void XConv_layer_Set_relu(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_relu(XConv_layer *InstancePtr);
void XConv_layer_Set_shift(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_shift(XConv_layer *InstancePtr);
void XConv_layer_Set_qmin(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_qmin(XConv_layer *InstancePtr);
void XConv_layer_Set_qmax(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_qmax(XConv_layer *InstancePtr);
void XConv_layer_Set_fan_on(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_fan_on(XConv_layer *InstancePtr);
void XConv_layer_Set_fan_xa0(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_fan_xa0(XConv_layer *InstancePtr);
void XConv_layer_Set_fan_xag(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_fan_xag(XConv_layer *InstancePtr);
void XConv_layer_Set_fan_xb0(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_fan_xb0(XConv_layer *InstancePtr);
void XConv_layer_Set_fan_xbg(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_fan_xbg(XConv_layer *InstancePtr);
void XConv_layer_Set_fan_y0(XConv_layer *InstancePtr, u32 Data);
u32 XConv_layer_Get_fan_y0(XConv_layer *InstancePtr);

void XConv_layer_InterruptGlobalEnable(XConv_layer *InstancePtr);
void XConv_layer_InterruptGlobalDisable(XConv_layer *InstancePtr);
void XConv_layer_InterruptEnable(XConv_layer *InstancePtr, u32 Mask);
void XConv_layer_InterruptDisable(XConv_layer *InstancePtr, u32 Mask);
void XConv_layer_InterruptClear(XConv_layer *InstancePtr, u32 Mask);
u32 XConv_layer_InterruptGetEnabled(XConv_layer *InstancePtr);
u32 XConv_layer_InterruptGetStatus(XConv_layer *InstancePtr);

#ifdef __cplusplus
}
#endif

#endif
