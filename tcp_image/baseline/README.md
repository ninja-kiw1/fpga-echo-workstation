# CPU 基线与功耗（赛题拓展⑥）

更新：2026-10-12（FPGA 一侧换成当前固件 xu2dp_e2b 的实测；CPU 数字仍是 10-08 的）。全部数字来自 FZ3B 实板与本机实测（功耗除外，见第 4 节）。

## 一句话结论

在同一块 FZ3B 上、同一个 U-Net、同一套参数、**输出逐值相同**的前提下：

- **2026-10-12 起（xu2dp_e2b 固件）**：FPGA 推理时 PS 只占 **1 个核**，速度**略快于 4 个 A53 核满载跑 NEON 优化代码**：
  512×512 FPGA 471.6 ms 对 CPU 4 核 565.2 ms（**1.20 倍**）；128×128 31.9 ms 对 34.8 ms（**1.09 倍**）。
  （10-11 的 xu2dp 是 476.1 / 32.2 ms；10-08 的 V9b 固件是 564.3 / 37.8 ms，与 4 核打平。）
- 与同样占 1 个核的 CPU 方案相比，FPGA 快 **4.3–4.7 倍**（NEON 优化）、**18–20 倍**（普通 C）。
- 也就是说，把网络卸载到 PL 后，PS 还剩约 3 个核给通信、界面、形态指标；纯 CPU 方案占满 4 核也稍慢一些。
- 瓶颈仍在搬运：PL 纯计算 304.5 ms，另外约 167 ms 是 PS↔PL 的数据搬运（put ~77 + get ~80）和调度（PS 当搬运工）。
  只比纯计算的话，PL 比 CPU 4 核快 1.9 倍。

报告里要如实写：FPGA 对**我们写的** 4 核 NEON 基线只快 9–20%；CPU 侧逐元素算子还没向量化（估计还能快 10–15%，见 §1），
更强的 CPU 实现可能把差距抹平。优势仍主要在“只用 1 个核 + 0.6 W 级的 PL 动态功耗（估算）”。

## 1. 同板同算法：A53 CPU 与 FPGA

CPU 基线 `cpu_unet.c`：纯 C，直接链接板端程序用的参数表（`tcp_image_rx/src/unet_net_data.c` / `skin/board/gen/unet_net_data.c`），
算术与两个 HLS 核完全相同（int16 激活 × int8 权重、INT32 累加、INT64 定点乘、最近偶数舍入、饱和；Add 一次舍入、Concat 各自舍入、2×2 MaxPool）。
**每次运行都与 FPGA / 整数参考的输出逐值比对：板上 36 组、电脑上 24 组全部 `BIT_EXACT`。**

三种 CPU 实现（越往下越快，都逐值一致）：

| 实现 | 说明 |
|---|---|
| 普通 C | `gcc -O3 -mcpu=cortex-a53`。板上 gcc 7.5 **没有**把乘加内层向量化（反汇编无 `smlal`），等于标量代码 |
| NEON | 内层改用 `vmlal_n_s16` / `vmlal_high_n_s16`，一像素的 8/16/32 路输出累加器常驻寄存器 |
| NEON 分块 | 再把相邻 2–4 个输出像素放在一起，同一份权重在寄存器里复用；输入先补零边，所有抽头都有效 |

板端 FZ3B：4× Cortex-A53 @ 1.067 GHz（cpufreq 当前值），Ubuntu 18.04，gcc 7.5，OpenMP。中位数，128 档 20 次、512 档 3 次（另有 1 次预热）。

| 方案 | 占用 | 512×512 | 128×128 |
|---|---|---:|---:|
| **FPGA xu2dp_e2b（10-12：xu2dp + eltwise 读写合并）** | **PL + 1 核** | **471.6 ms**（10 帧） | **31.9 ms** |
| FPGA xu2dp（10-11：V12 conv + 真双口张量 BRAM；PS：条带调度与搬运） | PL + 1 核 | 476.1 ms（10 帧，±0.5） | 32.2 ms |
| FPGA V9b（10-08：V9b conv + 单口 BRAM） | PL + 1 核（88%） | 564.3 ms（50 帧，564.0–566.4） | 37.8 ms |
| A53 NEON 分块，4 线程 | 4 核满载 | 565.2 ms | 34.8 ms |
| A53 NEON，4 线程 | 4 核满载 | 792.6 ms | 48.5 ms |
| A53 普通 C，4 线程 | 4 核满载 | 2375 ms | 147.0 ms |
| A53 NEON 分块，1 线程 | 1 核 | 2230 ms | 136.4 ms |
| A53 NEON，1 线程 | 1 核 | 3153 ms | 192.2 ms |
| A53 普通 C，1 线程 | 1 核 | 9470 ms | 582.5 ms |

