# Read-only inspection of the completed implementation; never save timing overrides.
set envdir [file dirname [file normalize [info script]]]
set root [file dirname $envdir]
set rx [file join $root tcp_image tcp_image_rx]
set out [file join $envdir checks xu2dp_validation]
file mkdir $out
# Load the completed routed checkpoint directly; never save timing overrides.
open_checkpoint [file join $rx vivado ai_sys_xu2dp ai_sys_xu2dp.runs impl_1 system_wrapper_routed.dcp]
report_route_status -file [file join $out route_status.rpt]
report_drc -file [file join $out drc.rpt]
report_utilization -hierarchical -file [file join $out utilization_hierarchical.rpt]
report_timing_summary -check_timing_verbose -report_unconstrained -file [file join $out timing_original.rpt]

set fp [open [file join $out tensor_bram.tsv] w]
puts $fp [join {cell primitive MODE_FROM_PORT_WIDTHS READ_WIDTH_A WRITE_WIDTH_A READ_WIDTH_B WRITE_WIDTH_B} "\t"]
set brams [get_cells -hierarchical -filter {NAME =~ *bram_gen* && (REF_NAME == RAMB36E2 || REF_NAME == RAMB18E2)}]
if {[llength $brams] == 0} {error "No tensor BRAM primitives found"}
foreach cell $brams {
    # E2 primitives have no RAM_MODE parameter. Verify actual port widths.
    set widths {}
    foreach prop {READ_WIDTH_A WRITE_WIDTH_A READ_WIDTH_B WRITE_WIDTH_B} {
        set width [get_property $prop $cell]
        if {![string is integer -strict $width] || $width <= 0 || $width > 36} {
            error "Tensor BRAM port is not configured for dual read/write: $cell $prop=$width"
        }
        lappend widths $width
    }
    if {![string match *TRUE_DP* $cell]} {error "Tensor BRAM instance is not TRUE_DP: $cell"}
    set values [concat [list $cell [get_property REF_NAME $cell] TRUE_DP] $widths]
    puts $fp [join $values "\t"]
}
close $fp
puts "TENSOR_BRAM_PRIMITIVES [llength $brams]"

# The inherited implementation constraint is 100 MHz. Check the existing routed
# design at the documented board clock without changing the saved project/bitstream.
set clks [get_clocks -quiet clk_pl_0]
if {[llength $clks] != 1} {error "Expected exactly one clk_pl_0 clock"}
set pins [get_property SOURCE_PINS $clks]
if {[llength $pins] == 0} {error "No source pins for clk_pl_0"}
set period [expr {1000.0 / 101.376}]
create_clock -name clk_pl_0 -period $period -waveform [list 0 [expr {$period / 2.0}]] [get_pins $pins]
report_timing_summary -check_timing_verbose -report_unconstrained -file [file join $out timing_101_376mhz.rpt]
puts "VALIDATION_REPORTS_WRITTEN $out"
close_design
exit 0
