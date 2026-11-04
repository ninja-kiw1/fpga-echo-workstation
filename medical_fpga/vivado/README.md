# FZ3B U-Net 加速系统（Vivado 2023.1，C 负责集成）

Zynq UltraScale+ PS + [conv_layer](../deployment/unet_conv/README.md) + [eltwise_layer](../deployment/unet_eltwise/README.md)。由 `create_system.tcl` 完整重建：工程、Block Design、综合、实现、bitstream 和 XSA 全部由脚本生成，不需要在 GUI 里手工操作。

器件 xazu3eg-sfvc784-1-i，与 LED 例程和 HLS 工程相同。

## 运行

```powershell
Set-Location E:\PLD\medical_fpga\vivado
& 'D:\xlinx2023.1\Vivado\2023.1\bin\vivado.bat' -mode batch -nojournal -log build.log -source .\create_system.tcl
```

- `$env:SYSTEM_STAGE = "create"`：只建工程、BD 和 wrapper，约 2 分钟，适合在 GUI 里查看。默认 `all` 会接着综合、实现并生成 bitstream。
- `unet_system/` 已存在时脚本会拒绝运行。重建前先删除它；如果只是想查看，直接打开 `unet_system/unet_system.xpr`。
- 脚本依赖两个 HLS IP 的导出目录 `deployment/unet_*/build/*/solution1/impl/ip`，HLS 源码改动后要先重新导出 IP。

## 输出（`output/`）

| 文件 | 用途 |
|---|---|
| `unet_system.bit` | PL 配置文件 |
| `unet_system.hwh` | PYNQ Overlay 使用，文件名必须和 .bit 相同 |
| `unet_system.xsa` | Vitis / PetaLinux 硬件平台，已包含 bitstream |
| `address_map.txt` | 地址映射 |
| `utilization*.rpt`、`timing_summary.rpt`、`drc.rpt`、`build_summary.txt` | 实现后的资源、时序和 DRC 报告 |
| `unet_system_bd.tcl` | 由 BD 导出的 Tcl，便于代码评审或在 GUI 中重建 |

## 系统结构

```
             M_AXI_HPM0_LPD (32 位)
PS ────────────► smc_ctrl ──► conv_layer_0/s_axi_control     0x8000_0000
                          └─► eltwise_layer_0/s_axi_control  0x8001_0000

conv_layer_0     gmem_in / gmem_out / gmem_par ─► smc_conv ─► S_AXI_HP0_FPD (128 位) ─► DDR
eltwise_layer_0  gmem_a / gmem_b / gmem_out    ─► smc_elt  ─► S_AXI_HP1_FPD (128 位) ─► DDR

pl_clk0 100 MHz；rst_pl 由 pl_resetn0 产生复位
中断：conv_layer_0 → pl_ps_irq0[0]，eltwise_layer_0 → pl_ps_irq0[1]
```

- PS 的 DDR4、MIO 和外设配置取自 `edgeboard/board_files/fz3b/a.0/preset.xml`（359 个参数）。我核对过，它和 PYNQ `edgeboard-fz3b/base.hwh` 的 PS 配置一致，差别只在高地址 DDR 窗口；这里按 PYNQ 的做法映射全部 4 GB。
- 同一目录下的 `board.xml` 实际上是 FZ3A 文件的副本（`name="fz3a"`、器件 xczu3eg），因此没有把它当作 board part 使用，只提取了其中的 PS 参数。
- AXI 端口和时钟与 PYNQ 基础设计保持一致（控制口 HPM0_LPD 32 位，HP 口 128 位，PL0 100 MHz）。这样在已启动的 Linux 上动态加载本 bitstream 时，不需要改 PL 时钟或 HP 端口位宽。
- 两个加速器只映射 DDR：低 2 GB 在 `0x0000_0000`，高 2 GB 在 `0x8_0000_0000`。

## 寄存器（每个 IP 64 KiB 窗口，32 位访问）

公共部分：`0x00` 控制（bit0 ap_start，bit1 ap_done，bit2 ap_idle），`0x04` GIE，`0x08` IER，`0x0C` ISR（写 1 翻转清除），`0x10` 返回值（0 表示成功，1 表示配置非法）。64 位地址先写低 32 位，再写高 32 位。

