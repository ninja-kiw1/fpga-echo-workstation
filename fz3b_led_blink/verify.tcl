set example_dir [file dirname [file normalize [info script]]]
open_project [file join $example_dir vivado_project fz3b_led_blink.xpr]
launch_simulation
close_sim
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "Synthesis failed"
}
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "Implementation/bitstream failed"
}
open_run impl_1
report_timing_summary -file [file join $example_dir timing_summary.txt]
report_drc -file [file join $example_dir drc.txt]
close_project
