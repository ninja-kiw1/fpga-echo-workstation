# =============================================================================
# create_image_proc_bd.tcl
#
# 在**已有的、PS 已配置好的** block design 里加入图像处理通路:
#
#   PS(M_AXI_HPM0_LPD) ─► SmartConnect ─┬─► AXI DMA  (S_AXI_LITE, 控制)
#                                       └─► AXI GPIO (S_AXI, 模式寄存器)
#
#   AXI DMA M_AXI_MM2S ─► PS S_AXI_HPC0_FPD          (DMA 读 DDR 里的输入图)
#   AXI DMA M_AXIS_MM2S ─► img_proc_axis s_axis      (流进处理流水线)
#   img_proc_axis m_axis ─► AXI DMA S_AXIS_S2MM      (处理完流回 DMA)
#   AXI DMA M_AXI_S2MM ─► PS S_AXI_HPC0_FPD          (DMA 写 DDR 里的输出图)
#
#   AXI GPIO ch1(输出 16bit) ─► img_proc_axis.ctrl   ([2:0]=模式 [15:8]=阈值)
#   AXI GPIO ch2(输入  16bit) ◄─ {stat_max, stat_min} (PS 读回, 确认流水线真跑过)
#
# 前置条件(本脚本**不碰**这些, 必须你手工先做好):
#   * 已有的 block design(默认名字 system)里放着 zynq_ultra_ps_e, 且:
#       - 使能了 PL 时钟 pl_clk0 (FCLK0) 与 PL 复位 pl_resetn0
#       - 使能了至少一个 PS→PL 主口 M_AXI_HPM0_LPD
#       - 使能了至少一个 PL→PS 从口 S_AXI_HPC0_FPD / HP0_FPD
#     (这三个口用板上 FZ3B 的 PS 预设配出来, 本脚本不会去臆造 PS 参数)
#   * rtl/ 下的 5 个 .v 已经加进工程:
#       add_files -fileset sources_1 {
#           rtl/recip_lut.v rtl/pcolor_lut.v rtl/gray_frame_store.v
#           rtl/mode_map.v   rtl/img_proc_axis.v }
#
# 用法(Tcl Console, 工程已打开):
#   source <repo>/bd/create_image_proc_bd.tcl
#
# 注释一律 ASCII —— Vivado 在中文(cp936) Windows 下可能误读 Tcl 里的非 ASCII 字节。
# =============================================================================

# ------------------------------ 参数 ----------------------------------------
set bd_name      "system"                 ;# 已有的 block design 名
set ps_name      "zynq_ultra_ps_e_0"
set clk_pin      "pl_clk0"
set rst_pin      "pl_resetn0"
set dma_name     "axi_dma_img"
set sc_name      "sc_ctrl"
set rst_name     "rst_pl"
set gpio_name    "gpio_ctrl"
set proc_name    "img_proc_axis_0"
set proc_module  "img_proc_axis"
set ctrl_bits    16                       ;# 模式寄存器位宽: [2:0]模式 [15:8]阈值

proc die {msg} { puts "ERROR: $msg" ; error $msg }

# ------------------------------ 前置检查 -------------------------------------
if {[llength [current_project -quiet]] == 0} { die "No project is open." }

set bd_files [get_files -quiet "*${bd_name}.bd"]
if {[llength $bd_files] == 0} { die "Block design '${bd_name}.bd' not found." }
open_bd_design $bd_files

if {[llength [get_bd_cells -quiet $ps_name]] == 0} {
    die "PS cell '$ps_name' not found. Adjust ps_name at the top."
}

# 处理模块的 RTL 必须在工程里, 否则 create_bd_cell -reference 会失败
if {[llength [get_files -quiet "*${proc_module}.v"]] == 0} {
    die "RTL '${proc_module}.v' not in project. Add rtl/*.v to sources_1 first."
}

# PS 时钟 / 复位
if {[llength [get_bd_pins -quiet ${ps_name}/${clk_pin}]] == 0} {
    die "PS clock '${ps_name}/${clk_pin}' not found (enable FCLK0)."
}
if {[llength [get_bd_pins -quiet ${ps_name}/${rst_pin}]] == 0} {
    die "PS reset '${ps_name}/${rst_pin}' not found (enable the PL reset)."
}

