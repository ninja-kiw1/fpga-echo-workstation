# build_ai_sys_f_novis.tcl -- 2026-11-03 资源精简：= f150（build_ai_sys_f_clk.tcl）去掉 vis_layer（PL 配准引擎 + 图形叠加）。
#   心脏主线不用 vis_layer；眼底分块叠加在没有 vis 时走 PS（job_tile.c ovl_ps）。其余 IP、时钟、地址映射与 f150 完全相同，
#   0xA004_0000 不再有从机（服务器按固件指纹 ai_sys_vis.sha256 判断，新固件不登记即不碰该地址）。
#   控制交叉开关 1 进 4 出，数据互联 9 个从口（原 vis 的从口去掉）。
#   用法：vivado -mode batch -source build_ai_sys_f_novis.tcl -tclargs --mhz 150 --tag f150nv --ips p6757
#   以下为 build_ai_sys_f_clk.tcl 原说明。
#
# build_ai_sys_f_clk.tcl -- F 提频（2026-10-16）= r2 + PL 内 MMCM（clk_wiz）：
#   pl_clk0（100 MHz，不改 boot.bin）→ clk_wiz_0 → clk_out1 = F_MHZ，全部 PL 逻辑、HPM0 / HPM1 两个主口时钟都接 clk_out1；
#   rst_pl 的 dcm_locked 接 clk_wiz 的 locked（MMCM 没锁定时整片保持复位）。功能、地址映射、接口与 r2 完全相同。
#   回滚 = 换回 r2 比特流（boot.bin / PS 配置不动）。
#   用法：vivado -mode batch -source build_ai_sys_f_clk.tcl -tclargs --mhz 148.5 --tag f148 [--ips orig|f<周期标记>] [--preflight]
#     --ips orig  = 用已部署 r2 的四个 IP（只为看时序）；--ips p6757 = 用 ai/build/f_*_p6757 里按新周期重综合的 IP
#   产物：vivado/ai_sys_<tag>/，bitstream/ai_sys_<tag>.bit/.bin，vivado/ai_sys_<tag>_{util,timing,util_hier}.rpt
#   以下为 r2 脚本原说明。
#
set here    [file dirname [file normalize [info script]]]
set R       [file normalize [file join [file dirname [info script]] ../..]]
set share   [file normalize [file join [file dirname [info script]] ../../../..]]
set preset_file [file join $share edgeboard board_files fz3b a.0 preset.xml]
proc argval {name def} { set i [lsearch -exact $::argv $name]; if {$i >= 0} { return [lindex $::argv [expr {$i+1}]] }; return $def }
set fmhz [argval --mhz 150]
set ftag [argval --tag f150nv]
set fips [argval --ips orig]
if {$fips eq "orig"} {
    set ip_repos [list $R/ai/build/conv_ip_xu2/solution1/impl/ip $R/ai/build/elt_ip_e2b/solution1/impl/ip $R/ai/build/pp_ip_v3]
} else {
    set ip_repos {}
    foreach c {conv elt pp} {
        set d $R/ai/build/f_${c}_${fips}/solution1/impl/ip
        if {![file exists $d/component.xml]} { error "IP repo missing: $d" }
        lappend ip_repos $d
    }
}
puts "### F clock: $fmhz MHz, tag $ftag, ips $fips"
file mkdir $R/bitstream
set prjdir  $R/vivado
set prjname ai_sys_$ftag
set part    xazu3eg-sfvc784-1-i
set bdname  system
set preflight [expr {[lsearch -exact $argv --preflight] >= 0}]
if {$preflight} {
    set prjdir $R/vivado/preflight
    append prjname _preflight
}

proc logmsg {m} { puts "### $m" }

