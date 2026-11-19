// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2025 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
#ifndef __linux__

#include "xstatus.h"
#ifdef SDT
#include "xparameters.h"
#endif
#include "xpp_layer.h"

extern XPp_layer_Config XPp_layer_ConfigTable[];

#ifdef SDT
XPp_layer_Config *XPp_layer_LookupConfig(UINTPTR BaseAddress) {
	XPp_layer_Config *ConfigPtr = NULL;

	int Index;

	for (Index = (u32)0x0; XPp_layer_ConfigTable[Index].Name != NULL; Index++) {
		if (!BaseAddress || XPp_layer_ConfigTable[Index].Control_BaseAddress == BaseAddress) {
			ConfigPtr = &XPp_layer_ConfigTable[Index];
			break;
		}
	}

	return ConfigPtr;
}

int XPp_layer_Initialize(XPp_layer *InstancePtr, UINTPTR BaseAddress) {
	XPp_layer_Config *ConfigPtr;

	Xil_AssertNonvoid(InstancePtr != NULL);

	ConfigPtr = XPp_layer_LookupConfig(BaseAddress);
	if (ConfigPtr == NULL) {
		InstancePtr->IsReady = 0;
		return (XST_DEVICE_NOT_FOUND);
	}

	return XPp_layer_CfgInitialize(InstancePtr, ConfigPtr);
}
#else
XPp_layer_Config *XPp_layer_LookupConfig(u16 DeviceId) {
	XPp_layer_Config *ConfigPtr = NULL;

	int Index;

	for (Index = 0; Index < XPAR_XPP_LAYER_NUM_INSTANCES; Index++) {
		if (XPp_layer_ConfigTable[Index].DeviceId == DeviceId) {
			ConfigPtr = &XPp_layer_ConfigTable[Index];
			break;
		}
	}

	return ConfigPtr;
}

int XPp_layer_Initialize(XPp_layer *InstancePtr, u16 DeviceId) {
	XPp_layer_Config *ConfigPtr;

	Xil_AssertNonvoid(InstancePtr != NULL);

	ConfigPtr = XPp_layer_LookupConfig(DeviceId);
	if (ConfigPtr == NULL) {
		InstancePtr->IsReady = 0;
		return (XST_DEVICE_NOT_FOUND);
	}

	return XPp_layer_CfgInitialize(InstancePtr, ConfigPtr);
}
#endif

#endif

