// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2025 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
#ifndef XPP_LAYER_H
#define XPP_LAYER_H

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
#include "xpp_layer_hw.h"

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
} XPp_layer_Config;
#endif

typedef struct {
    u64 Control_BaseAddress;
    u32 IsReady;
} XPp_layer;

typedef u32 word_type;

/***************** Macros (Inline Functions) Definitions *********************/
#ifndef __linux__
#define XPp_layer_WriteReg(BaseAddress, RegOffset, Data) \
    Xil_Out32((BaseAddress) + (RegOffset), (u32)(Data))
#define XPp_layer_ReadReg(BaseAddress, RegOffset) \
    Xil_In32((BaseAddress) + (RegOffset))
#else
#define XPp_layer_WriteReg(BaseAddress, RegOffset, Data) \
    *(volatile u32*)((BaseAddress) + (RegOffset)) = (u32)(Data)
#define XPp_layer_ReadReg(BaseAddress, RegOffset) \
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
int XPp_layer_Initialize(XPp_layer *InstancePtr, UINTPTR BaseAddress);
XPp_layer_Config* XPp_layer_LookupConfig(UINTPTR BaseAddress);
#else
int XPp_layer_Initialize(XPp_layer *InstancePtr, u16 DeviceId);
XPp_layer_Config* XPp_layer_LookupConfig(u16 DeviceId);
#endif
int XPp_layer_CfgInitialize(XPp_layer *InstancePtr, XPp_layer_Config *ConfigPtr);
#else
int XPp_layer_Initialize(XPp_layer *InstancePtr, const char* InstanceName);
int XPp_layer_Release(XPp_layer *InstancePtr);
#endif

void XPp_layer_Start(XPp_layer *InstancePtr);
u32 XPp_layer_IsDone(XPp_layer *InstancePtr);
u32 XPp_layer_IsIdle(XPp_layer *InstancePtr);
u32 XPp_layer_IsReady(XPp_layer *InstancePtr);
void XPp_layer_EnableAutoRestart(XPp_layer *InstancePtr);
void XPp_layer_DisableAutoRestart(XPp_layer *InstancePtr);
u32 XPp_layer_Get_return(XPp_layer *InstancePtr);

void XPp_layer_Set_in_r(XPp_layer *InstancePtr, u64 Data);
u64 XPp_layer_Get_in_r(XPp_layer *InstancePtr);
void XPp_layer_Set_out_r(XPp_layer *InstancePtr, u64 Data);
u64 XPp_layer_Get_out_r(XPp_layer *InstancePtr);
void XPp_layer_Set_op(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_op(XPp_layer *InstancePtr);
void XPp_layer_Set_h(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_h(XPp_layer *InstancePtr);
void XPp_layer_Set_w(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_w(XPp_layer *InstancePtr);
void XPp_layer_Set_x0(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_x0(XPp_layer *InstancePtr);
void XPp_layer_Set_y0(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_y0(XPp_layer *InstancePtr);
void XPp_layer_Set_cw(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_cw(XPp_layer *InstancePtr);
void XPp_layer_Set_ch(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_ch(XPp_layer *InstancePtr);
void XPp_layer_Set_k(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_k(XPp_layer *InstancePtr);
void XPp_layer_Set_thr(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_thr(XPp_layer *InstancePtr);
void XPp_layer_Set_tn(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_tn(XPp_layer *InstancePtr);
void XPp_layer_Set_ya(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_ya(XPp_layer *InstancePtr);
void XPp_layer_Set_yb(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_yb(XPp_layer *InstancePtr);
void XPp_layer_Set_in_row0(XPp_layer *InstancePtr, u32 Data);
u32 XPp_layer_Get_in_row0(XPp_layer *InstancePtr);

void XPp_layer_InterruptGlobalEnable(XPp_layer *InstancePtr);
void XPp_layer_InterruptGlobalDisable(XPp_layer *InstancePtr);
void XPp_layer_InterruptEnable(XPp_layer *InstancePtr, u32 Mask);
void XPp_layer_InterruptDisable(XPp_layer *InstancePtr, u32 Mask);
void XPp_layer_InterruptClear(XPp_layer *InstancePtr, u32 Mask);
u32 XPp_layer_InterruptGetEnabled(XPp_layer *InstancePtr);
u32 XPp_layer_InterruptGetStatus(XPp_layer *InstancePtr);

#ifdef __cplusplus
}
#endif

#endif
