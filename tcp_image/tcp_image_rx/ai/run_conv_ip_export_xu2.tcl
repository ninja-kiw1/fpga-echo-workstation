# run_conv_ip_export_xu2.tcl -- 导出**并行化版** conv_layer IP（V9b: 整行一条流水线 + 循环槽位滑窗）
#
#   与 run_conv_ip_export.tcl 的区别：源码换成 ai/opt_hls/src/conv_layer.cpp（-DXU=2），
#   导出到**新的** IP 仓库目录，不覆盖原来那份 PLD 原版 IP（便于 A/B 回退）。
set here [file dirname [file normalize [info script]]]
file mkdir [file join $here build]
cd [file join $here build]
open_project conv_ip_xu2 -reset
set_top conv_layer
add_files [file join $here opt_hls src conv_layer.cpp] -cflags "-DXU=2 -I[file join $here opt_hls src]"
open_solution solution1 -reset -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
csynth_design
export_design -format ip_catalog -rtl verilog
set fp [open [file join $here build "completed_ip_xu2.txt"] w]
puts $fp "conv ip (xu2) exported at [clock format [clock seconds]]"
close $fp
exit
