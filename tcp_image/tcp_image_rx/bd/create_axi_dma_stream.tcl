# =============================================================================
# create_axi_dma_stream.tcl
#
# Add an AXI DMA (MM2S only, simple mode) to an EXISTING block design that
# already contains a configured Zynq UltraScale+ PS, and expose the MM2S
# AXI4-Stream to the PL so the received image can be streamed out of DDR.
#
# ---------------------------------------------------------------------------
# PREREQUISITES (do these by hand first -- this script does NOT touch the PS):
#
#   * A block design exists that contains zynq_ultra_ps_e, with:
#       - at least one ENABLED PL-facing slave port  (S_AXI_HPC0_FPD / HP0_FPD
#         ...) so AXI DMA can read DDR through the PS;
#       - at least one ENABLED PL-facing master port (M_AXI_HPM0_LPD / ...)
#         so the Linux app can program the DMA registers;
#       - PL clock FCLK0 enabled (pl_clk0) and PL reset (pl_resetn0) enabled.
#   * The PS preset -- DDR4 timings for MT40A512M16LY, GEM3 on MIO64..77,
#     SD, UART -- comes from the FZ3B vendor board files or a known-good
#     project. This script never invents DDR4 parameters.
#
# ---------------------------------------------------------------------------
# WHY ASCII-ONLY COMMENTS:
#   Vivado running on a Chinese (cp936) Windows can mis-read non-ASCII bytes
#   in a Tcl script. Chinese explanation for this file lives in docs/.
#
# USAGE (Vivado Tcl console, with the project already open):
#   source <repo>/bd/create_axi_dma_stream.tcl
#
# VERSION NOTE: written against Vivado 2025.2 IP revisions
#   axi_dma 7.1 / smartconnect 1.0 / proc_sys_reset 5.0.
# =============================================================================

# ------------------------------ parameters ----------------------------------
set bd_name       "system"                 ;# existing block design name
set ps_name       "zynq_ultra_ps_e_0"      ;# existing PS instance name
set dma_name      "axi_dma_0"
set rst_name      "rst_ps"
set sc_name       "smartconnect_0"
set clk_pin_name  "pl_clk0"                ;# PS PL clock
set rst_pin_name  "pl_resetn0"             ;# PS PL reset (active low)
set stream_name   "M_AXIS_MM2S"            ;# exported external stream port name
set mm2s_data_w   64                       ;# DMA data width (bytes/sample step)
set mm2s_tdata_w  32                       ;# AXI-Stream data width (bits)
set export_xsa    0                        ;# set to 1 to also write an XSA

# ------------------------------ helpers -------------------------------------
proc die {msg} {
    puts "ERROR: $msg"
    error $msg
}

# ------------------------------ sanity checks -------------------------------
if {[llength [current_project -quiet]] == 0} {
    die "No project is open. Open your Vivado project first."
}

set bd_files [get_files -quiet "*${bd_name}.bd"]
if {[llength $bd_files] == 0} {
    die "Block design '${bd_name}.bd' not found. Adjust bd_name at the top."
}
open_bd_design $bd_files

if {[llength [get_bd_cells -quiet $ps_name]] == 0} {
    die "PS cell '$ps_name' not found. Adjust ps_name at the top."
}

if {[llength [get_bd_pins -quiet ${ps_name}/${clk_pin_name}]] == 0} {
    die "PS clock '${ps_name}/${clk_pin_name}' not found. Enable FCLK0 in the PS."
}
if {[llength [get_bd_pins -quiet ${ps_name}/${rst_pin_name}]] == 0} {
    die "PS reset '${ps_name}/${rst_pin_name}' not found. Enable the PL reset in the PS."
}

# Find the first enabled PL-facing PS slave port (DMA reads DDR through it).
set ps_slave ""
foreach p [get_bd_intf_pins -quiet -of_objects [get_bd_cells $ps_name]] {
    if {[string match "S_AXI_HP*" [get_property NAME $p]]} {
        set ps_slave $p
        break
    }
}
if {$ps_slave eq ""} {
    die "PS exposes no enabled PL slave port (S_AXI_HPC0_FPD/HP0_FPD/...). \
         Enable one so AXI DMA can read DDR."
}
puts "INFO: DMA will read DDR through PS slave port: [get_property NAME $ps_slave]"

