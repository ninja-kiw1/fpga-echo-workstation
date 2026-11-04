# 可调阈值二值化验证结果

C 仿真通过、HLS 综合成功、Verilog RTL 协同仿真通过、Vivado IP 导出成功。
共 20 组测试、849,994 个有效输出像素，逐像素差异为 0。
包含 4 组非法尺寸测试，由 testbench 核对整个输出缓冲区仍为哨兵值。

| 测试 | 宽×高 | 阈值 | 输出像素数 | 差异数 |
|---|---|---|---|---|
| ramp_t0 | 16×16 | 0 | 256 | 0 |
| ramp_t1 | 16×16 | 1 | 256 | 0 |
| ramp_t127 | 16×16 | 127 | 256 | 0 |
| ramp_t128 | 16×16 | 128 | 256 | 0 |
| ramp_t254 | 16×16 | 254 | 256 | 0 |
| ramp_t255 | 16×16 | 255 | 256 | 0 |
| pixel_127_t128 | 1×1 | 128 | 1 | 0 |
| pixel_128_t128 | 1×1 | 128 | 1 | 0 |
| pixel_129_t128 | 1×1 | 128 | 1 | 0 |
| black_t1 | 8×9 | 1 | 72 | 0 |
| white_t255 | 8×9 | 255 | 72 | 0 |
| consecutive_t64 | 23×19 | 64 | 437 | 0 |
| consecutive_t192 | 23×19 | 192 | 437 | 0 |
| consecutive_t64_again | 23×19 | 64 | 437 | 0 |
| stare_green_t128 | 700×605 | 128 | 423500 | 0 |
| stare_mean_t128 | 700×605 | 128 | 423500 | 0 |
| zero_width | 0×8 | 128 | 0 | 0 |
| negative_height | 8×-1 | 128 | 0 | 0 |
| too_wide | 701×8 | 128 | 0 | 0 |
| too_tall | 8×606 | 128 | 0 | 0 |

## HLS 估计

- 器件：xazu3eg-sfvc784-1-i。
- 目标时钟：10.00 ns；估计时钟：7.300 ns。
- 资源：BRAM_18K=4，DSP=3，FF=2269，LUT=2699，URAM=0。
- 详细循环 II、AXI 接口等见综合报告；估计不是实现后或开发板实测值。

## 原始证据

- [C 仿真日志](../build/threshold/solution1/csim/report/threshold_binary_csim.log)
- [综合报告](../build/threshold/solution1/syn/report/threshold_binary_csynth.rpt)
- [RTL 协同仿真报告](../build/threshold/solution1/sim/report/threshold_binary_cosim.rpt)
- [导出 IP](../build/threshold/solution1/impl/ip/xilinx_com_hls_threshold_binary_1_0.zip)

## 验证范围

规则为输入大于等于阈值输出 255，否则输出 0。等于阈值、阈值极值、连续调用修改阈值以及非法尺寸均有覆盖。
滤波后输入来自独立 NumPy 参考，不表示两个硬件 IP 已完成联合验证。未上板。
眼底图简单阈值化只是模块演示，不是模型推理或有效血管分割证明。