# PS 的 PL 从口(DMA 经它读写 DDR)与主口(PS 配寄存器)
set ps_slave ""
foreach p [get_bd_intf_pins -quiet -of_objects [get_bd_cells $ps_name]] {
    if {[string match "S_AXI_HP*" [get_property NAME $p]]} { set ps_slave $p ; break }
}
if {$ps_slave eq ""} { die "PS has no enabled S_AXI_HP*/HPC* slave port." }
puts "INFO: DMA will read/write DDR via [get_property NAME $ps_slave]"

set ps_master ""
foreach p [get_bd_intf_pins -quiet -of_objects [get_bd_cells $ps_name]] {
    if {[string match "M_AXI_HPM*" [get_property NAME $p]]} { set ps_master $p ; break }
}
if {$ps_master eq ""} { die "PS has no enabled M_AXI_HPM* master port." }
puts "INFO: PS will program registers via [get_property NAME $ps_master]"

# ------------------------------ 建 IP ----------------------------------------
# AXI DMA: MM2S + S2MM 双向, simple 模式(不用 SG)
if {[llength [get_bd_cells -quiet $dma_name]] == 0} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 $dma_name
}
set_property -dict [list \
    CONFIG.c_include_sg               {0} \
    CONFIG.c_sg_include_stscntrl_strm {0} \
    CONFIG.c_include_mm2s             {1} \
    CONFIG.c_include_s2mm             {1} \
    CONFIG.c_include_mm2s_dre         {0} \
    CONFIG.c_include_s2mm_dre         {0} \
    CONFIG.c_micro_dma                {0} \
    CONFIG.c_addr_width               {32} \
    CONFIG.c_m_axi_mm2s_data_width    {64} \
    CONFIG.c_m_axi_s2mm_data_width    {64} \
    CONFIG.c_m_axis_mm2s_tdata_width  {32} \
    CONFIG.c_s_axis_s2mm_tdata_width  {32} \
] [get_bd_cells $dma_name]
puts "INFO: $dma_name = MM2S + S2MM, simple mode, 32-bit streams, 64-bit AXI."

# 图像处理流水线(模块引用, 不做 IP 打包)
if {[llength [get_bd_cells -quiet $proc_name]] == 0} {
    create_bd_cell -type module -reference $proc_module $proc_name
}
puts "INFO: instantiated module $proc_module as $proc_name"

# AXI GPIO: ch1 输出模式寄存器, ch2 输入读回帧统计
if {[llength [get_bd_cells -quiet $gpio_name]] == 0} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 $gpio_name
}
set_property -dict [list \
    CONFIG.C_IS_DUAL          {1} \
    CONFIG.C_GPIO_WIDTH       $ctrl_bits \
    CONFIG.C_GPIO2_WIDTH      {16} \
    CONFIG.C_ALL_OUTPUTS      {1} \
    CONFIG.C_ALL_INPUTS       {0} \
    CONFIG.C_ALL_OUTPUTS_2    {0} \
    CONFIG.C_ALL_INPUTS_2     {1} \
] [get_bd_cells $gpio_name]
puts "INFO: $gpio_name = ch1 输出 $ctrl_bits bit(模式寄存器), ch2 输入 16 bit(帧统计)"

if {[llength [get_bd_cells -quiet $sc_name]] == 0} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 $sc_name
}
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] [get_bd_cells $sc_name]

if {[llength [get_bd_cells -quiet $rst_name]] == 0} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 $rst_name
}

# ------------------------------ 连控制通路 ------------------------------------
connect_bd_intf_net $ps_master [get_bd_intf_pins ${sc_name}/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ${sc_name}/M00_AXI] [get_bd_intf_pins ${dma_name}/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins ${sc_name}/M01_AXI] [get_bd_intf_pins ${gpio_name}/S_AXI]

