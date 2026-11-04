# 二值形态学 HLS 模块

一个 IP 支持 3×3 方形结构元素的腐蚀、膨胀、开运算和闭运算。输入用于验证形态学计算，不代表模型预测或分割改进。

## 规则与接口

| mode | 运算 | 定义 |
|---|---|---|
| 0 | 腐蚀 | 3×3 邻域全部是前景，输出才为前景 |
| 1 | 膨胀 | 3×3 邻域任意像素是前景，输出为前景 |
| 2 | 开运算 | 先腐蚀，再膨胀 |
| 3 | 闭运算 | 先膨胀，再腐蚀 |

- 输入每像素 8 位，0 表示背景，任意非零值表示前景；输出严格为 0 或 255。
- 输入应是阈值后的掩膜；不能直接把灰度图或概率图送入本模块，否则几乎所有非零像素都会成为前景。
- 每一步独立使用边缘复制填充；不采用 OpenCV 默认的形态学边界设置。
- 宽 1～700，高 1～605，支持 1×1、单行、单列；非法尺寸或 mode>3 时不写输出。
- 输入输出为不重叠的连续 DDR 缓冲区，按行存储、每像素 1 字节。
- 两个 AXI Master 访问 DDR，AXI-Lite 配置地址、宽、高、mode 和启动控制。
- 每帧开始前设置模式，运行期间不改变配置。目标器件 xazu3eg-sfvc784-1-i，目标时钟 10 ns。
- 顶层使用内存接口，不是 AXI4-Stream 视频接口，不要求额外串接 DMA。

## 硬件结构

DDR 读取 → 第一阶段形态学 → 第二阶段形态学或直通 → DDR 写入。

两个阶段通过深度 64 的 FIFO 连接，使用 DATAFLOW 并行执行。每个阶段采用两条行缓存和 3×3 窗口；开闭运算的中间结果在片上流动，无需中间帧写回 DDR。二值行缓存每像素使用 1 位。

第一阶段在腐蚀/开运算时做腐蚀，在膨胀/闭运算时做膨胀。第二阶段在开运算时做膨胀，在闭运算时做腐蚀，其他模式直通。单帧内部两个阶段需要处理相同数量的有效像素。

## 运行

```powershell
Set-Location E:\PLD\medical_fpga\hls\morphology
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' .\generate_vectors.py
& 'D:\xlinx2023.1\Vitis_HLS\2023.1\bin\vitis_hls.bat' -f .\run_hls.tcl
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' .\verify_results.py
```

默认运行 C 仿真、综合、Verilog 协同仿真及 IP 导出。通过环境变量 MORPHOLOGY_STAGE 可单独选择 csim、synth、cosim、export、all。单独协同仿真或导出前必须已成功综合。

GUI 工程在 build/morphology，顶层 morphology_binary，testbench 参数是 vectors 目录绝对路径。不能仅凭 HLS 批处理退出码判断成功，需检查完成标记和原始报告。

## 验证

NumPy 通过独立邻域归约生成期望输出，逐例同显式边界设置的 OpenCV 交叉核对。HLS testbench 自动比较每个像素，检查有效帧后的哨兵值；非法参数时整个输出缓冲区应保持不变。测试覆盖合成噪点、孔洞、细线、边缘区域、全黑/全白、1×1、单行/单列、非二值非零输入、随机图、完整 STARE 人工掩膜和模式切换后的不同尺寸帧。

STARE 使用人工 AH 标注作为输入测试，不是在运行模型。开运算可能删除细血管，闭运算可能连接本应分离的区域；是否用于最终分割后处理，须用实际模型输出评估。

## 交付与边界

源码在 src，自动测试在 tb，期望与实际输出在 vectors。核验后查看 results/verification_results.md、results/verification.json 和 results/morphology_comparison.png。导出的 IP 在 build/morphology/solution1/impl/ip。

C 负责连接 DDR 和 AXI-Lite、ARM 调用及缓存一致性；A 提供规则和测试向量。三个独立模块可由系统后续串联，但单模块仿真不代表均值滤波、阈值化、形态学三者已完成系统集成。上板正确性和端到端性能需要另行测量。

## 如何读懂仿真与效果

C 仿真中的 hls::stream 由软件顺序执行，可能记录到一整帧的最大队列长度；该数值不是实际综合后的 FIFO 深度。硬件 FIFO 配置为 64，容量是否足够需要 RTL 协同仿真检查有界缓冲、背压和死锁。

先看合成图：开运算会清理孤立亮点，也可能删除单像素细线；闭运算可能填补小孔洞和窄间断。再看真实人工掩膜，观察细血管受到的影响。不能把四种形态学操作当作一定能改善分割的默认处理链，应由 A 与 B 根据模型输出选择。
