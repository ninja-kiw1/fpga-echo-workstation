# run_hls_elt.tcl -- eltwise E2: csim / synth / cosim / export into ai/build/elt_ip_e2
# (never touches ai/build/elt_ip, which holds the IP of the accepted xu2dp bitstream)
# Stage from env ELT_STAGE: csim | synth | cosim | export ; ELT_RANDOM = random case count
set here [file dirname [file normalize [info script]]]
set ai   [file normalize [file join $here ..]]
set vec  [file normalize [file join $ai ../../../medical_fpga/deployment/unet_eltwise/vectors]]
set stage csim
if {[info exists ::env(ELT_STAGE)]} { set stage $::env(ELT_STAGE) }
if {$stage ni {csim synth cosim export}} { error "ELT_STAGE must be csim, synth, cosim or export" }
set nrand 300
if {[info exists ::env(ELT_RANDOM)]} { set nrand $::env(ELT_RANDOM) }
file mkdir [file join $ai build]
cd [file join $ai build]
set marker [file join $ai build "elt_e2_$stage.done"]
file delete -force $marker
open_project elt_ip_e2
set_top eltwise_layer
add_files [file join $here src eltwise_layer.cpp] -cflags "-I[file join $here src]"
add_files -tb [file join $here tb test_eltwise_e2.cpp] -cflags "-I[file join $here src] -std=c++14"
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
if {$stage eq "csim"}  { csim_design -O -argv "$vec cases_all.txt $nrand" }
if {$stage eq "synth"} { csynth_design }
if {$stage eq "cosim"} { cosim_design -rtl verilog -tool xsim -argv "$vec cases_cosim.txt $nrand 16384" }
if {$stage eq "export"} { export_design -format ip_catalog -rtl verilog }
set fp [open $marker w]
puts $fp "elt E2 $stage completed at [clock format [clock seconds]]"
close $fp
exit