| 偏移 | conv_layer（0x8000_0000） | eltwise_layer（0x8001_0000） |
|---|---|---|
| 0x18 / 0x1C | in 地址 低 / 高 | in_a 地址 低 / 高 |
| 0x24 / 0x28 | out 地址 低 / 高 | in_b 地址 低 / 高 |
| 0x30 / 0x34 | params 地址 低 / 高 | out 地址 低 / 高 |
| 0x3C | mode | mode |
| 0x44 | cin | ca |
| 0x4C | cout | cb |
| 0x54 | in_h | h |
| 0x5C | in_w | w |
| 0x64 | relu | m0 |
| 0x6C | shift | m1 |
| 0x74 | qmin | shift |
| 0x7C | qmax | qmin |
| 0x84 | — | qmax |

以 HLS 导出驱动 `drivers/*/src/x*_hw.h` 为准。每层的参数值来自 `deployment/unet_network/vectors/network.txt`。

一次调用的流程：写好所有参数和地址 → 写 `0x00 = 1` → 轮询 `0x00` 的 bit1（或等待中断）→ 读 `0x10` 确认为 0。

## 板端软件注意事项

1. **缓存一致性**：HP0/HP1 不经过 CCI，与 CPU 缓存不一致。ARM 写完输入或参数后要先刷新缓存，读取输出前要先使缓存失效；也可以直接使用不可缓存的连续内存（PYNQ 的 `allocate`、Linux 的 u-dma-buf 或 CMA）。
2. **缓冲区要求**：物理地址连续，至少 16 字节对齐（最好 4 KiB 对齐）；输入和输出不能重叠。整网 30 个张量中最大的是 512×512×16 通道，int16 容器下为 8 MiB。
3. **加载方式**：
   - PYNQ 镜像：把 `.bit` 和 `.hwh` 放在同一目录，用 `Overlay` 加载，再用 `MMIO` 读写上表中的寄存器。
   - 板上现有的 Ubuntu 18.04 镜像：需要先确认它的 FPGA 加载方式（fpga_manager 或厂商工具）以及启动时 psu_init 设置的 HP 位宽和 PL0 时钟。这些**尚未核实**。
4. 板上第一步建议用 im0002 逐节点比较：把每层输出与 `unet_network/vectors/images/im0002/golden/<node>.bin` 对照。

## 构建结果

2026-09-23，本机 Vivado 2023.1 完整构建约 32 分钟，没有 ERROR 或 CRITICAL WARNING，bitstream、hwh、XSA 都已生成。

- **时序满足**（100 MHz）：WNS +2.469 ns，WHS +0.010 ns。按 WNS 推算关键路径约 7.5 ns，提频潜力约到 130 MHz，但需要实际改频后重新实现来验证。
- **资源**（布局布线后）：

| 资源 | 总计 | 占比 | conv_layer | eltwise_layer | 3 个 SmartConnect |
|---|---:|---:|---:|---:|---:|
| LUT | 35,310 | 50.0% | 15,384 | 13,321 | 6,597 |
| FF | 31,009 | 22.0% | 10,446 | 8,727 | 11,798 |
| BRAM（36Kb 块） | 64 | 29.6% | 39 | 25 | 0 |
| DSP | 204 | 56.7% | 132 | 72 | 0 |

  实现后的 LUT 明显低于 HLS 估计（HLS 两者合计约 49K）。剩余约 35K LUT、150 个 BRAM 和 156 个 DSP，可以用于 A 的前后处理 IP 或后续加宽并行度。
- **DRC**：没有错误。只有 DSP 输入/输出流水线寄存器的性能提示（DPIP-2、DPOP-3/4），外加 1 条 RTSTAT-10（没有可布线负载的信号）。这些不影响功能。
- **已上板（JTAG，2026-09-28）**：在 FZ3B 上完整运行整网，im0002 的 9 个关键节点和 im0324 的最终输出都与 HLS 参考逐值一致，见 [embedded/jtag/README.md](../embedded/jtag/README.md)。
