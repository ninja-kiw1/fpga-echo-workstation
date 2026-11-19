#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_bd_tcl.py -- 由 11-13 包的 build_ai_sys_f_novis.tcl 生成 2026pld 的整片构建脚本。

只改"路径与 IP 来源"这几处，BD 结构、PS 预设、时钟、地址映射、连线一律照抄。
"""
import io
import os
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else \
    "E:/fpga/旧版归档_20261119/01_版本档案/V00_分享包底版_20261113/fpga-echo-workstation/tcp_image/tcp_image_rx/ai/bd/build_ai_sys_f_novis.tcl"
DST = sys.argv[2] if len(sys.argv) > 2 else "E:/fpga/my_project/2026pld/01_src/PL/tcl/build_2026pld.tcl"

with io.open(SRC, "r", encoding="utf-8") as f:
    s = f.read().replace("\r\n", "\n")

old_paths = '''set here    [file dirname [file normalize [info script]]]
set R       [file normalize [file join [file dirname [info script]] ../..]]
set share   [file normalize [file join [file dirname [info script]] ../../../..]]
set preset_file [file join $share edgeboard board_files fz3b a.0 preset.xml]'''
new_paths = '''# ---- 2026pld：只把路径与 IP 来源换成本工程，其余与 f150nv 原脚本完全一致 ----
set here    [file dirname [file normalize [info script]]]
set ROOT    [file normalize [file join $here ../../..]]      ;# tcl 在 01_src/PL/tcl -> 上三级 = 2026pld
set R       [file join $ROOT 01_src PL]                        ;# rtl_lb / iprepo / edgeboard
set share   [file join $ROOT 01_src PL]
set OUT_BIT [file join $ROOT 02_bitstream]
set OUT_RPT [file join $ROOT 03_build report]
set preset_file [file join $share edgeboard board_files fz3b a.0 preset.xml]'''
assert s.count(old_paths) == 1
s = s.replace(old_paths, new_paths)

old_ips = '''set fips [argval --ips orig]
if {$fips eq "orig"} {
    set ip_repos [list $R/ai/build/conv_ip_xu2/solution1/impl/ip $R/ai/build/elt_ip_e2b/solution1/impl/ip $R/ai/build/pp_ip_v3]
} else {
    set ip_repos {}
    foreach c {conv elt pp} {
        set d $R/ai/build/f_${c}_${fips}/solution1/impl/ip
        if {![file exists $d/component.xml]} { error "IP repo missing: $d" }
        lappend ip_repos $d
    }
}'''
new_ips = '''# conv = 本次改过的（f150nv + 去 %3 + 扇形寄存器）；elt / pp = f150nv 原本那两个，一字未改。
# 三个 IP 都打包在 01_src/PL/iprepo/ 下（conv 由 run_hls.bat export + run_ippack.tcl 重新打包进去）
set ip_repos [list [file join $R iprepo]]   ;# iprepo/{conv,elt,pp}/component.xml
foreach d $ip_repos {
    if {![file exists $d]} { error "IP repo missing: $d" }
}'''
assert s.count(old_ips) == 1
s = s.replace(old_ips, new_ips)

# --ips 换掉之后 $fips 就不存在了，原脚本的这行 puts 会让 Tcl 报 "no such variable"
old_puts = 'puts "### F clock: $fmhz MHz, tag $ftag, ips $fips"'
assert s.count(old_puts) == 1, "puts 行与预期不符"
s = s.replace(old_puts, 'puts "### F clock: $fmhz MHz, tag $ftag, ips custom(conv+elt+pp)"')

old_prj = 'set prjdir  $R/vivado'
assert s.count(old_prj) == 1
s = s.replace(old_prj, 'set prjdir  [file join $ROOT 03_build vivado]')

old_mk = 'file mkdir $R/bitstream'
assert s.count(old_mk) == 1
s = s.replace(old_mk, 'file mkdir $OUT_BIT\nfile mkdir $OUT_RPT')

old_tail = '''file copy -force [file join $impl_dir ${bdname}_wrapper.bit] $R/bitstream/ai_sys_$ftag.bit
file copy -force [file join $impl_dir ${bdname}_wrapper.bin] $R/bitstream/ai_sys_$ftag.bin
report_utilization -file $R/vivado/ai_sys_${ftag}_util.rpt
report_timing_summary -file $R/vivado/ai_sys_${ftag}_timing.rpt
report_utilization -hierarchical -hierarchical_depth 2 -file $R/vivado/ai_sys_${ftag}_util_hier.rpt'''
new_tail = '''file copy -force [file join $impl_dir ${bdname}_wrapper.bit] $OUT_BIT/ai_sys_$ftag.bit
file copy -force [file join $impl_dir ${bdname}_wrapper.bin] $OUT_BIT/ai_sys_$ftag.bin
report_utilization -file $OUT_RPT/ai_sys_${ftag}_util.rpt
report_timing_summary -file $OUT_RPT/ai_sys_${ftag}_timing.rpt
report_utilization -hierarchical -hierarchical_depth 2 -file $OUT_RPT/ai_sys_${ftag}_util_hier.rpt'''
assert s.count(old_tail) == 1
s = s.replace(old_tail, new_tail)

# 默认 tag 换成本工程的
s = s.replace('set ftag [argval --tag f150nv]', 'set ftag [argval --tag pld2026]')

os.makedirs(os.path.dirname(DST), exist_ok=True)
with io.open(DST, "w", encoding="utf-8", newline="\r\n") as f:
    f.write("# 2026pld 整片构建脚本（由 build_ai_sys_f_novis.tcl 机械改写路径/ IP 来源）\n" + s)
print("wrote", DST)
