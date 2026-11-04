# =============================================================================
# run_axil_build.tcl -- full rebuild of the PS<->PL register (AXI4-Lite) design
#   vivado -mode batch -source run_axil_build.tcl -log axil_build.log
# =============================================================================
set root [file normalize [file join [file dirname [info script]] ..]]

catch {close_project -quiet}
source "$root/bd/build_axil_design.tcl"

puts "### launching synth_1"
launch_runs synth_1 -jobs 4
wait_on_run synth_1
puts "### SYNTH: [get_property STATUS [get_runs synth_1]] / [get_property PROGRESS [get_runs synth_1]]"
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "synth failed" }

puts "### launching impl_1"
launch_runs impl_1 -jobs 4
wait_on_run impl_1
puts "### IMPL: [get_property STATUS [get_runs impl_1]] / [get_property PROGRESS [get_runs impl_1]]"
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "impl failed" }

open_run impl_1
set runbit "$root/vivado/img_proc_axil/img_proc_axil.runs/impl_1/system_wrapper.bit"
write_bitstream -force -bin_file $runbit
puts "### bitstream written"

file copy -force "${runbit}" "$root/bitstream/img_proc_axil.bit"
file copy -force "$root/vivado/img_proc_axil/img_proc_axil.runs/impl_1/system_wrapper.bin" "$root/bitstream/img_proc_axil.bin"
puts "### axil bitstream copied to bitstream/"
puts "### AXIL_BUILD_DONE"
