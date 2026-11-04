# wire_ai.tcl -- 把 AI 子系统接进现有 BD
#   策略：AI 子系统挂在 **M_AXI_HPM0_LPD**（第二个 PS 主口）上，**完全不动**现有
#         M_AXI_HPM0_FPD -> sc_axil -> pc_axil -> img_proc_axil 那条已经工作的通路。
#   结构（照抄 PLD create_system.tcl 的接线方式，只是把 HP 换成片上 BRAM）：
#     PS(M_AXI_HPM0_LPD) + conv 的 3 个主口 + eltwise 的 3 个主口  -> ai_ic(7SI:3MI)
#       M00 -> conv_layer_0/s_axi_control
#       M01 -> eltwise_layer_0/s_axi_control
#       M02 -> bram_ctrl/S_AXI   (数据)
#     bram_ctrl/BRAM_PORTA|B <-> bram_gen/BRAM_PORTA|B

set R [file normalize [file join [file dirname [info script]] ../..]]

# -- 1) PS 打开 M_AXI_HPM0_LPD (GP2) --
set ps [get_bd_cells zynq_ultra_ps_e_0]
set_property CONFIG.PSU__USE__M_AXI_GP2 {1} $ps
catch { set_property CONFIG.PSU__MAXIGP2__DATA_WIDTH {32} $ps }

# -- 2) 互联件：用 axi_interconnect(与我们已验证的图像通路同类) --
if {[llength [get_bd_cells -quiet smc_ai_data]] > 0} { delete_bd_objs [get_bd_cells smc_ai_data] }
if {[llength [get_bd_cells -quiet ai_ic]] == 0} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ai_ic
}
set_property -dict [list CONFIG.NUM_SI {7} CONFIG.NUM_MI {3}] [get_bd_cells ai_ic]

# -- 3) 接口连线 --
connect_bd_intf_net [get_bd_intf_pins ${ps}/M_AXI_HPM0_LPD]      [get_bd_intf_pins ai_ic/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins conv_layer_0/m_axi_gmem_in]  [get_bd_intf_pins ai_ic/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins conv_layer_0/m_axi_gmem_out] [get_bd_intf_pins ai_ic/S02_AXI]
connect_bd_intf_net [get_bd_intf_pins conv_layer_0/m_axi_gmem_par] [get_bd_intf_pins ai_ic/S03_AXI]
connect_bd_intf_net [get_bd_intf_pins eltwise_layer_0/m_axi_gmem_a]   [get_bd_intf_pins ai_ic/S04_AXI]
connect_bd_intf_net [get_bd_intf_pins eltwise_layer_0/m_axi_gmem_b]   [get_bd_intf_pins ai_ic/S05_AXI]
connect_bd_intf_net [get_bd_intf_pins eltwise_layer_0/m_axi_gmem_out] [get_bd_intf_pins ai_ic/S06_AXI]

connect_bd_intf_net [get_bd_intf_pins ai_ic/M00_AXI] [get_bd_intf_pins conv_layer_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins ai_ic/M01_AXI] [get_bd_intf_pins eltwise_layer_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins ai_ic/M02_AXI] [get_bd_intf_pins bram_ctrl/S_AXI]

connect_bd_intf_net [get_bd_intf_pins bram_ctrl/BRAM_PORTA] [get_bd_intf_pins bram_gen/BRAM_PORTA]
connect_bd_intf_net [get_bd_intf_pins bram_ctrl/BRAM_PORTB] [get_bd_intf_pins bram_gen/BRAM_PORTB]

# -- 4) 时钟 / 复位 --
set plclk [get_bd_pins ${ps}/pl_clk0]
foreach p [list ai_ic/ACLK ai_ic/S00_ACLK ai_ic/S01_ACLK ai_ic/S02_ACLK ai_ic/S03_ACLK \
                ai_ic/S04_ACLK ai_ic/S05_ACLK ai_ic/S06_ACLK \
                ai_ic/M00_ACLK ai_ic/M01_ACLK ai_ic/M02_ACLK \
                conv_layer_0/ap_clk eltwise_layer_0/ap_clk bram_ctrl/s_axi_aclk \
                ${ps}/maxihpm0_lpd_aclk] {
    if {[llength [get_bd_pins -quiet $p]] > 0} { connect_bd_net $plclk [get_bd_pins $p] } else { puts "WARN clk $p" }
}
# bram_gen 的时钟脚名随配置而变, 逐个试
foreach p {bram_gen/clka bram_gen/clkb} {
    if {[llength [get_bd_pins -quiet $p]] > 0} { connect_bd_net $plclk [get_bd_pins $p] }
}
set plrstn [get_bd_pins rst_pl/peripheral_aresetn]
set icrstn [get_bd_pins rst_pl/interconnect_aresetn]
foreach p [list conv_layer_0/ap_rst_n eltwise_layer_0/ap_rst_n bram_ctrl/s_axi_aresetn] {
    if {[llength [get_bd_pins -quiet $p]] > 0} { connect_bd_net $plrstn [get_bd_pins $p] }
}
foreach p [list ai_ic/ARESETN ai_ic/S00_ARESETN ai_ic/S01_ARESETN ai_ic/S02_ARESETN ai_ic/S03_ARESETN \
                ai_ic/S04_ARESETN ai_ic/S05_ARESETN ai_ic/S06_ARESETN \
                ai_ic/M00_ARESETN ai_ic/M01_ARESETN ai_ic/M02_ARESETN] {
    if {[llength [get_bd_pins -quiet $p]] > 0} { connect_bd_net $icrstn [get_bd_pins $p] }
}

# -- 5) 地址 --
assign_bd_address -offset 0x80000000 -range 64K [get_bd_addr_segs conv_layer_0/s_axi_control/Reg]
assign_bd_address -offset 0x80010000 -range 64K [get_bd_addr_segs eltwise_layer_0/s_axi_control/Reg]
assign_bd_address -offset 0x80020000 -range 128K [get_bd_addr_segs bram_ctrl/S_AXI/Reg]
# 加速器主口只允许 DDR/BRAM, 不许乱指
foreach seg [get_bd_addr_segs -quiet {/conv_layer_0/Data_m_axi_*/SEG_* /eltwise_layer_0/Data_m_axi_*/SEG_*}] {
    catch { exclude_bd_addr_seg $seg }
}
assign_bd_address
foreach seg [get_bd_addr_segs -quiet {/conv_layer_0/Data_m_axi_*/SEG_* /eltwise_layer_0/Data_m_axi_*/SEG_*}] {
    if {![string match *bram* $seg]} { catch { exclude_bd_addr_seg $seg } }
}

if {[catch {validate_bd_design} vm]} { puts "VALIDATE_FAILED: $vm" } else { puts "VALIDATE_OK" }
save_bd_design
puts "AI_BD_WIRED"
