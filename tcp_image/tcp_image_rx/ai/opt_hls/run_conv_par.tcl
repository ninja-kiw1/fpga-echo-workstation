# run_conv_par.tcl —— 并行版 conv 算子: 逐位等价性 csim + csynth
#
#   XU     环境变量, 每周期并行几个输出列 (1/2/4), 默认 2
#   HLS_STAGE  csim | synth | export, 默认 csim
#
# 用法(主机):
#   vitis-run.bat --mode hls --tcl run_conv_par.tcl
set here [file dirname [file normalize [info script]]]
set xu 2
if {[info exists ::env(XU)]} { set xu $::env(XU) }
set stage csim
if {[info exists ::env(HLS_STAGE)]} { set stage $::env(HLS_STAGE) }

file mkdir [file join $here build]
cd [file join $here build]
set proj "conv_par_xu$xu"
# 可选工程名后缀: 上一次运行被强杀时旧工程目录里的文件可能还被占着,
# `open_project -reset` 删不掉就中止 -> 换个新名字即可绕开。
if {[info exists ::env(PROJ_TAG)] && $::env(PROJ_TAG) ne ""} {
    append proj "_" $::env(PROJ_TAG)
}
open_project $proj -reset
set_top conv_layer
add_files [file join $here src conv_layer.cpp] -cflags "-DXU=$xu -I[file join $here src]"
if {$stage eq "csim"} {
    add_files -tb [file join $here tb ref_conv_layer.cpp] -cflags "-I[file join $here src] -I[file join $here tb]"
    add_files -tb [file join $here tb test_conv_par.cpp] -cflags "-I[file join $here src] -I[file join $here tb] -std=c++14"
}
open_solution solution1 -reset -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
if {$stage eq "csim"} {
    csim_design -O
}
if {$stage eq "synth"} {
    csynth_design
}
if {$stage eq "export"} {
    csynth_design
    export_design -format ip_catalog -rtl verilog
}
set fp [open [file join $here build "done_${stage}_xu${xu}.txt"] w]
puts $fp "stage=$stage xu=$xu at [clock format [clock seconds]]"
close $fp
exit
