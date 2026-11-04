# run_hls_conv.tcl -- conv r1 (V12 + shallow m_axi buffers): csim / synth / cosim / export into ai/build/conv_ip_r1
# (never touches ai/build/conv_ip_xu2, which holds the IP of the accepted bitstream)
# Stage from env CONV_STAGE: csim | synth | cosim | export ; CONV_CASES = case count for cosim (default 20 small cases)
set here [file dirname [file normalize [info script]]]
set ai   [file normalize [file join $here ..]]
set stage csim
if {[info exists ::env(CONV_STAGE)]} { set stage $::env(CONV_STAGE) }
if {$stage ni {csim synth cosim export}} { error "CONV_STAGE must be csim, synth, cosim or export" }
set ncases 20
if {[info exists ::env(CONV_CASES)]} { set ncases $::env(CONV_CASES) }
file mkdir [file join $ai build]
cd [file join $ai build]
set marker [file join $ai build "conv_r1_$stage.done"]
file delete -force $marker
open_project conv_ip_r1
set_top conv_layer
add_files [file join $here src conv_layer.cpp] -cflags "-DXU=2 -I[file join $here src]"
add_files -tb [file join $here tb ref_conv_layer.cpp] -cflags "-I[file join $here src] -I[file join $here tb]"
add_files -tb [file join $here tb test_conv_par.cpp] -cflags "-I[file join $here src] -I[file join $here tb] -std=c++14"
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
# r1: 读缓冲深 64（>32 时适配器默认用块 RAM）-> 强制 LUTRAM
config_interface -m_axi_buffer_impl lutram
if {$stage eq "csim"}  { csim_design -O -argv "0" }
if {$stage eq "synth"} { csynth_design }
if {$stage eq "cosim"} { cosim_design -rtl verilog -tool xsim -argv "$ncases" }
if {$stage eq "export"} { export_design -format ip_catalog -rtl verilog }
set fp [open $marker w]
puts $fp "conv r1 $stage completed at [clock format [clock seconds]]"
close $fp
exit
