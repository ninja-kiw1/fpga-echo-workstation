# =============================================================================
# build_boot_design.tcl -- ZynqMP image-processor design with the FULL FZ3B PS
# configuration (so an XSA/FSBL/PMUFW built from it can go into a boot.bin).
#
# Same PL as build_full_design.tcl:
#   PS(M_AXI_HPM0_FPD) -> sc_ctrl -> {AXI DMA S_AXI_LITE, AXI GPIO}
#   AXI DMA M_AXI_MM2S/S2MM -> sc_data -> PS S_AXI_HP0_FPD
#   AXI DMA M_AXIS_MM2S -> img_proc_axis -> AXI DMA S_AXIS_S2MM
#   AXI GPIO ch1(16b out)->img_proc_axis.ctrl ; stats -> GPIO ch2(18b in)
#
# The ONLY difference from build_full_design.tcl is that the PS is configured
# from bd/ps_config.tcl (DDR4-2400 64bit, GEM3 MIO64..77, SD0 eMMC, SD1 SD card,
# QSPI, UART1) instead of Vivado's power-on defaults.
#
# ASCII-only on purpose (cp936 Vivado may misread non-ASCII inside Tcl).
# =============================================================================

set root    [file normalize [file join [file dirname [info script]] ..]]
set prjdir  "$root/vivado"
set prjname "boot_prj"
set part    "xazu3eg-sfvc784-1-i"
set bdname  "system"

proc die {msg} { puts "FATAL: $msg" ; error $msg }

