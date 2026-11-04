# eltwise E2：Concat / MaxPool 读写合并（P2，2026-10-11）

PL 优化指导里的 **P2**。配合真双口张量 BRAM（xu2dp，`progress_and_handoff.md` §9.49），
把 eltwise IP 里“先读完、再写回”的循环改成“写本行 + 读下一行”同一趟，思路与 conv V12 相同。

- 源码：`src/eltwise_layer.cpp`（E2）；原版在 `E:/PLD/medical_fpga/deployment/unet_eltwise/src/eltwise_layer.cpp`，一字未改。
- IP 导出到 **`ai/build/elt_ip_e2/`**（新目录）。`ai/build/elt_ip/` 仍是已验收 xu2dp 比特流用的原版 IP，**不要覆盖**。
- 整系统构建：`ai/bd/run_build_xu2dp_e2.bat`（`build_ai_sys_xu2dp_e2.tcl`），与 xu2dp 脚本只差 eltwise 的 IP 仓库和产物名，
  产物 `bitstream/ai_sys_xu2dp_e2.{bit,bin}`、`vivado/ai_sys_xu2dp_e2_{util,timing}.rpt`。
  101.376 MHz 时序复核：`environment/inspect_xu2dp_e2.bat`。

## 为什么改、改了什么

10-06 实测（单口 BRAM，`ai/net/_profall_ai.log`）eltwise 9 个节点共 48.9 ms，全部卡在 BRAM 访问次数上：

| 类型 | 时间 | 实测 | 单口下限 | 双口下限 |
|---|---:|---:|---:|---:|
| Add ×5（每字 2 读 1 写） | 26.3 ms | 3.13 拍/字 | 3 | 2（读口） |
| Concat ×2（每字 1 读 1 写） | 17.1 ms | 1.10 拍/次访问 | 2 拍/输出字 | 1 拍/输出字 |
| MaxPool ×2（4 读 1 写） | 5.5 ms | 1.41 拍/输入字 | 1.25 | 1 |

双口后一拍能一读一写，但原版 Concat、MaxPool 的读和写在不同循环里，从不同时发生。E2：

- **Add**：不改（本来就是每拍读 a、b、写 out 的一条循环；双口下瓶颈是读口，2 拍/字）。
- **Concat**：行缓冲 `buf_a`、`buf_b` 各拆两半做乒乓，第 k 趟“写回第 k−1 行 + 预取第 k 行”，每拍一读一写，
  h 行共 h+1 趟（原来每行三趟、读写串行）。行放不进半个缓冲（每行 > 1024 字，网络里不出现）时，
  **同一条循环**退回“读一趟、写一趟”——不另写一条循环，因为 HLS 不跨循环共享硬件，会把 8 路舍入逻辑复制一份。
- **MaxPool**：每个输出行三趟 → 两趟，“读上行”和“写回上一个输出行”合成一趟；输出行缓冲拆两半乒乓
  （`(w/2)·ga ≤ 1024` 恒成立）。

接口、寄存器映射、m_axi 口名（`gmem_a / gmem_b / gmem_out`）、整数运算、非法配置检查全部不变，PS 侧（`unet_ai.c`）不用改。
BRAM 用量不变（原有两块行缓冲各拆两半）。

## 验证

`tb/test_eltwise_e2.cpp`：参考实现 = 原版源码一字不改（`namespace ref`），逐 int16 lane 比对，每组检查输出区后的哨兵（越界写）。

- A. 原有 26 组向量（含网络 9 个真实节点），对 golden；
- B. 13 种非法配置，必须返回 `ELT_BAD_CONFIG` 且不写内存；
- C. `unet_ai.c` 单缓冲条带形状：9 个节点 × 512/256/128/64/32 和几种 ROI 尺寸，满条带 + 末条带；
- D. 300 组随机差分 + 5 组强制用例（含不乒乓的大行 Concat、32 通道 MaxPool 满宽）。

