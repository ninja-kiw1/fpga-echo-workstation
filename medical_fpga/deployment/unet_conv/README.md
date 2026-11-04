# U-Net 通用卷积层 HLS 算子（v0）

对应 `lwnet/mixed_precision_spec.md` 中的混合位宽第一阶段 U-Net。一个 IP 按层调用，覆盖网络中全部 20 个卷积类节点：17 个 Conv→(ReLU)→BN 融合块、2 个 2×2 转置卷积、1 个输出 1×1 卷积。Add、Concat、MaxPool 还没有实现，见文末“下一步”。

目标器件 xazu3eg-sfvc784-1-i（FZ3B），目标时钟 10 ns。以下性能和资源均为 HLS 估计，没有经过 Vivado 实现，也没有上板。

## 运算定义

每个输出通道：

```
acc = bias + Σ x·w                  INT32，x 为 int16 容器，w 为 INT8
a   = relu ? max(acc, 0) : acc      仅 Conv→ReLU→BN 主分支
v   = a·M + offset                  精确计算，65 位
q   = clamp(RNE_shift(v, shift), qmin, qmax)
```

RNE_shift 为按绝对值的最近偶数舍入，再恢复符号；与 `int8_reference.py` 的 `round_shift` 相同。普通 Conv2d、ConvTranspose2d 的 offset 为 0、relu 为 0。

| mode | 运算 | 输出尺寸 |
|---|---|---|
| 0 | 3×3，stride 1，零填充 1 | H×W |
| 1 | 1×1，stride 1 | H×W |
| 2 | 2×2 转置卷积，stride 2 | 2H×2W；输出 (y,x) 用输入 (y/2,x/2) 与权重 tap (y%2)·2+x%2 |

## 接口与数据格式

`int conv_layer(in, out, params, mode, cin, cout, in_h, in_w, relu, shift, qmin, qmax)`

- 三个 AXI4 Master（`gmem_in`/`gmem_out` 128 位，`gmem_par` 64 位），AXI-Lite 传地址、配置和返回值。
- 返回 0 表示完成；返回 1 表示配置非法，此时不读写任何缓冲区。
- 限制：cin、cout 为 1～32；mode 0/1 时宽高 ≤512，mode 2 时输入宽高 ≤256；shift 为 0～31；-32768 ≤ qmin ≤ qmax ≤ 32767。
- **激活**：NHWC，每个值用 int16 容器存放（INT8 层也一样），通道数补齐到 8 的倍数，补齐部分为 0。一个 128 位字存一个像素的 8 个连续通道，lane i 位于 bit 16i～16i+15。按小端字节流看，就是普通的 int16 HWC 数组。
- **参数**：64 位字，依次为：
  1. 权重：按 (tap, 输出组, 输入组, 输出 lane) 排列，每字 8 个 int8 输入 lane，共 taps×⌈cout/8⌉×⌈cin/8⌉×8 字；
  2. bias[Cp]、M[Cp]、offset[Cp]，均符号扩展到 int64，Cp=⌈cout/8⌉×8。
- 输出补齐通道恒为 0（权重、bias、M 都补 0），testbench 也逐一核对这些通道。
- 输入输出缓冲区不能重叠。`generate_vectors.py` 的 `pack_params` / `pack_activations` 就是 PS 端应复制的打包规则。

## 硬件结构

逐行执行“读入所需输入行 → 计算一整行 → 写回一行”，三个阶段目前不重叠。

- 3 行滚动行缓存（行 r 放在槽 r%3），存全部输入通道；整层权重、bias、M、offset 全部放片上。
- COMPUTE 循环把 (x, 输出组, ky, kx, 输入组) 展平为一个 II=1 流水线，每周期 8 输出 × 8 输入 = 64 个 MAC；到最后一个 tap 时 8 个通道同时完成重缩放，写入输出行缓存。
- 周期模型（`vectors/manifest.json`，只算循环迭代次数，不含流水线填充和 AXI 延迟）：整网 20 层合计 4116 万周期，其中计算 3355 万、读 400 万、写 360 万；100 MHz 下约 412 ms。有效 MAC 20.28 亿，MAC 阵列利用率约 94%。