catch {close_project -quiet}
create_project $prjname "$prjdir/$prjname" -part $part -force
add_files -norecurse [glob $root/rtl/*.v]
puts "INFO: RTL: [get_files -quiet -of_objects [get_filesets sources_1] *.v]"

create_bd_design $bdname

# ---------------------------------------------------------------------------
# PS with the full board configuration
# ---------------------------------------------------------------------------
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 zynq_ultra_ps_e_0]
source "$root/bd/ps_config.tcl"
catch {set_property CONFIG.PSU__FPGA_PL0_ENABLE {1} $ps}
puts "INFO: PS configured. M_AXI_GP0=[get_property CONFIG.PSU__USE__M_AXI_GP0 $ps] S_AXI_GP2=[get_property CONFIG.PSU__USE__S_AXI_GP2 $ps]"

# find the PS->PL master pin
set ps_master ""
foreach p [get_bd_intf_pins -of_objects $ps] {
    if {[string match "M_AXI_HPM0_FPD" [get_property NAME $p]]} { set ps_master $p ; break }
}
if {$ps_master eq ""} {
    foreach p [get_bd_intf_pins -of_objects $ps] {
        if {[string match "M_AXI_HPM*" [get_property NAME $p]]} { set ps_master $p ; break }
    }
}
if {$ps_master eq ""} { die "PS has no M_AXI_HPM* master pin." }
puts "INFO: PS master = $ps_master"

set ps_slave [get_bd_intf_pins "${ps}/S_AXI_HP0_FPD"]
if {$ps_slave eq ""} { die "S_AXI_HP0_FPD not present on PS." }
puts "INFO: PS slave  = $ps_slave"

# ---------------------------------------------------------------------------
# PL cells
# ---------------------------------------------------------------------------
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 sc_ctrl
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] [get_bd_cells sc_ctrl]

create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 sc_data
set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] [get_bd_cells sc_data]

create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pl

create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_img
set_property -dict [list \
    CONFIG.c_include_sg               {0} \
    CONFIG.c_sg_include_stscntrl_strm {0} \
    CONFIG.c_include_mm2s             {1} \
    CONFIG.c_include_s2mm             {1} \
    CONFIG.c_micro_dma                {0} \
    CONFIG.c_addr_width               {32} \
    CONFIG.c_m_axi_mm2s_data_width    {128} \
    CONFIG.c_m_axi_s2mm_data_width    {128} \
    CONFIG.c_m_axis_mm2s_tdata_width  {32} \
    CONFIG.c_s_axis_s2mm_tdata_width  {32} \
] [get_bd_cells axi_dma_img]

create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_ctrl
set_property -dict [list \
    CONFIG.C_IS_DUAL       {1} \
    CONFIG.C_GPIO_WIDTH    {16} \
    CONFIG.C_GPIO2_WIDTH   {18} \
    CONFIG.C_ALL_OUTPUTS   {1} \
    CONFIG.C_ALL_INPUTS    {0} \
    CONFIG.C_ALL_OUTPUTS_2 {0} \
    CONFIG.C_ALL_INPUTS_2  {1} \
] [get_bd_cells gpio_ctrl]

create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 concat_stat
set_property -dict [list CONFIG.NUM_PORTS {4}] [get_bd_cells concat_stat]

create_bd_cell -type module -reference img_proc_axis img_proc_axis_0

# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------
connect_bd_intf_net $ps_master [get_bd_intf_pins sc_ctrl/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M00_AXI] [get_bd_intf_pins axi_dma_img/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M01_AXI] [get_bd_intf_pins gpio_ctrl/S_AXI]

connect_bd_intf_net [get_bd_intf_pins axi_dma_img/M_AXI_MM2S] [get_bd_intf_pins sc_data/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_img/M_AXI_S2MM] [get_bd_intf_pins sc_data/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins sc_data/M00_AXI] $ps_slave

connect_bd_intf_net [get_bd_intf_pins axi_dma_img/M_AXIS_MM2S] [get_bd_intf_pins img_proc_axis_0/s_axis]
connect_bd_intf_net [get_bd_intf_pins img_proc_axis_0/m_axis] [get_bd_intf_pins axi_dma_img/S_AXIS_S2MM]

connect_bd_net [get_bd_pins gpio_ctrl/gpio_io_o] [get_bd_pins img_proc_axis_0/ctrl]
connect_bd_net [get_bd_pins img_proc_axis_0/stat_max]   [get_bd_pins concat_stat/In0]
connect_bd_net [get_bd_pins img_proc_axis_0/stat_min]   [get_bd_pins concat_stat/In1]
connect_bd_net [get_bd_pins img_proc_axis_0/stat_valid] [get_bd_pins concat_stat/In2]
connect_bd_net [get_bd_pins img_proc_axis_0/proc_busy]  [get_bd_pins concat_stat/In3]
connect_bd_net [get_bd_pins concat_stat/dout] [get_bd_pins gpio_ctrl/gpio2_io_i]

# clock
set plclk [get_bd_pins ${ps}/pl_clk0]
set clk_sinks [list sc_ctrl/ACLK sc_ctrl/S00_ACLK sc_ctrl/M00_ACLK sc_ctrl/M01_ACLK \
    sc_data/aclk \
    axi_dma_img/s_axi_lite_aclk axi_dma_img/m_axi_mm2s_aclk axi_dma_img/m_axi_s2mm_aclk \
    gpio_ctrl/s_axi_aclk img_proc_axis_0/aclk rst_pl/slowest_sync_clk \
    "${ps}/maxihpm0_fpd_aclk" "${ps}/saxihp0_fpd_aclk"]
foreach p $clk_sinks {
    if {[llength [get_bd_pins -quiet $p]] > 0} {
        connect_bd_net $plclk [get_bd_pins $p]
    } else { puts "WARN: clock pin $p not present, skipped" }
}

# reset
connect_bd_net [get_bd_pins ${ps}/pl_resetn0] [get_bd_pins rst_pl/ext_reset_in]
set plrstn [get_bd_pins rst_pl/peripheral_aresetn]
set rst_sinks [list sc_ctrl/ARESETN sc_ctrl/S00_ARESETN sc_ctrl/M00_ARESETN sc_ctrl/M01_ARESETN \
    sc_data/aresetn \
    axi_dma_img/axi_resetn gpio_ctrl/s_axi_aresetn img_proc_axis_0/aresetn]
foreach p $rst_sinks {
    if {[llength [get_bd_pins -quiet $p]] > 0} {
        connect_bd_net $plrstn [get_bd_pins $p]
    } else { puts "WARN: reset pin $p not present, skipped" }
}

# ---------------------------------------------------------------------------
# addresses + validate + save + wrapper
# ---------------------------------------------------------------------------
assign_bd_address

puts "INFO: ---- register bases ----"
foreach c {axi_dma_img gpio_ctrl} {
    foreach seg [get_bd_addr_segs -quiet -of_objects [get_bd_cells $c]] {
        set off [get_property -quiet OFFSET $seg]
        if {$off ne ""} { puts "   [get_property NAME $seg]  offset = $off" }
    }
}
puts "INFO: ---- DMA master DDR windows ----"
foreach sp {Data_MM2S Data_S2MM} {
    set asp [get_bd_addr_spaces -quiet "axi_dma_img/$sp"]
    if {[llength $asp] == 0} { continue }
    foreach seg [get_bd_addr_segs -quiet -of_objects $asp] {
        puts "   $sp : [get_property NAME $seg] off=[get_property -quiet OFFSET $seg] range=[get_property -quiet RANGE $seg]"
    }
}

if {[catch {validate_bd_design} vm]} {
    puts "VALIDATE_FAILED: $vm"
    puts "VALIDATION_ERRORINFO: $::errorInfo"
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
    puts "INFO: top = ${bdname}_wrapper"
} else {
    die "wrapper not found"
}
update_compile_order -fileset sources_1
puts "DONE build_boot_design.tcl"
