# run_conv_synth.tcl -- 只做综合，验证流接口版 conv_layer 能编过、能综合
set here [file dirname [file normalize [info script]]]
file mkdir [file join $here build]
cd [file join $here build]
open_project conv_stream
set_top conv_layer
add_files [file join $here src conv_layer_stream.cpp] -cflags "-I[file join $here src] -std=c++14"
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
csynth_design
set fp [open [file join $here build "completed_synth.txt"] w]
puts $fp "synth done at [clock format [clock seconds]]"
close $fp
exit