# Find the first enabled PL-facing PS master port (app programs DMA through it).
set ps_master ""
foreach p [get_bd_intf_pins -quiet -of_objects [get_bd_cells $ps_name]] {
    if {[string match "M_AXI_HPM*" [get_property NAME $p]]} {
        set ps_master $p
        break
    }
}
if {$ps_master eq ""} {
    die "PS exposes no enabled PL master port (M_AXI_HPM0_LPD/...). \
         Enable one so the Linux app can program AXI DMA."
}
puts "INFO: Linux app will program DMA through PS master port: [get_property NAME $ps_master]"

# ------------------------------ create the IP -------------------------------
if {[llength [get_bd_cells -quiet $dma_name]] == 0} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 $dma_name
}
set_property -dict [list \
    CONFIG.c_include_sg              {0} \
    CONFIG.c_sg_include_stscntrl_strm {0} \
    CONFIG.c_include_mm2s            {1} \
    CONFIG.c_include_s2mm            {0} \
    CONFIG.c_include_mm2s_dre        {0} \
    CONFIG.c_micro_dma               {0} \
    CONFIG.c_addr_width              {32} \
    CONFIG.c_m_axi_mm2s_data_width   $mm2s_data_w \
    CONFIG.c_m_axis_mm2s_tdata_width $mm2s_tdata_w \
] [get_bd_cells $dma_name]
puts "INFO: $dma_name configured: MM2S only, simple mode, addr width 32."

if {[llength [get_bd_cells -quiet $sc_name]] == 0} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 $sc_name
}
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] [get_bd_cells $sc_name]

if {[llength [get_bd_cells -quiet $rst_name]] == 0} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 $rst_name
}

# ------------------------------ wiring: control path ------------------------
connect_bd_intf_net $ps_master [get_bd_intf_pins ${sc_name}/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ${sc_name}/M00_AXI] [get_bd_intf_pins ${dma_name}/S_AXI_LITE]

# ------------------------------ wiring: data path ---------------------------
# AXI DMA MM2S reads DDR through the PS slave port.
connect_bd_intf_net [get_bd_intf_pins ${dma_name}/M_AXI_MM2S] $ps_slave

# ------------------------------ wiring: clocks / resets ---------------------
connect_bd_net [get_bd_pins ${ps_name}/${clk_pin_name}] [get_bd_pins ${sc_name}/aclk]
connect_bd_net [get_bd_pins ${ps_name}/${clk_pin_name}] [get_bd_pins ${dma_name}/s_axi_lite_aclk]
connect_bd_net [get_bd_pins ${ps_name}/${clk_pin_name}] [get_bd_pins ${dma_name}/m_axi_mm2s_aclk]
connect_bd_net [get_bd_pins ${ps_name}/${clk_pin_name}] [get_bd_pins ${rst_name}/slowest_sync_clk]
connect_bd_net [get_bd_pins ${ps_name}/${rst_pin_name}] [get_bd_pins ${rst_name}/ext_reset_in]
connect_bd_net [get_bd_pins ${rst_name}/peripheral_aresetn] [get_bd_pins ${dma_name}/axi_resetn]
connect_bd_net [get_bd_pins ${rst_name}/peripheral_aresetn] [get_bd_pins ${sc_name}/aresetn]

# NOTE: mm2s_introut is intentionally left unconnected. The Linux app polls
# MM2S_DMASR instead of using interrupts, which keeps the design simple.

# ------------------------------ export the AXI-Stream ------------------------
set before [get_bd_intf_ports -quiet]
make_bd_intf_pins_external [get_bd_intf_pins ${dma_name}/M_AXIS_MM2S]
foreach p [get_bd_intf_ports -quiet] {
    if {[lsearch -exact $before $p] < 0} {
        set_property NAME $stream_name $p
        puts "INFO: exported external AXI-Stream port: $stream_name"
    }
}

# ------------------------------ addresses ------------------------------------
assign_bd_address -quiet
foreach seg [get_bd_addr_segs -quiet -of_objects [get_bd_cells $dma_name]] {
    set off [get_property -quiet OFFSET $seg]
    if {$off ne ""} {
        puts "== AXI DMA register (S_AXI_LITE) base = $off"
        puts "   -> pass this to the Linux app as:  --dma-addr $off"
    }
}

# ------------------------------ validate / save ------------------------------
validate_bd_design
save_bd_design
puts "INFO: block design '$bd_name' updated and saved."

if {$export_xsa} {
    set xsa_path [file join [get_property DIRECTORY [current_project]] "${bd_name}.xsa"]
    write_hw_platform -fixed -include_bit -force -file $xsa_path
    puts "INFO: wrote hardware platform: $xsa_path"
}

puts "DONE. Reminder: the image DDR window (0x30000000) is reserved in the"
puts "      device tree (dts/zynqmp-image-reserved-memory.dtsi), not here."
