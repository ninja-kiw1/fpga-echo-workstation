# =============================================================================
# build_axil_design.tcl  --  PS-driven variant (no AXI DMA)
#
#   PS (M_AXI_HPM0_FPD, 32-bit) --> axi_interconnect --> axi_protocol_converter
#                               --> img_proc_axil (AXI4-Lite slave)
#
# Why: this board's factory PS configuration enables NO PL->PS slave port, so a
# PL master (AXI DMA) can never reach DDR (measured: S_AXI_HPC0/HP0/HP1/ACP all
# just hang, not even a SLVERR).  The PS->PL master does work, so the data path
# is turned around: the PS pushes pixels in and pulls results out itself.
#
# All image processing stays inside the PL (img_proc_axil wraps img_proc_axis).
#
# 2026-10-01 FIX (see test_report_2026-10-01.md §9):
#   原来 PS 主口是 128-bit, axi_interconnect 自动插了一个 axi_dwidth_converter
#   (128->32) + axi_protocol_converter. 上板实测: 只有 16 字节对齐地址的 32bit
#   写能把数据送到从机, 其它偏移的写数据全变 0 (读通路正常) -> IN_PIX(0x08)
#   永远写 0 -> 图像全黑. 改法:
#     1) 把 PS 主口设成 **32-bit** (CONFIG.PSU__MAXIGP0__DATA_WIDTH=32),
#        彻底去掉 128->32 的宽度转换;
#     2) 宽度一致后, 协议转换改成**显式**的一个 axi_protocol_converter(pc_axil),
#        放在互联与从机之间 —— 不再让互联自动插.
#
# Usage:  source <repo>/bd/build_axil_design.tcl
# =============================================================================

set root    [file normalize [file join [file dirname [info script]] ..]]
set prjdir  "$root/vivado"
set prjname "img_proc_axil"
set part    "xazu3eg-sfvc784-1-i"
set bdname  "system"

proc die {msg} { puts "FATAL: $msg" ; error $msg }

create_project $prjname "$prjdir/$prjname" -part $part -force
add_files -norecurse [glob $root/rtl/*.v]
puts "INFO: RTL added: [get_files -quiet -of_objects [get_filesets sources_1] *.v]"

create_bd_design $bdname

# ---------------------------------------------------------------------------
# PS : only the PS->PL master is needed
# ---------------------------------------------------------------------------
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 zynq_ultra_ps_e_0]
set_property CONFIG.PSU__USE__M_AXI_GP0 {1} $ps
# 关键: 32-bit 主口, 不触发 128->32 宽度转换 (那个转换器会丢写数据)
set_property CONFIG.PSU__MAXIGP0__DATA_WIDTH {32} $ps
for {set i 0} {$i <= 6} {incr i} { catch {set_property CONFIG.PSU__USE__S_AXI_GP$i {0} $ps} }
catch {set_property CONFIG.PSU__USE__S_AXI_ACP {0} $ps}
catch {set_property CONFIG.PSU__USE__S_AXI_ACE {0} $ps}

set ps_master ""
foreach p [get_bd_intf_pins -of_objects $ps] {
    if {[string match "M_AXI_HPM0_FPD" [get_property NAME $p]]} { set ps_master $p ; break }
}
if {$ps_master eq ""} { die "PS has no M_AXI_HPM0_FPD pin." }
puts "INFO: PS master pin = $ps_master"

# ---------------------------------------------------------------------------
# cells
# ---------------------------------------------------------------------------
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 sc_axil
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] [get_bd_cells sc_axil]

# 显式协议转换器: AXI4 (32-bit) -> AXI4-Lite (32-bit)
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_protocol_converter:2.1 pc_axil
set_property -dict [list \
    CONFIG.SI_PROTOCOL {AXI4}   CONFIG.MI_PROTOCOL {AXI4LITE} \
    CONFIG.DATA_WIDTH  {32}     CONFIG.ADDR_WIDTH  {32} \
    CONFIG.TRANSLATION_MODE {2} ] [get_bd_cells pc_axil]

create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pl

create_bd_cell -type module -reference img_proc_axil img_proc_axil_0

# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------
connect_bd_intf_net $ps_master                        [get_bd_intf_pins sc_axil/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins sc_axil/M00_AXI] [get_bd_intf_pins pc_axil/S_AXI]
connect_bd_intf_net [get_bd_intf_pins pc_axil/M_AXI]   [get_bd_intf_pins img_proc_axil_0/S_AXI]

set plclk [get_bd_pins ${ps}/pl_clk0]
foreach p [list sc_axil/ACLK sc_axil/S00_ACLK sc_axil/M00_ACLK \
                pc_axil/aclk pc_axil/s_axi_aclk pc_axil/m_axi_aclk \
                img_proc_axil_0/s_axi_aclk rst_pl/slowest_sync_clk \
                "${ps}/maxihpm0_fpd_aclk" "${ps}/maxihpm0_lpd_aclk"] {
    if {[llength [get_bd_pins -quiet $p]] > 0} {
        connect_bd_net $plclk [get_bd_pins $p]
    } else {
        puts "WARN: clock pin $p not present, skipped"
    }
}

connect_bd_net [get_bd_pins ${ps}/pl_resetn0] [get_bd_pins rst_pl/ext_reset_in]
set plrstn [get_bd_pins rst_pl/peripheral_aresetn]
foreach p [list sc_axil/ARESETN sc_axil/S00_ARESETN sc_axil/M00_ARESETN \
                pc_axil/aresetn pc_axil/s_axi_aresetn pc_axil/m_axi_aresetn \
                img_proc_axil_0/s_axi_aresetn] {
    if {[llength [get_bd_pins -quiet $p]] > 0} {
        connect_bd_net $plrstn [get_bd_pins $p]
    } else {
        puts "WARN: reset pin $p not present, skipped"
    }
}

assign_bd_address
puts "INFO: ---- register bases ----"
foreach c {img_proc_axil_0} {
    foreach seg [get_bd_addr_segs -quiet -of_objects [get_bd_cells $c]] {
        set off [get_property -quiet OFFSET $seg]
        if {$off ne ""} { puts "   [get_property NAME $seg]  offset = $off" }
    }
}

if {[catch {validate_bd_design} vm]} {
    puts "VALIDATE_FAILED: $vm"
} else {
    puts "INFO: validate_bd_design OK"
}
save_bd_design

set bdfile [get_files -quiet "${bdname}.bd"]
make_wrapper -files $bdfile -top
set wrap [glob -nocomplain "$prjdir/$prjname/$prjname.gen/sources_1/bd/${bdname}/hdl/${bdname}_wrapper.v"]
if {[llength $wrap] > 0} {
    add_files -norecurse $wrap
    set_property top ${bdname}_wrapper [get_filesets sources_1]
    puts "INFO: top set to ${bdname}_wrapper"
} else {
    puts "WARN: wrapper not found"
}
update_compile_order -fileset sources_1
puts "DONE build_axil_design.tcl"
