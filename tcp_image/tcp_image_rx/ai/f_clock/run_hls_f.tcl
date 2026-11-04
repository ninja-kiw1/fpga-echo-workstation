# run_hls_f.tcl -- F 提频（2026-10-16）：四个 HLS 核按新时钟周期重新综合 / 协同仿真 / 导出。
# 源码、编译选项、接口配置与已部署 r2 的 IP 完全一样，只改 create_clock；工程放在 ai/build/f_<核>_p<周期>，
# 不碰已验收的 IP 仓库（conv_ip_xu2 / elt_ip_e2b / pp_ip_v3 / vis_ip_r2）。
# 环境变量：F_CORE = conv | elt | pp | vis，F_PERIOD = 周期 ns（如 6.757），F_STAGE = synth | cosim | export
set here [file dirname [file normalize [info script]]]
set ai   [file normalize [file join $here ..]]
set rx   [file normalize [file join $ai ..]]
foreach v {F_CORE F_PERIOD F_STAGE} { if {![info exists ::env($v)]} { error "$v not set" } }
set core $::env(F_CORE); set period $::env(F_PERIOD); set stage $::env(F_STAGE)
if {$core ni {conv elt pp vis}} { error "F_CORE must be conv, elt, pp or vis" }
if {$stage ni {synth cosim export}} { error "F_STAGE must be synth, cosim or export" }
set tag "f_${core}_p[string map {. {}} $period]"
file mkdir [file join $ai build]
cd [file join $ai build]
set marker [file join $here "${tag}_$stage.done"]
file delete -force $marker
open_project $tag
switch $core {
  conv {
    set_top conv_layer
    set s [file join $ai opt_hls src]; set t [file join $ai opt_hls_conv_r1 tb]  ;# r1 的测试台（协同仿真版：缓冲按 depth 开、argv 限用例数），被测源码仍是 V12
    add_files [file join $s conv_layer.cpp] -cflags "-DXU=2 -I$s"
    add_files -tb [file join $t ref_conv_layer.cpp] -cflags "-I$s -I$t"
    add_files -tb [file join $t test_conv_par.cpp] -cflags "-I$s -I$t -std=c++14"
  }
  elt {
    set_top eltwise_layer
    set s [file join $ai opt_hls_elt_e2b src]; set t [file join $ai opt_hls_elt_e2b tb]
    add_files [file join $s eltwise_layer.cpp] -cflags "-I$s"
    add_files -tb [file join $t test_eltwise_e2.cpp] -cflags "-I$s -std=c++14"
  }
  pp {
    set_top pp_layer
    set s [file join $ai pp_hls src]
    add_files [file join $s pp_layer.cpp] -cflags "-I$s"
    add_files -tb [file join $ai pp_hls tb test_pp.cpp] -cflags "-I$s -std=c++14"
    add_files -tb [file join $rx src ai_view.c] -cflags "-I[file join $rx src] -D_GNU_SOURCE"
  }
  vis {
    set_top vis_layer
    set s [file join $ai vis_hls src]
    add_files [file join $s vis_layer.cpp] -cflags "-I$s"
    add_files -tb [file join $ai vis_hls tb test_vis.cpp] -cflags "-I$s -std=c++14"
    add_files -tb [file join $rx src ai_view.c] -cflags "-I[file join $rx src] -D_GNU_SOURCE"
  }
}
open_solution solution1 -flow_target vivado
set_part {xazu3eg-sfvc784-1-i}
create_clock -period $period -name default
if {$core eq "vis"} { config_interface -m_axi_addr64=false }
if {$stage eq "synth"} { csynth_design }
if {$stage eq "cosim"} {
  switch $core {
    conv { set n 20; if {[info exists ::env(CONV_CASES)]} { set n $::env(CONV_CASES) }
           cosim_design -rtl verilog -tool xsim -argv "$n" }
    elt  { set vec [file normalize [file join $ai ../../../medical_fpga/deployment/unet_eltwise/vectors]]
           set nr 0; if {[info exists ::env(ELT_RANDOM)]} { set nr $::env(ELT_RANDOM) }   ;# E2 的协同仿真门槛用 0 个随机用例
           cosim_design -rtl verilog -tool xsim -argv "$vec cases_cosim.txt $nr 16384" }
    pp   { cosim_design -rtl verilog -tool xsim -argv "[file join $ai pp_hls vectors] cases_cosim.txt" }
    vis  { cosim_design -rtl verilog -tool xsim -argv "[file join $ai vis_hls vectors] cases_cosim.txt" }
  }
}
if {$stage eq "export"} { export_design -format ip_catalog -rtl verilog }
set fp [open $marker w]
puts $fp "$tag $stage completed at [clock format [clock seconds]]"
close $fp
exit