- 皮肤模型（同结构、不同参数）的 CPU 数字与眼底相差 <1%，见 `results/board_cpu.json`。
- 每帧 2.028 GMAC（512）/ 0.127 GMAC（128）。CPU 吞吐：普通 C 0.21、NEON 0.64、NEON 分块 0.91 GMAC/s/核；4 核 3.59 GMAC/s。
  FPGA 整网 3.59 → **4.26 GMAC/s**（含搬运，V9b → xu2dp），**PL 纯计算 5.73 → 6.61 GMAC/s**（354 → 307 ms）。
- FPGA 512 档分解（10-06，V9b，`progress_and_handoff.md` §9.27.6）：PL 计算 354 ms、PS→BRAM 108 ms、BRAM→PS（GDMA）126 ms。
- 10-11 同一个严格测试程序前后对比（`E:/PLD/board_acceptance_xu2dp_20261011/验收报告.md`，各 10 次）：
  V9b ap 356.9 / put 107.7 / get 90.0 / 整网 566.3 ms → xu2dp **306.9 / 75.2 / 79.6 / 473.0 ms**。
  TCP 服务端 AI 时间（上表口径）564.2 → 476.1 ms（512）、37.8 → 32.2 ms（128）。
- 10-12 xu2dp_e2b（`E:/PLD/board_acceptance_e2b/验收报告.md`）：ap 304.5 / 整网 473.1 ms；TCP 板端 AI 471.6 ms（512）、31.9 ms（128）。
  “PS 占 1 个核”的占用数据是 V9b 时测的，xu2dp 没重测。
- CPU 4 线程逐层（512，NEON 分块）：卷积 480 ms、Add/Concat/MaxPool 86 ms。后者还是标量 INT64 舍入，再向量化估计还能快 10–15%，**没做**。
- FPGA 推理期间 PS 占用（`results/cpu_load_during_fpga.json`）：整机 22.3% / 22.9%（512 / 128），服务进程 88% / 86% 单核 —— 主要在轮询 `ap_done` 和搬张量。
  换成中断等待可以再省掉一部分（没做）。

## 2. 电脑上的参照（i7-14650HX + RTX 4060 Laptop）

不是同一平台，只用来说明量级。i7-14650HX：16 核 24 线程（WSL Ubuntu 24.04，gcc 13.3 `-O3 -march=native`，AVX2 自动向量化）。
笔记本同时开着别的程序，数字有抖动，取中位数。

| 方案 | 512×512 | 128×128 | 与 FPGA 逐值一致 |
|---|---:|---:|---|
| 同一份 C（整数），1 线程 | 388–419 ms | 26–28 ms | ✓ |
| 同一份 C（整数），8 线程 | 109–111 ms | 7.5 ms | ✓ |
| 同一份 C（整数），24 线程 | 67–103 ms | 5.3–6.3 ms | ✓ |
| PyTorch FP32 CPU，24 线程（oneDNN） | 33–36 ms | 8.1 ms | ✗（浮点） |
| PyTorch FP32 CPU，1 线程 | 108–112 ms | 8.0–8.4 ms | ✗ |
| PyTorch FP32 GPU（RTX 4060 Laptop） | 5.2 ms | 2.0–2.1 ms | ✗ |

功耗量级（**厂商标称，非实测**）：i7-14650HX 基础功耗 55 W、最大睿频功耗 157 W；RTX 4060 Laptop 整卡 35–115 W。
FZ3B 整板见下节，Vivado 估算片上 3.8 W 量级——差一到两个数量级，所以“PC 更快”与“边缘设备本地处理”不矛盾。

浮点与量化的精度差（之前测过）：眼底 STARE 20 张平均 Dice FP32 0.7634 → 部署 0.7618；ISIC 测试集 Jaccard FP32 0.8187 → 部署 0.8182。

## 3. Vivado 功耗估算（实现后，向量无关）

来源：`tcp_image_rx/vivado/ai_sys_xu2/.../system_wrapper_power_routed.rpt`（Vivado 2025.2.1，xazu3eg-sfvc784-1-i，置信度 Medium，默认翻转率）。
⚠ 这份报告对应的比特流是 `bitstream/ai_sys_xu2.bin`，**不是板上跑的 V9b**（哈希不同），应为同一 BD、只改 conv 内部调度的 V11；
资源与 V9b 几乎相同（DSP 271 = 271，LUT 46,905 对 46,532，FF 37,651 对 37,665），作为近似。精确的 V9b 估算需要 Vivado 2025.2.1 打开 V9b 的布线检查点（已被后续构建覆盖）。

| 项 | 功耗 |
|---|---:|
| 片上总功耗 | 3.782 W |
| PS8（含 4×A53、DDR 控制器；按 Vivado 默认活动率） | 2.821 W |
| PL 动态 | 0.633 W |
| ├ conv_layer（XU=2） | 0.350 W |
| ├ eltwise_layer | 0.205 W |
| └ 其他（BRAM 控制、互联、图像处理 IP 等） | 0.078 W |
| 器件静态（PS 0.104 + PL 0.223） | 0.328 W |

