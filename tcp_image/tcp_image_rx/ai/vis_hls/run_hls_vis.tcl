# run_hls_vis.tcl -- vis_layer (r2): csim / synth (stage from env VIS_STAGE), project in ai/build/vis_ip
set here [file dirname [file normalize [info script]]]
set ai   [file normalize [file join $here ..]]
set rx   [file normalize [file join $ai ..]]
set stage csim
if {[info exists ::env(VIS_STAGE)]} { set stage $::env(VIS_STAGE) }
if {$stage ni {csim synth evaluate cosim}} { error "VIS_STAGE must be csim, synth, evaluate or cosim" }
file mkdir [file join $ai build]
cd [file join $ai build]
set marker [file join $ai build "vis_$stage.done"]
file delete -force $marker
open_project vis_ip
set_top vis_layer
add_files [file join $here src vis_layer.cpp] -cflags "-I[file join $here src]"
add_files -tb [file join $here tb test_vis.cpp] -cflags "-I[file join $here src] -std=c++14"
add_files -tb [file join $rx src ai_view.c] -cflags "-I[file join $rx src] -D_GNU_SOURCE"
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
config_interface -m_axi_addr64=false
if {$stage eq "csim"}  { csim_design -O -argv "[file join $here vectors]" }
if {$stage eq "synth"} { csynth_design }
if {$stage eq "cosim"} { csynth_design; set clist cases_cosim.txt
    if {[info exists ::env(VIS_COSIM_LIST)]} { set clist $::env(VIS_COSIM_LIST) }
    cosim_design -rtl verilog -tool xsim -argv "[file join $here vectors] $clist" }
if {$stage eq "evaluate"} { csynth_design; export_design -format ip_catalog -rtl verilog -flow syn }
set fp [open $marker w]
puts $fp "vis $stage completed at [clock format [clock seconds]]"
close $fp
exit
