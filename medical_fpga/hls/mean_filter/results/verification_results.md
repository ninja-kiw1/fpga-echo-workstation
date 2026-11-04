# 均值滤波 HLS 验证结果

已通过 C 仿真、HLS 综合和 Verilog RTL 协同仿真，并导出 IP。
尚未进行完整 Vivado 系统实现或开发板测试。

共 12 组测试、425,713 个输出像素，逐像素误差均为 0。
参考定义：8 位灰度、边缘复制、3×3 求和后整数除以 9 向下取整。

| 测试 | 宽×高 | 不同像素数 | 最大误差 |
|---|---|---|---|
| black | 9×8 | 0 | 0 |
| white | 9×8 | 0 | 0 |
| single_pixel | 1×1 | 0 | 0 |
| single_row | 9×1 | 0 | 0 |
| single_column | 1×9 | 0 | 0 |
| impulse_center | 9×8 | 0 | 0 |
| impulse_corner | 9×8 | 0 | 0 |
| impulse_bottom_right | 9×8 | 0 | 0 |
| checkerboard | 17×13 | 0 | 0 |
| random | 48×32 | 0 | 0 |
| stare_im0001_green | 700×605 | 0 | 0 |
| after_real_frame | 7×11 | 0 | 0 |

## HLS 估计结果

- 目标器件：xazu3eg-sfvc784-1-i。
- 目标时钟周期：10.00 ns；HLS 估计周期：7.300 ns。
- 资源估计：BRAM_18K=6，DSP=1，FF=3790，LUT=4072，URAM=0。
- 像素循环 II=1；整帧仍有行切换、填充与 AXI 访问开销，不能据此声称板上帧率。
- HLS 报告存在行循环不能展平、行缓存依赖声明及 AXI 同步低有效复位提示；当前未启用 rewind 优化。边界及连续多帧测试通过，实际系统仍需检查接口和时序。

## 查看证据

- C 仿真：../build/mean_filter/solution1/csim/report/mean_filter_csim.log
- 综合：../build/mean_filter/solution1/syn/report/mean_filter_csynth.rpt
- RTL 协同仿真：../build/mean_filter/solution1/sim/report/mean_filter_cosim.rpt
- IP：../build/mean_filter/solution1/impl/ip/xilinx_com_hls_mean_filter_1_0.zip
- 详细数据和源码哈希：verification.json

滤波效果图只展示数值运算，不证明它可以提高血管分割精度。
