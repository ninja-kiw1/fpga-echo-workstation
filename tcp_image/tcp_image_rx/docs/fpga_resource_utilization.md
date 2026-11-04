# FPGA 资源利用报告

> 2026-10-02。对象：板上正在跑的 **`ai_sys`** 比特流
> （`bitstream/ai_sys.bin`，md5 `9f141b1b4f175faddeb9e6c3f0d8049e`）。
> 数据来源：Vivado 实现后的
> `vivado/ai_sys/ai_sys.runs/impl_1/system_wrapper_utilization_placed.rpt`
> 与各 OOC 综合报告；器件 **`xazu3eg-sfvc784-1-i`**（Zynq UltraScale+ EG，ZU3EG）。

---

## 1. 顶层总览（实现后 / Fully Placed）

| 资源 | 已用 | 可用 | 占用率 | 说明 |
| --- | --- | --- | --- | --- |
| **Block RAM Tile** | **192** | 216 | **88.89%** | ← **最紧的资源**；RAMB36 191 + RAMB18 2 |
| **DSP48E2** | **206** | 360 | **57.22%** | 全部来自两个 HLS 算子 |
| CLB LUTs | 35 703 | 70 560 | 50.60% | 其中 LUT-as-Logic 33 561、LUT-as-Memory 2 142 |
| └ LUT as Distributed RAM | 1 280 | 28 800 | 7.44% | HLS 算子的行缓存 |
| └ LUT as Shift Register | 862 | 28 800 | — | |
| CLB Registers (FF) | 27 605 | 141 120 | 19.56% | 全部是带复位/使能的 FDRE |
| CARRY8 | 2 180 | 8 820 | 24.72% | |
| **Bonded IOB（PL 引脚）** | **0** | 252 | **0.00%** | **一个 PL 引脚都没用** |
| BUFGCE / BUFG_PS | 3 / 1 | 88 / 72 | 3.4% / 1.4% | 时钟走的 PS→PL 的 `pl_clk0` |
| MMCM / PLL | 0 / 0 | 3 / 6 | 0% | 不用 |
| PS8 | 1 | 1 | 100% | ZynqMP 硬核 |

**一句话**：**计算和存储都放在 PL，但通信全靠 PS**——0 个 PL 引脚，全部走 PS 内部 AXI。

---

## 2. 按模块分解（OOC 综合口径）

| 模块 | 作用 | LUT | FF | RAMB36 | RAMB18 | DSP |
| --- | --- | --- | --- | --- | --- | --- |
| **`conv_layer`** (HLS) | U-Net 卷积（3×3 / 1×1 / 2×2转置） | **18 097** | 13 884 | **39** | 0 | **133** |
| **`eltwise_layer`** (HLS) | Add / Concat / MaxPool | **13 367** | 8 565 | 24 | **2** | **72** |
| **`img_proc_axil`** (自写 RTL) | 6 模式图像处理 + 2 Mb 帧缓冲 | 843 | 626 | **64** | 0 | 1 |
| `blk_mem_gen` (`bram_gen`) | **张量 BRAM 256 KB** | 137 | 8 | **64** | 0 | 0 |
| `axi_bram_ctrl` | 张量 BRAM 控制器 | 409 | 590 | 0 | 0 | 0 |
| `ai_data_ic` 及其 `auto_us` | 数据通路互联（7 主 1 从） | 2 063 | 1 845 | 0 | 0 | 0 |
| `sc_axil` 及其 `auto_pc` | 控制通路互联（32 位 AXI-Lite） | 1 229 | 1 758 | 0 | 0 | 0 |
| `pc_axil` | 显式协议转换器（修写数据丢通道） | 467 | 624 | 0 | 0 | 0 |
| `zynq_ultra_ps_e` | PS 硬核（含少量胶合逻辑） | 264 | 0 | 0 | 0 | 0 |
| `rst_pl` | 复位同步 | 19 | 40 | 0 | 0 | 0 |
| **合计** | | **≈36 900** | **≈27 940** | **191** | **2** | **206** |

