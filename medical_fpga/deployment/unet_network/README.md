# 混合位宽 U-Net 整网 HLS C 仿真

把 [unet_conv](../unet_conv/README.md) 和 [unet_eltwise](../unet_eltwise/README.md) 两个 HLS 算子，按 ARM 板端程序将来的调用方式逐层执行 30 个节点，并对全部 20 张 STARE 图做逐值验证。

这里只做 C 仿真，没有综合；两个算子各自在自己的工程里完成了综合、RTL 协同仿真和 IP 导出。本目录的结果不是 RTL 仿真结果，也不是板上结果。

## 层表 `vectors/network.txt`

每行一个节点，按执行顺序排列：

```
name kind in0 in1 mode ca cb h w relu shift qmin qmax m0 m1
```

- kind：`INPUT`、`CONV`（调用 conv_layer）或 `ELT`（调用 eltwise_layer）；in1 为 `-` 表示没有第二个输入。
- CONV：mode 0 为 3×3，1 为 1×1，2 为 2×2 转置卷积；ca=cin，cb=cout；参数在 `vectors/params/<name>.bin`，打包格式与 unet_conv 相同。
- ELT：mode 0 为 Add，1 为 Concat，2 为 MaxPool；m0、m1 是两路乘数。
- h、w 是该节点的输入尺寸。qmin/qmax、shift 原样写入对应寄存器。

这张表加上 params 目录，就是 C 编写板端调度程序所需的全部模型信息：按行分配 DDR 缓冲区，依次写寄存器、启动、等待完成。板端程序可以在最后一个消费者使用完某个张量后回收它的缓冲区。

## 文件

- `export_network.py`：用 lwnet 的 `.venv` 运行，从 `lwnet/results/quant_sensitivity` 导出层表、参数和 20 张图的输入与期望输出。输入缩放直接调用 lwnet 的 `read_input`，并检查 im0002 与 golden `x.npy` 一致。im0002 额外导出全部 30 个节点的 golden 张量。
- `tb/test_network.cpp`：读取层表，检查节点顺序和张量形状，逐层调用两个算子。每张图比较最终输出的所有 lane（包括补齐通道）；有 golden 的图逐个比较全部节点。最终输出写到 `images/<img>/final_actual.bin`（int8，512×512）。
- `run_csim.tcl`：把两个算子源码和 testbench 放进一个 Vitis HLS 工程执行 C 仿真。`$env:NETWORK_LIST` 选择图像列表，默认是 `images.txt`（全部 20 张）；`images_golden.txt` 只含 im0002。
- `verify_network.py`：独立读取 `final_actual.bin`，与 lwnet 的 `output_quantized.npy` 和 `mask.png` 比较，用 `final >= -3` 生成掩膜，再与 AH 标注计算 Dice，并核对 TP/FP/FN 与 lwnet 记录完全一致。输出 `results/report.md`、`verification.json`、`comparison.png`。

## 运行

```powershell
Set-Location E:\PLD\medical_fpga\deployment\unet_network
& 'E:\PLD\lwnet\.venv\Scripts\python.exe' .\export_network.py
& 'D:\xlinx2023.1\Vitis_HLS\2023.1\bin\vitis_hls.bat' -f .\run_csim.tcl
& 'E:\PLD\lwnet\.venv\Scripts\python.exe' .\verify_network.py
```

C 仿真单进程每张图约 4 分钟，20 张约 80 分钟。编译一次后，也可以直接运行 `build/unet_network/solution1/csim/build/csim.exe <vectors 绝对路径> <列表文件名>`，把图像拆成几份列表并行运行。本机 4 个进程并行时每张约 11 分钟，20 张合计约 45 分钟（日志为 `csim_part*.log`）。

## 验证结果

2026-09-23，Vitis HLS 2023.1 C 仿真：

- **全部 20 张图 PASS**：最终输出 512×512（含 7 个补齐通道）与 lwnet `output_quantized.npy` 逐值一致。
- **im0002 逐节点 PASS**：全部 30 个节点（包括两个 INT16 节点、两次 Concat、两次转置卷积）与 golden 张量逐值一致。
- `verify_network.py` 独立核验：20/20 张图的输出、`>= -3` 掩膜与 lwnet `mask.png` 完全一致，TP/FP/FN 与 lwnet 记录相同。

| 子集 | 张数 | HLS 整数 Dice | FP32 Dice |
|---|---:|---:|---:|
| calibration | 8 | 0.779411 | 0.779824 |
| holdout | 12 | 0.750030 | 0.752385 |
| 全部 | 20 | 0.761782 | 0.763361 |

也就是说，这两个 HLS 算子忠实复现了 `mixed_precision_spec.md` 的混合位宽模型，精度数字和 lwnet 报告完全相同。逐图结果见 [results/report.md](results/report.md)，对比图见 [results/comparison.png](results/comparison.png)。

全部 STARE 图都参与过 lwnet 的模型选择，这些 Dice 属于开发验证，不是独立测试集结论，也不是医学有效性证明。

## 性能预算（周期模型，非实测）

| 部分 | 周期 | 100 MHz 下 |
|---|---:|---:|
| 20 个卷积层 | 41,161,736 | 412 ms |
| 9 个 Add/Concat/MaxPool | 2,916,352 | 29 ms |
| 合计 | 44,078,088 | 约 441 ms/帧 |

模型只统计循环迭代次数。卷积层在 RTL 协同仿真中比模型多约 1%，Concat 比模型少约 25%。以上都没有考虑真实 DDR 延迟、多个 AXI 主设备争用和 ARM 调度开销，最终帧率要以上板测量为准。

## 下一步

1. **C：Vivado 系统**。Zynq PS 加两个 HLS IP，经 HP 端口访问 DDR。板端程序按 `network.txt` 逐层调用，先用 im0002 做逐节点比较，再测端到端时间。
2. **A：前后处理接入**。模型输入是 512×512 RGB（int16 容器）；输出为 int8 logit，`>= -3` 即为血管，可以直接接 A 的形态学 IP（需要转成 0/255 的 8 位灰度格式）。
3. **B：性能优化**。可选方向：读、算、写重叠；INT8 打包存储；Add 融合进卷积写回阶段；提高时钟。每项优化后都要重新运行本目录的整网回归。
