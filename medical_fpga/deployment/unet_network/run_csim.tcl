# Whole-network C simulation of unet_conv + unet_eltwise (no synthesis here;
# each kernel is synthesized in its own project).
# NETWORK_LIST selects the image list inside vectors/ (default images.txt).
set here [file dirname [file normalize [info script]]]
set list images.txt
if {[info exists ::env(NETWORK_LIST)]} { set list $::env(NETWORK_LIST) }
set deploy [file dirname $here]
file mkdir [file join $here build]
cd [file join $here build]
set marker [file join $here build "completed_csim_[file rootname $list].txt"]
file delete -force $marker
open_project unet_network
set_top conv_layer
add_files [file join $deploy unet_conv src conv_layer.cpp] -cflags "-I[file join $deploy unet_conv src]"
add_files [file join $deploy unet_eltwise src eltwise_layer.cpp] -cflags "-I[file join $deploy unet_eltwise src]"
add_files -tb [file join $here tb test_network.cpp] -cflags "-std=c++14"
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period 10 -name default
csim_design -O -argv "[file join $here vectors] $list"
set fp [open $marker w]
puts $fp "Completed csim $list at [clock format [clock seconds]]"
close $fp
exit