**对账**：BRAM（64+39+24+64 = **191** RAMB36、2 RAMB18）与 DSP（1+133+72 = **206**）
**与顶层报告逐项吻合**；LUT/FF 的模块和略高于顶层，是布局阶段跨模块边界优化的正常现象。

### 三块 BRAM 的用途（192 块里）

| 用途 | 块数 | 大小 |
| --- | --- | --- |
| `img_proc_axil` 的**全帧缓冲** | 64 | 2 Mb（512×512×8bit） |
| **张量 BRAM**（AI 条带暂存） | 64 | 256 KB（128bit × 16384） |
| `conv_layer` 内部行缓存 | 39 | ≈175 KB |
| `eltwise_layer` 内部缓存 | 25 | ≈110 KB |

---

## 3. 时序

| 项 | 值 |
| --- | --- |
| WNS（建立） | **+2.013 ns** |
| WHS（保持） | **+0.0115 ns** |
| 结论 | **All user specified timing constraints are met**（0 失败端点） |
| 时钟约束 | `pl_clk0` 100 MHz（**实际板上 `pl0_ref` = 101.376 MHz**，由 boot.bin/FSBL 设定） |
| 错误/警告 | 综合实现 **0 error / 0 warning / 0 critical warning** |

⚠ WHS 只有 **+11.5 ps**——保持余量极薄。以后动 RTL/BD 时**必须重看时序**。

---

## 4. 余量：还剩什么、卡在哪

| 资源 | 余量 | 能不能再加东西 |
| --- | --- | --- |
| **BRAM** | **24 块（11%）** | ✗ **这是当前唯一真正的瓶颈** |
| DSP | 154 块（43%） | ✓ 还算宽裕 |
| LUT | 34 857（49%） | ✓ 很宽裕 |
| FF | 113 515（80%） | ✓ 很宽裕 |
| PL 引脚 | 252 个全空 | ✓ 完全没用 |
| MMCM/PLL | 3 + 6 个 | ✓ 完全没用 |

**结论：这块设计已经是"BRAM 受限"。** 再想塞任何需要存储的东西（更大的张量缓存、
更多行缓存、双缓冲的第二份），都得先砍掉现在占着的 192 块里的一部分。

**几个具体判断**：

* **加一个 `axi_bram_ctrl`**（比如把张量 BRAM 拆成两个，给 A-ov/并发用）：
  只要 ~400 LUT + ~600 FF + **0 块 BRAM** —— 代价很低。
* **把张量 BRAM 改成真双口（True Dual Port）**：**块数不变**（RAMB36 本来就是双口能力，
  已用 64 块），所以"改双口"这件事**不额外吃 BRAM**，只花布线。这也是为什么它值得先试。
* **再加一个大帧缓冲（如 512×512 的 32bit）**：要 ~128 块 → **放不下**。
* **再加一个 HLS 算子（类似 conv_layer）**：约 39 块 BRAM + 133 DSP → **BRAM 放不下**，
  DSP 也只剩 154。

---

## 5. 数据来源

* `vivado/ai_sys/ai_sys.runs/impl_1/system_wrapper_utilization_placed.rpt`（顶层，实现后）
* `vivado/ai_sys/ai_sys.runs/*_synth_1/*_utilization_synth.rpt`（各模块 OOC）
* `vivado/ai_sys/ai_sys.runs/impl_1/system_wrapper_timing_summary_routed.rpt`（时序）
* 构建脚本 `ai/bd/build_ai_sys.tcl`（一条 Tcl 从零重建）

> 注：顶层 `synth_1` 的资源报告显示全 0 是**正常的**——BD 内容走 OOC 综合，
> 顶层报告里看不到；真实数字看实现后的 `_placed.rpt`。