运行：`run_hls_elt.bat csim|synth|cosim|export [随机用例数]`，日志 `_e2_<stage>.log`（末行 `EXITCODE`）。

## 结果

### xu2dp 上的逐节点实测（10-11 晚，E2 之前；`E:/PLD/board_acceptance_e2/xu2dp_before_profile_fundus512.log`）

> 口径：每份 profile 日志有 5 次运行（2 次预热 + 3 次采样），**第一次是冷启动、整网 ap 慢 ~2–3 ms**。
> 下表都是去掉第一次后的均值（最初那版表格误用了冷启动那一次，已更正；验收脚本的 `parse_profile` 也已改）。

双口 BD 本身已经让 **Add 自动降到 0.65×（3.13 → ~2.05 拍/字，到了读口下限）**；
Concat、MaxPool 读写分在不同循环，只到 0.73× / 0.93×：

| 512 档 | 10-06 单口 | xu2dp | 比例 |
|---|---:|---:|---:|
| Add ×5 | 26 349 µs | 17 220 µs | 0.65 |
| Concat ×2（cat、cat_1） | 17 090 µs | 12 440 µs | 0.73 |
| MaxPool ×2 | 5 464 µs | 5 061 µs | 0.93 |
| eltwise 合计 | 48 903 µs | 34 721 µs | 0.71 |

⇒ P2 能动的只剩 Concat（12.4 ms）和 MaxPool（5.1 ms）。Add 已在读口下限（2 读/字、读口 1 字/拍）。

### C 仿真（`_e2_csim.log`，Vitis 2025.2）

非法配置 13 / 原有向量 26（对 golden）/ PS 条带形状 176 / 随机 300 + 强制 5：**30 577 264 个值，0 差异，0 越界写**。

### 综合（`build/elt_ip_e2/solution1/syn/report/csynth.rpt`）

- 所有循环 **II=1**；新循环里的 m_axi 读写全部推断为可变长度突发（含 Concat 后半段才读的 `in_b`）。
- 资源（同为 2025.2 综合的原版对照）：BRAM_18K 54 → 54、DSP 74 → 76、FF 9 136 → 9 818、LUT 22 739 → 24 850（+2 111）。

### RTL 协同仿真（`_e2_cosim.log`，xsim）

20 组 cosim 向量 + 104 组小尺寸条带形状 + 5 组强制用例：**4 761 496 个值，`C/RTL co-simulation finished: PASS`**
（验证了 Concat 合并循环里 `DEPENDENCE false` 在 RTL 上成立）。
注意：xsim 带 AXI VIP 跑这些用例要 ~25 分钟、内存峰值 17 GB，别和别的大任务并行。

### IP 导出

`build/elt_ip_e2/solution1/impl/ip`；驱动头文件 `xeltwise_layer_hw.h` 的寄存器定义与原 IP **逐行相同**，m_axi 口名相同 ⇒ `unet_ai.c` 不用改。

### 整系统构建（E2，`bitstream/ai_sys_xu2dp_e2.{bit,bin}`，bin `bb7f6675…`）

`EXITCODE 0`，100 MHz 约束 WNS +1.685 / WHS +0.017；101.376 MHz 复核（`environment/inspect_xu2dp_e2.bat`）WNS +1.549 / WHS +0.017，
0 路由错误，0 未约束端点，64 个张量 RAMB36E2 仍为 TRUE_DP；LUT 46 116 → 47 400、FF 37 489 → 38 113、BRAM 192/216 不变；
DRC 只有 DSP 流水级告警。

### 上板（`E:/PLD/board_acceptance_e2/`）—— 17 个程序逐位通过，但 Concat 反而慢了

BoardCheck 31/31、512 golden 0/262144、17 个程序专项回归 + 1000/5000 帧稳定性全部通过（`state.json` 15 项）。
逐节点（512 档，去掉冷启动后的均值，µs）：