## 每层寄存器配置（im0002 参数包）

| 节点 | mode | 通道 | 输入尺寸 | relu | shift | 输出范围 | 参数字数 | 模型周期(M) |
|---|---|---|---|---|---|---|---|---|
| first_block_2 | 3×3 | 3→8 | 512×512 | 1 | 27 | -32767..32767 | 96 | 2.88 |
| first_block_5 | 3×3 | 8→8 | 512×512 | 1 | 30 | -127..127 | 96 | 2.88 |
| first_shortcut_1 | 1×1 | 3→8 | 512×512 | 0 | 30 | -127..127 | 32 | 0.79 |
| down_path_0_block_2 | 3×3 | 8→16 | 256×256 | 1 | 30 | -127..127 | 192 | 1.38 |
| down_path_0_block_5 | 3×3 | 16→16 | 256×256 | 1 | 30 | -127..127 | 336 | 2.62 |
| down_path_0_shortcut_1 | 1×1 | 8→16 | 256×256 | 0 | 30 | -127..127 | 64 | 0.33 |
| down_path_1_block_2 | 3×3 | 16→32 | 128×128 | 1 | 30 | -127..127 | 672 | 1.28 |
| down_path_1_block_5 | 3×3 | 32→32 | 128×128 | 1 | 30 | -127..127 | 1248 | 2.49 |
| down_path_1_shortcut_1 | 1×1 | 16→32 | 128×128 | 0 | 30 | -127..127 | 160 | 0.23 |
| up_path_0_up_layer_block_0 | 转置2×2 | 32→16 | 128×128 | 0 | 30 | -127..127 | 304 | 0.72 |
| up_path_0_conv_bridge_layer_block_2 | 3×3 | 16→16 | 256×256 | 1 | 30 | -127..127 | 336 | 2.62 |
| up_path_0_conv_layer_block_2 | 3×3 | 32→16 | 256×256 | 1 | 30 | -32767..32767 | 624 | 5.11 |
| up_path_0_conv_layer_block_5 | 3×3 | 16→16 | 256×256 | 1 | 30 | -127..127 | 336 | 2.62 |
| up_path_0_conv_layer_shortcut_1 | 1×1 | 32→16 | 256×256 | 0 | 30 | -127..127 | 112 | 0.92 |
| up_path_1_up_layer_block_0 | 转置2×2 | 16→8 | 256×256 | 0 | 30 | -127..127 | 88 | 0.92 |
| up_path_1_conv_bridge_layer_block_2 | 3×3 | 8→8 | 512×512 | 1 | 30 | -127..127 | 96 | 2.88 |
| up_path_1_conv_layer_block_2 | 3×3 | 16→8 | 512×512 | 1 | 30 | -127..127 | 168 | 5.51 |
| up_path_1_conv_layer_block_5 | 3×3 | 8→8 | 512×512 | 1 | 30 | -127..127 | 96 | 2.88 |
| up_path_1_conv_layer_shortcut_1 | 1×1 | 16→8 | 512×512 | 0 | 30 | -127..127 | 40 | 1.31 |
| final | 1×1 | 8→1 | 512×512 | 0 | 30 | -127..127 | 32 | 0.79 |

各层的 shift 和 M 来自 `parameters.npz`，更换量化参数包后必须重新生成，不能直接照抄上表。

## 文件

- `generate_vectors.py`：独立 NumPy 参考（不调用 PyTorch 卷积）。先把 20 个真实层与 `lwnet/results/quant_sensitivity/im0002/golden_tensors` 逐值比对，全部一致后才写出测试向量；另外生成 15 组合成用例。
- `src/conv_layer.cpp`、`src/conv_layer.hpp`：可综合源码。
- `tb/test_conv_layer.cpp`：逐 lane 比较全部输出，包括补齐通道；检查越界写入；检查 9 种非法配置被拒绝且不写内存。
- `run_hls.tcl`：C 仿真（`cases_all.txt`）、综合、Verilog 协同仿真（`cases_cosim.txt`）、IP 导出。
- `vectors/`：每个用例一个子目录，包含 config.txt、params.bin、input.bin、expected.bin；`manifest.json` 记录来源、尺寸和周期模型。约 117 MB，可以用脚本重新生成。

