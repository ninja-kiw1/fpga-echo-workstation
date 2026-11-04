# FZ3B system for the mixed-precision U-Net: Zynq UltraScale+ PS + conv_layer + eltwise_layer.
#
#   vivado -mode batch -source create_system.tcl
#
# SYSTEM_STAGE (environment): create  -> project + block design + wrapper only
#                             all     -> also synthesis, implementation, bitstream, XSA (default)
# Outputs of "all" go to output/: unet_system.bit, unet_system.hwh (PYNQ needs the same
# basename), unet_system.xsa (Vitis / PetaLinux), and utilization / timing reports.
#
# PS settings: DDR4, MIO and peripherals come from the community FZ3B board preset
# (edgeboard/board_files/fz3b/a.0/preset.xml). They match the PS of the PYNQ
# edgeboard-fz3b base.hwh except the DDR high-address window, which follows the
# PYNQ design (4 GB mapped). The board.xml in that folder is a copy of the FZ3A
# file (xczu3eg), so it is not used as a board part.
# AXI ports and the PL clock follow the PYNQ base design: control on
# M_AXI_HPM0_LPD (32-bit), data on S_AXI_HP0/HP1_FPD (128-bit), pl_clk0 = 100 MHz.

set here [file dirname [file normalize [info script]]]
set pld [file normalize [file join $here .. ..]]
set deploy [file join $pld medical_fpga deployment]
set preset_file [file join $pld edgeboard board_files fz3b a.0 preset.xml]
set project_dir [file join $here unet_system]
set output_dir [file join $here output]
set part xazu3eg-sfvc784-1-i
set design unet_system

set stage all
if {[info exists ::env(SYSTEM_STAGE)]} { set stage $::env(SYSTEM_STAGE) }
if {$stage ni {create all}} { error "SYSTEM_STAGE must be create or all" }
if {[file exists $project_dir]} {
    error "Project directory already exists: $project_dir (delete it to rebuild, or open unet_system.xpr)"
}
set ip_repos [list [file join $deploy unet_conv build conv_layer solution1 impl ip] \
                   [file join $deploy unet_eltwise build eltwise_layer solution1 impl ip]]
foreach repo $ip_repos {
    if {![file exists [file join $repo component.xml]]} { error "Missing exported HLS IP: $repo" }
}

# PS preset: every user_parameter of the zynq_ultra_ps_e_preset block.
proc read_ps_preset {path} {
    set f [open $path r]
    set xml [read $f]
    close $f
    if {![regexp {<ip_preset preset_proc_name="zynq_ultra_ps_e_preset">(.*?)</ip_preset>} $xml -> block]} {
        error "zynq_ultra_ps_e_preset not found in $path"
    }
    set config {}
    foreach {- name value} [regexp -all -inline {<user_parameter name="([^"]+)" value="([^"]*)"} $block] {
        dict set config $name $value
    }
    return $config
}

create_project $design $project_dir -part $part
set_property target_language Verilog [current_project]
set_property ip_repo_paths $ip_repos [current_project]
update_ip_catalog

create_bd_design $design

# ---- Processing system ------------------------------------------------------
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e ps]
set preset [read_ps_preset $preset_file]
puts "Applying [dict size $preset] PS preset parameters from $preset_file"
set_property -dict $preset $ps
set_property -dict [list \
    CONFIG.PSU__DDR_HIGH_ADDRESS_GUI_ENABLE {1} \
    CONFIG.PSU__HIGH_ADDRESS__ENABLE {1} \
    CONFIG.PSU__USE__M_AXI_GP0 {0} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {1} \
    CONFIG.PSU__MAXIGP2__DATA_WIDTH {32} \
    CONFIG.PSU__USE__S_AXI_GP2 {1} \
    CONFIG.PSU__SAXIGP2__DATA_WIDTH {128} \
    CONFIG.PSU__USE__S_AXI_GP3 {1} \
    CONFIG.PSU__SAXIGP3__DATA_WIDTH {128} \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
    CONFIG.PSU__NUM_FABRIC_RESETS {1} \
    CONFIG.PSU__USE__IRQ0 {1} \
] $ps

# ---- Accelerators, interconnect, clock and reset -----------------------------
set conv [create_bd_cell -type ip -vlnv xilinx.com:hls:conv_layer:1.0 conv_layer_0]
set elt [create_bd_cell -type ip -vlnv xilinx.com:hls:eltwise_layer:1.0 eltwise_layer_0]
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset rst_pl]
# Control: PS -> both s_axi_control. Data: each IP's three masters -> its own HP port.
set smc_ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect smc_ctrl]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $smc_ctrl
set smc_conv [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect smc_conv]
set_property -dict [list CONFIG.NUM_SI {3} CONFIG.NUM_MI {1}] $smc_conv
set smc_elt [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect smc_elt]
set_property -dict [list CONFIG.NUM_SI {3} CONFIG.NUM_MI {1}] $smc_elt
set irq [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat irq_concat]
set_property CONFIG.NUM_PORTS {2} $irq

set clk [get_bd_pins ps/pl_clk0]
connect_bd_net $clk \
    [get_bd_pins ps/maxihpm0_lpd_aclk] [get_bd_pins ps/saxihp0_fpd_aclk] [get_bd_pins ps/saxihp1_fpd_aclk] \
    [get_bd_pins rst_pl/slowest_sync_clk] [get_bd_pins conv_layer_0/ap_clk] [get_bd_pins eltwise_layer_0/ap_clk] \
    [get_bd_pins smc_ctrl/aclk] [get_bd_pins smc_conv/aclk] [get_bd_pins smc_elt/aclk]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins rst_pl/ext_reset_in]
