# fix3.tcl -- 数据通路用第二个 PS 主口(M_AXI_HPM1_FPD, 128 位)，全链路同宽
#
#  教训：只要 128 位的 IP 主口和 32 位的 PS 主口出现在同一个互联里，互联内部就被撑到 128 位，
#        32 位那条就被 auto_us/auto_ds 转换 -> 写数据丢。所以必须**物理分开**：
#
#  控制(32 位): PS M_AXI_HPM0_FPD(32) -> sc_axil(1SI:3MI) -> {pc_axil->img_proc, conv ctrl, elt ctrl}
#  数据(128位): PS M_AXI_HPM1_FPD(128) + conv 3 主口 + eltwise 3 主口 -> ai_data_ic(7SI:1MI) -> bram_ctrl(128)
#  => 两边都不需要位宽转换。CPU 的 32bit store 由 PS 自己按 128bit 拍 + WSTRB 发出去，
#     BRAM 控制器认 WSTRB，所以数据能正确落。

set R [file normalize [file join [file dirname [info script]] ../..]]

# ---- 1) 控制互联回到"只有 PS 一个 SI, 3 个 MI" ----
foreach pin {S01_AXI S02_AXI S03_AXI S04_AXI S05_AXI S06_AXI} {
    set n [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins sc_axil/$pin]]
    if {$n ne ""} { catch { delete_bd_objs $n } }
}
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {3}] [get_bd_cells sc_axil]

# ---- 2) 打开 PS 的第二个 FPD 主口(128 位) ----
set ps [get_bd_cells zynq_ultra_ps_e_0]
set_property CONFIG.PSU__USE__M_AXI_GP1 {1} $ps
catch { set_property CONFIG.PSU__MAXIGP1__DATA_WIDTH {128} $ps }

# ---- 3) 128 位数据互联 ----
if {[llength [get_bd_cells -quiet ai_data_ic]] == 0} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ai_data_ic
}
set_property -dict [list CONFIG.NUM_SI {7} CONFIG.NUM_MI {1}] [get_bd_cells ai_data_ic]

# ---- 4) BRAM 回到 128 位单口 ----
set_property -dict [list CONFIG.DATA_WIDTH {128} CONFIG.SINGLE_PORT_BRAM {1} \
                         CONFIG.SUPPORTS_NARROW_BURST {0} CONFIG.PROTOCOL {AXI4}] [get_bd_cells bram_ctrl]
set_property -dict [list \
  CONFIG.use_bram_block {BRAM_Controller} \
  CONFIG.Memory_Type {Single_Port_RAM} \
  CONFIG.Byte_Size {8} CONFIG.Use_Byte_Write_Enable {true} \
  CONFIG.Write_Width_A {128} CONFIG.Read_Width_A {128} CONFIG.Write_Depth_A {8192} \
  CONFIG.Register_PortA_Output_of_Memory_Primitives {false} \
  CONFIG.Register_PortA_Output_of_Memory_Core {false} \
  CONFIG.EN_SAFETY_CKT {true} CONFIG.Load_Init_File {false} CONFIG.Interface_Type {Native} \
] [get_bd_cells bram_gen]

# ---- 5) 数据侧接线 ----
catch {connect_bd_intf_net [get_bd_intf_pins ${ps}/M_AXI_HPM1_FPD]     [get_bd_intf_pins ai_data_ic/S00_AXI]}
foreach {net pin} {
    conv_layer_0/m_axi_gmem_in    ai_data_ic/S01_AXI
    conv_layer_0/m_axi_gmem_out   ai_data_ic/S02_AXI
    conv_layer_0/m_axi_gmem_par   ai_data_ic/S03_AXI
    eltwise_layer_0/m_axi_gmem_a  ai_data_ic/S04_AXI
    eltwise_layer_0/m_axi_gmem_b  ai_data_ic/S05_AXI
    eltwise_layer_0/m_axi_gmem_out ai_data_ic/S06_AXI
} { catch { connect_bd_intf_net [get_bd_intf_pins $net] [get_bd_intf_pins $pin] } }
catch {connect_bd_intf_net [get_bd_intf_pins ai_data_ic/M00_AXI] [get_bd_intf_pins bram_ctrl/S_AXI]}
catch {connect_bd_intf_net [get_bd_intf_pins bram_ctrl/BRAM_PORTA] [get_bd_intf_pins bram_gen/BRAM_PORTA]}

# 控制侧: M03 -> bram 断开(数据不再走控制互联)
set n3 [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins sc_axil/M03_AXI]]
if {$n3 ne ""} { catch { delete_bd_objs $n3 } }
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {3}] [get_bd_cells sc_axil]
puts "wiring done"

# ---- 6) 时钟/复位 ----
set plclk  [get_bd_pins ${ps}/pl_clk0]
set plrstn [get_bd_pins rst_pl/peripheral_aresetn]
set icrstn [get_bd_pins rst_pl/interconnect_aresetn]
foreach p [list ai_data_ic/ACLK ai_data_ic/S00_ACLK ai_data_ic/S01_ACLK ai_data_ic/S02_ACLK \
                ai_data_ic/S03_ACLK ai_data_ic/S04_ACLK ai_data_ic/S05_ACLK ai_data_ic/S06_ACLK \
                ai_data_ic/M00_ACLK bram_gen/clka ${ps}/maxihpm1_fpd_aclk] {
    if {[llength [get_bd_pins -quiet $p]] > 0} { catch { connect_bd_net $plclk [get_bd_pins $p] } }
}
foreach p [list ai_data_ic/ARESETN ai_data_ic/S00_ARESETN ai_data_ic/S01_ARESETN ai_data_ic/S02_ARESETN \
                ai_data_ic/S03_ARESETN ai_data_ic/S04_ARESETN ai_data_ic/S05_ARESETN ai_data_ic/S06_ARESETN \
                ai_data_ic/M00_ARESETN] {
    if {[llength [get_bd_pins -quiet $p]] > 0} { catch { connect_bd_net $icrstn [get_bd_pins $p] } }
}
catch { connect_bd_net $plrstn [get_bd_pins bram_ctrl/s_axi_aresetn] }
puts "clk/rst done"

# ---- 7) 地址 ----
catch {assign_bd_address -offset 0xA0010000 -range 64K  [get_bd_addr_segs conv_layer_0/s_axi_control/Reg]}
catch {assign_bd_address -offset 0xA0020000 -range 64K  [get_bd_addr_segs eltwise_layer_0/s_axi_control/Reg]}
catch {assign_bd_address -offset 0xA0100000 -range 128K [get_bd_addr_segs bram_ctrl/S_AXI/Mem0]}
catch {assign_bd_address}
foreach seg [get_bd_addr_segs -quiet {/conv_layer_0/Data_m_axi_*/SEG_* /eltwise_layer_0/Data_m_axi_*/SEG_*}] {
  if {![string match *bram* $seg]} { catch { exclude_bd_addr_seg $seg } }
}
set rc [catch {validate_bd_design -force} vm]
puts "validate rc=$rc : $vm"
puts "bram_ctrl DW=[get_property CONFIG.DATA_WIDTH [get_bd_cells bram_ctrl]] depth=[get_property CONFIG.MEM_DEPTH [get_bd_cells bram_ctrl]]"
catch {save_bd_design}
puts "FIX3_DONE"
