set here [file dirname [file normalize [info script]]]
set stage all
if {[info exists ::env(MORPHOLOGY_STAGE)]} { set stage $::env(MORPHOLOGY_STAGE) }
if {$stage ni {all csim synth cosim export}} { error "Invalid MORPHOLOGY_STAGE" }
file mkdir [file join $here build]
cd [file join $here build]
set marker [file join $here build "completed_$stage.txt"]
file delete -force $marker
open_project morphology
set_top morphology_binary
add_files [file join $here src morphology.cpp] -cflags "-I[file join $here src]"
add_files -tb [file join $here tb test_morphology.cpp] -cflags "-I[file join $here src]"
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
