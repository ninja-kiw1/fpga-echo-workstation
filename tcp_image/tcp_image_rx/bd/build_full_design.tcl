# =============================================================================
# build_full_design.tcl  --  tcp_image_rx : complete ZynqMP + PL image-processor BD
#
# Creates the whole design from scratch (project + block design + wrapper):
#
#   PS(M_AXI_HPM0_LPD) --> sc_ctrl ---> AXI DMA  S_AXI_LITE   (control)
#                                  \--> AXI GPIO S_AXI       (mode register)
#
#   AXI DMA M_AXI_MM2S --\
#                         >-- sc_data --> PS S_AXI_HPC0_FPD  (read/write DDR)
#   AXI DMA M_AXI_S2MM --/
#
#   AXI DMA M_AXIS_MM2S --> img_proc_axis.s_axis
#   img_proc_axis.m_axis --> AXI DMA S_AXIS_S2MM
#   AXI GPIO ch1(16b out) --> img_proc_axis.ctrl  ([2:0]=mode [15:8]=thr)
#   {stat_max,stat_min,stat_valid,proc_busy} --> AXI GPIO ch2(18b in)
#
# Two SmartConnects are needed: the DMA's two AXI masters (MM2S+S2MM) cannot be
# wired straight onto one PS slave port (a slave port accepts a single master).
#
# NOTE: only the PL part of this design reaches the board at run time -- we
# load the bitstream with fpga_manager, and the PS keeps whatever the vendor
# boot.bin configured. So what matters is the PL clock (pl_clk0, verified live
# at 101.375 MHz) and which PS slave port we attach the DMA to.
#
# Comments are ASCII-only on purpose: Vivado on a Chinese (cp936) Windows may
# mis-read non-ASCII bytes inside Tcl.
#
# Usage (Vivado Tcl console, a project does NOT need to be open):
#   source <repo>/bd/build_full_design.tcl
# =============================================================================

set root    [file normalize [file join [file dirname [info script]] ..]]
set prjdir  "$root/vivado"
set prjname "img_proc"
set part    "xazu3eg-sfvc784-1-i"
set bdname  "system"

# Diagnostic switch: 1 = normal (DMA -> img_proc_axis -> DMA)
#                    0 = DMA loopback (M_AXIS_MM2S wired straight to S_AXIS_S2MM,
#                        img_proc_axis left out) -- used to tell "the PL cannot
#                        read DDR" apart from "the image pipeline never accepts
#                        the stream".  Both look identical from DMASR.
# Override before sourcing:   set use_proc 0 ; source build_full_design.tcl
if {![info exists use_proc]} { set use_proc 1 }

proc die {msg} { puts "FATAL: $msg" ; error $msg }

