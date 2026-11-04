# 模型 FPGA 部署（B 负责）

混合位宽第一阶段 U-Net（`lwnet/mixed_precision_spec.md`）的 HLS 实现。所有结果目前只到仿真，还没有经过 Vivado 实现，也没有上板。

| 目录 | 内容 | 验证状态 |
|---|---|---|
| [unet_conv](unet_conv/README.md) | 通用卷积层 IP：3×3、1×1、2×2 转置卷积，融合 ReLU 与重缩放 | C 仿真、综合、Verilog 协同仿真、IP 导出 |
| [unet_eltwise](unet_eltwise/README.md) | Add、Concat、MaxPool IP | C 仿真、综合、Verilog 协同仿真、IP 导出 |
| [unet_network](unet_network/README.md) | 30 节点层表、整网 C 仿真、Dice 核验 | 20 张 STARE 图输出与 lwnet 逐值一致 |

两个 IP 的 HLS 资源估计合计约为 LUT 49K（69%）、DSP 204（57%）、BRAM_18K 246（57%），目标时钟 100 MHz。周期模型约为 441 ms/帧。

系统集成见 [vivado/](../vivado/README.md)：Vivado 布局布线后整个系统占 LUT 50%、DSP 57%、BRAM 30%，100 MHz 时序满足（WNS +2.47 ns），bitstream 和 XSA 已生成。已通过 JTAG 在 FZ3B 上运行，im0002 的 9 个关键节点和 im0324 的最终输出与参考逐值一致，见 [embedded/jtag](../embedded/jtag/README.md)。

各目录下的 `vectors/` 都可以用对应的 Python 脚本重新生成，三个目录合计约 430 MB；`build/` 是 Vitis HLS 生成的工程和报告。
