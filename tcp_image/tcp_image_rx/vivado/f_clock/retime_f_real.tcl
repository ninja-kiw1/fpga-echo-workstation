# F 复核（2026-10-17）：板上 pl0_ref 实际是 101.376 MHz（FSBL 设定，见交接第 100 行），不是 BD 里的 99.999 MHz。
# 打开 f150 / f125 布线后网表，把 clk_pl_0 改成 101.376 MHz（周期 9.8643 ns），MMCM 输出随之按比例变快，重做时序。
# 11-03：可用 -tclargs <tag...> 指定（如 f150nv），不给时仍是 f150 f125。
set here [file dirname [file normalize [info script]]]
set tags [expr {[llength $argv] > 0 ? $argv : {f150 f125}}]
foreach tag $tags {
    set dcp [file normalize $here/../ai_sys_$tag/ai_sys_$tag.runs/impl_1/system_wrapper_routed.dcp]
    open_checkpoint $dcp
    set src [get_property SOURCE_PINS [get_clocks clk_pl_0]]
    create_clock -name clk_pl_0 -period 9.8643 [get_pins $src]
    foreach c [get_clocks] { puts "### $tag clock [get_property NAME $c] period [get_property PERIOD $c]" }
    report_timing_summary -no_detailed_paths -file $here/retime_${tag}_101376_summary.rpt
    report_timing -max_paths 5 -nworst 1 -file $here/retime_${tag}_101376_paths.rpt
    set p [get_timing_paths -setup -max_paths 1]
    set h [get_timing_paths -hold -max_paths 1]
    puts "### $tag @pl0_ref 101.376 MHz: WNS [get_property SLACK $p] WHS [get_property SLACK $h]"
    close_design
}
exit