对照：旧版 `ai_sys`（XU=1，DSP 206）片上总功耗 3.779 W，conv 0.345 W —— 并行度翻倍几乎不增加估算功耗。
PS8 的 2.8 W 来自 Vivado 对 CPU / DDR 活动率的默认假设，**不能当实测**；PL 部分是 PL 新增负担的合理量级。

## 4. 实测功耗：流程已就绪，等功率计

板上没有电流 / 功率传感器（hwmon 为空；i2c 上只有 DS28C36 认证芯片；AMS 只能读电压和温度），必须在板子直流输入上串功率计。
`measure_power_phases.py` 把板子依次保持在 5 个状态，每段默认 60 s，你在每段读表后输入平均瓦数，脚本算每帧能耗：

| 阶段 | 板子在做什么 | 干跑实测（10 s，未读表） |
|---|---|---|
| idle | 什么都不做 | — |
| fpga_512 | 电脑连续发帧，PL 跑 512 整网 | 1.62 帧/s，AI 564.9 ms |
| fpga_128 | 同上，128 | 11.7 帧/s，AI 37.8 ms |
| cpu4_512 | 板上 CPU 基线 4 线程 NEON 分块 | 567.0 ms/帧，逐值一致 |
| cpu1_512 | 板上 CPU 基线 1 线程 | 2224 ms/帧，逐值一致 |

```powershell
cd E:\PLD\tcp_image\baseline
& E:\PLD\skin\.venv\Scripts\python.exe measure_power_phases.py          # 每段 60 s，按提示输入读数
```

- 前提：眼底服务在运行（皮肤服务也行，但脚本按眼底检查），上位机断开；`/root/cpu_baseline` 已由 `run_board_baseline.py` 部署。
- 需要的仪表：任一种能读直流功率或电流的表（DC 插头式功率计、USB-C PD 表、带电流显示的稳压电源）。
- 报告里要用的量：各阶段平均功率；`fpga_512` 与 `cpu4_512` 的**每帧能耗**（同样的帧率下，FPGA 只用 1 核 + PL，CPU 用 4 核）。
- 干跑记录：`results/power_DRYRUN_no_meter_*.json`（只有时间和温度，没有瓦数）。

## 复现

```powershell
$P = 'E:\PLD\skin\.venv\Scripts\python.exe'
cd E:\PLD\tcp_image\baseline
& $P make_vectors.py           # 输入与期望输出（来自已与实板逐值核对过的金标准 / 整数参考）
& $P run_board_baseline.py     # 上传、板上编译、跑 2 模型 × 2 分辨率 × 1/2/4 线程 × 3 种实现，约 8 分钟
& $P run_pc_baseline.py        # 本机：WSL 编同一份 C + PyTorch FP32 CPU/GPU
```

| 文件 | 内容 |
|---|---|
| `cpu_unet.c` / `makefile` | CPU 基线（`make` 板上：NEON + 普通 C；`make pc` 本机） |
| `net/` | 参数表副本：`fundus_unet_net_data.c` = `tcp_image_rx/src/unet_net_data.c`，`skin_unet_net_data.c` = `skin/board/gen/unet_net_data.c`（哈希相同） |
| `vectors/` | `{fundus,skin}_{128,512}.{rgb,i8}` |
| `results/board_cpu.json` | 板上 36 组 + 逐层耗时 + 运行前后 CPU 频率和 PS/PL 温度 |
| `results/pc.json` | 本机 24 组 C + 12 组 PyTorch |
| `results/cpu_load_during_fpga.json` | FPGA 推理时 PS 占用 |
| `board_sh.py` / `cpu_load.sh` | 远程执行小工具 / 占用采样 |

板上只新增了 `/root/cpu_baseline/`（只用 CPU，不碰 PL、不动眼底服务）；删掉它不影响任何东西。

## 局限

- CPU 基线是“认真写的 C + NEON 内层 + 分块”，不是手写汇编或 GEMM 库级别；逐元素算子还没向量化。
  10-08 估计更强的 CPU 实现可能比当时的 FPGA（564 ms）快 10–20%，即约 470–515 ms；和 xu2dp（476 ms）比，是打平到慢 8%。
- FPGA 侧的 476 ms（xu2dp）/ 564 ms（V9b）都包含 PS 搬运；两者的分解都是 10-11 同一测试程序实测（§1）。
- 功耗只有 Vivado 估算，且对应的是 V9b 的姊妹版本（xu2dp 没出功耗报告）；实测要等功率计。
- 电脑数据来自正在使用的笔记本，有抖动；只作量级参照。
