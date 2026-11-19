// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2025 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
#ifndef XELTWISE_LAYER_H
#define XELTWISE_LAYER_H

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
#include "xeltwise_layer_hw.h"

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
} XEltwise_layer_Config;
#endif

typedef struct {
    u64 Control_BaseAddress;
    u32 IsReady;
} XEltwise_layer;

typedef u32 word_type;

/***************** Macros (Inline Functions) Definitions *********************/
#ifndef __linux__
#define XEltwise_layer_WriteReg(BaseAddress, RegOffset, Data) \
    Xil_Out32((BaseAddress) + (RegOffset), (u32)(Data))
#define XEltwise_layer_ReadReg(BaseAddress, RegOffset) \
    Xil_In32((BaseAddress) + (RegOffset))
#else
#define XEltwise_layer_WriteReg(BaseAddress, RegOffset, Data) \
    *(volatile u32*)((BaseAddress) + (RegOffset)) = (u32)(Data)
#define XEltwise_layer_ReadReg(BaseAddress, RegOffset) \
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
int XEltwise_layer_Initialize(XEltwise_layer *InstancePtr, UINTPTR BaseAddress);
XEltwise_layer_Config* XEltwise_layer_LookupConfig(UINTPTR BaseAddress);
#else
int XEltwise_layer_Initialize(XEltwise_layer *InstancePtr, u16 DeviceId);
XEltwise_layer_Config* XEltwise_layer_LookupConfig(u16 DeviceId);
#endif
int XEltwise_layer_CfgInitialize(XEltwise_layer *InstancePtr, XEltwise_layer_Config *ConfigPtr);
#else
int XEltwise_layer_Initialize(XEltwise_layer *InstancePtr, const char* InstanceName);
int XEltwise_layer_Release(XEltwise_layer *InstancePtr);
#endif

void XEltwise_layer_Start(XEltwise_layer *InstancePtr);
u32 XEltwise_layer_IsDone(XEltwise_layer *InstancePtr);
u32 XEltwise_layer_IsIdle(XEltwise_layer *InstancePtr);
u32 XEltwise_layer_IsReady(XEltwise_layer *InstancePtr);
void XEltwise_layer_EnableAutoRestart(XEltwise_layer *InstancePtr);
void XEltwise_layer_DisableAutoRestart(XEltwise_layer *InstancePtr);
u32 XEltwise_layer_Get_return(XEltwise_layer *InstancePtr);

void XEltwise_layer_Set_in_a(XEltwise_layer *InstancePtr, u64 Data);
u64 XEltwise_layer_Get_in_a(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_in_b(XEltwise_layer *InstancePtr, u64 Data);
u64 XEltwise_layer_Get_in_b(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_out_r(XEltwise_layer *InstancePtr, u64 Data);
u64 XEltwise_layer_Get_out_r(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_mode(XEltwise_layer *InstancePtr, u32 Data);
u32 XEltwise_layer_Get_mode(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_ca(XEltwise_layer *InstancePtr, u32 Data);
u32 XEltwise_layer_Get_ca(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_cb(XEltwise_layer *InstancePtr, u32 Data);
u32 XEltwise_layer_Get_cb(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_h(XEltwise_layer *InstancePtr, u32 Data);
u32 XEltwise_layer_Get_h(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_w(XEltwise_layer *InstancePtr, u32 Data);
u32 XEltwise_layer_Get_w(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_m0(XEltwise_layer *InstancePtr, u32 Data);
u32 XEltwise_layer_Get_m0(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_m1(XEltwise_layer *InstancePtr, u32 Data);
u32 XEltwise_layer_Get_m1(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_shift(XEltwise_layer *InstancePtr, u32 Data);
u32 XEltwise_layer_Get_shift(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_qmin(XEltwise_layer *InstancePtr, u32 Data);
u32 XEltwise_layer_Get_qmin(XEltwise_layer *InstancePtr);
void XEltwise_layer_Set_qmax(XEltwise_layer *InstancePtr, u32 Data);
u32 XEltwise_layer_Get_qmax(XEltwise_layer *InstancePtr);

void XEltwise_layer_InterruptGlobalEnable(XEltwise_layer *InstancePtr);
void XEltwise_layer_InterruptGlobalDisable(XEltwise_layer *InstancePtr);
void XEltwise_layer_InterruptEnable(XEltwise_layer *InstancePtr, u32 Mask);
void XEltwise_layer_InterruptDisable(XEltwise_layer *InstancePtr, u32 Mask);
void XEltwise_layer_InterruptClear(XEltwise_layer *InstancePtr, u32 Mask);
u32 XEltwise_layer_InterruptGetEnabled(XEltwise_layer *InstancePtr);
u32 XEltwise_layer_InterruptGetStatus(XEltwise_layer *InstancePtr);

#ifdef __cplusplus
}
#endif

#endif
