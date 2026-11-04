# FPGA 眼底血管实时分割系统：工程总结

PLD 竞赛题目一（医学影像）。在 FZ3B（Zynq UltraScale+ XAZU3EG）上用 PL 运行混合位宽 U-Net，对眼底彩色照片做血管分割。

更新：2026-09-29。仅用于工程教学与算法演示，不作为临床诊断依据。

## 一句话现状

**整个 U-Net 已经在 FZ3B 硬件上跑通**，结果与软件参考逐值一致。本文件所说的“参考”都是 lwnet 的整数模型。还没有做的是：能自己启动的演示系统、上位机界面，以及把 A 的前后处理接进这条链路。

## 系统输入与输出

```
眼底照片 700×605 RGB ──(电脑上缩放)──► 512×512×3 uint8
      ──► FZ3B PL 逐层执行 30 个节点（20 个卷积 + 5 Add + 2 Concat + 2 MaxPool）
      ──► 512×512 int8 血管分数（logit，scale 0.1017）
      ──► 分数 ≥ -3 判为血管（等价概率 ≥ 0.4196）──► 血管掩膜
```

- 模型：lwnet 的 W-Net 第一阶段 U-Net，34,201 个参数，每帧 20.3 亿次乘加。权重 INT8；激活大多为 INT8，其中 2 个节点为 INT16。
- 目前输入是预先准备好的图像文件，通过 JTAG 写入 DDR；输出也通过 JTAG 读回电脑。还没有实时输入和显示。

## 完成情况

| 部分 | 负责 | 内容 | 验证程度 | 文档 |
|---|---|---|---|---|
| 数据 | A | STARE 20 张图，AH/VK 两套标注 | — | [data/stare](data/stare/README.md) |
| 前后处理 IP | A | 均值滤波、阈值二值化、形态学 | C 仿真 + RTL 协同仿真，共 78 组、298 万像素全部一致；**未接入系统，未上板** | [hls/](hls/README.md) |
| 模型与量化 | B | 模型选型；混合位宽量化（两个节点 INT16） | 20 张图开发验证，见下方“关键指标” | [lwnet/mixed_precision_spec.md](../lwnet/mixed_precision_spec.md) |
| 卷积 IP | B | 3×3、1×1、2×2 转置卷积；融合 ReLU、BN 与重缩放 | C 仿真 35 组 + RTL 协同仿真 17 组，全部一致 | [deployment/unet_conv](deployment/unet_conv/README.md) |
| Add/Concat/MaxPool IP | B | 非卷积的 9 个节点 | C 仿真 26 组 + RTL 协同仿真 20 组，全部一致 | [deployment/unet_eltwise](deployment/unet_eltwise/README.md) |
| 整网仿真 | B | 30 节点层表 `network.txt`，两个 IP 串联 | C 仿真 20 张图，输出、掩膜、Dice 都与 lwnet 完全一致 | [deployment/unet_network](deployment/unet_network/README.md) |
| Vivado 系统 | C | PS + 两个 IP + DDR 通路，由脚本一键重建 | 实现后 100 MHz 时序满足，已生成 bitstream 和 XSA | [vivado/](vivado/README.md) |
| **上板验证** | C | 通过 JTAG 在 FZ3B 上运行整网 | **im0002 的 9 个关键节点、im0324 的最终输出，全部 0 差异** | [embedded/jtag](embedded/jtag/README.md) |
| PYNQ 驱动 | C | Python 调度程序、上板包、冒烟测试 | 只在电脑的模拟设备上验证；**未在板上运行** | [embedded/pynq](embedded/pynq/README.md) |

## 关键指标

**分割精度**（lwnet 整数模型；FPGA 硬件复现了同样的输出，所以精度相同）

| 子集 | 张数 | FP32 Dice | 量化后 Dice |
|---|---:|---:|---:|
| 校准集 | 8 | 0.7798 | 0.7794 |
| 开发留出集 | 12 | 0.7524 | 0.7500 |
| 全部 | 20 | 0.7634 | 0.7618 |

这 20 张图都参与过模型选择，所以只是开发验证，不是独立测试集结论。表现最差的是 im0324（Dice 0.5676），问题主要是粗血管漏检。

**硬件资源**（Vivado 布局布线后，整个系统，xazu3eg）

| LUT | FF | BRAM（36Kb） | DSP | 时钟 | 时序余量 |
|---:|---:|---:|---:|---:|---:|
| 35,310（50%） | 31,009（22%） | 64（30%） | 204（57%） | 100 MHz | WNS +2.47 ns |

