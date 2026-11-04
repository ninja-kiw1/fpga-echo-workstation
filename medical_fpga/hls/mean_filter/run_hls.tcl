set here [file dirname [file normalize [info script]]]
cd $here
set stage all
if {[info exists ::env(MEAN_FILTER_STAGE)]} { set stage $::env(MEAN_FILTER_STAGE) }
if {$stage ni {all csim synth cosim export}} { error "stage must be all, csim, synth, cosim, or export" }
file mkdir [file join $here build]
cd [file join $here build]
open_project mean_filter
set_top mean_filter
add_files [file join $here src mean_filter.cpp] -cflags "-I[file join $here src]"
add_files -tb [file join $here tb test_mean_filter.cpp] -cflags "-I[file join $here src]"
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
set vectors [file join $here vectors]
if {$stage in {all csim}} { csim_design -argv $vectors }
if {$stage in {all synth}} { csynth_design }
if {$stage in {all cosim}} { cosim_design -rtl verilog -tool xsim -argv $vectors }
if {$stage in {all export}} { export_design -format ip_catalog -rtl verilog }
exit
