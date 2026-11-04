# fix2.tcl -- 合并互联 + 32 位单口 BRAM
#
#  问题回顾：之前 BRAM 用"非对称双口"(A 128 给 IP / B 32 给 PS) + 两个控制器(SINGLE_PORT_BRAM=1)，
#  而且控制器的 BRAM_PORTA 连到了 BRAM 的 BRAM_PORTB（跨名连接）-> 写不进去。
#
#  本版（标准做法）：
#    一个互联 sc_axil，SI = {PS(32) + conv 3 主口 + eltwise 3 主口}，MI = {pc_axil, conv ctrl, elt ctrl, BRAM(32)}
#    -> 互联内部宽度 = 32 位(由 MI 决定) => PS 原生不转换；IP 的 128 位走 128->32 缩小(整字访问, 安全)
#    BRAM 只做 32 位单口，一个 bram_ctrl(SINGLE_PORT_BRAM=1) 供所有人访问。

set R [file normalize [file join [file dirname [info script]] ../..]]

# ---- 1) 拆掉上一版的 128 位专线和第二个控制器 ----
if {[llength [get_bd_cells -quiet ai_ip_ic]] > 0}   { delete_bd_objs [get_bd_cells ai_ip_ic] }
if {[llength [get_bd_cells -quiet bram32_ctrl]] > 0} { delete_bd_objs [get_bd_cells bram32_ctrl] }

# ---- 2) 互联：7 SI : 4 MI ----
set_property -dict [list CONFIG.NUM_SI {7} CONFIG.NUM_MI {4}] [get_bd_cells sc_axil]

# ---- 3) BRAM 控制器改成 32 位单口 ----
set_property -dict [list CONFIG.DATA_WIDTH {32} CONFIG.SINGLE_PORT_BRAM {1} \
                         CONFIG.SUPPORTS_NARROW_BURST {0} CONFIG.PROTOCOL {AXI4}] [get_bd_cells bram_ctrl]

# ---- 4) BRAM 改成 32 位单口 ----
set_property -dict [list \
  CONFIG.use_bram_block {BRAM_Controller} \
  CONFIG.Memory_Type {Single_Port_RAM} \
  CONFIG.Byte_Size {8} CONFIG.Use_Byte_Write_Enable {true} \
  CONFIG.Write_Width_A {32} CONFIG.Read_Width_A {32} CONFIG.Write_Depth_A {32768} \
  CONFIG.Register_PortA_Output_of_Memory_Primitives {false} \
  CONFIG.Register_PortA_Output_of_Memory_Core {false} \
  CONFIG.EN_SAFETY_CKT {true} CONFIG.Load_Init_File {false} CONFIG.Interface_Type {Native} \
] [get_bd_cells bram_gen]

# ---- 5) IP 的 6 个主口接到互联的 S01..S06 ----
foreach {net pin} {
    conv_layer_0/m_axi_gmem_in    sc_axil/S01_AXI
    conv_layer_0/m_axi_gmem_out   sc_axil/S02_AXI
    conv_layer_0/m_axi_gmem_par   sc_axil/S03_AXI
    eltwise_layer_0/m_axi_gmem_a  sc_axil/S04_AXI
    eltwise_layer_0/m_axi_gmem_b  sc_axil/S05_AXI
    eltwise_layer_0/m_axi_gmem_out sc_axil/S06_AXI
} { catch { connect_bd_intf_net [get_bd_intf_pins $net] [get_bd_intf_pins $pin] } }

# ---- 6) BRAM: 控制器 <-> 存储（同名端口, 不再跨名） ----
catch {connect_bd_intf_net [get_bd_intf_pins sc_axil/M03_AXI] [get_bd_intf_pins bram_ctrl/S_AXI]}
catch {connect_bd_intf_net [get_bd_intf_pins bram_ctrl/BRAM_PORTA] [get_bd_intf_pins bram_gen/BRAM_PORTA]}
puts "data path wired"

# ---- 7) 时钟/复位 ----
set plclk  [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]
set icrstn [get_bd_pins rst_pl/interconnect_aresetn]
set plrstn [get_bd_pins rst_pl/peripheral_aresetn]
foreach p {sc_axil/S01_ACLK sc_axil/S02_ACLK sc_axil/S03_ACLK sc_axil/S04_ACLK sc_axil/S05_ACLK sc_axil/S06_ACLK \
           bram_gen/clka} {
    if {[llength [get_bd_pins -quiet $p]] > 0} { catch { connect_bd_net $plclk [get_bd_pins $p] } }
}
foreach p {sc_axil/S01_ARESETN sc_axil/S02_ARESETN sc_axil/S03_ARESETN sc_axil/S04_ARESETN \
           sc_axil/S05_ARESETN sc_axil/S06_ARESETN} {
    if {[llength [get_bd_pins -quiet $p]] > 0} { catch { connect_bd_net $icrstn [get_bd_pins $p] } }
}
catch { connect_bd_net $plrstn [get_bd_pins bram_ctrl/s_axi_aresetn] }
puts "clk/rst wired"

# ---- 8) 地址 ----
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
puts "bram_gen type=[get_property CONFIG.Memory_Type [get_bd_cells bram_gen]] A=[get_property CONFIG.Write_Width_A [get_bd_cells bram_gen]]x[get_property CONFIG.Write_Depth_A [get_bd_cells bram_gen]]"
catch {save_bd_design}
puts "FIX2_DONE"