# ---------------------------------------------------------------------------
# 1) project + RTL
# ---------------------------------------------------------------------------
create_project $prjname "$prjdir/$prjname" -part $part -force
add_files -norecurse [glob $root/rtl/*.v]
puts "INFO: RTL added: [get_files -quiet -of_objects [get_filesets sources_1] *.v]"

create_bd_design $bdname

# ---------------------------------------------------------------------------
# 2) PS : one PL->PS slave port (HPC0) + one PS->PL master
# ---------------------------------------------------------------------------
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 zynq_ultra_ps_e_0]

# In this IP version the PL->PS slave ports are enabled through
# CONFIG.PSU__USE__S_AXI_GP0..6 ; GP0 -> S_AXI_HPC0_FPD, GP1 -> HPC1,
# GP2..GP5 -> HP0..HP3, GP6 -> S_AXI_LPD.
#
# We use GP2 -> S_AXI_HP0_FPD (non-coherent).  HPC0 was tried first and the
# DMA hung with no error: HPC is the *coherent* port and its AWUSER/ARUSER
# attribute bits get dropped by the interconnect (BD 41-237 warning), so a
# plain AXI DMA master cannot drive it.  HP0 is the correct port for that.
# Which PL->PS slave port(s) the DMA may use.
#   A single entry  -> the normal design.
#   Several entries -> the DMA's master fans out through sc_data, one MI per
#     PS slave port, each with its own DDR window, so ONE bitstream can tell
#     which port the vendor boot.bin actually left enabled (that is a PS
#     setting our .bit cannot change).
# Override before sourcing:   set ps_slave_ports {HPC1 HP1} ; source ...
if {![info exists ps_slave_ports]} { set ps_slave_ports {HP0} }

array set PORT_PROP {HPC0 PSU__USE__S_AXI_GP0 HPC1 PSU__USE__S_AXI_GP1 \
                     HP0  PSU__USE__S_AXI_GP2 HP1  PSU__USE__S_AXI_GP3 \
                     HP2  PSU__USE__S_AXI_GP4 HP3  PSU__USE__S_AXI_GP5 \
                     ACP  PSU__USE__S_AXI_ACP ACE  PSU__USE__S_AXI_ACE}
array set PORT_PIN {HPC0 S_AXI_HPC0_FPD HPC1 S_AXI_HPC1_FPD \
                    HP0 S_AXI_HP0_FPD HP1 S_AXI_HP1_FPD \
                    HP2 S_AXI_HP2_FPD HP3 S_AXI_HP3_FPD \
                    ACP S_AXI_ACP_FPD ACE S_AXI_ACE_FPD}
array set PORT_CLK {HPC0 saxihpc0_fpd_aclk HPC1 saxihpc1_fpd_aclk \
                    HP0 saxihp0_fpd_aclk HP1 saxihp1_fpd_aclk \
                    HP2 saxihp2_fpd_aclk HP3 saxihp3_fpd_aclk \
                    ACP saxiacp_fpd_aclk ACE sacefpd_aclk}

set_property CONFIG.PSU__USE__M_AXI_GP0 {1} $ps
for {set i 0} {$i <= 6} {incr i} { set_property CONFIG.PSU__USE__S_AXI_GP$i {0} $ps }
catch {set_property CONFIG.PSU__USE__S_AXI_ACP {0} $ps}
catch {set_property CONFIG.PSU__USE__S_AXI_ACE {0} $ps}

set ps_slave_pins {}
set ps_slave_clks {}
foreach nm $ps_slave_ports {
    if {![info exists PORT_PROP($nm)]} { die "unknown ps_slave_ports entry '$nm'" }
    set_property CONFIG.$PORT_PROP($nm) {1} $ps
    lappend ps_slave_pins [get_bd_intf_pins "${ps}/$PORT_PIN($nm)"]
    lappend ps_slave_clks "${ps}/$PORT_CLK($nm)"
}
puts "INFO: PS slave pins = $ps_slave_pins"

# The PS exposes two PS->PL masters here (HPM0_FPD via GP0, HPM0_LPD via GP2,
# the latter on by default).  We use ONLY HPM0_FPD: putting both onto one
# SmartConnect drops it into "low-area mode", which does not remap AXI IDs and
# was observed on hardware to return *stale read data* (every read came back
# with the previous transaction's value).  HPM0_FPD is also what the vendor's
# own reference design drives.
set ps_master ""
foreach p [get_bd_intf_pins -of_objects $ps] {
    set n [get_property NAME $p]
    if {[string match "M_AXI_HPM0_FPD" $n]} { set ps_master $p ; break }
}
if {$ps_master eq ""} {
    foreach p [get_bd_intf_pins -of_objects $ps] {
        if {[string match "M_AXI_HPM*" [get_property NAME $p]]} { set ps_master $p ; break }
    }
}
if {$ps_master eq ""} { die "PS has no M_AXI_HPM* master pin." }
puts "INFO: PS master pin = $ps_master"

# ---------------------------------------------------------------------------
# 3) IP cells
# ---------------------------------------------------------------------------
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 sc_ctrl
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] [get_bd_cells sc_ctrl]

# Data path PL->PS.  SmartConnect (not axi_interconnect) because the ZynqMP
# S_AXI_HP*_FPD slave presents a 6-bit AWID/ARID while a bare interconnect only
# propagates the master's 1-bit ID ("BD 41-2384 only lower order bits"), which
# makes the AFI lose track of outstanding reads -> the transfer just hangs.
# SmartConnect does the ID re-mapping for us.
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 sc_data
set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI [llength $ps_slave_pins]] [get_bd_cells sc_data]

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

if {$use_proc} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 concat_stat
    set_property -dict [list CONFIG.NUM_PORTS {4}] [get_bd_cells concat_stat]

    create_bd_cell -type module -reference img_proc_axis img_proc_axis_0
}

# ---------------------------------------------------------------------------
# 4) connections
# ---------------------------------------------------------------------------
# control path : the single PS->PL master into sc_ctrl
connect_bd_intf_net $ps_master [get_bd_intf_pins sc_ctrl/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M00_AXI] [get_bd_intf_pins axi_dma_img/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins sc_ctrl/M01_AXI] [get_bd_intf_pins gpio_ctrl/S_AXI]

# data path : both DMA masters -> SmartConnect -> one PS slave port per entry
connect_bd_intf_net [get_bd_intf_pins axi_dma_img/M_AXI_MM2S] [get_bd_intf_pins sc_data/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_dma_img/M_AXI_S2MM] [get_bd_intf_pins sc_data/S01_AXI]
set mi 0
foreach pp $ps_slave_pins {
    connect_bd_intf_net [get_bd_intf_pins sc_data/M0${mi}_AXI] $pp
    incr mi
}

if {$use_proc} {
    # image-processing pipeline in the middle of the stream
    connect_bd_intf_net [get_bd_intf_pins axi_dma_img/M_AXIS_MM2S] [get_bd_intf_pins img_proc_axis_0/s_axis]
    connect_bd_intf_net [get_bd_intf_pins img_proc_axis_0/m_axis] [get_bd_intf_pins axi_dma_img/S_AXIS_S2MM]

    # mode register + frame statistics
    connect_bd_net [get_bd_pins gpio_ctrl/gpio_io_o] [get_bd_pins img_proc_axis_0/ctrl]
    connect_bd_net [get_bd_pins img_proc_axis_0/stat_max]   [get_bd_pins concat_stat/In0]
    connect_bd_net [get_bd_pins img_proc_axis_0/stat_min]   [get_bd_pins concat_stat/In1]
    connect_bd_net [get_bd_pins img_proc_axis_0/stat_valid] [get_bd_pins concat_stat/In2]
    connect_bd_net [get_bd_pins img_proc_axis_0/proc_busy]  [get_bd_pins concat_stat/In3]
    connect_bd_net [get_bd_pins concat_stat/dout] [get_bd_pins gpio_ctrl/gpio2_io_i]
} else {
    # pure DMA loopback: MM2S stream straight into S2MM
    puts "INFO: use_proc=0 -> wiring DMA loopback (no img_proc_axis)"
    connect_bd_intf_net [get_bd_intf_pins axi_dma_img/M_AXIS_MM2S] [get_bd_intf_pins axi_dma_img/S_AXIS_S2MM]
}

# clock : everything on pl_clk0.  The PS's own AXI-interface clock pins must be
# fed too, otherwise validation fails with BD 41-758.
set plclk [get_bd_pins ${ps}/pl_clk0]
set clk_sinks [list sc_ctrl/ACLK sc_ctrl/S00_ACLK sc_ctrl/M00_ACLK sc_ctrl/M01_ACLK \
    sc_data/aclk \
    axi_dma_img/s_axi_lite_aclk axi_dma_img/m_axi_mm2s_aclk axi_dma_img/m_axi_s2mm_aclk \
    gpio_ctrl/s_axi_aclk img_proc_axis_0/aclk rst_pl/slowest_sync_clk \
    "${ps}/maxihpm0_fpd_aclk" "${ps}/maxihpm0_lpd_aclk" {*}$ps_slave_clks]
foreach p $clk_sinks {
    if {[llength [get_bd_pins -quiet $p]] > 0} {
        connect_bd_net $plclk [get_bd_pins $p]
    } else {
        puts "WARN: clock pin $p not present, skipped"
    }
}

# reset : pl_resetn0 -> proc_sys_reset -> peripheral_aresetn
connect_bd_net [get_bd_pins ${ps}/pl_resetn0] [get_bd_pins rst_pl/ext_reset_in]
set plrstn [get_bd_pins rst_pl/peripheral_aresetn]
set rst_sinks [list sc_ctrl/ARESETN sc_ctrl/S00_ARESETN sc_ctrl/M00_ARESETN sc_ctrl/M01_ARESETN \
    sc_data/aresetn \
    axi_dma_img/axi_resetn gpio_ctrl/s_axi_aresetn img_proc_axis_0/aresetn]
foreach p $rst_sinks {
    if {[llength [get_bd_pins -quiet $p]] > 0} {
        connect_bd_net $plrstn [get_bd_pins $p]
    } else {
        puts "WARN: reset pin $p not present, skipped"
    }
}

# DMA interrupts intentionally left unconnected -- the Linux side polls.

# ---------------------------------------------------------------------------
# 5) addresses + validate + save
# ---------------------------------------------------------------------------
assign_bd_address

# Window fix-up.  With several PS slave ports, the auto-assigner splits the DDR
# range into equal slices, and every slice ends up mapping physical 0..(size).
# Physical 0..1G is Linux System RAM -- DMA there corrupts the kernel.  So force
# window n to offset n*0x80000000 with the FULL 2 GB range: then
#     window 0 : DMA 0x4000_0000 -> physical 0x4000_0000
#     window 1 : DMA 0xC000_0000 -> physical 0x4000_0000
# i.e. the same safe (no-map) DDR is reachable through every window.
if {[llength $ps_slave_ports] > 1} {
    set wi 0
    foreach nm $ps_slave_ports {
        set off [expr {$wi == 0 ? 0x00000000 : 0x80000000}]
        foreach sp {Data_MM2S Data_S2MM} {
            set asp [get_bd_addr_spaces -quiet "axi_dma_img/$sp"]
            if {[llength $asp] == 0} { continue }
            foreach seg [get_bd_addr_segs -quiet -of_objects $asp] {
                if {[string match "*_${nm}_DDR_LOW" [get_property NAME $seg]]} {
                    if {[catch {assign_bd_address -offset $off -range 0x80000000 \
                                 -target_address_space $asp $seg} m]} {
                        puts "WARN: window fixup $nm failed: $m"
                    }
                }
            }
        }
        incr wi
    }
}

puts "INFO: ---- DMA master -> PS DDR windows ----"
foreach sp {Data_MM2S Data_S2MM} {
    set asp [get_bd_addr_spaces -quiet "axi_dma_img/$sp"]
    if {[llength $asp] == 0} { continue }
    foreach seg [get_bd_addr_segs -quiet -of_objects $asp] {
        puts "   $sp : [get_property NAME $seg]  off=[get_property -quiet OFFSET $seg] range=[get_property -quiet RANGE $seg]"
    }
}
puts "INFO: ---- register bases ----"
foreach c {axi_dma_img gpio_ctrl} {
    foreach seg [get_bd_addr_segs -quiet -of_objects [get_bd_cells $c]] {
        set off [get_property -quiet OFFSET $seg]
        if {$off ne ""} { puts "   [get_property NAME $seg]  offset = $off" }
    }
}

if {[catch {validate_bd_design} vm]} {
    puts "VALIDATE_FAILED: $vm"
    puts "VALIDATION_ERRORINFO: $::errorInfo"
} else {
    puts "INFO: validate_bd_design OK"
}
save_bd_design

# ---------------------------------------------------------------------------
# 6) wrapper + top
# ---------------------------------------------------------------------------
set bdfile [get_files -quiet "${bdname}.bd"]
make_wrapper -files $bdfile -top
set wrap [glob -nocomplain "$prjdir/$prjname/$prjname.gen/sources_1/bd/${bdname}/hdl/${bdname}_wrapper.v"]
if {[llength $wrap] > 0} {
    add_files -norecurse $wrap
    set_property top ${bdname}_wrapper [get_filesets sources_1]
    puts "INFO: top set to ${bdname}_wrapper"
} else {
    puts "WARN: wrapper not found; check make_wrapper output."
}
update_compile_order -fileset sources_1
puts "DONE build_full_design.tcl"
