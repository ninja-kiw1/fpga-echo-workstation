# 3×3 均值滤波 HLS 入门工程

这是独立验证工程，不修改已有 Vivado 系统、不烧写开发板。目标器件按现有 FZ3B 工程设置为 xazu3eg-sfvc784-1-i，目标时钟 10 ns（100 MHz）。这些是设计约束，不是板上实测性能。

## 算法与接口

- 输入、输出均为 uint8 灰度，逐行连续存储，每像素 1 字节。
- 尺寸支持宽 1～700、高 1～605；非法尺寸直接返回、不写输出，调用方须提前检查。
- 3×3 窗口，边缘复制填充，整数和除以 9 后向下取整。
- 和最大为 2295，HLS 使用 12 位无符号累加器。
- 两条行缓存和 3×3 窗口；无需完整图像片上缓存。
- 两个 AXI4 Master 接口读输入和写输出，AXI4-Lite 配置地址、尺寸与控制。
- 输入和输出必须使用不重叠的缓冲区；图像数据应保持有效直到处理完成。
- 当前是入门内存接口版本，不是现成 AXI 视频流模块，不保证达到每拍一个有效输出像素。II、访问效率、资源和时序需要检查工具报告。
- 它不是血管分割模型；滤波是否适合模型输入仍需与 B 共同评估。

## 文件

- src/mean_filter.cpp：可综合算法。
- src/mean_filter.hpp：函数与最大尺寸。
- tb/test_mean_filter.cpp：逐像素自动比较，出错返回非零。
- generate_vectors.py：独立 NumPy 参考与固定测试数据。
- vectors/：输入、期望输出，以及最近一次仿真写出的实际输出。
- run_hls.tcl：C 仿真、综合、RTL 协同仿真、IP 导出。
- build/：HLS 生成工程和报告，可通过脚本重建。

Python 参考明确采用整数向下取整，不把 OpenCV 的默认整数舍入当作逐像素标准。

## 运行

在 PowerShell 中：

```powershell
Set-Location E:\PLD\medical_fpga\hls\mean_filter
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' .\generate_vectors.py
& 'D:\xlinx2023.1\Vitis_HLS\2023.1\bin\vitis_hls.bat' -f .\run_hls.tcl
```

只跑一个阶段时，在 PowerShell 先设置 `$env:MEAN_FILTER_STAGE = "csim"`，可选值为 `csim`、`synth`、`cosim`、`export` 或 `all`，再执行 HLS 命令。全部运行可设置为 `all` 或移除该环境变量。单独协同仿真和导出前需先成功综合。

GUI 可以打开 build/mean_filter 工程；顶层是 mean_filter，testbench 是 test_mean_filter.cpp，C 仿真与协同仿真的参数需设置为 vectors 目录的绝对路径。

测试覆盖全黑、全白、1×1、单行、单列、中间及角落亮点、棋盘格、固定随机图、完整 STARE 图以及真实图后的不同尺寸帧。testbench 检查所有输出像素，并检查有效帧之后的哨兵值，确认没有越界写入。

## 验证边界

C 仿真证明所测输入上的 C++ 行为符合参考；C/RTL 协同仿真进一步核对生成 RTL 的行为。HLS 综合资源和时序属于估计。完整 Vivado 实现、实际 DDR/DMA 系统、ARM 驱动、板上输出和端到端性能仍需与 C 联调验证。

本工程中的 STARE 图像仅用于计算正确性测试，不代表已完成医学分析或验证滤波能改善分割。

## 本机已完成的验证

2026-09-22，Vitis HLS 2023.1：C Simulation PASS、C Synthesis 成功、Verilog C/RTL Co-Simulation PASS、IP 导出成功。共 12 组测试、425,713 个输出像素，参考与实际输出逐像素完全一致。

- [验证结果](results/verification_results.md)
- [滤波与误差对比图](results/filter_comparison.png)
- [导出的 Vivado IP](build/mean_filter/solution1/impl/ip/xilinx_com_hls_mean_filter_1_0.zip)

像素循环 II=1，HLS 估计资源为 LUT 4072、FF 3790、BRAM_18K 6、DSP 1。100 MHz 是目标时钟；RTL 协同仿真最长一次调用为 453,997 个周期（仿真配置下约 4.54 ms），不是开发板的端到端耗时。

重新核对保存的测试输出并生成结果文档与图像：

```powershell
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' .\verify_results.py
```

建议阅读顺序：先看 generate_vectors.py 的 reference 函数，再看 src/mean_filter.cpp，最后看 tb/test_mean_filter.cpp 如何比较像素和返回失败。源码改变后必须重新运行验证流程，不能沿用旧报告。

## 交给 C 集成时的说明

本 IP 自带 AXI Master 读写 DDR，不要求在其前后额外串接 AXI DMA。C 需要连接 AXI 互连与 PS 的存储访问端口，配置 AXI-Lite 控制地址和时钟复位，并提供两个不重叠的 DDR 缓冲区。若完整系统统一使用 DMA 加 AXI4-Stream，需另做流接口版本，不能直接把本 IP 当作 AXI 视频流模块。

ARM 调用顺序：准备输入缓冲区 → 按实际系统完成缓存一致性处理 → 设置输入/输出地址、宽高 → 启动 → 等待完成 → 完成输出缓存同步 → 读取结果。寄存器定义与驱动在导出 IP 的 drivers 目录中，以自动生成文件为准。
