# run_hls_pp.tcl -- pp_layer prototype: csim / synth (stage from env PP_STAGE), project in ai/build/pp_ip
set here [file dirname [file normalize [info script]]]
set ai   [file normalize [file join $here ..]]
set rx   [file normalize [file join $ai ..]]
set stage csim
if {[info exists ::env(PP_STAGE)]} { set stage $::env(PP_STAGE) }
if {$stage ni {csim synth evaluate cosim}} { error "PP_STAGE must be csim, synth, evaluate or cosim" }
file mkdir [file join $ai build]
cd [file join $ai build]
set marker [file join $ai build "pp_$stage.done"]
file delete -force $marker
# PP_ONE_BUNDLE=1: r2 variant (both m_axi pointers in one bundle), separate project pp_ip_r2
set prj pp_ip
set defs ""
if {[info exists ::env(PP_ONE_BUNDLE)] && $::env(PP_ONE_BUNDLE) eq "1"} {
    set prj pp_ip_r2
    set defs " -DPP_ONE_BUNDLE"
    set marker [file join $ai build "pp_r2_$stage.done"]
}
open_project $prj
set_top pp_layer
add_files [file join $here src pp_layer.cpp] -cflags "-I[file join $here src]$defs"
add_files -tb [file join $here tb test_pp.cpp] -cflags "-I[file join $here src] -std=c++14$defs"
add_files -tb [file join $rx src ai_view.c] -cflags "-I[file join $rx src] -D_GNU_SOURCE"
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
if {$stage eq "csim"}  { csim_design -O -argv "[file join $here vectors]" }
if {$stage eq "synth"} { csynth_design }
if {$stage eq "cosim"} { csynth_design; cosim_design -rtl verilog -tool xsim -argv "[file join $here vectors] cases_cosim.txt" }
if {$stage eq "evaluate"} { csynth_design; export_design -format ip_catalog -rtl verilog -flow syn }
set fp [open $marker w]
puts $fp "pp $stage completed at [clock format [clock seconds]]"
close $fp
exit
