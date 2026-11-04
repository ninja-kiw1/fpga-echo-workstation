# ==============================================================
# Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2.1 (64-bit)
# Tool Version Limit: 2025.11
# Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
# Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
# 
# ==============================================================
CSIM_DESIGN = 1

__SIM_FPO__ = 1

__SIM_MATHHLS__ = 1

__SIM_FFT__ = 1

__SIM_FIR__ = 1

__SIM_DDS__ = 1

__USE_CLANG__ = 1

__USE_VCXX_CLANG__ = 1

ObjDir = obj

HLS_SOURCES = ../../../../../tb_network_dump.cpp ../../../../../../../../PLD_share_20260929/PLD/medical_fpga/deployment/unet_eltwise/src/eltwise_layer.cpp ../../../../../../../../PLD_share_20260929/PLD/medical_fpga/deployment/unet_conv/src/conv_layer.cpp

override TARGET := csim.exe

AUTOPILOT_ROOT := E:/fpga/vivado/2025.2.1/Vitis
AUTOPILOT_MACH := win64
ifdef AP_GCC_M32
  AUTOPILOT_MACH := Linux_x86
  IFLAG += -m32
endif
ifndef AP_GCC_PATH
  AP_GCC_PATH := E:/fpga/vivado/2025.2.1/Vitis/tps/mingw/10.0.0/win64.o/nt/bin
endif
AUTOPILOT_TOOL := ${AUTOPILOT_ROOT}/${AUTOPILOT_MACH}/tools
AP_CLANG_PATH := ${AUTOPILOT_ROOT}/win64/tools/clang-16/bin
AUTOPILOT_TECH := ${AUTOPILOT_ROOT}/common/technology


IFLAG += -I "${AUTOPILOT_ROOT}/include"
IFLAG += -I "${AUTOPILOT_ROOT}/include/ap_sysc"
IFLAG += -I "${AUTOPILOT_TECH}/generic/SystemC"
IFLAG += -I "${AUTOPILOT_TECH}/generic/SystemC/AESL_FP_comp"
IFLAG += -I "${AUTOPILOT_TECH}/generic/SystemC/AESL_comp"
IFLAG += -I "${AUTOPILOT_TOOL}/auto_cc/include"
IFLAG += -D__HLS_COSIM__

IFLAG += -D__HLS_CSIM__

IFLAG += -D__VITIS_HLS__

IFLAG += -D__SIM_FPO__

IFLAG += -D__SIM_FFT__

IFLAG += -D__SIM_FIR__

IFLAG += -D__SIM_DDS__

IFLAG += -D__DSP48E2__
LFLAG += -Wl,--stack,0x40000000
AP_ENABLE_OPTIMIZED := 1
IFLAG += -DNT
LFLAG += -Wl,--enable-auto-import 
DFLAG += -D__xilinx_ip_top= -DAESL_TB
CCFLAG += -Werror=return-type
CCFLAG += -Wno-abi
CCFLAG += -fdebug-default-version=4
CCFLAG += --sysroot=E:/fpga/vivado/2025.2.1/Vitis/tps/mingw/10.0.0/win64.o/nt
CCFLAG += -Werror=uninitialized
CCFLAG += -Wno-c++11-narrowing
CCFLAG += -Wno-error=sometimes-uninitialized
LFLAG += --sysroot=E:/fpga/vivado/2025.2.1/Vitis/tps/mingw/10.0.0/win64.o/nt
CCFLAG += --target=x86_64-w64-windows-gnu
LFLAG += --target=x86_64-w64-windows-gnu



include ./makefile.rules

all: $(TARGET)



$(ObjDir)/tb_network_dump.o: ../../../../../tb_network_dump.cpp $(ObjDir)/.dir csim.mk
	$(Echo) "   Compiling ../../../../../tb_network_dump.cpp in $(BuildMode) mode" $(AVE_DIR_DLOG)
	$(Verb)  $(CXX) -std=gnu++17 ${CCFLAG} -c -MMD -std=c++14 -Wno-unknown-pragmas -Wno-unknown-pragmas  $(IFLAG) $(DFLAG) -DNDEBUG $< -o $@ ; \

-include $(ObjDir)/tb_network_dump.d

$(ObjDir)/eltwise_layer.o: ../../../../../../../../PLD_share_20260929/PLD/medical_fpga/deployment/unet_eltwise/src/eltwise_layer.cpp $(ObjDir)/.dir csim.mk
	$(Echo) "   Compiling ../../../../../../../../PLD_share_20260929/PLD/medical_fpga/deployment/unet_eltwise/src/eltwise_layer.cpp in $(BuildMode) mode" $(AVE_DIR_DLOG)
	$(Verb)  $(CXX) -std=gnu++17 ${CCFLAG} -c -MMD -IE:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/unet_eltwise/src  -fhls-csim -fhlstoplevel=conv_layer $(IFLAG) $(DFLAG) -DNDEBUG $< -o $@ ; \

-include $(ObjDir)/eltwise_layer.d

$(ObjDir)/conv_layer.o: ../../../../../../../../PLD_share_20260929/PLD/medical_fpga/deployment/unet_conv/src/conv_layer.cpp $(ObjDir)/.dir csim.mk
	$(Echo) "   Compiling ../../../../../../../../PLD_share_20260929/PLD/medical_fpga/deployment/unet_conv/src/conv_layer.cpp in $(BuildMode) mode" $(AVE_DIR_DLOG)
	$(Verb)  $(CXX) -std=gnu++17 ${CCFLAG} -c -MMD -IE:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/unet_conv/src  -fhls-csim -fhlstoplevel=conv_layer $(IFLAG) $(DFLAG) -DNDEBUG $< -o $@ ; \

-include $(ObjDir)/conv_layer.d
