set here [file dirname [file normalize [info script]]]
set stage all
if {[info exists ::env(THRESHOLD_STAGE)]} { set stage $::env(THRESHOLD_STAGE) }
if {$stage ni {all csim synth cosim export}} { error "Invalid THRESHOLD_STAGE" }
file mkdir [file join $here build]
cd [file join $here build]
set marker [file join $here build "completed_$stage.txt"]
file delete -force $marker
open_project threshold
set_top threshold_binary
add_files [file join $here src threshold.cpp] -cflags "-I[file join $here src]"
add_files -tb [file join $here tb test_threshold.cpp] -cflags "-I[file join $here src]"
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
set vectors [file join $here vectors]
if {$stage in {all csim}} { csim_design -argv $vectors }
if {$stage in {all synth}} { csynth_design }
if {$stage in {all cosim}} { cosim_design -rtl verilog -tool xsim -argv $vectors }
if {$stage in {all export}} { export_design -format ip_catalog -rtl verilog }
set fp [open $marker w]
puts $fp "Completed $stage at [clock format [clock seconds]]"
close $fp
exit
