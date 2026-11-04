# 当前进度与交接说明

> **2026-10-12 PS 更新（最新）**：服务端 **v5.10** `8332ec12…`（回滚 → v5.9）：PL 后处理接入实际流程，心超工作站的"最大块 + 填洞"、
> CT 窗口的双线性放大改在 PL 上做，与 PC 原算法逐位相同；16 项回归通过。见 **9.53**。

> **2026-10-12 PL 更新**：固件换成 **xu2dp_e2b_pp** `21aa27b6…`（回退 → `ai_sys.bin.before_pp` = e2b）：
> PL 上多了后处理 IP `pp_layer`（阈值 / 放大 / 连通域 / 最大块 + 填洞 / 分块和），板上 182 个向量逐位一致，
> 原有 17 个程序回归、BoardCheck 31/31、稳定性全过，AI 耗时不变。服务端还没调用它。见 **9.52**。

> **2026-10-12 PS 更新**：服务端 **v5.9** `785ed9ca…`（回滚 → v5.8 `9484870d…`）：**程序包**，模型可以在运行时安装，
> 服务端不用重编、不用重启，安装时在 PL 上自检、逐位一致才启用。17 个内置模型打成包后与内置、HLS 参考三方逐位一致。见 **9.51**。

> **2026-10-12 PL 更新**：板端比特流换成 **xu2dp_e2b**（xu2dp + eltwise P2 读写合并），
> `/lib/firmware/ai_sys.bin` = `3bbd9396…`；回退链 `ai_sys.bin.before_e2b`（E2）→ `.before_e2`（xu2dp）→ `.before_xu2dp_20261011`（V9b）。
> 17 个程序逐位回归、5000 帧稳定性通过。P2 收益很小：`ap` 306.9 → **304.5 ms**（整网 ~0.5%），BRAM 192 → **188**。
> 本机 PL 构建环境已就绪（`E:\AMD\Xilinx\2025.2`，队友的可移植脚本已合并，`environment\probe_fpga.bat` 预检通过）。见 **9.50**；
> 下一步最大的杠杆是 🥉 提频（`ap` 约 −96 ms，要换 boot.bin）。

> **2026-10-11 PL 更新**：板端比特流换成队友做的 **xu2dp**（V12 conv + 真双口张量 BRAM），
> `/lib/firmware/ai_sys.bin` = `8f8c27fe…`（旧 V9b `4a744c89…` 备份为同目录 `ai_sys.bin.before_xu2dp_20261011`）。
> PS 服务程序仍是 v5.8 `9484870d…`，模型库、boot.bin、PL 时钟都没动。17 个程序全部逐位回归、5000 帧稳定性通过。
> 512 整网 **566 → 473 ms（−16.5%）**，128 档 **37.8 → 32.0 ms（−15%）**。见 **9.49**；下一步（eltwise / 提频）见第四节。
> 本机已装 Vivado / Vitis **2025.2**（`E:\AMD\Xilinx\2025.2`），PL 可以在本机重建了（脚本路径还没改，见 9.49）。

> **2026-10-08 上板更新**：当前板端已部署统计协议 **v4**，置信度、ROI、形态指标已真实上板验收。
> 修复了 `ai_view.c` 的 16 位连通域索引溢出，以及 `unet_ai.c` 的 3×3 卷积条带输出少预留两行导致的 ROI BRAM 越界。
> 整图 golden logit **0/262144 差异**，12 组 ROI 与整图逐值一致，形态指标与独立 NumPy/SciPy 参考一致。
> 75 帧验收（含连续 50 帧）、重启后 30 帧回归、GUI 置信度/门限/ROI 测试全部通过。
> 板端整图 AI 约 564 ms，150×140 ROI 约 84 ms。仍使用原有 V9b 固件；本次只更新用户态程序。
> 详细证据、启动与回退命令见 [`../tcp_image_ui/board_check_v4/上板验收报告_2026-10-08.md`](../tcp_image_ui/board_check_v4/上板验收报告_2026-10-08.md)。
> 下方 10-07「未上板」段落保留为历史记录，当前状态以本条和验收报告为准。

> **给新会话**：读这一份就能接着干。更细的内容在
> `docs/design_notes.md`（网口收图）、`docs/image_processing_design.md`（PL 图像处理）、
> **`docs/ai_inference_design.md`**、**`docs/ai_full_resolution_strip_plan.md`**（条带调度 / BRAM / 内核模块）、
> **`docs/ai_latency_breakdown_and_optimization.md`**（2026-10-06 的耗时分解与优化排序，
> **含所有被实测否决的方案**）、**`docs/fpga_resource_utilization.md`**（资源与时序口径）、
> **`ai/opt_hls/README.md`**（conv 算子并行化 V1→V12 全过程）、
> **`test_report_2026-10-06_conv_parallel.md`**（本轮交付报告）里，
> 本文只讲"现在到哪了、下一步做什么、别踩什么坑"。
>
> 最后更新：**2026-10-06（深夜）** —— 覆盖 **9.22 ~ 9.34**（10-02 深夜之后新增的全部轮次）
>
> ⚡ **2026-10-07 补记：置信度热力图（flags b5 回传 logit 图，9.35）、ROI 局部推理（控制头 v5，9.36）与血管形态指标（flags b6，9.37）代码完成、离线验证通过，⚠ 板上还是旧程序，
> 要重新编译部署才生效**，见 **9.35 ~ 9.37**。同日上位机还加了菜单栏、帧率、板端推理耗时与免责声明。
>
> ⚡ **最新一轮：conv 算子并行化（XU=2）上板交付** ——
> `ap`（PL 计算）**531.99 → 353.7 ms（1.502×）**、板端整网 **776.3 → 599.0 ms**、
> 512 全回传端到端 **898.5 → 698.2 ms**、golden **0/262144 逐字节 PASS**、
> **整板 BRAM 一块没多（192/216）**。见 **9.27**。
> ⛔ 同一目标下的两个变体 **V10 / V11 都上板实测更慢（1.63× / 1.52×），已废弃**（9.28）；
> **V12 已写好、C 仿真 0 差异、并且已经在 10-06 23:45 综合 + 导出成 IP**（⚠ 这意味着
> **`ai/build/conv_ip_xu2` 现在装的是 V12、不是交付版 V9b**）；真双口 BD 脚本也已就绪
> 并在 23:47 启动过一次构建，**但跑到 Physical Synthesis 就断了（没有比特流）—— 重跑即可**（9.29）。
>
> ⚡ 本轮更早：**GDMA 搬"读回"落地**（`plbdma.ko` + `/dev/plbdma`）——
> 板端整网 **1768 → 835 ms**、上位机 **1877 → 896 ms（2.1×）**、
> `get` **1113 → 181 ms（6.1×）**（后来复测的稳态是 125.4 ms，见 9.25）、
> golden 仍 `0/262144` PASS（9.25）。
> 再往前：**把"读"的硬顶钉死**（每次 16 B ≈ **198 ns**、不流水、NEON 反而慢一倍，9.23）、
> **"搬运与计算重叠"第三次尝试 —— 仍被硬件挡住，且算下来是白做，已否决**（9.26）、
> **新工程 `Fz3b_BoardCheck`（板子自己当上位机，31 项全 PASS + GTK3 界面 + 开机自启）（9.30）**。
>
> ⚠ **本轮出过一次事故**：在 conv 核 `ap_idle=0` 时重编 PL → **整板 ping/SSH 全断，
> 只能断电重启**（本机 JTAG 不通）。规矩已写进脚本，见 **9.32** 与第六节坑 24。
>
> ⚡ 更早（10-02 深夜，上一版交接的末尾）：**第 7 个处理模式「边缘叠加」**（PL 跑模式 2，
> PS 就地画白边，不改 RTL）0/786432 差异、57.7 ms（9.14）；**自动算阈值时剔除背景**
> （test.ppm 灰度下限 35、一级 Otsu 71 → **99**，血管终于显形，9.13）；
> **阈值门限做成上位机可调**（控制头 v4，逐帧带 `thr`，9.12）；
> **P4-full 全分辨率 512×512 跑通**（条带 + halo，与 PLD golden 262144/262144 一致，9.11）；
> P5+P6（flags/掩膜/叠加/统计、C 驱动 3.1×、换权重）；P4 整网 32×32 bit-exact；
> P3 单层卷积 0 差异；9.6 记录试过的 7 个方向与经验。

---

## 一、三个工程

| 路径 | 内容 |
| --- | --- |
| `E:\fpga\my_project\tcp_image_rx\` | 板端程序 + PL 图像处理 RTL + AI(HLS) + 内核模块 + 文档 + BD 脚本 |
| `E:\fpga\my_project\tcp_image_ui\` | PC 端 tkinter 界面（发图/收图/计时/选模式/选回传内容） |
| **`E:\fpga\my_project\Fz3b_BoardCheck\`**（2026-10-06 新建） | **板端自检工具**：板子自己当上位机、不过网，直调板端驱动跑完整条 PS/PL 通路，带 GTK3 界面 + 开机自启（见 9.30） |

---

## 二、硬件与环境现状

**开发板 FZ3B（百度 EdgeBoard）**

| 项 | 值 |
| --- | --- |
| 主芯片 | `XAZU3EG-1SFVC784I`（Zynq UltraScale+ EG，4×A53 + 2×R5） |
| 系统 | Ubuntu 18.04.5，内核 `4.14.0-xilinx-v2018.3` |
| 启动 | **SD 卡**（SW1 = `ON,OFF,ON,OFF`）；eMMC 档起不来（实测不行） |
| 网络 | eth0 静态 `192.168.1.254/24`，SSH `root`/`root` |
| 板载工具 | `gcc` 7.5.0 / `make` / `python3` 都有 → **源码直接 scp 上去原生编译**，不用交叉编译；**gcc 7.5.0 正好配 4.14 内核 → 内核模块也在板上原生编**（见 9.19/9.20） |
| PL 现状 | **跑的是我们自己的设计**：`ai_sys_xu2_v9b`（conv XU=2 版），开机由 `ai-sys-bitstream.service` 把 `/lib/firmware/ai_sys.bin` 烧进去。（这是截至 10-06 深夜的状态；**一跑 `Fz3b_BoardCheck` 的硬件自检就能确认**） |
| 可换比特流 | ✓ `fpga_manager` 已注册：`/sys/class/fpga_manager/fpga0`；换之前要 `rmmod fpgadrv`。⚠ **重编前必须先确认 HLS 核 idle，否则整板挂死**（见第六节坑 24） |
| PL 时钟 | `pl0_ref = 101.376 MHz`（**由 FSBL/boot.bin 设定，加载 `.bit` 改不了**）；时序约束按 100 MHz 做 |
| 内核模块 | `plbram.ko`（BRAM 映射成 Normal-NC）+ `plbdma.ko`（GDMA 搬张量读回），都开机自加载；**都没装也不影响功能**，只是慢 |
| 内核源码 | **板上有** `/root/linux-xlnx`；**本机也有一份** `E:\fpga\my_project\_kernel_src\linux-xlnx-xlnx_rebase_v4.14_2018.3\`（tarball 版、**无 `.git`**）→ 重编 `plbram.ko` / `plbdma.ko` 直接用。`/usr/local/bin/bc` 是 **Python 写的 bc 替身**（板上没 bc） |
| 板上受限的地方 | **没有外网**、apt 缓存里也没有新包；`tkinter` 没装（但 `python3-gi` + GTK3 **已装**）；**通常没有键鼠**（`/dev/input` 不存在）；板子时钟停在 2021 年 |

**PC 环境**

| 项 | 值 |
| --- | --- |
| 网卡 | 有线网卡 `以太网`（ifIndex 10）设成静态 **`192.168.1.111/24`**，不设网关 |
| Vivado | `E:/fpga/vivado/2025.2.1/`（2025.2.1） |
| 交叉编译器 | `E:/fpga/vivado/2025.2.1/gnu/aarch64/nt/aarch64-linux/bin/aarch64-linux-gnu-gcc.exe`（GCC 13.4.0，**Linux** 版，不是 `aarch64-none` 裸机那套） |
| Python | 用 **Vivado 自带的 3.13**（系统那个 3.14 的 tkinter 是坏的） |
| 已装库 | `paramiko`（→ **本机可直接 SSH/SFTP 到板子**）、`pillow`（都在 `--user` 目录） |
| 跑长任务的唯一稳套路 | **写 `.bat` → `exec cmd /c xxx.bat &` 后台起 → 用 Read 工具读日志**。`after N` 只敢用到 ~30 s；长阻塞调用会把整个 Vivado 会话杀掉（2026-10-06 早先连杀 4 次，换这套之后一次没掉） |

---

## 三、已完成并**实测验证**的部分

> ⚠ **3.1 ~ 3.7 是 10-01/10-02 当时的记录**，其中不少"卡住/没执行过/未上板"的说法
> **后来都已经解决**（那次 PL→PS 从口不通的问题最终是**改走 PS→PL 通路**绕过去的）。
> 读的时候请把它们当**历史过程**，**现状以 3.8 的总表为准**。

### 3.1 网口收图（全部真机跑过）

- 协议：512×512 RGB888，切成 512 个**行包**，每包 = 行号 u16 + 长度 u16 + CRC16 u16 + 1536 B
- 往返延迟 **1250 ms → 27 ms**（详见 `docs/design_notes.md` 第 12 节）
- **读回逐字节比对：786432 字节完全一致**
- 三种图样整幅 CRC 全对：`bars`=0xF9F9、`checker`=0x1529、`gradient`=0x7BB7
- 开机自启已做：systemd 服务 **`tcp-image-server`**（enabled，重启后 9 秒自动起来）
  - 参数：`-p 5060 -a 0x40000000 --no-dma --echo -n 0`
  - 日志：`journalctl -u tcp-image-server -f`
- `systemctl stop` 已能**干净退出（0.1 s）**——修掉了 SIGTERM 被 SA_RESTART 挡住的问题

### 3.2 PC 端界面（全部验证过）

- 左发原图 / 右收回图 / 显示往返耗时 + 拆分 + 统计
- **可载入任意格式任意尺寸图片，自动拉伸为 512×512**
  （512×512 的不重采样，保证逐字节可比）
- CLI 自测：`python imagelink.py 192.168.1.254 5060 bars --repeat 5`
- 启动：`tcp_image_ui\run.bat`（或 `run.bat --image D:\pics\cat.jpg`）

### 3.3 PL 图像处理 RTL（综合验证过，未上板）

```
rtl/img_proc_axis.v      AXI-Stream 顶层(RGB→灰度 + 握手 + 流控)
rtl/gray_frame_store.v   帧缓冲 262144×8 + min/max 统计 + 3x3 窗口读出
rtl/mode_map.v           五种模式映射，三级流水
rtl/recip_lut.v          归一化倒数表
rtl/pcolor_lut.v         jet 伪彩色表(例化两次)
```

**综合结果（`img_proc_axis`，out-of-context，0 error / 0 critical warning / 0 warning）**：

```
Block RAM Tile   64 / 216   29.63%      ← 2 Mb 帧缓冲，正确推断成 RAMB36
DSPs              1 / 360    0.28%      ← 归一化的 8x16 乘法
LUT 约 830 / 71000 (1.2%)   FF 约 460
```

### 3.4 Block Design 脚本（写好了，**没执行过**）

`bd/create_image_proc_bd.tcl`：往已有的 BD 里加
AXI DMA（MM2S+S2MM 双向）+ `img_proc_axis`（模块引用）+ AXI GPIO（模式寄存器）。

### 3.5 板端 PS 程序改造（2026-10-01，**交叉编译 + 主机测试通过，未上板**）

让板子把 PC 选的模式真送到 PL、跑 DMA 往返、回传**处理后的图**（细节见
`docs/image_processing_design.md` 第 7 节）：

- 新增 `src/axigpio.h` / `src/axigpio.c`：AXI GPIO 最小驱动（ch1 写模式寄存器，
  ch2 读回 PL 统计的帧 min/max）。
- `src/ddr_layout.h`：`frame_status` 由 32 B **扩到 64 B**（加 `s2mm_status` /
  `mode` / `proc_min` / `proc_max`）。
- `src/tcp_image_server.c`：
  - 紧包 RGB888 → DDR 32bit/像素 的展开（**必须在 DMA 之前**，旧代码放错了位置）；
  - 写模式寄存器 `ctrl=(mode&7)|(thr<<8)`；新增 `-g/--gpio-addr`、`--thr`；
  - DMA 改**往返**：**先架 S2MM 再开 MM2S**，两路都按 `IMG_DDR_BYTES`(1048576) 搬；
  - `--echo` 回传**处理后的图**（Echo 前把 32bit 还原成紧包并**重算行 CRC**）；
    `--no-dma` 时行为不变（回显原图）。
- 验证：`aarch64-linux-gnu-gcc -O2 -Wall -Wextra` 四个 `.c` **0 warning**，
  链出 AArch64 ELF（`e_machine=0xB7`）；`test/host_test_rx.exe` **15/15 通过**
  （含新增的像素格式转换往返）。
- **顺手修好了 `test/` 那个一直没跑起来的主机测试**（`sigaction` 缺垫片 + 正例没开
  `verbose` 拿不到 frame CRC），并把 `axigpio.c` 加进了它的编译命令。

### 3.6 Block Design + 比特流 + 上板（2026-10-01，**比特流能加载，但 PL→PS 通路还没打通**）

> **10-18 更正**：下面"厂商 PS 里没有任何 PL→PS 从口是使能的"不成立——HP0 被厂商配成 64 位，实测 PL 经 HP0 直接读写 DDR 逐位正确、811 MB/s / 方向，见 **§9.75**。

用户提供了**百度 EdgeBoard FZ3B 的 Vivado 参考例程**（`C:\Users\moxie\Downloads\Compressed\Baidu_EdgeBoardLite_FZ3B_Vivado_Reference-main\`），
里面有 `参考工程/{AXI-GPIO, UART, FreeRTOS-Lwip-TCP-Server}/*.tcl` —— **完整的 FZ3B `zynq_ultra_ps_e` 配置**
（DDR4-2400、512M×16×4=64bit/4GB、MIO 树、SD/QSPI/UART1）。⑧ 的"缺 PS 预设"到此解决。

- 新脚本 **`bd/build_full_design.tcl`**：从零建工程 + BD（PS + `axi_dma_img` + `img_proc_axis` +
  `gpio_ctrl` + 两个互联 + `proc_sys_reset`）+ wrapper。一条 Tcl 跑完。
- 工程在 `vivado/img_proc/`；产物 `bitstream/system_wrapper.{bit,bin}`（.bin = 5 568 668 B，
  头部 `0x000000BB` → `0xAA995566`，是合法的 PCAP 比特流）。
- 器件：`hardware_info.txt` 写 **XAZU3EG-1SFVC784I**；参考例程用 `xczu3eg-sfvc784-1-i`。
  **两者 IDCODE 相同（`0x04a42093`），所以比特流通用**，我们用 `xazu3eg-sfvc784-1-i`。
- 综合 0 error / 0 critical warning；实现 **WNS +4.12 ns / WHS +0.004 ns**（保持很紧但过）；
  资源 BRAM 69/216、DSP 1/360、无 PL 引脚（全走 PS 内部）。
- 板上实测：
  - `rmmod fpgadrv` → `echo system_wrapper.bin > /sys/class/fpga_manager/fpga0/firmware`
    → **器件接受、PL 配置成功**（厂商 PL 的 `serial@0xA0020000` 变成总线错误 = 已被顶掉）。
  - **PS→PL 主口通**：AXI GPIO 在 `0xA0010000` 写 `0x0BADF00D` 读回 `0x0000F00D`（16bit 截断，正确）。
  - **PL→PS（DDR）不通**：AXI DMA 在 `0xA0000000`，写 DMACR/LENGTH 后 `DMASR` 一直 `0x00000000`
    （busy、无任何 error）；跨过 2 GB 边界也不报 DECERR。就是"事务没人应答"。

- **已排除/已确认**：
  - PS 时钟：`PL0_REF_CTRL=0x01010802` → **CLKACT=1，pl0_ref=101.375 MHz 在跑**。
  - 厂商 PS 的 PL→PS 从口不是没开：`AFI_FM0..5` 寄存器都有配置值，`AFI*_REF_CTRL` 也有 CLKACT=1。
  - 换 PS 从口（`S_AXI_HPC0_FPD` ↔ `S_AXI_HP0_FPD`）**结果完全一样**。
  - 互联件：**`SmartConnect` 在低区模式 + 2 个主口时会返回"上一次读的数据"**（实测），
    换成 `axi_interconnect` 后控制通路恢复正常 —— 这条是本次抓到的真 bug，已写进脚本注释。
    数据通路三种组合（SmartConnect+HPC0 / axi_interconnect+HP0 / SmartConnect+HP0）都仍卡住。
  - `axi_interconnect` 还有 `BD 41-2384`：PS 的 HP 从口 AWID/ARID 是 **6 位**、互联只给 1 位；
    但换回 `SmartConnect`（自动扩 ID）也没用。

- **排除法做完了（2026-10-01 晚）：厂商 PS 里没有任何 PL→PS 从口是使能的。**
  证据链：
  1. 做了 **纯 DMA 回环**版（`M_AXIS_MM2S` 直连 `S_AXIS_S2MM`，绕开 `img_proc_axis`）——
     **一样卡死**。所以跟图像流水线无关（`img_proc_axis` 洗清嫌疑，它的握手 RTL 也逐行看过，上电
     `s_axis_tready` 就是高的）。
  2. 逐个换 PL→PS 从口：**`HPC0` / `HP0` / `HP1` / `ACP` 四个口现象完全相同** ——
     DMA 启动后 `DMASR` 恒 `0x00000000`（busy）、**无任何 error**；跨 2 GB 边界也不报 DECERR。
  3. `ACP` 那条最有说服力：BD 明确警告"ACP 只支持 16/64bit INCR，其它一律回 SLVERR"，
     可实际连 **SLVERR 都没有** —— 说明口子根本没接上，不是"接上了但 master 不兼容"。
  4. 旁证：`AFI_FM0..5` 六个寄存器值完全相同（`0x000003B0`，即复位默认）；
     厂商设备树里 PL 侧只有 `reg_irq_path@0xA0000000` / `mipi_axi@0xA0010000` /
     `serial@0xA0020000` —— **全是 PS→PL 的从口**，没有任何 PL→PS 的证据。
  → **这块板的出厂 boot.bin 里 PS 没配 PL→PS 通路，我们的 `.bit` 改不了它**
  （PS-PL 接口是 PS 配置，由 FSBL/boot.bin 上电时写入）。

- **下一步（两条路，二选一）**：
  1. **换 boot.bin**（保留设计的 AXI DMA 架构）：用参考例程的 PS 配置 + Vitis 出 FSBL/PMUFW，
     再合上 ATF/U-Boot/bitstream 做成自己的 `boot.bin` 放回 SD 卡 kernel 分区。
     这样 `S_AXI_HP0_FPD` 就由我们使能，DMA 通路即可用。
     ⚠ 有风险：先备份原 `boot.bin`；板子能从读卡器恢复。
  2. **改走 PS→PL 唯一通路**（已证可用）：PS 直接把图像写进 PL 的 BRAM/寄存器，
     处理完再读回来，完全不用 AXI DMA、不用 DDR 图像区。
     ✅ 一定能通，但 RTL 和板端 C 都要改，每帧多约 30–50 ms。

### 3.7 PS→PL 方案（用户 2026-10-01 选定，进行中）

**新增 RTL：`rtl/img_proc_axil.v`** —— AXI4-Lite 寄存器版顶层，内部**原样例化
`img_proc_axis`**（灰度/统计/3x3/五模式映射一行没改），只是把输入/输出的 AXI-Stream
换成 PS 可读写的寄存器：

| 偏移 | 访问 | 名称 | 说明 |
| --- | --- | --- | --- |
| `0x00` | W | CTRL | `[2:0]`模式 `[15:8]`阈值; 写入即复位帧计数器 |
| `0x04` | R | STATUS | `[0]`in_ready `[1]`out_valid `[2]`proc_busy `[3]`frame_done `[15:8]`帧min `[23:16]`帧max |
| `0x08` | W | IN_PIX | 写一个 32bit 像素 `{8'h00,R,G,B}`; 只有 in_ready=1 才被接收 |
| `0x0C` | R | OUT_PIX | 读一个处理后的像素, 读走即弹出 |
| `0x10` | R | VERSION | `0x494D_5001` |

一帧的用法：写 CTRL → 循环 262144 次{等 in_ready, 写 IN_PIX} → 循环 262144 次{等 out_valid, 读 OUT_PIX}。

**BD：`bd/build_axil_design.tcl`**（PS + `axi_interconnect` + `img_proc_axil`，**不用 DMA**）。
- 工程 `vivado/img_proc_axil/`；比特流 **`bitstream/img_proc_axil.{bit,bin}`**。
- **寄存器基址 `0xA000_0000`**（4 KB）；综合/实现 **0 error / 0 CW**，
  **WNS +4.45 ns / WHS +0.008 ns**，BRAM 64/216、DSP 1/360。
- `validate_bd_design OK`；`s_axi` 接口被 Vivado 正确推断（模块时钟/复位端口必须叫
  `s_axi_aclk`/`s_axi_aresetn`，否则推不出 AXI 接口 —— 这条踩过）。

**⏳ 上板测试卡住的地方（需要人工处理）**：
2026-10-01 12:30 左右把 `img_proc_axil.bin` 加载进 PL 后跑一个**纯 Python 逐像素推拉**
的脚本，脚本里 262144 次寄存器轮询把板子 CPU 打满；**随后板子从网上掉了**
（`ping 192.168.1.254` 一直"无法访问目标主机"），SSH 连不上。
- **需要做的**：给板子**断电重启**（或复位）。重启后 PL 回到厂商设计、`fpgadrv` 自动加载，
  一切复原；我们的设计在 SD 卡上，不会丢。
- **重测注意事项**：⚠ **不要在板上用纯 Python 逐像素轮询**（262144 次 `mmap` 读太慢，
  会把板子拖死）。应该：
  1. 把这段推拉写成 **C 小程序**（板上自带 gcc，直接编），或者
  2. 在 PL 里加一个"推完一帧自动跑"的触发，减少寄存器往返。

**已经能用的东西（别丢）**：`bd/build_full_design.tcl` 一条 Tcl 出整套设计；
  `bitstream/` 下的 `.bit`/`.bin`；`rmmod fpgadrv` + `echo xxx.bin > /sys/class/fpga_manager/fpga0/firmware`
  就能换 PL；PS→PL 寄存器读写已验证。

### 3.8 整条链的现状（2026-10-06 实测口径，**这一段是"现在到哪了"的总表**）

| 环节 | 状态 | 关键数字 |
| --- | --- | --- |
| 网口收图（PS + TCP） | ✅ 真机 | 往返 27 ms 级；786 432 字节逐字节一致 |
| PC 端界面 | ✅ 真机 | 7 个模式 + flags + 阈值 + samples/ 五张 STARE 眼底图 |
| PL 预处理 7 模式 | ✅ 真机 | 每个模式 **0/786432 差异**（板端自检），灰度 20 ms / 伪彩 60 ms |
| AI 整网（U-Net 血管分割） | ✅ 真机 | 512×512 与 PLD golden **0/262144 逐字节一致** |
| AI 的 `ap`（PL 计算） | ✅ 已优化 | **531.99 → 353.7 ms（1.502×，conv XU=2）→ 306.9 ms（10-11，V12 + 真双口 BRAM，9.49）→ 304.5 ms（10-12，eltwise E2b，9.50）** |
| AI 的 `get`（读回） | ✅ 已优化 | **1113 → 181 ms**（首次落地那一轮）→ 复测稳态 **125 ms**；GDMA 原始带宽比是 **15.9×**（1290 vs 81 MB/s），端到端只快 6.1×。10-11 同一测试程序：V9b 90.0 → xu2dp **79.6 ms** |
| AI 的 `put`（写回） | ✅ 已优化 | 108 ms（CPU Normal-NC 写合并 1.08 GB/s，**换 DMA 反而慢 54 ms**）→ **75.2 ms**（10-11 双口 BRAM 后 PS 写 1.62 GB/s，9.49） |
| PL 比特流 | ✅ 真机 | 10-12 起 **xu2dp_e2b** `3bbd9396…`（9.50）；10-11 的 xu2dp `8f8c27fe…` 把 512 整网 566 → **473 ms**、128 档 37.8 → **32.0 ms**（9.49），E2b 再省 `ap` 2.4 ms、BRAM 188/216 |
| 512 全回传端到端 | ✅ 真机 | **1877 → 698.2 ms**（GDMA 2.1×，再叠 conv 1.29×） |
| 板端自检 / 图形界面 | ✅ 真机 | `Fz3b_BoardCheck` **31 项全 PASS、0 FAIL、0 SKIP**；GTK3 界面开机自启（重启验收过） |
| 开机固化 | ✅ 真机 | 四个 unit（bitstream / plbram / plbdma / 业务）+ 业务程序，断电重启自动恢复 |

---

## 四、下一步要做的事（2026-10-06 版）

> 历史条目 ⑦（PC 端模式选择）⑧（出比特流）⑨（上板验证）**都已闭环**：
> ⑦ 已完成、PC 侧参考实现按用户要求取消（只发图收图）；
> ⑧ 已由 `ai/bd/build_ai_sys.tcl` / `build_ai_sys_xu2.tcl` 一条 Tcl 从零出比特流解决；
> ⑨ 已上板，且现在有 `Fz3b_BoardCheck` 做板端自动回归。

> **10-12 现状**：🥇 双口 BRAM 已完成（9.49，整网 −16.5%）；🥈 eltwise 已完成（9.50，E2b，`ap` 只省 2.4 ms——
> Add 的收益早被双口拿走，剩下的 Concat 空间约 2 ms，不再投入）。**现在的第一顺位是 🥉 提频**（`ap` 304.5 ms × (1 − 101.4/148) ≈ −96 ms）。
>
> **10-11 现状**（保留）：🥇 已完成并上板验收（9.49，整网 −16.5%）。现在的第一顺位是 🥈 eltwise，其次 🥉 提频；
> 本机已有 Vivado 2025.2（`E:\AMD\Xilinx\2025.2`）。下面 🥇 一段保留为历史记录。

### 🥇 第一顺位：把张量 BRAM 改成**真双口**（解锁 V12）—— ✅ 10-11 完成（9.49）

- **为什么**：张量 BRAM 现在是**单口**（`blk_mem_gen Single_Port_RAM` + `axi_bram_ctrl`
  `SINGLE_PORT_BRAM=1`），每行至少 `512 读 + 512 写 = 1024` 次访问 → 硬件下限 ≈1024 拍。
  V9b 实测 `LOAD(548)+STORE(515)=1063` **已贴着下限**，所以"合并搬运算式"这条已经无路可走。
  双口后一拍能做 2 次访问 → 搬运砍到 ~550 拍/行 → **ap 354 → ~309 ms**。
- **为什么现在就能做**：**RAMB36 本来就是双口能力，块数一块不变**（已用 64 块，
  见 `docs/fpga_resource_utilization.md` §4），所以不额外吃 BRAM —— 那是唯一紧的资源（88.9%）。
- **代码已就绪**：`ai/opt_hls/src/conv_layer_v12.cpp.txt`（V9b + LOAD/STORE 合成一趟），
  **C 仿真 25 用例 / 737 695 字 / 0 差异**，**而且已经在 10-06 23:45 综合并导出成 IP 了**
  （见 9.29）—— **只差"用双口 BRAM 把整套系统建出来并上板"**。
- **⚠⚠ 一个必须知道的陷阱**：出口仓库 **`ai/build/conv_ip_xu2/` 现在装的是 V12，不是交付版 V9b**
  （V12 的导出覆盖了它，`csynth.rpt` 时间戳 `Tue Oct 6 23:45:55 2026`）。
  所以**现在直接重跑 `build_ai_sys_xu2.tcl`（"V9b 脚本"）会得到 V12 + 单口 BRAM 的组合** ——
  那既不是已交付的 V9b，也不是想验的双口版。**要重建 V9b 必须先**
  `copy ai/opt_hls/src/conv_layer_v9.cpp.txt → src/conv_layer.cpp` 再重新导出 IP。
- **BD 脚本也已就绪、10-06 23:47 已启动过一次构建，但没跑完**（详见 9.29 的状态补记）：
  `ai/bd/build_ai_sys_xu2dp.tcl` + `run_build_xu2dp.bat`；双口**确实生效了**
  （原语是 `TRUE_DP.SIMPLE_PRIM36`），但 `impl_1` 停在 Physical Synthesis、**没有比特流**。
- **所以第一步很具体**：**重跑 `ai/bd/run_build_xu2dp.bat` 让它跑完**，
  并顺手**删掉脚本里那行 `CONFIG.Write_Depth_B {16384}`**（真双口下没有这个参数，
  会留一条 `CRITICAL WARNING [BD 41-1276]`，挡住 `generate_bitstream` 的安全闸）。
  产物应落在 `vivado/ai_sys_xu2dp/`、`bitstream/ai_sys_xu2dp.{bit,bin}`、
  `vivado/ai_sys_xu2dp_util.rpt` / `_timing.rpt`。
- **然后**：上板 + **全回归**（`run_fullres.py` 的 0/262144 + `Fz3b_BoardCheck` 31 项），
  并重跑一次 `_memtest`/`_plbram_bench` 看**读**有没有变化（双口可能顺带影响 PS 侧通路）。
- **风险**：动 BD 会重新走一遍 PS 侧通路（历史上这里踩过位宽转换器丢写数据、
  FPD 主口地址窗口各半等坑，见 9.6）；且改完要**重新确认 CPU/DMA 与 IP 并发访问的行为**
  （本板读∥写会坏数据，见 9.26）。

### 🥈 第二顺位：`eltwise` IP（占 `ap` 剩下的 ~49 ms）—— ✅ 10-12 完成（9.50，实际只省 2.4 ms）

Add/Concat/MaxPool 走的是另一个 IP，占了优化后 ap 里剩下的 ~14%。它**同样是搬运受限的**
（一样吃单口 BRAM），所以大概率要等上面那条双口 BRAM 落地才有意义。结构完全不同，另开一轮。

### 🥉 第三顺位：PL 提频 101 → 148 MHz（**风险高**）

- **注意口径**：10-02 那时算的"省 167 ms / ap 占 68%"是 **conv 优化之前**的数
  （ap 531.4 / 整网 777.3）。**conv 落地之后 ap = 354.2 ms、板端整网 599.0 ms**
  ⇒ ap 占 **59%**，提频到 148 MHz 大约只省 **~112 ms**（≈ 新帧的 19%）。
  仍然是"动 ap 的最大一项"，但别再用 167 ms 那个数去立项。
- **但 PL 时钟由 FSBL/boot.bin 设定，加载 `.bit` 改不了**；2026-10-01 试过"改 BD 约束提频"
  上板无效（板上 `pl0_ref` 仍 101.376 MHz）。
- 真要做得**运行时写 `CRL_APB`**（有把板子挂死、要断电的风险），或换 boot.bin。
  **要单独找时间做、先想好回滚。** 详见 9.6 ⑤ 与第六节坑 20。

### 其它小杠杆 / 待办

| 项 | 收益 | 说明 |
| --- | --- | --- |
| DMA 直写张量（去掉 bounce memcpy） | 省 ~37 ms | 把张量换成**模块持有的物理连续可缓存区**再 mmap 给进程；干净、可独立做 |
| 减少条带数（BRAM 加到 ~344 KB） | 省 ~26 ms | 由"**IP 每次启动 ~100 µs**"反推；张量 BRAM 每 4 KB 占 1 块 RAMB36（256 KB = 64 块），**加到 344 KB 约需再吃 22 块**，而余量只有 24 块 ⇒ 几乎顶到天花板，要跟 eltwise/双口的需求一起算 |
| 加宽 `in`/`out` 到 256 bit | 理论上再砍一半搬运 | `ai/opt_hls/README.md` §9 列过：**要动 BD + PS 侧张量布局 + 互联位宽**，代价大、风险高（历史上位宽转换器丢写数据这条坑就是同源问题） |
| 低分辨率场景的另一条线 | 3.8× 已现成 | 32 档整帧 45 ms 里 AI 只占 4 ms，剩下 41 ms 是"TCP 收帧 23 + 展开 2.7 + **预处理 PL 往返 20**" → 该优化的是那趟预处理往返 |
| **未决账：`vessels` 口径** | — | 2026-10-06 服务端跑出 `vessels=16458/262144`，而 10-02 记录是 `35257`。**同一套代码/比特流与 PLD golden 是 0/262144 一致**，所以不是退化，更像客户端发的图/阈值口径不同。**没核**，用户可决定要不要查 |
| 文档待补 | — | `kmod/README.md` 还没有 `plbdma` 一节；`boot/README.md` 的固化清单还停在 4 件（**实际已是 5 件**，见 9.33） |

### ⛔ 明确不建议做（都已实测否掉，别再试）

XU=3（整板 DSP 会到 93%，超预算）｜给 `rows` 加第 4 个行槽（BRAM 已 88.9%）｜
**任何"把 m_axi 折进计算流水线"的变形**（V10/V11 已否掉两版）｜
搬运与计算重叠（硬件 + 条带成本双重否决）｜分组层间链式（GDMA 之后收益从 660 → ~60 ms）｜
HLS int8 打包（读侧 0 收益）｜可缓存映射（被 GDMA 覆盖）｜`put` 走 DMA（−54 ms，更慢）｜
NEON 宽读（实测慢一倍）。

---

## 五、关键设计决策（别推翻，理由都在文档里）

| 决策 | 理由 |
| --- | --- |
| **PC 端切模式**（不做按键） | 板上**没有用户按键**：`hardware_info.txt` 正文无引脚、无 `/dev/input`、设备树无 `gpio-keys`，三重确认 |
| **可以顶掉厂商比特流** | 用户明确同意不用 MIPI 摄像/串口那些功能 |
| **DDR 里是 32bit/像素**（`{8'h00,R,G,B}`） | AXI DMA 按 4 字节搬，3 字节/像素会**跨 beat 边界**，PL 对不齐 |
| **PS 做紧包 RGB888 ↔ 32bit 转换** | 同上；转换是一次 ~1 ms 的循环 |
| **PL 侧全帧缓冲**（2 Mb BRAM） | 归一化要全帧 min/max，纯流式做不到；缓冲一帧换"一次 DMA 就够" |
| **归一化用倒数表不用除法** | PL 里除法又大又慢；查表+乘法+移位，逐像素一次乘法 |
| **读出 1 像素/3 周期** | 帧 RAM 用单端口，三行各读一次 |
| **PC 端不做参考实现**，只负责收 FPGA 回传的图 | 用户 2026-10-01 决定。所以没有"逐像素自动比对"这条验证手段 |
| **模式寄存器在推流前写** | `img_proc_axis` 的 `ctrl` 是组合直连的，晚写就会用上一帧的模式 |
| **`--echo` 开 DMA 时回传处理后的图、否则回显原图** | 让 `--no-dma` 的纯软件联调路径行为完全不变（PC 界面现在就是这么用的） |
| **PS 当搬运工、PL 当计算器**（张量常驻 PS 内存，按条带搬进 BRAM 算） | 三条硬约束逼出来的：PLD 的 IP 挂 `ps_HP` 而从口未使能 / 片上 BRAM 装不下整网张量 / 不动 boot.bin。代价是"同一份中间结果被搬两趟"，这正是 10-06 之前 63% 时间的来源 |
| **算子代码一行不改**（只把 `m_axi` 从 `ps_HP` 指向片上 BRAM） | 用户 2026-10-01 要求"尽量原样复制少改"；副产物是**换模型接口**：换 `network.txt` + `params/*.bin` → `gen_unet_c.py` → 重编译，**RTL 与驱动都不用改** |
| **读回走 GDMA（`plbdma.ko`），写回仍走 CPU** | 读 1113→181 ms（端到端 **6.1×**；GDMA 的**带宽**是 CPU 的 15.9×，别把这两个数混用）；**写侧走 DMA 反而慢 54 ms**（CPU 的 Normal-NC 写合并已经 1.08 GB/s，DMA 还要先 memcpy 进 bounce） |
| **LOAD / COMPUTE / STORE 必须各自成环** | V11（只折回写）慢 1.52×、V10（读写都折）慢 1.63×；而且方向混在一起时**每次访问 ≈8 拍**（纯读/纯写都是 1 拍/字）。见 9.28 |
| **PS(CPU/DMA) 与 PL 的 IP 不能同时访问同一片张量 BRAM** | 三次实测都坏数据（2026-10-02 A-ov、10-02 用新映射重试、10-06 用 DMA）；**把 CPU 换成 DMA 也救不了** ⇒ 在控制器/互联那一层。见 9.26 |
| **不做 HLS int8 打包** | 读的成本 = **访问次数 × 198 ns**，与一次搬多少字节无关 → 只压位宽不改次数 = **0 收益**（微基准实测） |
| **不做"搬运与计算重叠"** | ① 硬件挡住（读∥写坏数据）；② 就算修好也是白做：双缓冲把条带数 826→1809，**每次 `ap_start` 有 ~100 µs 固定开销**，多付 115 ms 正好抵掉省的 110 ms |
| **不做分组层间链式**（曾经的首推） | GDMA 之后收益从 660 ms 掉到 **~60 ms**（同样的字现在只要 ~48 ms 就能读下来），工程量大而收益小 |
| **`Fz3b_BoardCheck` 直接链接 `tcp_image_rx/src` 的真实驱动源码** | 不抄一份 —— 检测的就是真正在跑的那份代码；换平台只改 `bc_pl.c` |

**DDR 布局**（在厂商预留的 **1 GB no-map 区 `0x40000000`** 里）：

| 偏移 | 大小 | 用途 |
| --- | --- | --- |
| `+0x000000` | 1 MB | 输入图（512×512×4） |
| `+0x100000` | 1 MB | 输出图 |
| `+0x200000` | 4 KB | 状态块 |

**控制字**：`ctrl[2:0]` = 模式 0..4；`ctrl[15:8]` = 阈值（默认 128）；`[7:3]` 保留。

---

## 六、踩过的坑（**别重踩**）

1. **`/dev/mem` 映射（`VM_PFNMAP`）不能让内核碰**
   - `recv()` **写进去** → `EFAULT (errno 14)`
   - `send()` **从里面读出来** → **内核 oops，进程一起死**
   - 用户态自己 load/store/memcpy **可以**
   - → 必须**两级缓冲**：先收进普通内存，再 memcpy 过去；回发也从暂存区发
   - → 读回校验**必须用 mmap，不能用 `dd`**（`dd` 也 EFAULT）

2. **`0x30000000` 不能当缓冲区**：它在 Linux 的 `System RAM` 里，
   而该内核 **`CONFIG_STRICT_DEVMEM` 没开**（完全不拦）→ 写下去会**真写坏内存**。
   用 `0x40000000`（厂商已保留的 no-map 区）。

3. **`signal()` 有 `SA_RESTART`** → `accept()` 被信号打断后自动重启，收不到 `EINTR`，
   systemd 停服务会卡 90 秒再 SIGKILL。**必须用 `sigaction` 且 `sa_flags = 0`**。

4. **Verilog：所有 reg 声明放模块开头**。先用后声明会变成隐式 1 位 wire，比较全错。

5. **块 RAM 的写不能放进带异步复位的 always 块** → 无法推断成 BRAM，
   会退化成触发器实现（2 Mb 用 FF 装直接爆）。要单独放无复位块。

6. **流式 3×3 窗口的移位寄存器，每行开头必须预置**。否则每行第 0 列取到上一行残留值
   → 点态模式下**左边一条黑线**。综合 0 warning，上板才看得见。

7. **板子时钟停在 2021 年**，`make` 会因时钟偏移误判"无需重建" → **要 `make clean`**。

8. **tkinter 起不来是 `TCL_LIBRARY` 冲突**：从 Vivado 环境启动 Python 会继承
   `Vivado/tps/tcl/tcl8.6`，与 Python 自带的版本不匹配。`run.bat` 里已把
   `TCL_LIBRARY`/`TK_LIBRARY` 钉到 Python 自己的 `tcl/` 目录。

9. **pip 默认源在这台机器上极慢会卡死**（Pillow 7 MB 卡 5 分钟+，能把 MCP 调用拖超时）
   → 用 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

10. **刚开机 1 分钟内板子忙于起桌面**（4 核 A53 load ~1.2），首次连接可能超时，等一分钟。

11. **`mcp__vivado__*` 长阻塞调用会杀掉整个 Vivado 会话**（超时即终止）。
    综合/实现要用异步 + 短轮询；`run_tcl` 里避免长 `after`。

12. **Tcl 里 `[...]` 是命令替换**，写脚本生成 Python 时要用 `{}` 包住。

13. **DMA 往返必须先架 S2MM、再开 MM2S**。S2MM 是"水槽"，MM2S 一推流就有人接着；
    反过来 MM2S 先跑，PL 的 `m_axis` 没人接会背压、甚至静默丢像素。
    两路都要按 `IMG_DDR_BYTES`（1 048 576，32bit/像素）搬，**不是**紧包的 786 432。

14. **把图展开成 32bit/像素必须在启动 DMA 之前**。旧代码把这次拷贝放在 MM2S **之后**
    —— 一直没暴露，因为生产上一直跑 `--no-dma`；一旦真开 DMA，搬走的就是上一帧/垃圾。
    这类"只在某条路径上才现形"的顺序错误，改 DMA 时务必对着数据流走一遍。

15. **`send()` 不能从 `/dev/mem` 映射里读**（内核 oops）。所以回传"处理后的图"时，
    要先 `pack_ddr_to_rgb888()` 把它拷进**普通内存**再发 —— 和坑 1 是同一件事的两个面。

16. **`test/` 的主机测试会随主程序一起过期**。`host_test_rx.c` 直接 `#include` 那份
    `tcp_image_server.c`，主程序加了新的 libc 用法（如 `sigaction`）就可能让测试编不过。
    2026-10-01 修好了它（补垫片 + 正例改 `verbose=1` 才能拿到整幅 CRC）；
    以后改主程序记得**顺手跑一次** `test/build_host_test.bat`。

### 六之二、2026-10-06 新踩的坑（**这一批更贵，务必看**）

17. **`/dev/mem` 映射区绝不能用 `memset`**（10-02 P4-full 第一次上板 `Bus error`、
    而且**一行日志都没有**）。glibc 的 `memset` 对大块对齐目标会用 **`DC ZVA`**，
    而 `DC ZVA` 只对 Normal 内存合法 → 对 Device 内存是未定义行为 → ARM64 直接 **SIGBUS**。
    **`memcpy` 反而安全**（走 `ldp/stp`）。补零改成自己写的 volatile 32 bit 循环。
    顺带：**崩溃时 stdout 块缓冲会把日志全吞掉** → 先 `setvbuf(..., _IOLBF, 0)`。

18. **⛔ 重编 PL 之前必须先确认两个 HLS 核 idle**（`CTRL` 的 bit2 = `ap_idle`）。
    在 conv 还有任务在飞（`ap_idle=0`）时编程 → **PS 的 AXI 互联等不到响应而挂死
    → 整板 ping/SSH 全断，只能断电重启**（本机 JTAG 又看不到目标）。
    `ai/net/_board_load_bin.py` 现在有这道闸；**烧完还要回读板上 md5 比对**
    （这招还顺带抓到过一次"脚本在板上崩了、其实根本没烧进去"的假数据）。
    另：**跑板端自检前先 `systemctl stop tcp-image-server`**，否则服务和自检抢 PL，
    自检会直接中止、或者 `ap` 数字全是垃圾（实测跑出过单节点 86680 µs 这种离谱值）。

19. **判"并发是不是安全的"必须造出"读∥写"场景。** 我做过一个探针：在 `ap` 期间并发
    一笔 DMA 读 + 逐字节校验，得到 `ok=114 / bad=0` 且整网 golden 还 PASS
    → **据此判断"并发安全"是错的**：它读的是 **IP 只读的输入区**，测的是"读∥读"；
    真实场景是"DMA 读上一段输出 ∥ IP 写自己的输出"= **读∥写**。
    **读∥读通过完全没有参考价值。**

20. **PL 时钟由 FSBL/boot.bin 设定，加载 `.bit` 改不了**。2026-10-01 把 BD 约束从 100 改到
    148 MHz，时序收敛、比特流也出了，**上板往返一点没变**（`pl0_ref` 仍 101.376 MHz）。
    真要提频只能改 boot.bin 或运行时写 `CRL_APB`（**有把板子挂死的风险**）。

21. **模块里绝对不能有浮点**：`pr_info("...%8.2f...", (double)x)` 直接编译失败
    （`'-mgeneral-regs-only' is incompatible with floating-point argument`）。
    MB/s 一律用整数算到两位小数：`mb_x100 = bytes*1e9/ns/10000;` 再 `%llu.%02llu` 打印。

22. **内核模块几处命名/签名坑**（报错信息都很绕）：
    - 模块参数/变量**别叫 `dev_name`** —— `<linux/device.h>` 里有同名 inline 函数；
    - 自己起的名字别和 `file_operations` 的成员同名；
    - `.open/.release` 的参数顺序是 **`(struct inode *, struct file *)`**，写反只报
      "initialization from incompatible pointer type"，很容易看漏；
    - 模块被进程打开时持有引用计数 → **`rmmod` 会失败**，要先把用它的服务停掉
      （`plbdma` 就被 `tcp-image-server` 常年开着）。

23. **部署脚本 `make clean; make` 的隐患**：编译一旦失败，`make clean` **已经把
    `tcp_image_server` 删了** → systemd 服务进入 `activating` 重启循环，**板子上的业务就断了**。
    （10-06 因为漏了一个 `now_us()` 前向声明踩到。）
    → **改完先本地交叉编译 `-fsyntax-only` 过一遍**（`ai/net/_prof_all.py check`），再去板上 `make clean`。

24. **`pgrep -x tcp_image_server` 永远匹配不上** —— Linux 的 `comm` 只留 **15 字符**，
    实际名字是 `tcp_image_serve`，所以"手动起的服务有没有起来"的判断一直是**假阴性**
    （连 `pkill -x` 也一直没杀掉进程）。判断端口请用 `ss -ltn | grep 5060`。

25. **GBK 控制台会当场打断脚本**：板上输出里的 `∘`/`µ` 之类字符会让 Python 的 `print`
    抛 `UnicodeEncodeError`，**整个脚本中断**。→ 脚本开头
    `sys.stdout.reconfigure(errors="replace")`，subprocess 也带 `errors="replace"`；
    **板上 printf 一律用 ASCII**。

26. **崩溃 + 块缓冲 = 什么都看不到**：stdout 是管道（全缓冲），一崩就什么都不剩，
    看起来像"卡住了"。→ 用 **`stdbuf -oL -eL`** 跑，并且 `echo RC=$?`
    （**139 = SIGSEGV**）。10-06 就是靠它才区分出"不是挂死，是崩了/算错了"。

27. **`.bat` 里绝对不能有中文**（又踩一次）：cmd 按 GBK 读 UTF-8 文件，
    连 `rem ... 真 FF 寄存器` 这种注释都会把 `FF` 当成命令执行，**日志文件都不会建**。
    `.bat` 一律纯 ASCII、不含 `&|<>`。

28. **HLS 工具坑**（`ai/opt_hls/`）：
    - `#pragma HLS ARRAY_PARTITION ... complete`（**不带 dim**）在 Vitis HLS 2025.2.1 上
      **只切首尾两维**，中间维会退化成 LUTRAM（`ram_2p`，1 读 1 写口）→ 不要按直觉理解
      "complete = 全维度都变寄存器"，**要去 storage report 核实**；
    - **HLS 不跨循环共享硬件**：两个循环里的乘法器/requantize 会各来一份，硬件直接翻倍；
    - `csynth` 中途失败时**报告文件是上一次留下的**（按 mtime 判断真伪）；
    - `vitis-run.bat` 结尾带 `exit`；
    - **被强杀的 vitis/clang 会占着工程目录**，导致下次 `open_project -reset` 静默中止
      → 用 `PROJ_TAG` 给工程名加后缀即可绕开。

29. **GTK3 / 板端界面的三个坑**（见 `Fz3b_BoardCheck/docs/界面说明.md`）：
    - `Gtk.Window.get_accel_group()` **在 PyGObject 里不存在**（C API 没暴露）→ 快捷键走
      `key-press-event`；
    - `ComboBoxText.remove_all()` 会**触发 `changed` 回调** → 回调里重建列表 = 无限递归；
    - `flock -n` 抢不到锁就直接退出 → 屏幕上什么都没有；改 `flock -w 60` 等锁，
      并且"停"的时候要**先杀 launcher 再杀 python**、轮询等进程真的没了。

30. **⚠ LXDE 的用户级 autostart 会"整体覆盖"全局文件，不是合并。**
    `~/.config/lxsession/LXDE/autostart` 一旦存在就**只用它**，不存在的才用
    `/etc/xdg/lxsession/LXDE/autostart`。第一版安装脚本只往用户级写了自己那一行 →
    真装上去 `lxpanel`/`pcmanfm`/`xscreensaver` **会一起消失、桌面变空白**。
    改成"用户级存在就追加到它，不存在就追加到全局"，并留 `.bak-fz3b` 备份。

31. **`/dev/plbram` 与 `/dev/mem` 的 mmap 偏移约定是反的**：
    `/dev/plbram` **只能传 offset 0**（物理窗口由模块参数固定），
    `/dev/mem` **必须传物理地址**。弄混就 `EINVAL`。
    另外 `/dev/plbram` 的 `len` 不能超过模块参数 `size`（默认 256 KB），超了也 `EINVAL`。
    Fz3b_BoardCheck 里统一走 `bc_pl_file_offset()`，**不要各自手写**。

32. **`snprintf(x, ..., "%s", x)` 是自引用**，实测会把内容重复好几遍（PPM 的 note 字段
    被印成三遍）。aarch64 的 gcc 13 比主机 gcc 6.2 严，是它揪出来的 —— 先拷到临时缓冲。

33. **长 Vivado 批处理构建会被"会话结束"打断，而且 `.done` 标记文件判断不了成败。**
    10-06 23:47 启动的 `run_build_xu2dp.bat`（真双口重建）**停在 Physical Synthesis、
    没有比特流**，但 `vivado/ai_sys_xu2dp.done` 却实实在在地躺在那儿
    （那是更早一次运行留下的 —— `.bat` 里 `.done` 的写入**不看 `ERRORLEVEL`**）。
    **判成功的唯一可靠依据：日志末尾出现 `EXITCODE 0`，且产物文件真的存在**
    （`bitstream/*.bit|.bin`、`vivado/*_util.rpt|*_timing.rpt`）。
    ⇒ 长构建一律**后台跑 + 用文件工具读日志**（见 PC 环境那行的套路），别在前台等。

34. **`blk_mem_gen` 切到真双口（`True_Dual_Port_RAM`）后 `Write_Depth_B` 参数就不存在了**，
    再设它会吃一条 `CRITICAL WARNING: [BD 41-1276] Cannot set the parameter Write_Depth_B`
    —— 而 CW 会**挡住 `generate_bitstream` 的前置安全检查**（要么删掉那行，要么 `force`）。
    深度由地址段范围推出，本来就不用手设（同 9.6 ⑤ 里 `MEM_DEPTH` 的脾气）。

---

## 七、常用命令速查

```bash
# ---- 板端：服务与状态 ----
systemctl status ai-sys-bitstream plbram plbdma tcp-image-server --no-pager
cat /sys/class/fpga_manager/fpga0/state          # 期望 operating
ss -ltn | grep 5060                              # 判断服务在不在(别用 pgrep -x, 见坑 24)
journalctl -b -u ai-sys-bitstream --no-pager     # 看那次开机烧的比特流 + 校验
journalctl -u tcp-image-server -f                # 看程序日志

# ---- 板端：业务程序 ----
./tcp_image_server -p 5060 -a 0x40000000 --no-dma -x 0xA0000000 --echo -v -n 0   # 现行参数
UNET_NOGDMA=1 ./tcp_image_server ...             # 强制读回退回 CPU(对照用)
UNET_BRAM_MEM=1 ./tcp_image_server ...           # 强制走 /dev/mem(不用 plbram)

# ---- 板端：自检(首选回归手段) ----
cd /root/Fz3b_BoardCheck && make clean && make CC=gcc
./build_board/board_check -i /root/test.ppm --auto-thr --save --ai --ai-res 512 --ai-golden-x golden/v512_x.bin --ai-golden-final golden/v512_final.bin --save-baseline
systemctl stop tcp-image-server                  # ⚠ 跑自检前先停业务, 免得抢 PL

# ---- PC 端：一键驱动板子 ----
python Fz3b_BoardCheck/scripts/deploy_run.py run -i /root/test.ppm --auto-thr --save --ai
python Fz3b_BoardCheck/scripts/reboot_verify.py          # 唯一能证明"开机自启生效"的办法
python ai/net/_boot_install.py check|install|now|reboot  # 开机固化
python ai/net/load_ai_sys.py                             # 手动烧一次(等价于跑板上那个 load 脚本)
python ai/net/_prof_all.py state|ai|e2e|sweep            # 从头复测全部步骤(每档 3 帧)
python ai/net/run_fullres.py                             # golden 逐字节回归(0/262144)
python ai/net/_board_load_bin.py <bin>                   # ⚠ 带 idle 闸 + md5 回读, 别手敲 fpga_manager
python ai/net/_gdma_deploy.py check|mod|golden|deploy|boot|quick   # plbdma 部署

# ---- 编译 ----
# 板上原生编译(推荐)
scp -r src makefile root@192.168.1.254:/root/tcp_image_rx/
ssh root@192.168.1.254 'cd /root/tcp_image_rx && make clean >/dev/null; make CC=gcc'
# 本机交叉编译(⚠ 改完先拿它 -fsyntax-only 过一遍, 再去板上 make clean, 见坑 23)
make CC="E:/fpga/vivado/2025.2.1/gnu/aarch64/nt/aarch64-linux/bin/aarch64-linux-gnu-gcc.exe"

# ---- 主机功能测试(不碰硬件, 改完主程序跑一次) ----
test\build_host_test.bat                   # 期望最后一行 TOTAL FAILURES: 0

# ---- PC 端界面 ----
run.bat                                             # 开界面
run.bat --image D:\pics\cat.jpg                     # 预载一张图
python imagelink.py 192.168.1.254 5060 bars --repeat 5        # 命令行往返自测
python imagelink.py 192.168.1.254 5060 im0003 --mode 1 --thr auto --flags proc,mask,stats
python imagelink.py 192.168.1.254 5060 bars --ai-res 512 --save out.png

# ---- HLS 算子(改 conv 时) ----
# 批处理入口(本机是 Vitis for HLS, 没有 vitis_hls)
E:/fpga/vivado/2025.2.1/Vitis/bin/vitis-run.bat --mode hls --tcl <script>
cd ai/opt_hls && _run_hls.bat 2 csim      # 逐位等价(25 用例, 要 0 差异)
cd ai/opt_hls && _run_hls.bat 2 synth     # 综合看 II / 资源
cd ai/bd && run_build_xu2.bat             # 重出比特流(只换 conv 的 IP 仓库, PS/BD/地址一字不改)

# ---- RTL 综合检查(每写完一个模块就跑) ----
# (在 Vivado Tcl Console 里)
read_verilog rtl/*.v
synth_design -top img_proc_axis -part xazu3eg-sfvc784-1-i -mode out_of_context
```

---

## 八、如实说明：哪些没验证

> 这一节在 2026-10-06 重写过 —— 10-02 版写的"RTL 没上板、没做实现"**已经过期**，
> 现在**整条链都上板验证过了**。下面是**截至 2026-10-06 仍然没验证**的东西。

**已经真机验证过的（可以当事实用）**：PL 预处理 7 个模式每个 0/786432 差异｜
AI 整网 512 与 PLD golden 0/262144 逐字节｜AI 32×32 档 0/1024｜
board_check **31 项全 PASS / 0 FAIL / 0 SKIP**｜GTK3 界面开机自启（重启验收过）｜
网口收图 786432 字节逐字节一致｜实现后时序 **WNS +1.819 / WHS +0.012，All constraints met**。

**仍然没验证 / 有已知局限的**：

- **`ai/opt_hls/src/conv_layer_v12.cpp.txt`（LOAD/STORE 合并版）**：
  **C 仿真 0 差异（25 用例 / 737 695 字）+ 已在 10-06 23:45 综合并导出成 IP**，
  **但没上板**。⚠ 它的导出把 `ai/build/conv_ip_xu2/` **覆盖**了（交付版 V9b 的 IP 仓库已不在）；
  配套的真双口 BD 构建**跑到 Physical Synthesis 就断了、没有比特流**。见 9.29。
- **`eltwise` IP 完全没动过**（Add/Concat/MaxPool，占 ap 里剩下的 ~49 ms）。
- **PL 提频没生效**（板子仍 101.376 MHz）；改 BD 约束那条路已证无效，运行时写 `CRL_APB` 没试过。
- **`Fz3b_BoardCheck` 的 14 条未验证项**（AI 只在 512 档对过 golden、
  `--ai-src proc` 没跑过、`--compare-baseline` 的板上路径没跑过、
  只跑过 `test.ppm` 没跑过 bars/checker、界面的交互路径基本没点过、
  「自动重跑」刻意没开……）**全部列在 `Fz3b_BoardCheck/测试报告_2026-10-06.md` §7**，
  **别当成已验证**。
- **`kmod/README.md` 还没有 `plbdma` 一节**；`boot/README.md` 的固化清单还停在 4 件
  （实际已是 5 件）。**文档欠账，接口以本文 9.25 / 9.33 为准。**
- **未决账**：服务端 `vessels=16458/262144` 与 10-02 记录的 `35257` 不一致 ——
  整网输出与 golden 是 0/262144 一致，所以不是退化，但**口径差异没查**。
- **本机的 Verilog skill（`readable-verilog-generator`）只装了 `SKILL.md`**，
  没有 `scripts/` / `references/`，**它的质量门禁跑不了**。

---

# 九、AI 识别（眼底血管分割）—— 2026-10-01 新增，**已完成整网上板验证**

> 本节是 2026-10-01 到 10-02 的工作（9.1 ~ 9.21）。详见 `docs/ai_inference_design.md`、
> `docs/ai_full_resolution_strip_plan.md`。
> **10-06 的全部轮次在后面「九（续）」9.22 ~ 9.34** —— 那一节里的 9.7「下一步」
> 已被 9.22~9.29 取代，别照着 9.7 的字面去做。

## 9.1 目标与范围（用户要求）

把 `E:\fpga\my_project\PLD_share_20260929\` 里 PLD 医学影像工程的 **U-Net 血管分割**接进本项目：

- **预处理（现有 6 个模式）保持不变** → 之后做 **AI 识别**；
- 识别**在 PL 里做**（不是软件推理）；**不动 Linux 系统**；
- **上位机可以选择回传什么**（预处理图 / 血管掩膜图 / 掩膜叠加图 / 数值统计）；
- **预留接口导入新的训练参数**（换权重就能换识别功能）。

## 9.2 素材与三条硬约束

**素材**：PLD 的 `medical_fpga/`（U-Net 推理 HLS 源码 + 前后处理 HLS + Vivado 系统 + JTAG 上板记录）
和 `lwnet/`（模型与量化，STARE 眼底血管，Dice≈0.76）。整网 **30 个节点**（20 卷积 + 5 Add + 2 Concat + 2 MaxPool
—— ⚠ **这是全项目沿用的口径**；`network.txt` 实际是 30 行含第 1 行 `x INPUT` ⇒ **计算节点 29 个**，对数的时候别对不上）、
3 级 512→256→128→256→512、通道 8/16/32、混合位宽(多 INT8、2 个 INT16)、
输出 int8 logit，**≥ -3 判为血管**。层表 `deployment/unet_network/vectors/network.txt`。

**三条硬约束（决定了架构）**：

1. **PLD 的 IP 挂在 `ps_HP0/HP1`（PL→PS 从口）读写 DDR —— 本板出厂 PS 配置一个都没使能**，
   那套在我们板上跑不了；
2. **片上 BRAM 装不下整网张量**（ZU3EG 全片 ≈0.97 MB，512×512 下一层 8 通道就 4 MB）；
3. **不动 Linux/boot.bin**。

## 9.3 最终架构：PS 当搬运工，PL 当计算器

```
PC ─TCP─► PS(Linux)
  ① 收图 → ② 推给 PL 的 img_proc_axil 做 6 模式预处理（不动）→ ③ 结果写进片上 BRAM
  ④ 逐层跑 U-Net：PS 把该层输入/权重搬进 BRAM → 配 conv/eltwise IP 寄存器 → 启动 →
     轮询 ap_done → 把输出搬回 DDR
  ⑤ 掩膜/叠加/统计 ─TCP─► PC（按上位机选择）
```

**关键决策：算子代码一行不改** —— PLD 的 `conv_layer`/`eltwise_layer` 保持 `m_axi` 主设备，
只是把 `m_axi` 从 `ps_HP` 指向**片上 BRAM**；控制口走 AXI4-Lite，由 PS 驱动。
**权重也由 PS 从文件推进 BRAM —— 这就是"换训练参数"的接口**（换模型 = 换 `network.txt` +
`params/*.bin` + 板端调度，RTL 不用改）。

## 9.4 进度

| 项 | 状态 |
| --- | --- |
| 两个 HLS IP 用 2025.2.1 从**未修改的 PLD 源码**导出 | ✅ `ai/build/{conv_ip,elt_ip}/.../export.zip` |
| 整套系统 BD 脚本（一条 Tcl 从零出比特流） | ✅ `ai/bd/build_ai_sys.tcl` |
| 比特流 | ✅ `bitstream/ai_sys.{bit,bin}`，**WNS +2.254 / WHS +0.011** |
| **P3：单层卷积上板，与 golden 逐字节比对** | ✅ **mismatch = 0 / 1008 字节（0 差异）** |
| 图像通路回归（整体重建后） | ✅ 4/4 PASS |
| **P4：30 个节点串成整网（32×32 bring-up）** | ✅ **30/30 节点 bit-exact（两组输入均 0 差异）** |
| **P5：接进上位机链路（flags + 掩膜/叠加/统计）** | ✅ **真机端到端跑通**（8 组 flags + 真实眼底图 6 组） |
| **P6：提速 + 换权重接口** | ✅ **C 驱动 3.1×；换权重真机验证（掩膜取反、逐值可预测）** |
| **P4-full：全分辨率 512×512（条带 strip+halo）** | ✅ **262144/262144 逐值一致，2.1 s/帧；分辨率可调** |

## 9.5 地址表（**改代码前必看**）

| 主口 | 宽度 | 从机 | 地址 |
| --- | --- | --- | --- |
| `M_AXI_HPM0_FPD` (GP0) | 32 | `img_proc_axil_0` | `0xA000_0000` |
| " | | `conv_layer_0` 控制 | `0xA001_0000` |
| " | | `eltwise_layer_0` 控制 | `0xA002_0000` |
| `M_AXI_HPM1_FPD` (GP1) | **128** | 张量 BRAM（`bram_ctrl`） | **`0xB000_0000`**（128 KB） |

IP 的 `in/out/params` 指针也用 `0xB000_0000`（同一块存储）。

## 9.6 ⭐ 试过的方向与经验（**这一节最值钱**）

### ① 位宽转换器会丢写数据（踩了三次，最后靠"物理分开"解决）

只要 **128 位的 IP 主口和 32 位的 PS 主口出现在同一个互联里**，Vivado 会把互联内部宽度撑到 128 位，
32 位那条就被插 `auto_us`/`auto_ds` —— 而**这类转换器在本工具链/器件上只让 16 字节对齐地址的写数据通过**：

- 现象：读正常、写全不落；写 64 个字只有 16 字节对齐的 16 个进得去。
- 试过**合并互联 + 32 位 BRAM**（fix2）→ 生成日志里 PS 那条又出现 `auto_us` ✗；
- 试过**非对称双口 BRAM**（A 口 128 给 IP / B 口 32 给 PS，两个控制器）→ 写不进去 ✗
  （很可能因为控制器的 `BRAM_PORTA` 必须连到 BRAM 的 `BRAM_PORTB` 这种**跨名连接** ✗）；
- ✅ **正解**：**让每条路径位宽自洽、一个转换器都不出现** ——
  控制走 32 位的 HPM0，数据单独走 **128 位的 HPM1**，BRAM 只做 128 位单口。

### ② ZynqMP 两个 FPD 主口在 PL 里**各占一半地址**

```
Valid apertures are {<0xB000_0000 [256M]>, ...}
```
**`M_AXI_HPM0_FPD` 管 `0xA000_0000`~`0xAFFF_FFFF`，`M_AXI_HPM1_FPD` 管 `0xB000_0000`~`0xBFFF_FFFF`。**
把挂在 HPM1 上的东西放在 `0xA0xxxxxx` → `assign_bd_address` 报 "no available apertures"，段被自动排除。
（我一开始误判成"PS 没套板级预设"，套上 FZ3B 预设后报错才变明确 —— **报错里会直接列可用窗口，别猜**。）

### ③ 绝不要访问 PL 里没有从机的地址

PS 访问一个没有从机应答的 PL 地址时，**ZynqMP 不给 DECERR，而是永久挂起** →
CPU 卡死、**板子掉线（需断电重启）**。我踩过：探测脚本沿用了旧地址 `0x8000_0000`（AI 已挪走）。
→ **规矩：探测脚本先只读一次确认有响应，再写**（`ai/test_p3_layer.py` 已这么做）。

### ④ 在旧 BD 上就地改 PS 配置很危险

- 改了 PS 配置（开 `M_AXI_HPM1_FPD`）后，**地址空间没跟着重建** → 报"没有可用窗口"，白折腾；
- 加 IIP 的互联 MI 后，**新 MI 的 `ACLK`/`ARESETN` 必须显式补连**，否则 `validate` 直接失败；
- ✅ **正确做法：改结构就用脚本从零重建**（`ai/bd/build_ai_sys.tcl`），别在旧 BD 上打补丁。

### ⑤ BD 里其它坑

- `axi_bram_ctrl` **只有 1 个 AXI 从口**（它的 A/B 双口是给拼宽/拼深用的，不是两个独立访问口）；
- `blk_mem_gen` 必须设 **`use_bram_block=BRAM_Controller`** 才被认成配套存储器，否则报 MASTER_TYPE 不匹配；
- `CONFIG.MEM_DEPTH` 是只读、由地址段范围推出，**改完地址要 `validate_bd_design -force` 才重算**。

### ⑥ 工具链与离线准备

- 本机是 **Vitis for HLS**（没有 `vitis_hls`），批处理入口
  **`E:/fpga/vivado/2025.2.1/Vitis/bin/vitis-run.bat --mode hls --tcl <script>`**；
- PLD 的 `vectors/` 不在分享包里，但 **golden 张量和 `parameters.npz` 都在**，
  用 `unet_conv/generate_vectors.py` **离线重生成功**（35 个用例，20 个真实层与 golden 核对过）；
- 真实层用例 3–12 MB 塞不进 128 KB BRAM，**合成用例 `syn_*` 只有 0.8–40 KB** 正好用。

### ⑦ 后台跑长任务

`exec cmd /c <bat> > log 2>@1 &`（Tcl 后台符 `&`）**可行**；用 cmd 的 `start "" /b`
会把 Tcl 的 exec 卡死并把会话拖死（踩了两次）。
**改完 BD 的比特流必须重出**（module reference 的 OOC run 要一起 reset，否则比特流一字不变）。

## 9.7 下一步

1. **P4-full：全分辨率 512×512**。真实层张量是 MB 级、塞不进 128 KB BRAM →
   必须**按条带**（strip + halo，IP 的 `in_h/in_w`/指针都是运行时参数，不用改代码）。
   ✅ 先决条件已具备：**30 节点调度逻辑已在 32×32 上逐节点验证正确**（9.9），
   所以这一步只剩"条带 + 丢 halo 行"的簿记要做。
   或**加大 BRAM / 张量放 DDR**（后者要解决 PS↔PL 搬运带宽）。
2. **P5：上位机协议加 flags**（`magic+mode+flags+...`，默认只回预处理图，**老 PC 端行为不变**）；
   回传血管掩膜图 / 掩膜叠加图 / 数值统计。
3. **P6：提速**（PS 自带 GDMA 从 DDR 直搬 PL、选择性片上缓存）。

## 9.8 已确认可用 / 别丢

`ai/bd/build_ai_sys.tcl`（一条 Tcl 从零出整套比特流）；`bitstream/ai_sys.{bit,bin}`；
`ai/test_p3_layer.py`（单层上板验证，含"先读确认"防护）；`ai/build/*/.../export.zip`（两个 IP）；
`ai/build/.../vectors/`（测试向量）；`ai/p3_postmortem.py`、`ai/bram_scan.py`（数据通路诊断）。
`ai/net/`（**P4 整网调度与验证全套**，见 9.9）。

**板上现状**：PL 里是我们的 `ai_sys`（含图像处理 + AI 子系统）；`tcp-image-server` 跑新版、
参数 `-x 0xA0000000` 走 PS→PL；**重启即回原厂 PL**。

## 9.9 ✅ P4 完成：30 个节点串成整网（32×32，逐节点 bit-exact）

**一句话**：把整网按 **32×32** 跑，**所有张量都放得进那片 128 KB 张量 BRAM**，
PS 逐节点调度；结果与**一字未改的 PLD HLS 源码**（`conv_layer.cpp`/`eltwise_layer.cpp`，
在 Vitis HLS 里跑 `csim_design` 得到）**逐字节一致**。

### 为什么是 32×32（关键取舍）

- `in_h/in_w` 是运行时参数 → **整网能在任意分辨率跑**（权重不变），所以不存在"必须 512"。
- 512×512 时 L0 一层 4 MB，128 KB BRAM 装不下 → 必须切条带。
- 32×32 时**全部张量峰值只有 5120 字 / 80 KB** → **一个新比特流都不用出**，直接用 P3 的 `ai_sys.bin`。
  这是本轮最省事、也最安全的选择：先把"调度链"证对，再去啃条带。

### 结果（两组不同输入）

| 组 | 输入 | 结果 |
| --- | --- | --- |
| 1 | im0002 抽样（相位 0） | **30/30 节点 bit-exact，0 差异** |
| 2 | im0002 抽样（相位 8，2597/8192 lane 不同） | **30/30 节点 bit-exact，0 差异** |

- 覆盖算子：3×3 conv / 1×1 conv / **2×2 转置 conv** / Add / **Concat** / **MaxPool** + 张量缓冲复用。
- 输出非退化：`first_block_2` 3726 个不同值（-15500…22941）；`final` 95 个不同值、12.4% 非零。
- 加速器耗时 **3.0 ms / 30 节点**（32×32，不含 Python 搬数据）。

### 复现（`ai/net/`，细节见该目录 README）

```
python gen_small_vectors.py        # 512 向量 -> 32x32 向量 + plan.json(BRAM 分配方案)
run_csim_small.bat                 # 10 秒出逐节点 golden
python run_p4.py                   # sftp 上传 -> 板上跑(unet_board.py) -> 拉回 -> 逐节点比对
```

### 本轮新踩的坑（记下别再踩）

1. **读 VERSION 要看偏移 `0x10`**：`img_proc_axil` 偏移 `0x00` 是**只写的 CTRL**，读回来恒 0。
   我一开始拿偏移 0 去判"PL 是不是我们的设计"，得到 0 就以为比特流没加载成功 —— **虚惊一场**。
   正确的指纹是 `0xA0000010 == 0x494D5003`。
2. **`.bat` 里 `rem` 注释挡不住 `&`**：`rem ... & ...` 会被 cmd 当成命令分隔符执行 → 直接报错退出。
   .bat 一律写**纯 ASCII、不含 `&|<>`**；而且 `.bat` 里的中文注释会被按 GBK 解析成乱码命令。
3. **`/dev/mem` 的 `mmap` offset 必须页对齐**（4 KB），`0xFF5E00C0` 这种会 `EINVAL`；
   改成映射 `0xFF5E0000` 再读偏移 `0xC0` 就对了。
4. **`file delete` 可能报 permission denied**（文件被别的进程占着）→ 脚本里一律 `catch` 住，
   并用 `>>` 追加而不是先删。

### 下一步

**P4-full**：把同一套调度器改成"**条带 + halo**"跑 512×512。要点：
只有 `out_h` 个输出行需要取 `out_h+2` 行输入（3×3），喂给 IP 时它是按"图像边界"零填充的，
所以**只保留中间有效行、丢掉首尾 halo 行的输出**即可。IP 不用改一行代码。

## 9.10 ✅ P5 + P6 完成：上位机链路、掩膜/叠加/统计、C 驱动、换权重

**一句话**：AI 接进服务进程（C 驱动），上位机可按 **flags** 选回传
**预处理图 / 血管掩膜 / 掩膜叠加 / 数值统计**；整网从 Python 的 9 ms 降到 **2.87 ms**；
**换一层权重就能换功能**，且改动逐值可预测。细节与全部脚本见 `ai/net/README.md`。

### 9.10.1 板端：驱动换 C，协议加 flags

- **`src/unet_ai.c`**（+ 生成的 `src/unet_net_data.c`）在服务进程内跑整网，
  不再 fork Python：**2.87 ms vs 8.99 ms，快 3.1×**。
  上板自检 `test/unet_selftest.c`（`make CC=gcc unet_selftest`）：两组向量都
  `mismatch = 0 / 1024 -> PASS`。
- **控制头 v3**（`image_proto.h`）：板子**先读 2 字节 magic 再决定读几字节** ——
  `0x4D43`(v2, 4B) 与 `0x4D44`(v3, 8B: mode+flags+aiparam) 都认，老客户端不用改。
- **flags**：b0 预处理图 / b1 掩膜 / b2 叠加 / b3 统计块 / b4 只算不传图。
  **下行顺序固定**：`[64B 统计块]` → 预处理图 → 叠加图 → 掩膜图。
- `src/ai_view.c`：下采样、掩膜上采样（0/255）、红色叠加、**4 邻域连通域计数**、64B 统计块。

### 9.10.2 上位机

- `proto.py` 加 v3 头/flags/`expected_images()`/`unpack_stats()`，导入时自检；
- `imagelink.py` 加 `--flags`、`--ai-src`、`--ai-thr`、`--v2`、`--save-{proc,mask,overlay}`；
  **第三个参数可以直接是图片文件路径**（不再只能是内置图样）；
- `app.py` 加"回传内容"复选框、"AI 输入"下拉、"AI 阈值"、右图"显示"选择器、AI 统计行；
- **图片格式**：Netpbm 全家桶 **P1–P7 原生读写（不依赖 Pillow）**，
  含 ASCII/二进制、`maxval` 1..65535、头里注释、CRLF、PBM 位打包；
  写侧支持 `.ppm/.pgm/.pbm/.pam/.pnm`（PGM 用与 PL 相同的 `(77R+150G+29B)>>8`）。
  非 512×512 一律拉伸，**已是 512×512 的不重采样**。

### 9.10.3 真机实测（真实眼底图 `tcp_image_ui/test.ppm` = im0002）

| 用例 | 板端 recv | 预处理 | AI 整网 | 回传 | PC 端总耗时 |
| --- | --- | --- | --- | --- | --- |
| v2 老头（旧客户端） | ~21 ms | 20 ms | — | 11 ms | **55.8 ms** |
| proc,mask,stats | 21 ms | 20 ms | 3 ms | 25 ms | **74 ms** |
| proc,mask,overlay,stats | ~22 ms | 20 ms | 3 ms | ~50 ms | **94.9 ms** |
| noimage,stats（只算不传） | 22 ms | 20 ms | 3 ms | ~0 | **42.2 ms** |
| mode3(伪彩) proc,mask,stats | ~21 ms | **55 ms** | 3 ms | 25 ms | **111.5 ms** |

AI 输出 `vessels=37/1024 (3.6%) comps=4 logit[-86..14]`，叠加图红色落在**视盘/血管汇聚区**。
AI 输入换成"预处理结果"→ `324/1024 (31.6%)`（输入源开关确实生效）；阈值 -3→0 → 37→28。

**P6 提速的诚实结论**：32×32 下 AI 只占整帧 ~4%，**瓶颈已经不是 AI**，
而是 TCP 收图 + PS↔PL 预处理 + 多幅回传。设计文档里的"GDMA 搬运/片上缓存"
是为**全分辨率**准备的，现在还用不上。

### 9.10.4 换权重 = 换功能（真机验证）

`make_model_b.py` 把 `final` 层的 `mult` 与 `offset` 同时取负 → **logit_B == -logit_A（逐值精确）**；
离线校验 1024/1024 全对，掩膜从 3.6% 翻到 98.7%、只有 2.3% 重合。
`run_model_swap.py` 真机部署模型 B 后实测 `vessels=1011/1024, logit[-14..86]` —— 与预测一致，
掩膜就是 A 的补集；跑完自动恢复模型 A。

**换模型的接口**：换 `network.txt` + `params/*.bin` → `gen_unet_c.py` 重生成
`src/unet_net.h`/`src/unet_net_data.c` → 重新编译部署。**RTL 与驱动都不用改。**

### 9.10.5 本轮踩的坑

1. **服务"假死"**：服务一次只服务一个连接，半死的客户端会把它**永久堵住**
   （后到的连接只能排 backlog，表现为"板子不响应"）。已修：给已连接 socket 加
   **`SO_RCVTIMEO = 10 s`**，超时就断开回到 accept。
   诊断过程：**journald 从 9/1 起就不再记录**，只能手动前台跑服务抓 stdout 才看清。
2. **`.bat` 里的中文会被 cmd 按 GBK 解析成乱码命令**（又踩一次）→ .bat 一律纯 ASCII。
3. **`pkill -f '<含自身命令行的模式>'` 会把自己杀掉**（命令行里含同样字符串）→ 用 `pkill -x <名字>`。
4. **`taskkill /F /IM python.exe` 会把 MCP 服务自己的 python 也杀掉**，导致 Vivado 会话掉线。
   —— 别用按镜像名全杀。
5. **比对失败时打印整个 786 KB 的 bytes** 会刷出 5 MB 日志、把 MCP 传输撑爆 →
   打日志前先截断。

### 9.10.6 下一步（唯一的大件）

**P4-full：512×512 按条带（strip + halo）**。前提条件已经具备
（调度逻辑在 32×32 上逐节点验证过），只剩"条带 + 丢 halo 行"的簿记。
做完之后掩膜才真正有意义（现在的掩膜是 32×32 放大 16 倍的方块）。

## 9.11 ✅ P4-full 完成：全分辨率 512×512（条带调度）

**一句话**：张量从"全放片上 BRAM"改成"**常驻 PS 内存 + 按条带搬进 BRAM**"，
于是 512×512 也能跑；结果与 **PLD 自己的 golden 逐值一致**，**2.1 秒/帧**。
完整设计与调试日志在 **`docs/ai_full_resolution_strip_plan.md`**。

### 9.11.1 行依赖（条带正确性的全部依据，从算子源码推出来的）

| 算子 | 输出行 y 需要 | halo | 处理 |
| --- | --- | --- | --- |
| 3×3 conv | `y-1, y, y+1` | 上下各 1 行 | 喂 `S+2` 行，**丢首尾各 1 行** |
| 1×1 conv | `y` | 无 | 喂 `S` 行 |
| 2×2 转置 | `y>>1`（一行产两行） | 无 | 喂 `n` 行，产 `2n` 行，全有效 |
| Add / Concat | 逐像素 | 无 | 喂 `S` 行 |
| MaxPool | `2y, 2y+1` | 无 | 喂 `2S` 行 |

**图像真正的上下边界由我们自己补零行**（缓冲第 j 行 = 真实输入行 `y0-1+j`，越界整行写 0），
**不依赖 IP 自己的边界填充** —— 否则真边界会被多填一次零。

### 9.11.2 ⛔ 本轮最大的坑：`/dev/mem` 映射区不能用 `memset`

第一次上板两种档位都 `Bus error (core dumped)`，而且**一行日志都没有**
（stdout 块缓冲把日志吞了 → 先加 `setvbuf(..., _IOLBF, 0)` 才看得见）。

根因：glibc 的 **`memset`** 对大块且对齐的目标会用 **`DC ZVA`**（数据缓存清零指令），
而 `DC ZVA` 只对 Normal 内存合法 —— 对 `/dev/mem` 的 **Device 内存是未定义行为**，
ARM64 上直接 **SIGBUS**。崩点恰好是 3×3 卷积给条带补零行的第一次 `memset`。

> **规矩：`/dev/mem` 映射区绝不能用 `memset`。** `memcpy` 反而安全（走 `ldp/stp`，对齐即可）。
> 补零改成自己写的 **volatile 32bit 循环**（零行只在每节点首/末条带各一次，慢路径无所谓）。

对照：全程用 volatile 32bit 循环，功能正确但 32×32 档从 9 ms 掉到 25 ms（4 倍）。

### 9.11.3 结果

**档位回归**（先拿 32×32 跟 P4 已知 golden 对账，把"条带逻辑"和"512 正确性"分开）：
```
shift=4 -> input 32x32, output 32x32
timing: 1 iter(s) in 9.1 ms       compare: mismatch = 0 / 1024    ->  PASS
```

**全分辨率**（判据 = PLD `vectors/images/im0002/` 的 input.bin + golden/final.bin）：
```
shift=0 -> input 512x512, output 512x512
run: 2106523 us   timing: 2106.57 ms/frame
compare: mismatch = 0 / 262144  ->  PASS
logit range [-107..100], non-zero 261623/262144
```

**分辨率旋钮**（板端 `--ai-res`，客户端可在 `aiparam` bits[4:2] 逐帧覆盖）：

| 分辨率 | AI 整网 | 血管像素 | PC 端总耗时 |
| --- | --- | --- | --- |
| **512** | **2076 ms** | 16458/262144 (6.3%) | **2185 ms** |
| 256 | 480 ms | 4317/65536 (6.6%) | 568 ms |
| 128 | 120 ms | 761/16384 (4.6%) | 205 ms |
| 64 | 31 ms | 120/4096 (2.9%) | 116 ms |
| 32 | 8.3 ms | 37/1024 (3.6%) | 94 ms |

**效果**：512 的掩膜（`ai/net/e2e_out/fullres_mask_512.png`）是一棵**真正的血管树**，
叠加图里红色几乎精确贴在真实血管上。

### 9.11.4 瓶颈与下一步

2.1 s 对应约 80 MB 的张量搬运 → **有效带宽 ~38 MB/s**，正好是 PS 对 Device 内存访存的量级。
**瓶颈是 PS↔BRAM 搬运，不是 PL 算力。** 想再快：
① **加大 BRAM**（现只用 128 KB，还有 ~56 块 RAMB36 空闲；条带高度近似线性于 BRAM）；
② 降分辨率（现成旋钮）；③ NEON 搬运（memcpy 已是向量实现，空间有限）。

## 9.12 ✅ 阈值分割的门限做成上位机可调 + Otsu 自动预设

**背景**：用户要"上位机加入阈值分割和伪彩映射的阈值可调，并根据实际图片给出合适的预设值"。
查 PL 后发现：**`thr` 只被 mode 1（阈值分割）用**（`gray >= thr ? 255 : 0`），
**mode 3（伪彩映射）是 `jet[gray]` 直接查表，压根没有阈值参数**；
而 mode 1 的阈值当时只是**板端命令行** `--thr`，上位机够不着。
用户拍板：**伪彩先不动**（不改 RTL、不重出比特流），只把 mode 1 的阈值做进协议。

### 9.12.1 协议加 v4 控制头（12 字节）

板子"先读 2 字节 magic 再决定读几字节"，三级都认：

| 版本 | magic | 长度 | 布局 |
| --- | --- | --- | --- |
| v2 | `0x4D43` | 4 B | mode |
| v3 | `0x4D44` | 8 B | mode + flags + aiparam |
| **v4** | **`0x4D45`** | **12 B** | mode + flags + aiparam + **thr** + 保留 |

老客户端照旧可用（v2/v3 不带 thr 时用板端 `--thr` 的默认值 128）。

### 9.12.2 上位机：两级 Otsu 自动预设

`tcp_image_ui/imageio.py` 里算灰度直方图（**权重与 PL 完全一致** `(77R+150G+29B)>>8`），
然后在同一张直方图上跑**两次** Otsu：

* **一级 = 背景/物体**（全图）→ test.ppm 上 **71**（黑边 vs 眼底盘）
* **二级 = 物体内部**（把一级以下的桶清零再跑）→ **115**（血管 vs 盘内背景）

预设填一级（通用图也稳），二级写进界面提示里供参考。
界面加了「阈值分割门限(mode1)」输入框 + 滑块 + 「自动(Otsu)」按钮，载入图片即自动算。

### 9.12.3 实测（真机，test.ppm，mode 1）

逐帧下发阈值，与上位机**独立复算**的 `gray >= thr ? 255 : 0` 逐字节比对：

| 阈值 | 白像素 | 与 PC 复算差异 |
| --- | --- | --- |
| 40 | 191757 (73.1%) | **0 字节** |
| 71（一级 Otsu） | 163773 (62.5%) | **0 字节** |
| 100 | 111859 (42.7%) | **0 字节** |
| 160 | 4887 (1.9%) | **0 字节** |
| 200 | 0 (0.0%) | **0 字节** |

**5 个阈值全部 0 差异**，白像素随阈值单调变化。

### 9.12.4 顺手修掉的一个自己的错

写提示文案时把"黑"算成了 `gray <= thr`，而 PL 的判据是 `>= thr` 为**白**
——`gray == thr` 那个桶（1421 个像素）被算反了。改成 `hist[:thr]` 后，
上位机算的 163773 个白与板上实测**完全对上**。

## 9.13 自动算阈值时排除背景像素（纯黑/纯白 → 再自动定灰度下限）

用户先后提了两条：① "排除全黑或者全白的像素"；② "可以，自动定灰度最小值"。

### 9.13.1 先排纯黑/纯白（灰度为 0 或 255）

这类像素往往只是背景（图像黑边、过曝白块、拼图留白），会**只**影响挑阈值所用的统计。
⚠ **对 test.ppm 其实没用** —— 实测它**灰度为 0 的只有 2 个像素、为 255 的一个都没有**，
黑边是"近黑"（灰度 2~30 的斜坡）。所以这条单独拿出来几乎无效。

### 9.13.2 再自动定"灰度最小值"（`auto_gray_floor`）

在暗半区找**第一个合格的谷**当背景右沿。判据四条：

| 判据 | 作用 |
| --- | --- |
| 是局部极小 | 谷才可能是分界 |
| 左侧暗区 ≥ 全图 5% | 挡掉"一圈细黑边"的假谷 |
| 谷深 ≤ 两侧峰较小者的 30% | 挡掉平坦直方图（渐变图） |
| **暗区贴在图像边框上**（≥30% 边框像素是暗的） | 背景总在边上；**画面正中的深色物体不该被剔掉** |

**为什么"左侧 ≥5%"这条是必须的**：眼底图的暗端不止一个结构 ——
"细黑边(0-3) → 谷(~8) → 深色背景环(16-31) → 深谷(~35) → 内容(48+)"。
只看第一个谷会切在 **8**（只削 3.7%，背景环原封不动）；筛掉 8 之后才轮到 **35**
（正好是背景环的右沿，削 26%）。

### 9.13.3 效果（离线 + 真机）

| 图 | 自动下限 | 一级 Otsu | 不排背景时 |
| --- | --- | --- | --- |
| **test.ppm（眼底）** | **35（削26%）** | **99**（血管显形） | 71（整盘全白无血管） |
| test_blackborder.ppm（纯黑圆窗） | 2 | 101 | 54（整盘全白） |
| 合成"近黑背景 60%" | 4 | 100 | 2（被拽到近黑处） |
| 居中深色物体 / 渐变图 / 棋盘 | 0 | — | 判据④/③ 生效，不乱切 |

真机出图：`ai/net/e2e_out/thr_auto.png`（自动 99）vs `thr_71.png`（旧的 71）。
CLI 加了 `--gray-min N` 手动覆盖（0 = 不排背景）。（**订正**：这个开关其实**只在
`imageio.auto_threshold(..., gray_min=N)` 这个**函数参数**上有，**`imagelink.py` 并没有
`--gray-min` 命令行选项**；`tcp_image_ui/README.md` 里写成 CLI 参数是笔误。）

### 9.13.4 造测例踩的坑（两回）

1. 第一版合成图造的是 **50/25/25 三级**，排除前后 Otsu 都给 60 ——
   那张图"内容内部"的类间方差更大，Otsu 本来就不会被拽走。
   要**背景占得够多**才显形。
2. 第二版造"暗背景铺开在 10..40"，也是**排除前后都给 100** ——
   铺开的暗区与内容分得开，Otsu 本来就选对。要**近黑且集中**（灰度 2）才会被拽。
3. 平滑窗口一开始取 ±3，把"一个桶宽的尖峰"抹平了，导致谷的判定失准 → 改成 **±1**。
4. 搜索范围一开始被"一级 Otsu"卡死 —— 合成图里一级 Otsu 正好落在暗峰(2)上，
   谷(4)在范围外 → 改成**整个暗半区 [1,127]**。

## 9.14 第 7 个处理模式「边缘叠加」（模式 6）

用户要求："6 个预处理选项再加一个边缘增强之后的边缘图层和原图叠加的效果"。

### 9.14.1 为什么不用改 RTL

查 `mode_map.v`：`mode` 是 3 位、`case` 只列了 0..4，其余落到 `default`（灰度直通）。
要真在 PL 里加一路"边缘叠加"就得改 RTL + 重出比特流（~40 分钟综合实现 + 回归）。

但**没必要**：PL 的模式 2 本来就输出 Sobel 边缘图，而**原图 PS 手里就有**（`rx_buf`）。
所以：

```
edge = PL 跑模式 2 的输出        # 复用，不改一行 RTL
out[i] = (edge[i] >= thr) ? 白 : 原图[i]      # PS 侧就地叠加
```

这与 **AI 掩膜叠加**（`ai_view_overlay_rgb`）是同一套做法，架构上是自洽的。
代价是多花一趟 PL 往返 —— 实测 57.7 ms（模式 2 本身 57.0 ms），可忽略。

### 9.14.2 实现

| 位置 | 改动 |
| --- | --- |
| `image_proto.h` | `CTRL_MODE_MAX` 5→6；加 `CTRL_MODE_EDGE_OVERLAY=6`、`CTRL_MODE_EDGE_PL=2` |
| `ai_view.c/.h` | 新增 `ai_view_edge_overlay(base, edge, out, thr, n)`，**支持就地**（先读后写同一像素） |
| `tcp_image_server.c` | 模式 6 时把发给 PL 的模式按 **2** 发，拿回边缘图后就地叠加；`--thr` 说明补一句 |
| `proto.py` | `MODE_NAMES[6]`、`CTRL_MODE_MAX`、自检里加"模式清单必须连续覆盖 0..6" |
| `imagelink.py` | `--mode` 校验到 6，错误提示直接列全 7 项 |
| `app.py` | 模式下拉自动多一项；阈值标签随模式变（模式 6 = "边缘阈值(mode6)"）；换模式自动重算推荐值 |
| `imageio.py` | 新增 `sobel_magnitude()` / `edge_threshold()`（**PC 端 Sobel**，给模式 6 挑推荐阈值） |

### 9.14.3 模式 6 的"自动阈值"用分位数，不用 Otsu

边缘幅度直方图**极度偏向 0**（大多数像素是平坦区），Otsu 会被压得很低、画得满屏都是边。
所以模式 6 的自动值取 **Sobel 幅度的 92 分位**（"让约 8% 的像素判成边缘"），再取下限 24。

### 9.14.4 真机验证（2026-10-02）

判据特意用**板子自己**的边缘图在 PC 上复算叠加，把 PC/PL 的 Sobel 差异排除在外：

```
mode2 边缘图    57.0 ms
mode6 边缘叠加  57.7 ms
叠加图与 PC 复算差异 = 0 / 786432 字节  ->  PASS
画成白边的像素 = 22162 / 262144 (8.5%)
参考: PC Sobel vs 板子 Sobel —— 逐值相同 99.2%，阈值判决一致 100.0%
```

出图：`ai/net/e2e_out/edge_overlay_mode6.png`（眼底原图 + 白边血管/病灶轮廓）、
`edge_mode2.png`（纯边缘图）。脚本 `ai/net/run_edge_test.py`。

## 9.15 A1：加大张量 BRAM 128 KB → 256 KB（2026-10-02）

用户选的第 1 个提速项。**做完并上板验证。** 细节见 `docs/ai_full_resolution_strip_plan.md` §9。

- **账**：128 KB 版用 160/216 块 RAMB36（img_proc 64 + 张量 32 + conv 39 + eltwise 25），
  只剩 56 块；翻倍到 256 KB 约需 64 块 → **192/216 (88.9%)**；再大到 384 KB 会超。
- **改动**：`ai/bd/build_ai_sys.tcl`（`Write_Depth_A 8192→16384`、地址段 `128K→256K`、
  `validate_bd_design -force`、**加 `STEPS.WRITE_BITSTREAM.ARGS.BIN_FILE true`**）；
  `ai/net/gen_unet_c.py` 默认 16384；`src/unet_net.h` → `16384u`。**RTL 一行没改。**
- **综合实现**：0 error / 0 warning / 0 CW；**WNS +2.013 / WHS +0.0115**；BRAM 192/216。
  `.bin` md5 `9f141b1b…`（旧 `3e9051a4…`，备份 `bitstream/_pre_a1/ai_sys_128kb.bin`）。
- **上板（`ai/net/run_fullres.py`）**：512 **2106.6 → 1946.8 ms**、32 档 9.07 → 8.62 ms，
  两档都 `mismatch = 0` **PASS**，logit 与改前逐值不变。服务已重编部署（`ai_us=1948708`）。
- ⚠ **只有 7.6%**：输出搬运量与条带高度无关、halo 只在输入侧、params 拷贝占比 0.9%
  —— 事前用 `ai/net/_a1_estimate.py` 算出来是 5.6%，实测 7.6%。
  **A1 是"便宜安全但收益个位数"的一步**；真正的大杠杆在"降搬运量（int8 打包）"
  或"提搬运算力（Device 强序 → 可流水映射）"，见 `ai_full_resolution_strip_plan.md` §9.4。
- **顺手修的脚本 bug**：`write_bitstream` 默认只出 `.bit`，脚本拷 `.bin` 会失败；
  已加 `BIN_FILE` 属性。（另：`file copy` 拼目标名时 `file extension` 自带点，
  别写成 `ai_sys.[file extension $f]` —— 会拼出 `ai_sys..bin`。）

## 9.16 第 0 步：整网时间分解（2026-10-02，实测）——**结论出乎意料**

给 `src/unet_ai.c` 加了插桩（`unet_stats_t` 扩字段 + 可按节点 dump），
`unet_selftest` 打印，跑法 `ai/net/_profile_run.py`。细节见 `ai_full_resolution_strip_plan.md` §10。

**512×512 整帧 1948.8 ms 的分解**：

| 段 | 时间 | 占比 | 吞吐 |
| --- | --- | --- | --- |
| **get BRAM→PS** | **1112.0 ms** | **57.1%** | 85.5 MB → **76.9 MB/s（208 ns/字）** |
| **ap PL 计算** | **531.4 ms** | **27.3%** | — |
| **put PS→BRAM** | **287.9 ms** | **14.8%** | 117.2 MB → **407 MB/s（39 ns/字）** |
| zero + other | 17.6 ms | 0.9% | — |

- **搬运确实是主瓶颈**（71.8% vs 计算 27.3%）—— 老判断成立；
- **但读占 57%、写只占 15%，读比写慢 5.3×** —— 是 PS 读 Device 内存的
  强序代价（每次读一个完整 AR→R 往返），**不是数据量问题**；
- 早先按"80 MB / 38 MB/s"估的带宽**偏低**，实测写 407 MB/s、读 77 MB/s；
- 搬运字数与 `ai/net/_a1_estimate.py` 模型**逐位吻合**（out 5 341 184 完全一致）。

**👉 下一步最优解 = A-ov（计算/读回重叠，双缓冲）**：纯软件、不用重出比特流，
把 ap(531 ms) 藏到 get+put 后面 → 预期省 **~450–530 ms（23–27%）**。
**A1 加大 BRAM 的真正价值在这里兑现**（多出来的 128 KB 正好给双缓冲用）。
A3a（内核模块让读可流水，省 ~800 ms 但风险高）与 A3b（int8 打包，省 ~550 ms
但要重导 HLS）排在后面。

## 9.17 A-ov（读回与计算重叠）试做了 —— **被硬件挡住，已回退**（2026-10-02）

按 9.16 的分解，重叠"读回"(1112 ms) 与"计算"(531 ms) 理论能把计算藏起来。
**实现了、上板了，但结果是错的。** 细节见 `ai_full_resolution_strip_plan.md` §11。

**设计**：输出缓冲 ×2、输入仍 ×1（因为 get >> put，重叠组装没意义），
循环重排成 `组装(k) → start(k)异步 → 拉回(k-1) → wait(k)`。

**决定性判据**：

| 版本 | 512 耗时 | 与 golden |
| --- | --- | --- |
| A1（不重叠） | 1949 ms | **0/262144 PASS** |
| A-ov（重叠） | **1626 ms** | **253787/262144 FAIL** |
| A-ov 布局但 `UNET_NOAOV=1`（只关重叠） | 1992 ms | **0/262144 PASS** |

第三行**布局/条带数(1207)/搬运字数完全相同**，只把读回挪回 wait 之后 → 就 PASS。
⇒ **布局和双缓冲都对，坏在"CPU 在 IP 运行期间访问同一片 BRAM"这件事本身。**

**根因（推断）**：这是本工程**第一次** CPU 与 IP 同时访问同一片 BRAM（以前从不并发）。
两个 master 同时打 `ai_data_ic`(axi_interconnect 7SI→1MI) → `bram_ctrl`，
数据就坏（输出饱和到 ±127）。最像 **并发读的 RID 路由 / 单口 BRAM 读写切换**处理不住。
重叠本身**确实有效（1626 vs 1992 = 18%）**，但数据不能用。

**现状：已回退到单缓冲（A1 语义，1949 ms，逐值一致）**；板端服务已重编部署。
A-ov 代码存档 `ai/net/_unet_ai_aov_overlap_reference.c.txt`。

**想解锁只能改 BD**（要重出比特流 + 全回归，约 30–40 min，**不保证有效**）：
① `blk_mem_gen` 改真双口 + `axi_bram_ctrl` `SINGLE_PORT_BRAM=0`（最值得先试）；
② 数据通路换 `SmartConnect` 高性能模式；③ 张量 BRAM 拆两块、各挂一个控制器
让 CPU 与 IP 走不同从机。

## 9.18 A3b（int8 打包）**实测无效，已否决**（2026-10-02）

先做了一次微基准（`ai/net/_memtest.py`），同代码同样 16B 步长各搬 16 MB：

```
A 128bit: 16384 次 x 16B | 197.7 ns/次 | 80.9 MB/s
B  64bit: 32768 次 x  8B | 197.7 ns/次 | 40.5 MB/s
```

**每次访问耗时完全相同 → 读 BRAM 是"访问次数/延迟"主导，不是字节数主导。**
所以"每字仍 8 通道、只把 16 位压成 8 位"的 int8 打包**一点都不会快**（访问次数不变）。
整帧反推也吻合：读 5.34M×198ns=1057ms、写 7.32M×39ns=285ms（实测 1112 / 288）。

再定位这 198 ns（`ai/net/_memtest2.py`）：**PL BRAM 197.7 ns** vs
**PS 自己的 DDR(也走 /dev/mem) 102.9 ns** vs **普通可缓存内存 4.2 ns**。
⇒ **~103 ns 是 `/dev/mem` 的 Device 映射语义**，**~95 ns 是 PL 通路**；可缓存快 47×。

**结论：继续提速要打"每次访问 198 ns"本身，不是"搬多少字节"。** 剩下三条路：
① 每字塞 16 个 int8 通道（访问次数减半，整体 ~19%，但要改两个 HLS 算子 + 全链路重做，工程量最大）；
② **改映射：内核模块 `ioremap` 成 Normal-NC/可缓存**（搬运 ~2×，整体 ~36%；但板上**没有
kernel build 目录**，要搞内核源码+config 交叉编译）；
③ **PS 自带 GDMA**（板上已加载，22 个通道）做搬运，支持 burst（同样受"没 build 目录"限制）。
细节见 `ai_full_resolution_strip_plan.md` §12。

## 9.19 内核模块改映射（`plbram.ko`）—— **代码就位，等内核源码**（2026-10-02）

用户选了"内核模块改映射"这条路。**已经把不依赖内核源码的部分全部做完并放到板上。**

**思路**：`/dev/mem` 给的是 Device（强序、不可缓存）映射 → 实测**每次访问 103 ns
（连读 PS 自己的 DDR 都这么慢）**。换成 **Normal-NC（写合并）** 后 CPU 可以有多笔
未完成访问、允许合并 → 读应当显著变快。**因为不缓存，不需要任何 cache 维护，
也没有和 PL 的一致性问题**（这也是不选"可缓存"的原因：用户态没法执行 `DC IVAC`）。

**已经写好的**（`tcp_image_rx/kmod/`，已上传到板上 `/root/tcp_image_rx/kmod/`）：
- `plbram.c` —— ~90 行模块：`misc_register` 出 `/dev/plbram`，mmap 时把 vma 的
  `vm_page_prot` 换成 `pgprot_writecombine()` 再 `remap_pfn_range()`；
  窗口由模块参数 `base`/`size` 决定（默认 `0xB0000000` / 256 KB）。
- `makefile`（kbuild 三行）、`build_on_board.sh`（**在板上原生编译** —— 板载
  **gcc 7.5.0** 正好配 4.14 内核，完全避开工具链不匹配）、`README.md`（完整步骤）。
- 基准程序 `ai/net/_plbram_bench.c` / 编排脚本 `ai/net/_plbram_setup.py`
  （`check` / `build` / `status` / `bench` 四个模式）。

**板上的关键事实（都已确认）**：内核 `4.14.0-xilinx-v2018.3`、**`CONFIG_MODVERSIONS`
未开、`CONFIG_MODULE_SIG` 未开** → 编第三方模块很容易（不需要 `Module.symvers`、
不需要签名）；4K 页、39 位 VA；rootfs 还剩 2.4 GB。

**⏳ 唯一的阻塞：内核源码树。** 板上没有 build 目录、本地工作区也没有源码。
（**补记（10-02 之后）**：这句在当时成立，后来源码拿到了 —— 板上解在 `/root/linux-xlnx`，
**本机也留了一份** `E:\fpga\my_project\_kernel_src\linux-xlnx-xlnx_rebase_v4.14_2018.3\`，
命令行见第七节。）
需要 Xilinx **linux-xlnx** 对应 2018.3 的 tag（形如 `xlnx_rebase_v4.14_2018.3`），
解开后 `make ARCH=arm64 kernelrelease` 必须打印 **`4.14.0-xilinx-v2018.3`**
（目录里不要有 `.git`，否则 `LOCALVERSION_AUTO` 会加 `-g<hash>` 后缀、vermagic 对不上）。
拿到后放板子 `~/linux-xlnx`，然后 `python ai/net/_plbram_setup.py build` → `bench`。

**如果基准显示读真的快了**，再把 `src/unet_ai.c` 的 BRAM `mmap` 切到 `/dev/plbram`
（一处改动），重跑 `run_fullres.py` 确认 golden 仍逐值一致并记新耗时；
顺带可以看 **A-ov（重叠）能不能复活**（`ai/net/_unet_ai_aov_overlap_reference.c.txt`）。

## 9.20 `plbram.ko` **做出来了** —— 写快 2.7×、整体 **−9.4%**（2026-10-02）

用户提供内核源码后完成。细节见 `ai_full_resolution_strip_plan.md` §14、`kmod/README.md`。

**三个坑**：① 板上**没有 `bc`**（Kbuild 靠它生成 `timeconst.h`）→ 写了个 Python 的
`bc` 替身（`kmod/bc`，把 timeconst.bc 的算法原样实现 + 数值自检）装到
`/usr/local/bin/bc`，**内核树一行没改**；② 4.14 里没有 `misc_unregister` →
是 `misc_deregister`；③ 其余顺利（板上 gcc 7.5.0 原生编译，vermagic 完全一致）。

**结果：只有"写"变快**

| 目标 | 操作 | Device | Normal-NC |
| --- | --- | --- | --- |
| PL BRAM | 读 | 197.8 ns | **197.7 ns（不变）** |
| PL BRAM | 写 | 39.5 ns | **14.8 ns（2.67×）** |
| PS DDR | 读 | 102.7 ns | **102.9 ns（不变）** |
| PS DDR | 写 | 9.4 ns | **3.2 ns（2.9×）** |

⇒ **非缓存读就是一次完整往返、不流水，与映射属性无关**（连 PS 自己的 DDR 也一样）。
Normal-NC 只帮写。**读那 57% 靠改映射救不了。**

**整帧**：512 **1950.9 → 1767.9 ms（−9.4%）**，put 287.9→107.9 ms，读/ap 不变；
**golden 仍 0/262144 PASS**；服务端到端 `ai_us` 1948708 → **1765383**、
`vessels=35257/262144` 与改前完全一致。32 档 8.88 → 7.98 ms。

⚠ **写合并的正确性保护（别删）**：Normal-NC 下写可能还在 CPU 写缓冲里，而 `AP_START`
走**另一个口**（HPM0）→ 必须 `bram_commit()`：**读回最后一个写入字**强制刷出。

**⛔ 顺带重试 A-ov：仍然坏，且与映射无关。** 用新映射跑 A-ov 重叠 → 仍是
**253787/262144 FAIL**（失配数与用 `/dev/mem` 时**一模一样**），同布局只关重叠 →
PASS。⇒ **并发破坏在 PL 互联/控制器那一层**，只能改 BD（真双口 / SmartConnect /
拆两个控制器）。

**交付**：`plbram.ko` 在 `/lib/modules/<ver>/extra/`，开机自加载
（systemd `plbram.service`，enabled）；驱动优先 `/dev/plbram`、没有就自动退回
`/dev/mem`（`UNET_BRAM_MEM=1` 强制走 mem）。**模块不在也不影响功能**，只是少 9.4%。
内核源码树在板上 `/root/linux-xlnx`；`bc` 替身在 `/usr/local/bin/bc`。
脚本：`ai/net/_plbram_setup.py`（check/install-bc/install-boot/build/bench/bench-ddr/status）、
`_make_aov_plbram.py`、`_kern_upload.py`。

## 9.21 固化到板子（开机自动恢复）—— **已重启验收通过**（2026-10-02）

目标：断电重启后板子自动回到"PL 跑我们的设计 + 业务在跑"，不用人工敲命令。
细节见 **`boot/README.md`**。

**固化清单**（都在 SD 卡根文件系统上，断电不丢）：① PL 比特流
`/lib/firmware/ai_sys.bin`；② 业务程序 `/root/tcp_image_rx/`；③ `plbram.ko`
（`/lib/modules/<ver>/extra/`）；④ 编模块用的 `bc` 替身。

**出厂唯一缺的是第 ①：boot.bin 带的是厂商比特流**，上电后 PL 是厂商设计 →
业务程序初始化失败、被 `Restart=always` 反复拉起（服务一直 `activating`）。
（本次正好撞上板子自己重启过一次，看到的就是这个现象。）

**新做的 `ai-sys-bitstream.service`**（oneshot）：卸 `fpgadrv` → 把 `ai_sys.bin`
写进 `fpga_manager` → **读回 `0xA0000010` 校验是不是我们的设计**（期望 `0x494D5003`）。
顺序：`rc-local`（厂商 insmod fpgadrv）→ **本服务** → `plbram` → `tcp-image-server`。
**故意让 rc-local 先跑**：厂商驱动只对着厂商 PL 探测；随后我们换 PL 并卸掉它。
脚本 `/usr/local/sbin/load_ai_sys.sh` 幂等、失败不挡启动。

**验收（真重启一次）**：三个 unit 全 `active`、PL `operating`、
**`VERSION=0x494D5003 OUR DESIGN`**、`fpgadrv` 已卸；端到端
`imagelink` **`ai_us=1711742`、`vessels=35257/262144` 与重启前逐值一致**。
（第一次连接超时是"刚开机 1 分钟内板子在起桌面"，第二次成功 —— 不是程序问题。）

**回退**：`systemctl disable --now ai-sys-bitstream.service` 即回到厂商 PL。
**工具**：`ai/net/_boot_install.py`（`check`/`install`/`now`/`reboot`）。

---

# 九（续）、2026-10-06 这一整天（9.22 ~ 9.34）

> 本节是 10-02 深夜之后**新增的全部轮次**，按时间顺序写，**成功和失败的尝试都记**。
> 每一轮都有一句"结论"和"证据在哪"，便于回查。
> 汇总性的东西在：`docs/ai_latency_breakdown_and_optimization.md`（优化方案与否定依据）、
> `ai/opt_hls/README.md`（conv 并行化全过程）、`test_report_2026-10-06_conv_parallel.md`（交付报告）、
> `Fz3b_BoardCheck/测试报告_2026-10-06.md`（板端自检）。

## 9.22 全链路耗时分解 —— **结论：只有 AI 值得动**（10-06 上午）

用户要求"测 AI 识别哪一步最耗时 + 给优化方案"。

**先立基线**：同一轮里 `run_fullres.py` 回归 → 32×32 档 `0/1024 PASS`、
**512 档 `0/262144 PASS`**（对 PLD golden 逐字节）。**下面所有耗时都是在"结果正确"前提下测的。**

**全链路（512²，mode 0，flags = proc+mask+overlay+stats）**：

| 段 | 耗时 | 占比 |
| --- | --- | --- |
| TCP 收帧（recv 20 / parse 7 / crc 7） | 34 ms | 1.8% |
| 紧包 RGB888 → 32bpp 展开 | 2.7 ms | 0.1% |
| PL 预处理往返 | 21 ms | 1.1% |
| **AI 整网** | **1779 ms** | **94.6%** |
| 回传 3 幅图（只算不传时 0 ms） | 45 ms | 2.4% |
| PC 端 `total` | **1877 ms** | |

**AI 内部（三次独立测量一致：服务端 2 帧 + selftest）**：

| 段 | 时间 | 占比 | 吞吐 |
| --- | --- | --- | --- |
| **get（BRAM→PS）** | **1113 ms** | **63%** | 5 341 184 字 → 76.9 MB/s |
| **ap（PL 计算）** | **531 ms** | 30% | — |
| put（PS→BRAM） | 108 ms | 6% | 7 322 564 字 → 1.09 GB/s |
| zero + other | 16 ms | 1% | strips = 826 |

⇒ **TCP / 预处理 / 回传都已不是瓶颈**；**读比写贵 5.3×**，而"读"的每一次都是
"某层输出被搬回 PS"——**同一份中间结果被来回搬两趟**，这是"PS 当搬运工"架构的固有代价。

**产出**：`docs/ai_latency_breakdown_and_optimization.md`；脚本与原始日志 `ai/net/_prof_*.py|.log`。

## 9.23 把"读"的硬顶**钉死** —— 四类尝试全部无效（10-06）

在动手优化之前先把"单价"测准。`ai/net/_prof_recon{,2,3}.py`，同一片 PL BRAM
（`0xB0000000`）、同一个循环里只换读法（256 KB × 8 轮）：

| 读法 | ns / 16 B | MB/s | 结果 |
| --- | --- | --- | --- |
| `ldp` 单条 16 B（= glibc memcpy 的写法） | **198.2** | **80.7** | 最快档 |
| `ldp` ×4（64 B/轮，四条独立） | 199.4 | 80.3 | **加宽无效** |
| `ld1 {v0.16b}` 单条 16 B（NEON） | 396.5 | 40.4 | **反而慢一倍** |
| `ld1` ×2 / ×4 / ×8 **各自独立**（32/64/128 B 每轮） | 394 ~ 397 | ~40.5 | 多流无效 |
| `ld1 {v0-v3}` 一条指令搬 64 B | 394.5 | 40.6 | 无效 |
| 2/4/8 路**地址交错**读 | 394.5 ~ 396 | ~40.5 | 无效 |
| glibc memcpy（工程现用） | **197.8** | **80.9** | **就是最快那档** |
| *（对照）普通可缓存堆内存 `ld1` ×1 / ×4* | 7.7 / 5.1 | 2080 / 3154 | 快 40× |

**三条硬结论**：

1. **读 = 每次 16 B 访问 ≈ 198 ns 的纯延迟，完全不流水（无 MLP）**。
   把 8 条**互不相关**的 16 B load 背靠背排在一起，耗时与 1 条一样
   ⇒ **搬更多字节不能摊薄这笔账，只能减少访问次数**。
2. **NEON 的 load 比整数 `ldp` 还慢一倍**（395 vs 198 ns / 16 B）
   ⇒ "一条 `ld1 {v0-v3}` 搬 64 B 绕过延迟"这条**实测被证伪，别再试**。
   （工程现用的 `bram_get()` 就是 `memcpy`，**软件层面已经到顶**。）
3. **映射属性对读完全无效**（`/dev/mem` 与 `/dev/plbram` 逐位相同）；
   而**写侧能 burst**：Normal-NC 写 14.8 ns/16 B = **1.08 GB/s** ⇒ 读卡住不是带宽，
   是"单笔往返 + 不允许并发" → 给"**换一个能发 burst 的主设备**"留了口子（见 9.24）。

**⇒ 以后遇到"读卡住"，第一反应应该是"上 GDMA"，不是改循环、改映射。**

## 9.24 GDMA 探测：**PS 自带的 DMA 能到达 PL BRAM**（10-06）

**这是给上面那道墙开的口子。** 板上 `/sys/class/dma/` 有 **22 个通道**：
**8 个 FPD GDMA @`0xFD500000`（128 bit，用这个）**、8 个 LPD ADMA（64 bit）、
6 个 DisplayPort DMA；内核 `drivers/dma/xilinx/zynqmp_dma.c` 注册了 `device_prep_dma_memcpy`。

**探测脚本 `kmod/gdmatest.c` + `ai/net/_gdma_test.py`，用 `mode=` 分四档，一档一档 insmod**
（因为"越界或不可达的地址会让 AXI 事务永久挂起"——本工程 2026-10-01 因此掉过线）：

| 档 | 做什么 | 结果 |
| --- | --- | --- |
| `mode=0` | DDR→DDR 自检（零风险） | 73 µs → **3590 MB/s**，`mismatch 0/262144` |
| `mode=1` | **BRAM→DDR 读** 4/64/256 KB | 254 / 1053 / **1290 MB/s**（CPU 只有 81 MB/s = **15.9×**），**DMA vs CPU 逐字对账 0/65536** |
| `mode=2` | **DDR→BRAM 写** 256 KB | 194 µs → **1346 MB/s**（CPU Normal-NC 已 1085，只快 1.2×）；回读 DMA 0/262144、CPU 0/65536 |
| `mode=3` | **真实条带模拟**：826 条带 ×（写 142 KB + 读 103 KB）= 193 MB | **166 ms**，有效 **1218 MB/s** |

**尺寸扫描**（4 KB 265 → 8 KB 475 → 16 KB 720 → 32 KB 951 → 64 KB 1120 → 128 KB 1228
→ 256 KB 1290 MB/s）外推出 **每笔"DMA 传输"的固定开销 ≈ 12 µs（不随长度变）**
⇒ **必须按"大块/整条带"传，别按行传**（一帧 2.5 万行 × 12 µs = 300 ms 白扔）。
> ⚠ **两个"固定开销"别混**：**裸 DMA 一笔 ≈ 12 µs**（上面尺寸扫描外推的）；
> **落地版走 ioctl 之后一笔 ≈ 25 µs**（多出来的是 ioctl + `dma_map/unmap` + 完成唤醒，
> 见 9.25 的实测分解 `88.2 ms / 826 笔`）。做预算时按 **25 µs** 算才准。

**两个设计巧思**：模块**故意在 `init` 末尾 `return -ENODEV` 不常驻** —— 不占通道、
可反复跑、不用 `rmmod`；`dma_map_resource(dev, 0xB0000000, ...)` 返回**同一个物理地址**
（本板没有 IOMMU 翻译）。**探测全程板子在线，没需要重启。**

## 9.25 ✅ GDMA 落地：`plbdma.ko` —— 整网 **2.1×**（10-06）

**结构**（`kmod/plbdma.c` → `plbdma.ko` → `/dev/plbdma`；`src/unet_ai.c` 的 `bram_get()` 改走它）：

```
张量(malloc, 物理不连续)  ←memcpy→  bounce 缓冲(模块 alloc_pages, 物理连续 2MB, mmap 给进程且可缓存)
                                        ↕  ioctl: PLBDMA_IOC_XFER {bram_off, len, dir}
                                    PL BRAM 0xB0000000 (dma_map_resource)
```

**为什么中间隔一层 bounce**：张量是进程 `malloc` 的、**物理不连续**，直接 DMA 要么拆成几十笔
（每笔 ~25 µs 固定开销，亏死），要么上 `get_user_pages` + sg 映射（复杂、cache 维护易错）。
隔一层之后 DMA 永远是**一笔连续传输**，cache 维护由 `dma_map_single/unmap_single` 包办
（`FROM_DEVICE` invalidate / `TO_DEVICE` clean）→ **DMA 从不直接写张量，零一致性风险**。
代价是多一次内存 memcpy（稳态 36.9 ms）。

**只换了"读回"这一条路**：

| 改动 | 说明 |
| --- | --- |
| `unet_init` 里 `gdma_open()` | 失败就打印一行原因并**自动退回 CPU**（`UNET_NOGDMA=1` 强制对照） |
| `bram_get()` | 有 GDMA 就 `ioctl(XFER, dir=0)` + `memcpy(bounce→dst)`；**任何一笔出错就永久退回 CPU** |
| **把"逐行搬运"合并成"整条带一笔"** | ⚠ **这一步是必须的**：第一版忘了合并，`get` 只从 1113 → 325 ms；**合并后 → 181 ms** |
| `put` / `zero` **故意不动** | 8 KB 一行用 DMA 要 12 µs 开销 + 6 µs 传输 = 18 µs，而 CPU 靠 Normal-NC 写合并只要 **7.6 µs** → **换过去反而更慢** |

**实测（同一块板、同一套脚本）**：

| 指标 | 改之前 | 改之后 | |
| --- | --- | --- | --- |
| 上位机 全回传（proc+mask+overlay+stats） | 1877 ms | **896 ms**（875–937，3 帧） | **2.1×** |
| 上位机 只算不传（noimage+stats） | 1771 ms | **827 ms** | 2.1× |
| 板端 AI 整网 | 1768 ms | **835 ms** | 2.1× |
| ├ `get`（读回） | **1113 ms** | **181 ms** | **6.1×** |
| ├ `put`（仍走 CPU） | 108 ms | 108 ms | — |
| ├ `ap`（PL 计算） | 531 ms | 531 ms | — |
| 32×32 档（bring-up） | 7.95 ms | **4.34 ms** | 1.8× |

**正确性**：512 档 `0/262144 PASS`、32 档 `0/1024 PASS`，服务端每帧
`vessels=16458/262144` 与改前**逐值相同**；模块累计 **12 979 笔 DMA、0 错误**。

**复测后的新分解（512 稳态 777.3 ms）**
> ⚠ **别把两个数字当成矛盾**：上面那张表是**首次落地那一轮**（整网 835 ms、`get` 181 ms）；
> 这里是**同日晚些时候的稳态复测**（整网 777.3 ms、`get` 125.4 ms）。
> 差在 `get` 上（181 − 125.4 = **55.6 ms**），而 `ai_latency_breakdown_and_optimization.md` §3 正好写着
> "**第一帧 bounce memcpy 冷页要 94.8 ms、稳态只要 36.9 ms**"（差 **57.9 ms**）——
> 两个数**几乎正好对上** ⇒ **835 / 181 这一组是"含冷第一帧"的测量**，稳态应引用 **777.3 / 125.4**。
> （这条是我按两处数据对出来的，源文档没写死；**做基准时记得丢掉第一帧**。）

`ap 531.4（68.4%）` + `get 125.4（DMA 88.2 + bounce memcpy 36.9）` + `put 108.3（13.9%）`
+ `zero/other 12.2`。
⚠ **第一帧比稳态慢 ~8%**（842 vs 777，差在冷页/冷 cache 的 bounce memcpy：94.8 vs 36.9 ms）
→ **基准测试要丢掉第一帧**。

**⇒ 瓶颈已经从"搬运"转到"PL 算得慢"（ap 占 68%）**，这就是下一轮去动 HLS 算子的直接原因。

## 9.26 ⛔ "搬运与计算重叠"第三次尝试 —— **硬件挡住 + 算下来是白做，已否决**

模型说"把 233 ms 搬运藏到 531 ms 计算后面"能省 220 ms。做法：BRAM 布局改成
**输入 ×2 + 输出 ×2**，条带循环改成软件流水（`收上一条带的输出 ∥ 本条的 IP 计算`）。
**不改 PL、不改 HLS、不用出比特流**；开关 `UNET_DBUF=1`、对照 `UNET_NOOVL=1`。

**三档对照（同一份代码只切开关）**：

| 配置 | 总时间 | golden |
| --- | --- | --- |
| **A** 双缓冲布局 + **不重叠**（对照） | **891.2 ms** | ✅ `0/262144` **PASS** |
| **B** 双缓冲布局 + **重叠** | **781.7 ms** | ❌ **233879/262144 FAIL**（logits 饱和 ±127） |
| C 32×32 + 重叠（回归） | 4.34 ms | ✅ `0/1024` PASS |
| （基线）单缓冲 + GDMA | 776.5 ms | ✅ `0/262144` PASS |

**A 全对 ⇒ 布局与流水记账没问题；坏就坏在并发本身。**

**结论 1：PS 侧读 ∥ IP 写 = 坏数据（硬件级）。** 这正是 2026-10-02 A-ov 那类缺陷，
只是当年是 **CPU∘IP**，这次是 **DMA∘IP** —— **把主设备从 CPU 换成 DMA 也救不了**。
⇒ 只能改 BD（真双口 / SmartConnect 高性能 / BRAM 拆两块各挂控制器），而 A-ov 那次
已经证明**改 BD 也不保证有效**。

**⚠ 本轮我自己犯的一个判断错误（值得记）**：中途做了个"并发探针"
（`UNET_OVL_PROBE=1`，`ap` 期间并发一笔 DMA 读 + 逐字节校验），得到
**`ok=114 / bad=0` 且整网 golden 还 PASS** → 我据此判断"并发是安全的"，**这个判断是错的**：
探针读的是**输入区**（IP 只读它），测的是"**读∥读**"；真实场景是
"DMA 读**上一段的输出区** ∥ IP 正在**写自己的输出区**" = "**读∥写**"。

**结论 2：就算并发没问题，这个优化也是白做（≈ −5 ms）。**
重叠确实省了 `891.2 − 781.7 = 109.5 ms`；但双缓冲把**条带数从 826 抬到 1809**，
而 **IP 每次启动有约 100 µs 的固定开销** → 多出的 983 次启动要付 **+114.7 ms**。两笔相抵。

**"IP 每次 `ap_start` ≈ 100 µs"是这轮最有价值的新数字**，两点拟合：

```
826  条带 -> ap = 531.4 ms
1809 条带 -> ap = 675.4 ms
=> ap ≈ 61 ms × 输入百万字 + 0.102 ms × 条带数    (与 32×32 档独立吻合)
```

⇒ 以前"**ap 与条带高度无关**"这个假设**只对了一半**：计算量确实无关，
但**每次调用的固定开销是按次数收的** —— 512 档 826 次调用 ≈ **84 ms（ap 的 16%）纯粹是"开机"钱**。
**反过来"条带变少"是有价值的**：BRAM 加大到 ~344 KB → 条带 826→~650 → 省 ~26 ms。

**现状**：**已回退到单缓冲**（`UNET_DBUF` 不设），实测 776.5 ms、golden PASS、条带 826。
实验路径保留在 `src/unet_ai.c` 里可复现。

## 9.27 ✅ 交付：conv 算子并行化（XU=2）—— `ap` **531.99 → 353.7 ms（1.502×）**

> 全过程推导在 **`ai/opt_hls/README.md`**（V1→V12 每一代为什么死/为什么活），
> 交付报告 **`test_report_2026-10-06_conv_parallel.md`**。
> 用户要求："把 HLS 里的 3×3 卷积改成并行结构，尽可能榨干资源；需要随机读取的内容用 FF 而不是 BRAM"。

### 9.27.1 先量化，再选方向

`ai/net/_hls_analyze.py` 把 30 个节点的计算量算出来与每节点实测 `ap_us` 对账：

```
合计 2256 MMAC / 532.7 ms = 平均 41.8 MAC/周期
原版 COMPUTE 主循环：II=1、每周期 64 MAC（8 输出 lane × 8 输入 lane）
```

⇒ **流水线本身已经榨干、没有气泡可挖，唯一出路是"每周期多做活"**。逐档看谁值钱：

| 档 | MAC | 时间 | 占时 |
| --- | --- | --- | --- |
| **3×3 conv** | 1868.6 M | **417.7 ms** | **78.4%** |
| 1×1 conv | 92.3 M | 48.3 ms | 9.1% |
| Add/Concat | 26.2 M | 43.4 ms | 8.2% |
| 转置 2×2 | 67.1 M | 17.8 ms | 3.3% |
| MaxPool | 0.8 M | 5.5 ms | 1.4% |

**为什么选"并行多个输出列（x）"**：最贵的是 `wbuf` 那个 144 路权重 mux，而 XU 个像素
**共享同一组权重** → mux 不用加；而最贵的几个节点恰好是 `cog=1, cig=1`（8→8 的 3×3），
cg/ig 方向根本没得并行 —— **只有 x 这一维能用**。

### 9.27.2 ⭐ 三个"把前几轮带偏"的坑（都已实测钉死，别重踩）

1. **`ARRAY_PARTITION ... complete` 不带 dim，在 Vitis HLS 2025.2.1 上只切首尾两维。**
   storage report 显示 `win_*` 是 24 个 `ram_2p`（LUTRAM，每拍 1 读 1 写）
   → XU=2 一拍照读 2 列就报 `Lower bound of II is 2`。
   **跟 BRAM 分 bank、跟 requantize 完全无关**（之前的猜测是错的）。
2. **"真 FF 滑窗"能修好 II，但代价不可接受**：384 个真寄存器按运行时索引读要铺
   48 选 1 的 mux → STEP 自身 LUT 8409→22239、**整体 LUT 46159 → 72497（102%），装不下**。
3. **真正的吞吐瓶颈是"流水线每个 x 步都要重启"** —— STEP 的 iteration latency = 39~40 拍，
   而 3×3/cog=cig=1 时每步只有 `work=9` 次迭代 → 每步排空重灌：
   `40+9·II` 拍算 2 个像素（V6=28 拍/像素、V6a=24.5 拍/像素），而原版整行一条循环
   是 9.1 拍/像素 ⇒ **XU=2 按老结构不但没提速，是比原版慢 2.7 倍**。
   （**这推翻了上一轮"II=2 正好抵消迭代数减半 ⇒ 无提升"的结论** —— 它漏算了 prologue。）
4. **HLS 不跨循环共享硬件**：试过"3×3 走新结构、1×1/转置沿用原版循环"，结果乘法器
   128+96、requantize 出现 3 份副本 → **DSP 329（91%）/ LUT 72800（103%）**。
   **必须所有模式共用一条循环。**

### 9.27.3 最终结构（V9b，源码留档 `ai/opt_hls/src/conv_layer_v9.cpp.txt`）

> ⚠ **注意**：工作区里那份 **`ai/opt_hls/src/conv_layer.cpp` 现在已经是 V12 了**
> （10-06 当晚为了配真双口改的），**不再是本节描述的 V9b**。
> V9b 的源码只有 `src/conv_layer_v9.cpp.txt` 这一份，要重建交付版就得先把它拷回
> `conv_layer.cpp` 再导出 IP（见 9.29 与第四节 🥇 的陷阱说明）。

**① 整行一条流水线**：x 步展开成 slot，一个 slot 一拍 ——
`[0,work)` 计算（一拍 XU 像素 × 8×8 MAC）、`[0,nfill)` 补窗、`[0,nq)` requantize+存回；
`ncyc = max(work, nfill, nq)` —— **重叠不相加**，补窗/requantize 白搭在计算 slot 上跑；
整行 `total = (nsteps+2)·ncyc`，prologue 只付一次（末尾 2 步空转把最后两步结果补写出去）。

**② 滑窗 = 循环槽位 + A/B 双份 LUTRAM**（同时解决读口和 LUT 爆炸）：
列索引走 **RAM 地址**（不是寄存器选择）→ 没有大 mux；存 A/B 两份，u=0 读 A、u=1 读 B
→ 两块 RAM 各出一个读口；窗口宽 `WSN=2·XU+2=6`，**用取模寻址代替移位**
（逻辑列 c 在步 s 的槽位 = `(c + sft·phase) mod WSN`）→ 移位免费、滑窗零读改写；
计算读槽位与补窗写槽位**天然不相交** → 补窗与计算同拍跑。
三种模式共用一条循环：`(krows,sft,KC,PER)` = 3×3 `(3,2,4,3)` / 1×1 `(1,2,2,3)` / 转置 `(1,1,1,6)`。

### 9.27.4 V9 里踩到的两个真坑（都写进代码注释了）

1. **补窗一拍读 `rows` 的 3 行 → II=2。** 行槽位 `sl=(iy+6)%3` 是**运行时索引**，
   HLS 证明不了 3 条 load 落在不同分片 → 被判成撞同一个 `rows_0_0`。
   摊成"一拍读 1 行"后 II 回 1；因为 `6·cig ≤ 9·cog·cig` 恒成立，**`ncyc` 完全不变（免费）**。
2. **`nq ≥ work` 时 requantize 读到被覆盖的 raw。** requantize 读的是**上一步**的 raw，
   而计算在**本步** slot `work-1` 写 raw；`nq ≥ work` 时 slot 段会盖过它。
   3×3 恒有 `work=9·cog·cig > nq=2·cog` 所以没暴露，**1×1 的 cog=cig=1（work=1,nq=2）直接挂**
   —— csim 一次报 **7 个 FAIL，失败集合精确等于 `nq ≥ work` 的档**。
   修法：raw 做**两份按 `s&1` 轮转**（写 `raw[s&1]`、读 `raw[(s+1)&1]`）。

### 9.27.5 验证（三层，全部真实执行）

| 层次 | 方法 | 结果 |
| --- | --- | --- |
| C 仿真 | 把 **PLD 原版源码一字不改**当参考实现编译进同一单元，逐 int16 lane 比对 | `cases=25 words=737695 **failures=0**`（三模式 + 奇数宽 + 全尺寸） |
| RTL 综合 | `csynth`：`STEP_ALL Target II=1 / Final II=1` | ✓ 且 **BRAM18 148 一块没多** |
| **上板** | 板上原生自检对 PLD golden 逐字节比对 | **512 档 `0/262144` PASS、32 档 `0/1024` PASS** |
| 时序 | 实现后 `report_timing_summary` | **WNS +1.819 / WHS +0.012**，All constraints met |

**综合口径对比**（conv IP 自身）：BRAM18 **148（不变）**、DSP 134→198、LUT 29195→49410、
FF 18871→46168；3×3/cog=cig=1 时每行 `ncyc=9`、`total=(258)·9=2322` →
latency `44+2321=2365` 拍/行（**原版同一条行 4648 拍**）。

### 9.27.6 ✅ 上板实测（同一块板 / 同一套脚本 / 同一会话内 A/B）

重建脚本 `ai/bd/build_ai_sys_xu2.tcl` —— **只换 conv 的 IP 仓库，PS 预设 / 两个 FPD 主口 /
张量 BRAM 256 KB / 地址分配一字未改**；比特流 `bitstream/ai_sys_xu2_v9b.{bit,bin}`。
⚠ **但这套 A/B 现在已经失效**：`ai/build/conv_ip_xu2/` 在 10-06 23:45 被 **V12** 的导出覆盖了，
**重跑 `build_ai_sys_xu2.tcl` 得到的不再是 V9b**（见 9.29）。已交付的 V9b 只剩
`bitstream/ai_sys_xu2_v9b.{bit,bin}` 和源码 `src/conv_layer_v9.cpp.txt` 这两份凭据。

| | 旧 `ai_sys.bin` | **新 `ai_sys_xu2_v9b.bin`** | 倍数 |
| --- | --- | --- | --- |
| **ap（PL 计算）** | 531 989 µs | **354 172 µs** | **1.502×** |
| put（PS→BRAM） | 107 406 | 107 672 | 1.00×（没动 ✓） |
| get（BRAM→PS） | 125 401 | 125 688 | 1.00×（没动 ✓） |
| 整网（板端） | 776 344 µs | **599 045 µs** | 1.296× |
| 512 全回传（上位机） | 898.5 ms | **698.2 ms** | 1.287× |
| 512 只算不传 | 826.2 ms | **646.1 ms** | 1.279× |
| golden | PASS | **`0/262144` PASS** | ✓ |

`put`/`get` 数字一字不变，正好反证"这轮只动了 conv 算子"。

**整板资源**：**BRAM 192/216 一点没涨**（唯一紧的资源）、DSP 206→271、LUT 35 703→46 532、
FF 27 605→37 665、**WNS +1.819 / WHS +0.012**（有 CLB 拥塞告警，最终收敛）。

**逐节点 ap 精确落在设计预期的档位上、没有任何节点变慢**：

| 节点类型 | 倍数 |
| --- | --- |
| 3×3 卷积（12 个节点，占 78% 时间） | **1.62 ~ 1.81×** |
| 1×1 cog=cig=1（4 个节点） | **0.98 ~ 1.00×（如期"不倒退"）** |
| 1×1 通道多的 | 1.30 ~ 1.34× |
| 转置 2×2 | 1.47× |
| eltwise（走另一个 IP，本就没动） | 1.00× |

**⚠ 板子当前状态**：测试过程中已把新比特流写到板上 `/lib/firmware/ai_sys.bin`，
`ai-sys-bitstream.service` 开机会加载它 —— **板子现在默认跑的就是新设计**；
旧版 `bitstream/ai_sys.bin` 完整保留，随时可回退。

### 9.27.7 V1→V9 中间那些**死掉的版本**（都在 `src/*.cpp.txt` 里留档）

| 版本 | 做法 | 死因 |
| --- | --- | --- |
| V1 | 给 `rows` 分 bank（`ARRAY_PARTITION factor=XU` 或把 bank 写进数组形状） | **C 仿真逐位通过，但 csynth 出不来**：设计规模在 `Array/Struct` 阶段从 2,527 条指令**炸到 125,915 条（50×）**，卡在调度十几分钟无报告 |
| V2/V3 | 同上两条写法 | 都炸 |
| V4/V5(w1~w5) | 寄存器滑窗 + requantize 分拍 | 结构性障碍解决（规模回到 6,996 条、BRAM 不用分 bank），**但 II 仍是 2、LUT 到 81%**；requantize 分拍后资源大降（DSP 227→128、LUT 57765→46159）**II 却还是 2** —— 这一步的价值是**排除了"requantize 是瓶颈"这个假设** |
| V6 / V6a | 逐维显式 `complete` 的"真 FF 滑窗" | II 修好了（=1），**LUT 72497（102%）装不下** |
| V7 | 整行一条流水线 + 寄存器滑窗，但 1×1/转置走另一条循环 | 走到 V8 那条路：**HLS 不跨循环共享硬件**，硬件翻倍 |
| V8 | 双路径（3×3 新结构 + 1×1/转置原版） | **DSP 329（91%）/ LUT 72800（103%）**，装不下 |
| **V9/V9b** | 全模式统一 + 循环槽位 LUTRAM 滑窗 | ✅ **交付**（源码只在 `conv_layer_v9.cpp.txt`；`src/conv_layer.cpp` 已被 V12 顶替） |

**另有两次"中途先试过、发现问题后改掉"的小弯路**（写在 `opt_hls/README.md` §4.5）：
① "延迟一拍、每拍处理一个 u" 的 requantize 写法，对 3×3 没问题，但对 `cig=1` 的 1×1
（cg 间隔只有 1 拍）会**互相覆盖 `raw`** → 2 个用例挂，挪到"本步尾巴"才解决；
② `pend_cg = cg` 写在计数器自增**之后** → 记成下一个 cg（`cog=1` 时还越界写
`resA[.][1][.]`），表现为**输出全 0**。

## 9.28 ⛔ V10 / V11：想把那 31% 的搬运折进流水线 —— **两版都上板实测，都更慢，废弃**

V9b 每行 `LOAD(548)+init(48)+STEP(2365)+STORE(515) = 3476` 拍，其中 **1063 拍（31%）
是纯搬运**。于是试了两个版本，都把这部分做成 slot（`ncyc = max(...)` 对 3×3/cog=cig=1
仍是 9，**模型上"白搭"、一拍不多**）：**V10** = 预取 + 回写都折进计算流水线；
**V11** = 只折回写（纯写），预取仍是独立一趟（纯读）。两版都过了 csim 逐位等价与 csynth
（`STEP_ALL II=1`），资源也都在预算内。

**上板实测（同会话 A/B，每次烧录都回读 md5 确认换成功）**：

| | ap | golden | 整网 |
| --- | --- | --- | --- |
| **V9b**（三趟分开，交付版） | **353.7 ms** | 0/262144 PASS | 619.5 ms/帧 |
| V11（只折回写） | **539.2 ms（慢 1.52×）** | PASS | 802.8 ms/帧 |
| V10（读写都折） | **576.1 ms（慢 1.63×）** | PASS | 838.9 ms/帧 |

**三组数放一起就能定位**：V10 只比 V11 多慢 37 ms（那是"多折一个读"的代价），
而 V11 比 V9b 慢 185 ms ⇒ **代价来自"把 m_axi 访问折进计算流水线"这件事本身**，
跟"读写混合"无关（那个假设被这组数据否掉了）。**golden 全过** ⇒ 不是数据坏，是纯性能回归。

**对账**：每行张量 BRAM 至少 1024 次访问（512 读 + 512 写）；V10 实测每行 ≈8497 拍
⇒ **方向混在一起时每次访问 ≈ 8 拍**；而 V9b 里"纯读一趟""纯写一趟"都是 **1 拍/字**。

**⇒ 硬约束：本设计里 LOAD / COMPUTE / STORE 必须各自成环，不能折进同一条流水线。**

**留档情况（订正一处）**：工作区里 **V10 的比特流确实留着**
（`bitstream/ai_sys_xu2_v10.{bit,bin}`），但 **`bitstream/` 下并没有 V11 的比特流** ——
`ai/opt_hls/README.md` §8.9 写的"V10 / V11 的比特流都留着"**与目录不符**，只有源码
`src/conv_layer_v1[01].cpp.txt` 两份在。**别去 `bitstream/` 找 v11。**

**而且搬运这条路本来就到头了**：张量 BRAM 是**单口**（`blk_mem_gen Single_Port_RAM` +
`axi_bram_ctrl SINGLE_PORT_BRAM=1`），每行 ≥ `512 读 + 512 写 = 1024` 次访问
→ **硬件下限 ≈1024 拍**，而 V9b 的 `548+515=1063` **已贴着下限**（多出的只是 prologue）。
**计算那侧也到顶**：`ncyc = work = 9·cog·cig`，一拍 2 像素 × 64 MAC = 128 MAC/拍；
再快只能上 XU=3（192 DSP）→ 整板 DSP 会到 **335/360 = 93%**，超预算。

## 9.29 V12（LOAD/STORE 合并成一趟）—— **已写好、C 仿真过，等"真双口 BRAM"**

`ai/opt_hls/src/conv_layer_v12.cpp.txt` = V9b + **把"回写本行输出"和"预取下一行输入"
合并成同一条 streaming 循环**（**独立循环**，不折进计算流水线 —— 守住 9.28 的教训）。

- **单口 BRAM 下这样做没有意义**（一拍只能一次访问，分两趟还是合一趟一样）；
- **改成真双口之后**（`blk_mem_gen` → `True_Dual_Port_RAM` + `axi_bram_ctrl`
  `SINGLE_PORT_BRAM=0`）一拍能同时一读一写 → 合并这一趟可做到 ~`max(512,512)+prologue ≈ 550` 拍，
  每行 `3476 → ~2960`，**ap 354 → ~309 ms**。
- **已经做完的验证**：**C 仿真 25 用例 / 737 695 字 / 0 差异**
  （`build/conv_par_xu2_v12/solution1/csim/report/conv_layer_csim.log`）；
  **并且已经综合 + 导出成 IP** —— `ai/build/conv_ip_xu2/solution1/syn/report/csynth.rpt`
  的时间戳是 `Tue Oct 6 23:45:55 2026`，报告里能看到 V12 特有的 **`Pipeline_MERGE`**
  （`m_axi_gmem_in` 读 / `m_axi_gmem_out` 写都在这一条循环里）。
  ⚠ **这次导出把交付版 V9b 的 IP 仓库覆盖掉了**。
- **没做的**：**没上板**（BD 那一侧已经启动过，见下）。
- **关键底气**：**RAMB36 本来就是双口能力、块数一块不变**（已用 64 块），
  所以"改双口"**不额外吃 BRAM** —— 而 BRAM 是这块设计唯一紧的资源（88.9%）。

### ⚠⚠ 状态补记（**这一条原来的文档和记忆里都没有，是我这次翻工作区翻出来的**）

**真双口那套 BD 已经写好并且 10-06 当晚 23:47 已经启动过构建，但没跑完。**

| 证据 | 内容 |
| --- | --- |
| 脚本 | `ai/bd/build_ai_sys_xu2dp.tcl`（注释写明：与 `build_ai_sys_xu2.tcl` 唯一区别是 `blk_mem_gen` → `True_Dual_Port_RAM` + `axi_bram_ctrl SINGLE_PORT_BRAM=0`；工程 `vivado/ai_sys_xu2dp`、比特流 `bitstream/ai_sys_xu2dp.{bit,bin}`）；配套 `ai/bd/run_build_xu2dp.bat` |
| 构建日志 | `vivado/ai_sys_xu2dp_build.log` —— 第一行 `START 2026/10/06 23:47:19`，**没有 `EXITCODE` 行** |
| 做到哪一步 | `[Tue Oct 6 23:48:18 2026] Launched impl_1...`，日志停在 **Physical Synthesis** 阶段 |
| 双口有没有生效 | ✅ **生效了** —— 综合/实现日志里 BRAM 原语是 `WITH_BMM_INFO.**TRUE_DP**.SIMPLE_PRIM36...`（几十条 `Opt 31-422`），确认 `blk_mem_gen` 真的变成真双口了 |
| 产物 | ❌ **`bitstream/` 下没有 `ai_sys_xu2dp.*`**；`impl_1/` 里只有 `system_wrapper.tcl/.vdi`，**没有 `.bit`**；**没有 util/timing 报告** |
| 标记文件 | `vivado/ai_sys_xu2dp.done` 存在（内容是 `BUILD_XU2DP_DONE`），但**它和日志对不上** —— 现在这版 `.bat` 里 `EXITCODE` 那行写在 `.done` **之前**，而日志里没有它 ⇒ 这个 `.done` **很可能是更早一次运行留下的**（也可能当时那版 `.bat` 还没有 `EXITCODE` 这一行）。**总之：别拿 `.done` 当"构建成功"的证据。** |

**⚠ 构建时还有一条 CRITICAL WARNING（会挡住 `generate_bitstream` 的安全闸）**：

```
CRITICAL WARNING: [BD 41-1276] Cannot set the parameter Write_Depth_B on /bram_gen. Parameter does not exist
```

—— **真双口模式下 `blk_mem_gen` 没有 `Write_Depth_B` 这个参数**（深度由地址段范围推出），
脚本里那行 `CONFIG.Write_Depth_B {16384}` 是空操作 + 一条 CW。
**重跑时把它删掉**（或改用 `force` 出比特流），否则 `generate_bitstream` 会因为 CW 而拦下来。

**⇒ 下一顺位的第一步就是：重跑 `ai/bd/run_build_xu2dp.bat`，让它真正跑完出比特流**
（长构建记得后台跑 + 检查日志里出现 `EXITCODE 0`，别只看 `.done` 文件）。

## 9.30 ✅ 新工程 `Fz3b_BoardCheck`：板子自己当上位机（10-06）

**用户要求**："在 FPGA 的 Linux 里也写一个类似 Windows 上位机的程序，可以独立运行检测代码。"
三个选择（别推翻）：① 功能对齐上位机但**尽量低开销**（不做图形界面）；② **不过网**，
直调板端驱动；③ 四项自检全要（预处理 7 模式 / AI 整网 / 硬件自检 / 性能基准 + 基线留档）。

**为什么要有它**：以前每验一次都得开 PC、发图、肉眼看、手工记耗时 —— 麻烦、不可复现，
而且**判据在 PC 上**，主板不在 PC 边上就验不了。现在**判据在板子上**：
`bc_ref.c` 是**照着 `rtl/` 里那几个 `.v` 的语义写的**参考实现，板端拿它和 PL 的实际输出
**逐字节比**。**关键设计：makefile 直接编译 `tcp_image_rx/src` 的真实驱动**
（`axilproc.c` / `ai_view.c` / `unet_ai.c` / `unet_net_data.c`），**不是抄一份** ——
检测的就是真正在跑的那份代码。

**✅ 上板 31 项全 PASS、0 FAIL、0 SKIP**：

- PL 指纹 `0x494D5003`；conv/elt `CTRL=0x4`（`ap_idle=1`）；
- **7 个模式每个 `mismatch 0/786432`**；灰度 20.2~20.9 ms、伪彩 59.8~62.0 ms
  （伪彩 1 像素/字 → 读回 262144 次，是灰色的 4 倍访问）；
- **AI 512 全分辨率**：`vessels=16458/262144`，**与 HLS golden `0/262144`**；
  分解 `ap 531 / put 108 / get 1113`、条带 826 —— **与 10-02 的历史值逐项吻合**；
- 阈值预设 Otsu 一级 99 / 灰度下限 35 / 二级 129 / 边缘分位 42 —— 与 PC 上位机
  `imageio.py` 对同一张 test.ppm 算出的**完全一致**。
- 主机侧：`host_selftest` **57 项全过**；`check_lut_vs_rtl.py` **两张表 512 项逐项相同**。

**🐛 过程中修掉 8 个真 bug（前 8 个里 7 个是"主机编译过、交叉编译过、板上一跑就露"）**：

| # | 现象 | 根因 |
| --- | --- | --- |
| 1 | `mmap 0xB0000000: Invalid argument` | `/dev/plbram` 的 mmap **只能传 offset 0**，我把物理地址当偏移传了 |
| 2 | AI 整条检查一直 SKIP | `main.c` **从头到尾没调过 `bc_pl_ai_init()`** |
| 3 | 基准报 `13586.0 ms`，而整个程序只跑了 0.6 s | ns→ms 单位错（应除 `1e6`，写成除 `1000`） |
| 4 | （aarch64 gcc13 抓到）形参类型写成 `bc_hw_cfg_t` 而非 `bc_hw_state_t` | 类型写错 |
| 5 | ⭐ **mode6 第 2 步的两条路都调我自己的叠加函数** —— 自己和自己比，永远相等，**这条检查等于没有判据** | 自查发现；改成 `got` 走板端真身、`ref` 走独立复算，重跑仍 0 差异 |
| 6 | 界面在板上一跑就崩（launcher 连崩 5 次） | `Gtk.Window.get_accel_group()` **在 PyGObject 里不存在** |
| 7 | 下拉框重建时无限递归 | `ComboBoxText.remove_all()` 触发 `changed` → 回调又重建列表 |
| 8 | 新起的界面被 flock 挡掉、屏幕空白 | `flock -n` 直接退；`do_stop` 又先杀 python 后杀 launcher |

另：aarch64 gcc13 还揪出一批 `snprintf` **自引用**（`snprintf(x,...,"%s",x)` 会把内容重复几遍）。

**⭐ 顺带修正了一条成本模型**（这条比工具本身更值钱）：把 4 B/次 与 16 B/次两个程序并排看，
**只有"Normal-NC 写"差 4 倍**，换算成带宽就一致了：
`4B/3.8ns = 16B/14.8ns = 1.05~1.08 GB/s`。⇒
**读 = 延迟受限（198 ns × 次数，与位宽无关）／写 Device = 每笔强序（39.5 ns × 次数）／
写 Normal-NC = 带宽受限（字节 / 1.05 GB/s）**。
所以"int8 打包"在**读**侧依然 0 收益（而读正是瓶颈），但在**写**侧确实能省（写只占 6%）。

**✅ 追加：板端 GTK3 界面 + 开机自启（重启验收过）**

- **选型靠探测**：板上 `python3-gi` + GTK3 **已经装了** → 零安装；tkinter **没装**、
  板子**没外网**、apt 缓存里也没那套 deb → **选 GTK3**。
- **分层**：`gui/board_check_gui.py`（界面）+ `gui/bc_gui_engine.py`（引擎层，**不依赖 GTK**，
  可脱离显示器在板上单跑验）+ `gui/board_gui_launcher.sh`（等 X、等 PL、`flock -w 60` 单实例、
  崩溃重试 5 次、日志）。**界面不重写判据** —— 判据全在 C 工具里。
- **板子通常没有键鼠**（`/dev/input` 不存在）→ 设计成"没人操作也要有用"：启动即自动跑一遍；
  **每 2 s 看 `report_latest.md` 的 mtime** —— 你在 SSH 里跑一次 `board_check`，界面自己刷新。
- **自启写进 `/etc/xdg/lxsession/LXDE/autostart`**（不是用户级 —— 那个会**整体覆盖**全局，
  差点把 lxpanel/pcmanfm 弄没）。
- **重启验收**（`scripts/reboot_verify.py`）：SSH **25 s** 回来 → 再等 50 s →
  进程表里 launcher + `board_check_gui.py` **自己在跑**、`lxpanel=1`（桌面完好），
  截屏 `reports/gui_after_reboot.png` 显示界面已跑完一遍：**30 项全 PASS、AI 1780.5 ms、
  与 golden `0/262144`**。**这就是"开机自启生效"的唯一证据。**
- ⚠ **自检会和 `tcp-image-server` 争用 PL** →「自动重跑」默认关着。

## 9.31 PC 端：`samples/` 五张 STARE 眼底图（10-06）

用户要"默认测试图"。做法：把 STARE 的 `im0001`~`im0005.ppm`（P6 700×605 maxval=255，
1 270 515 B，**保留原始分辨率**）放进 `tcp_image_ui/samples/`，
下拉框里**除了三个内置图样，还列 `samples/` 里每一张图**（启动时扫一次，加图不用改代码）。

**验证**：源文件与原目录 `fc /b` **逐字节 identical**｜`_verify_samples.py` 全过
（目录扫描、`figure_names()`、五张都出 786432 B、未知名字抛 `KeyError`、目录缺失返回 `{}`、
内置图样不被同名 sample 顶掉）｜**交叉验证**：我们"原生 Netpbm 解码 + Pillow LANCZOS"
的结果 vs "直接让 Pillow 读同一个 PPM 再 resize"，五张**逐字节 0 差异**｜
`_verify_samples_ui.py`：下拉框候选值含这五张、`app.image` **逐字节等于**待发字节、
**Tk 回读像素 15 点 × 5 张不匹配 0**｜老契约未破（`test.ppm` 仍走"未重采样"路径）。

**上板实测**：`imagelink.py ... im0003 --mode 1 --thr auto` → 往返 **1796.2 ms**，
`proc` 与 `mask` 各 `rows_ok=512`，AI `vessels=17613/262144 (6.7%)`，
PC 端算的 Otsu=128 与界面侧一致。

## 9.32 ⛔ 事故：重编 PL 把板子挂死（10-06，代价是断电重启）

流程本是"烧 A → 跑板端自检 → 烧 B → 跑板端自检"。中间某一步自检报：

```
[ai] conv ap_idle=0 (CTRL=0x00000001): PL not our design?
```

即 **conv 核还有任务没跑完**，而脚本紧接着就重编了 PL。之后整块板
**ping 不通、SSH 不通**（ARP 都不通）→ PS 卡死。
机理：PL 被重编时 conv 的 `m_axi` 还有 AXI 事务在飞行，PS 侧的 AXI 互联等不到响应就挂了。
**本机 JTAG 又看不到目标**（`xsdb → connect → targets` 返回**空**，重试 5 次约 21 s 都是空，
Windows 只看到一个状态 `Unknown` 的 FTDI "USB Serial Converter"）→ **只剩断电一条路**。

**规矩（已写进 `ai/net/_board_load_bin.py`）**：

1. **重编 PL 前先 `systemctl stop tcp-image-server`**；
2. **读 CTRL 确认两个 HLS 核 idle（bit2 = `ap_idle`），等不到就中止、不烧**，别抱侥幸；
3. **烧完回读板上文件 md5 与本地比对**，打 `>>> LOAD_OK / LOAD_FAILED`
   （这招还顺带抓到过一次"脚本在板上 Python 3.6 上崩了、其实根本没烧进去"的假数据）。

**为什么以前没发现**：之前几次"烧 A → 测 → 烧 B"都碰巧成功，是因为服务恰好空闲，
让人误以为"重编 PL 是安全的"。**一旦有一次任务在跑，整块板就没了。**

## 9.33 固化清单更新：现在是 **5 件**（10-06 新增 `plbdma`）

| # | 东西 | 位置 | 谁拉起 |
| --- | --- | --- | --- |
| 1 | **PL 比特流** `ai_sys.bin`（= `ai_sys_xu2_v9b`） | `/lib/firmware/ai_sys.bin` | `ai-sys-bitstream.service`（oneshot） |
| 2 | 业务程序 `tcp_image_server` | `/root/tcp_image_rx/` | `tcp-image-server.service` |
| 3 | `plbram.ko`（Normal-NC 映射） | `/lib/modules/<ver>/extra/` | `plbram.service` |
| 4 | **`plbdma.ko`（GDMA 搬张量读回）** | 同上 | **`plbdma.service`**（`modprobe plbdma bram=0xB0000000 window=0x40000`，`After=plbram`、`Before=tcp-image-server`） |
| 5 | 编模块用的 `bc` 替身 | `/usr/local/bin/bc` | — |

开机顺序：`rc-local`（厂商 insmod `fpgadrv`）→ `ai-sys-bitstream`（卸 fpgadrv + 烧 bin + 读
`0xA0000010` 校验）→ `plbram` → `plbdma` → `tcp-image-server`。
**3/4 两件没装也不影响功能**，只是读回退回 CPU（慢 6 倍）。
⚠ **卸 `plbdma` 之前必须先停 `tcp-image-server`**（服务开着 `/dev/plbdma`，引用计数非 0 → `rmmod` 失败）。
📌 **文档欠账**：`boot/README.md` 的清单还停在 4 件、`kmod/README.md` 还没有 `plbdma` 一节 —— **以本节为准**。

## 9.34 本轮"试过但没成功 / 主动放弃"的清单（**汇总，免得后人重走**）

| 尝试 | 期望 | 实测结果 | 结论 |
| --- | --- | --- | --- |
| 用更宽的 load 加速读（`ldp`×4 / NEON `ld1 {v0-v3}` 64B） | 摊薄延迟 | 与单条**完全一样**；NEON 反而**慢一倍** | ⛔ 证伪，别再试 |
| 多流/交错地址并发读（2/4/8 路） | 打出 MLP | 394~396 ns，一模一样 | ⛔ 通路只有一笔读在飞 |
| 把 BRAM 映射成 Normal-NC / 可缓存来加速读 | 2~4× | 读**一点没变**（连 PS 自己的 DDR 也一样）；只有写快 2.7× → 整体 −9.4% | ⚠ 只帮写 |
| HLS int8 打包（每字 16 个 int8） | 读减半 ≈ −265 ms | 读代价 = **次数 × 198 ns**，与位宽无关 → **0 收益** | ⛔ 否决（省下几周白工） |
| 搬运与计算重叠（A-ov / GDMA 版，共 3 次） | 省 220 ms | ① 硬件坏数据（读∥写）② 条带数 826→1809、每次调用 ~100 µs 把收益吃光 | ⛔ 否决 |
| 分组层间链式（曾经的首推） | 省 660 ms | GDMA 之后同样的字只要 ~48 ms 就能读下来 → 只剩 ~60 ms | ⛔ 不做 |
| `put` 也走 DMA | 更快 | **108 → 162 ms（更慢 54 ms）** | ⛔ 明确不做 |
| 双缓冲布局（只改布局、不重叠） | 试并发 | 布局本身正确（`0/262144` PASS），但条带数翻倍、成本 +115 ms | ⚠ 只当对照 |
| conv 折进流水线（V10 / V11） | 省 31% 搬运 | 慢 **1.63× / 1.52×** | ⛔ 否决 |
| conv 给 BRAM 分 bank（V1/V2/V3） | XU=2 的读口 | HLS 规模从 2 527 炸到 **125 915** 条指令，csynth 出不来 | ⛔ 换思路 |
| conv "真 FF 滑窗"（V6a） | II=1 且便宜 | II=1 了，但 **LUT 72 497（102%）** | ⛔ 装不下 |
| conv 双路径（V8） | 1×1 摊不开开销 | **HLS 不跨循环共享硬件** → DSP 91% / LUT 103% | ⛔ 必须共用一条循环 |
| XU=3 | 再快 1.5× | 整板 DSP 会到 **93%**，超预算 | ⛔ 不做 |
| 给 `rows` 加第 4 个行槽做真预取 | 省搬运 | BRAM 已 **88.9%**（唯一紧资源） | ⛔ 不做 |
| 改 BD 约束提频到 148 MHz | 省 167 ms | 时序收敛、比特流也出了，**上板往返一点没变**（PL 时钟由 FSBL 决定） | ⛔ 此路不通 |
| 自建 boot.bin 打通 PL→PS 从口（10-01） | 用 AXI DMA 读 DDR | **板子起不来**，已回滚原厂镜像 | ⛔ 已确认死的 |
| 板端 Python 逐像素轮询推拉（10-01） | 快速验证 | 把板子 CPU 打满、**掉线** | ⛔ 改成 C/批量 |
| `/dev/mem` 区用 `memset` 补零 | 简单 | **SIGBUS（`DC ZVA` 对 Device 内存非法）** | ⛔ 用 volatile 循环 |
| 并发探针（读∥读）证明"并发安全" | 判并发 | `ok=114/bad=0` **但测的是读∥读，说明不了问题** | ⛔ 判据本身错了 |
| **真双口 BRAM 重建**（`ai/bd/build_ai_sys_xu2dp.tcl`，10-06 23:47 启动） | 解锁 V12 的搬运合并 | **跑到 Physical Synthesis 就断了（会话结束），没有比特流**；但**双口本身生效**（原语是 `TRUE_DP.SIMPLE_PRIM36`） | ⏳ **未完成，重跑即可**（见 9.29 状态补记） |

---

## 9.35 置信度热力图：flags b5 回传 logit 图（2026-10-07，**代码完成、离线验证通过，未上板**）

**目的**：赛题拓展③（交互式分割）、⑤（伪彩色热力图）。板端把 U-Net 的 int8 logit 回传，上位机画
置信度热力图、标出“不确定区域”，并且**拖门限滑条时在本地重算掩膜 / 叠加，不重跑 FPGA**。

**协议**（向下兼容，没改控制头）：

| 项 | 内容 |
| --- | --- |
| 新标志 | `FLAG_ECHO_LOGIT = 0x0020`（b5），`src/image_proto.h` |
| 回传内容 | 多一幅 512×512 RGB 图，排在掩膜之后：每像素 **R=G=B=logit+128**（偏移码 `LOGIT_IMG_OFFSET`），放大方式与掩膜相同（`ai_view_logit_rgb()`，`src/ai_view.c`）；复用行包 + CRC16 |
| 能力协商 | 统计块 `AI_STAT_VERSION` 1 → **2**。上位机要 logit 时**强制带统计块**，看到 `version < 2`（老板端）就不等 logit 图；看到 `ai_us == 0`（这帧 AI 没跑出来）就不等叠加 / 掩膜 / logit —— 顺手修掉了“AI 失败时上位机空等到超时”的老问题 |
| 代价 | 多传 789 504 字节（千兆网约 15 ms）；板端多一次 512×512 展开 |

**改动文件**：板端 `src/image_proto.h`、`src/ai_view.h/.c`、`src/tcp_image_server.c`（AI 触发条件加 b5、
发送顺序、`--help`）；上位机 `proto.py`、`imagelink.py`（CLI 支持 `--flags proc,logit`）、
新增 `confidence.py`（纯 Python，无 numpy）、`app.py`（「置信度」复选框、AI 判定门限滑条 + 不确定带、
`heat` / `uncertain` 两个视图）；测试 `test/ai_view_dump.c` + `tcp_image_ui/_verify_confidence.py`。

**已验证（不碰硬件）**：
- WSL Linux gcc 按 makefile 选项（`-O2 -Wall -Wextra -std=c99`）全部源码编译 + 链接通过，0 警告；
- `_verify_confidence.py`：用板端**原样的** `ai_view.c` 生成掩膜 / 叠加 / logit 图，与 `confidence.py`
  本地重算**逐字节一致**，30 组（合成满量程 + im0002 / im0324 真实 logit；512/256/32；门限 −128…127），
  血管计数也一致（im0002 门限 −3 = **16458**，与 10-06 实板服务端日志相同）；
  假板端走 `imagelink.round_trip`：新板 / 老板 / AI 失败三种情况都按预期收图、不挂；
  一次换门限的全部本地重算 ≈ 17 ms；
- 界面假数据预览（im0002 真实 logit）：热力图、不确定区域、本地改门限、自检提示都正常；`_verify_samples_ui.py` 仍全过。

**上板要做的（下次连板）**：
1. `scp -r src makefile` 到板上 `make clean && make CC=gcc`，替换业务程序后 `systemctl restart tcp-image-server`；
2. 上位机勾「置信度」+ 预处理图 / 掩膜 / 叠加发一帧：提示里应出现“与板端逐字节一致 ✓”；
3. 量一次多传 logit 图的耗时（预计 +15 ms 左右）；
4. 回归：`run_fullres.py`（0/262144）+ `Fz3b_BoardCheck`（板端自检不经网络协议，不受影响，跑一遍确认）。

**顺带发现**：`test/build_host_test.bat` **在本次改动之前就编不过**（`host_test_rx.c` 还在用旧的
`receive_frame()` 5 参数签名，现在是带 flags/aiparam/thr 的新签名），与本次无关，未修。

---

## 9.36 ROI 局部推理：控制头 v5（2026-10-07，**代码完成、离线验证通过，未上板**）

**目的**：赛题拓展③（框选 ROI、FPGA 实时更新掩膜）。上位机在左图拖框，板端只在框附近的裁剪窗口上跑整网，
**ROI 内结果与整图推理逐值一致**，计算量按面积缩小（150×140 的框只算 200×192，占整图 14.6%）。

**正确性（核心）**：`ai/net/roi_exactness.py`
- 理论：在部署层表上做行依赖传播（3×3 卷积 ±1 行、池化 2 行并 1 行、转置卷积 1 行出 2 行），
  裁剪边界的零填充最多影响离边 **22 像素**；起点必须对齐到 4（两级 2×2 池化）。
- 实测：用 HLS 两个算子的 NumPy 逐位参考（PYNQ 模拟器同款）在裁剪尺寸上跑整网，与整图输出
  （medical_fpga 整网 C 仿真 `final_actual.bin`，与实板逐字节一致）比对：3 张图 × 12 个 ROI（含贴边、非正方形、随机）
  **36 组 ROI 内全部 0 差异**；差异最深**正好 22 像素**（上界是紧的）；板端取边距 24。
  反例：起点不对齐（202,182）时中心 96×96 内 7949 个像素不同。

**改动**：
| 文件 | 内容 |
| --- | --- |
| `src/unet_ai.c/.h` | 新增 `unet_set_size(h, w)`：各节点尺寸按 h/512、w/512 等比缩放（原来只有 `>> shift`），`unet_set_shift` 成为特例；`UNET_SIZE_ALIGN 8` / `UNET_ROI_ALIGN 4` / `UNET_ROI_MARGIN 24`。条带调度和两个 HLS 核本来就按运行时尺寸工作（卷积核只限宽 ≤512、转置 ≤256），**IP 与比特流不用动** |
| `src/image_proto.h` | 控制头 **v5**（0x4D46，20 字节 = v4 + roi_x/y/w/h）；`ROI_OUTSIDE_LOGIT = -128` |
| `src/ai_view.c/.h` | `ai_view_roi_crop()`（外扩 24、起点 4 对齐、尺寸 8 的倍数，越界回挪起点）、`ai_view_crop_rgb()`、`ai_view_roi_paste()`、`ai_view_set_stats_extra()`；统计块 **version 2 → 3**，ROI 帧 `total` = ROI 面积、`reserved` = (裁剪高<<16)\|裁剪宽 |
| `src/tcp_image_server.c` | `receive_frame()` 认 v5（多一个 `roi_out` 参数）；ROI 帧：`unet_set_size` → 裁剪 → `unet_run` → 贴回 512×512 → 只在 ROI 内计数；`--help` |
| `ai/net/gen_unet_c.py`、`src/unet_net.h` | 只改了一行注释（“尺寸 = 表里的值 >> shift” 已不准确） |
| 上位机 `proto.py` / `imagelink.py` / `confidence.py` / `app.py` | v5 打包、`proto.roi_crop()`（与板端同算法）、`--roi x,y,w,h`、左图画布拖框 / 右键或 Esc 清除、右图描框、ROI 口径统计、**ROI 与整图 logit 自动比对** |

`ai_view_fill_stats()` 的签名**故意没改**（另加 `ai_view_set_stats_extra`），免得 `Fz3b_BoardCheck` 跟着要改。

**已验证（不碰硬件）**：WSL Linux gcc 全部源码 + `unet_selftest` 编译链接 0 警告；`roi_exactness.py` 36/36；
`tcp_image_ui/_verify_roi.py`：板端 C / `proto.roi_crop` / `roi_exactness.crop_for_roi` 三份裁剪算法在 2011 个框上一致、
对齐与边距规则 0 违规、贴回逐字节一致、假板端 v5 往返与统计块 v3 解码正确；`_verify_confidence.py`、`_verify_samples_ui.py` 仍全过；
界面端到端预览（im0002，ROI 帧的 logit 由 NumPy 参考真实算出）显示“ROI 与整图推理逐值一致 ✓（21000 像素）”。

**上板要做的**：
1. 与 9.35 一起重新编译部署（`src/` 全部）；
2. 勾「置信度」，对 im0002 先发一帧整图，再拖框发 ROI 帧：状态栏应显示“ROI 与整图推理逐值一致 ✓”；
3. **量 ROI 帧的板端耗时**（按面积估算 150×140 的框约 599 × 14.6% ≈ 90 ms，加上每层固定开销，**未实测**）；
   顺手量几个框大小，画“ROI 面积 vs 耗时”曲线，给报告用；
4. 风险点：非 2 的幂、非正方形尺寸在实板上第一次跑（C 仿真 / NumPy 参考已覆盖，但条带调度在这些尺寸上没上过板）——
   若某尺寸出错，先用 `UNET_DEBUG=1` 看各节点尺寸，再用 `run_fullres.py` 的方式对单个 ROI 做逐节点比对。

---

## 9.37 血管形态定量指标：flags b6（2026-10-07，**代码完成、离线验证通过，未上板**）

**目的**：赛题基础②（连通域统计、形态学）、“实际应用性”。在 AI 掩膜上算总长度、平均管径、分支点、端点、段数、
迂曲度、分形维数、视野内血管密度（含四象限），随统计块回传，外加一幅形态骨架视图。完整说明：**`docs/vessel_morphology_metrics.md`**。

**改动**：新增 `src/vessel_morph.c/.h`（去小块 → Zhang-Suen 细化 → 交叉数 → 分段迂曲度 → 盒计数 → FOV 密度）；
`image_proto.h` 加 `FLAG_MORPH = 0x0040`；统计块 **version 3 → 4**；`tcp_image_server.c` 在 AI 之后算指标、
在统计块后发 64 字节指标块、在图像最后发骨架视图（两者都要求同时带 b3）；**`makefile` 加了 `LDLIBS := -lm`**（用到 log/sqrt）。
上位机：`proto.py`（`FLAG_MORPH`、`unpack_morph`）、`imagelink.py`（收指标块、`--flags morph`）、`app.py`（「形态指标」复选框、
底部指标行、`morph` 视图）。测试：`test/vessel_morph_dump.c`、`ai/net/vessel_morph_ref.py` / `_check.py` / `_eval.py`、`tcp_image_ui/_verify_morph.py`。

**已验证（不碰硬件）**：
- 板端 C 与独立 NumPy/SciPy 参考：20 张模型掩膜 + AH 标注 + 128 档 + ROI 网格，**骨架图逐像素一致、指标逐项一致**；
  7 个已知答案图形（横线 / T 形 / 十字 / 圆环 / 正弦 / 带噪点 / 空图）拓扑量全部正确；
- 板端打包 ↔ 上位机解包逐字段一致；假板端 v4 / v3 / AI 失败三种往返正确；WSL 按 makefile 编译 0 警告；
- 与人工标注对比（以两位专家之间的差异为参照）：**总长度、分支点数、分形维数、FOV 密度可用；平均管径和端点数在模型掩膜上不可靠**
  （模型血管偏粗、细血管易断），详见 `docs/vessel_morphology_metrics_eval.md`。

**没做的**：原计划“把后处理挪进 PL、接 A 的形态学 IP”。原因：本机只有 Vivado/HLS 2023.1，现有系统与 HLS IP 是 2025.2.1 构建的
（2023.1 导不进新版 IP，重综合 conv 又依赖 2025.2.1 的行为）；PL 只剩 24 块 BRAM；需要上板。`vessel_morph.c` 全是 3×3 / 逐像素的流式步骤，
迁 PL 时可直接作参考。

**上板要做的**：与 9.35 / 9.36 一起部署（`make clean && make CC=gcc`，注意新加的 `-lm`）；勾「形态指标」发 im0002 整图，
板端日志 `[vm] ...` 与界面指标行应与离线结果一致（im0002 门限 −3：总长 4828.3、分支 54、端点 69、分形 1.382）；**记下 PS 用时**。

---

## 9.38 模型库：同一比特流、运行时切换三套参数（2026-10-08，**已上板验收**）

**目的**：一个板端程序里放眼底、皮肤镜、心超三套参数，上位机逐帧选模型，不重启服务、不换比特流
（取代皮肤镜那次的“两个程序 + 互斥服务”方案 A；用户 10-08 选定方案 B）。

**协议**（向下兼容）：`aiparam` bits[7:5] = 模型，0 = 板端默认（`--model`，缺省眼底），k = 第 k−1 号；
老客户端这三位恒为 0，行为完全不变。选了模型而分辨率字段为 0 时，用该模型自己的默认档位（皮肤、心超是 128）。
统计块 version 4 → **5**：`flags` bits[23:16] = 实际使用的模型下标 + 1。128 档训练的模型不支持 ROI，板端收到 ROI 会忽略并按整图推理。

| 下标 | 名字 | 来源 | 默认档位 | 默认门限 | ROI |
|---|---|---|---|---|---|
| 0 | fundus | 原 `src/unet_net_data.c`（一字未改） | 512 | −3 | 支持 |
| 1 | skin | `E:/PLD/skin/deploy/r128` | 128 | 1 | 不支持 |
| 2 | echo_lv | `E:/PLD/echo/deploy/lv_r128`（CAMUS 左心室） | 128 | 1 | 不支持 |

**改动**：
- `ai/net/gen_unet_models.py`（新）→ `src/unet_models.{h,c}`（生成）。它会先用同一套逻辑把眼底表重新生成一遍，和 `unet_net_data.c` 逐字段比对；
  写出的 C 再解析回来，和 `network.txt` / `params/*.bin` 比对。
- `src/unet_ai.{h,c}`：`unet_select_model()` / `unet_get_model()`；12 处参数表引用改走当前模型指针（参数本来就是每次推理逐层写进 BRAM，切换无开销）。
- `src/tcp_image_server.c`：`--model`、逐帧选模型、档位默认值、ROI 门控、统计块里报模型、`--help`、启动时打印模型表。
- `src/image_proto.h`（`AIPARAM_MODEL_*`）、`src/ai_view.h`（`AI_STAT_VERSION 5`）、`makefile`（`unet_selftest` 加 `unet_models.c`）。
- 上位机：`proto.py`（`MODELS` 表、`make_aiparam(model=)`、v5 解码、自检）、`confidence.py`（按模型换输出 scale）、
  `app.py`（「AI 模型」下拉框和菜单；换模型自动设门限和分辨率，关掉不适用的 ROI / 形态指标；统计行显示模型和目标名）、
  `imagelink.py`（`--model`）。
- 备份：`_backup/SNAPSHOT_20261008_pre_v5_models/`、`tcp_image_ui/_backup_SNAPSHOT_20261008_pre_v5_models/`。

**部署**：`boot/deploy_board_v5.py prepare|activate|rollback|status`（清单在 `tcp_image_ui/board_check_v5/deployment.json`）。
板上原生编译 0 警告，`ai_view_count_test` 5/5；新程序 SHA-256 `3745f10f…`；**回退 = `rollback`，恢复 v4 程序 `73e3ad89…`**。
⚠ **没有**把新源码同步进 `/root/tcp_image_rx/src/`：板上 `Fz3b_BoardCheck` 的 makefile 直接编那里的 `unet_ai.c` / `unet_net_data.c`，
同步后要在它的 makefile 里加 `$(RX)/src/unet_models.c`，否则重编会链接失败（现有 BoardCheck 二进制不受影响）。

**上板验收（10-08）**：
| 项目 | 结果 |
|---|---|
| 眼底，**原样的 v4 客户端**（`verify_board_v4.py`，不发模型号） | 75 帧 PASS，整图 golden 262144 logit 0 差异；统计块 v5，报模型 0 |
| 皮肤（模型 1） | `skin/verify_board_skin.py`：ISIC 测试集 379/379 logit 逐值一致，Jaccard 0.8182（与独立服务时相同），AI 37.7 ms |
| 心超（模型 2） | `echo/verify_board_echo.py`：金标准帧 0 差异；验证集 200/200 帧逐值一致，板端 Dice 0.9196 = 离线；AI 37.7 ms |
| **模型来回切换** | 三个模型随机交替 60 帧，每帧与各自参考逐值一致 |
| 界面（`verify_board_v5_ui.py`） | 眼底整图 + 本地门限 + ROI（12065 像素）一致；界面切到心超、皮肤后，门限 / 分辨率 / ROI / 形态指标随模型变化，模型号与本地重算全部一致 |

**坑**：
1. `verify_board_v4_ui.py` 写死了 `board_stat_version == 4`，在 v5 板上必然失败；**而且它会覆盖 `board_check_v4/gui_acceptance.json`** ——
   10-08 那份原始记录已被这次运行覆盖（结论仍在 `上板验收报告_2026-10-08.md`）。v5 以后用 `verify_board_v5_ui.py`，结果写 `board_check_v5/`。
2. 原来的皮肤独立服务（`tcp-image-server-skin.service`、`/root/skin/`）现在多余，但还在（未启用、不影响）；需要时用 `skin/board/deploy_skin.py uninstall` 删掉。

## 9.39 点击纠错模型进模型库（v5.1，2026-10-08，**已上板验收**）

- 模型库加第 3 号 `echo_inter`（`gen_unet_models.py` 的 `MODELS`，来源 `E:/PLD/echo/deploy/inter2_r128`）。它的输入是上位机拼的
  “R 原图 / G 先验掩膜 / B 有符号点击锥形”（`tcp_image_ui/interactive.py`），**板端代码和协议没有任何新增**，只是多了一套参数。
- 上位机：`proto.MODELS` 加 `echo_inter`（`hidden`，不进下拉框；`echo_lv` 带 `inter=3`）；`app.py` 加「点击纠错」勾选框、「撤销」「重置」按钮和 Ctrl+Z；
  勾上后先发一帧拿自动结果，左图左键 = 正点、右键 = 负点，每次点击用模型 3 发一帧（只回掩膜 + logit + 统计），叠加画在原图上；
  ROI 的鼠标操作在点击纠错时让位。服务端 `--help` 的模型列表补上 `echo_inter`。
- 部署：`deploy_board_v5.py` 改为**每次部署备份当时正在运行的程序**（回退退一个版本）。v5.1 = `1a70adfa…`，回退到 v5.0 = `3745f10f…`。
- 验收：`echo/verify_board_inter.py` 2400 个点击输入逐值一致、板上点击曲线与离线相同（每次点击 AI 37.7 ms / 往返 112 ms）；
  `verify_board_v5_ui.py` 增加两次点击 + 撤销 + 重置；`echo/verify_board_echo.py`（含三模型随机切换 60 帧）和 `verify_board_v4.py`（眼底，原样 v4 客户端 75 帧）回归通过。
- 细节与精度见 `E:/PLD/echo/README.md` 的 F2 一节。

## 9.40 序列传播模型进模型库（v5.2，2026-10-09，**已上板验收**）

- 模型库加第 4 号 `echo_seq`（`gen_unet_models.py` 的 `MODELS`，来源 `E:/PLD/echo/deploy/seq_none_ex`，全 INT8）。输入是上位机拼的
  “R 本帧 / G 上一步的结果 / B 128”（`tcp_image_ui/sequence.py`），**板端代码和协议没有新增**，只多一套参数；生成时核对了 0–3 号模型的源码只增不改。
  服务端 `--help` 的模型列表补上 `echo_seq`。改前备份 `_backup/SNAPSHOT_20261009_pre_v5_2_echo_seq/`。
- 上位机：`proto.MODELS` 加 `echo_seq`（`hidden`，`enc="none"`；`echo_lv` 带 `seq=4`）；新窗口 `sequence.py`「序列跟踪」（主界面按钮和 AI 菜单都能打开，
  与主界面共用同一条板子连接——板端一次只服务一个连接）；`interactive.py` 加 `compose_seq`（B = 上一帧，供 `enc="prev"` 的模型用，当前没用上）。
- 部署：`deploy_board_v5.py prepare / activate`，v5.2 = `b4acc143…`，回退到 v5.1 = `1a70adfa…`（`rollback`）。
- 验收：`echo/verify_board_f3.py` 板上闭环传播 3716 步逐值一致（AI 37.8 ms / 往返 111 ms）；`tcp_image_ui/verify_board_f3_ui.py` 界面整段播放 + 点击纠正 + 撤销 + 往返循环通过。
  跑界面脚本要先把 `TCL_LIBRARY` / `TK_LIBRARY` 指到 `srtp-vision\Library\lib\tcl8.6` / `tk8.6`（与 `run.bat` 相同），否则 Tk 初始化会卡住。
- 精度、训练过程和“中间帧标注疑似插值”的证据见 `E:/PLD/echo/README.md` 的 F3 一节。

## 9.41 射血分数（F4，2026-10-09，**已上板验收**；只改上位机，板端仍是 v5.2）

- 新文件 `tcp_image_ui/ef_simpson.py`：CAMUS 官方笔记本的双平面 Simpson 圆盘法逐步移植（上位机和 `E:/PLD/echo` 的离线评测共用）；
  专家标注复现 CAMUS 参考 EF 450/450 完全一致。预测掩膜先取最大连通域并填洞。需要 numpy / scipy / scikit-image（srtp-vision 环境），缺包时 EF 栏变灰。
- `sequence.py`：窗口下方加「射血分数」栏（记入当前视图 / 算 EF / 清空，结果另开窗口画轮廓、长轴、二尖瓣平面、20 个圆盘）；
  NIfTI 读取支持 float32 等类型（CAMUS 的半周期序列是 float32），从 CAMUS 文件名读病人 / 视图，从同目录 `Info_*.cfg` 读参考 EF。
- 验收：`verify_board_f4_ui.py`（srtp-vision python，TCL/TK 路径按 `run.bat` 固定）三位演示病人两视图在板上放完，掩膜、关键帧、EDV / ESV / EF 与离线评测代码逐值一致。
- 精度见 `E:/PLD/echo/README.md` 的 F4 一节（验证集界面默认做法 EF 平均绝对误差 5.3 个百分点，每视图点 2 次 4.3）。

## 9.42 v5.2 长时间稳定性测试（2026-10-09，**通过**）

- `tcp_image_ui/verify_board_stability_v5.py`：5 个模型混合 5000 帧逐帧对照逐位参考，0 帧不一致、0 次通信失败。
  服务进程不变、重启 0 次、日志报错增量 0；预热后内存恒定 93.6 MB（无泄漏）；温度 PS 38.6 °C / PL 36.3 °C。
  报告见 `tcp_image_ui/board_check_v5/稳定性测试报告_2026-10-09.md`。
- 测试前板子曾掉线一次，重启后恢复。板子没有实时时钟，开机后日期回到 2021 年，掉线那次的日志没有保存，原因不明。
  注意：用 `journalctl --since <时间>` 会把以前几次开机的日志也算进来，要用 `-b`。

## 9.43 F5 连续帧配准：分块求和（b7）+ 模型 5（v5.3，2026-10-09，**已上板验收**）

- 协议：flags **b7 `FLAG_REG`**。AI 跑完后 PS 把 logit 图切成 4×4 块，各块有符号求和，作为 96 字节块（`'REG1'`、版本、网格、输出宽高、耗时、16 个 i32 和）
  跟在统计块和形态块之后，要同时带 b3；ROI 帧不算。统计块 **version = 6**；回显的 flags 里只有真的跟着这个块时 b7 才是 1，上位机据此决定要不要收。
  代码：`src/ai_view.c` 的 `ai_view_fill_reg`、`src/tcp_image_server.c` 的 3c 段，`test/ai_view_count_test.c` 增加分块求和用例。
- 模型库加第 5 号 `reg_absdiff`（`E:/PLD/echo/deploy/reg_absdiff`，手工构造的 \|R−G\| 参数，默认 512 档，门限 8）。0–4 号模型源码只增不改。
- 上位机：`proto.py` 加 `FLAG_REG` / `unpack_reg` / `expects_reg_block`，以及模型 5（hidden）；`imagelink.py` 收这个块；
  新增 `registration.py`（候选编码、三步搜索、全搜索）、`regwin.py`（「连续帧配准」窗口，从序列窗口打开）。
- 部署：v5.3 = `96d506f6…`，`rollback` 回 v5.2 = `b4acc143…`。中间多跑了一次 `prepare`，板上多一个版本目录；两次是同一份源码，哈希相同，回退目标不受影响。
  改前备份 `_backup/SNAPSHOT_20261009_pre_v5_3_reg/`。
- 验收：`echo/verify_board_reg.py`（20 帧 320 个候选逐值一致；已知平移三步搜索 100/100、全搜索 30/30；每候选 38.7 ms）、
  `tcp_image_ui/verify_board_f5_ui.py` 通过；回归见下一行。精度和局限见 `E:/PLD/echo/README.md` 的 F5 一节。
- v5.3 回归（2026-10-09）：心超 F1 验证集 200 帧 + 三模型切换 60 帧、F2 点击输入 2400 个、F3 板上闭环 3716 步、眼底 v4 客户端 75 帧、
  5 模型混合稳定性 1000 帧（`stability_v5.3_1000.json`）、v5 界面验收，全部通过；v5.2 时的记录另存为 `*_on_v5.2.json`。

## 9.44 ① 实时化：紧凑帧 + 板端跟踪状态（v5.4，2026-10-09，**已上板验收**）

- 新控制头 **v6c（`"GM"`，16 字节）**：只传模型要的 w×w 灰度或 RGB，回传统计块、b8 int8 logit、b9 位掩膜（都附 CRC）；
  每个连接有一份**板端跟踪状态**（track 0 无 / 1 跟踪一步 / 2 清空 / 3 本帧结果写回状态）。统计块 **version = 7**。
  代码在 `src/tcp_image_server.c` 的 `receive_v6c` 和 `handle_v6c`，以及 `src/image_proto.h`；**没有改 `unet_ai.c`**。
- 上位机：`proto.pack_v6c`、`imagelink.send_v6c / recv_v6c / v6c_round_trip`；`sequence.py` 自动探测后走实时通路（后台线程 2 帧在途、Pillow 快速绘制、128 网格算面积）。
- 部署：v5.4 = `fb0950e7…`，`rollback` 回 v5.3 = `96d506f6…`。v5.3 的源码在板上 `releases/` 对应的版本目录里。
- 验收：`echo/verify_board_rt.py` 全部逐位一致；**心超 8.6 → 25.8 帧/秒**，界面 5 → 25.5 帧/秒，眼底 512 1.54 → 1.71 帧/秒。
  细节和速度表见 `docs/realtime_compact_frames_and_board_tracking.md`。

## 9.45 ② 算子编译器进模型库 + 板端多程序流水线（v5.5，2026-10-09，**已上板验收**）

- `E:/PLD/opcomp/`：经典算子写成表达式，编译成这张固定网络的权重。量化时中间层 INT16、输出 INT8，用区间上界定量化范围，并加量化锚点。
  9 个算子浮点与参考逐像素相等，整数误差 ≤ 1.7 LSB，HLS 模拟器 0 差异。细节见 `E:/PLD/opcomp/README.md`。
- 模型库加 6–13 号（`op_median3 … op_vessel4`，共 14 个）。7 号以上只能经紧凑帧调用（aiparam 的模型字段只有 3 位）。
- 紧凑帧多程序：控制头第 9 字节为额外程序数（0–3），后面跟 4 字节描述（模型、输入来源、放入哪个通道、门限）；
  b8 依次回所有程序的 logit。跟踪类请求（track 1 / 2）若负载是 RGB，程序 0 用 G 通道，复制成灰度。
- 部署：v5.5 = `5ff72fd3…`（同一天先部署过 `f68e8129…`，又修了上面那条 RGB 语义）。
  **`rollback` 只能退到 `f68e8129…`**；要回 v5.4（`fb0950e7…`），用板上 `releases/v5_20261010T055053Z/tcp_image_server`。
- 验收：`E:/PLD/opcomp/verify_board_ops.py` 8 个算子程序、串联、分叉全部逐位一致，1 / 2 / 3 个程序每秒 25.2 / 12.8 / 8.6 次；
  v5.5 回归（实时通路、心超和模型切换、1000 帧稳定性）通过。

## 9.46 ⑤ CT 脾脏进模型库（v5.6，2026-10-10，**已上板验收**）

- `E:/PLD/ct/`：MSD Task09 脾脏 CT（CC BY-SA 4.0），41 例带标注病例按病例分成训练 27 / 验证 7 / 测试 7（测试集未动）。
  输入 = 上 5 mm / 本层 / 下 5 mm 三层放进 R / G / B（W400/L40），上位机 4×4 块平均到 128，经紧凑帧送板。
  验证集三维 Dice：板端掩膜 0.932，上位机双线性放大 + 最大连通块 **0.949**（HD95 4.4 mm，体积误差 3.4%）。细节见 `E:/PLD/ct/README.md`。
- 模型库加 14 号 `ct_spleen`（`gen_unet_models.py` 的 `MODELS`，来源 `E:/PLD/ct/deploy/z3_r128`，INT16 节点 `down_path_1_block_2`），共 15 个；
  生成的 `unet_models.c` 与之前相比只增不改。只能经紧凑帧调用（模型号 ≥ 7）。服务端 `--help` 的模型列表补上 `ct_spleen`，**板端逻辑没改**。
  改前备份 `_backup/SNAPSHOT_20261010_pre_v5_6_ct/`。
- 上位机：`proto.MODELS` 加 `ct_spleen`（`hidden`，`enc="z3"`）；新窗口 `ctwin.py`「CT 整卷分割」（主界面按钮「CT 整卷…」和 AI 菜单），计算部分在 `ct_volume.py`
  （NIfTI 读取并转放射科视图、窗宽窗位、块平均、z3 编码、双线性放大、最大连通块、体积、三维表面渲染），与离线代码逐字节一致。
  不连板自检 `_verify_ctwin.py` 通过。
- 部署：v5.6 = `afa4d34f…`（`deploy_board_v5.py prepare / activate`），`rollback` 退回 v5.5 = `5ff72fd3…`。比特流 `ai_sys.bin` 仍是 `4a744c89…`（未变）。
  部署前板子一度掉线（网口有链路但收不到任何包），重启后恢复。注意本机的 TUN 代理（网卡 Meta）在板子不在时会让 22 / 5060 端口“假连通”，判断板子在不在要看 SSH 横幅。
- 验收：`E:/PLD/ct/verify_board_ct.py`：验证集 519 层板端 logit 与整数参考 0 层不一致，板上算出的 Dice 与离线相同；27 卷 2,146 层平均 25.65 层/秒（每层 PL 37.8 ms）；
  上位机代码路径负载 / 掩膜逐字节相同。界面 `_verify_ctwin.py --board`：spleen_10 整卷 2.16 s，每层与整数参考相同，显示 Dice 0.967。
- v5.6 回归（日志 `tcp_image_ui/board_check_v5/regression_v56/`，改前的验收 JSON 备份在 `echo/board_check/_kept_20261010_pre_v56_regression/` 和 `opcomp/board_check/` 同名目录）：
  心超分割 200 帧 + 三模型切换 60 帧、点击纠错 2400 个输入、序列传播 3716 步、配准 F5、实时通路（紧凑帧 25.8 帧/秒）、8 个算子程序与串联 / 分叉、
  1000 帧混合稳定性（RSS 95.6 MB，0 次重启），**全部逐位一致、通过**。

## 9.47 ⑤ CT 第二阶段 ROI 精分割进模型库（v5.7，2026-10-10，**已上板验收**）

- 做法（`E:/PLD/ct/roi.py`）：14 号粗分割的结果定一个 256×256 框（每卷一个），框内每层 2×2 块平均到 128 交给 15 号 `ct_spleen_roi`；
  输入 R = 本层、G = 粗结果掩膜、B = 上下层平均；输出放大 2 倍贴回、再取最大三维连通块。上位机调度，两遍都在 PL 上算。
  训练用的先验来自三折交叉验证的粗模型（真实的错误）。验证集三维 Dice 0.9491 → **0.9549**，HD95 4.4 → 3.5 mm，体积误差 3.4% → 2.7%。
  另一个候选（框内只放三层图像、不给先验）0.9487，不如单阶段——说明是粗结果带来的全局信息起作用。
- 模型库加 15 号（INT16 节点 `up_path_0_conv_layer_block_5` + `up_path_1_conv_layer_block_2`，输出 scale 4.15），共 16 个；只增不改。
  服务端 `--help` 补一行，**板端逻辑没改**。改前备份 `_backup/SNAPSHOT_20261010_pre_v5_7_ct_roi/`。
- 部署：v5.7 = `68e16b67…`，`rollback` 退回 v5.6 = `afa4d34f…`。比特流 `4a744c89…` 未变。
- 上位机：`proto.MODELS` 加 15 号（`enc="roi_prior"`）；`ct_volume.py` 加 `roi_window / roi_payloads / roi_combine`；
  `ctwin.py` 后处理默认「两阶段」，粗分割跑完自动接着跑精分割，轴位图上画 ROI 框，状态栏分别给两遍的时间。
- 验收：`E:/PLD/ct/verify_board_roi.py` 两遍都 0 层不一致、Dice 与离线相同；27 卷平均每卷 4.15 s（精分割占 33.6% 的层）；
  界面 `_verify_ctwin.py --board --case spleen_47` 通过。v5.7 回归日志在 `tcp_image_ui/board_check_v5/regression_v57/`，
  v5.6 时的验收 JSON 备份在 `echo/board_check/_kept_20261010_v56_regression/`、`opcomp/board_check/` 同名目录。
- v5.7 回归 7 项全部通过。**测试集一次性评估（10-10，已用）**：单阶段 Dice 0.9613 → 两阶段 **0.9678**（最差 0.948 → 0.960，HD95 3.0 → 2.0 mm），
  7 例里 6 例更好；两阶段体积全部低估（平均 −2.5%，单阶段绝对误差 2.0%），记作已知局限。细节见 `E:/PLD/ct/README.md`。

## 9.48 ③ 实时心超工作站：EchoNet-Dynamic 模型进模型库（v5.8，2026-10-10，**已上板验收**）

- `E:/PLD/echonet/`：EchoNet-Dynamic（斯坦福研究使用协议，不得外传）。112×112 灰度帧四周补 8 像素到 128，R / G / B = 前约 40 ms / 本帧 / 后约 40 ms，
  输出裁回 112（全程不重采样）。VAL ED+ES 帧 Dice 0.9066（整数）。逐拍 EF：单平面圆盘法（主轴方向 20 个圆盘）+ 容积曲线找峰谷 + 各拍中位数 + 训练集线性校准；
  VAL 平均绝对误差 5.42（全帧）/ 5.49（实时）。细节见 `E:/PLD/echonet/README.md`。
- 模型库加 16 号 `echonet_lv`（INT16 节点 `first_block_2` + `add`，输出 scale 0.1378），共 17 个；只增不改。服务端 `--help` 补一行，板端逻辑没改。
  改前备份 `_backup/SNAPSHOT_20261010_pre_v5_8_echonet/`。部署：v5.8 = `9484870d…`，`rollback` 退回 v5.7 = `68e16b67…`；比特流 `4a744c89…` 未变。
- 上位机：`proto.MODELS` 加 16 号（`enc="t3"`）；新窗口 `echowin.py`「实时心超工作站」（主界面按钮「心超工作站…」和 AI 菜单），计算在 `echo_ws.py`，
  参数在 `echonet_config.json`（只用训练集定的逐拍参数、校准、质控门限）。实时模式按视频节拍送帧，全帧模式尽快跑；「生成证据报告」写 HTML 到 `reports/`。
- 验收：`E:/PLD/echonet/verify_board_echonet.py`：VAL 2,576 帧逐值一致；60 段整段视频两种模式每帧一致、EF 与离线相同；实时模式每秒 25.7 帧、实时倍数中位数 1.03。
  `verify_ui_code_echonet.py`（界面环境）上位机代码与离线逐项相同；`_verify_echowin.py --board` 界面两种模式通过。
- 踩过的坑：
  1. 验收脚本两次送流之间做分析，可能超过服务端 10 s 空闲超时被断开——每段送流前重连；
  2. venv 的 `python.exe` 只是启动器，结束它子进程还在跑、还占着板子（服务端一次只服务一个连接），要连子进程一起结束；
  3. 后台 PowerShell 作业的命令行是编码过的，按文字匹配找不到，要按进程号结束。

## 9.49 ⑥ PL：V12 + 真双口张量 BRAM，固件 xu2dp（2026-10-11，**已上板验收**）

- 谁做的：队友（⑥）在自己电脑上用 Vivado / Vitis **2025.2** 构建（交付 IP 是 2025.2.1，用户同意同系列）。
  交付包 `E:/PLD/交付包_xu2dp_20261011/xu2dp_20261011.zip`（sha256 `41ecd9dc…`，已核对），解压审阅目录 `E:/PLD/xu2dp_review_20261011/`（先读 `DELIVERY.md`）。
  **没改**：HLS 源码、交付 IP、模型库、PS 服务程序、boot.bin、PL 时钟。
- 改了什么：`ai/bd/build_ai_sys_xu2dp.tcl`（§四 🥇 那份脚本）：张量 BRAM `blk_mem_gen` 真双口 + `axi_bram_ctrl SINGLE_PORT_BRAM=0`，
  conv 用 `ai/build/conv_ip_xu2` 里的 V12（LOAD/STORE 合成一趟，9.29），删掉 `Write_Depth_B`。
  另外 62 个构建 / 部署脚本改成相对路径 + `environment/build-env.bat`（读 `environment/local-tools.json` 的工具路径），差异见包里的 `script_changes.patch`。
  ⚠ **这些脚本改动只在交付包里，还没合进本机工程**：本机 `ai/bd/build_ai_sys_xu2dp.tcl` 仍含 `Write_Depth_B` 和 `E:/fpga/...` 路径。
  （**10-11 夜已合并 60 个**，见 9.50.1。）
  本机要重建时，以交付包里的脚本为准，`local-tools.json` 指向 `E:/AMD/Xilinx/2025.2`。
- 实现（队友电脑）：LUT 46116、FF 37489、BRAM **192/216（不变）**、DSP 258/360（比单口版 −13）；64 个 RAMB36E2 都是 TRUE_DP；
  101.376 MHz 复核 WNS +1.633 ns、WHS +0.004 ns，0 路由错误。
- 产物：`bitstream/ai_sys_xu2dp.bin` `8f8c27fe…`（= 板上 `/lib/firmware/ai_sys.bin`），`.bit` `39afe2d3…`；
  部署记录 `bitstream/deployment_xu2dp_20261011.json`。
- 部署：只换固件文件 + 重启，由开机的 `ai-sys-bitstream.service` 加载（VERSION `0x494D5003`）；`deploy_board_v5.py` 不管比特流。
  **回退**：板上 `cp -a /lib/firmware/ai_sys.bin.before_xu2dp_20261011 /lib/firmware/ai_sys.bin && sync && reboot`（先停服务）；
  PC 上同名备份在 `E:/PLD/board_acceptance_xu2dp_20261011/`。PS 服务的 `rollback`（v5.8 → v5.7）与比特流无关，照常可用。
- 上板验收（`E:/PLD/board_acceptance_xu2dp_20261011/验收报告.md`，证据清单 `evidence_manifest.json`）：
  - 旧、新固件 BoardCheck 都是 31/31 PASS，512 golden 0/262144；
  - 17 个程序专项回归全部逐位一致：心超 200 帧 + 三模型切换、点击纠错 2400 个输入、序列传播、配准、实时通路、眼底 v4 客户端、
    上位机 v5 / F4 界面、8 个算子与串联 / 分叉、CT 两阶段、EchoNet 2576 帧 + 60 段视频两种模式与 EF；
  - 1000 / 5000 帧混合稳定性：0 差异、0 异常、0 次重启，RSS 恒定 100.7 MB。
  - 原 `run_fullres.py` 有旧绝对路径，验收改用独立严格测试 `strict_net_test.c`（链接原驱动和原模型库，原 golden 不重生成）。
  - 10-11 晚我又只读核对了一次：板子重启过（新 boot_id），开机仍加载 `8f8c27fe…`，`operating`，服务 / plbram / plbdma 都 active。
- 性能（同一严格测试程序、同图同档，各 10 次，ms）：

  | 档位 | 固件 | ap | put | get | 整网 |
  |---|---|---:|---:|---:|---:|
  | 眼底 512 | V9b | 356.9 | 107.7 | 90.0 | 566.3 |
  | 眼底 512 | xu2dp | **306.9** | **75.2** | **79.6** | **473.0（−16.5%）** |
  | 皮肤 128 | V9b → xu2dp | 24.1 → 20.7 | 6.1 → 4.4 | 6.3 → 5.7 | 37.8 → **32.0（−15.4%）** |
  | 心超 128 | V9b → xu2dp | 24.1 → 20.7 | 6.1 → 4.6 | 6.3 → 5.7 | 37.8 → **32.1（−15.0%）** |

  - TCP 服务端 AI 时间：512 564.2 → 476.1、128 37.8 → 32.2；512 TCP 往返 632.7 → 543.3 ms。
  - 下游：紧凑帧实时通路 25.8 → **30.2 帧/秒**；CT 两阶段每卷 4.15 → **3.54 s**；EchoNet 实时模式实时倍数中位数 1.03 → **1.19**。
  - 比指导文档预计的 8% 多一倍：`ap` −50 ms 与 V12 的预期（−45 ms）相符，多出来的是 **`put` −32 ms、`get` −10 ms**。
    PS 程序没变，微基准（`_memtest` / `_plbram_bench`）里 PS 写 BRAM 14.8 → 9.9 ns/字（1.08 → 1.62 GB/s）、读 197.8 → 187.8 ns/字，
    所以是 BD 改动带来的。推测是 `axi_bram_ctrl` 从单口模式换成双口模式（读、写各走一个 BRAM 口），**机理没验证**。
  - `Fz3b_BoardCheck` 自己报的整网是 656 → 567 ms（单次，`get` 171–180 ms，计时口径和上表不同，原因没查），不要和上表混用。
- 对 CPU 基线的影响：FPGA（1 核）512 476 ms 对 4 核 NEON 565 ms，128 32.2 对 34.8 ms，**从打平变成略快（1.19× / 1.08×）**，
  `tcp_image/baseline/README.md` 已改；措辞仍要克制（CPU 逐元素算子没向量化）。
- 下一步口径（替换 §四 里的旧数）：`ap` 307 ms 里 eltwise 约 49 ms（10-06 的数，xu2dp 没重测分解）；
  提频 101 → 148 MHz 只作用于 `ap`，约省 307 × (1 − 101/148) ≈ **98 ms**（≈ 新整网的 21%），仍要换 boot.bin。

## 9.50 本机 PL 构建环境 + P2 eltwise E2/E2b，固件 xu2dp_e2b（2026-10-11 夜 ~ 10-12，**已上板验收、已部署**）

### 9.50.1 本机能重建 PL 了

- 用户在本机装了 Vivado / Vitis **2025.2**（`E:\AMD\Xilinx\2025.2`；交付 IP 是 2025.2.1，同系列，按用户决定允许）。
- 队友交付包里改成相对路径的 62 个脚本，合并了 **60 个**进 `tcp_image_rx`（`ai/`、`ai/bd/`、`ai/opt_hls/`、`ai/net/`、`bd/`、`boot/`、`vivado/`），
  改前原件备份 `_backup/SNAPSHOT_20261011_pre_portable_scripts/`（含 `merge_log.json`）。
  **没合**：`tcp_image_ui/run.bat`（队友版要求根目录 `.venv`，会弄坏本机 srtp-vision 的界面启动）和 `boot/deploy_board_v5.py`（只改了参数解析）。
- 新增 `E:\PLD\environment\`：`env.bat` / `build-env.bat` / `local_config.py` / `check_toolchain.py` / `probe_fpga.bat` / `inspect_*.bat|tcl`；
  `local-tools.json` 的 `tools_root` = `E:/AMD/Xilinx/2025.2`；本机没有根目录 `.venv`，`env.bat` 退回用 `skin\.venv`。
- 预检 `environment\probe_fpga.bat`：`PREFLIGHT_OK`、`EXITCODE 0`、无 ERROR / CRITICAL WARNING（工具版本检查报 WARN：2025.2 对 2025.2.1，同系列放行）。
- 已验收 xu2dp 那次构建的日志和报告也拷进了 `vivado/`（`ai_sys_xu2dp_build.log`、`_util.rpt`、`_timing.rpt`）。

### 9.50.2 P2：eltwise E2 → E2b（源码 `ai/opt_hls_elt/`、`ai/opt_hls_elt_e2b/`，细节见各自 README）

- **先量化**（xu2dp 上逐节点，去掉冷启动的均值）：eltwise 34.7 ms = Add 17.2 + Concat 12.4 + MaxPool 5.1。
  **双口 BD 本身已经让 Add 降到 0.65×（读口下限 2 读/字）**，P2 能动的只剩 Concat、MaxPool。
- **E2**：Concat 行缓冲拆两半乒乓、“写本行 + 读下一行”合成一趟；MaxPool 三趟变两趟；Add 不动。接口 / 寄存器映射 / m_axi 口名 / 整数运算不变（驱动头文件逐行相同），BRAM 不变。
  验证：C 仿真 3058 万个值（26 组 golden 向量 + 176 组 PS 条带形状 + 300 随机 + 5 强制）0 差异；综合全部 II=1、突发全部推断；
  RTL 协同仿真 476 万个值 PASS。上板 17 个程序全部逐位通过，**但 Concat 慢了 6.5%**（MaxPool 0.82× 如设计）。
- **原因（推断）**：Concat 合并循环先只吃 a、后只吃 b，而 `gmem_a` / `gmem_b` 默认各 16 个在途突发，互连轮流服务 → b 的数据和 a 交替到达，
  前半趟 a 只拿到一半读带宽。协同仿真的存储模型每个口独立，看不出这个问题。
- **E2b** = E2 + 两个读口 `num_read_outstanding=2`（只改两个 pragma）：Concat 0.876×，与“b 只抢先 128 拍”的估算相符。
  读 FIFO 变小，**整系统 BRAM 192 → 188**。
- 结果（同一严格测试程序，各 10 次；逐节点去掉冷启动）：

  | | xu2dp | E2 | **E2b** |
  |---|---:|---:|---:|
  | Add / Concat / MaxPool（µs） | 17 220 / 12 440 / 5 061 | 17 632 / 13 251 / 4 173 | 17 224 / **10 893** / **4 163** |
  | 512 整网 ap / 整网 | 306.9 / 473.9 ms | 306.8 / 474.5 ms | **304.5 / 473.1 ms** |
  | 512 TCP 板端 AI / 往返 | 474.4 / 542.9 ms | 472.9 / 540.4 ms | **471.6 / 538.1 ms** |
  | 128 档 | 32.0 ms | 31.9 ms | 31.9 ms（eltwise 太小，看不出） |

  **P2 实际收益：`ap` −2.4 ms（整网约 0.5%）+ BRAM −4 块**，远小于“eltwise 49 ms 能省一部分”的印象（Add 的收益早被双口拿走了）。
  `put` 本身每次有 ±1 ms 波动，所以“整网”一栏的差别比 `ap` 小。
- ⚠ 坑：`UNET_PROFILE` 日志里每次运行一张逐节点表，**第一张是冷启动**（整网 ap 慢 2–3 ms）。验收脚本原来取“总和最大的那张”恰好取到冷启动，
  一度把 E2b 的 Concat 收益算没了；`parse_profile` 已改成去掉第一张求均值。

### 9.50.3 构建 / 实现

| | xu2dp | E2 | **E2b** |
|---|---:|---:|---:|
| bin sha256 | `8f8c27fe…` | `bb7f6675…` | **`3bbd9396…`** |
| LUT / FF | 46 116 / 37 489 | 47 400 / 38 113 | 47 313 / 38 037 |
| BRAM / DSP | 192 / 258 | 192 / 262 | **188** / 262 |
| 101.376 MHz WNS / WHS | +1.633 / +0.004 | +1.549 / +0.017 | +1.656 / +0.013 |

本机构建（2 并发）约 30 分钟；101.376 MHz 复核 `environment\inspect_xu2dp_e2b.bat`：0 路由错误、0 未约束端点、张量 BRAM 64 个 TRUE_DP。

### 9.50.4 上板验收与部署（`E:/PLD/board_acceptance_e2/`、`E:/PLD/board_acceptance_e2b/`）

- **E2 和 E2b 各跑了一遍全套验收，都通过**（各 15 项：BoardCheck 31/31、512 golden 0/262144、严格测试、17 个程序专项回归、1000 / 5000 帧混合稳定性 0 差异 0 重启、RSS 恒定 100.8 MB）。E2b 报告：`E:/PLD/board_acceptance_e2b/验收报告.md`；部署记录 `bitstream/deployment_xu2dp_e2b_20261012.json`。
- E2b 下游：紧凑帧实时通路 30.5 帧/秒、CT 两阶段每卷 3.49 s、EchoNet 实时倍数中位数 1.21（xu2dp 时 30.2 / 3.54 / 1.19）。
- **用户决定保留 E2b**（10-12）：板上现在 `/lib/firmware/ai_sys.bin` = `3bbd9396…`。

- 部署 = 换 `/lib/firmware/ai_sys.bin` + 重启（`ai-sys-bitstream.service` 加载）；PS 服务仍是 v5.8 `9484870d…`。
- **板上回退链**（都在 `/lib/firmware/`）：`ai_sys.bin.before_e2b` = E2 `bb7f6675…`；`ai_sys.bin.before_e2` = xu2dp `8f8c27fe…`；
  `ai_sys.bin.before_xu2dp_20261011` = V9b `4a744c89…`。回退：停服务 → `cp -a <备份> ai_sys.bin && sync && reboot`。
- 验收脚本可复用：`board_acceptance_e2b/acceptance.py baseline | deploy_tests`（换常量即可；失败自动回退）。
- 板子 10-11 21 点左右掉线过一次（ping / ARP 都没有），用户重新上电、插好网线后恢复；原因不明。

### 9.50.5 下一步

- eltwise 剩下的 Concat 空间约 2 ms，不值得再投入。
- 最大的杠杆仍是 **P3 提频 101 → 148 MHz**：`ap` 304.5 ms × (1 − 101.4/148) ≈ **−96 ms**（整网约 −20%），要重做 boot.bin（见 §四 🥉、PL 优化指导第三节），
  先在实现里看 148 MHz 能否收敛；动 boot.bin 前要有 SD 卡完整备份。

## 9.51 程序包：运行时安装模型，不重编服务端（v5.9，2026-10-12，**已上板验收**）

**做了什么**：以前每加一个模型都要重新生成 `unet_models.c`、重编、重新部署服务端。现在模型可以打成**程序包**（`.upk` 单文件），
拷进板上的 `/root/tcp_image_rx/programs/`，发 SIGHUP 就生效；服务端不重启、比特流不动。

| 部分 | 文件 | 说明 |
| --- | --- | --- |
| 驱动入口 | `src/unet_ai.c/.h` | 新增 `unet_use_model(const unet_model_t *m)`：按指针选模型；结构（kind、输入、mode、通道、尺寸）必须与 `unet_models[0]` 相同，否则返回 -1。调度和 PL 代码没动 |
| 板端加载器 | `src/progpkg.c/.h` | 解析、CRC-32 校验、编号 17–254 唯一、名字唯一、每层参数长度必须与内置相同、**安装即自检**：在 PL 上跑包里的测试输入，logit 必须和包里的期望值逐位一致才注册。结果写 `programs/LOADED.txt`（写完才改名，读到的一定完整） |
| 服务端 | `src/tcp_image_server.c` | 紧凑帧（v6c）的 `model0/model1/extra` 都可以用包的编号；`--programs <dir>`（默认 `/root/tcp_image_rx/programs`）；SIGHUP 在两帧之间重新加载（有空闲连接时，等它 10 s 超时后再加载） |
| 打包 | `ai/net/make_program_pkg.py` | 从部署目录（`network.txt` + `params/`）打包；期望 logit 用 HLS 逐位 NumPy 参考算；`--builtin <name>` 可把内置模型重新打包；`--inspect` 查看 |
| 安装 | `boot/install_program.py` | `install / remove / list / reload`，等 `LOADED.txt` 并检查每个包都是 OK |
| 测试 | `test/progpkg_host_test.c`、`tcp_image_ui/verify_board_programs.py` | 主机端 17 项（替身驱动：各种拒绝路径）；上板验收见下 |

**格式**（小端，详见 `src/progpkg.h`）：64 B 头（`UPK1`、版本、编号、默认档位 / 阈值、ROI、自检档位、各段长度、正文 CRC-32、名字）
+ 30×32 B 节点表 + 参数偏移 / 长度 + 参数 + 元数据 JSON（用途声明、来源、参数 sha256 等）+ 自检输入 + 期望 logit。一个 128 档包约 108 KB。

**上板验收**（`tcp_image_ui/board_check_v5/programs_acceptance.json`，`PROGRAMS_BOARD_PASS`）：
- A：17 个内置模型全部重新打包（编号 32–48）并安装，17 个自检全部 0/16384 差异，重载约 1.4 s。
- B：每个模型 3 个输入，**包 = 内置 = HLS 参考**三方逐位一致（眼底、reg_absdiff 在 512 档，其余 128 档）。
- C：多程序流水线（op_median3 → echo_lv）用包和用内置结果相同；D：板端跟踪（echo_lv 复位 + echo_seq 4 步）相同。
- E：未知编号 → 断开该连接，服务继续可用。F：改了 1 个期望 logit 的包被**板上自检**拒绝（1/16384 差异）；损坏的包被 CRC 拒绝；两者编号都不可用。G：删包 → 编号消失，重装 → 恢复，全程不重启。
- H：耗时相同（眼底 512：内置 473.4 ms / 包 470.4 ms；128 档都是约 31.9 ms）。
- 验收结束时删掉了所有测试包，板上 `programs/` 现在是空的。

**部署**：v5.9 = `785ed9ca…`（`deploy_board_v5.py`，release `v5_20261012T133108Z`），`rollback` 退回 v5.8 `9484870d…`。
比特流仍是 xu2dp_e2b `3bbd9396…`。第一次部署（`6d80a684…`）发现 `LOADED.txt` 会被读到半截，修复后先回滚到 v5.8 再重新部署，所以回滚链是干净的。

**回归**（`E:/PLD/board_acceptance_v59_programs/`，13 项，沿用 e2b 验收的脚本；仓库里原有的验收报告先备份、跑完还原，v5.9 的报告只放在该目录）：**13/13 全部通过**（`REGRESSION_V59_PASS`）：心超分割、点击纠错、序列传播、配准、实时化、眼底 v4、主界面、EF 界面、编译算子、CT 两阶段、EchoNet（60 段视频）、EchoNet 界面代码、1000 帧混合稳定性（0 差异、0 重连、同一进程、日志无新增错误）。

**板上环境（10-12 查到）**：python3 有 numpy 1.13.3、scipy 0.19.1、PIL 5.1.0、cv2 3.4.3、gi 3.26.1；`zlib.h`、`jpeglib.h` 都在；
4 核，系统可见内存约 1.9 GB；根分区只剩约 880 MB。本机连板子：`skin\.venv` 或 anaconda base 的 Python，加 `tcp_image/.board_tools` 到 `sys.path`（paramiko 在那里）。

**用法**：
```
python ai/net/make_program_pkg.py --deploy <部署目录> --name <名字> --id <17..254> --shift 2 --thr 1 --out x.upk
python boot/install_program.py install x.upk
```
客户端用紧凑帧把 `model0` 设成这个编号即可。旧的 v4/v5 整帧协议只有 3 位模型字段，用不到包。

**还没做**：上位机界面里没有"已安装程序"列表和安装入口（现在用 `install_program.py`）；程序包还不带输入编码和后处理链的描述（构想文档 §4.2 的完整设想）。

## 9.52 PL 后处理 IP `pp_layer` 进比特流：固件 xu2dp_e2b_pp（2026-10-12，**已上板验收、已部署**）

**是什么**：第三个 HLS 算子，读张量 BRAM 里的 int8 logit（PS 原样拷入，16 个/字），在 PL 上做阈值、整数双线性 ×2/×4 放大（CT）、
4/8 连通计数、最大连通块 + 填洞（心超 clean）、4×4 分块和（F5）、位掩膜输出。源码和验证：`ai/pp_hls/`（README），
可行性：`docs/pl_postprocess_pipeline_feasibility_2026-10-12.md`。512 帧压缩后正好占满 256 KB，所以 512 时分两带调用（状态在调用间保留）。

| 项 | 结果 |
| --- | --- |
| 比特流 | `bitstream/ai_sys_xu2dp_e2b_pp.bin` = `21aa27b6…`，BD 脚本 `ai/bd/build_ai_sys_xu2dp_e2b_pp.tcl`（= e2b + pp_layer：控制 `0xA003_0000`，`ai_data_ic` S07/S08） |
| 时序 / 资源 | WNS +1.466 ns，WHS +0.015 ns；LUT 56 281（79.8%），BRAM 199/216，pp_layer 自身 LUT 8 125、DSP 28、BRAM 4×36 + 14×18 |
| 板上 182 个向量（`test/pp_board_test.c`） | **全部逐位一致**（`PP_BOARD_PASS`） |
| 板上耗时（PL / 含拷入拷出） | 眼底 512 计数 3.4–4.4 / 4.1–5.1 ms（同板 PS `ai_view_count` 3.9–5.2 ms，含拷贝基本持平，价值在于不占 CPU）；CT ×4 3.4–3.9 / 3.9–4.4 ms；×2 约 1.2 ms；心超 clean 约 0.6 ms |
| 原有功能 | BoardCheck 31/31（含 512 golden 0/262144）；严格比对 4 组通过；17 个程序回归 13/13（`REGRESSION_PP_PASS`）；1000 帧稳定性通过；AI 512 整网 472.7 → 473.9 ms（不变） |

**安全**：PS 访问没有从机的 PL 地址会永久挂死，所以 `pp_ai.c` 只在 `/lib/firmware/ai_sys.bin` 的 sha256 列在
`/lib/firmware/ai_sys_pp.sha256` 里时才允许碰 `0xA003_0000`（`pp_firmware_ok()`）。在 e2b 上验证过会拒绝（退出码 3）。回滚比特流后指纹对不上，自动不用。

**部署 / 回退**：验收脚本 `E:/PLD/board_acceptance_pp/acceptance.py`（baseline / deploy_tests，失败自动回滚）。
板上回退链：`/lib/firmware/ai_sys.bin.before_pp` = e2b `3bbd9396…`（再往前见 9.50）。回退：停服务 → `cp -a ai_sys.bin.before_pp ai_sys.bin && sync && reboot`。
服务端仍是 v5.9 `785ed9ca…`，**还没有调用 pp_layer**（下一步：服务端接入 + 协议 + 上位机，见 `ai/pp_hls/README.md`）。

## 9.53 服务端 v5.10 + 上位机：PL 后处理接入实际流程（2026-10-12，**已上板验收**）

**协议**（统计块 version 8）：紧凑帧 flags **b10 `FLAG_C_PP`**。请求在程序列表之后、负载之前多 12 字节
（`u8 op | u8 k | i8 thr | i8 tn | u16 x0, y0, cw, ch`，cw = 0 表示整帧），处理程序 0 的 logit；
回传排在最后：32 字节块（`'PPR1'`、status、count、comps、largest、pl_us、mask_bytes、rows<<16|row_bytes）+ 位掩膜 + CRC16。
紧凑帧统计块 reserved 的 **bit2 = 这块板能做**。上位机 `imagelink.pp_available` 只有看到一帧 v8 回复且 bit2 = 1 才发 b10
（老服务端不认那 12 字节，会把协议搞乱；每次重连清零）。

**服务端**（`src/tcp_image_server.c` `send_pp`）：启动时只有固件 sha256 列在 `/lib/firmware/ai_sys_pp.sha256` 里才 `pp_init`
（日志 `[pp] pp_layer ready @0xA0030000`）；`--no-pp` 关掉。做不了时 PP 块 status 置 bit31，流照常对齐。

**上位机**：
- 心超工作站（`echowin.py` / `echo_ws.pp_request / pp_clean`）：每帧请求 112 窗口内"保留最大块 + 填洞"，掩膜和连通块数直接用板上的；
  板子做不了的帧（例如刚连上的头两帧）照旧在 PC 上 `clean`。状态栏显示"最大块 + 填洞：PL x/y 帧"。
- CT 窗口（`ctwin.py` / `ct_volume.pp_request / pp_mask / postprocess_masks / roi_combine_masks`）：粗分割每层要 ×4 放大掩膜，
  第二阶段框内要 ×2；整卷后处理直接用这些掩膜做三维最大连通块（三维连通块仍在 PC，要整卷）。状态栏显示"双线性放大：PL x/y 层"。
- 两边的结果都与原来 PC 上的算法逐位相同，所以可以逐帧混用。

**验收**（`tcp_image_ui/verify_board_pp.py`，`PP_BOARD_UI_PASS`，报告 `board_check_v5/pp_acceptance.json`）：
- 心超 10 段 VAL 视频 1 741 帧：板上"最大块 + 填洞"与 `echo_ws.clean` 全部逐位相同，PL 每帧 0.60 ms；
- CT spleen_2 / spleen_10（非测试集）：×4 粗分割 145 层、×2 第二阶段 46 层逐层相同，整卷最终掩膜也相同；×4 约 3.45 ms/层，×2 约 1.35 ms/层；
- 眼底 512（驱动分两带）：掩膜、像素数、4 连通块数一致，PL 3.9 ms；带 / 不带 b10 往返 504 / 502 ms；
- 坏窗口返回 status 4，下一帧照常。
回归：见下（`E:/PLD/board_acceptance_v510_pp/`）。**16/16 通过**（`summary_final.json`）：原有 13 项（含 1000 帧稳定性）+ `verify_board_pp` + 心超工作站真板（实时 86/87 帧、全帧 174/174 帧在 PL 上做最大块 + 填洞，逐帧容积和 EF 与离线一致）+ CT 窗口真板（spleen_10，状态栏"双线性放大：PL"）。第一次整轮跑在第 4 项时会话中断，仓库证据已按备份还原，日志在 `run1_interrupted/`；第二次整轮里心超一项是我加的状态栏检查判错（状态栏被"证据报告已写到…"覆盖），改成检查 `pp_frames > 0` 后单独重跑通过。

**部署**：v5.10 = `8332ec12…`（release `v5_20261012T180413Z`），`deploy_board_v5.py rollback` 退回 v5.9 `785ed9ca…`。固件不变（e2b_pp）。

## 9.54 板端整病例流程：病例任务协议 v7 + CT 整卷上板 + F5 搜索上板（v5.11，2026-10-13，**已上板验收**）

**做了什么**（`E:/PLD/文档/规划与构想/板端化规划_2026-10-12.md` 的 P1 + P2）：以前一个病例要 PC 一帧一帧地发（紧凑帧），编码、后处理、搜索、决策都在 PC。
现在加了**病例任务**：PC 上传一次（CT 就是 `.nii.gz` 原文件），板上把整个病例做完，只回数值和掩膜。PC 只剩"选文件、显示"。

| 部分 | 文件 | 说明 |
| --- | --- | --- |
| 协议 | `src/job.h/.c` | 控制头 v7 `"HM"`（0x4D48）：24 字节头（任务、flags、参数 / 数据长度、CRC-32）+ 参数 + 数据；回复是一串消息（16 字节头 `"JR"` + 负载）：进度（JSON，任务运行时**至少每 0.5 s 一条**，PC 的套接字超时不会误触发）、数据块（可 zlib）、结果（JSON）、结束。连接在任务后保持，下一个请求可以是任务也可以是帧。与 v2–v6c 并存 |
| 任务 0 `caps` | `job.c` | 报告支持的任务、pp_layer、核数；老板端不认 `HM` 会断开 → 上位机据此自动退回老流程 |
| 任务 1 `ct_seg` | `src/job_ct.c`、`src/cc3d.c` | NIfTI（zlib 解压）→ 放射科视图 → W400/L40（float32，与 numpy 逐位一致，`-ffp-contract=off`）→ 4×4 平均 + z3 → **模型 14（PL）** → ×4 双线性 → 三维 26 连通最大块 → ROI 框 → roi_prior 编码 → **模型 15（PL）** → ×2 → 拼回 → 最大块 → 体积。全部是 `ct_volume.py` 的逐位移植 |
| 任务 2 `reg_search` | `src/job_reg.c` | F5：A、B 两帧（128 灰度）进，三步 / 全搜索的循环在 PS 上跑，每帧 16 个候选在 PL 上算 \|a−b\|，回 (dx, dy)、路径、每个候选的代价。`registration.py` 的逐位移植（含平局规则） |
| 上位机 | `tcp_image_ui/jobs.py`、`ctwin.py`、`regwin.py` | CT 窗口顶栏多了"运行方式"：**板上整病例（默认）** / 逐层流式；配准窗口多了"搜索在板上"（默认勾）。板端太旧时两者都自动退回老流程 |
| 验收 | `tcp_image_ui/verify_board_jobs.py`、`_verify_ctwin.py --board --job` | 见下 |

**三个核一起干活**（`job_ct.c`）：解码线程（解压 → 窗宽窗位 → 视图 → 4×4 平均）跑在 PL 循环前面（第 z 层只等到第 z+o 层解好）；
服务线程只管 PL；工作线程在 PL 跑第 z+1 层时给第 z 层做双线性放大。**PL 是瓶颈**（整卷时间的 81% 是 PL 计算），
所以放大默认放在空闲的 PS 核上：实测用 PL 的 pp_layer 放大反而慢（spleen_10 粗分割 2.20 s vs 1.84 s，因为 pp_layer 和 U-Net 抢同一个 PL），
flags bit5 仍可强制用 PL（验收里跑过一次，结果逐位相同）。第一版单线程是 5.27 s，流水化后 3.30 s（同一卷、同样的回传内容）。

**验收**（开发版二进制临时顶替服务跑，`verify_board_jobs.py` 全量，`JOBS_BOARD_PASS`；部署后回归里又跑了一遍，见下）：
- 配准 30 对（STARE 图上的已知平移）+ 6 次全搜索：板上与 `registration.Registrar`（走紧凑帧）的 (dx, dy)、路径、**每个候选的代价（浮点）完全相同**，已知平移恢复 30/30。
  三步搜索 1.43 s vs PC 驱动 1.52 s（3 帧 512 整网本身就要 1.4 s，省的只是往返）。
- CT 11 卷（验证集 7 卷 + 训练集 4 卷，覆盖层厚 1.5 / 2.5 / 3 / 4 / 5 / 7 / 8 mm、33–168 层、最大 55.9 MB 的文件；**测试集没碰**）：
  4×4 平均、粗 logit（vs 紧凑帧逐层）、粗掩膜、ROI 框、精分割输入、精 logit、最终掩膜**逐位相同**，体积（双精度）**完全相同**；flags 只要最终掩膜时再跑一次，结果相同。

| 病例 | 层数 | 层厚 mm | 精分割层 | 上传 MB | 老路：PC 准备 + 流式 + PC 后处理 = 合计 s | 整病例 s | 其中板上 s（解码 / 粗 / 精 / 连通块） | PL 计算 s | 体积 mL | 3D Dice |
| --- | ---: | ---: | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: |
| spleen_10 | 55 | 5.0 | 21 | 15.7 | 0.30 + 2.51 + 0.84 = 3.66 | **2.98** | 2.82（1.23 / 1.83 / 0.71 / 0.11） | 2.44 | 245.1 | 0.9668 |
| spleen_25 | 87 | 2.5 | 30 | 26.9 | 0.46 + 3.86 + 0.80 = 5.11 | **4.62** | 4.36（1.99 / 2.89 / 1.02 / 0.18） | 3.76 | 98.7 | 0.9578 |
| spleen_41 | 94 | 5.0 | 19 | 31.6 | 0.59 + 3.73 + 0.82 = 5.14 | **4.52** | 4.24（2.22 / 3.11 / 0.65 / 0.20） | 3.64 | 180.5 | 0.9443 |
| spleen_47 | 88 | 5.0 | 26 | 27.6 | 0.60 + 3.76 + 0.89 = 5.25 | **4.51** | 4.24（2.07 / 2.90 / 0.88 / 0.18） | 3.67 | 428.1 | 0.9611 |
| spleen_52 | 112 | 4.0 | 29 | 41.4 | 0.72 + 4.65 + 0.88 = 6.25 | **5.69** | 5.27（2.76 / 3.69 / 0.99 / 0.23） | 4.54 | 183.0 | 0.9750 |
| spleen_56 | 33 | 7.0 | 14 | 9.7 | 0.20 + 1.55 + 0.22 = 1.96 | **1.86** | 1.78（0.74 / 1.13 / 0.48 / 0.07） | 1.51 | 80.5 | 0.9259 |
| spleen_59 | 50 | 5.0 | 27 | 15.1 | 0.35 + 2.55 + 0.46 = 3.36 | **3.00** | 2.85（1.13 / 1.67 / 0.92 / 0.10） | 2.47 | 424.5 | 0.9536 |
| spleen_12 | 168 | 1.5 | 93 | 47.8 | 1.09 + 8.62 + 1.60 = 11.31 | **10.02** | 9.57（3.74 / 5.55 / 3.16 / 0.35） | 8.39 | 450.1 | 0.9818 |
| spleen_16 | 61 | 8.0 | 15 | 22.0 | 0.36 + 2.51 + 0.41 = 3.28 | **3.06** | 2.85（1.49 / 2.03 / 0.51 / 0.13） | 2.44 | 195.1 | 0.9643 |
| spleen_53 | 156 | 3.0 | 25 | 55.9 | 0.90 + 5.97 + 1.12 = 7.99 | **7.30** | 6.77（3.79 / 5.12 / 0.85 / 0.32） | 5.83 | 118.9 | 0.9671 |
| spleen_27 | 119 | 4.0 | 23 | 39.2 | 0.69 + 4.75 + 0.91 = 6.34 | **5.70** | 5.31（2.82 / 3.92 / 0.78 / 0.25） | 4.57 | 147.9 | 0.9640 |

11 卷合计：老路 59.66 s → 整病例 **53.27 s（−10.7%）**，其中 PL 计算 43.26 s。解码与 PL 并行，粗分割阶段只等了几十毫秒。
**如实说明**：快得不多，因为两条路都被 PL（每层 32 ms）卡住；整病例的价值主要在"PC 不参与计算"和"PS 的 3 个空核用上了"，不是速度。
老路的"PC 准备"是在本机（桌面 CPU）上测的，换成弱一点的 PC 会更慢。

**部署**：v5.11 = `d15dd19a…`（release `v5_20261013T040912Z`），`deploy_board_v5.py rollback` 退回 v5.10 `8332ec12…`。固件不变（e2b_pp）。

**回归**（`E:/PLD/board_acceptance_v511_jobs/`，`regression.py`）：**19/19 通过**（`REGRESSION_V511_PASS`，`summary.json`）：v5.10 的 16 项（含 1000 帧稳定性、PL 后处理、心超 / CT 窗口真板）
+ `verify_board_jobs`（部署版再跑一遍全量，通过）+ CT 窗口"板上整病例"模式（spleen_10，板上 3.04 s、合计 3.19 s，参考 logit / 掩膜 / Dice 全对）+ F5 窗口"搜索在板上"。
makefile 多链 `-lz -lpthread`（板上 zlib 1.2.11 有头文件）。

**坑**：
- Git Bash 会把 `/root/...` 参数改写成 Windows 路径：调板上命令的脚本要 `MSYS_NO_PATHCONV=1`。
- `pkill -f <路径>` 会连带杀掉 SSH 里执行它的那个 `bash -c`（命令行里也有这个路径）：用 `pkill -f "[/]root/..."`，并且同一条命令里别再出现原路径。
- WSL 里没有 zlib 头文件，新代码只能在板上编（`/root/jobdev`，开发版用 `devstart.sh / devstop.sh` 临时顶替服务）。

**还没做**（板端化规划 P3 / P4）：EchoNet EF 整段上板（视频解码要先确认板上 libjpeg 与 PC 上 OpenCV/FFmpeg 解码是否逐位一致）、F2 点击只发坐标、F4 Simpson。

## 9.55 板端整病例 P3：EchoNet 整段心超在板上分析（病例任务 `echo_ef`，v5.12，2026-10-13，**已上板验收**）

**做了什么**：病例任务 3 `echo_ef`（`src/job_echo.c`）。PC 解码视频（OpenCV），灰度帧整段上传一次；板上：t3 编码 → **模型 16（PL）** →
112 窗口阈值、最大 4 连通块 + 填洞 → 主轴 20 圆盘容积 → 每帧质控信息 → 中值滤波、找谷（scipy `find_peaks` 的 distance + prominence 逐行移植）、
ED = 谷前最大值 → 各拍 EF 中位数 → 训练集校准 → 整段质控（等级 + 原因位）。逐帧结果可以边算边回（flags bit4，"frame" 块 + 每 0.25 s 一次滚动 EF），
实时模式还可以按视频自身帧率节拍处理（bit5，模拟探头）。上位机：`jobs.echo_ef`；`echowin.py` 顶栏"运行方式"默认"板上整病例"（老板端自动退回逐帧流式）；
质控原因的文字由 `echo_ws.qc_bits / qc_reasons` 统一生成（PC 和板端措辞一致）。

**两个决定**：
- **视频只在 PC 上解码**（用户 10-13 定）：板上 libjpeg 与 PC 上 OpenCV（FFmpeg 解码器）在 2.7% 的像素上差 1–4 个灰度级（20 段实测），没法和离线评测逐位一致，板上解码不做。
- **清理（最大块 + 填洞）默认在 PS 工作线程**，与 PL 跑下一帧并行；用 pp_layer（flags bit2）反而慢（174 帧：6.19 s vs 6.05 s），理由同 CT（9.54）：PL 是瓶颈。

**为什么容积不是逐位一致**（如实说明）：`volume_pca` 的投影轴来自 `np.linalg.eigh(q.T @ q)`。板上已经逐位复现了
LAPACK 选特征向量的约定（`dsteqr` → `dlaev2`，含符号；Python 模拟与 numpy 2 万例逐位相同）和 `q @ v`（OpenBLAS dgemv = `fma(a, vx, b·vy)`，300/300），
但 `q.T @ q` 走 OpenBLAS 的分块 / SIMD 核，结果与 CPU 指令集有关，复现不了。两个端点像素在格点上的"中点像素"正好落在第 10 个圆盘的边界上（很常见），
归哪边取决于最后一位，于是少数帧差一两个像素。最初没复现 LAPACK 的符号时 28/174 帧不同，修正后约 2.5%。

**验收**（开发版顶替服务，`tcp_image_ui/verify_board_echo_jobs.py`，VAL 前 20 段 + 实时模式 5 段，`ECHO_JOBS_BOARD_PASS`）：
- **逐位相同**：板上 logit = 紧凑帧逐帧发（老路）；清理后的掩膜、每帧质控信息（连通块数、面积、碰边、不确定度）= `echo_ws.frame_info`；
  把板上的容积喂给 `echo_ws.analyze`，各拍、EF、心率与板上的分析**完全相同**（medfilt / percentile / find_peaks 的移植是精确的）；质控原因位和文字相同；pp_layer 清理与 PS 清理相同。
- **容差内**：3,525 个分析帧里 89 帧容积不同（最大相对差 3.8e-4）；校准后 EF 最大差 **0.0014 个百分点**；质控等级全部相同；各拍 ED / ES 帧号全部相同。
- 实时 + 节拍模式：174 帧的片子 3.48 s，板上 3.52 s 跑完，最后一帧滞后 35 ms；87 个逐帧块、12 次滚动 EF；结果与非节拍的实时模式相同。
- 耗时：20 段 124.5 s vs 老路（流式 + PC 分析）119.7 s（**慢 4%**：多了 2–3 MB 上传，且两条路都被 PL 卡住——PL 计算 112.9 s，占 91%）。
  和 CT 一样，价值在"PC 只显示"，不在速度。

**部署**：v5.12 = `ce2203cc…`（release `v5_20261013T052304Z`），`deploy_board_v5.py rollback` 退回 v5.11 `d15dd19a…`。makefile 不再链 libjpeg。
回归按用户要求只跑心超（EchoNet）相关的 5 项：`E:/PLD/board_acceptance_v512_echo/`，**5/5 通过**（`summary_final.json`）：
`verify_board_echonet`（60 段）、`verify_ui_code_echonet`、心超窗口逐帧流式真板、`verify_board_echo_jobs`（部署版）、心超窗口"板上整病例"真板。
最后一项第一次判失败，问题在测试不在板子：`_verify_echowin.py --job` 沿用了流式模式的 1e-9 逐位标准去比容积和 EF，
改成上面写明的容差（容积相对差 < 1e-3、EF 差 < 0.01）后单独重跑通过（全帧模式 5/174 帧容积不同、最大相对差 2.5e-4，EF 69.5199 vs 69.5201）；
回归脚本里"状态栏含'板上整病例'"这一条也改成看测试自己的检查项（状态栏最后会被"证据报告已写到…"覆盖，v5.10 回归踩过同一个坑）。
中途用户叫停过一次（第一项进行中），仓库证据没被改动，日志 `run_stopped_by_user.log`。

## 9.56 板端整病例 P4（一）：F4 双平面 Simpson 在板上算（病例任务 `simpson_ef`，v5.13，2026-10-13，**已上板验收**）

**做了什么**：病例任务 4 `simpson_ef`（`src/job_simpson.c`）。上传 4 个左心室掩膜（2CH / 4CH × ED / ES）+ 两个视图的像素间距；
板上：（可选）Pillow NEAREST 回原图尺寸 → （可选）最大 4 连通块 + 填洞 → 各向同性重采样 → marching squares 取第一条轮廓 →
二尖瓣平面（45° 内、中间点距弦 ≤ 8 px 的最长弦）→ 心尖 → 20 个圆盘（半像素步进）→ 双平面 Simpson 容积（mL 取整）→ EF。
回 EDV / ESV / EF、每个掩膜的 20 个直径和步长，以及画图用的几何（原图坐标：二尖瓣平面、中点、心尖、圆盘端点、轮廓）。
序列窗口「算 EF」默认在板上算（`sequence.compute_ef_board`），板端太旧时退回 PC，结果行注明在哪算的。

**怎么做到逐位一致**：先写了一份不依赖库的 Python 移植 `tcp_image_ui/simpson_port.py`，逐块和库对过，再照它写 C：
- Pillow `resize(NEAREST)`：源坐标是 `a2 + a0·0.5` 起**逐次累加** `a0` 再取整（ImagingScaleAffine），不是乘法——400 组随机尺寸逐像素相同；
- scikit-image `find_contours(seg, 0.5)[0]`：二值图上 marching squares（'low' 连接）+ `_assemble_contours` 的拼接规则（合并后保留编号小的那条、首尾相接，取编号最小的存活轮廓）；坐标是半整数，板上用加倍的整数坐标做键——随机 300 张逐点相同；
- `|degrees(arctan2(dr, dc))| ≤ 45` 换成几何等价式 `dc ≥ |dr|`（避开不同 libm 的 atan2 末位差异）——半整数网格上 64 万组穷举相同；
- scipy 清理（4 连通最大块 + 填洞）——随机 200 张相同；其余浮点运算顺序照 numpy（`np.sum` 的成对求和已挪到 `job.c` 的 `job_np_sum`，心超和 Simpson 共用），`-ffp-contract=off`。

**验收**（开发版顶替服务，`tcp_image_ui/verify_board_simpson.py`，`SIMPSON_BOARD_PASS`）：
- A：CAMUS 训练 + 验证集 **450 例专家掩膜**：板上 EDV / ESV / EF 与 `ef_simpson.volumes` **450/450 相同**，每个掩膜 20 个直径和步长也逐位相同；
  板上 EF 复现 CAMUS 参考 EF **450/450**。每例板上 150 ms，PC 上 1.3 s（板上快 7.7 倍）。
- B：界面路径（验证集 50 例的 F3 预测掩膜，512 上传、板上回原图尺寸 + 清理，面积最大 / 最小帧作 ED / ES）：与 `eval_f4.py`（f3_auto）**50/50 相同**，EF MAE 5.30（与原评测一致）。
- 测试集没碰。

**部署**：v5.13 = `427b1fab…`（release `v5_20261013T160948Z`），`deploy_board_v5.py rollback` 退回 v5.12 `ce2203cc…`。
回归只跑这次动到的部分：`E:/PLD/board_acceptance_v513_simpson/`，**4/4 通过**（`REGRESSION_V513_PASS`）：序列窗口 F3 → F4 真板（3 例与离线逐位相同，结果行注明「Simpson 在板上算」，187–454 ms）、Simpson 全量（部署版再跑一遍）、echo_ef 全量、CT / F5 任务快速。

**还没做**：P4（二）F2 点击纠错只发坐标——主界面现在靠 v4 整帧协议回的 512 图像显示纠错结果，改成只发坐标要连带改 `app.py` 的显示，范围待定。

## 9.57 两个决定（2026-10-13，用户定）：F2 不再上板；整病例任务的后处理默认放回 PL（v5.14）

**依据**：赛题原文（`E:/PLD/917c3753-….docx` 选题指南）要求"作品的核心数据通路（影像处理和 AI 推理）主要用 FPGA 可编程逻辑实现，
不得仅由片上处理器或上位机完成"，并希望 FPGA 承担"图像预处理、模型推理、几何变换、特征提取、指标计算、图形叠加等关键任务"；
扩展要求里写明交互式分割可以"在上位机选择 ROI、点击提示，FPGA 实时更新分割掩膜"。评分表：基本指标 60（系统完整 10、主要功能 20、性能 15、交互 15）、
拓展指标 40（优化改进 15、可扩展 15、实际应用 10）、加分 20（创新 10、演示 10）。

1. **F2 点击纠错不再搬上板**（方案 B）：模型推理本来就在 PL，点击锥编码只是输入编码，搬不搬都不影响"核心计算在 PL"，
   而且赛题明确接受"上位机点击、FPGA 更新"。改成只发坐标要连带改主界面的显示，收益只在交互延迟，不划算。报告里照此写。
2. **CT / 心超整病例任务的像素后处理默认改回 PL 的 pp_layer**：CT 的 ×4 / ×2 双线性放大、心超的最大块 + 填洞。
   §9.54 / §9.55 当时按速度选了 PS 工作线程（PL 是瓶颈，pp_layer 会占 PL 时间）；现在按赛题"FPGA 承担关键任务"改回 PL，结果逐位相同。
   - 协议：CT flags bit3 = 改用 PS 工作线程（bit5 旧的"用 PL"保留但已是默认）；心超 flags bit2 = 改用 PS 工作线程（**v5.12 / v5.13 板端 bit2 的意思相反**，
     上位机界面不带这个位，所以新老板端都能用，只是落在不同的地方）。
   - 代价（实测）：CT spleen_10 粗分割 1.84 → 2.20 s，spleen_25 整病例 5.25 s ≈ 老路 5.20 s（原来快约 10%）；心超 4 段慢约 3%；实时节拍模式最后一帧仍只滞后 34 ms。
   - 验收脚本改为默认要求"每层 / 每帧都由 PL 做"，并用 PS 工作线程版交叉核对。

**部署**：v5.14 = `29c37ba9…`（release `v5_20261013T165034Z`），`deploy_board_v5.py rollback` 退回 v5.13 `427b1fab…`。
回归（只跑动到的部分）：`E:/PLD/board_acceptance_v514_pl_default/`，**4/4 通过**（`REGRESSION_V514_PASS`）：CT + F5 任务全量（11 卷每层都在 PL 放大）、echo_ef 全量（每帧都在 PL 清理）、CT 窗口 / 心超窗口"板上整病例"真板（窗口里同样全部在 PL 上做）。

## 9.58 服务端 v5.15：临床测量（2a 心超 / 2b CT 脾脏）+ 驱动兼容行缓冲预处理（2026-10-13，**已上板验收、已部署**）

**两件事一起进了 v5.15**：
1. **临床测量**（线 2，只做"辅助测量与复核"，不下诊断）。定义、门限、出处和验证集结果都在 `docs/clinical_measurements_2026-10-13.md`，这里只记要点：
   - 2a 心超（`job_echo.c` 结果多 `"clinical"`，PC 端 `echo_ws.clinical`）：LVEF 三档（≤40 / 41–49 / ≥50，2021 ESC）、距分界 < m 标"建议复核"、逐拍 EF 标准差 / 极差、
     由相邻 ED 帧推出的 RR 间期（**EchoNet 没有心电图**）、RR 变异系数、不规则提示"建议多拍平均（≥ 5 拍）"、指数心拍（只作参考：VAL 上单拍误差 10.5 > 中位数 7.7）。
     门限只在 VAL 上定（`E:/PLD/echonet/eval_clinical.py`）：m = 6.0（全帧）/ 6.5（实时），RR 变异门限 0.276 / 0.225；写在 `echonet_config.json` 的 `"clinical"`。
     echo_ef 参数 104 → 128 字节（v5.14 及以前只读前 104 字节；PC 用板上的逐拍结果自己算，界面注明在哪算的）。
   - 2b CT 脾脏（`job_ct.c` 结果多 `"spleen"`，PC 端 `ct_volume.spleen_measure`）：头尾长度、最大轴位宽（Feret）/ 厚、脾指数；阈值在 PC 上套：
     **按体重 3.01 × kg + 127 mL（> 125 kg 固定 503 mL）**（原文是 3.01，不是任务清单写的 3.0）、"约 314 mL 轻度脾大"经核对是误读（10-14：Radiopaedia 原文 314.5 是公式 30 + 0.58 × L × W × T 估算体积的健康人上限；该公式和脾指数 120–480 都是手工测量定义，
     本系统自动测量下在 VAL 标注上公式体积平均偏大 1.64 倍，所以都不套用，脾指数只显示数值；头尾长度保留 > 10.5 / > 14.5 cm 提示，见 `docs/clinical_measurements_2026-10-13.md`）、
     随访体积变化 % 与 SVR35（≥ 35%，维持 ≥ 12 周）。误差带用 VAL 7 卷两阶段的 −1.6% ± 3.2%。MSD 没有同一患者两次扫描，随访只演示计算，界面写明。
   - 逐位一致：主机端 C = Python（心超 3,000 组、脾脏 60 个随机体，`test/clinical/`）；上板 VAL 40 段心超 + 10 段实时、CT 7 卷，板上与 PC 复算**完全相同**（`tcp_image_ui/verify_board_clinical.py`）。
   - VAL 7 卷预测 vs 标注：体积 −1.6% ± 3.2%，长 ±6.6%，宽 +2.9% ± 5.5%，厚 ±10.2%，脾指数 +4.0% ± 9.8% —— 线性测量比体积抖得多，界面以体积为主。
2. **`src/axilproc.c` 认两个 VERSION**：`0x494D5003`（整帧缓冲，现有比特流）走原来的路；`0x494D5004`（行缓冲，r1 比特流，见 9.59）走"逐行交替 + 模式 0/4 送两遍"。
   **v5.14 遇到 VERSION 4 会直接退出**（`axilproc_init` 失败 return 1），所以换 r1 比特流之前必须先上 v5.15；回退比特流后 v5.15 照常工作（实测）。

**部署**：v5.15 = `2f6ec0aa…`（release `v5_20261014T032357Z`），`deploy_board_v5.py rollback` 退回 v5.14 `29c37ba9…`。固件不变（e2b_pp）。
**回归**（`E:/PLD/board_acceptance_v515/`，仓库证据先备份、跑完核对未被改动）：4/4 通过 —— `verify_board_clinical`（`CLINICAL_BOARD_PASS`）、
`verify_board_echo_jobs`（20 段，容差与 v5.12 相同）、`verify_board_jobs`（11 卷 CT + 30 对配准）、`verify_board_v4`（眼底，含 PL 预处理走新驱动的旧路径）。

## 9.59 FPGA 资源优化 r1：预处理整帧缓冲改行缓冲（1a 保留）、m_axi 缓冲调浅（1b 回退）；固件 xu2dp_e2b_pp_r1a（2026-10-13 ~ 10-14，**已上板验收、已部署**）

**1a 预处理行缓冲**（`rtl_lb/`，`rtl/` 保持原样）：`img_proc_axis.v` 去掉 262144×8 帧缓冲，改成一块 512×16 的行 RAM（{第 y-2 行, 第 y-1 行}）+ 每步 2 拍的引擎
（行末虚拟列 = 右边界复制、帧末冲刷行 = 下边界复制，边界规则与原版逐像素相同）；`img_proc_axil.v` 加 1024×24 输出 FIFO + 打包寄存器。
- VERSION `0x494D5004`。CTRL[3] = 只统计这一遍（模式 0 / 4 先送一遍只统计 min/max，再送一遍出图）；0x14 OUT_LEVEL；STATUS[4] 读空 / [5] 溢出（粘滞）；
  读 OUT_PIX 没攒满时最多等 64 拍（不会无限挂住总线），等不到置 STATUS[4]。
- PS 驱动（`src/axilproc.c`，v5.15，见 9.58）按 VERSION 选走法：逐行交替（写第 r 行、读第 r-1 行），帧末读第 511 行。
- 仿真（`rtl_lb/sim/`，xsim）：新版与原版（`rtl/` 改名 `*_old`）同一张图、同一模式逐字节对比 —— 4 张图（真眼底、随机、常数、窄范围）× 模式 0–5、访问间隔随机 0–24 拍，24 帧全部相同；
  零间隔（PS 读得比 PL 产出还快）12 帧也全部相同，读空 / 溢出位为 0。

**1b m_axi 缓冲调浅**（`ai/opt_hls_conv_r1`、`ai/opt_hls_elt_r1` → `ai/build/conv_ip_r1`、`elt_ip_r1`，原 IP 未动）：突发 16–32、在途 2–4、`config_interface -m_axi_buffer_impl lutram`。
HLS 估计 BRAM18 conv 148 → 128、elt 54 → 30，conv 协同仿真 20 例 PASS；**但实现后只省约 6 块 BRAM36**（conv 39 → 35、elt 21 → 19），
上板 512 档 `ap` 304.5 → 307.4 ms（逐节点几乎都慢 1–3%，合计 +3.25 ms，超出噪声）→ **按规则回退**。eltwise 协同仿真因此中途停掉（没必要了）。

| 构建 | BRAM | LUT | WNS / WHS (ns) | bin sha256 | 结论 |
| --- | ---: | ---: | --- | --- | --- |
| e2b_pp（原） | 199（92%） | 56 281 | +1.466 / +0.015 | `21aa27b6…` | 回滚目标 |
| r1 = 1a + 1b | 130.5 | 56 185 | +1.730 / +0.011 | `8a91b75c…` | AI 慢 1.1%，弃用 |
| **r1a = 1a** | **136.5（63%）** | 56 050 | +1.447 / +0.011 | **`c3d5645c…`** | **已部署** |

预处理模块自身：BRAM 64 → 1.5 块，LUT 843 → 673。省出约 62 块 BRAM，用来做"张量 BRAM 加倍"还是 148 MHz 提频，待用户定。

**上板验收**（`E:/PLD/board_acceptance_r1/acceptance.py baseline|deploy_tests --variant r1a`，失败自动回滚；`state_r1a.json`）：
- BoardCheck（`/root/Fz3b_BoardCheck_r1`，按 v5.15 源码编）31 项、0 FAIL，7 个模式逐字节 0 差异；只有模式 0 因"送两遍"标 WARN（预期）。
  预处理耗时（板上，含 PS 推拉）：模式 0 20.2 → 25.0 ms、模式 4 59.8 → 64.1 ms，模式 1 / 2 / 6 约 +1 ms（逐行交替），模式 3 不变。
- AI：512 `ap` 304.50 → 304.52 ms、整网 471.4 → 472.0 ms，128 `ap` 20.73 → 20.72 ms，逐节点差合计 +72 µs（噪声）；TCP 板端 AI 470.5 → 470.4 ms。
- 严格逐位比对 4 组、pp_layer 182 个向量 `PP_BOARD_PASS`、TCP 逐位 + 耗时。
- 回归 17 组（`regression.py`：原 13 组 + pp / jobs / echo_jobs / clinical）**全部通过**（`REGRESSION_R1_PASS`）。如实记录：第一遍里 pp / echo_jobs / clinical 是我的脚本错
  （用了没有 cv2 的 skin venv），改用 srtp-vision 后通过；realtime 第一遍在最后测速那步遇到一次"板端关连接"（服务没重启、日志被 journald 限流，原因没查到），
  单独重跑两遍都通过（30.3 帧/秒，各部分逐位一致）。中途会话断过一次，回归停在第一项，仓库证据核对过未被改动。

**一个旧问题（不是 r1 引入的）**：原 BoardCheck 二进制里编进去的是一份更老的驱动（反汇编确认：模式 1 推 G 通道），而服务端驱动一直推灰度、界面也说模式 1 是"灰度门限"；
BoardCheck 的参考按 G 算，所以原来测的不是服务端真正在跑的驱动。r1 版 BoardCheck 的 `bc_ref.c` 改成模式 1 也取灰度（产品行为不变），原 `/root/Fz3b_BoardCheck` 保持不动。

**部署 / 回退**：板上 `/lib/firmware/ai_sys.bin` = r1a `c3d5645c…`，sha256 已加进 `/lib/firmware/ai_sys_pp.sha256`（r1 也加了，无害）。
回退：停服务 → `cp -a /lib/firmware/ai_sys.bin.before_r1 /lib/firmware/ai_sys.bin && sync && reboot`（10-13 实测回退过一次，v5.15 在 e2b_pp 上照常工作）。
**服务端必须是 v5.15+**：v5.14 遇到 VERSION 4 会退出。开机脚本 `/usr/local/sbin/load_ai_sys.sh` 改成认 3 和 4（原件 `.before_r1`，本地 `boot/load_ai_sys.sh` 同步）。
构建脚本：`ai/bd/build_ai_sys_xu2dp_e2b_pp_r1a.tcl` / `run_build_xu2dp_e2b_pp_r1a.bat`（r1 的也留着）。

## 9.60 P1 第一天（2026-10-14）：r2 的 vis_layer（配准引擎 + 图形叠加）、1.4 三维重建 C 版、1.6 训练（**进行中，板上什么都没动**）

> 板上仍是 r1a `c3d5645c…` + v5.15 `2f6ec0aa…`，本节所有东西都只在本机。计划见 `E:/PLD/文档/交接与新对话提示词/新对话提示词_P1_2026-10-14.md`。

### 9.60.1 vis_layer（1.1 + 1.2 合一个 HLS 核，源码 `ai/vis_hls/`）

- **接口**：只有 `mem`（一个 128 位 m_axi，读写合用，只看张量 BRAM）+ `desc`（描述符所在字号）两个寄存器；参数全在 BRAM 里的 5 字描述符（`src/vis_layer.hpp` 有字段表）。所有偏移先校验，不合法返回 4、**一个字都不写**。规格 = `visref.py`（逐位）。
- **REG**：A、B 两帧（128 或 256）装进片上 BRAM（B 按行奇偶分两块，双线性要的两行各在一块），每拍 4 个像素：平移 + 1/4 像素整数双线性 + |A−G| + 256 项代价表（PS 装入）；**搜索也在 PL 里**（三步 / ±R 全搜索，可加 ½→¼ 像素细化），候选比较用整数交叉相乘 S1·O2 < S2·O1（与现有 double MAD 的比较**可证明等价**：分母 ≤ 2¹⁶，不同分数之差 > 2⁻³²，远大于 double 间距），同分规则同 `Registrar.best`。回最优位移、路径、每个候选的 (qx, qy, SAD, 重叠面积)。
- **关键发现**：reg_absdiff（模型 5）的 int8 logit **只取决于 |R−G|**（逐位参考跑遍 65 536 对），但不是 `d//2` 之类的公式（30 处不同，量化重标定所致），所以用表；表在 `ai/vis_hls/absdiff_table.json`。装这张表 = 现有 F5 逐位一致；装 f(d)=d = 真 SAD。
- **OVL**：分带合成（每带：底图行 + logit 行 / 位掩膜行 → RGB 行），最近邻取样（= 板端 logit 图 / `ai_view` 的 (x·lw)/ow），alpha 按 1/16 档、填充 / 不确定带 / 轮廓（4 邻域，图外算背景）/ 热力图（256 项伪彩表 PS 装入，每项 4 字节）。**k = 16 时与 `ai_view_overlay_rgb`、`confidence.overlay_rgb / uncertain_rgb / heat_rgb` 逐位相同**。
- **为 LUT 做的取舍（都已体现在规格和向量里）**：16 路 → 4 路（128 全搜索 ±7 约 9 ms，仍比现在 F5 的 15 帧 U-Net 快几百倍）；去掉双线性放大（v1 有，已验证过，代码留 `src/vis_layer_v1.cpp.txt`；带双线性标志的描述符现在返回 4）；alpha 从 0..256 改 0..16。

| 验证（`gen_vectors.py` → `test_native.sh`，WSL g++） | 结果 |
| --- | --- |
| 全量 614 个向量（v1；v2 起的完整集还没重生成，快速集 188 个 v4 全过） | **0 失败**，173 万个期望字 + 每个向量检查"期望区之外一个字都没被改"；45 个还和链接进来的板端 `ai_view_overlay_rgb` 比 |
| 独立 golden | 128 整数：`registration.Registrar` 走它自己的 compose + 分块和；256 / ¼ 像素：Registrar.best + 独立浮点双线性；OVL：confidence.py、ai_view.c、scipy 轮廓、逐像素标量实现 |
| 已知平移（CAMUS VAL 相邻帧，同 `echo/verify_board_reg.py` B） | 三步 **100/100**、全搜索 **30/30**；256（步长 8/4/2/1）12/12 |
| ¼ 像素（平滑帧三次样条移动已知 ¼ 像素） | 10/10 精确找回 |

| OOC 综合（`run_hls_vis.bat evaluate`，100 MHz） | LUT | DSP | 时序 |
| --- | ---: | ---: | --- |
| v1（16 路、双线性、LUTRAM 行缓冲） | 13 037 | 40 | 5.4 ns |
| v2（4 路、去双线性、行缓冲进 BRAM） | 8 871 | 25 | 5.1 ns |
| v3（伪彩表 4 字节 / 项、去除法器） | 7 960 | 19 | 5.1 ns |
| **v4（alpha 1/16 移位加、比较用 DSP、32 位地址）** | **7 319** | 16 | 5.4 ns |

v4 分项：m_axi + 控制口 1.3k、REG 2.4k（计算核 0.55k）、OVL 3.4k（出图循环 1.6k）。BRAM 约 RAMB36 32 + RAMB18 7。

**卡点（待用户定）**：按 v4 估整片 r2 ≈ 56.05k − 1.7k（1.3）+ 0.2k（新 SI）+ 7.7k ≈ **62.2k（88%）**，超过 86%。建议：① 出图循环 RGB 打包改移位寄存器（约 −0.6～0.9k）+ ② pp_layer 两个 m_axi 合成一个（约 −0.8～1.2k，pp 要重跑 182 向量 + 协同仿真 + 上板 PP 回归）。其它选项见当天对话。

坑：跑 HLS 时 Bash 工具的当前目录在 `ai/build/vis_ip/...` 下会导致"permission denied"（§9.50 那个坑的又一次）；HLS 的 clang 在 `BIND_OP` 写在 `if` 块里时会崩（改成单独函数里写）；`ram_t2p` 不能配 `lutram`。

### 9.60.2 r2 BD 脚本（1.3，**还没构建**）

`ai/bd/build_ai_sys_xu2dp_e2b_pp_r2.tcl` / `run_build_xu2dp_e2b_pp_r2.bat`：= r1a + vis_layer（控制 0xA004_0000、数据 ai_data_ic/S09，IP 仓库要求 `ai/build/vis_ip_r2`——**定稿后把 `vis_ip/solution1/impl/ip` 拷过去**）+ 控制互联改为 `pc_axil`（AXI4→AXI4-Lite，32 位）→ `ctl_xbar`（axi_crossbar，AXI4-Lite，1 进 5 出，SASD），去掉 sc_axil，全程无位宽转换。还没跑过 preflight。

### 9.60.3 1.4 三维重建 C 版（`src/surf3d.c/.h`、`src/job_render.c/.h`）

- `surf3d.c` 是 `ct_volume.Surface` / `render` 的逐步移植（scipy zoom order 1、gaussian_filter 的 correlate1d 对称分支、float32 存储、binary_erosion、np.gradient、round half to even、z 缓冲同序同分取先到）。
- **本机核对**（`test/surf3d/compare.py`，WSL gcc，结果 `test/surf3d/surf3d_host.json`）：CT 11 卷标注（VAL 7 + 训练 4，没碰测试集）+ 4 个合成体：平滑体尺寸、>0.5 体素数、表面点数**全部相同**；6 个角度 × 15 例 = 90 张 320² 渲染图**逐像素相同**（max diff 0，`SURF3D_HOST_PASS`）。板上 aarch64 的 libm 可能差末位，所以上板仍按容差验收。C 建表面 6–20 ms（本机），Python 45–107 ms。
- `job_render.c`：新任务 `JOB_RENDER3D = 5`（上传 bit 掩膜 + 间距 + 角度，回 `render3d` 图块）和 `job_render3d()`（给 ct_seg 加 flag bit6 用）已写好、`-Wall -Wextra` 无告警；**还没接进 `job.h` / `job.c` 分发和 `job_ct.c`**（下一步，随 v5.16）。

### 9.60.4 1.6 CAMUS 左房 / 左室心外膜（`E:/PLD/echo`）

- `prep_camus_targets.py`：新缓存 `{train,val,test}_{la,epi}512.npy` + `cells`（la = 标签 3；epi = 标签 1|2，心肌 = epi − lv），图像缓存不动。`train_echo.py` 加了 `--target lv|la|epi`（默认 lv，旧用法不变）。
- **测试集锁文件**：`echo/results/final_test_p1_16/lock.json`（训练前写的）。
- 训练（128 档，20000 步，同 F1 配置），VAL 最佳：

| 模型 | 步 | Dice 全部 | 好 + 中 | 差 | ED | ES |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `la_r128` 左房 | 13 000 | 0.882 | 0.886 | 0.851 | 0.867 | 0.898 |
| `epi_r128` 左室心外膜 | 19 000 | 0.937 | 0.941 | 0.907 | 0.943 | 0.931 |

- 下一步：量化（`quantize_echo.py`）→ HLS 逐位导出 → `.upk` → 测量（左房双平面 Simpson 容积 / LAVI、左室质量 = (epi − endo) × 1.05、壁厚）→ **ASE 原文核对阈值**后再在 VAL 上评测；测试集最后只跑一次。

### 9.60.5 还没开始

r2 构建与上板、服务端 v5.16、上位机、1.5 独立运行、1.7 面板。

## 9.61 固件 r2 `xu2dp_e2b_pp_r2`：vis_layer（PL 配准引擎 + 图形叠加）+ 控制互联改交叉开关（2026-10-15，**已上板验收、已部署**）

> 部署：板上 `/lib/firmware/ai_sys.bin` = r2 **`81d0f4625875eb0971d5ae2fba723e3b93730552fd7fada3aaa5f614bc234bb6`**（`.bit` `24999d44…`）。
> **回滚目标**：r1a `c3d5645c9c35d6fae630caa1d559efe6b2d82360d92585807549354771764169`，板上备份 `/lib/firmware/ai_sys.bin.before_r2`
> （停服务 → `cp -a /lib/firmware/ai_sys.bin.before_r2 /lib/firmware/ai_sys.bin && sync && reboot`）。服务端此阶段仍是 v5.15 `2f6ec0aa…`（不碰 0xA004_0000）。
> 新哈希已追加到 `/lib/firmware/ai_sys_pp.sha256`（pp_layer 地址不变）和新文件 `/lib/firmware/ai_sys_vis.sha256`（vis_layer 的指纹门，`src/vis_ai.c` 的 `vis_firmware_ok()`）。

| 项 | 结果 |
| --- | --- |
| BD | `ai/bd/build_ai_sys_xu2dp_e2b_pp_r2.tcl`：r1a + `vis_layer_0`（控制 `0xA004_0000`，`m_axi_gmem` → `ai_data_ic/S09`）；控制口 `pc_axil`（AXI4→AXI4-Lite）→ `ctl_xbar`（AXI-Lite 交叉开关 1 进 5 出，SASD），去掉 sc_axil，全程 32 位、无位宽转换 |
| IP | `ai/build/vis_ip_r2`（= `vis_ip/solution1/impl/ip`，源码副本 `vis_layer_source_v5b.cpp` sha256 `97bd0148…`）；pp_layer 仍是 `pp_ip_v3`（两口，单口合并实测不省 LUT，已放弃） |
| 资源 | LUT **60 726（86.06%）**、BRAM **172（79.63%）**、DSP 306（85%）；vis_layer 6 220 LUT / 32+7 BRAM / 16 DSP；控制互联 ctl_xbar 202 + pc_axil 406（原 sc_axil + pc 共 2 355，**省 1.75k**） |
| 时序（100 MHz） | WNS **+1.155 ns**，WHS +0.014 ns，0 个严重警告 |
| 板上验收 `E:/PLD/board_acceptance_r2/`（`acceptance.py baseline / deploy_tests`，失败自动回滚） | BoardCheck_r1 31/31 PASS（0 WARN）；严格比对 6 组 STRICT_NET_PASS；pp_layer 182 向量 PP_BOARD_PASS；**vis_layer 592 向量（每个 BRAM 字都查）VIS_BOARD_PASS**，其中 45 个同时与板上 `ai_view_overlay_rgb` 逐位相同，PL 合计 1 466.6 ms；TCP 往返正常；**17 组回归 17/17**（`REGRESSION_R2_PASS`，含 1000 帧混合稳定性 0 差异）；`R2_ACCEPTANCE_PASS` |
| AI 耗时（软件计时，只记录） | 眼底 512 整网 471.6 → 469.7 ms；128 档 31.9 → 31.9 ms（不变） |

**第一次构建作废**（`vivado/r2_build1_cosim_bug/`，`.bin` 改名 `_BUGGY_DO_NOT_DEPLOY`，**从未上板**）：协同仿真发现 RTL 在三步搜索的第一圈跳过了中心点（25 个候选只出 24 条记录），
原因在“中心点是否已评估”的判断：C 仿真里第一圈的中心照常评估，RTL 里却当成已知跳过了；改成 `const bool known = ph > 0;` 后 C 仿真 / 协同仿真 10/10 通过，重新导出 IP、重建比特流。教训：**协同仿真是 HLS 核进比特流前的硬门槛**。

## 9.62 服务端 v5.16 部署（2026-10-15，**已部署**；固件 r2）

> （**10-17 起现役是 v5.17 `51780377…`，见 §9.67**；下面是 v5.16 当时的状态）
> **当前**：`/root/tcp_image_rx/tcp_image_server` = v5.16 **`e11199f3881145a248a8259a28091b9bd116a0970ee4adb5f353b6a78186c60e`**
> （release `/root/tcp_image_rx/releases/v5_20261016T051654Z`，`deploy_board_v5.py prepare / activate`，板上原生编译 0 警告）。
> **回滚目标**：v5.15 **`2f6ec0aa2d03f3d912c007420cd35f078936af3c487893847d59e5aad236d648`**（`deploy_board_v5.py rollback`；v5.15 与 r2 固件一起验收过，可以直接退）。
> **不能再往下退**：v5.14 及以下不认 r1a / r2 的预处理 VERSION 4。固件回滚见 §9.61。

**第一次部署（`5f85a9c0…`，release `v5_20261016T051228Z`）有崩溃，已撤下**：`verify_board_v516.py` 的 ct3d（`ct_seg` 带 `CT_RENDER3D`、views = 12）让服务端段错误
（`job_ct.c` 传 `angles = NULL`，`job_render3d` 照样 `memcpy(ang, NULL, …)`；systemd 自动重启了服务）。修复：没给角度时按 360 / n 均分（`src/job_render.c`）。
处理顺序同 §9.51：先 `rollback` 回 v5.15（`2f6ec0aa…` 校验通过），再 `prepare / activate` 修好的版本，所以回滚链干净（v5.16 → v5.15）。
旧清单留在 `tcp_image_ui/board_check_v5/deployment_v516_crash_render3d_NULL.json`。

**v5.16 相对 v5.15**：vis_layer 驱动（`src/vis_ai.c`，指纹门 `/lib/firmware/ai_sys_vis.sha256`，`--no-vis` 关掉）；配准任务 engine 1 = PL 引擎（256 / ¼ 像素只有 PL 引擎）；
`JOB_RENDER3D = 5`、`JOB_OVERLAY = 6`（PL 合成，PS 回落同规格）；`ct_seg` flag bit6 板上三维渲染、bit7 回窗宽窗位切片（板上界面用）；
`simpson_ef` 的 1.6 扩展（`"cardiac"`）；结果里加 `"pl_split"`（U-Net 计算 / 搬入 / 搬出 / 补零）和 `"pp_ms"`（软件计时，1.7 时间线用）；caps 报 `"server":"v5.16","vis":true,"cardiac":true`。

| 验收 | 结果 |
| --- | --- |
| `tcp_image_ui/verify_board_v516.py`（`board_check_v5/v516_acceptance.json`） | **V516_BOARD_PASS**：caps；配准 PL 引擎 vs U-Net 老路 36 次全同（位移、路径、每个候选代价）、已知平移全找回，**三步 1.26 ms vs 1 428 ms、全搜索 9.5 ms vs 7 139 ms**；256 / ¼ 像素 = `visref.search` 逐位；叠加 PL = PS 回落 = `visref.overlay` 逐字节；板上三维渲染 vs PC `ct_volume.Surface`（前景 xor 0）；ct_seg + 三维：体积不变，建表面 90 ms、每张 15.7 ms |
| 程序包 `boot/install_program.py install programs_p1/camus_{la,epi}.upk` | 17 camus_la、18 camus_epi 板上自检 0/16384 差异，`INSTALL_PROGRAM_PASS` |
| `test/cardiac/compare.py --board` | **CARDIAC_BOARD_PASS**：程序 17 / 18 在 VAL 全部 200 帧上 logit 与整数参考逐位相同；100 个 8 掩膜 `simpson_ef` 请求的全部测量值与 `cardiac_measure.measure` 逐位相同 |
| `tcp_image_ui/_verify_cardiac_ui.py` | `CARDIAC_UI_PASS`（上位机 `sequence.cardiac_board()` 整条路，5 位 VAL 病人） |
| 回归 `E:/PLD/board_acceptance_v516/regression.py`（17 组 + F4 Simpson 上板） | **18/18 通过**（`REGRESSION_V516_PASS`，10-16 上午）：含 1000 帧混合稳定性 0 差异、服务端全程 0 次重启；F4 Simpson 450 例专家掩膜 + 界面路径照旧逐位一致。10-15 晚第一次跑到 f3 中途板子掉线（板上日志在那之前就断了、之后板子被重启），后面 15 组全是连接错误，日志存 `run1_board_lost/`；第二次原样重跑全过 |
| 1.7 时间线 `tcp_image_ui/_verify_timeline.py`（`board_check_v5/timeline_check.json`） | `TIMELINE_BOARD_PASS`：CT（spleen_10）PL / PS 后处理最终掩膜逐位相同，心超（EchoNet VAL）掩膜和容积相同；两路都带 pl_split / pp_ms，时间线画得出。实测（软件计时，各一次）：CT 合计 PL 版 3 417 ms / PS 版 3 027 ms——PS 版的放大在工作线程里与 PL 循环并行，墙钟反而短，PL 版的价值是不占 CPU（PL 后处理 218 ms）；U-Net 计算 46%、搬运 23%。心超 5 769 / 5 642 ms，U-Net 63%、搬运 30%，PL clean 105 ms vs PS 505 ms |

## 9.63 1.6 左房 / 心肌：截断判定 + 训练集校准 + 板端 C 移植 + 程序包（2026-10-15）

**用户定（10-15）**：左房碰到图像下边缘 → 报「左房被截断，不可测」，不给容积 / LAVI；剩余系统偏差用**训练集**拟合线性校准；只在未截断病例上评测。

| 部分 | 文件 | 说明 |
| --- | --- | --- |
| 截断 + 校准 | `tcp_image_ui/cardiac_measure.py` | `la_truncated`：任一切面 LA 掩膜碰到原图最下 `LA_EDGE_ROWS = 2` 行；`LA_CAL = 0.9534 × 原始 + 9.02 mL`；`measure(..., calibrate=True)`（专家掩膜做参照时 `False`） |
| 拟合 | `echo/fit_la_calib.py` → `results/p1_16/la_calib.json`（sha256 前 16 位 `89f79b072179dabc`） | 训练集 400 人 ES 帧跑整数 la_r128（= 板上），两切面都未截断的 192 人最小二乘，r 0.84；训练集截断：专家 187 / 预测 190 人 |
| VAL 评测 | `echo/eval_p1_16.py` → `results/p1_16/eval_val.json` | 50 人全部可量；截断判定 预测 21 / 专家 22 / 一致 21；左房（28 人）校准前偏差 −20.5%、校准后 **−6.2%**、平均相对误差 20.9%、r 0.87；左室质量 −3.8% / 11.5% / r 0.89；壁厚 −1.3 mm / r 0.73（详见 `docs/clinical_measurements_2026-10-13.md` 第 3 节） |
| 板端 C | `src/job_simpson.c`（v5.16） | `simpson_ef` 可多带 4 个掩膜（2CH / 4CH ED 心外膜、2CH / 4CH ES 左房），结果加 `"cardiac"`：截断、原始 / 校准左房容积、心内膜 / 心外膜容积、左室质量、壁厚（各切面）；精确欧氏距离变换（Meijster，整数）= scipy `distance_transform_edt`；caps 加 `"cardiac":true` |
| 主机核对 | `test/cardiac/compare.py` + `harness_card.c` | VAL 50 人 × （专家 + 预测）= 100 个请求，**所有数值与 `cardiac_measure.measure` 逐位相同**（`CARDIAC_HOST_PASS`，`test/cardiac/cardiac_host.json`） |
| 程序包 | `programs_p1/camus_la.upk`（编号 17）、`camus_epi.upk`（18） | `make_program_pkg.py --deploy echo/deploy/{la,epi}_r128 --shift 2 --thr 1`，各约 108 KB，128 档自检 |
| 上位机 | `sequence.py` `cardiac_board()` / `cardiac_text()` | EF 在板上算完后，板上跑 17（ES）/ 18（ED）两切面 → 8 掩膜的 `simpson_ef` → EF 结果下面一行显示左房 / 左室质量 / 壁厚（"辅助测量与复核，不作诊断"）；LAVI / 指数在输入身高体重时才算 |
| 上板 | 见 §9.62：程序包已装、CARDIAC_BOARD_PASS、CARDIAC_UI_PASS |
| 测试集 | `echo/final_test_p1_16.py`（**写好、没跑**） | 先查锁、把冻结输入的 sha256 写进 LOCK 并置 running，再推理（torch）→ 测量（srtp-vision）→ 置 used；**只跑一次，等用户点头** |

## 9.64 部分 A：左室质量指数分类 + RWT 数值（ASE 2015 核对原文）——代码 + 主机逐位一致 + VAL 一致率（2026-10-16；**10-18 随 v5.18 上板，见 §9.72**）

**用户定（10-16）**：**RWT 只显示数值、不套指南的 0.42**；分类只按 LVMI（增高 / 不增高）。原因见下面「为什么不做四型」。

**阈值核对原文**（规则：不凭记忆）：JASE / OUP 原站有机器人校验，打不开；改读 **ASE 官方托管的指南日文全译本**
`https://www.asecho.org/wp-content/uploads/2018/01/ChamberQuant_Japanese.pdf` 第 13–14 页（正文、表 6、RWT × LVMI 分型图——**图的坐标轴是英文原图**）：
- RWT =（2 × 后壁厚）/ 左室舒张末内径；正文 "RWT > 0.42" 向心性、≤ 0.42 离心性；分型图纵轴 "> 0.42 / ≤ 0.42"（图注写 ≧ / ≦）。
- 分型图横轴 LVMI：**≤ 95（女）/ ≤ 115（男）g/m²** 为不增高；图注写明这些左室质量**按线性法**。表 6：线性法 LVMI 女 43–95、男 49–115，RWT 女 0.22–0.42、男 0.24–0.42；
  2D 法 LVMI 女 44–88、男 50–102。BSA 公式指南参考文献里有 Mosteller（本系统一直用它）。

**口径（界面、文档都写明）**：指南按胸骨旁长轴 M 型 / 二维**线性**测量；本系统全部来自**两个心尖切面的二维掩膜**：
壁厚 = 1.6 的「ED 心内膜轮廓到心外膜的距离中位数」；**内径 LVIDd = ED 心内膜 20 圆盘里第 3–6 个（沿长轴 15%–30%，二尖瓣叶尖附近）直径的平均，两切面平均**（看数据之前定的）；
质量 = 双平面 Simpson 心外膜 − 心内膜。LVMI 分类只作「辅助测量与复核」的参考，不作诊断。

| 部分 | 文件 | 说明 |
| --- | --- | --- |
| Python | `tcp_image_ui/cardiac_measure.py` | `LVMI_LIMIT = {F: 95, M: 115}`、`LVIDD_DISKS = (3,4,5,6)`、`RWT_GUIDELINE = 0.42`（只作文档 / 评测对照，不参与分类）；`lvidd_view_mm()`（圆盘直径从左到右相加再除，C 同序）、`lvmi_class(lvmi, sex)`；`measure()` 多 `lvidd_mm`、`lvidd_mm_views`、`rwt`，给了身高体重再多 `lvmi_class`（还要性别，否则 None） |
| 板端 C | `src/job_simpson.c` | `"cardiac"` 多 `lvidd_mm`、`lvidd_mm_views`、`rwt`；**参数可选扩到 56 字节**：32..55 = f64 身高 cm、f64 体重 kg、u8 性别（0 未知 / 1 F / 2 M）、7 字节 0 → 再多 `bsa_m2`、`lvmi`、`lvmi_class`（`{"code":"increased"/"not_increased","lvmi_limit"}` 或 null）。旧客户端发 32 字节照旧；v5.16 板端会忽略多出的参数（只查 ≥ 32） |
| 请求 | `tcp_image_ui/jobs.py` | `simpson_ef(..., patient=(cm, kg, 'F'/'M'/None))` / `simpson_request(..., patient)` |
| 上位机 | `sequence.py` | EF 面板加 性别 / 身高 / 体重 输入；`cardiac_board(..., patient)`；`cardiac_text()` 多「RWT 0.xx（2×壁厚 / 内径 xx mm，二维掩膜口径，只显示数值）｜左室质量指数增高 / 不增高（ASE 2015 线性法上限 xx g/m²；本系统为心尖切面二维掩膜，口径不同）」；LVMI 显示改一位小数（免得 95.4 显示成 95 又报增高） |
| 主机核对 | `test/cardiac/compare.py` + `harness_card.c` | VAL 50 人 × （专家 + 预测）= 100 个请求，每个请求带合成身高体重（`PATIENT()`，确定性，CAMUS 只有性别）；每 10 人 1 人不带参数（旧 32 字节请求）、1 人不带性别。**全部数值（含 lvidd / rwt / bsa / lvmi / LVMI 分类）与 `cardiac_measure.measure` 逐位相同：`CARDIAC_HOST_PASS`**（`test/cardiac/cardiac_host.json`：增高 46、不增高 34、无分类 20）；gcc -Wall -Wextra 0 警告 |
| 上板核对（部署后跑） | `_verify_cardiac_ui.py` | 默认带 170 cm / 65 kg / 男，多比 lvidd / rwt / bsa / lvmi / LVMI 分类；对 v5.16 板端请加 `--v516`（新项会缺） |
| VAL 评测 | `echo/eval_geometry_a.py` → `echo/results/p1_16/eval_geometry_val.json`（sha256 前 16 位 `59837378eba21772`） | 掩膜 / 帧同 `eval_p1_16`；参照 = **同一套测量用在专家掩膜上**（不是超声医师的线性测量）；CAMUS 没有身高体重 → LVMI 用固定 BSA 网格 1.6 / 1.8 / 2.0（专家和预测用同一 BSA，只有掩膜差异会改分类）；不上线的四型结果也算了一份留档（`not_shipped`）；测试集没碰 |

**VAL 结果（50 人全部可量，女 25 / 男 25）**：

| | 预测 vs 专家掩膜上的同一测量 |
| --- | --- |
| LVIDd | 专家均值 45.0 mm，偏差 −0.6 mm（−1.3%），平均相对误差 4.4%，r 0.87 |
| 壁厚 | 专家均值 13.2 mm，偏差 −1.3 mm，r 0.73（同 §9.63） |
| RWT（只显示数值） | 专家均值 0.596（中位 0.584，P10–P90 0.46–0.73），预测偏差 −0.049（−7.7%），平均相对误差 12.7%，r 0.77 |
| 左室质量 | 偏差 −3.8%，平均相对误差 11.5%，r 0.89 |
| **LVMI 分类（95 / 115），BSA 1.6 / 1.8 / 2.0** | **一致 40 / 47 / 48 人（80% / 94% / 96%），κ 0.57 / 0.88 / 0.92** |
| LVMI 分类，2D 法上限 88 / 102（敏感性） | 一致 41 / 42 / 47 人，κ 0.47 / 0.66 / 0.88 |

BSA 1.6 时一致率低：那里多数病人的 LVMI 落在上限附近，预测质量偏小 3.8% 就会翻过去（预测 21 人不增高 vs 专家 13 人）。一致率说的是「板上分割会不会改变分类」，不是「分类临床上对不对」（CAMUS 没有线性测量和身高体重）。

**为什么不做四型（留档，`not_shipped`）**：
- 专家掩膜上 **RWT 49 / 50 人 > 0.42（中位 0.58）**，不是 CAMUS 人群都向心，而是**口径差异**：二维心尖掩膜的心肌宽（壁厚均值 13.2 mm，后壁线性测量正常 6–10 mm）。
  按指南四型的话几乎人人「向心」，四型实际只剩 LVMI 一维；RWT 二分一致 44 / 50 但 κ 只有 0.22；四型一致 BSA 1.6 / 1.8 / 2.0 = 70% / 84% / 86%（κ 0.42 / 0.72 / 0.75）。
- **换壁厚定义也不行（只在训练集上试，没碰 VAL）**：训练集 400 人专家掩膜，壁厚改成「与内径同一水平（圆盘 3–6）沿圆盘线从心内膜量到心外膜、两侧平均」
  （最接近线性法）：壁厚中位 13.4 mm（轮廓中位数法 13.0），RWT 中位 0.60（0.58），> 0.42 的 382 / 400（375 / 400）。偏厚的是掩膜本身的心肌宽度，不是取中位数的方式。原型只在 scratchpad，没进仓库。
- 要有方法专属的 RWT 界值得有带线性测量的参照人群（计划里的可选升级 EchoNet-LVH），本期不做。
- **没有用 VAL 调任何定义或阈值**（LVIDd 的圆盘位置是看数据前定的；RWT 不套阈值是用户按上述结果定的）。

**上板说明（准备好了，没部署；另一会话在用板子 / 做新比特流）**：
- 只改了 `src/job_simpson.c`（+ 上位机 Python），不动比特流。客户端靠结果里有没有 `rwt` 判断新功能。
- **v5.17（J1）已部署、不含部分 A**（10-16，J1 会话告知）：v5.17 `51780377…` 用 `prepare --pin=src/job_simpson.c=E:/PLD/board_acceptance_v517/pinned/job_simpson.c`
  钉的是 v5.16 的 job_simpson.c（sha `74afe8e9…`），工作区这份没上板。
- **部分 A 单独出 v5.18**：部署前把 `src/job.c` 的 caps 字符串 `"v5.17"` 改成 `"v5.18"`，然后普通 `boot/deploy_board_v5.py prepare / activate`（**不带 --pin**，板上原生编译），
  **回滚目标 = v5.17 `51780377…`**。v5.17 回归期间（约 1–2 小时，见共享笔记 `v517_board_busy.md`）不要连板。
- 部署后验收：`test/cardiac/compare.py --board`（会覆盖 `cardiac_board.json`；程序 17 / 18 logit + 100 个请求全部新字段逐位）→ `tcp_image_ui/_verify_cardiac_ui.py`（不加 `--v516`）→ 回归。
- 证据报告 / 板端网页还没接（现在只有上位机 EF 窗口和 `simpson_ef` 结果里有）。

## 9.65 部分 J 第一步：GDMA 读回微基准（J1）+ FPGA 优势证据基线 J3（b）（c）（2026-10-16，**只测量，板上什么都没部署**）

> 板子状态不变：固件 r2 `81d0f462…`、服务端 v5.16 `e11199f3…`。每次测量都“停服务 → 跑测试程序 → 起服务”（结束均 `active`）。
> 板上只多了 `/root/perf_j/`（测试源码 + 可执行文件约 6 MB，可留着在 F / J1 之后复测）。全部脚本、日志、结果：**`E:/PLD/perf_j/`**，结论汇总 `perf_j/j_results_2026-10-16.md`。

| 项 | 结果 |
| --- | --- |
| GDMA 单笔代价（`gdma_bench sweep`） | **13.6 µs 固定 + 0.765 ns/B（1 307 MB/s）**；整网实际 853 笔：16.4 µs + 1 313 MB/s → 固定开销合计只有 **~14 ms**，带宽部分 65 ms |
| PS↔BRAM 端口上限 | HPM1 128 位 @ 101.376 MHz = 1 622 MB/s；CPU 写（`put`）**1 616 MB/s = 99.6%**，GDMA 读 81% → **搬运受 PL 时钟限制**（F 若把 HPM1 / `ai_data_ic` / `bram_ctrl` 一起提频，`put` / `get` 也会跟着缩短） |
| 计划里的三种改法 | 都不成立：单缓冲下跨条带无法链式（输出区每条带复用；`plbdma.ko` 一次 ioctl 一个描述符，无链式接口）；没有 <1 KB 的条带（CPU 读交叉点 ~1 KB，最小条带 16 KB / 4 KB）；节点输出 0.5–4 MB 放不进 256 KB BRAM |
| **新改法：读回 DMA ∥ 下一条带输入写入** | IP 空闲时读、写两个 AXI 通道并行（不是已否决的“PS 与 IP 并发”）。微基准平均条带 185 → 143 µs，1 200 次逐字节核对 0 错。整网原型 `perf_j/unet_ai_j1.c`（`UNET_J1OVL=2` 条件变量工作线程）：**512 整网 471.7 → 416.9 ms（−54.8 ms，−11.6%）**、128 档 31.9 → 30.4 ms；空转版 412.0 ms。4 个网络 × 多轮 `STRICT_NET_PASS` |
| J3（b）延迟分布（自写 `hog` 代替 stress-ng，用户同意） | PL 的 `ap` 在任何负载下都不变（304.5 / 304.5 / 306 ms）。**推理进程 nice −20**：4 核整数满载 512 整网 P50 469.5 / P99 471.9 ms（= 空载 471.1 / 472.2），同样负载下 CPU 4 线程 1 171 ms（×2.07）；内存流满载 PL 534 ms（+13%）vs CPU 1 183 ms。128 档满载 PL 最大 41 ms vs CPU 4 线程 154 ms |
| ⚠ 当前服务是 nice 0 | 满载时 PL 512 被拖到 **899 ms**（`get` 79 → 500 ms：每笔 GDMA ioctl 睡眠等完成，唤醒被负载线程抢）。`chrt -f` 在板上被拒（cgroup `cpu.rt_runtime_us = 0`）。**建议服务单元加 `Nice=-20`，和 J1 一起上、一起验收（要用户点头）** |
| J3（c）计算效率 | 每帧 2.028 GMAC = 4.06 GOP（512）。卷积引擎峰值 128 MAC/周期 × 101.376 MHz = **25.95 GOPS**（185 DSP）。实际：卷积期间 **14.83 GOPS（57%）**、PL 计算期间 13.32（51%）、整网 8.59（33%；J1 原型 9.81）。整网分解 `ap` 64.5%（卷积 273.4 + 逐元素 32.3 ms）/ `put` 16.2% / `get` 16.8% / 调度 2.4%。逐层：3×3 卷积 60–76%，**1×1 卷积 7–45%**（固定开销摊不开），首层 3 通道补 8、末层 1 通道补 8 |

**下一步（待用户点头）**：服务端 v5.17 = 把 `unet_ai_j1.c` 的条件变量版并进 `src/unet_ai.c`（**不改内核模块、不换比特流**）+ 服务单元 `Nice=-20`；
验收：17 个程序逐位一致、`get` / 整网前后对照、1000 帧稳定性，再用 `perf_j/j3b_*.sh` 复测满载延迟。可选再给 `plbdma.ko` 加异步“提交 / 等待” ioctl（约再 −5 ms，要重载模块，排后）。
F 完成后用同一套脚本重测 J3（b）（c）的最终数字。

## 9.66 F 提频：150 MHz 固件 `ai_sys_f150`（PL 内 MMCM，不改 boot.bin）——构建、协同仿真、**已上板验收、已部署**（2026-10-16 ~ 10-17）

> **部署（10-17）**：板上 `/lib/firmware/ai_sys.bin` = f150 **`30f06745619038236765e237bedec98229f93df7c3824ef48eb48b98807c5685`**，服务端仍是 v5.17 `51780377…`（J1，§9.67）。
> **回滚目标**：r2 `81d0f462…`，板上备份 `/lib/firmware/ai_sys.bin.before_f`（停服务 → `cp -a /lib/firmware/ai_sys.bin.before_f /lib/firmware/ai_sys.bin && sync && reboot`；第 1 轮验收时自动回滚实际走过一次，通过）。
> 新哈希已追加进 `/lib/firmware/ai_sys_pp.sha256` 和 `ai_sys_vis.sha256`。125 MHz 退路 `bitstream/ai_sys_f125.bin` `ecd48843…`（WNS +0.887，LUT 84.63%）已构建、没上板。候选固件 `bitstream/ai_sys_f150.bin` **`30f06745619038236765e237bedec98229f93df7c3824ef48eb48b98807c5685`**（`.bit` `df57a08b…`，旁边有 `.bin.sha256`）。
> 上板验收：`E:/PLD/board_acceptance_f/`（`acceptance.py baseline` → `deploy_tests --variant f150`，`state_f150.json` phase = complete，**`F_ACCEPTANCE_PASS f150`**）。

**上板验收结果（10-17，用户点头后跑；服务端前后都是 v5.17）**

| 项 | 结果 |
| --- | --- |
| 基线（r2 + v5.17） | BoardCheck 31/31（0 WARN）、严格比对、pp 182、vis 592、TCP 全过；工具和 BoardCheck_r1 按 v5.17 源码编（J1 的读回线程要 `-lpthread`，已加），本地 `src/` 与板上 v5.17 release 源码 sha256 逐个相同 |
| 换固件重启 | `operating`、预处理 VERSION 0x494D5004、`pp_layer ready`、`vis_layer ready`、服务 active |
| 逐位门槛 | BoardCheck 31/31（**0 WARN**）；严格比对 4 组 0 差异（512：0/262144）；pp_layer 182 向量；vis_layer 592 向量；TCP 逐位 |
| 回归 `regression.py`（20 组） | **20/20 通过**（`REGRESSION_F_PASS`）：v5.16 的 18 组 + `verify_board_v516`（PL 配准 / 叠加 / 三维渲染）+ `cardiac --board`；**5000 帧混合稳定性 561 s 通过**（RSS 恒定 112 MB、服务 0 次重启、journal / dmesg 错误增量 0、PL 温度 38.9 → 40.1 °C） |
| 第 1 轮 | 除两项外全过，按规则自动回滚到 r2（回滚后核验通过）。两项都是**检查本身的版本不符**：`verify_board_v516` 的 caps 写死 `"v5.16"`（板上是 v5.17）；`compare.py` 已被部分 A 会话加了 RWT / LVMI 等 6 个字段，而 v5.17 还不算这些。对照：在 r2 上跑同一 `compare.py --board` 得到同样的"FAIL"，且 **f150 与 r2 的 100 个请求板端输出逐字节相同**（`control_r2/`）。于是收窄两项检查（caps 接受 ≥ v5.16 + vis/render3d/overlay，其余子项必须全过；cardiac 要求程序 logit 0 差异、每行只允许部分 A 字段不同——注入真实差异时两项都判失败），证据存 `run1_check_mismatch/`，第 2 轮原样重跑全过 |

| 耗时（严格工具 / TCP，各取均值或中位数） | r2 @100 MHz | **f150 @150 MHz** |
| --- | ---: | ---: |
| 512 `ap`（PL 计算） | 304.5 ms | **203.4 ms**（×0.668，= 时钟比 × 周期数 +0.2%） |
| 512 `put` / `get` | 76.6 / 84.3 ms | **57.9 / 63.7 ms**（HPM1 和互联也跑 150 MHz） |
| 512 整网（板上） | 417.5 ms | **292.3 ms（−30%）** |
| PC 往返 512（TCP） | 488.6 ms | **358.5 ms** |
| 128 档整网 | 30.4 ms | **22.2 ms** |
| 心超实时（紧凑帧，depth 2） | 31.9 帧/秒 | **43.1 帧/秒** |
| 眼底 512 紧凑帧 | 2.29 帧/秒 | **3.20 帧/秒** |
| vis_layer 592 向量 PL 合计 | 1 468 ms | **978 ms** |

> **更正（10-17，J3 会话发现）**：板上 `pl0_ref` 实际是 **101.376 MHz**（FSBL 设定，见本文第 100 行；BD 里的 99.999 MHz 只用于约束），所以 MMCM 输出实际是
> **152.15 MHz**（f125 实际 126.7 MHz）。按实际输入时钟复核布线后网表（`vivado/f_clock/retime_f_real.tcl` → `retime_f{150,125}_101376_*.rpt`）：
> **f150 @ 152.15 MHz WNS +0.470 / WHS +0.013 ns**，f125 @ 126.7 MHz WNS +0.779 / WHS +0.011 ns——仍然收敛。下文"150 MHz"按名义值写；`ap` 比值 0.668 = 101.376 / 152.15 × 周期数 +0.2%。
> 以后的 BD 若要精确约束，可把 MMCM 输入按 101.376 MHz 写（或沿用现在的做法，构建后再按 101.376 MHz 复核一次）。

**做法**：`pl_clk0` 保持不变（BD 里是 99.999001 MHz，板上实际 101.376 MHz），BD 里加 `clk_wiz_0`（MMCM）倍频，**全部 PL 逻辑 + HPM0 / HPM1 两个主口时钟**都接 `clk_out1`，
`rst_pl/dcm_locked` 接 MMCM 的 `locked`。PS 配置、boot.bin、设备树都不变，回滚 = 换回 r2 比特流。服务端源码里没有任何按周期换算时间的地方（全是 `clock_gettime`），不用改。

| 步骤 | 文件 | 结果 |
| --- | --- | --- |
| 瓶颈地图 | `vivado/f_clock/retime_r2.tcl` → `retime_r2_{125,148}.txt` | 打开 r2 布线后网表、把时钟改成 125 / 148 MHz 重做静态时序（按 100 MHz 布的线，偏悲观）：125 MHz WNS −0.845（pp_layer 193 个端点、conv 36 个）；148 MHz WNS −2.088（pp −2.09 / conv −1.68 / eltwise −0.65 / vis −0.09）。**互联、张量 BRAM、预处理 RTL、PS 口在 148 MHz 下都已满足**——只有四个 HLS 核要重综合 |
| HLS 重综合 | `ai/f_clock/run_hls_f.tcl` / `.bat`（`<核> <周期> synth|cosim|export`），工程 `ai/build/f_{conv,elt,pp,vis}_p6757` | 源码、编译选项、接口配置与 r2 的 IP 完全相同，只把 `create_clock` 改成 6.757 ns。`cmp_csynth.py p6757` 对比：**所有循环 II 不变**，只是流水深度 +1–4 拍；总周期数 conv +0.01%、eltwise +0.15%、pp +0.16%、vis +0.01%；LUT 合计 +~400、FF +~5k |
| 协同仿真（进比特流前的硬门槛） | `ai/f_clock/_f_*_6.757_cosim.log` | **四个核全部 PASS**：conv 20 例（测试台用 `opt_hls_conv_r1/tb`，被测仍是 V12 源码）、eltwise 76 项（与 E2 门槛相同：40 向量 + 10 强制 + 26 非法参数）、pp 3 例、vis 10 例 |
| BD | `ai/bd/build_ai_sys_f_clk.tcl` / `run_build_f_clk.bat <MHz> <tag> <orig\|p6757> [--preflight]` | r2 脚本 + MMCM；`--ips orig` 用 r2 的 IP、`--ips p6757` 用重综合的 IP。150 MHz：实际 **150.079 MHz**（M 116.5 / D 9 / O 8.625，抖动 179 ps）。D = 1 的低抖动方案要请求 149.9985 MHz（M 12 / O 8），只差约 0.03 ns，这次没用 |
| 实现（150 MHz） | `vivado/ai_sys_f150/`，报告 `vivado/ai_sys_f150_{util,timing,util_hier}.rpt`，日志 `ai_sys_f150_build.log` | **WNS +0.558 ns、WHS +0.013 ns**，0 个严重警告。LUT **59 801（84.75%，比 r2 还少 925）**、FF 55 911（+2.9k）、BRAM 172、DSP 306、MMCM 1/3。最差路径是 conv 参数口 FIFO（BRAM）→ 寄存器，0 级逻辑、走线占 84%——还有余量 |
| 退路（125 MHz） | `vivado/ai_sys_f125/` | 第一次与 150 同时启动，Vivado 加载 tclapp 时撞车（`Could not open 'C' for writing`，工具问题不是设计问题）；10-16 晚单独重跑中 |

**预期收益（估计，以上板实测为准）**：`ap` 304.5 ms × 100 / 150.08 × 1.001 ≈ **203 ms（约 −101 ms）**；HPM1 和 PL 侧互联也变快，`put` / `get` 可能还会小幅下降。512 整网约 471 → 约 365 ms 或更低。

**上板验收做法**（`E:/PLD/board_acceptance_f/`，照 r2 派生）：
- `acceptance.py baseline`（r2 上：备份 `ai_sys.bin.before_f`、编工具、严格比对 + 逐节点计时、BoardCheck 基线、pp / vis 向量、TCP）；
- `acceptance.py deploy_tests --variant f150`：新哈希先追加进 `ai_sys_pp.sha256` / `ai_sys_vis.sha256` → 重启 → BoardCheck 31 项、严格比对、pp 182 向量、vis 592 向量、TCP、
  `regression.py`（v5.16 的 18 组 + `verify_board_v516` + `cardiac compare --board` + **5000 帧**稳定性），任何一步失败自动回滚到 r2；
- 板上根分区验收前剩 459 MB，`/root/f_acceptance` 约 150 MB（旧的 `*_acceptance` 共约 420 MB 还在，删不删由用户定，见部分 B）。

**坑**：
- conv 的协同仿真必须用 `opt_hls_conv_r1/tb`（缓冲按 pragma 的 depth 开，否则包装层越界 SIGSEGV）；`open_project` 不带 `-reset` 会保留旧文件列表，换测试台后要从 `hls.app` 删掉旧的（同名文件后者生效）。
- `clk_wiz` 的输入频率必须与 `pl_clk0` 的 `FREQ_HZ`（99 999 001）逐 Hz 相同，否则 BD 校验报 41-238；脚本里已自动读取。
- 两个整板构建同时跑会撞车：这次两个相隔 60 秒启动，但各自派生的 OOC 子任务还是同时初始化 tclapp，其中一个失败。**整板构建一次只跑一个**。

## 9.67 服务端 v5.17：J1 读回 ∥ 写入 + 服务单元 `Nice=-20`（2026-10-16 晚 ~ 10-17 凌晨，**已部署、已验收**；固件 r2 不变）

> **当前**：`/root/tcp_image_rx/tcp_image_server` = v5.17 **`51780377b10a77a2d19cbf4d8fd757b77006ab227b032b556beed0ba7db38ef4`**
> （release `/root/tcp_image_rx/releases/v5_20261017T050952Z`，`deploy_board_v5.py prepare / activate`，板上原生编译 0 警告），
> 外加单元 drop-in `/etc/systemd/system/tcp-image-server.service.d/10-nice.conf`（`Nice=-20`，仓库里是 `boot/tcp-image-server.service.d/10-nice.conf`）。
> 服务主线程和 J1 工作线程都是 NI −20（`ps -L`）。
> **回滚目标**：v5.16 **`e11199f3…`**（`deploy_board_v5.py rollback`，同时删掉 drop-in——v5.16 时没有）。**不能退到 ≤ v5.14**。
> **v5.17 只含 J1**（用户定）：`src/job_simpson.c` 用的是 v5.16 那份（`74afe8e9…`，`prepare --pin=src/job_simpson.c=…`，副本 `E:/PLD/board_acceptance_v517/pinned/`）。
> 部分 A（§9.64）工作区里的 `job_simpson.c` **没上板**，下一版服务端 v5.18 再上（不带 `--pin`，回滚目标 v5.17），记得把 `src/job.c` 的 caps 改成 `"v5.18"`。

**改了什么**

| 文件 | 改动 |
| --- | --- |
| `src/unet_ai.c`（`56d6c64d…`） | 把原型 `perf_j/unet_ai_j1.c` 的条件变量版（`UNET_J1OVL=2`）并进来，**默认开**：`j1_init()`（`unet_init` 里 `gdma_open` 之后建一个常驻工作线程，睡在条件变量上；没有 GDMA 或设了 **`UNET_NOJ1=1`** 就关）；两个条带循环里“收上一条带”的 `bram_get_defer` 换成 `j1_get()`（交给工作线程，主线程同时写下一条带的输入 / 参数）；`j1_wait()` 在 `ovl_arm` 之后、写寄存器起 IP 之前。条带循环与原型逐行相同（只差注释）；`UNET_GTRACE`、空转版去掉了。`UNET_PROFILE` 多一行 `[prof] J1 on: posts / main_wait`。`get_us` 照旧记 DMA 时间，但它现在与 `put` 重叠，所以 `put + get + ap` 会大于整网（profile 的 `other` 为负） |
| `src/job.c` | caps 报 `"server":"v5.17"` |
| `makefile` | `unet_selftest` 加 `-lpthread`（服务端本来就链） |
| `boot/deploy_board_v5.py` | drop-in：`prepare` 记下板上原来有没有（`previous/dropin.state`，清单 `"dropin"`），`activate` 装上 + `daemon-reload` + 核对 `Nice=-20`，`rollback` 恢复原状（老清单没有 `"dropin"` 时不动它）；新参数 `prepare --pin=src/x.c=本地路径`（用另一份源码编这一个文件，清单 `"pinned"`） |

**验收**（全部原始数据：`E:/PLD/board_acceptance_v517/`、`E:/PLD/perf_j/results/`）

| 项 | 结果 |
| --- | --- |
| 部署前 A/B（`perf_j/j1_v517_ab.sh` → `results/j1_v517_ab.json`） | `strict_net_test` 链移植后的 `unet_ai.c`，默认 vs `UNET_NOJ1=1`，两轮交替：**12/12 `STRICT_NET_PASS`**；512 整网中位数 **471.5 → 419.1 ms**，皮肤 / 心超 128 31.9 → 30.4 ms（原型是 416.9 / 30.4） |
| 回归 `board_acceptance_v517/regression.py`（v5.16 那 18 组照搬） | **18/18 `REGRESSION_V517_PASS`**；1000 帧混合稳定性 0 差异、0 重连、服务端 0 次重启；F4 Simpson 450 例专家掩膜 + 界面路径逐位照旧 |
| 服务端前后对照（同一脚本 `verify_board_v4.py`，`fundus_regression.json` v5.16 vs v5.17） | 512 `ai_us` 中位数 **470.7 → 416.2 ms**（59 帧），PC 往返 721.5 → 666.8 ms；256 117.5 → 105.6；128 31.8 → 30.3；ROI 44.7 → 41.8 ms；心超紧凑帧实时（`realtime.json`，深度 2）30.4 → **31.9 fps** |

**满载延迟**（自写 `hog`，同 §9.65；P50 / P99 / 最大，ms，去掉每组第一帧）

| 512 档 | 空载 | cpu4 满载 | mem4 满载 |
| --- | --- | --- | --- |
| `strict_net_test_v517`（= 部署的 `unet_ai.c`），`nice -n -20`，与 `hog` 同一 ssh 会话 | 417.7 / 418.4 / 418.4 | **423.5 / 442.2 / 442.2** | **463.1 / 497.4 / 497.4** |
| 同上，nice 0（对照，= 老单元） | 416.9 / 417.3 / 417.3 | 847.0 / 907.2 / 907.2 | 935.7 / 1 006.4 / 1 006.4 |
| **部署的服务**（systemd，`Nice=-20`；PC 发帧，取统计块 `ai_us`，`perf_j/j3s_server.py`） | 416.5 / 422.6 / 422.6 | ⚠ 604.1 / 1 105.0 / 1 105.0 | ⚠ 779.7 / 1 346.2 / 1 346.2 |

| 128 档 | 空载 | cpu4 满载 | mem4 满载 |
| --- | --- | --- | --- |
| `strict_net_test_v517`，nice −20 | 30.36 / 37.56 / 43.85 | 30.26 / 64.22 / 69.77 | 36.51 / 66.85 / 72.81 |
| 部署的服务 | 30.27 / 30.37 / 30.39 | ⚠ 48.3 / 185.1 / 275.8 | ⚠ 65.9 / 206.7 / 213.9 |

`j3b_v517.sh` 9/9 `STRICT_NET_PASS`（`results/j3b_v517.log/json`）；服务那组 `results/j3s_server.json`。

**⚠ 发现：`Nice=-20` 挡不住 ssh 会话里的负载**（`results/j3s_cgroup.txt`）。板上是 cgroup v1 的 `cpu,cpuacct`：服务在 `/system.slice`（`cpu.shares` 1024），
ssh 登录起的进程（包括 `hog`）在 `/user.slice/…`（也是 1024）。CFS 先在两个 slice 之间按份额平分，nice 只在 slice 里面排先后——所以服务满载时只拿到一半左右。
测试程序那组是和 `hog` 在同一个会话里跑的，nice 就生效（423 ms）。别的 systemd 服务带来的负载（同在 `system.slice`）nice 是管用的。
**修法候选（要用户点头、再部署一次）**：给服务一个自己的 slice 并调高份额（如 drop-in 加 `Slice=pl.slice` + `pl.slice` 的 `CPUShares=8192`），
或 `systemctl set-property system.slice CPUShares=8192`；改完用 `perf_j/j3s_server.py` 复测（目标：cpu4 下接近 423 ms）。
J3（b）证据现在能写的口径：“推理进程优先级高时（同一调度组内）PL 延迟不受满载影响”= 测试程序那组；部署的服务那组要照实写，或修好后重测。
另：128 档 nice −20 满载时 P99 64 ms，比 §9.65 不带 J1 的 33 ms 高（每条带要唤醒一次工作线程）；P50 不变。

**坑**
- `Fz3b_BoardCheck` 从 `/root/tcp_image_rx/src` 编 `unet_ai.c`（部署脚本不碰那里）。要是有人把新源码同步过去，它的 makefile 得加 `-lpthread`（板上 glibc 2.27，pthread 不在 libc 里）。
- `tcp_image_ui/verify_board_v516.py` 的 caps 检查写死 `"v5.16"`，对 v5.17 会报失败（它不在回归 18 组里）。
- Git-Bash 里 `python - <<'EOF'` 的 heredoc 会把 `\\` 吃成 `\`（Python 源里的 `"\\n"` 变成真换行）——改 C / Python 源码的补丁脚本先写成文件再跑。

## 9.68 J3 最终数字：f150 + v5.17 上重测（b）（c）+ 单核口径（2026-10-17，**只测量，板上什么都没部署**）

> 板子状态不变：固件 f150 `30f06745…`、服务端 v5.17 `51780377…`。每次测量“停服务 → 跑测试程序 → 起服务”（结束均 `active`、BoardCheck 界面已恢复）；服务那组是 PC 发帧给运行中的服务。
> 板上 `/root/perf_j/` 只多了 3 个小脚本。全部脚本、日志、结果：**`E:/PLD/perf_j/`**，结论汇总 **`perf_j/j3_results_f150_2026-10-17.md`**（前 = r2 + v5.17，后 = f150 + v5.17）。

**⚠ 更正：f150 在板上实际跑 ≈152.15 MHz，不是 150.08 MHz。** 只读 `/dev/mem`：`PL0_REF_CTRL 0x01010802`（RPLL / 8）、`RPLL_CTRL 0x00013000`、`RPLL_FRAC 0x8000a915`（小数分频开）
→ RPLL 811.008 MHz → `pl0_ref` = **101.376 MHz**（FSBL 设的，§3 老结论）。BD 里 `clk_wiz` 按 99.999001 MHz 输入算出 150.079，但 MMCM 乘的是真实输入：101.376 × 116.5 / 9 / 8.625 = **152.146 MHz**（VCO 1 312 MHz）。
旁证：`ap` 比值 0.6677 = 101.376 / 152.146 × 1.002；按 152.146 算卷积引擎每周期利用率 57.2% → 57.3% 不变。**时序是按 6.663 ns 签的（WNS +0.558）**，152.146 MHz 周期 6.573 ns，
粗估余量剩约 +0.47 ns（**没重跑时序**）；功能验收本来就在真实时钟下跑，不受影响。建议：PC 上对 `vivado/ai_sys_f150` 布线后网表按 152.146 MHz 重做 `report_timing_summary`；对外写“约 152 MHz（标称 150）”。

| 项 | r2 @101.376 MHz | **f150 @152.146 MHz** |
| --- | --- | --- |
| 512 整网（测试程序，J1，空载中位数） | 419.1 ms | **292.8 ms（−30.1%）**；不带 J1 471.5 → 335.0 |
| 512 `ap` / `put` / `get`（不带 J1） | 304.5 / 76.1 / 79.5 | **203.3 / 58.4 / 61.9**（×0.668 / ×0.768 / ×0.779：搬运没完全跟上时钟，`put` 2 016 MB/s = 新端口上限 2 434 MB/s 的 83%，r2 时 95%） |
| J3（b）512 PL nice −20，P50 / P99：空载 / cpu4 / mem4 | 417.7 / 418.4；423.5 / 442.2；463.1 / 497.4 | **293.7 / 299.6；300.1 / 322.4；359.6 / 400.4**（cpu4 下 `ap` 203.3 = 空载；mem4 `put` 60.6 → 112.9） |
| 同负载 CPU 4 线程 NEON 分块（nice 0 / **nice −20 新增**） | cpu4 1 171.3 | cpu4 **1 165.7 / 573.5**、mem4 1 176.0 / 573.9 → PL 快 3.89 / **1.91 倍**（同优先级时 CPU 中位数也基本不变，“确定性”要照实写） |
| 128 档 PL nice −20 P50 / P99 / 最大（cpu4） | 30.3 / 64.2 / 69.8 | **22.0 / 55.4 / 60.6**；CPU 4 线程 nice −20 34.8 / 40.7 / 162.3 |
| ⚠ 部署的服务（`Nice=-20`，cgroup 问题未修）512 P50 / P99 | 空载 416.5 / 422.6；cpu4 604.1 / 1 105.0；mem4 779.7 / 1 346.2 | **292.0 / 305.2；427.7 / 962.2；537.1 / 1 328.5**（满载 P50 比测试程序慢 43%）；PC 往返 471.0 → 340.8 |
| J3（c）卷积引擎峰值（128 MAC/周期，185 DSP） | 25.95 GOPS | **38.95 GOPS** |
| 卷积期间 / PL 计算期间 / 整网（J1） | 14.83（57.2%）/ 13.32（51.3%）/ 9.68 GOPS（37.3%） | **22.32（57.3%）/ 19.95（51.2%）/ 13.85 GOPS（35.6%）** |
| 整网分解（J1，部署口径） | `ap` 72.7% + 暴露搬运 / 调度 27.3% | **`ap` 69.4% + 暴露搬运 / 调度 30.6%**（J1 藏掉 42.2 ms） |
| 单核口径：PL 512 对 1 核 NEON 分块 / NEON / 普通 C（2 220.8 / 3 142.0 / 9 460.6 ms，逐位） | 5.3 / 7.5 / 22.6 倍 | **7.6 / 10.7 / 32.3 倍**；对 4 核 NEON 分块 565.1 ms：1.35 → **1.93 倍**；128 档 6.2 / 8.7 / 26.3 倍、4 核 1.58 倍 |
| J3（a）PL 各核（F 验收向量计时） | 配准三步 128 1 055 µs、叠加条带 1 396 µs、后处理眼底 4.30 ms | **703 µs、931 µs、2.92 ms**；后处理对同板 PS `ai_view_count`（4.27 ms）由打平变 **1.46 倍** |

测量：`j3b_f150.sh`（27 组、每次逐位，全部 `STRICT_NET_PASS` / `BIT_EXACT`）、`j1_f150_ab.sh`（12/12 `STRICT_NET_PASS`）、`j3a_single.sh`（6 组 `BIT_EXACT`）、`j3s_server.py j3s_server_f150.json`、
`j3c.py f150`（新增变体参数，默认 `r2` 输出与 10-16 逐字节相同）、`j3_compare.py`（前 / 后对照）。
**没做**：（a）逐个核的 NEON C 版（配准 / 叠加 / 预处理 / 后处理都还没有 NEON 版，要写新代码，由用户定）；（d）（e）不在本步范围（e 等功率计）。

**坑**：板子 RTC 每次开机回到 2021-08-31，单元文件的修改时间（上次开机写的）比这次开机 systemd 加载时间“晚”，`systemctl` 会提示 “changed on disk, run daemon-reload”——内容核对过没变，不用管。

## 9.69 部分 E 第一步：现有 vis_layer 能否"每块调用一次"做 SAD 块匹配（2026-10-17，**只做 C 仿真，板上什么都没动、没构建比特流**）

> 板子状态不变：固件 f150 `30f06745…`、服务端 v5.17 `51780377…`。完整说明 `E:/PLD/strain/e_step1_vis_layer_block_matching_feasibility_2026-10-17.md`，脚本 / 测试台 / 输出在 `E:/PLD/strain/step1/`。

**结论：能，但有限制；E 不需要 r3**（F 已单独部署，"E 与提频合并构建"作废）。

- **做法（不改 PL、不改驱动）**："掩膜图块"——每块一次 REG（n = 128）：A = 255 背景 + 中心 16×16 模板（像素 >> 1），B = 0 背景 + 外扩 8 px 的窗口（像素 >> 1），
  代价表 `tab[d] = d (d < 128)，0 (d ≥ 128)` → A 的背景对 B 里任何值（含双线性混合）都是 d ≥ 128、代价 0，每条候选记录的 SAD = 模板上的 SAD。用现有 `vis_reg()` 加这张表即可。
- **C 仿真**（`tb_bm.cpp` 链原封不动的 `ai/vis_hls/src/vis_layer.cpp`，WSL `sh run_tb_bm.sh`；数据 `dump.py` 取 CAMUS **VAL**）：308 块 × 3 种搜索 = 924 次调用、121 660 个候选，
  SAD 与独立参考（模板 vs 窗口、同一整数双线性）**0 处不同**（`BM_ONE_CALL_PER_BLOCK_EXACT`）。已知平移 (−3, +2) px：全搜索 ±6 **120/120**，三步 4-2-1 只 94/120（斑点图局部极小）。
  PL 自带的"最优"按 SAD / 整图块重叠面积比较，5–6/308 块与原始 SAD 最小不同 → 最优由 PS 从记录里取。
- **耗时**（按 f150 板上实测拟合：`board_acceptance_f/after_vis_timing.txt`，±7 全搜索 6 125 µs / 三步 703 µs）：每候选 **4 125 拍 = 27.1 µs**（整张 128² 图块，有用的只占 1.6%），每次调用固定约 25 µs；
  每块 ±4 ≈ **2.3 ms**、±6 ≈ 4.7 ms（含 PS 估计 ~0.05 ms）。
- **块数**（`blocks.py`，VAL 200 帧专家掩膜，心肌 = epi − lv）：中层线长 P50 221 mm、壁厚 11.9 mm；一条线 3 mm 间距 **P50 74 块**（P95 90），三条线 222 块；
  帧间运动 128 档 ≤ 1–2 px/帧 → 256 档 ±4、512 档 ±6–8；一例（A4C + A2C）约 34 对帧。
  → 现有 PL：每对帧 74 块 ≈ **0.17 s**（±4）/ 0.35 s（±6），每例约 5.7 s；PS 单核 NEON **估计** 0.3–1 ms / 对帧（没上板测）。
- **r3 选项（不建议，清单见说明 §5）**：新 op 3 `BM`（多块 + 窗口，A / B 装一次）4 路 +0.6–0.9k LUT（≈ 86%）→ 每对帧 3.8 ms（≈ 纯 C）；再加 16 路 + 候选间无气泡 +3.5–4.5k（≈ 90–91%，152 MHz 下有风险、可能要退 V9b）→ 1–1.8 ms（≈ 1 个 NEON 核）。
  若做：从 `build_ai_sys_f_clk.tcl` 派生，HLS 按实际 152.15 MHz（`pl0_ref` 101.376）约束，协同仿真是硬门槛，现有 592 向量 + 新 BM 向量，按 `board_acceptance_f` 全套验收。
- **下一步（E 第二步）**：追踪放 PS（NEON / C；先在板上跑 10 分钟 SAD 微基准核实估计），vis_layer 作可选的"PL 复核 / 演示"（6–12 个节段块每帧 15–30 ms，可与 PS 的 SAD 表逐位对照）；之后 STRAUS / Evain 定量验证、CAMUS ED / ES 心内膜长度对照。

## 9.70 部分 E 第二步：PS 端 SAD 块匹配（C + NEON）+ 板上微基准 + 主机 GLS 原型（2026-10-17，**只测量，板上什么都没部署、没构建比特流**）

> 板子状态不变：固件 f150 `30f06745…`、服务端 v5.17 `51780377…`。板上两次微基准（用户逐次确认；停服务 → 编译 / 测试 / 计时 → 起服务，两次都 `active`）；板上只多了 `/root/strain_e2/`（源码 + step-1 VAL 数据约 1.7 MB + 测试程序）。
> 完整说明 **`E:/PLD/strain/e_step2_ps_block_matching_gls_prototype_2026-10-17.md`**，代码 / 脚本 / 日志 **`E:/PLD/strain/step2/`**。测试集没碰（VAL 调参，偏移校准只用 TRAIN）。

- **核函数** `bm_sad.c/h`：16×16 模板、全搜索 ±R、原始 SAD 最小（并列规则 = `tb_bm.cpp`）、抛物线亚像素（可选 V 拟合 `BM_OPT_VFIT`，整数 1/256 px）；纯 C + 两种 NEON（`neon` 直接写法、**`neon2` 4 个 dy 共用行读取，追踪用它**）；
  追踪 `bm_track_points` / `bm_track_line`（线上 k 个邻块 SAD 表相加；k = 1 与前者逐位相同）。
  **逐位**（`test_bm.c`）：470 624 个 VAL 候选 + 135 584 个随机候选对 step-1 `ref_sad` 0 处不同，2 864 个 argmin 与 `tb_bm.cpp` 相同，已知平移 120/120，40 万次拟合对双精度公式 0 处不同；**主机和板上（gcc 7.5，含两种 NEON、C 对 NEON 追踪）都 `BM_PS_BIT_EXACT`**。
- **板上微基准**（单核，**A53 实际 1.0667 GHz**；第 1 次日志里加法链给的 1 197.7 MHz 是循环开销算错，第 2 次已改）：每候选 **neon2 91.5 ns ≈ 97 拍**（±4 / ±8：102 / 96 拍），neon 183 拍（瓶颈是每候选 16 次非对齐读），纯 C 不向量化 727 拍（gcc `-O3` 自动向量化反而 1 617 拍）。
  每块 ±4：neon2 7.8 µs + argmin 1.0 µs（PL 掩膜图块 2.3 ms 的约 1/260）。每对帧 74 块：`bm_track_points` ±4 1.12 ms（旧核实测；neon2 推算 0.65）；
  **最终方案 `bm_track_line` 512 档 ±6、k = 9：2.52 ms（旧核实测）/ ≈ 1.5 ms（neon2 推算）→ 一例 34 对帧 ≈ 50–86 ms**，纯 C 13.3 ms / 对帧。第一步的估计（60–100 拍）只有 neon2 达到；"纯 C ≈ NEON 1/4" 实为约 1/7.5。
  日志 `strain/step2/results/board_e2.log`、`board_e2b.log`（`run_e2.py [标签]`，Python 用 `anaconda3/python.exe`，msys / Python313 的 paramiko 加载不了）。
- **GLS 原型**（`track_val.py`，srtp-vision 环境；C 核经 `bm_sad.dll`，`build_dll.sh`）：VAL 100 段半周期，ED 心肌 = 程序 18 − F1 的板端等价掩膜，中层线每 3 mm 一块（P50 74 块），逐帧追踪、5 点平滑后算线长 → GLS-like 曲线；
  对专家 ED / ES 心肌中线长度变化（均值 −14.5%）：**原始偏差 +3.7 个百分点、MAE 4.2、r 0.66**（按例两切面平均 MAE 3.8、r 0.83；Poor 质量 11 段 r 0.17 基本失败）；
  **TRAIN 偏移 −3.81（`calib_train.py`，800 段）后 MAE 3.1、按例 2.3**；只用 ED / ES 分割不追踪 MAE 7.7、r 0.38；合成仿射压缩 15%：偏差 +0.23、MAE 0.56（无去相关，偏乐观）。
  调参记录：不平滑时点抖动把线拉长（偏 +24 / +76）；抛物线亚像素偏向整数最多 0.13 px（V 0.03–0.05），但正则后真实序列两者相当；SAD 表沿线聚合优于位移中值；模板锚定 ED / 每 4 帧更差（去相关）。
  **精度不够用于"相对 GLS 下降 >15%"判读 → 作为探索性成果如实写误差**（计划兜底条款），措辞只做辅助测量。
- **没做**：STRAUS / Evain 定量验证（要下载，先问用户）、板上集成（病例任务 + 服务端新版本 + 上位机曲线 / 追踪点）、PL 复核演示模式、neon2 版追踪整体耗时的板上补测。

## 9.71 部分 D：眼底视盘 / 视杯 + 垂直杯盘比——两个程序包 + PS 端 C + 主机逐位一致 + VAL + 测试集一次（2026-10-17；**10-18 程序包 19 / 20 已装上板、板上核对通过**，见文末）

详见 `E:/PLD/fundus_cdr/README.md`。仅作辅助测量与复核，不作青光眼诊断。
- **数据**（许可在原始出处核对，用户选）：Chákṣu IMAGE（CC BY 4.0，训练 807 / VAL 202 / 官方测试 336）+ PAPILA（GPL-3.0+，外部测试 488）。
  RIGA（CC BY-NC）、REFUGE（CC BY-NC-ND）、Drishti-GS（禁止再分发）、ORIGA（无许可）、Zenodo 8009107 合集（许可标注与原始条款不符）都没用。
- **流程**（`fundus_cdr/cdr_ref.py` == `src/fundus_cdr.c`）：512 图 → box4 → 程序 19 定位视盘 → 窗口 128k（≥ 2 倍视盘）→ box_k →
  程序 19（视盘）+ 20（视杯，同一紧凑帧 `extra`）→ 双线性 ×k、最大连通域、填洞、视杯 ∧ 视盘 → vCDR = 行数比。
- **程序包**（`programs_d/`）：19 `fundus_disc.upk` `0393e680…`（整图 + 窗口一个程序两用，INT16 `first_block_5`），
  20 `fundus_cup.upk` `5930b94f…`（INT16 `add_4` + `first_block_2`），各约 108 KB，128 档自检；包内期望值 == lwnet 整数参考。
- **逐位核对**：两个模型 `export_verify` PASS（30 节点、全部 VAL 输入、512 整表 0 差异）；`test/fundus_cdr/compare.py`：
  C == Python（VAL 202 例 + 合成 500 例，含各窗口边长 / 阈值 / 并列连通域 / 空洞 / 贴边）**FUNDUS_CDR_HOST_PASS**（`fundus_cdr_host.json`）。
- **结果（整数 = 板上）**：VAL 视盘 Dice 0.974、视杯 0.908、**vCDR MAE 0.035**（单个专家 vs 多数 0.038–0.059）；
  **Chákṣu 测试（一次）0.972 / 0.898 / MAE 0.038**（专家 0.042–0.050）；**PAPILA 外部（一次）失败**：0.820 / 0.526 / MAE 0.130，10 / 488 没找到视盘
  （30° 视盘居中 Topcon，视盘大、颜色不同）→ 只适用于与 Chákṣu 相近的相机。锁 `fundus_cdr/results/final_test/lock.json`（used）。
- **参考范围**：Foster 2002（ISGEO）只定义"该人群第 97.5 百分位"，无固定数值（表 1 四个人群 0.70–0.71）→ 界面只显示数值。
- **上板（待用户安排）**：v5.17 即可，不换比特流、不重编：`boot/install_program.py install programs_d/fundus_{disc,cup}.upk` →
  `tcp_image_ui/verify_board_fundus_cdr.py --eval-name v1`（写好、没跑）。回滚 = `install_program.py remove`。
- **没做**：病例任务接线（`JOB_FUNDUS_CDR`，排在 A 的 v5.18、E 的 v5.19 之后）、上位机"眼底综合筛查"页、对照模型（只整图 / 只窗口）训练被打断未补。
  `src/fundus_cdr.c` 在 makefile 通配符里会被编进服务端（未被调用，无行为变化）。
- **上板（2026-10-18，服务端 v5.19 `bc386abc…`，固件 f150 不变）**：`boot/install_program.py install programs_d/fundus_disc.upk programs_d/fundus_cup.upk`
  → `INSTALL_PROGRAM_PASS`，板上自检 19 / 20 都是 **0/16384 差异**（各约 23 ms）；`programs/` 现在 17、18、19、20。
  `tcp_image_ui/verify_board_fundus_cdr.py --eval-name v1`：VAL 202 张，板上 q1 / qd / qc logit 与 vCDR 全部 == 主机参考，0 差异 → **FUNDUS_CDR_BOARD_PASS**；
  每例两次往返平均 100 ms（最大 140 ms）。证据 `tcp_image_ui/board_check_v5/fundus_cdr_board.json`。回滚 = `install_program.py remove fundus_disc.upk fundus_cup.upk`。
  v5.18 / v5.19 构建时排除了 `src/fundus_cdr.c/.h`，板上服务端里没有它；板端算杯盘比（`JOB_FUNDUS_CDR`）+ 筛查页 = 可能的 v5.20，用户再定。

## 9.72 服务端 v5.18：部分 A（左室质量指数分类 + RWT 数值）上板（2026-10-18，**已部署、已验收**；固件 f150 不变）

> **当前**：`/root/tcp_image_rx/tcp_image_server` = v5.18 **`cb79204a0b9c7cbccb4e6b12309cc9c7047260de95a8c2fe9d1aa6ddb1acfd45`**
> （release `/root/tcp_image_rx/releases/v5_20261018T032210Z`，`deploy_board_v5.py prepare --exclude=src/fundus_cdr.c --exclude=src/fundus_cdr.h` / `activate`，
> 板上原生编译 0 警告；单元 drop-in `Nice=-20` 照旧，服务 NI −20）。固件 f150 `30f06745…` 不变。
> **回滚目标**：v5.17 **`51780377b10a77a2d19cbf4d8fd757b77006ab227b032b556beed0ba7db38ef4`**（`deploy_board_v5.py rollback`；备份在 release 的 `previous/`）。**不能退到 ≤ v5.14**。
> 用户 10-17 定：部分 A 先单独出 v5.18；strain（部分 E 第三步，`E:/PLD/strain/step3/rx_src`）之后出 v5.19，回滚目标 v5.18。

**v5.18 相对 v5.17（只含部分 A，§9.64）**：部署前用 v5.17 清单（`board_check_v5/deployment_v5_20261017T050952Z.json`）的 `sources` 哈希核对工作区：只有三处不同——
`src/job_simpson.c`（部分 A，`24d2b2c6…`；v5.17 钉的是 v5.16 那份 `74afe8e9…`）和部分 D 还没上板的 `src/fundus_cdr.c/.h`（§9.71）。
makefile 编 `src/*.c` 全部，所以给部署脚本加了 **`prepare --exclude=src/x.c`**（不上传该文件，清单记 `"excluded"`），这次排除了 `fundus_cdr.c/.h`，板上编译列表里确实没有它。
另外 `src/job.c` caps 报 `"server":"v5.18"`（`"cardiac":true` 照旧）。strain 的源码没进 `src/`。

**验收**（`E:/PLD/board_acceptance_v518/`，`regression.py` 从 `board_acceptance_f` 复制，f 那份没改）

| 项 | 结果 |
| --- | --- |
| 回归 21 组（f 的 20 组 + `_verify_cardiac_ui.py`） | **21/21 `REGRESSION_V518_PASS`** |
| `cardiac`（`test/cardiac/compare.py --board`，**严格**：去掉了 f 里容忍部分 A 字段的 `PART_A_KEYS`） | `CARDIAC_BOARD_PASS`：程序 17 / 18 在 VAL 200 帧上 logit 0 差异；100 个 8 掩膜 `simpson_ef` 请求（带合成身高体重 / 性别，含 32 字节旧请求）**全部字段（含 lvidd / rwt / bsa / lvmi / lvmi_class）与 `cardiac_measure.measure` 逐位相同**，0 mismatch；分类 增高 46 / 不增高 34 / 无 20 |
| `cardiac_ui`（`_verify_cardiac_ui.py`，170 cm / 65 kg / 男） | `CARDIAC_UI_PASS`：5 位 VAL 病人上位机整条路逐位相同，`cardiac_text()` 输出见 `cardiac_ui.log` |
| `v516`（caps 改成要求 `"v5.18"`，其余检查照旧） | 通过：caps `server v5.18`，PL 配准引擎 / 叠加 / 三维渲染全过 |
| 5000 帧混合稳定性（`stability_1000` 组，`board_acceptance_v518/stability_1000.json`） | 5000 / 5000 帧 0 差异、0 重连、0 失败；512 档 `ai_ms` 均值 295 ms（f150 + J1） |
| 其余 17 组（心超 / 点击 / 序列 / 配准 / 实时 / 眼底 v4 / 两个界面 / 算子 / CT ROI / EchoNet ×2 / 后处理 / 病例任务 ×3 / F4 Simpson） | 全部通过 |

仓库里各脚本写的验收报告由回归脚本先备份、跑完还原（`repo_reports_backup/`），新报告都在 `board_acceptance_v518/`（新建的文件在 `new_files/`）。

**还没做**：证据报告 / 板端网页还没显示 LVMI 分类和 RWT（上位机 EF 窗口和 `simpson_ef` 结果里有）。

## 9.73 服务端 v5.19：部分 E 第三步——心肌应变病例任务 `strain` 上板（2026-10-18，**已部署、已验收**；固件 f150 不变）

> **当前**：`/root/tcp_image_rx/tcp_image_server` = v5.19 **`bc386abcc0d0b6d889cfc31004f2c42bc80ce1961214dc870f895ba445c19cf6`**
> （release `/root/tcp_image_rx/releases/v5_20261018T051322Z`，`deploy_board_v5.py prepare --exclude=src/fundus_cdr.c --exclude=src/fundus_cdr.h` / `activate`，
> 板上原生编译 0 警告；drop-in `Nice=-20` 照旧，服务 NI −20）。固件 f150 `30f06745…` 不变。**release 二进制与验收过的开发版逐字节相同**（同一哈希）。
> **回滚目标**：v5.18 **`cb79204a0b9c7cbccb4e6b12309cc9c7047260de95a8c2fe9d1aa6ddb1acfd45`**（`deploy_board_v5.py rollback`；备份在 release 的 `previous/`）。**不能退到 ≤ v5.14**。
> 清单副本 `E:/PLD/board_acceptance_v519/deployment_v519_manifest.json`（v5.18 的在同目录 `deployment_v518_manifest.json`）。部分 D 的 `fundus_cdr.c/.h` 仍未上板（与 v5.18 一样排除）。

**v5.19 相对 v5.18（只含部分 E 第三步）**：部署前核对工作区 = v5.18 清单（0 处不同，另有未上板的 `fundus_cdr.c/.h`），再跑 `E:/PLD/strain/step3/rx_src/apply_to_rx.py`：
新文件 `src/strain.c/.h`（`16fe1e4b…` / `0fa79c7f…`）、`src/job_strain.c`（`0c5b10a2…`）、`src/bm_sad.c/.h`（`3b95d5b3…` / `195185d3…`，= `strain/step2` 原样）；
`src/job.h` 加 `JOB_STRAIN = 7`（`f203873d…`）、`src/job.c` 加分发、caps 任务表加 `"strain"`、caps `"server":"v5.19"`（`f117570d…`）；
`makefile` 给 `strain.o / job_strain.o` 加 `-ffp-contract=off`、`bm_sad.o` 加 `-O3`（`ed4fb13a…`）。**以后的服务端版本都要在这上面编**。

**任务 `strain`**（说明全文 `E:/PLD/strain/e_step3_strain_on_board_host_part_2026-10-17.md`，计划 `strain/step3/e_step3_plan_2026-10-17.md`）：
上传一段半周期（T 帧 512×512 u8，第 0 帧 ED、最后一帧 ES）+ sx / sy（512 网格每像素 mm）；板上 ED 帧 `[::4, ::4]` → **PL 跑模型 2（F1）和程序 18（心外膜）→ PL pp_layer 清理**（PS 清理同时跑做交叉核对）
→ PS：心肌 = epi − lv → 骨架最长路径 = 中层线、每 3 mm 一个 16×16 块 → 逐帧 `bm_track_line`（512 档、±6、9 邻块 SAD 聚合、V 拟合，**NEON neon2**）→ 每帧 5 点平滑后线长 → GLS-like(t)；
回 `gls_pct`、`length_mm`、ES 值（原始 / TRAIN 偏移 −3.806 后）、峰值、耗时、`pl_split`，块 `points` / `line` / `myo`（flags bit0 PS 清理、bit1 纯 C 核、bit2 回 logit）。
上位机：序列窗口「心肌应变…」→ `strainwin.py`（追踪点动画 + 曲线 + 数值 + 固定的探索性说明）。**措辞：探索性辅助测量，VAL 上 ES 值 MAE 3.1 个百分点（TRAIN 偏移后），不作诊断、不套相对下降阈值**。

**逐位链**：`strain/step2/track_val.py`（原型）= `strain/step3/strain_port.py`（无库参照，VAL 100/100 每一步相同，`results/compare_port.json`）= `src/strain.c`（主机 `test/strain/compare_host.py` → `STRAIN_HOST_PASS`）= 板上（下表）。

**验收**（全部原始数据 `E:/PLD/board_acceptance_v519/`；开发版用 `devrun.py build / start / stop` 在 `/root/strain_v519dev` 编、临时顶替服务，结束恢复 `active`、哈希不变）

| 项 | 结果 |
| --- | --- |
| 开发版 `verify_board_strain.py`（VAL 100 段，`strain_dev.json`） | **`STRAIN_BOARD_PASS`**：模型 2 / 程序 18 的 logit = `echo/results/quant/*` 整数参考（100/100）；PL 清理 = PS 清理（100/100）；中线、每帧追踪坐标（Q8）、长度、曲线、ES 值**全部逐位**；前 20 段纯 C 核、前 10 段 PS 清理结果相同；出界点 0 |
| 部署版同上（`strain_deployed.json`，C 核 5 段、PS 清理 5 段） | **`STRAIN_BOARD_PASS`**，数字同开发版 |
| 上位机真板（`strain/step3/check_ui_board.py`，窗口自己的 NIfTI 读取 → `strainwin.run_strain`，4 段） | **`STRAIN_UI_BOARD_PASS`**（逐位 = 参照） |
| F4 Simpson（`simpson.json`） | `SIMPSON_BOARD_PASS`：450 例专家掩膜 + 界面路径 50/50 逐位照旧 |
| CT + F5 病例任务（`verify_board_jobs.py --quick`） | `JOBS_BOARD_PASS` |
| echo_ef（`verify_board_echo_jobs.py --videos 5 --rt 2`） | `ECHO_JOBS_BOARD_PASS`（容积相对差 ≤ 2.5e-4、EF 差 ≤ 2.2e-4，同 §9.55 容差） |
| 1.6 / 部分 A cardiac（`test/cardiac/compare.py --board`） | `CARDIAC_BOARD_PASS`，0 mismatch；仓库里的 `cardiac_board.json` 已还原成 v5.18 那份，v5.19 的在 `board_acceptance_v519/cardiac_board_v519.json` |

**耗时（第四项任务：neon2 追踪重测，板上软件计时，VAL 100 段 / 50 例）**

| 项 | P50 | P95 |
| --- | ---: | ---: |
| **追踪，每例（两切面合计）** | **52.9 ms** | **82.9 ms** |
| 追踪，每切面（T P50 18 帧、74 块） | 27.6 ms | — |
| 追踪，每对帧 | 1.59 ms | 1.96 ms |
| 分割（两次 U-Net 44.3 ms + pp 清理 1.2 ms + 取样） | 50.5 ms | — |
| 提线（骨架 + 两次 O(n²) Dijkstra + 重采样） | 62.6 ms | — |
| 板上任务合计 / 每切面 | 147.7 ms | — |
| PC 墙钟 / 每切面（含上传约 4.7 MB） | 0.21 s | — |

第二步的推算（neon2 每对帧 ≈ 1.5 ms、每例 ≈ 50 ms）得到确认。纯 C 核（flag bit1，只作对照）每对帧 19.4 ms、是 NEON 的 12.4 倍——比第二步的 7.5 倍大，因为 `bm_sad.o` 用 `-O3` 编，gcc 7.5 把 C 路径自动向量化反而更慢（第二步已见：`-O3` 1 516 ns vs `-fno-tree-vectorize` 681 ns / 候选）；默认路径是 NEON，不受影响。
提线是 PS 上最大的一块（62.6 ms），可以换成堆实现的 Dijkstra 再省几十毫秒，本版没动（结果要保持逐位）。

**坑**：本会话的自动权限检查把"停服务、换开发版"拦下过一次，用户在会话里明确同意后才继续（v5.18 部署期间板子一直没动）。板上留着 `/root/strain_v519dev`（开发版源码 + 二进制，约 1 MB，可删）。

**还没做**：STRAUS / Evain 定量验证（要下载）；PL vis_layer 复核演示；证据报告 / 板端网页里没有应变。

## 9.74 部分 B：压缩日志 + SD 卡整卡备份 + 新建 /data 分区（2026-10-18，用户逐步同意；**三步全部完成**）

> 板上软件 / 固件都没动：服务端 v5.19 `bc386abc…`、固件 f150 `30f06745…`。脚本和证据：`E:/PLD/board_storage_b/`（`board_tool.py` 记每条命令的日志）。
> 时机：等 A 的 v5.18、E 的 v5.19、D 的程序包都做完、PC 上没有回归 / 验收进程再做（回归里的稳定性检查会对比日志错误行数，回归中途不能删日志）。

| 步骤 | 文件 | 结果 |
| --- | --- | --- |
| 1 压缩日志 | `step1_journal.py` → `step1_journal.log` | 根分区 **259 MB → 763 MB 空闲**（97% → 89%）；`journalctl --vacuum-size=200M` 删了 503.5 MB 归档日志（704 → 201 MB）；`/etc/systemd/journald.conf` 加 `SystemMaxUse=200M`（原件 `journald.conf.before_b`），journald 已重启、服务 active |
| 2 整卡备份（网络读出） | `step2_backup_sd.py` → `E:/PLD/board_backup/sd_20261018T054022Z/` | `mmcblk1_first_7620MiB.img`（7 990 149 120 字节 = 卡开头到最后一个分区结尾，向上取整到 4 MiB）**sha256 `d12750262708874d2222193ab4f6214eda3aba65ad77fc3e8b64ad7df44aa4e6`，PC 与板上两端一致**；459 s（16.6 MiB/s）；读出期间停服务、`fsfreeze` 冻结 `/root/workspace` 和 `/`（保护进程 40 分钟后强制解冻，这次没触发、已关掉）；之后两个分区都 rw、服务 active。另存 `sfdisk_mmcblk1.txt`、`backup_report.json` |

**卡的布局**（`sfdisk -d`，DOS 分区表 `6e382e1c`）：p1 `Kernel` vfat 200 MB（`/run/media/mmcblk1p1`）、p2 `System` ext4 7 GB（`/`，UUID `bfbc2005…`）、p3 `Data` ext4 249 MB（`/root/workspace`）；
卡共 63 864 569 856 字节，最后一个分区结束在 7 988 051 968 字节——**正好等于 `E:/PLD/FZ3B_ubuntu18.04_211115.img` 的大小**，MBR 分区表与它逐字节相同（这张卡就是用它烧的）。后面约 **55.9 GB 未分区**，没读。
**恢复**：把镜像 `dd` 写到任意 ≥ 8 GB 的卡（分区表在镜像开头），日志 / 程序包 / 验收目录都会回到 10-18 备份时的状态。`board_backup/` 已加进 `make_share_package.py` 的排除表和 `.gitignore`。

**坑**：`board_tool.command()` 的日志前两行是命令原文——在日志里找关键字（如 `WATCHDOG_FIRED`）会匹配到命令本身。第一次报告的 `watchdog_fired` 就这样误判为 true，已改成只解析输出行并更正报告（实际输出 `WATCHDOG_KILLED`、`RW_OK`、`active`）。
paramiko 2.x 的 `Transport.open_session` 参数叫 `window_size`（不是 `window`）。

**第 3 步 /data 分区**（用户 10-18 同意；`step3_data_partition.py check / apply / reboot_verify`，每阶段先核对根分区在 mmcblk1p2、p1–p3 与备份时的 sfdisk 逐项相同）：

| 阶段 | 结果 |
| --- | --- |
| check | `sfdisk --append --no-act` 预演：p4 = 扇区 15 601 664–124 735 487（109 133 824 扇区，**52.04 GiB**，1 MiB 对齐），p1–p3 不变 |
| apply | 写分区表（盘在用，内核重读失败是预期的，`partx -a --nr 4` 让内核认出 p4）；原表另存板上 `/root/mmcblk1_before_p4.sfdisk`；`mkfs.ext4 -L PLDdata` → **UUID `8abd21b9-2fdc-47bd-92ae-f663501cb1b5`**；`/etc/fstab` 追加 `UUID=8abd21b9-… /data ext4 defaults,nofail,noatime,x-systemd.device-timeout=10s 0 2`（原件 `/etc/fstab.before_b`）；挂上、读写通过，可用 49 GB |
| reboot_verify | **`STEP3_REBOOT_PASS`**：重启后 `/data` 自动挂上（`journalctl -b -u data.mount`：Mounted /data）、可写；`data.mount` 的 `RequiredBy=` 为空、只被 `local-fs.target` Wants（= nofail，分区坏了也不卡开机）；FPGA operating、tcp-image-server / plbram / plbdma active，固件 f150、服务端 v5.19 哈希不变；根分区 755 MB 空闲 |

**没做的验证**：没有故意弄坏 / 改错 UUID 再重启来实测 nofail（若失败会进 emergency 模式、SSH 连不上，要人到板子跟前）；用上面的 Wants / RequiredBy 检查代替。
**回退**：`umount /data`，把 `/etc/fstab.before_b` 拷回，`echo ",,," | sfdisk --delete /dev/mmcblk1 4`（或按 `/root/mmcblk1_before_p4.sfdisk` 恢复），重启；最坏情况用 PC 上的整卡镜像恢复。
**用途**：C（HRF 大图）、G（板端网页工作站的病例）放 `/data`；旧验收目录要不要挪过去或删掉，由用户定。

## 9.75 PL→DDR 直连实测：S_AXI_HP0_FPD（64 位）**不换 boot.bin 就能用**（2026-10-18，用户同意；测试比特流临时加载、已恢复 f150）

**起因**：§3.6（10-01）结论是"厂商 boot.bin 没使能任何 PL→PS 从口、`.bit` 改不了"，此后整个架构按"PS 把数据按条带搬进 256 KB BRAM"设计。10-18 只读复查发现这个结论与寄存器不符：

| 证据（只读，板上 f150） | 值 | 含义 |
| --- | --- | --- |
| `CRF_APB.RST_FPD_TOP` 0xFD1A0100 | `0x32` | 第 7–12 位 AFI_FM0–5 复位 = 0（已解除）；`psu_afi_config` 本来就无条件清掉（掩码 `0x1F80`） |
| `CRL_APB.RST_LPD_TOP` 0xFF5E023C | `0xA93` | 第 19 位 AFI_FM6 = 0（已解除） |
| AFIFM2（= S_AXI_HP0_FPD）RDCTRL / WRCTRL 0xFD380000 / +0x14 | **`0x3B1`** | FABRIC_WIDTH = 1 = **64 位**，厂商 FSBL 专门配的；其余 HPC0/1、HP1–3、LPD 都是 `0x3B0`（= 0 = 128 位默认）。编码由 Vivado 生成 64 / 32 / 128 位 PS 配置的 `psu_init.c` 对照确认：0 = 128、1 = 64、2 = 32 |
| 厂商设备树 `fpga-driver@0`（`baidu,paddle_mobile_fpga_drv`） | `memory-region` → `reserved-memory/buffer@0` = `0x4000_0000` 起 1 GB、`buffer@1` = `0x8_0000_0000` 起 1 GB，都 `no-map` | 厂商给 PL 加速器预留的 DDR（Linux 的 System RAM 只有 0–1 GB 和 `0x84000_0000` 起 1 GB，所以板上只看得到约 1.9 GB）；驱动没加载，区域空闲 |

**实测**（`E:/PLD/board_hp0_test/`；测试比特流只用 fpga_manager 临时加载，**没碰 `/lib/firmware/ai_sys.bin`**）：
- HLS 探测核 `ai/hp0_probe/hp0_probe.cpp`（64 位 m_axi 直连 HP0，II = 1；C 仿真 + 协同仿真 PASS）：从 src 连续读 n 个 64 位字算 sum / xor，同时写 `dst[i] = src[i] ^ (seed + i)`；
  BD `ai/bd/build_hp0_probe.tcl`（PS 预设 + HPM0 32 位控制 + **HP0 64 位**，只映射 DDR_LOW，pl_clk0）→ `bitstream/hp0_probe_test.bin` `6ba8b974…`，WNS +6.29 ns；
- 板上工具 `hp0_tool.c`（对齐 64 位 volatile 读写，超时只报 HANG、不重配 PL）；`hp0_test.py pre / test / restore`。
- **结果 `HP0_TEST_PASS`**：src `0x4000_0000`、dst `0x5000_0000`，n = 1 / 512 / 64 Ki / 1 Mi / **16 Mi 字（134 MB 每个方向）全部逐位正确**（sum、xor、每个写回字），
  **读 811 MB/s + 同时写 811 MB/s**（= 8 B × 101.376 MHz，PL 侧每拍一字跑满，瓶颈在 PL 时钟 × 64 位，不在 DDR）。
- 恢复 `HP0_RESTORE_PASS`：重启后开机服务加载 f150（VERSION 0x494D5004 OK）、pp / vis ready、服务 active、`hp0_probe_test.bin` 已删；TCP 三模型逐位 `TCP_PERF_PASS`（`hp0_after_tcp_performance.json`）。

**10-01 为什么没通（推断，未复现）**：当时的 PL 主口按 128 位接 HP0，而 AFIFM2 是 64 位——宽度不匹配；HP1 / HPC0 / ACP 也不通的原因没查清（那几次的 BD 宽度没留记录）。**只验证了 HP0 @ 64 位**。

**意义与后续（都要出新比特流，约 10-27 冻结前；LUT 已 84.75%）**：
- PL 核可以**直接读写 DDR**，不再必须经 PS 条带搬运（现在 512 整网 292 ms 里 `put` + `get` 约 122 ms）；同时 PS 和 PL 访问 DDR 是正常用法（以前"搬运与计算重叠坏数据"是张量 BRAM 的问题）。
- 带宽上限：HP0 64 位 × PL 时钟，101 MHz 时 811 MB/s / 方向，152 MHz 约 1.2 GB/s；比现在 HPM1 128 位的 `put` 通路窄。HP1–3 默认 128 位，能否用要另测（AFIFM 宽度寄存器能否运行时改也没测）。
- 直接受益的候选：C（大图）里 PL 直接从 DDR 读原图做裁剪 / 缩放 / 分块；更远：U-Net 引擎读写 DDR 中的张量（要改调度器和 HLS 访存，工作量大）。
- PS 访问预留区用 `/dev/mem` + `O_SYNC`（非缓存，与非一致性的 HP 口天然一致）；只能对齐 32 / 64 位访问，不能 memcpy / memset（会 SIGBUS）。

## 9.76 服务端 v5.20：部分 C——大图无缝分块 + 框选放大（病例任务 `JOB_TILE`，HRF）上板（2026-10-18，**已部署、已验收**；固件 f150 不变）

> **当前**：`/root/tcp_image_rx/tcp_image_server` = v5.20 **`9c2ede7fb57144441e062a0fcafa9312dbff0fd039c7226beaa7ccdac5e6ea59`**
> （release `/root/tcp_image_rx/releases/v5_20261018T102938Z`，`deploy_board_v5.py prepare --exclude=src/fundus_cdr.c --exclude=src/fundus_cdr.h` / `activate`，
> 板上原生编译 0 警告；drop-in `Nice=-20` 照旧，服务 NI −20）。**release 二进制与验收过的开发版逐字节相同**。固件 f150 `30f06745…` 不变。
> **回滚目标**：v5.19 **`bc386abcc0d0b6d889cfc31004f2c42bc80ce1961214dc870f895ba445c19cf6`**（`deploy_board_v5.py rollback`）。**不能退到 ≤ v5.14**。
> 清单副本 `E:/PLD/board_acceptance_v520/deployment_v520_manifest.json`。部分 D 的 `fundus_cdr.c/.h` 仍排除（与 v5.18 / v5.19 一样）。
> 全文报告：**`E:/PLD/hrf/README.md`**（数据、定义、C1 / C2 全部数字）。

**v5.20 相对 v5.19（只含部分 C）**：新文件 `src/job_tile.c`（JOB_TILE = 8）、`src/tile_plan.c/.h`（分块规划，纯 C）；`src/job.h`（`JOB_TILE`、`job_unet_account`、`job_overlay_compose`
声明，job.h 现在 include `unet_ai.h` / `vis_ai.h`）；`src/job.c`（分发、caps 任务表加 `"tile"`、`"server":"v5.20"`、`job_unet` 的计时拆成 `job_unet_account`）；
`src/job_overlay.c`（导出 `job_overlay_compose`，选 vis_layer 或 PS）。其余源码与 v5.19 清单逐个相同（部署前按 v5.19 清单 sha256 核对过）。**以后的服务端版本都要在这上面编**。

**做了什么**（用户 10-18 定的改版 C）：
- **逐位精确的分块**：任意大小的图按比例缩放成"层"（PC 上 LANCZOS、补黑到 8 的倍数），框切成 ≤ 512 的块，不贴图像边的边各留 24 像素真实邻域，各块只取自有区拼回
  → **= 该倍数整幅图一次推理，逐位相同**。一块够用时就是老 ROI 的裁剪窗口（`ai_view_roi_crop`），所以 512 帧上 1 倍框选 = 原来的 v5 ROI 帧。
- **放大倍数**：1 倍 = 原图宽缩到 600（STARE 扫描定）；每张图的上限 = min(2.5 倍（HRF 训练集 Dice 曲线）, 原图分辨率)；超过上限只"显示放大（仅插值）"。**模型 0 权重不变，没有微调**（有可用尺度）。
- **板上**：层文件在 `/data/hrf/<图>/m<倍数>.lvl`（50 张 = HRF 45 + samples 的 STARE 5，449 MB，sha256 板上核对过），板子只 pread 裁剪；每块 PL 推理 → PL pp_layer 计数 / 掩膜 → PL vis_layer 叠加，逐块回传；
  视野外的块跳过；计算顺序可由 PC 按总览不确定度给。上传区域的方式也支持（任意图）。r3（PL 经 HP0 直接从 DDR 裁剪 / 缩放）没做，接口是 `job_tile.c` 的 `src_crop()`。
- **上位机**：主窗口 ROI 框选升级为「框选放大」（`app.py`，备份 `app.py.bak_before_c_20261018`；原有 `self.roi` / `_roi_press/_drag/_release` / `_draw_roi` / `roi_verify_note` / Esc / 「清除 ROI」都在）；
  1 倍 = 老 ROI；> 1 倍「发送」= 打开**大图精查**窗口（`bigwin.py`：总览 + 不确定度格子 + 建议框 → 框选 → 倍数 → 逐块重算 + 显示放大 + 指标 / 形态）。引擎 `tile_engine.py` 不依赖界面（C3）。

**验收**（证据 `E:/PLD/board_acceptance_v520/`、`E:/PLD/hrf/results/`）

| 项 | 结果 |
| --- | --- |
| C1 精确性（PC，`hrf/c1_exactness.py`） | 21 层 × 462 框、1.04 亿像素 0 差异；反例（边距 16）7058 差异；原始分辨率两种切法重叠 0 差异；512 帧 2000 框 = `proto.roi_crop` |
| 规划（`hrf/c2_plan_compare.py`） | 板端 C == 上位机 Python，5239 组 0 差异 |
| STARE（开发集） | 保持长宽比 1 倍 Dice 0.7813 vs 今天拉伸 512 的 0.7770（16/20 张更好） |
| HRF 训练集（定上限） | 1 倍 0.703 → 2 倍 0.761（峰）→ 2.5 倍 0.759 → 3 倍 0.712 → 4 倍 0.490 |
| 板上 `verify_board_tile.py --files`（开发版 + 回归里的部署版） | **`TILE_BOARD_PASS`**：1 倍框 == v5 ROI 帧 == golden（24 框）；大图每块 == 整数参照；pp / vis == 参照；PS == PL；/data 文件 == 上传；顺序无关；出错路径 |
| 界面 `verify_board_tile_ui.py` | **`TILE_UI_BOARD_PASS`**：1 倍仍是老 ROI（"ROI 与整图推理逐值一致 ✓"）；2 倍 → 大图精查窗口，总览 / 放大逐位 == 参照、按不确定度顺序、层来自 /data |
| **回归 27 组**（`board_acceptance_v520/regression.py`，从 v518 派生：v518 的 21 组 + strain / strain_ui / fundus_cdr + tile_plan / tile / tile_ui） | **27/27 通过**（`regression_v520_summary.json`）：第 1 轮 25 组过，`v516` / `fundus_cdr` 被**回归脚本自己的判定写错**判失败（v516：我加了"日志里要有 PASS 标记"，而该脚本把 caps 写死成 v5.16、必然打印 FAIL；fundus_cdr：报告里的键是 `passed`）——两个脚本自身结果都通过；修好判定后在同一部署二进制上重跑这两组，都过。5000 帧稳定性 0 差异、0 失败、0 重连 |
| **HRF 测试集（30 张，只一次，锁文件 `hrf/results/test_lock.json`，部署版板上跑）** | Dice 1 倍 0.694 / 1.5 倍 0.736 / **2 倍 0.760 / 2.5 倍 0.765**；细血管召回 0.29 → 0.45；每张整幅 0.30 / 0.70 / 1.25 / 2.07 s |

**耗时**：每块 U-Net = 1.10 µs/像素 × 面积 + 3.3 ms（512² → 292 ms，与整帧相同）；HRF 1 倍总览 2 块 0.30 s，2 倍整幅 6 块 1.25 s，框选放大常见 50–400 ms。halo 开销理论 21.8%（512 块、464 自有），
整幅实测 8–19%；视野跳过在 HRF 整幅只省约 4%（视野几乎占满），视野外的框整块不算。PL 后处理比 PS 快约 2 倍。

**坑**：
- 板端会断开空闲连接（看图 / 框选可能很久）→ `tile_engine.run_board` 每个任务先 `link.close()` 再新连。
- tile 块不压缩：A53 上 zlib level 1 比千兆网发原样慢（2 倍整幅 1.44 → 1.25 s）。
- STARE 700 宽：2 倍层（1200 宽）是把原图插值放大后送进网络（血管密度只剩 0.5%）——所以每张图的上限要和原图分辨率取小。
- 板上 systemd 提示 "unit file … changed on disk / NeedDaemonReload=yes"：板子时钟每次开机回到 2021-08-31 19:53，之前开机时写的单元文件"看起来更新"。无害（NI −20 生效，activate 会 daemon-reload）。
- 板上留着 `/root/tile_v520dev`（开发版，约 1 MB，可删）和 `/data/hrf/c_verify/`（验收用层文件，约 30 MB，回归要用，别删）。

**还没做**：C4（视盘居中 Zone B 自动 ROI、Knudtson 管径、A/V 可行性）；r3（PL 从 DDR 直接裁剪 / 缩放）；病理全切片写进报告"下一步"。

## 9.77 上位机小修：大图精查总览被"不确定度格子"盖成整片橙色（2026-10-18，只改 PC 界面，板上没动）

- **现象**：`bigwin.py` 总览整片黄 / 橙色，看不到眼底图（10-18 全项目演示时用户发现；演示截图里也是这样）。
- **原因**：`render_overview` 先 `convert("RGBA")` 再 `ImageDraw.Draw(im, "RGBA")` 画半透明格子——Pillow 在 RGBA 底图上是**直接覆盖**像素（不混合），转回 RGB 后 alpha 丢失，格子变实心 (255, 160, 0)。实测：同一填充 (255, 160, 0, 40) 在 RGBA 底图上得 (255, 160, 0)，在 RGB 底图上得 (48, 42, 25)。
- **修复**：底图保持 RGB（`ImageDraw` 的 "RGBA" 模式只在 RGB 底图上混合）；原件 `tcp_image_ui/bigwin.py.bak_before_heatfix_20261018`。只影响显示，logit / 掩膜 / 拼接不受影响。
- **验证**：`verify_board_tile_ui.py` 真板重跑 `TILE_UI_BOARD_PASS`；截图 `E:/PLD/demo_20261018/shots/bigimg/`（总览可见眼底 + 血管叠加 + 淡橙不确定格子 + 青色建议框）。
- **PL 程序台窗口撑出屏幕（也已修）**：底部结果是一长串不带空格的中文，`uistyle.label(wrap=True)` 只在空格处折行，把窗口撑到比 2560 宽的屏幕还宽；改用 `uistyle.text_box`（自动按字折行，原件 `progwin.py.bak_before_wrapfix_20261018`）；`_verify_progwin.py` 照样读 `var_txt`，重跑通过。
- **F5 界面脚本**：已由另一个会话按 PL 配准引擎更新（PL 引擎路径不再要求 3 / 15 帧，改查 `evals > 0`，10-18 08:55），演示重跑 `GUI_F5_PASS`。
- **演示里另外发现、还没修**：时间线 JSON 的 `other_ms` 出现 1.8e16（疑似减成负数后无符号溢出，界面比例正常）；杯盘比没有界面页。


## 9.78 服务独立 CPU 切片（tcpimage.slice，8192 份额）：修好"满载时被 ssh 负载拖慢"（2026-10-20，用户同意；服务端 v5.20 二进制不变）
- **问题**（J3，§9.67–9.68）：板子 cgroup v1 的 cpu 控制器下 `/system.slice`、`/user.slice` 都是 1024 份额，服务在前者、ssh 起的负载在后者，CFS 先按切片平分，`Nice=-20` 只在切片内部起作用。
- **改法**：新增顶层切片 `/etc/systemd/system/tcpimage.slice`（`CPUShares=8192`、`CPUAccounting=yes`；**名字里不能带横杠**，带横杠会变成 `tcp.slice` 的子切片）+ drop-in `/etc/systemd/system/tcp-image-server.service.d/20-slice.conf`（`Slice=tcpimage.slice`）。脚本 `boot/slice_fix.py status | apply | rollback`。**和 `deploy_board_v5.py` 互相独立**（后者只管 `10-nice.conf`），服务端升级 / 回滚都不会动它。
- **验证**：`systemctl show` → `Slice=tcpimage.slice`、`Nice=-20`、active；`/sys/fs/cgroup/cpu/tcpimage.slice/cpu.shares` = 8192；服务进程在该切片。
- **前后对比**（同一 v5.20 `9c2ede7f`、f150，`perf_j/j3s_server.py`，结果 `perf_j/results/j3s_before_slice_v520.json` / `j3s_after_slice_v520.json`；负载 = ssh 起的 `hog` 4 线程）：

| 128 档 ai_us（ms） | 修前 P50 / P99 / 最大 | **修后 P50 / P99 / 最大** |
| --- | --- | --- |
| 空载 | 22.04 / 22.11 / 22.22 | 22.11 / 22.21 / 22.22 |
| cpu4 | 22.05 / 95.29 / 99.92 | **22.08 / 27.79 / 28.02** |
| mem4 | 29.25 / 173.5 / 352.1 | **29.09 / 47.25 / 47.28** |

| 512 档 ai_us（ms） | 修前 P50 / P99 | 修后 P50 / P99 |
| --- | --- | --- |
| 空载 | 290.9 / 299.2 | 292.7 / 303.1 |
| cpu4 | 298.3 / 355.9 | 308.9 / 331.5 |
| mem4 | 344.8 / 448.4 | 372.3 / 427.8 |

- **解读**：CPU 争用造成的长尾基本消除（128 cpu4 P99 95 → 28 ms，最大值 100 → 28 ms）。**mem4 的 P50 没变（≈29 ms）**——那是 DDR 带宽争用（PS 搬张量走 DDR），切片管不到；512 档每组只有 29 帧，P50 的 ±10% 起伏在噪声范围内。对外口径："CPU 满载下 128 档最坏 28 ms；内存带宽被打满时最坏 47 ms"。
- 注：10-20 修前测得 cpu4 P99 95 ms，比 J3（10-17，v5.17）的 183.8 ms 低，板子状态不同，以本次前后对照为准。PC 往返 128 档 P50 ≈ 62 ms（板上推理 22 ms），说明经 PC 的链路本身要 40 ms，板上闭环（心脏聚焦方案）能省掉这部分。
- 板子空闲时 `load average` 恒为 1.00（CPU 99.6% 空闲），疑似有个 D 状态线程，和本次无关，未查。


## 9.79 服务端 v5.21：创新 ①②③（关键帧调度 + 质量灯 + 保形 EF 区间）上板——`echo_ef` mode 2（2026-10-24，用户同意；**已部署、已验收**；固件 f150 不变）

> **当前**：`/root/tcp_image_rx/tcp_image_server` = v5.21 **`abd8242b2cedef31c32cc239d017805373ef8eea8e6d0c375bdc5fc0e1315c5a`**
> （release `/root/tcp_image_rx/releases/v5_20261024T180714Z`，`deploy_board_v5.py prepare --exclude=src/fundus_cdr.c --exclude=src/fundus_cdr.h` / `activate`）；
> **回滚**：`deploy_board_v5.py rollback` → v5.20 `9c2ede7f…`（同一 manifest 的 backup）。固件 `30f06745…`（f150）、程序包 17–20、切片 `tcpimage.slice`（§9.78）都没动。
> 主机侧说明：`E:/PLD/keyframe/board_c_port_123_2026-10-24.md`；算法与评估：`keyframe/keyframe_simulation_2026-10-23.md`、`quality/quality_guidance_and_conformal_ef_2026-10-24.md`。

**改了什么**（`src/`）：
- `echo_num.c/.h`（新）：`job_echo.c` 的 clean / 百分位 / 中位数 / dlaev2 / `volume_pca` / `find_peaks` / `analyze` 和 `job.c` 的 `job_np_sum` **原样搬出**（`en_` 前缀；`job_echo.c` 留别名，mode 0 / 1 代码一行没动）。
- `kf.c/.h`（新）：① 关键帧调度（full / hold / fixed / adaptive：间隔上限、块残差触发、相位强制）、轮廓（沿像素边取边中点 + 按弧长等距）、多边形填充、跟踪一步（`bm_track_line`，**`BM_OPT_NEON`**，环形 SAD 聚合 + 位移环形平均）。
- `echo_quality.c/.h`（新）：② 九个质量特征（扇区自动定位、形态学）→ 得分 / 灯色 / 原因；③ σ(x)、80 % / 90 % 区间、80 % 区间跨 40 / 50 → 复核；mode 2 参数扩展解析 `eq_parse_ext`。
- `job_echo.c`：mode 2 → `job_echo_kf`。参数 = 128 字节基本块 + 560 字节扩展 "KF01"（调度、跟踪、该调度的 EF 校准 a / b、60 个模型数字）。结果在 mode 0 字段之外多 `keyframe`（调度、原因计数、每关键帧 PL ms、每跟踪帧 PS ms）、`key`（每帧 0 / 1）、`quality`、`interval`。
- `job.c`：`job_np_sum` 搬走；`caps` 报 `"server":"v5.21","echo_modes":[0,1,2]`（上位机据此决定能否发 mode 2）。`makefile`：三个新文件 `-ffp-contract=off`。
- 上位机：`jobs.py` `load_kf_config` / `echo_kf_params` / `echo_ef(..., keyframe=)`；`echo_kf_config.json`（`quality/make_kf_config.py` 生成：推荐档 `adapt_nmax6_res1.15_winoff` 与每帧推理两套）。
  心超窗口 `echowin.py`：新模式「关键帧」（默认；只走板上整病例，先查 caps，老板端提示要 v5.21+）——EF 下方 80 % 区间（跨 40 / 50 红字"建议复核"），质量灯只在临床测量栏作原因提示（不显示 stability），时间线标关键帧（蓝）/ 跟踪帧（灰），逐拍 EF 用这套调度的 a / b，报告加"不确定度与质量提示"（80 % / 90 % 区间、关键帧统计）。离线自检 `_verify_echowin_kf.py`（假 echo_ef = 主机参考）**`ECHOWIN_KF_PASS`**；`_verify_echowin.py` 跳过关键帧模式。
  10-24 晚真板窗口（`_verify_echowin_kf.py --board`，2 段）通过，见下表"板子重启后"；顺手改了两处显示：关键帧模式结束时进度条填满（板上最后一个进度包在结束前），绿灯不再列"主要原因"（只黄 / 红灯列）。

**主机参考 = 同一份 C**：`E:/PLD/keyframe/build_core.sh` 用板上的数值选项把这些文件编成 `echo_core.dll`（`-ffp-contract=off`，**`-mfma`**），主机上 ①②③ 的全部数字都由它算出。

**验收**（证据 `E:/PLD/board_acceptance_v521/`；先 `devrun.py start` 用 release 的二进制临时顶替服务，测的就是要装的那个）：
| 项 | 结果 |
| --- | --- |
| 板上编译（gcc 7.5） | 0 告警；`ai_view_count_test` PASS |
| mode 0 / 1 老回归 `verify_board_echo_jobs.py --videos 20 --rt 5` | **`ECHO_JOBS_BOARD_PASS`**（全帧 20 段、实时 5 段、PS 清理、实时 + 按帧率送帧；容积相对差 ≤ 3.8e-4、EF 差 ≤ 0.0014，与 v5.20 相同） |
| mode 2 `verify_board_echo_kf.py --videos 20`（两套配置） | **`ECHO_KF_BOARD_PASS` 40 / 40**：关键帧序列、每帧掩膜、每帧容积、EF / 各拍 / 心率、9 个质量特征、得分、灯色、原因与主机 DLL **逐位相同**；区间 σ 相对差 < 1e-12，复核相同；`caps` = v5.21 |
| 板上耗时（推荐档，20 段） | **跟踪 0.81 ms / 帧（最长 2.19 ms）**，PL 22.9 ms / 关键帧，关键帧 17.8 %；整段任务约 1 s，每帧推理约 4.6 s |
| 启用后 | `activate` PASS（`Nice=-20`）；`Slice=tcpimage.slice`，systemd "unit changed on disk" 提示消失（activate 的 daemon-reload）；固件哈希不变；mode 2 复查 3 段 × 2 套 `ECHO_KF_BOARD_PASS`。mode 0 / 1 的 3 段复查当时被用户叫停中断，之后板子离线 |
| 板子重启后（10-24 晚，冷启动后服务自动起来：v5.21 `abd8242b…`，`tcpimage.slice`） | `verify_board_echo_jobs.py --videos 3 --rt 1` **`ECHO_JOBS_BOARD_PASS`**（容积相对差 ≤ 2.5e-4、EF 差 ≤ 2.2e-4）；`verify_board_echo_kf.py --videos 3` **`ECHO_KF_BOARD_PASS` 6 / 6**（跟踪 0.79 ms / 帧，PL 22.9 ms / 关键帧）；心超窗口真板：`_verify_echowin_kf.py --board` 2 段 **`ECHOWIN_KF_PASS`**（板上关键帧 / 掩膜 / 容积 / EF / 灯色 / 区间 = 主机参考；其中 0X10094BA0A028EAC3 EF 39 %、80 % 区间 30–48 % 跨 40 → 红字复核），`_verify_echowin.py --board --job` mode 0 / 1 **`ECHOWIN_SELFTEST_PASS`**；截图 `echowin_keyframe_board_*.png` |

**上板时查出、已修的两个问题**：
1. 第一版没给 `bm_track_line` 传 `BM_OPT_NEON`，板上跑的是纯 C 核：**5.5–7.5 ms / 跟踪帧**。补上后 0.81 ms（与推算一致；NEON 与 C 核逐位一致，part E 已验证，本次 40 / 40 也逐位）。
2. PC 上 MinGW 的 `fma()`（库函数）**不是正确舍入**：`en_volume_pca` 里显式的 `fma` 在 20,000 帧里有 3,905 帧容积和硬件 FMA 不同。板上（A53 硬件 FMA）是对的；主机 DLL 改为 `-mfma` 后与板上逐位一致，主机评估数字重算后不变。

**已知差异（不是本次引入）**：容积和 numpy 不逐位一致（散布矩阵累加顺序，§9.55 起按容差验收）；中值滤波后两相邻等值波谷，scipy 与 C 留的不同，EchoNet VAL 1,288 段中 19 段心率（偶尔某拍 ES 帧）不同，EF 基本不受影响。

**没做**：现场 HDMI 采集模式（流式，`kf.c` 的接口已按逐帧设计）→ 10-25 写了文件信号源版，见 §9.80。

## 9.80 服务端 v5.22：现场模式 `echo_ef` mode 3（板上信号源，2026-10-25；**用户同意，已启用、已复查**；固件 f150 不变）

> **10-25 稍后已被 v5.23 `f3db5cca…` 取代（见 §9.81）**，下面是 v5.22 启用时的记录。
> **当前服务（10-25 启用）**：`/root/tcp_image_rx/tcp_image_server` = v5.22 **`19534be5…`**（全哈希见下行），`Nice=-20`，`Slice=tcpimage.slice`；
> **回滚**：`deploy_board_v5.py rollback` → v5.21 `abd8242b…`（本 release 清单的 backup）。启用前 v5.21 的清单另存在 `E:/PLD/board_acceptance_v522/deployment_v521_before_v522_prepare.json`。
> **release**：`/root/tcp_image_rx/releases/v5_20261025T081738Z`，`tcp_image_server` = **`19534be563b8786cc28ea387311e7e9fcf0e8c29d13c91001fdea09e17791346`**
> （`deploy_board_v5.py prepare --exclude=src/fundus_cdr.c --exclude=src/fundus_cdr.h`，gcc 7.5 无告警，`ai_view_count_test` PASS）。
> 说明：`E:/PLD/live/live_mode_2026-10-25.md`；FPGA 证据：`E:/PLD/fpga_evidence/f2_f3_f4_results_2026-10-25.md`。

**改了什么**：
- `src/live_crop.c/.h`（新）：前 K 帧找"会动的方块"（max − min > thr → 4 × 4 格 → 最大 8 连通块 → 居中方块），之后只读方块，整数盒滤波到 112（整数倍逐位还原）。也编进主机 `echo_core.dll`。
- `src/live_src.c/.h`（新）：信号源 `file`（`/root/echo_src/*.y8`，按自身帧率放出）和 `v4l2`（UVC 采集卡 YUYV，**未测，卡没到**）。
- `src/job_echo.c`：mode 2 拆成引擎 `kfe_init / kfe_frame / kfe_finish`（质量特征最后统一累加，调用顺序不变，mode 2 结果不变）；新 mode 3 `job_echo_live`（采集线程 + 任务线程，每帧 "live" 块含 7 个时间戳，每 0.25 s 滚动 EF，结果多 "stream"）。参数 = mode 2 的 688 字节 + 112 字节 "LV01"（布局见文件）。
- `src/job.c`：caps `"server":"v5.22","echo_modes":[0,1,2,3],"live_sources":["file","v4l2"]`。
- 上位机：`jobs.py` `echo_live_params / echo_live / unpack_live`；`verify_board_echo_kf.py` 的 caps 检查改为"支持 mode 2"（v5.21+）。

**验收**（证据 `E:/PLD/board_acceptance_v522/`、`E:/PLD/live/results/`；devrun 临时顶替服务，测完已切回 v5.21，`DEV_STOP_PASS`）：
| 项 | 结果 |
| --- | --- |
| mode 2 回归 `verify_board_echo_kf.py --videos 20` | **`ECHO_KF_BOARD_PASS` 40 / 40** 逐位（跟踪 0.81 ms / 帧，PL 22.9 ms / 关键帧） |
| mode 0 / 1 回归 `verify_board_echo_jobs.py --videos 20 --rt 5` | **`ECHO_JOBS_BOARD_PASS`**（容积相对差 ≤ 3.8e-4、EF 差 ≤ 0.0014，与 v5.21 相同） |
| mode 3 `live/verify_board_echo_live.py`（4 个 640 × 480 模拟屏幕源，10 次运行） | **`ECHO_LIVE_BOARD_PASS`**：固定裁切 112 帧 == 原视频、结果 == 主机参考；自动裁切结果 == 同帧 mode 2；live 块 == 最终结果 |
| 延迟（推荐档、固定裁切，采到 → 掩膜 + 容积） | 中位数 29 ms、P99 67 ms，不累积；每帧推理跟不上 50 fps（PL 22.9 ms > 20 ms，延迟涨到 0.5–1.2 s） |
| 启用（10-25，用户同意） | `activate` **`V5_ACTIVATE_PASS`**（`Nice=-20`、`tcpimage.slice`，运行中的二进制哈希 = `19534be5…`）；复查：mode 0 / 1 `verify_board_echo_jobs.py --videos 3 --rt 1` **PASS**，mode 2 `verify_board_echo_kf.py --videos 3` **PASS 6 / 6**，mode 3 `verify_board_echo_live.py` **10 / 10 PASS**（推荐档固定裁切延迟中位数 29–30 ms、P99 67–71 ms）；证据 `*_post_activate.*` |

**已知**：`dev_server.log` 里每次客户端断开都有一行 `[warn] frame not stored (rc=-1)`，v5.21 验收时也有（95 次），不是本次引入。

**没做**：~~心超窗口接现场模式~~（10-25 已做，见 §9.81）；采集卡实测（V4L2、屏到屏延迟 F1）。

## 9.81 心超窗口接上现场模式 + 服务端 v5.23（live 块可带本帧图像，2026-10-25；**用户同意，已启用、已复查**；固件 f150 不变）

> **10-25 稍后已被 v5.24 `75c294e9…` 取代（见 §9.82）**，下面是 v5.23 启用时的记录。
> **当前服务（10-25 启用）**：`/root/tcp_image_rx/tcp_image_server` = v5.23 **`f3db5cca…`**（全哈希见下行），`Nice=-20`，`Slice=tcpimage.slice`；
> **release**：`/root/tcp_image_rx/releases/v5_20261025T083806Z`，`tcp_image_server` = **`f3db5cca1e2cbd219e3c24137e692342146745494b2535115db2885cd4ba2c3e`**（gcc 7.5 `-Werror` 通过）。
> **回滚**：`deploy_board_v5.py rollback` → v5.22 `19534be5…`（本 release 清单的 backup，`board_check_v5/deployment.json`）。启用前 v5.22 的清单另存在 `E:/PLD/board_acceptance_v523/deployment_v522_before_v523_prepare.json`；再往前回 v5.21 用 `board_acceptance_v522/deployment_v521_before_v522_prepare.json`。

**为什么要 v5.23**：现场模式下帧在板上，PC 没有图像。v5.22 的 "live" 块只有掩膜和容积，窗口运行中只能画轮廓、结束后才拿到 112 帧；接采集卡时 PC 也只是显示端。
v5.23 只加一个开关：LV01 参数 flags bit2 = 每个 "live" 块在掩膜后面带本帧 112 × 112（12,544 字节，50 fps 约 0.6 MB/s）。不置位时与 v5.22 完全相同。caps 报 `"server":"v5.23"`。

**上位机**（`tcp_image_ui`）：
- `jobs.py`：`LIVE_FRAME = 4`；`unpack_live` 多回 `frame`（老板端为 None）。
- `echowin.py`：运行方式新增「板上信号源（现场）」；左栏新卡片「现场信号源」（`E:/PLD/live/src/index.json` 里的 .y8 测试源 + 采集卡 video0）和「画面方块」（自动找 / 固定真值方块）。
  运行中逐帧显示画面（v5.23+）、轮廓、容积曲线、关键帧标记和板上滚动 EF；结束后与关键帧模式一样显示 80% 区间、质量提示、报告，状态栏另给画面方块和延迟；
  「停止」= 断开连接（板上任务随之结束，没有最终结果）；板端不到 v5.22 时提示、不发任务。报告文件名改用 `splitext`（.y8 名字不再被截错）。
- 自检 `_verify_echowin_live.py`：离线（假板子 = 主机参考）`ECHOWIN_LIVE_PASS`；`--board` 见下表。`_verify_echowin_kf.py` 仍 PASS。
- `verify_board_echo_live.py` 的 caps 检查改为"支持 mode 3"（v5.22+）。

**验收**（证据 `E:/PLD/board_acceptance_v523/`；devrun 临时顶替服务，测完已切回 v5.22，`DEV_STOP_PASS`）：
| 项 | 结果 |
| --- | --- |
| 窗口 + 真板 v5.22（10-25，启用后） | `_verify_echowin_live.py --board` **`ECHOWIN_LIVE_PASS`**：174 个 live 块全进窗口，结果 = 主机参考逐位，运行中无图像（预期）；证据 `board_acceptance_v522/echowin_live_board_v522.*` |
| 候选 v5.23：mode 2 回归 `verify_board_echo_kf.py --videos 20` | **`ECHO_KF_BOARD_PASS` 40 / 40** |
| 候选 v5.23：mode 0 / 1 回归 `verify_board_echo_jobs.py --videos 3 --rt 1` | **PASS** |
| 候选 v5.23：mode 3 `verify_board_echo_live.py` | **10 / 10 PASS** |
| 候选 v5.23：窗口 `_verify_echowin_live.py --board` | **`ECHOWIN_LIVE_PASS`**，每个 live 块带图像（`frame in live blob: True`） |
| 启用（10-25，用户同意） | `activate` **`V5_ACTIVATE_PASS`**（`Nice=-20`、`tcpimage.slice`，运行中的二进制哈希 = `f3db5cca…`，caps `"server":"v5.23"`）；复查：mode 0 / 1 `verify_board_echo_jobs.py --videos 3 --rt 1` **PASS**，mode 2 `verify_board_echo_kf.py --videos 3` **PASS 6 / 6**，mode 3 `verify_board_echo_live.py` **10 / 10 PASS**（推荐档固定裁切延迟中位数 28.6–29.5 ms、P99 65.5–69.5 ms），窗口 `_verify_echowin_live.py --board` **`ECHOWIN_LIVE_PASS`**（174 个 live 块全带图像）；证据 `board_acceptance_v523/*_post_activate.*`、`activate.log` |
| 带图对延迟的影响（同一源各 2 次） | 不带图中位数 28.8–30.1 ms / P99 67–68 ms；带图 29.0 ms / 67–70 ms：没有影响 |

**没做**：采集卡实测（V4L2、屏到屏延迟 F1）。

## 9.82 服务端 v5.24：全心扩展 "HC01"（节段室壁运动 + 右室 / 右房），程序包 21 / 22，四个测试集一次性评估（2026-10-25；**用户要求"一次性都做完"，已启用、已复查**；固件 f150 不变）

> **当前服务（10-25 启用）**：`/root/tcp_image_rx/tcp_image_server` = v5.24 **`75c294e9b633c61c3a60e6a046b6d5b2fb90b78ac7e716ec376b0d4d290e6ca0`**，release `/root/tcp_image_rx/releases/v5_20261025T104330Z`，`Nice=-20`，`Slice=tcpimage.slice`。
> **回滚**：`deploy_board_v5.py rollback` → v5.23 `f3db5cca…`（本 release 清单的 backup）；启用前 v5.23 的清单另存 `E:/PLD/board_acceptance_v524/deployment_v523_before_v524_prepare.json`。
> 编译仍是 `prepare --exclude=src/fundus_cdr.c --exclude=src/fundus_cdr.h`。caps：`"server":"v5.24"`，新增 `"heart":["wall_motion","right_heart"]`。
> **板上程序包**（10-25 装，`LOADED.txt` 全 OK）：`programs_rh/cardiac_rv.upk` **21**、`cardiac_ra.upk` **22**（CardiacUDA `rv_pl1_5` / `ra_pl1_5`，128 档自检 0 / 16384 差，PL 22.0 ms）。回滚服务端不用卸程序包（旧服务端不调用）。

**做了什么**
- 新文件 `src/cardiac_ext.c/.h`（`-ffp-contract=off`，makefile 已加；同一份 C 编进 `E:/PLD/keyframe/echo_core.dll`）：HC01 解析、节段面积变化（6 个心尖四腔节段，HMC-QU 顺序）、z / 节段提示 / 整段打分、右心最大 4 连通块 + 位置检查。
- `src/job_echo.c`：mode 2 参数第 688 字节起、mode 3 第 800 字节起可带 176 字节 "HC01"；`kfe_finish` 收尾时 `heart_json` 输出 "wall_motion" / "right_heart"，带 F_MASKS 时多一个 "rh_masks" 块（每拍 3 张：RV 舒张末、RV 收缩末、RA 收缩末）。不带 HC01 时与 v5.23 完全相同。右心每拍 3 次 PL（约 66 ms），最多 6 拍。
- 上位机：`jobs.py`（`load_heart_config` / `heart_block` / `unpack_rh`，`echo_ef` / `echo_live` 加 `heart=`）；`echo_heart_config.json`（`E:/PLD/wallmotion/make_heart_config.py` 生成）；`echo_ws.py`（`heart_text`、`add_contour`、`draw_wm_glyph`、报告"全心"一节、`screen_to_112`：非 112 的录屏按扇区取方块缩到 112，与 HMC-QU 评估缓存逐帧相同；轮廓改成完整内边界，原来右 / 下边是虚线）；`echowin.py`（板端报 "heart" 时关键帧 / 现场模式自动带 HC01；左栏"全心"卡片；右心紫 / 橙轮廓；右上角 6 节段示意；HMC-QU 标注作对照行）。
- 方法与依据：`E:/PLD/wallmotion/segmental_wall_motion_hmc-qu_2026-10-25.md`、`E:/PLD/cardiac_rv/right_heart_rv_ra_2026-10-23.md` 第 7 节。

**验收**（证据 `E:/PLD/board_acceptance_v524/`）：
| 项 | 结果 |
| --- | --- |
| 候选 devrun：`verify_board_echo_heart.py`（EchoNet VAL 20 + HMC-QU DEV 20 + mode 3 == mode 2 + 不带 HC01） | **`ECHO_HEART_BOARD_PASS` 42 / 42**，全心字段与 rh_masks 逐位 = 主机参考 `keyframe/heart_ref.py` |
| 候选：mode 2 `--videos 20` / mode 0·1 / mode 3 | **40 / 40**、PASS、**10 / 10** |
| 候选：窗口 `_verify_echowin_heart.py --board`、`_verify_echowin_kf.py --board`、`_verify_echowin_live.py --board` | 全 PASS（全心 == 主机参考逐位） |
| 候选：27 组全量回归 `board_acceptance_v524/regression.py`（由 v5.20 复制，只改 SERVER / strain --server；结尾标记名仍是 `REGRESSION_V520_PASS`） | **27 / 27 一次通过** |
| 启用（10-25） | `activate` **`V5_ACTIVATE_PASS`**；复查：全心 `--videos 5 --hmc 5` PASS、mode 2 6 / 6、mode 0·1 PASS、mode 3 10 / 10（延迟中位数 28.8–29.5 ms、P99 67–70 ms）、窗口全心 `--board` PASS；证据 `*_post_activate.*` |

**测试集（预注册 `E:/PLD/final_test_2026-10-25/prereg.json` 后各评一次，汇总 `final_test_results_2026-10-25.md`）**：
- EchoNet TEST 1,277 段：Dice 0.908；关键帧档（PL 17.9 % 帧）EF MAE 5.39（每帧 5.44）；80 / 90 % 区间覆盖 0.80 / 0.89；EF ≤ 40 AUC 0.951，HFrEF 98.1 % 判出或标复核（静默漏判 3 / 160）。
- HMC-QU TEST 65 段：节段 AUC 0.78，整段 AUC 0.90（单用 EF 也是 0.90）。
- CardiacUDA TEST：右室 Dice 0.776（VAL 0.822），右房 0.877。
- CAMUS 1.6 test：左房容积 MAE 11.0 mL，左室质量 MAE 24.8 g。

**没做**：采集卡实测（V4L2、屏到屏 F1）、DP 显示、功率计实测（用户说先不做采集卡和 DP）。

## 9.83 服务端 v5.25：现场模式滚动质量灯 + 切面提示 "RQ01"（2026-10-31；先做候选版，随后用户要求"直接上板，明天我要演示"——**已启用**，回滚 = v5.24 `75c294e9`；固件 f150 不变）

- 详细说明见 `E:/PLD/board_acceptance_v525/README_v525候选版.md`。
- 候选版 `releases/v5_20261031T105102Z`，sha256 `4c18f497…`；部署记录在 `board_acceptance_v525/deployment_candidate_v525.json`。`board_check_v5/deployment.json` 已恢复为 v5.24 的 active 记录，防止误启用。
- `src/job_echo.c` 新增可选 RQ01 块：
  - 滚动质量灯：每 5 帧算最近 50 帧，放在 worker 线程，nice +5。
  - 切面提示：每 25 帧在 PL 上跑一次右室程序 21，取 3 次中位数，≥ 0.27 判为四腔。
  - 不带 RQ01 时与 v5.24 相同。
- `src/job.c` 的 caps 改为 `v5.25` 并加 `live_roll`。**src/ 工作区现在是 v5.25。**
- 真板 devrun：
  - `host_check` 逐位一致：滚动灯 101 次、右室面积 35 次。
  - 延迟 P99 70.2 ms（不带 RQ01 时 68.7 ms）。
  - jobs、kf、heart、live 回归全部通过。live 脚本读 ES000108 源会报错，是脚本老问题。
  - 测完已切回 v5.24：POST_STOP_PASS。
- 局限：HMC-QU 两腔段切面只判对 2/4；质量灯只对 EchoNet 这类画面标定过。界面（PC / DP）还没接，启用时要一起做。
- **10-31 稍后启用**：V5_ACTIVATE_PASS（`4c18f497`，Nice -20），启用后复查 HOST_CHECK_PASS（演示源 P99 67.9 ms）。界面已接：PC `jobs.py`（`roll_block` / `ROLL`，`unpack_live` 多 roll_light / view）+ `heartview.py`（caps 有 `live_roll` 才发 RQ01；扫查中图像质量灯和"扫查中 / 切面"一行实时更新，结束后结果卡片多一行扫查过程统计），`_verify_heartapp.py` HEARTAPP_PASS（新增检查项）；DP 端 `board_station/app_merge/jobs.py`（板上原 jobs.py / heartview.py 备份 *.bak_v525），`verify_heart_station.py` 新增同样检查。演示清单第 2 步、问答、回滚命令已更新。
- **最终参数**：界面发 `jobs.ROLL` = win 50 / **every 10**（0.2 s）/ view every 25 / 中位数 3 / 0.27。every 5 时板上 DP 自检现场推荐档 P99 83 ms（DP 界面和质量线程抢 CPU），改 10 后 DP **HEART_STATION_PASS**（P99 71 ms，扫查灯 / 切面检查通过），PC **HEARTAPP_PASS**（P99 70 ms）。DP 工作站已重启（station_heart.py --demo）。板上服务不用改（参数由 PC / DP 发）。
- **10-31 深夜（演示前）**：① 屏幕标尺自动定标：`heartview.depth_ticks` 读录屏上的黄色深度刻度（1 cm 短线 / 5 cm 长线，只用 numpy / scipy），「送到快照测量」时自动定标、不弹框（读不出才弹框手点；「标尺定标…」按钮打开的框里也已自动放好两点）。HMC-QU 292 段首帧 236 段读出，ES000108 自动 27.80 像素/cm（手点 27.88），112 尺度仍是 1.23 mm/像素。② 板子 DP 上不开扫查滚动灯（DP 界面自己占 CPU，开了 P99 在 71–83 ms 浮动）；PC 上照开。DP 自检 HEART_STATION_PASS（P99 71 ms，自动定标 OK），DP 工作站已重启。③ 演示文档：`E:/PLD/demo_20261101_v525/现场验收演示文档_2026-11-01.docx / .pdf`（29 页，v5.25 真板截图，含 EchoNet 画面只在本地）。
- **10-31 深夜（接上 2560×1440 显示器后）DP 排版乱 + 很卡，兼容层三个 bug（shim_patch/，部署脚本 SHIM_PATCH 多了 `_gtkbase.py`，板上原文件备份 *.bak_lazy）**：
  ① `_gtkbase.py` pack：`"x" in fill` 判断填充，`fill="both"` 里没有 x / y → 当成不填，多行文本框（Text width=1）缩成 8 像素宽居中的一窄列 → 改成 `fill in ("x", "both")`；
  ② `_widgets.py` size-allocate → `<Configure>`：GTK 回调是 (widget, allocation)，原来取 rest[0] → 宽高永远 0 → 折行标签宽度被设成最小值；改取 event；
  ③ 同处：GTK 每次重新分配都发 size-allocate，原样转成 `<Configure>` → 画布重画 → 重新分配 → 延迟图再画 … 空闲时主线程 100 %（采样：全在 `_canvas._render` / `ImageDraw.text`）、Xorg 77 % → 只在位置 / 尺寸变了才发（tk 语义）。Label wraplength 同值不再重设。
  修后空闲 CPU python 1.3 %、Xorg ≈ 0；排版恢复。
- **10-31 深夜（续）DP 现场模式卡**：采样确认主线程 100 % 在 `_goto` 重画视频（112 → 448 放大 + 叠轮廓，A53 上几十 ms），且兼容层 `Scale.set()` 回调 command → 每帧画两遍；界面跟不上 50 fps，画面越积越落后。改 heartview：`_goto` 自己设进度条时忽略回调（`_scale_busy`，PC 也受益）；现场画面刷新间隔 `RENDER_GAP` = 板端兼容层 0.12 s / PC 0.035 s（数据仍每帧都收）。修后跑完时界面不再积压，运行中主线程约 3/4 空闲。
- **10-31 深夜（续）DP 快照模式窗口 3306 像素宽（屏 2560）**：圆盘图 `lab_fig` 是图片标签，`configure(image=…, width=228)`；兼容层把 width 按"字符"设到隐藏的文字部分（228 × 8 = 1824 px），之后某次 pack() 的 show_all() 又把它显出来 → 撑宽窗口。shim `_widgets.py` Label：图片模式不再 set_width_chars，藏文字时 set_no_show_all(True)。修后窗口 2560、卡片正常。


## 9.84 服务端 v5.26：CAMUS 口径的现场质量灯 + 切面（按信号源）+ 判定后保持（2026-11-02；用户要求做进去并选“按信号源区分”，随后“先判断好坏，后续不动就不用继续判断”——**已启用、已复查**，回滚 = v5.25 `4c18f497`；固件 f150 不变）

- 详细说明：`E:/PLD/quality/live_camus/camus_live_quality_light_v5.26_2026-11-02.md`（设计、数字、局限、文件）；验收 `E:/PLD/board_acceptance_v526`。
- 背景：F1 分不好 EchoNet 画面（Dice 0.47，灯与 EF 误差相关 0.05），程序 16 的灯换到 CAMUS 失效 → 两个口径只按信号源用：`live/src/index.json` 里 `"quality": "camus"` 的源（`CAMUS_scan_demo_z3.y8`，154 MB 在 `/data/echo_src_camus/`，`/root/echo_src/` 里是符号链接）走 CAMUS 口径，其余照旧。
- 板端：RQ01 字节 24.. = chain 1 / f1_every 5 / f1_model 2，后接 248 字节 CQ01（模型；字节 4 = 保持门限 ×1000 = 5258）。F1 每 5 帧一次（PL），右室程序推迟到 F1 帧后至少 2 帧的跟踪帧（P99 97 → 75 ms）；`eq_window` / `eq_clean` / `lc_crop_resize_n` 与主机 DLL 同一份 C。保持：最近 3 次灯色 + 2 次切面一致即锁定，停 F1 / 右室；1 s 平均画面变化 > 5.258 灰度级 = 探头动了，重判。caps `server v5.26`、`live_roll_camus`。
- 验证集（测试集没碰）：Poor AUC 0.911，切面 0.91；保持误触发 0/100、换切面 / 换病人检出 100%（约 0.3 s）。
- 启用（release v5_20261102T153944Z，sha `c88bac6f`）：V5_ACTIVATE_PASS；启用后 6 组现场模式 HOST_CHECK_PASS（老链路与 v5.25 完全一致；CAMUS 源锁定 72 / 194 / 352、解锁 117 / 217，P99 76 ms）。启用前 PC HEARTAPP_PASS 30/30、DP HEART_STATION_PASS 25/25；精简回归 13/14（fundus_cdr 断连一次，与演示无关，用户说不跑无关演示的，未复跑）。
- 界面：CAMUS 源自动固定方块、灯旁标“CAMUS 专家等级标定”、不请求全心、不给提示方向、报告用 CAMUS 口径的灯；状态“已判定，探头未动，暂停判断”/“探头移动了，重新判断中”。DP 工作站已重启（备份 `/data/board_station/app_heart.bak_v526pre`、`board.bak_v526pre`）。
- 回滚：`deploy_board_v5.py rollback`（v5.25）；DP 还原两个备份目录后 `deploy_board_station.py switch` 重启。
- **11-02 稍后（用户要求模拟医生找切面）**：新源 `CAMUS_view_search_z3.y8`（同一病人 patient0255：两腔 → 切到四腔但没放好（合成：侧移 + 降对比度）→ 慢慢调好 → 到位不动；`quality/live_camus/sim_search.py`、`degrade_test.py`）。板端小改：CAMUS 口径切面攒满 3 个比值才给结论（VAL 0.910，门限不变）→ **启用 v5.26 `2b0bc6a3`**（release v5_20261102T164020Z，回滚 → `c88bac6f`）；启用后 7 组 HOST_CHECK_PASS，找切面源 P99 75 ms。演示截图 `E:/PLD/demo_20261102_v526`（33 张）。
- **11-03（用户选 B）CAMUS 源的现场引擎关键帧改用 F1**（RQ01 chain 2，caps `live_f1_engine`）：F1 跑 128 原图，logits 最近邻缩到 112（`eq_f1_to112`，板上与主机 DLL 同一份），后面清理 / 跟踪 / 容积不变；EF 用 CAMUS TRAIN 校准 a 0.761 b 5.68（PC 换 KF01 的 a / b）。验证集 EF MAE 6.1（程序 16 链 7.6），心率对上 92%（88%）。**启用 v5.26 `9851f6fe`**（release v5_20261103T033243Z，回滚 → `2b0bc6a3`）；启用后 8 组 HOST_CHECK_PASS、DP HEART_STATION_PASS、PC HEARTAPP_PASS 30/30。界面：分析片段滚动条（全段 / 放好后 / 2–5 s，CAMUS 源默认放好后）；CAMUS 源不标逐拍 EF、不给 EchoNet 校准的 80% 区间；修了 `uistyle.text_box` 滚动条显隐导致的界面上下跳。

## 9.85 固件 f150nv：去掉 vis_layer 的资源精简版（2026-11-03；用户要求“优化板子资源，主线外可删，保留眼底血管”并同意拆 vis_layer；**已构建、未上板**——板子离线；当前板上仍是 f150）
- 依据：conv / eltwise / 张量 BRAM / pp_layer 心脏和眼底都要用；img_proc 行缓冲后只剩约 660 LUT / 1.5 BRAM（不拆）；vis_layer（PL 配准引擎 + 叠加）心脏主线完全不用，只有 JOB_OVERLAY、JOB_REG_SEARCH engine 1、眼底分块叠加用它，三者没有 vis 时都走 PS（同一规格逐位相同）。删程序（皮肤 / CT / 算子 / CDR）不省 PL：程序只是权重。
- 构建：`ai/bd/build_ai_sys_f_novis.tcl` / `run_build_f_novis.bat 150.0 f150nv p6757`（= build_ai_sys_f_clk.tcl 去掉 vis_layer、ctl_xbar M04、ai_data_ic S09；f150 原脚本未动）。产物 `bitstream/ai_sys_f150nv.bin` sha256 `a4f82bf89031a11159edf310ad3209e11f42d3a151ab4b37b86fd9184e0dbf95`，日志 vivado/ai_sys_f150nv_build.log，报告 vivado/ai_sys_f150nv_{util,util_hier,timing}.rpt。
- 资源（f150 → f150nv）：LUT 59 801（84.75%）→ **53 203（75.40%）**；BRAM 172（79.6%）→ **136.5（63.2%）**；DSP 306 → **290**；FF 55 911 → 48 045。
- 时序：标称 150 MHz WNS +0.574 / WHS +0.015；**按板上实际 pl0_ref 101.376 MHz（MMCM 输出 152.15 MHz）复核 WNS +0.486 / WHS +0.015**（vivado/f_clock/retime_f_real.tcl 现在可 `-tclargs f150nv`，日志 retime_f150nv.log；f150 当时 +0.470）。
- 服务端不用改：v5.26 只在固件 sha256 列在 /lib/firmware/ai_sys_vis.sha256 时才访问 0xA004_0000。部署时新 sha **只**追加进 ai_sys_pp.sha256，**绝不能**写进 ai_sys_vis.sha256（没有从机，访问即 PS 挂死）。
- 验收脚本 `E:/PLD/board_acceptance_nv/`（由 board_acceptance_f 派生）：`acceptance.py baseline`（f150 上：备份 .before_nv、工具从 v5.26 release 源码编译、strict + profile、BoardCheck、pp 向量、TCP）→ `acceptance.py deploy_tests`（装新固件重启；门槛 = 日志有 “registration / overlay off: firmware not listed” 且无 “vis_layer ready”、pp_layer ready、0x494D5004；BoardCheck / strict / pp / TCP；regression.py = v5.24 的组去掉 v516，SERVER v5.26；最后 v5.26 心脏现场 run_board.py + host_check.py；任一步失败自动回滚 f150 并重启）。用 skin\.venv python 运行（paramiko）。
- 回滚：/lib/firmware/ai_sys.bin.before_nv（= f150）拷回 + 重启。