**速度**
- 周期模型：约 441 ms/帧（100 MHz）。
- 板上：计算量大的卷积层与模型的比值为 0.98～1.11。整网估计 450～500 ms/帧，约 2 帧/秒。这是 JTAG 轮询测得的上限，**不是精确实测**。

## 验证层级

从软件到硬件，逐级比对，每一级都是逐值一致：

1. 独立写的 NumPy 参考 = lwnet golden：量化规则理解正确；
2. HLS C 仿真 = 参考：算法实现正确；
3. RTL 协同仿真 = 参考：生成的硬件电路正确；
4. 整网 C 仿真 = lwnet（20 张图）：两个 IP 串起来仍然正确；
5. **FZ3B 实板 = 参考**：真实硬件正确（目前覆盖 2 张图）。

## 上板测试记录

原始数据在 `embedded/jtag/results/`：

| 目录 | 内容 |
|---|---|
| `20260928_233150_im0002` | im0002 整网运行，读回 first_block_2 |
| `20260928_233544_im0002` | 从同一次运行中读回 add_4、final |
| `20260928_233839_im0002` | 从同一次运行中读回另外 6 个节点 |
| `20260928_232539_im0002` | 另一次独立运行，读回 7 个节点全部一致 |
| `20260928_234414_im0324` | im0324 独立完整运行，最终输出一致 |
| `20260928_230909_im0002` | 第一次上板：读回数据里有 JTAG 误码，重读后误码位置不同（证明是传输错误，不是计算错误） |

JTAG 链路不稳定，经常出现误码甚至断开连接。脚本用分块校验、重试和换新进程恢复来保证拿到的数据可靠，详见 [embedded/jtag](embedded/jtag/README.md)。

## 剩余工作与风险

| 优先级 | 事项 | 负责 |
|---|---|---|
| 高 | **能自己启动的演示系统**：PYNQ（已有驱动）或 PetaLinux / 裸机；比赛演示时不能依赖 JTAG | C |
| 高 | **上位机界面与通信**：原图、掩膜叠加、参数、计时显示 | C，A 协作 |
| 高 | **A 的前后处理接入链路**：统一数据格式（512×512 RGB int16 容器 ↔ 8 位灰度），加入 Vivado 系统 | A、C |
| 中 | 其余 18 张图上板比对（每张约 3 分钟） | C |
| 中 | 在 ARM 上精确计时；连续多帧稳定性 | C |
| 中 | 传统分割基线（降级方案兼对照组） | A |
| 中 | 设计方案 PDF 与工程统一：PDF 写的是 Mobile U-Net、AXI-Stream，实际是普通 U-Net、AXI 内存接口 | 全队 |
| 低 | 性能优化：读算写重叠、INT8 打包存储、把 Add 融合进卷积、提高时钟 | B |

其他注意事项：
- 板子现在处于 JTAG 启动模式（SW1 四位全 ON）。要用原来的系统，需要把开关拨回原位置。
- 比赛统一发的板子型号未确认。如果不是 FZ3B，需要重做 Vivado 系统，HLS 的 IP 基本可以复用。
- 本批代码大部分还没经过队友审阅，建议在接口冻结会上交叉复现。

## 目录结构

```
medical_fpga/
  data/stare/          STARE 图像与标注
  hls/                 A：前后处理 HLS（均值滤波、阈值、形态学）
  deployment/          B：推理 HLS
    unet_conv/           卷积 IP
    unet_eltwise/        Add / Concat / MaxPool IP
    unet_network/        层表、整网 C 仿真、Dice 核验
  vivado/              C：系统 Block Design 脚本与构建输出（output/）
  embedded/
    jtag/                JTAG 上板验证（已完成）
    pynq/                PYNQ 驱动与上板包（未上板）
../lwnet/              模型、量化脚本与 golden tensors
```

## 复现入口

```powershell
# 整网 C 仿真（约 4 分钟/图）
& 'D:\xlinx2023.1\Vitis_HLS\2023.1\bin\vitis_hls.bat' -f E:\PLD\medical_fpga\deployment\unet_network\run_csim.tcl
# Vivado 系统构建（约 32 分钟）
& 'D:\xlinx2023.1\Vivado\2023.1\bin\vivado.bat' -mode batch -source E:\PLD\medical_fpga\vivado\create_system.tcl
# 上板验证（SW1 全 ON，接好 JTAG）
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' E:\PLD\medical_fpga\embedded\jtag\jtag_verify.py
```

各模块的详细步骤见对应 README。
