set here [file dirname [file normalize [info script]]]
set stage all
if {[info exists ::env(CONV_STAGE)]} { set stage $::env(CONV_STAGE) }
if {$stage ni {all csim synth cosim export}} { error "CONV_STAGE must be all, csim, synth, cosim, or export" }
file mkdir [file join $here build]
cd [file join $here build]
set marker [file join $here build "completed_$stage.txt"]
file delete -force $marker
open_project conv_layer
set_top conv_layer
add_files [file join $here src conv_layer.cpp] -cflags "-I[file join $here src]"
add_files -tb [file join $here tb test_conv_layer.cpp] -cflags "-I[file join $here src] -std=c++14"
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
set vectors [file join $here vectors]
# C simulation covers all real layers; RTL co-simulation uses the smaller list.
if {$stage in {all csim}} { csim_design -O -argv "$vectors cases_all.txt" }
if {$stage in {all synth}} { csynth_design }
if {$stage in {all cosim}} { cosim_design -rtl verilog -tool xsim -argv "$vectors cases_cosim.txt" }
if {$stage in {all export}} { export_design -format ip_catalog -rtl verilog }
set fp [open $marker w]
puts $fp "Completed $stage at [clock format [clock seconds]]"
close $fp
exit
