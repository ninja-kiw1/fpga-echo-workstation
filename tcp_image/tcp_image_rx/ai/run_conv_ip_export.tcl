# run_conv_ip_export.tcl -- 用 PLD 的**未修改**源码，导出 conv_layer IP（给我们的 Vivado 用）
set here [file dirname [file normalize [info script]]]
set pld  [file normalize [file join [file dirname [info script]] ../../../medical_fpga/deployment/unet_conv]]
file mkdir [file join $here build]
cd [file join $here build]
open_project conv_ip -reset
set_top conv_layer
add_files [file join $pld src conv_layer.cpp] -cflags "-I[file join $pld src]"
open_solution solution1 -reset -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
csynth_design
export_design -format ip_catalog -rtl verilog
set fp [open [file join $here build "completed_ip.txt"] w]
puts $fp "conv ip exported at [clock format [clock seconds]]"
close $fp
exit
