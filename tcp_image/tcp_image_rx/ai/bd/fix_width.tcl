# fix_width.tcl -- 修"位宽转换器丢写数据"：让每条路径位宽自洽
#
#  32 位侧(已验证的 FPD 通路): PS(32) -> sc_axil(32)
#      M00 -> pc_axil -> img_proc_axil_0        (不动)
#      M01 -> conv_layer_0/s_axi_control        (32, 无转换)
#      M02 -> eltwise_layer_0/s_axi_control     (32, 无转换)
#      M03 -> bram32_ctrl/S_AXI                 (32, 无转换)   <-- PS 读写 BRAM
#  128 位侧: 6 个 IP 主口 -> ai_ip_ic(128) -> M00 -> bram128_ctrl/S_AXI   (128/64, 无 32 位混入)
#  BRAM: bram_gen True Dual Port, A 口 128 位(接 bram128_ctrl), B 口 32 位(接 bram32_ctrl)

set R [file normalize [file join [file dirname [info script]] ../..]]

# ---- 1) 拆掉那个 7SI:3MI 的混合互联 ----
if {[llength [get_bd_cells -quiet ai_ic]] > 0} { delete_bd_objs [get_bd_cells ai_ic] }

# ---- 2) 把现有 bram_ctrl 定成 128 位单口(给 IP 侧), 新建 32 位单口(给 PS 侧) ----
set_property -dict [list CONFIG.DATA_WIDTH {128} CONFIG.SINGLE_PORT_BRAM {1} \
                         CONFIG.SUPPORTS_NARROW_BURST {0} CONFIG.PROTOCOL {AXI4}] [get_bd_cells bram_ctrl]
if {[llength [get_bd_cells -quiet bram32_ctrl]] == 0} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl bram32_ctrl
}
set_property -dict [list CONFIG.DATA_WIDTH {32} CONFIG.SINGLE_PORT_BRAM {1} \
                         CONFIG.SUPPORTS_NARROW_BURST {0} CONFIG.PROTOCOL {AXI4}] [get_bd_cells bram32_ctrl]

# ---- 3) BRAM 双口, A 128 / B 32 ----
set_property -dict [list \
  CONFIG.use_bram_block {BRAM_Controller} \
  CONFIG.Memory_Type {True_Dual_Port_RAM} \
  CONFIG.Byte_Size {8} CONFIG.Use_Byte_Write_Enable {true} \
  CONFIG.Write_Width_A {128} CONFIG.Read_Width_A {128} CONFIG.Write_Depth_A {8192} \
  CONFIG.Write_Width_B {32}  CONFIG.Read_Width_B {32} \
  CONFIG.Register_PortA_Output_of_Memory_Primitives {false} \
  CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
  CONFIG.Register_PortA_Output_of_Memory_Core {false} \
  CONFIG.Register_PortB_Output_of_Memory_Core {false} \
  CONFIG.EN_SAFETY_CKT {true} CONFIG.Load_Init_File {false} CONFIG.Interface_Type {Native} \
] [get_bd_cells bram_gen]

# ---- 4) 128 位互联: 6 个 IP 主口 -> bram128_ctrl ----
if {[llength [get_bd_cells -quiet ai_ip_ic]] == 0} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ai_ip_ic
}
set_property -dict [list CONFIG.NUM_SI {6} CONFIG.NUM_MI {1}] [get_bd_cells ai_ip_ic]

# ---- 5) 32 位 FPD 互联扩到 4 个 MI ----
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {4}] [get_bd_cells sc_axil]

# ---- 6) PS 侧(全 32 位) ----
catch {connect_bd_intf_net [get_bd_intf_pins sc_axil/M01_AXI] [get_bd_intf_pins conv_layer_0/s_axi_control]}
catch {connect_bd_intf_net [get_bd_intf_pins sc_axil/M02_AXI] [get_bd_intf_pins eltwise_layer_0/s_axi_control]}
catch {connect_bd_intf_net [get_bd_intf_pins sc_axil/M03_AXI] [get_bd_intf_pins bram32_ctrl/S_AXI]}
puts "PS side wired"

# ---- 7) IP 侧(128 位) ----
foreach {net pin} {
    conv_layer_0/m_axi_gmem_in   ai_ip_ic/S00_AXI
    conv_layer_0/m_axi_gmem_out  ai_ip_ic/S01_AXI
    conv_layer_0/m_axi_gmem_par  ai_ip_ic/S02_AXI
    eltwise_layer_0/m_axi_gmem_a ai_ip_ic/S03_AXI
    eltwise_layer_0/m_axi_gmem_b ai_ip_ic/S04_AXI
    eltwise_layer_0/m_axi_gmem_out ai_ip_ic/S05_AXI
} { catch { connect_bd_intf_net [get_bd_intf_pins $net] [get_bd_intf_pins $pin] } }
catch {connect_bd_intf_net [get_bd_intf_pins ai_ip_ic/M00_AXI] [get_bd_intf_pins bram_ctrl/S_AXI]}

# ---- 8) BRAM 两口 ----
catch {connect_bd_intf_net [get_bd_intf_pins bram_ctrl/BRAM_PORTA]  [get_bd_intf_pins bram_gen/BRAM_PORTA]}
catch {connect_bd_intf_net [get_bd_intf_pins bram32_ctrl/BRAM_PORTA] [get_bd_intf_pins bram_gen/BRAM_PORTB]}
puts "IP side + BRAM wired"

# ---- 9) 时钟 / 复位 ----
set plclk  [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]
set plrstn [get_bd_pins rst_pl/peripheral_aresetn]
set icrstn [get_bd_pins rst_pl/interconnect_aresetn]
foreach p [list ai_ip_ic/ACLK ai_ip_ic/S00_ACLK ai_ip_ic/S01_ACLK ai_ip_ic/S02_ACLK \
                ai_ip_ic/S03_ACLK ai_ip_ic/S04_ACLK ai_ip_ic/S05_ACLK ai_ip_ic/M00_ACLK \
                bram32_ctrl/s_axi_aclk bram_gen/clka bram_gen/clkb] {
    if {[llength [get_bd_pins -quiet $p]] > 0} { catch { connect_bd_net $plclk [get_bd_pins $p] } }
}
foreach p [list bram32_ctrl/s_axi_aresetn] {
    if {[llength [get_bd_pins -quiet $p]] > 0} { catch { connect_bd_net $plrstn [get_bd_pins $p] } }
}
foreach p [list ai_ip_ic/ARESETN ai_ip_ic/S00_ARESETN ai_ip_ic/S01_ARESETN ai_ip_ic/S02_ARESETN \
                ai_ip_ic/S03_ARESETN ai_ip_ic/S04_ARESETN ai_ip_ic/S05_ARESETN ai_ip_ic/M00_ARESETN] {
    if {[llength [get_bd_pins -quiet $p]] > 0} { catch { connect_bd_net $icrstn [get_bd_pins $p] } }
}
puts "clk/rst wired"
puts "FIX_WIDTH_DONE"
