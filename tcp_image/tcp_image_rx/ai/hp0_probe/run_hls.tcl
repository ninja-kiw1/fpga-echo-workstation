# HLS: csim + csynth + export (stage from env HP0_STAGE: all | cosim). 10 ns, project ai/build/hp0_probe
set here [file dirname [file normalize [info script]]]
cd [file join $here .. build]
open_project hp0_probe -reset
set_top hp0_probe
add_files [file join $here hp0_probe.cpp]
add_files -tb [file join $here tb_hp0.cpp]
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
csim_design
csynth_design
if {[info exists ::env(HP0_STAGE)] && [string trim $::env(HP0_STAGE)] eq "cosim"} { cosim_design -rtl verilog -tool xsim }
export_design -format ip_catalog -rtl verilog
set fp [open [file join $here hp0_hls.done] w]; puts $fp done; close $fp
exit
