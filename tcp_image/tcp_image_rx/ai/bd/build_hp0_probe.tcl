# build_hp0_probe.tcl -- 10-18 HP0 实测用的**测试比特流**（不进 ai_sys.bin、不替换 f150）：
#   PS（FZ3B 预设）+ M_AXI_HPM0_FPD（32 位，控制）+ S_AXI_HP0_FPD（**64 位**，与厂商 FSBL 写的 AFIFM2 FABRIC_WIDTH=1 一致）
#   + hp0_probe（HLS，ai/hp0_probe，64 位 m_axi 直连 HP0）+ proc_sys_reset；全部跑 pl_clk0（板上实际 101.376 MHz）。
#   控制口 0xA000_0000；HP0 只映射 DDR_LOW（0x0000_0000 起 2 GB），实测目标是厂商预留的 no-map 区 0x4000_0000。
#   用法：vivado -mode batch -source build_hp0_probe.tcl ；产物 bitstream/hp0_probe_test.{bit,bin}
set R       [file normalize [file join [file dirname [info script]] ../..]]
set share   [file normalize [file join [file dirname [info script]] ../../../..]]
set preset_file [file join $share edgeboard board_files fz3b a.0 preset.xml]
set prjdir  $R/vivado/hp0_probe
create_project hp0_probe $prjdir -part xazu3eg-sfvc784-1-i -force
set_property ip_repo_paths [list $R/ai/build/hp0_probe/solution1/impl/ip] [current_project]
update_ip_catalog -rebuild
create_bd_design system

proc read_ps_preset {path} {
    set f [open $path r]; set xml [read $f]; close $f
    if {![regexp {<ip_preset preset_proc_name="zynq_ultra_ps_e_preset">(.*?)</ip_preset>} $xml -> block]} { error "preset not found" }
    set cfg {}
    foreach {- name value} [regexp -all -inline {<user_parameter name="([^"]+)" value="([^"]*)"} $block] { dict set cfg $name $value }
    return $cfg
}
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 zynq_ultra_ps_e_0]
set_property -dict [read_ps_preset $preset_file] $ps
set_property -dict [list \
    CONFIG.PSU__USE__M_AXI_GP0 {1} CONFIG.PSU__MAXIGP0__DATA_WIDTH {32} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} CONFIG.PSU__USE__M_AXI_GP2 {0} \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
] $ps
for {set i 0} {$i <= 6} {incr i} { catch {set_property CONFIG.PSU__USE__S_AXI_GP$i {0} $ps} }
catch {set_property CONFIG.PSU__USE__S_AXI_ACP {0} $ps}
catch {set_property CONFIG.PSU__USE__S_AXI_ACE {0} $ps}
# S_AXI_GP2 = S_AXI_HP0_FPD = AFIFM2
set_property -dict [list CONFIG.PSU__USE__S_AXI_GP2 {1} CONFIG.PSU__SAXIGP2__DATA_WIDTH {64}] $ps

set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pl]
set pc  [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_protocol_converter:2.1 pc_axil]
set_property -dict [list CONFIG.SI_PROTOCOL {AXI4} CONFIG.MI_PROTOCOL {AXI4LITE} CONFIG.DATA_WIDTH {32} CONFIG.ADDR_WIDTH {32} CONFIG.TRANSLATION_MODE {2}] $pc
set pr  [create_bd_cell -type ip -vlnv xilinx.com:hls:hp0_probe:1.0 hp0_probe_0]

connect_bd_intf_net [get_bd_intf_pins ${ps}/M_AXI_HPM0_FPD] [get_bd_intf_pins pc_axil/S_AXI]
connect_bd_intf_net [get_bd_intf_pins pc_axil/M_AXI] [get_bd_intf_pins hp0_probe_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins hp0_probe_0/m_axi_gmem] [get_bd_intf_pins ${ps}/S_AXI_HP0_FPD]

set clk [get_bd_pins ${ps}/pl_clk0]
foreach p [list pc_axil/aclk hp0_probe_0/ap_clk rst_pl/slowest_sync_clk ${ps}/maxihpm0_fpd_aclk ${ps}/saxihp0_fpd_aclk] {
    connect_bd_net $clk [get_bd_pins $p]
}
connect_bd_net [get_bd_pins ${ps}/pl_resetn0] [get_bd_pins rst_pl/ext_reset_in]
connect_bd_net [get_bd_pins rst_pl/peripheral_aresetn] [get_bd_pins hp0_probe_0/ap_rst_n]
connect_bd_net [get_bd_pins rst_pl/interconnect_aresetn] [get_bd_pins pc_axil/aresetn]

assign_bd_address -offset 0xA0000000 -range 64K [get_bd_addr_segs hp0_probe_0/s_axi_control/Reg]
catch {assign_bd_address}
foreach seg [get_bd_addr_segs -quiet /hp0_probe_0/Data_m_axi_gmem/*] {
    puts "### probe seg [get_property NAME $seg] off=[get_property OFFSET $seg] rng=[get_property RANGE $seg]"
    if {![string match *DDR_LOW* [get_property NAME $seg]]} { catch { exclude_bd_addr_seg $seg } }
}
foreach seg [get_bd_addr_segs -quiet /hp0_probe_0/Data_m_axi_gmem/*] {
    puts "### kept/excluded [get_property NAME $seg] excluded=[get_property EXCLUDED $seg]"
}
puts "### HP0 width = [get_property CONFIG.PSU__SAXIGP2__DATA_WIDTH $ps]"
validate_bd_design -force
save_bd_design

make_wrapper -files [get_files system.bd] -top
add_files -norecurse [glob $prjdir/hp0_probe.gen/sources_1/bd/system/hdl/system_wrapper.v]
set_property top system_wrapper [get_filesets sources_1]
set_property STEPS.WRITE_BITSTREAM.ARGS.BIN_FILE true [get_runs impl_1]
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { puts "### IMPL_FAILED"; exit 1 }
open_run impl_1
puts "### WNS [get_property STATS.WNS [get_runs impl_1]] WHS [get_property STATS.WHS [get_runs impl_1]]"
set d [get_property DIRECTORY [get_runs impl_1]]
file copy -force $d/system_wrapper.bit $R/bitstream/hp0_probe_test.bit
file copy -force $d/system_wrapper.bin $R/bitstream/hp0_probe_test.bin
report_utilization -file $R/vivado/hp0_probe_util.rpt
report_timing_summary -file $R/vivado/hp0_probe_timing.rpt
puts "### HP0_PROBE_BUILD_DONE"
exit