connect_bd_net [get_bd_pins rst_pl/peripheral_aresetn] \
    [get_bd_pins conv_layer_0/ap_rst_n] [get_bd_pins eltwise_layer_0/ap_rst_n]
connect_bd_net [get_bd_pins rst_pl/interconnect_aresetn] \
    [get_bd_pins smc_ctrl/aresetn] [get_bd_pins smc_conv/aresetn] [get_bd_pins smc_elt/aresetn]

connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_LPD] [get_bd_intf_pins smc_ctrl/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins smc_ctrl/M00_AXI] [get_bd_intf_pins conv_layer_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins smc_ctrl/M01_AXI] [get_bd_intf_pins eltwise_layer_0/s_axi_control]

connect_bd_intf_net [get_bd_intf_pins conv_layer_0/m_axi_gmem_in] [get_bd_intf_pins smc_conv/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins conv_layer_0/m_axi_gmem_out] [get_bd_intf_pins smc_conv/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins conv_layer_0/m_axi_gmem_par] [get_bd_intf_pins smc_conv/S02_AXI]
connect_bd_intf_net [get_bd_intf_pins smc_conv/M00_AXI] [get_bd_intf_pins ps/S_AXI_HP0_FPD]

connect_bd_intf_net [get_bd_intf_pins eltwise_layer_0/m_axi_gmem_a] [get_bd_intf_pins smc_elt/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins eltwise_layer_0/m_axi_gmem_b] [get_bd_intf_pins smc_elt/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins eltwise_layer_0/m_axi_gmem_out] [get_bd_intf_pins smc_elt/S02_AXI]
connect_bd_intf_net [get_bd_intf_pins smc_elt/M00_AXI] [get_bd_intf_pins ps/S_AXI_HP1_FPD]

# ap_done interrupts: bit 0 conv_layer, bit 1 eltwise_layer (both must be enabled via IER/GIE).
connect_bd_net [get_bd_pins conv_layer_0/interrupt] [get_bd_pins irq_concat/In0]
connect_bd_net [get_bd_pins eltwise_layer_0/interrupt] [get_bd_pins irq_concat/In1]
connect_bd_net [get_bd_pins irq_concat/dout] [get_bd_pins ps/pl_ps_irq0]

# ---- Addresses --------------------------------------------------------------
# Control registers: fixed 64 KiB windows in the HPM0_LPD region, so board software can hard-code them.
assign_bd_address -offset 0x80000000 -range 64K \
    [get_bd_addr_segs conv_layer_0/s_axi_control/Reg]
assign_bd_address -offset 0x80010000 -range 64K \
    [get_bd_addr_segs eltwise_layer_0/s_axi_control/Reg]
# Accelerator masters: DDR only (low 2 GB at 0, high 2 GB at 0x8_0000_0000).
assign_bd_address
foreach seg [get_bd_addr_segs {/conv_layer_0/Data_m_axi_*/SEG_* /eltwise_layer_0/Data_m_axi_*/SEG_*}] {
    if {![string match *DDR* $seg]} { exclude_bd_addr_seg $seg }
}

validate_bd_design
save_bd_design

set bd_file [get_files $design.bd]
set wrapper [make_wrapper -files $bd_file -top]
add_files -norecurse $wrapper
set_property top ${design}_wrapper [current_fileset]
update_compile_order -fileset sources_1

file mkdir $output_dir
# Mapped segments of every master address space (PS control and accelerator data).
set fp [open [file join $output_dir address_map.txt] w]
foreach seg [get_bd_addr_segs {/ps/Data/SEG_* /conv_layer_0/Data_m_axi_*/SEG_* /eltwise_layer_0/Data_m_axi_*/SEG_*}] {
    if {[string is true -strict [get_property EXCLUDED $seg]]} { continue }
    puts $fp [format "%-58s offset %-20s range %s" $seg [get_property OFFSET $seg] [get_property RANGE $seg]]
}
close $fp
write_bd_tcl -force [file join $output_dir ${design}_bd.tcl]

if {$stage eq "create"} {
    puts "Block design created: $project_dir"
    exit
}

# ---- Build ------------------------------------------------------------------
generate_target all $bd_file
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "Implementation did not complete: [get_property STATUS [get_runs impl_1]]"
}
open_run impl_1
report_utilization -file [file join $output_dir utilization.rpt]
report_utilization -hierarchical -hierarchical_depth 2 -file [file join $output_dir utilization_hier.rpt]
report_timing_summary -max_paths 20 -file [file join $output_dir timing_summary.rpt]
report_drc -file [file join $output_dir drc.rpt]
set wns [get_property STATS.WNS [get_runs impl_1]]
set whs [get_property STATS.WHS [get_runs impl_1]]

set impl_dir [get_property DIRECTORY [get_runs impl_1]]
file copy -force [file join $impl_dir ${design}_wrapper.bit] [file join $output_dir $design.bit]
set hwh [lindex [glob -nocomplain [file join $project_dir $design.gen sources_1 bd $design hw_handoff $design.hwh]] 0]
if {$hwh eq ""} { error "Cannot find $design.hwh" }
file copy -force $hwh [file join $output_dir $design.hwh]
write_hw_platform -fixed -include_bit -force -file [file join $output_dir $design.xsa]

set fp [open [file join $output_dir build_summary.txt] w]
puts $fp "part $part"
puts $fp "WNS $wns ns, WHS $whs ns"
puts $fp "completed [clock format [clock seconds]]"
close $fp
if {$wns < 0 || $whs < 0} { error "Timing not met: WNS $wns ns, WHS $whs ns (see output/timing_summary.rpt)" }
puts "Build complete: WNS $wns ns, WHS $whs ns"
exit
