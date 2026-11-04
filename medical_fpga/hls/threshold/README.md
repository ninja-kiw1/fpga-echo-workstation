# 可调阈值二值化 HLS 模块

输入像素大于等于阈值时输出 255，否则输出 0。用于学习及前后处理模块开发；对眼底灰度图直接阈值化不等于完成有效血管分割。

## 参数与接口

- uint8 输入、uint8 输出，逐行连续存储，每像素一个字节。
- 阈值 threshold_value 为 8 位无符号数，合法范围 0～255，等于阈值也输出白色。
- 阈值 0 输出全白；阈值 255 仅把值为 255 的像素置白。
- 宽 1～700，高 1～605；非法尺寸不写输出，ARM 调用方应提前检查。
- 两个 AXI Master 接口访问不重叠的输入输出 DDR 缓冲区；AXI-Lite 配置地址、宽高、阈值和启动控制。
- 每帧开始前设置阈值；模块运行期间不要修改配置。
- 当前与均值滤波模块一样使用内存接口，不是 AXI4-Stream 模块，无需额外串接 AXI DMA。
- 目标器件 xazu3eg-sfvc784-1-i，目标时钟 10 ns。能否满足系统时序须经 Vivado 实现验证。
- 8 位灰度阈值不自动等于模型概率阈值；模型输出格式、量化尺度和零点确定后再适配。

## 文件与操作顺序

1. generate_vectors.py：独立 NumPy 参考，生成固定测试向量。
2. src/threshold.cpp：可综合的二值化函数。
3. tb/test_threshold.cpp：自动对比并检查越界写入。
4. run_hls.tcl：执行 C 仿真、综合、Verilog 协同仿真与 IP 导出。
5. verify_results.py：检查工具证据、输出像素，生成报告和效果图。

在 PowerShell 中执行：

```powershell
Set-Location E:\PLD\medical_fpga\hls\threshold
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' .\generate_vectors.py
& 'D:\xlinx2023.1\Vitis_HLS\2023.1\bin\vitis_hls.bat' -f .\run_hls.tcl
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' .\verify_results.py
```

默认执行全部阶段。单独运行时设置环境变量 `$env:THRESHOLD_STAGE = "csim"`，还可选 synth、cosim、export、all；协同仿真和导出之前须先成功综合。build/completed_all.txt 仅在全部步骤执行完成后生成，HLS 批处理退出码本身不足以判断成功。

## 测试内容

完整 0～255 灰度阶梯分别测试阈值 0、1、127、128、254、255；单像素测试 127/128/129 与阈值相等的边界；全黑、全白；连续帧参数 64→192→64；完整 STARE 绿通道及其 NumPy 均值滤波参考；非法尺寸不写输出。像素必须完全一致。

## 交给 C

ARM 准备输入缓冲区，完成必要的缓存一致性处理，设置地址、尺寸、阈值，启动 IP 并等待完成，随后同步并读取输出。寄存器与驱动以导出 IP 内 drivers 文件为准。

两个独立 IP 后续可通过 DDR 中间缓冲串联：均值滤波完成后，再让二值化读取滤波输出。这里的滤波后测试输入由 Python 独立生成，不能作为两个硬件 IP 已经集成运行的证据。

## 验证边界

验证以 results/verification_results.md 和工具原始报告为准。没有上板或完整系统实现之前，不报告板上帧率。展示图不含模型推理，不属于医学分割效果证明。

## 阅读与集成提示

先看 src/threshold.cpp 中的比较表达式，再看 generate_vectors.py 的 NumPy 参考，最后看 testbench 如何检查“等于阈值”和输出哨兵。

综合中像素循环达到 II=1。模块整体资源仍包含 AXI 控制与存储接口，不能把整个 IP 的资源都归因于一个比较器。此次综合中 width×height 的 32 位乘法占用 3 个 DSP，比较循环本身不使用 DSP；后续如有系统资源压力，可针对尺寸计算另做优化。

工具日志中有内部流水线未用 AXI 字段置零及同步低有效复位提示，需结合顶层接口查看；最终能否在实际 DDR 系统工作，还需 C 完成连接和板上验证。

完成全流程并运行 verify_results.py 后查看：

- [验证结果](results/verification_results.md)
- [二值化效果及误差图](results/threshold_comparison.png)
- [完整核验数据](results/verification.json)