# ------------------------------ 连数据通路 ------------------------------------
# DMA 的 MM2S 从 DDR 读, S2MM 写回 DDR —— 都走同一个 PS 从口
connect_bd_intf_net [get_bd_intf_pins ${dma_name}/M_AXI_MM2S] $ps_slave
connect_bd_intf_net [get_bd_intf_pins ${dma_name}/M_AXI_S2MM] $ps_slave

# 处理流水线串在 MM2S 和 S2MM 之间
connect_bd_intf_net [get_bd_intf_pins ${dma_name}/M_AXIS_MM2S] \
                    [get_bd_intf_pins ${proc_name}/s_axis]
connect_bd_intf_net [get_bd_intf_pins ${proc_name}/m_axis] \
                    [get_bd_intf_pins ${dma_name}/S_AXIS_S2MM]

# 模式寄存器: GPIO 输出 -> 处理 IP 的 ctrl
connect_bd_net [get_bd_pins ${gpio_name}/gpio_io_o] [get_bd_pins ${proc_name}/ctrl]

# 帧统计读回: {stat_max, stat_min} -> GPIO 输入
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat:2.1 concat_stat
set_property -dict [list CONFIG.NUM_PORTS {2}] [get_bd_cells concat_stat]
connect_bd_net [get_bd_pins ${proc_name}/stat_max] [get_bd_pins concat_stat/In0]
connect_bd_net [get_bd_pins ${proc_name}/stat_min] [get_bd_pins concat_stat/In1]
connect_bd_net [get_bd_pins concat_stat/dout] [get_bd_pins ${gpio_name}/gpio_io_i]

# ------------------------------ 连时钟与复位 ----------------------------------
# 所有 PL 侧 IP 共用 PS 的 pl_clk0, 复位用 proc_sys_reset 从 pl_resetn0 同步出来
set pl_clk   [get_bd_pins ${ps_name}/${clk_pin}]
set clk_sinks [list \
    [get_bd_pins ${sc_name}/aclk] \
    [get_bd_pins ${dma_name}/s_axi_lite_aclk] \
    [get_bd_pins ${dma_name}/m_axi_mm2s_aclk] \
    [get_bd_pins ${dma_name}/m_axi_s2mm_aclk] \
    [get_bd_pins ${gpio_name}/s_axi_aclk] \
    [get_bd_pins ${proc_name}/aclk] \
    [get_bd_pins ${rst_name}/slowest_sync_clk] \
]
foreach p $clk_sinks { connect_bd_net $pl_clk $p }

connect_bd_net [get_bd_pins ${ps_name}/${rst_pin}] [get_bd_pins ${rst_name}/ext_reset_in]

set rstn [get_bd_pins ${rst_name}/peripheral_aresetn]
set rst_sinks [list \
    [get_bd_pins ${dma_name}/axi_resetn] \
    [get_bd_pins ${sc_name}/aresetn] \
    [get_bd_pins ${gpio_name}/s_axi_aresetn] \
    [get_bd_pins ${proc_name}/aresetn] \
]
foreach p $rst_sinks { connect_bd_net $rstn $p }

# NOTE: DMA 的两个中断输出(mm2s_introut / s2mm_introut)故意不接 —— Linux 侧用轮询,
#       不接中断可以让设计保持简单。

# ------------------------------ 地址与收尾 ------------------------------------
assign_bd_address -quiet

puts "INFO: ---- assigned addresses ----"
foreach c [list $dma_name $gpio_name] {
    foreach seg [get_bd_addr_segs -quiet -of_objects [get_bd_cells $c]] {
        set off [get_property -quiet OFFSET $seg]
        if {$off ne ""} { puts "  [get_property NAME $seg]  offset = $off" }
    }
}
puts "  (把 DMA 的基址传给 board 程序的 --dma-addr; GPIO 的给 --gpio-addr)"

validate_bd_design
save_bd_design
puts "INFO: block design '$bd_name' updated and saved."
puts "NEXT: 1) 生成 wrapper/bitstream  2) 导出 XSA  3) 用 XSA 重建 PetaLinux 的 dts"
