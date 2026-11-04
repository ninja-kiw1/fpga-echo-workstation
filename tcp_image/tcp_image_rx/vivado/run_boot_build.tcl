# =============================================================================
# run_boot_build.tcl -- full rebuild (BD -> synth -> impl -> bitstream -> XSA)
# for the full-PS-config design.  Run head-less so the MCP session cannot kill it:
#   vivado -mode batch -source run_boot_build.tcl -log boot_build.log -journal boot_build.jou
# =============================================================================
set root [file normalize [file join [file dirname [info script]] ..]]

catch {close_project -quiet}
source "$root/bd/build_boot_design.tcl"

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
report_timing_summary -file "$root/vivado/boot_timing.rpt" -max_paths 5
write_bitstream -force -bin_file "$root/bitstream/boot_design.bit"
puts "### BITSTREAM done"

write_hw_platform -fixed -include_bit -force -file "$root/bitstream/boot_design.xsa"
puts "### XSA done"

puts "### BOOT_BUILD_DONE"
