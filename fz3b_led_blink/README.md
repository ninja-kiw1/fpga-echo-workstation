# FZ3B LED 闪烁入门例程

使用 FZ3B 板载 25 MHz PL 时钟，让 D5 绿色用户灯每 0.5 秒翻转一次，完整周期为 1 秒。
纯 Verilog，不依赖 ARM、DDR、Vitis、PYNQ 或社区 Board Files。

## 已完成验证

2026-09-11 使用本机 Vivado 2023.1：行为仿真 PASS；综合、布局布线及 bitstream 生成通过；DRC 违规数为 0；时序 WNS 38.512 ns、WHS 0.066 ns，均满足约束。LED 无外部同步接收端，报告中的单个 no_output_delay 对应此指示灯输出。
尚未连接实际板卡验证。

可直接在 Vivado 中打开 `vivado_project/fz3b_led_blink.xpr`。
已生成的下载文件：`vivado_project/fz3b_led_blink.runs/impl_1/led_blink.bit`。

## 在当前 Vivado 工程使用

1. Add Sources → Add or create design sources：添加 `rtl/led_blink.v`。
2. Add Sources → Add or create constraints：添加 `constraints/fz3b.xdc`。
3. 确保器件为 `xazu3eg-sfvc784-1-i`，顶层为 `led_blink`。
4. 如需仿真，Add Sources → Add or create simulation sources，添加 `sim/tb_led_blink.v`，仿真顶层设为 `tb_led_blink`。
5. Run Simulation → Run Behavioral Simulation，运行 2 us，应看到 PASS。仿真缩短计数周期以便快速检查；综合使用默认的 12,500,000 周期。
6. Run Synthesis → Run Implementation → Generate Bitstream。

## 单独创建工程

关闭当前工程后，在 Vivado Tcl Console 执行：

```tcl
source E:/PLD/fz3b_led_blink/create_project.tcl
```

脚本在 `vivado_project` 内创建工程，不自动烧录。已有该目录时拒绝覆盖，直接打开其中 `.xpr` 文件即可。

## 引脚依据

| 信号 | 管脚 | 电平 | 来源 |
|---|---|---|---|
| PL_REF_CLK | D4 | LVCMOS18，25 MHz | 官方硬件手册第十九节 |
| PL_LED | A10 | LVCMOS33 | 官方硬件手册第十八节（管脚），社区 part0_pins.xml（电平） |

官方手册：https://ai.baidu.com/ai-doc/HWCE/nkq9b5ncu
社区文件：../edgeboard/board_files/fz3b/a.0/part0_pins.xml

寄存器初值用于 FPGA 配置后的初始化；此例没有外部复位。LED 输出是非同步指示信号，XDC 对它设置 false path。

## 板上运行

需要匹配 FZ3B JTAG 接口的下载器；板上的 USB 串口不能当作 JTAG 下载器使用。
在 Hardware Manager 连接设备后加载生成的 `.bit`。该步骤会替换当前 PL 逻辑，应先停止使用原有 FPGA 加速功能的程序。
本例不包含 Flash 烧写或上电启动镜像制作。
