# =============================================================================
# run_build.tcl  --  full rebuild of the ZynqMP image-processor design.
# Run head-less (so the MCP session timeout cannot kill it):
#   vivado -mode batch -source run_build.tcl -log build.log -journal build.jou
# =============================================================================
set root [file normalize [file join [file dirname [info script]] ..]]

catch {close_project -quiet}
source "$root/bd/build_full_design.tcl"

puts "### launching synth_1"
launch_runs synth_1 -jobs 4
wait_on_run synth_1
puts "### SYNTH: [get_property STATUS [get_runs synth_1]] / [get_property PROGRESS [get_runs synth_1]]"

puts "### launching impl_1"
launch_runs impl_1 -jobs 4
wait_on_run impl_1
puts "### IMPL: [get_property STATUS [get_runs impl_1]] / [get_property PROGRESS [get_runs impl_1]]"

puts "### writing bitstream"
open_run impl_1
write_bitstream -force -bin_file "$root/bitstream/system_wrapper.bit"
puts "### BIT_DONE"
