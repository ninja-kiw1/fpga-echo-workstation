# 二值形态学验证结果

已完成 C 仿真、HLS 综合、Verilog RTL 协同仿真及 IP 导出。
共 46 组测试、1,704,319 个输出像素，全部与 NumPy 参考一致。
NumPy 参考逐例通过 OpenCV 交叉核对；两者均显式采用边缘复制填充。
5 组非法尺寸或非法模式测试通过，testbench 确认整个输出缓冲区未被写入。

| 测试 | 宽×高 | 模式 | 输出像素数 | 差异数 |
|---|---|---|---|---|
| shapes_erode | 37×31 | 腐蚀 | 1147 | 0 |
| shapes_dilate | 37×31 | 膨胀 | 1147 | 0 |
| shapes_open | 37×31 | 开运算 | 1147 | 0 |
| shapes_close | 37×31 | 闭运算 | 1147 | 0 |
| edges_erode | 11×9 | 腐蚀 | 99 | 0 |
| edges_dilate | 11×9 | 膨胀 | 99 | 0 |
| edges_open | 11×9 | 开运算 | 99 | 0 |
| edges_close | 11×9 | 闭运算 | 99 | 0 |
| black_erode | 9×8 | 腐蚀 | 72 | 0 |
| black_dilate | 9×8 | 膨胀 | 72 | 0 |
| black_open | 9×8 | 开运算 | 72 | 0 |
| black_close | 9×8 | 闭运算 | 72 | 0 |
| white_erode | 9×8 | 腐蚀 | 72 | 0 |
| white_dilate | 9×8 | 膨胀 | 72 | 0 |
| white_open | 9×8 | 开运算 | 72 | 0 |
| white_close | 9×8 | 闭运算 | 72 | 0 |
| one_pixel_erode | 1×1 | 腐蚀 | 1 | 0 |
| one_pixel_dilate | 1×1 | 膨胀 | 1 | 0 |
| one_pixel_open | 1×1 | 开运算 | 1 | 0 |
| one_pixel_close | 1×1 | 闭运算 | 1 | 0 |
| single_row_erode | 7×1 | 腐蚀 | 7 | 0 |
| single_row_dilate | 7×1 | 膨胀 | 7 | 0 |
| single_row_open | 7×1 | 开运算 | 7 | 0 |
| single_row_close | 7×1 | 闭运算 | 7 | 0 |
| single_column_erode | 1×7 | 腐蚀 | 7 | 0 |
| single_column_dilate | 1×7 | 膨胀 | 7 | 0 |
| single_column_open | 1×7 | 开运算 | 7 | 0 |
| single_column_close | 1×7 | 闭运算 | 7 | 0 |
| nonbinary_erode | 17×13 | 腐蚀 | 221 | 0 |
| nonbinary_dilate | 17×13 | 膨胀 | 221 | 0 |
| nonbinary_open | 17×13 | 开运算 | 221 | 0 |
| nonbinary_close | 17×13 | 闭运算 | 221 | 0 |
| random_erode | 29×23 | 腐蚀 | 667 | 0 |
| random_dilate | 29×23 | 膨胀 | 667 | 0 |
| random_open | 29×23 | 开运算 | 667 | 0 |
| random_close | 29×23 | 闭运算 | 667 | 0 |
| stare_manual_ah_erode | 700×605 | 腐蚀 | 423500 | 0 |
| stare_manual_ah_dilate | 700×605 | 膨胀 | 423500 | 0 |
| stare_manual_ah_open | 700×605 | 开运算 | 423500 | 0 |
| stare_manual_ah_close | 700×605 | 闭运算 | 423500 | 0 |
| after_real_frame_open | 37×31 | 开运算 | 1147 | 0 |
| zero_width | 0×8 | 腐蚀 | 0 | 0 |
| negative_height | 8×-1 | 膨胀 | 0 | 0 |
| too_wide | 701×8 | 开运算 | 0 | 0 |
| too_tall | 8×606 | 闭运算 | 0 | 0 |
| bad_mode | 8×8 | 非法模式 | 0 | 0 |

## HLS 估计

- 器件：xazu3eg-sfvc784-1-i。
- 目标周期 10.00 ns，估计周期 7.300 ns。
- 资源：BRAM_18K=8，DSP=6，FF=4401，LUT=6621，URAM=0。
- 单步形态学与两步开闭运算共享同一顶层接口；内部为两个独立流处理阶段。
- II、FIFO 和时序估计见原始报告；不是板端帧率或端到端性能。

## 原始证据

- [C 仿真](../build/morphology/solution1/csim/report/morphology_binary_csim.log)
- [综合报告](../build/morphology/solution1/syn/report/morphology_binary_csynth.rpt)
- [RTL 协同仿真](../build/morphology/solution1/sim/report/morphology_binary_cosim.rpt)
- [导出 IP](../build/morphology/solution1/impl/ip/xilinx_com_hls_morphology_binary_1_0.zip)
- [运算效果对比](morphology_comparison.png)

## 结果解释

图中真实眼底掩膜来自 AH 人工标注，不是模型输出。腐蚀和开运算会显著删除细血管，闭运算也可能连接邻近区域，因此通过模块正确性测试并不意味着分割效果得到改善。
尚未完成上板或均值滤波、阈值化、形态学三个模块的系统集成。

## 控制路径修正

将帧长度和模式判断移到 DATAFLOW 外部后，HLS 估计周期从 11.180 ns 降为 7.300 ns。
最终版本重新执行全部 C/RTL 测试。100 MHz 仍是设计目标，需由实际 Vivado 实现和板上测试确认。