create_project $prjname "$prjdir/$prjname" -part $part -force
add_files -norecurse [glob $R/rtl_lb/*.v]
set_property ip_repo_paths $ip_repos [current_project]
update_ip_catalog -rebuild
logmsg "project + rtl + ip repos ready"

create_bd_design $bdname

# ---------------- PS: 套板级预设 + 两个 FPD 主口 ----------------
proc read_ps_preset {path} {
    set f [open $path r]; set xml [read $f]; close $f
    if {![regexp {<ip_preset preset_proc_name="zynq_ultra_ps_e_preset">(.*?)</ip_preset>} $xml -> block]} {
        error "zynq_ultra_ps_e_preset not found in $path"
    }
    set cfg {}
    foreach {- name value} [regexp -all -inline {<user_parameter name="([^"]+)" value="([^"]*)"} $block] {
        dict set cfg $name $value
    }
    return $cfg
}
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 zynq_ultra_ps_e_0]
if {[file exists $preset_file]} {
    set preset [read_ps_preset $preset_file]
    puts "### applying [dict size $preset] PS preset params"
    set_property -dict $preset $ps
} else {
    error "FZ3B preset not found: $preset_file"
}
set_property -dict [list \
    CONFIG.PSU__USE__M_AXI_GP0 {1} CONFIG.PSU__MAXIGP0__DATA_WIDTH {32} \
    CONFIG.PSU__USE__M_AXI_GP1 {1} CONFIG.PSU__MAXIGP1__DATA_WIDTH {128} \
    CONFIG.PSU__USE__M_AXI_GP2 {0} \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
] $ps
for {set i 0} {$i <= 6} {incr i} { catch {set_property CONFIG.PSU__USE__S_AXI_GP$i {0} $ps} }
catch {set_property CONFIG.PSU__USE__S_AXI_ACP {0} $ps}
catch {set_property CONFIG.PSU__USE__S_AXI_ACE {0} $ps}

# ---------------- 复位 ----------------
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pl]

# ---------------- F: PL 内 MMCM ----------------
set cw [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz clk_wiz_0]
set_property -dict [list CONFIG.PRIMITIVE {MMCM} CONFIG.PRIM_SOURCE {Global_buffer} CONFIG.PRIM_IN_FREQ {100.000}     CONFIG.CLKOUT1_REQUESTED_OUT_FREQ $fmhz CONFIG.USE_LOCKED {true} CONFIG.USE_RESET {true} CONFIG.RESET_TYPE {ACTIVE_LOW}     CONFIG.CLKOUT1_DRIVES {Buffer} CONFIG.JITTER_SEL {No_Jitter} CONFIG.MMCM_BANDWIDTH {OPTIMIZED}] $cw

# ---------------- 控制通路(32 位) ----------------
# r2: 一次协议转换（AXI4 → AXI4-Lite，32 位）+ AXI-Lite 交叉开关（共享地址 / 共享数据，最省面积）
set pc [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_protocol_converter:2.1 pc_axil]
set_property -dict [list CONFIG.SI_PROTOCOL {AXI4} CONFIG.MI_PROTOCOL {AXI4LITE} \
    CONFIG.DATA_WIDTH {32} CONFIG.ADDR_WIDTH {32} CONFIG.TRANSLATION_MODE {2}] $pc
set xb [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_crossbar:2.1 ctl_xbar]
set_property -dict [list CONFIG.PROTOCOL {AXI4LITE} CONFIG.NUM_SI {1} CONFIG.NUM_MI {4} \
    CONFIG.DATA_WIDTH {32} CONFIG.ADDR_WIDTH {32} CONFIG.CONNECTIVITY_MODE {SASD}] $xb
set iproc [create_bd_cell -type module -reference img_proc_axil img_proc_axil_0]
set conv  [create_bd_cell -type ip -vlnv xilinx.com:hls:conv_layer:1.0 conv_layer_0]
set elt   [create_bd_cell -type ip -vlnv xilinx.com:hls:eltwise_layer:1.0 eltwise_layer_0]
set ppl   [create_bd_cell -type ip -vlnv xilinx.com:hls:pp_layer:1.0 pp_layer_0]

# ---------------- 数据通路(128 位) ----------------
set dai [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ai_data_ic]
set_property -dict [list CONFIG.NUM_SI {9} CONFIG.NUM_MI {1}] $dai
set bctl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl bram_ctrl]
set_property -dict [list CONFIG.DATA_WIDTH {128} CONFIG.SINGLE_PORT_BRAM {0} \
    CONFIG.SUPPORTS_NARROW_BURST {1} CONFIG.PROTOCOL {AXI4}] $bctl
set bgen [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen bram_gen]
set_property -dict [list \
  CONFIG.use_bram_block {BRAM_Controller} CONFIG.Memory_Type {True_Dual_Port_RAM} \
  CONFIG.Byte_Size {8} CONFIG.Use_Byte_Write_Enable {true} \
  CONFIG.Write_Width_A {128} CONFIG.Read_Width_A {128} CONFIG.Write_Depth_A {16384} \
  CONFIG.Write_Width_B {128} CONFIG.Read_Width_B {128} \
  CONFIG.Register_PortA_Output_of_Memory_Primitives {false} \
  CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
  CONFIG.Register_PortA_Output_of_Memory_Core {false} \
  CONFIG.Register_PortB_Output_of_Memory_Core {false} \
  CONFIG.EN_SAFETY_CKT {true} CONFIG.Load_Init_File {false} CONFIG.Interface_Type {Native} \
] $bgen

# ---------------- 连线 ----------------
connect_bd_intf_net [get_bd_intf_pins ${ps}/M_AXI_HPM0_FPD] [get_bd_intf_pins pc_axil/S_AXI]
connect_bd_intf_net [get_bd_intf_pins pc_axil/M_AXI]    [get_bd_intf_pins ctl_xbar/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ctl_xbar/M00_AXI] [get_bd_intf_pins img_proc_axil_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ctl_xbar/M01_AXI] [get_bd_intf_pins conv_layer_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins ctl_xbar/M02_AXI] [get_bd_intf_pins eltwise_layer_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins ctl_xbar/M03_AXI] [get_bd_intf_pins pp_layer_0/s_axi_control]

connect_bd_intf_net [get_bd_intf_pins ${ps}/M_AXI_HPM1_FPD]    [get_bd_intf_pins ai_data_ic/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins conv_layer_0/m_axi_gmem_in]   [get_bd_intf_pins ai_data_ic/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins conv_layer_0/m_axi_gmem_out]  [get_bd_intf_pins ai_data_ic/S02_AXI]
connect_bd_intf_net [get_bd_intf_pins conv_layer_0/m_axi_gmem_par]  [get_bd_intf_pins ai_data_ic/S03_AXI]
connect_bd_intf_net [get_bd_intf_pins eltwise_layer_0/m_axi_gmem_a]   [get_bd_intf_pins ai_data_ic/S04_AXI]
connect_bd_intf_net [get_bd_intf_pins eltwise_layer_0/m_axi_gmem_b]   [get_bd_intf_pins ai_data_ic/S05_AXI]
connect_bd_intf_net [get_bd_intf_pins eltwise_layer_0/m_axi_gmem_out] [get_bd_intf_pins ai_data_ic/S06_AXI]
connect_bd_intf_net [get_bd_intf_pins pp_layer_0/m_axi_gmem_in]  [get_bd_intf_pins ai_data_ic/S07_AXI]
connect_bd_intf_net [get_bd_intf_pins pp_layer_0/m_axi_gmem_out] [get_bd_intf_pins ai_data_ic/S08_AXI]
connect_bd_intf_net [get_bd_intf_pins ai_data_ic/M00_AXI] [get_bd_intf_pins bram_ctrl/S_AXI]
connect_bd_intf_net [get_bd_intf_pins bram_ctrl/BRAM_PORTA] [get_bd_intf_pins bram_gen/BRAM_PORTA]
connect_bd_intf_net [get_bd_intf_pins bram_ctrl/BRAM_PORTB] [get_bd_intf_pins bram_gen/BRAM_PORTB]

# pl_clk0 实际是 IOPLL 分频出来的 99.999001 MHz，clk_wiz 的输入频率必须与之相同（BD 校验逐 Hz 比较）
set plhz [get_property CONFIG.FREQ_HZ [get_bd_pins ${ps}/pl_clk0]]
set_property CONFIG.PRIM_IN_FREQ [format %.6f [expr {$plhz / 1e6}]] $cw
logmsg "pl_clk0 $plhz Hz -> clk_wiz in [get_property CONFIG.PRIM_IN_FREQ $cw] MHz, clk_in1 [get_property CONFIG.FREQ_HZ [get_bd_pins clk_wiz_0/clk_in1]] Hz"
connect_bd_net [get_bd_pins ${ps}/pl_clk0] [get_bd_pins clk_wiz_0/clk_in1]
connect_bd_net [get_bd_pins ${ps}/pl_resetn0] [get_bd_pins clk_wiz_0/resetn]
connect_bd_net [get_bd_pins clk_wiz_0/locked] [get_bd_pins rst_pl/dcm_locked]
set plclk [get_bd_pins clk_wiz_0/clk_out1]
foreach p [list ctl_xbar/aclk \
                pc_axil/aclk pc_axil/s_axi_aclk pc_axil/m_axi_aclk \
                img_proc_axil_0/s_axi_aclk conv_layer_0/ap_clk eltwise_layer_0/ap_clk pp_layer_0/ap_clk \
                ai_data_ic/ACLK ai_data_ic/S00_ACLK ai_data_ic/S01_ACLK ai_data_ic/S02_ACLK \
                ai_data_ic/S03_ACLK ai_data_ic/S04_ACLK ai_data_ic/S05_ACLK ai_data_ic/S06_ACLK \
                ai_data_ic/S07_ACLK ai_data_ic/S08_ACLK \
                ai_data_ic/M00_ACLK bram_ctrl/s_axi_aclk bram_gen/clka bram_gen/clkb \
                rst_pl/slowest_sync_clk \
                "${ps}/maxihpm0_fpd_aclk" "${ps}/maxihpm1_fpd_aclk" "${ps}/maxihpm0_lpd_aclk"] {
    if {[llength [get_bd_pins -quiet $p]] > 0} { catch { connect_bd_net $plclk [get_bd_pins $p] } } else { puts "WARN clk $p" }
}
connect_bd_net [get_bd_pins ${ps}/pl_resetn0] [get_bd_pins rst_pl/ext_reset_in]
set prstn [get_bd_pins rst_pl/peripheral_aresetn]
set irstn [get_bd_pins rst_pl/interconnect_aresetn]
foreach p [list pp_layer_0/ap_rst_n \
                pc_axil/aresetn pc_axil/s_axi_aresetn pc_axil/m_axi_aresetn img_proc_axil_0/s_axi_aresetn \
                conv_layer_0/ap_rst_n eltwise_layer_0/ap_rst_n bram_ctrl/s_axi_aresetn] {
    if {[llength [get_bd_pins -quiet $p]] > 0} { catch { connect_bd_net $prstn [get_bd_pins $p] } }
}
foreach p [list ai_data_ic/ARESETN ai_data_ic/S00_ARESETN ai_data_ic/S01_ARESETN ai_data_ic/S02_ARESETN \
                ai_data_ic/S03_ARESETN ai_data_ic/S04_ARESETN ai_data_ic/S05_ARESETN ai_data_ic/S06_ARESETN \
                ai_data_ic/S07_ARESETN ai_data_ic/S08_ARESETN \
                ai_data_ic/M00_ARESETN ctl_xbar/aresetn] {
    if {[llength [get_bd_pins -quiet $p]] > 0} { catch { connect_bd_net $irstn [get_bd_pins $p] } }
}
logmsg "wired"

# ---------------- 地址 ----------------
assign_bd_address -offset 0xA0000000 -range 4K   [get_bd_addr_segs img_proc_axil_0/S_AXI/reg0]
assign_bd_address -offset 0xA0010000 -range 64K  [get_bd_addr_segs conv_layer_0/s_axi_control/Reg]
assign_bd_address -offset 0xA0020000 -range 64K  [get_bd_addr_segs eltwise_layer_0/s_axi_control/Reg]
assign_bd_address -offset 0xA0030000 -range 64K  [get_bd_addr_segs pp_layer_0/s_axi_control/Reg]
assign_bd_address -offset 0xB0000000 -range 256K [get_bd_addr_segs bram_ctrl/S_AXI/Mem0]
catch {assign_bd_address}
foreach seg [get_bd_addr_segs -quiet {/conv_layer_0/Data_m_axi_*/SEG_* /eltwise_layer_0/Data_m_axi_*/SEG_* /pp_layer_0/Data_m_axi_*/SEG_*}] {
  if {![string match *bram* $seg]} { catch { exclude_bd_addr_seg $seg } }
}
logmsg "=== PS Data segments ==="
foreach seg [get_bd_addr_segs -quiet {/zynq_ultra_ps_e_0/Data/*}] {
  catch { puts "   [get_property NAME $seg] off=[get_property OFFSET $seg] rng=[get_property RANGE $seg]" }
}

validate_bd_design -force
logmsg "VALIDATE_OK"
logmsg "clk_wiz actual: [get_property CONFIG.CLKOUT1_REQUESTED_OUT_FREQ $cw] MHz req, M=[get_property CONFIG.MMCM_CLKFBOUT_MULT_F $cw] D=[get_property CONFIG.MMCM_DIVCLK_DIVIDE $cw] O=[get_property CONFIG.MMCM_CLKOUT0_DIVIDE_F $cw] jitter=[get_property CONFIG.CLKOUT1_JITTER $cw]"
foreach p {maxihpm0_fpd_aclk maxihpm1_fpd_aclk} { logmsg "$p freq = [get_property CONFIG.FREQ_HZ [get_bd_pins ${ps}/$p]]" }
save_bd_design
if {$preflight} {
    logmsg "PREFLIGHT_OK: IP catalog, FZ3B preset and dual-port BD validated; no synthesis run"
    close_project
    exit 0
}

set bdfile [get_files -quiet "${bdname}.bd"]
make_wrapper -files $bdfile -top
set wrap [glob -nocomplain "$prjdir/$prjname/$prjname.gen/sources_1/bd/${bdname}/hdl/${bdname}_wrapper.v"]
if {[llength $wrap] > 0} {
    add_files -norecurse $wrap
    set_property top ${bdname}_wrapper [get_filesets sources_1]
}
update_compile_order -fileset sources_1
logmsg "wrapper ready -> launching build"

# write_bitstream 默认只出 .bit; fpga_manager 要的是 .bin —— 必须显式开 BIN_FILE
set_property STEPS.WRITE_BITSTREAM.ARGS.BIN_FILE true [get_runs impl_1]
set build_jobs 2
if {[info exists ::env(PLD_BUILD_JOBS)]} {
    set build_jobs $::env(PLD_BUILD_JOBS)
}
if {![string is integer -strict $build_jobs] || $build_jobs < 1} {
    error "PLD_BUILD_JOBS must be a positive integer"
}
logmsg "build jobs: $build_jobs"
launch_runs impl_1 -to_step write_bitstream -jobs $build_jobs
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    logmsg "IMPL_FAILED: [get_property STATUS [get_runs impl_1]]"
    exit 1
}
open_run impl_1
set wns [get_property STATS.WNS [get_runs impl_1]]
set whs [get_property STATS.WHS [get_runs impl_1]]
logmsg "WNS $wns WHS $whs"
set impl_dir [get_property DIRECTORY [get_runs impl_1]]
file copy -force [file join $impl_dir ${bdname}_wrapper.bit] $R/bitstream/ai_sys_$ftag.bit
file copy -force [file join $impl_dir ${bdname}_wrapper.bin] $R/bitstream/ai_sys_$ftag.bin
report_utilization -file $R/vivado/ai_sys_${ftag}_util.rpt
report_timing_summary -file $R/vivado/ai_sys_${ftag}_timing.rpt
report_utilization -hierarchical -hierarchical_depth 2 -file $R/vivado/ai_sys_${ftag}_util_hier.rpt
logmsg "AI_SYS_F_NOVIS_BUILD_DONE $ftag"
exit