| 节点 | xu2dp | E2 | E2b | E2b / xu2dp |
|---|---:|---:|---:|---:|
| MaxPool ×2 | 5 061 | 4 173 | 4 163 | **0.82** |
| Concat ×2 | 12 440 | 13 251 | 10 893 | **0.876** |
| Add ×5 | 17 220 | 17 632 | 17 224 | 1.00 |
| eltwise 合计 | 34 721 | 35 056 | 32 280 | 0.93 |
| 整网 ap（严格测试 10 次均值） | 306.9 ms | 306.8 ms | **304.5 ms** | −2.4 ms |

E2 的 Concat 反而慢 6.5%。协同仿真里 Concat 合并循环是 1.09 拍/输出字（`cat` 285 468 拍 / 262 144 字），
设计本身达到了预期；板上却是 ~1.7 拍/迭代。**推断**的原因：每趟开头 `gmem_a`、`gmem_b` 各一次性发出最多 16 个 64 拍突发
（`num_read_outstanding` 默认 16，读 FIFO 1024 拍），互连轮流服务两个口 → b 的数据与 a 交替到达、先塞进 b 的 FIFO，
而循环先只消费 a，前半趟 a 只拿到一半读带宽：每趟 ≈ 512×2 + 512 = 1536 拍 + 开销。
Add（a、b 同步消费）和 MaxPool（只有一个读口）不受影响。E2b 把在途突发限到 2 个后 Concat 降到 0.876×，
与“b 只抢先 128 拍”的估算（每趟 ~1024+128+开销 ≈ 1270 拍）相符，算是印证了这个推断；但没有用 ILA 直接看总线，仍是推断。

### E2b = E2 + `num_read_outstanding=2`（`ai/opt_hls_elt_e2b/`）

只给 `gmem_a`、`gmem_b` 的 pragma 加 `num_read_outstanding=2`：b 最多先取 2×64 拍，a 拿到几乎整条读口。
逻辑、寄存器映射、端口不变。C 仿真（26 + 条带 + 随机 100）0 差异；综合在途读数 = 2（逻辑与 E2 相同，没另跑协同仿真）。
整系统（`bitstream/ai_sys_xu2dp_e2b.{bit,bin}`，bin `3bbd9396…`）：101.376 MHz WNS +1.656 / WHS +0.013、0 路由错误、
TRUE_DP 64 个；LUT 47 313、**BRAM 188/216（比 xu2dp 少 4 块：读 FIFO 变小）**。
上板验收：`E:/PLD/board_acceptance_e2b/`（旧固件 = E2；xu2dp 备份仍在板上 `/lib/firmware/ai_sys.bin.before_e2`）。

### 结论

P2 实际收益 **`ap` −2.4 ms（整网 ~0.5%）**，外加 BRAM −4 块；远小于指导文档“eltwise 49 ms 能省一部分”给人的印象，原因：
① Add（eltwise 一半时间）已被双口 BD 本身拿走了收益；② Concat 受两个读口在互连上交替到达的限制，合并循环只吃到一部分。
剩下的 Concat 空间（理想约 8.7 ms：每输出字 1 拍、每条带多一趟预取；现在 10.9 ms）每张图最多再省 ~2 ms，不值得再投入；更大的杠杆仍是 P3 提频（304.5 × (1 − 101.4/148) ≈ `ap` −96 ms）。

### 部署（10-12）

E2b 上板全套验收通过（BoardCheck 31/31、17 个程序逐位、1000/5000 帧稳定性，`E:/PLD/board_acceptance_e2b/验收报告.md`），
**用户决定保留 E2b**：板上 `/lib/firmware/ai_sys.bin` = `3bbd9396…`，部署记录 `bitstream/deployment_xu2dp_e2b_20261012.json`。
E2（`bb7f6675…`）也通过了全套验收（`E:/PLD/board_acceptance_e2/`），但已被 E2b 取代，只作为板上回退的一级保留。