合成用例覆盖：RGB 输入且输出 INT16、32→32 通道、INT16 极值输入、非 8 倍数通道（13→5、9→3、8→1）、转置卷积、1×1 / 单行 / 单列图像、shift=0、正负数恰好半步的舍入、双向饱和、最大宽度 512（转置卷积为 256→512），以及在大尺寸层之后再跑小层（检查片上状态是否残留）。

## 运行

```powershell
Set-Location E:\PLD\medical_fpga\deployment\unet_conv
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' .\generate_vectors.py
& 'D:\xlinx2023.1\Vitis_HLS\2023.1\bin\vitis_hls.bat' -f .\run_hls.tcl
```

默认执行全部阶段。要单独运行某一阶段，先设置 `$env:CONV_STAGE`，可选 `csim`、`synth`、`cosim`、`export`。只有当 `build/completed_<stage>.txt` 生成时才说明该阶段真正完成，批处理退出码本身不足以判断成功。C 仿真带 `-O`，20 个真实层约需几分钟。

## 验证结果（2026-09-23，Vitis HLS 2023.1）

- **C 仿真 PASS**：35 组，共 28,864,456 个输出值，全部逐值一致；9 种非法配置全部拒绝且没有写内存。
- **综合**：主要循环 LOAD、COMPUTE、STORE、PARAM_WEIGHTS 都达到 II=1，COMPUTE 流水线深度 43。估计周期 7.30 ns，Fmax 约 137 MHz。

| 资源 | 用量 | 占 ZU3EG |
|---|---:|---:|
| BRAM_18K | 168 | 38% |
| DSP | 132 | 36% |
| FF | 14,214 | 10% |
| LUT | 26,857 | 38% |

  DSP 中，COMPUTE 占 96 个：64 个用于 MAC，32 个用于 8 路 32×32 重缩放乘法；其余主要是地址计算的乘法。
- **Verilog C/RTL 协同仿真 PASS**（xsim，约 10.5 分钟）：15 组合成用例 + 2 个真实层（down_path_1_block_5 为 3×3、32→32；up_path_0_up_layer_block_0 为转置卷积），共 1,601,480 个输出值逐值一致；非法配置在 RTL 中同样被拒绝。down_path_1_block_5 的 RTL 延迟为 2,515,618 周期，周期模型为 2.49M，偏差约 1%，所以整网约 4150 万周期（100 MHz 下约 415 ms）的估计可以作为规划依据。协同仿真使用理想 AXI 从设备，不能代表真实 DDR 的延迟和带宽竞争。
- **IP 导出**：`build/conv_layer/solution1/impl/ip/xilinx_com_hls_conv_layer_1_0.zip`，驱动在导出 IP 的 drivers 目录。

以上结果中，资源和时序为 HLS 估计。最终资源和时序以 Vivado 实现为准，速度以板上测量为准。

## 已知限制与优化方向

1. 读行、计算、写行三个阶段串行执行；改成 DATAFLOW 或乒乓缓冲后，可以省掉约 18% 的读写周期。
2. INT8 层也使用 int16 容器，DDR 流量翻倍。打包成 INT8 需要同时修改接口和 PS 端的打包规则。
3. `wbuf` 被拆成 64 个 BRAM bank，每个 bank 只用 144 个字节，改用 LUTRAM 可省约 64 个 BRAM_18K。
4. 每行起始地址用乘法计算，占了约 20 个 DSP，可以改为累加。
5. 3×3 层在 cin=3 时，输入 lane 只用了 3/8（first_block_2 是唯一的 RGB 3×3 层）。
6. 若要提速，可以把输出并行度提到 16，或把时钟提到 150 MHz 以上，但需要 Vivado 实现验证时序。

## 相关模块

- Add、Concat、MaxPool：[unet_eltwise](../unet_eltwise/README.md)
- 整网层表与 C 仿真验证：[unet_network](../unet_network/README.md)

下一步由 C 集成到 Vivado Block Design：Zynq PS、HP 端口、AXI 互连，由 PS 按 `unet_network/vectors/network.txt` 依次配置并启动各层。
