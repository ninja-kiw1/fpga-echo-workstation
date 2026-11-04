# U-Net Add / Concat / MaxPool HLS 算子（v0）

与 [unet_conv](../unet_conv/README.md) 配套，负责网络里的 9 个非卷积节点：5 个 Add、2 个 Concat、2 个 MaxPool。两个 IP 合起来可以执行 `lwnet/mixed_precision_spec.md` 描述的完整第一阶段 U-Net，整网验证见 [unet_network](../unet_network/README.md)。

目标器件 xazu3eg-sfvc784-1-i，目标时钟 10 ns。资源和时序为 HLS 估计，没有经过 Vivado 实现，也没有上板。

## 运算定义

| mode | 运算 | 定义 | 约束 |
|---|---|---|---|
| 0 | Add | `clamp(RNE(a·m0 + b·m1 >> shift))`，两路先在宽位数中相加，只舍入一次 | ca = cb |
| 1 | Concat | 输出通道依次为 `clamp(RNE(a·m0 >> shift))`、`clamp(RNE(b·m1 >> shift))` | ca 为 8 的倍数，ca+cb ≤ 32 |
| 2 | MaxPool | 2×2、stride 2 取最大值，保持输入 scale，不做重缩放 | h、w 为偶数 |

RNE 与 unet_conv 相同：按绝对值做最近偶数舍入，再恢复符号。h、w 是输入尺寸；MaxPool 时忽略 in_b、cb、m0、m1、shift、qmin、qmax。

Concat 要求 a 的通道数是 8 的倍数，这样 a、b 在 128 位字的边界上拼接，无需移位。网络中两个 Concat 分别是 16+16 和 8+8，都满足要求。

## 接口

`int eltwise_layer(in_a, in_b, out, mode, ca, cb, h, w, m0, m1, shift, qmin, qmax)`

- 激活格式与 unet_conv 完全相同：NHWC，int16 容器，每 8 个通道占一个 128 位字，补齐通道为 0。
- 三个 128 位 AXI4 Master（gmem_a、gmem_b、gmem_out），AXI-Lite 传地址和配置。
- 返回 0 表示完成，返回 1 表示配置非法，此时不读写内存。宽高 ≤ 512，通道 ≤ 32，shift 为 0～31。
- 输入输出缓冲区不能重叠。

## 硬件结构

- Add：一个展平的 II=1 循环，每周期读 a、b 各一个字，写出一个字，8 个 lane 并行计算。
- Concat：逐行执行：读 a 行、读 b 行（HLS 让两者并行执行）、合并并重缩放后写回。
- MaxPool：逐个输出行执行：读上一行到缓存，读下一行时同时做纵向和横向取最大值，结果先放入行缓存，再连续写回，以保证写突发。
- 网络中 9 个节点的周期模型合计约 292 万周期（100 MHz 下约 29 ms）。模型按 Concat 的三个循环串行估算，RTL 实测更快，见下文。

## 验证结果（2026-09-23，Vitis HLS 2023.1）

- `generate_vectors.py` 的 NumPy 参考先与 lwnet 的 9 个 golden 节点逐值比对，全部一致后才写出测试向量。
- **C 仿真 PASS**：26 组，13,914,776 个输出值逐值一致；13 种非法配置全部拒绝且没有写内存。
- **综合**：所有循环 II=1，估计周期 7.30 ns。资源：LUT 22,088（31%）、FF 9,677（6%）、DSP 72（20%）、BRAM_18K 78（18%）。
- **Verilog C/RTL 协同仿真 PASS**：17 组合成用例 + add_2、cat、down_path_1_pool 三个真实节点，共 2,904,728 个值逐值一致。RTL 中 cat 最长为 395,510 周期，低于模型的 524,288 周期，原因是 LOAD_A 和 LOAD_B 被并行执行。
- **IP 导出**：`build/eltwise_layer/solution1/impl/ip/xilinx_com_hls_eltwise_layer_1_0.zip`。

合成用例覆盖：int16 极值输入配 ±2³¹ 乘数、非 8 倍数通道、正负半步舍入（shift 为 1、2）、shift=0、双向饱和、8+5 / 16+16 / 24+8 拼接、全负数池化、最大宽度 512，以及在大尺寸节点之后运行小节点（检查行缓存残留）。

## 运行

```powershell
Set-Location E:\PLD\medical_fpga\deployment\unet_eltwise
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' .\generate_vectors.py
& 'D:\xlinx2023.1\Vitis_HLS\2023.1\bin\vitis_hls.bat' -f .\run_hls.tcl
```

单独执行某一阶段时设置 `$env:ELTWISE_STAGE`（csim、synth、cosim、export）。只有生成 `build/completed_<stage>.txt` 才说明该阶段真正完成。

## 已知限制与优化方向

1. LUT 占用偏高：8 个 lane × 3 份 50 位舍入逻辑（Add 一份、Concat 两份路径）。网络里的输入其实都是 INT8，把乘法和移位位宽收窄后应能明显减少 LUT。
2. 两个 IP 合计 LUT 约 69%，再加上 PS 互连后会比较紧。也可以把 Add 融合进卷积的写回阶段，省掉一次整帧 DDR 往返。
3. 与 unet_conv 一样使用 int16 容器，DDR 流量是 INT8 打包方式的两倍。
