# P4-2: 整网 C 仿真(只跑 csim, 不综合), 逐节点 dump golden。
#   vitis-run.bat --mode hls --tcl run_csim_small.tcl
# 两个算子用 PLD 未修改源码; testbench 只负责驱动与 dump。
set here   [file dirname [file normalize [info script]]]
set deploy [file normalize [file join $here ../../../.. medical_fpga deployment]]
set vecdir [file join $here vectors_small]
if {[info exists ::env(P4_VECDIR)]} { set vecdir $::env(P4_VECDIR) }
puts "### csim vecdir = $vecdir"

file delete -force [file join $here build_csim]
file mkdir [file join $here build_csim]
cd [file join $here build_csim]

open_project net_small
set_top conv_layer
add_files [file join $deploy unet_conv    src conv_layer.cpp]    -cflags "-I[file join $deploy unet_conv    src]"
add_files [file join $deploy unet_eltwise src eltwise_layer.cpp] -cflags "-I[file join $deploy unet_eltwise src]"
add_files -tb [file join $here tb_network_dump.cpp] -cflags "-std=c++14"
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
csim_design -O -argv $vecdir
puts "CSIM_SMALL_DONE"
exit
