# F 第一步（2026-10-16）：打开 r2 的布线后网表，把 clk_pl_0 改成 125 / 148 MHz 重新做静态时序，
# 看哪些模块的路径超长。布线是按 100 MHz 做的，结果偏悲观，只用来画"瓶颈地图"。
set here [file dirname [file normalize [info script]]]
set dcp  [file normalize $here/../ai_sys_xu2dp_e2b_pp_r2/ai_sys_xu2dp_e2b_pp_r2.runs/impl_1/system_wrapper_routed.dcp]
open_checkpoint $dcp
set clk [get_clocks clk_pl_0]
set src [get_property SOURCE_PINS $clk]
puts "### clk_pl_0 source: $src period [get_property PERIOD $clk]"
foreach {tag period} {125 8.000 148 6.757} {
    create_clock -name clk_pl_0 -period $period [get_pins $src]
    set out [open $here/retime_r2_$tag.txt w]
    set paths [get_timing_paths -setup -max_paths 20000 -nworst 1 -slack_lesser_than 0 -group clk_pl_0]
    puts $out "period $period  failing endpoints(sampled) [llength $paths]"
    array unset cnt; array unset worst
    foreach p $paths {
        set sl [get_property SLACK $p]
        set s [get_property STARTPOINT_PIN $p]
        set e [get_property ENDPOINT_PIN $p]
        set m [lindex [split $e /] 1]
        set ms [lindex [split $s /] 1]
        set key "$ms -> $m"
        if {![info exists cnt($key)]} { set cnt($key) 0; set worst($key) 0 }
        incr cnt($key)
        if {$sl < $worst($key)} { set worst($key) $sl }
    }
    foreach k [lsort [array names cnt]] { puts $out [format "%-60s n=%6d worst=%.3f" $k $cnt($k) $worst($k)] }
    puts $out "\n=== top 30 paths ==="
    foreach p [lrange $paths 0 29] {
        puts $out [format "%.3f  lvl=%s  dly=%.3f  %s -> %s" [get_property SLACK $p] [get_property LOGIC_LEVELS $p] [get_property DATAPATH_DELAY $p] [get_property STARTPOINT_PIN $p] [get_property ENDPOINT_PIN $p]]
    }
    close $out
    report_timing_summary -no_detailed_paths -file $here/retime_r2_${tag}_summary.rpt
    puts "### done $tag"
}
exit
