set example_dir [file dirname [file normalize [info script]]]
set project_dir [file join $example_dir vivado_project]
if {[file exists $project_dir]} {
    error "Project directory already exists. Open vivado_project/fz3b_led_blink.xpr instead."
}
create_project fz3b_led_blink $project_dir -part xazu3eg-sfvc784-1-i
set_property target_language Verilog [current_project]
add_files [file join $example_dir rtl led_blink.v]
add_files -fileset constrs_1 [file join $example_dir constraints fz3b.xdc]
add_files -fileset sim_1 [file join $example_dir sim tb_led_blink.v]
set_property top led_blink [get_filesets sources_1]
set_property top tb_led_blink [get_filesets sim_1]
set_property xsim.simulate.runtime 2us [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
