// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2.1 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
#ifndef __linux__

#include "xstatus.h"
#ifdef SDT
#include "xparameters.h"
#endif
#include "xconv_layer.h"

extern XConv_layer_Config XConv_layer_ConfigTable[];

#ifdef SDT
XConv_layer_Config *XConv_layer_LookupConfig(UINTPTR BaseAddress) {
	XConv_layer_Config *ConfigPtr = NULL;

	int Index;

	for (Index = (u32)0x0; XConv_layer_ConfigTable[Index].Name != NULL; Index++) {
		if (!BaseAddress || XConv_layer_ConfigTable[Index].Control_BaseAddress == BaseAddress) {
			ConfigPtr = &XConv_layer_ConfigTable[Index];
			break;
		}
	}

	return ConfigPtr;
}

int XConv_layer_Initialize(XConv_layer *InstancePtr, UINTPTR BaseAddress) {
	XConv_layer_Config *ConfigPtr;

	Xil_AssertNonvoid(InstancePtr != NULL);

	ConfigPtr = XConv_layer_LookupConfig(BaseAddress);
	if (ConfigPtr == NULL) {
		InstancePtr->IsReady = 0;
		return (XST_DEVICE_NOT_FOUND);
	}

	return XConv_layer_CfgInitialize(InstancePtr, ConfigPtr);
}
#else
XConv_layer_Config *XConv_layer_LookupConfig(u16 DeviceId) {
	XConv_layer_Config *ConfigPtr = NULL;

	int Index;

	for (Index = 0; Index < XPAR_XCONV_LAYER_NUM_INSTANCES; Index++) {
		if (XConv_layer_ConfigTable[Index].DeviceId == DeviceId) {
			ConfigPtr = &XConv_layer_ConfigTable[Index];
			break;
		}
	}

	return ConfigPtr;
}

int XConv_layer_Initialize(XConv_layer *InstancePtr, u16 DeviceId) {
	XConv_layer_Config *ConfigPtr;

	Xil_AssertNonvoid(InstancePtr != NULL);

	ConfigPtr = XConv_layer_LookupConfig(DeviceId);
	if (ConfigPtr == NULL) {
		InstancePtr->IsReady = 0;
		return (XST_DEVICE_NOT_FOUND);
	}

	return XConv_layer_CfgInitialize(InstancePtr, ConfigPtr);
}
#endif

#endif

