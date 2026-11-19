# ==============================================================
# Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2.1 (64-bit)
# Tool Version Limit: 2025.11
# Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
# Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
# 
# ==============================================================
source -notrace "E:/fpga/vivado/2025.2.1/Vitis/common/scripts/ipxhls.tcl"
set ip_out_dir "E:/fpga/my_project/2026pld/03_build/hls/conv_par_xu2_export/solution1/impl/ip"
set data_file "E:/fpga/my_project/2026pld/03_build/hls/conv_par_xu2_export/solution1/solution1_data.json"
set src_dir [file dir $data_file]
set ip_types {vitis sysgen}
set ippack_opts_dict {}
if { [catch {::ipx::utils::package_hls_ip $ip_out_dir $data_file $ip_types $src_dir $ippack_opts_dict} res] } {
  puts "Caught error:\n$::errorInfo"
  error $res
}
