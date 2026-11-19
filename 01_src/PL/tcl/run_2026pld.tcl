# run_2026pld.tcl -- 2026pld integrated build: csim / synth / export for the conv kernel
#   stage from env HLS_STAGE = csim | synth | export (default csim)
#   XU from env XU (default 2)
set here  [file normalize "E:/fpga/my_project/2026pld/01_src/PL"]
set build [file normalize "E:/fpga/my_project/2026pld/03_build/hls"]
set xu 2
if {[info exists ::env(XU)]} { set xu $::env(XU) }
set stage csim
if {[info exists ::env(HLS_STAGE)]} { set stage $::env(HLS_STAGE) }
file mkdir $build
cd $build
set proj "conv_par_xu$xu"
if {$stage ne "csim"} { append proj "_$stage" }
open_project $proj -reset
set_top conv_layer
add_files [file join $here src conv_layer.cpp] -cflags "-DXU=$xu -I[file join $here src]"
if {$stage eq "csim"} {
    add_files -tb [file join $here tb ref_conv_layer.cpp] -cflags "-I[file join $here src] -I[file join $here tb]"
    add_files -tb [file join $here tb test_conv_par.cpp] -cflags "-I[file join $here src] -I[file join $here tb] -std=c++14"
}
open_solution solution1 -reset -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 6.757 -name default
if {$stage eq "csim"} {
    csim_design -O
}
if {$stage eq "synth"} {
    csynth_design
}
if {$stage eq "export"} {
    csynth_design
    export_design -format ip_catalog -rtl verilog -output [file join $build "ip_conv"]
}
set fp [open [file join $build "done_${stage}_xu${xu}.txt"] w]
puts $fp "stage=$stage xu=$xu at [clock format [clock seconds]]"
close $fp
exit
