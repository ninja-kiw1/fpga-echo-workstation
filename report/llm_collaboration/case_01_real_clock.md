# 案例 01：实测时钟与设计假设不符

## 背景

10-16 起做"提频"（F）：在 PL 内加 MMCM，把全部 PL 逻辑从约 100 MHz 提到 150 MHz，生成固件 f150 并上板。
10-17 另开一个并行对话做性能测量（J3），在 f150 上重测延迟、吞吐和计算效率。

## 提示词

J3 子对话由主对话启动，开场背景（节选，原文英文）：

> Background (from the session that started this one):
> PLD competition project (FPGA medical-imaging U-Net co-processor on an FZ3B Zynq UltraScale+ board ...). User raised performance priority on 10-16: part F (PL clock raise via MMCM) and part J (perf + FPGA-advantage evidence) go first.

## 模型回答与纠错轨迹

**1. 写结果之前做一致性检查，发现数字对不上**（J3 对话，10-17，原文）：

> Before writing up, one consistency check. `ap` shrank by ×0.6677, which matches 100 → 150.08 MHz but not the 101.376 MHz that `j3c` uses for r2. Checking where 101.376 comes from.

**2. 只读方式读 PLL 寄存器，确认真实时钟**（原文）：

> **The clock is not 150 MHz.** The board's input clock to the PL is really 101.376 MHz. I confirmed this by reading the clock registers read-only (the FSBL runs the PLL in fractional mode). The 150 MHz build assumed a 100 MHz input, and the clock multiplier scales whatever it gets, so f150 actually runs at **about 152.15 MHz**. ... Timing was signed off at 150.08 MHz with +0.558 ns of slack; at the real clock I estimate about +0.47 ns, but I haven't re-run it.

注意最后一句：模型明确区分了"估计"和"已验证"，没有把估计值当结论。

**3. 主对话（F）按实测时钟重做时序分析，并承认遗漏**（10-17，原文）：

> **为什么是 152.15 MHz**：板子的启动程序（FSBL）把 PL 输入时钟设成了 101.376 MHz，而我在设计里按 99.999 MHz 写的。交接文档第 100 行其实早就记着这件事，是我做 F 时漏看了。

> | 固件 | 名义频率 | 实际频率 | 建立时间余量 | 保持时间余量 |
> | --- | --- | --- | --- | --- |
> | f150（在板上） | 150 MHz | 152.15 MHz | +0.470 ns | +0.013 ns |
> | f125（备用，没上板） | 125 MHz | 126.7 MHz | +0.779 ns | +0.011 ns |
>
> 这个更正已写进交接 §9.66 和项目记忆，并加了一条规则：以后每版新固件都要按 101.376 MHz 再核一次时序。

## 结果与证据

- f150 在实测 152.15 MHz 下时序仍收敛，无需改固件；之后的 f150nv 也按同一规则重签核（WNS +0.486 ns）。
- 报告与视频中的 GOPS、峰值利用率改按 152.15 MHz 计算，并注明"标称 150 / 实测约 152"。
- 证据：`tcp_image/tcp_image_rx/vivado/f_clock/`（retime 脚本与报告）、`perf_j/j3_results_f150_2026-10-17.md`。

## 沉淀

`skill/timing_at_real_clock.md`。教训有两条：结论写出之前，先用另一组独立数据交叉检查；交接文档里已有的事实，新设计前必须重读。
