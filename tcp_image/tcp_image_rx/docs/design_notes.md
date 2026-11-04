# 网口 TCP 图像接收 → DDR 设计说明

本工程实现：**上位机(PC)经千兆网口用 TCP 发一张 512×512 的 RGB888 图像，
FZ3B 开发板收到后存进 DDR，并可由 AXI DMA 把图像以 AXI4-Stream 推给 PL。**

> ⚠ **本文是"网口收图"那一层的基础设计，描述的是 2026-09-29 的形态**
> （只有 MM2S、DDR 窗口 1 MiB、`frame_status` 32 B、板端 CLI 只有 `--no-dma` 那一套）。
> **2026-10-01 起板端多了 PL 图像处理往返**：DDR 窗口扩到 4 MiB、状态块扩到 64 B、
> 新增 `--gpio-addr` / `--thr`、DMA 变成 MM2S+S2MM 往返、`--echo` 回传的是**处理后的图**。
> 这些增量的权威描述在 **`image_processing_design.md`**；本文 4.1 / 4.3 两节的表已按新值更新，
> 其余（协议、DMA 寄存器、排查）仍然适用。

---

## 1. 目标与硬件前提

| 项目 | 内容 |
| --- | --- |
| 开发板 | FZ3B，主芯片 `XAZU3EG-1SFVC784I`（Zynq UltraScale+ MPSoC EG） |
| PS | 4×Cortex-A53 + 2×Cortex-R5，4 GB DDR4（4 片 MT40A512M16LY，64 bit） |
| 网口 | 千兆以太网，PHY = KSZ9031RNX，**接在 PS 的 BANK502 MIO64..77（RGMII）** |
| PL 时钟 | 板上 `PL_REF_CLK` = 25 MHz（`D4`，BANK66，1.8 V） |
| 操作系统 | PetaLinux（Linux on PS） |
| 图像规格 | 512×512，RGB888（每像素 3 字节，序 R,G,B），共 786 432 字节 |

引脚与器件信息一律以工作区根目录 `hardware_info.txt` 为准（第九节网口、第三节 DDR4、
第十九节系统时钟）。

### 1.1 为什么 TCP 协议栈跑在 PS，而不是用 PL 写 RTL

板上网口 PHY 通过 **RGMII 直接连到 PS 的 MIO**，PL 侧根本拿不到 RGMII 信号。
要让 PL 自己收以太网帧，必须改硬件走线（或把 GEM 引到 EMIO 并另配 PHY），
在本板上不可行。因此：

- TCP 协议栈由 **PS 的 Cortex-A53 上的 Linux 内核**提供；
- 我们的程序只是一个**用户态 socket 服务端**，负责收数据、校验、搬进 DDR；
- 只有在"把 DDR 里的图像交给 PL"这一步才用到 PL（AXI DMA + AXI-Stream）。

这样也最省事：不用自己写 TCP 状态机，可靠传输、重传、流控全由内核负责。

---

## 2. 总体架构

```
  ┌──────────────┐   千兆以太网        ┌──────────────────────── FZ3B ───────────────────────┐
  │   PC 上位机   │  TCP, 512 个行包    │  ┌──────────── PS ────────────┐                     │
  │              │ ─────────────────▶ │  │ KSZ9031(RGMII) → GEM3       │                     │
  │ send_image.py│                     │  │   ↓ Linux 内核 TCP 协议栈    │                     │
  │              │                     │  │   ↓                          │                     │
  └──────────────┘                     │  │ tcp_image_server(用户态)     │                     │
                                       │  │   · 逐包校验 CRC16           │                     │
                                       │  │   · 写入 DDR 保留区          │                     │
                                       │  └──────────┬──────────────────┘                     │
                                       │             │ /dev/mem mmap(非缓存)                   │
                                       │             ▼                                        │
                                       │      ┌────────────────────┐                          │
                                       │      │  DDR 保留区 4 MiB   │  0x4000_0000            │
                                       │      │  · 输入图 32bit/px  │                          │
                                       │      │  · 输出图 32bit/px  │                          │
                                       │      │  · frame_status 64B │                          │
                                       │      └─────────┬──────────┘                          │
                                       │                │ AXI DMA MM2S 读 DDR                  │
                                       │                ▼                                     │
                                       │      ┌────────────────────┐                          │
                                       │      │  AXI DMA (MM2S)     │                          │
                                       │      └─────────┬──────────┘                          │
                                       │                │ AXI4-Stream(M_AXIS_MM2S)            │
                                       │                ▼                                     │
                                       │         PL 侧自定义逻辑(用户后续添加)                  │
                                       └──────────────────────────────────────────────────────┘
```

三条独立的通路：

1. **控制通路**：PS 主口 `M_AXI_HPM0_LPD` → SmartConnect → AXI DMA 的 `S_AXI_LITE`，
   用户程序经此写 DMA 寄存器。
2. **数据通路**：AXI DMA 的 `M_AXI_MM2S` → PS 从口 `S_AXI_HPC0_FPD`，DMA 经此读 DDR。
3. **流输出**：AXI DMA 的 `M_AXIS_MM2S` 导出成 PL 的外部接口，交给后续逻辑。

---

## 3. 传输协议

图像被切成 **512 个"行包"**，一个包正好一行像素（512 像素 × 3 字节 = 1536 字节）。
按 TCP 字节流依次发送，接收端按固定长度逐包解析。

### 3.1 行包格式（全部小端序）

| 偏移 | 长度 | 字段 | 说明 |
| --- | --- | --- | --- |
| 0 | 2 | `row` | 行号，0 .. 511 |
| 2 | 2 | `length` | 本包负载长度，固定 **1536** |
| 4 | 2 | `crc16` | CRC-16/CCITT-FALSE，只对后面的 1536 字节负载计算 |
| 6 | 1536 | `payload` | 该行 512 个像素，顺序 R,G,B |

- 单包总长 **1542** 字节（6 + 1536）。
- 整幅共 **512 × 1542 = 789 504** 字节。
- 整幅像素 **512 × 1536 = 786 432** 字节。

### 3.2 CRC-16/CCITT-FALSE 参数

| 参数 | 值 |
| --- | --- |
| 多项式 poly | `0x1021`（x¹⁶+x¹²+x⁵+1） |
| 初值 init | `0xFFFF` |
| 输入反转 refin | false |
| 输出反转 refout | false |
| 异或输出 xorout | `0x0000` |
| **标准校验值** | 对 `"123456789"` 计算 = **`0x29B1`** |

C 端实现在 `src/crc16.c`，Python 端在 `script/send_image.py`，两边都是 MSB-first
逐位实现，必须逐字一致。程序与脚本都内置了这条向量的自检。

### 3.3 为什么要行号

`row` 写在包头里，于是接收端**天然容忍 TCP 的粘包、拆包和行乱序**：不管数据怎么
分片，只要按 `row` 把 1536 字节落到 `图像基址 + row × 1536` 就是对的。
这也让"逐包校验"能定位到具体哪一行坏了。

### 3.4 失步的处理

包头的 `row` 或 `length` 一旦越界，说明字节流已经错位；由于长度字段本身已不可信，
**无法重新同步**，程序会打印 `desync` 并放弃本次连接，由上位机重连重发整幅。
（这也是协议刻意的简化：不做重传、不做断点续传。）

---

## 4. DDR 内存映射

### 4.1 保留窗口

| 项 | 值 |
| --- | --- |
| 物理基址（默认） | `0x4000_0000`（板端 `-a`；厂商设备树里预留的 1 GB `no-map` 区） |
| 窗口大小 | 4 MiB (`0x0040_0000`) |
| 预留方式 | 设备树 `reserved-memory` 的 **`no-map`** 节点 |

窗口内部布局（与 `src/ddr_layout.h` 一一对应）：

| 偏移 | 大小 | 内容 |
| --- | --- | --- |
| `0x0000_0000` | 1 MiB (`0x100000`) | 输入图区，**32bit/像素** `{8'h00,R,G,B}` |
| `0x0010_0000` | 1 MiB | 输出图区（PL 处理结果，32bit/像素） |
| `0x0020_0000` | 64 B | `frame_status` 状态块 |
| 其余 | — | 保留未用 |

> 早期版本是"1 MiB 窗口、图像区偏移 0、状态块偏移 `0xC0000`"。加了 PL 处理后
> 输入/输出两张 32bit/像素 的图各占 1 MiB，于是窗口扩到 4 MiB。
> 设备树那边**不用改** —— 厂商在 `0x4000_0000` 留的本来就是 1 GB。

### 4.2 为什么必须 `no-map`

这是本设计里最容易踩、也最难查的一个点：

- 用 `no-map` 时，内核在启动早期就把这块区域从**线性映射**和 **memblock** 中摘掉。
  于是 ARM64 的 `/dev/mem`（走 `phys_mem_access_prot()`）对它返回 `pgprot_noncached()`
  —— 用户态拿到的是**非缓存（Device）映射**，CPU 写完像素立刻落到 DDR，
  随后的 AXI DMA 读到的必然是最新数据，**不需要手动刷 cache**。
- 若不加 `no-map`，这块区域在内核眼里仍是普通 RAM，`/dev/mem` 会给出**可缓存 /
  写合并**映射，DMA 就可能读到旧数据。现象是"图像整体偏移若干行"或"首帧是旧的"，
  极难定位。

同理，`open("/dev/mem")` 时带 `O_SYNC`。

### 4.3 状态块 `frame_status`（64 字节）

| 偏移 | 字段 | 说明 |
| --- | --- | --- |
| +0 | `magic` | 帧就绪标志，**最后写**。`FRAME_MAGIC = 0x314D5246`，小端存放即 ASCII `"FRM1"` |
| +4 | `frame_count` | 累计成功收满的帧数，从 1 开始 |
| +8 | `rows_ok` | 本帧 CRC 正确的行数（正常 512） |
| +12 | `crc_errors` | 本帧 CRC 失败的行数 |
| +16 | `seq_errors` | 本帧行号越界 / 长度不符的包数 |
| +20 | `bytes_recv` | 本帧实际从 TCP 收到的字节数（正常 789 504） |
| +24 | `frame_crc16` | 整幅图像的 CRC16（按行 0→511 累加，仅 `-v` 时算） |
| +28 | `dma_status` | MM2S 结束后 `MM2S_DMASR` 的快照 |
| +32 | `s2mm_status` | S2MM 结束后 `S2MM_DMASR` 的快照（2026-10-01 新增） |
| +36 | `mode` | 本帧控制头里的处理模式 0..4（2026-10-01 新增） |
| +40 | `proc_min` | PL 统计的本帧最小灰度（2026-10-01 新增） |
| +44 | `proc_max` | PL 统计的本帧最大灰度（2026-10-01 新增） |
| +48 | `resv[4]` | 保留 16 B，凑满 64 B |

> 早期版本是 32 字节（到 `dma_status` 为止）。`IMG_STAT_BYTES` 与
> `self_test()` 里的 `sizeof(struct frame_status)` 断言会一起保证不再漂。

**握手约定**：接收开始时先把 `magic` 清 0，整幅收完、DMA 往返结束后才写入
`FRAME_MAGIC`。写状态块时**其余字段先写、`magic` 最后写**（`publish_status()`
按字顺序写，`dst[0]` 放最后），所以读端一看到 `FRAME_MAGIC` 就能确信别的字段已可见。
读端（PL 或另一个进程）轮询 `magic` 从 0 变 `FRAME_MAGIC` 即表示"新帧且 DMA 已完成"，
再读 `frame_count` 判断是不是新的一帧。

### 4.4 重要约束：`recv()` 不能直接写进 `/dev/mem` 映射区

**这是在真机上踩出来的**（2026-09-29，FZ3B 实测，内核 4.14.0-xilinx）：

`/dev/mem` 的映射带 `VM_PFNMAP | VM_IO` 标志。**用户态自己读写这块内存完全正常**
（`mmap` 之后直接 `memcpy` / load / store 都行，已实测），
但**让内核往里面拷数据不行**：

```
[rx] payload read failed on row 0 (recv_rc=-1 errno=14 Bad address)
```

`recv()` 直接返回 **`-1 / EFAULT`（errno 14）**。所以"把 `recv()` 的目标直接指向保留区"
这种写法必然失败——现象是刚连上就报错，PC 端看到连接被重置。

**反方向同样不行，而且后果更严重**：`send()` 会从用户缓冲读数据（`copy_from_user`），
**从 `/dev/mem` 映射里发送会让内核直接 oops，进程一起死**（2026-09-29 用 `--echo`
实测，内核 dmesg 里能看到寄存器现场，`x19` 正是那块映射的基址）。

**结论：内核经手的数据永远走普通内存。**

| 操作 | 能不能直接对着 `/dev/mem` 映射做 |
| --- | --- |
| 用户态 load / store / `memcpy` | ✅ 可以 |
| `recv()`（内核写进去） | ❌ EFAULT |
| `send()`（内核读出来） | ❌ 内核 oops |

**解决办法：两级缓冲。** 先 `recv` 到一块普通（匿名）内存，收满一帧后再用 `memcpy`
把 786 432 字节搬到 `/dev/mem` 映射的目的地；要回发（`--echo`）也从**那块暂存区**发，
不从映射区发。状态块同理，用逐字节 store 发布（`publish_status()`），
而不是让内核去写。代价是每帧多一次 786 KB 拷贝，实测整帧 671 ms，这点开销可以忽略。

**解决办法：两级缓冲。** 先 `recv` 到一块普通（匿名）内存，收满一帧后再用 `memcpy`
把 786 432 字节搬到 `/dev/mem` 映射的目的地；状态块同理，用逐字节 store 发布
（`publish_status()`），而不是让内核去写。代价是每帧多一次 786 KB 拷贝，
实测整帧 671 ms，这点开销可以忽略。

> 同一个坑还影响**验证手段**：`dd if=/dev/mem` / `read()` 在这类地址上同样返回 EFAULT，
> 所以**读回校验必须用 `mmap`，不能用 `dd`**。

---

## 5. AXI DMA（MM2S）时序与寄存器

只用到 **MM2S（Memory-Mapped to Stream）**，把 DDR 图像推到 PL 的 AXI4-Stream。
simple 模式、非 SG、无中断（轮询）。

### 5.1 寄存器（取自 PG021，AXI DMA v7.1）

| 偏移 | 名称 | 说明 |
| --- | --- | --- |
| `0x00` | `MM2S_DMACR` | 控制：bit0 `RS`(运行)、bit2 `Reset`、bit12 `IOC_IrqEn` |
| `0x04` | `MM2S_DMASR` | 状态（写 1 清零）：bit0 `Halted`、bit1 `Idle`、bit4/5/6 错误、bit12 `IOC_Irq` |
| `0x18` | `MM2S_SA` | 源地址低 32 位 |
| `0x1C` | `MM2S_SA_MSB` | 源地址高 32 位（地址宽度 > 32 时才有；本设计为 32 位，不用） |
| `0x28` | `MM2S_LENGTH` | 传输长度，**必须最后写**，写入即启动 |

### 5.2 启动一次传输的顺序

1. 读 `DMASR`，若有错误位先判断/上报；
2. 往 `DMASR` 写 `W1C` 掩码，清掉历史状态位；
3. 写 `DMACR`：置 `RS=1`、`IOC_IrqEn=1`（轮询依赖 IOC 置位，所以必须使能它）；
4. 写 `MM2S_SA` = 图像区物理地址；
5. 内存屏障 `__sync_synchronize()`；
6. **写 `MM2S_LENGTH` = 786 432** → 传输开始；
7. 轮询 `DMASR`：出现错误位 → 失败；`IOC_Irq` 置位 → 完成（写 1 清掉）。

786 432 = `0xC0000`，远小于 `LENGTH` 字段上限（26 位 → 64 MiB−1），一次传完，
不需要拆多次。

### 5.3 AXI-Stream 上的数据排布（重要）

AXI DMA 只是**按字节搬运**，不做像素重组。本设计 `M_AXIS_MM2S` 的 `tdata` 宽度取
**32 位**，也就是说：

> 图像的第 `b` 个字节会出现在第 `(b >> 2)` 个 beat 的第 `((b & 3) * 8)` 位起的位置
> （小端）。一个像素的 R,G,B 三个字节可能**跨 beat 边界**。

所以 **PL 侧消费端要自己做字节→像素的重组**。如果 PL 侧希望"一个 beat 一个像素"，
有两条路：把 `tdata` 改成 24 位（`c_m_axis_mm2s_tdata_width=24`，AXI4-Stream 允许；
但要确认你的互联 IP 也接受非 2 的幂宽度），或者在 PL 里先用小 FIFO 把字节流拼成像素。
`bd/create_axi_dma_stream.tcl` 顶部的 `mm2s_tdata_w` 就是留给这个选择的参数。

> 另外：**PL 侧消费端必须拉高 `tready`**，否则 DMA 发不出数据、`MM2S_DMASR` 会一直
> 处于 busy，软件侧表现为 `wait_done` 超时（打印 `mm2s FAILED rc=-1`）。
> 先跑通软件时可以用 `--no-dma` 跳过这一步。

---

## 6. 软件流程

`src/tcp_image_server.c` 的主流程：

```
开机自检 self_test()
   └─ 用 "123456789" 验证 CRC16 == 0x29B1，校验 sizeof(frame_status) == 32
open("/dev/mem", O_RDWR|O_SYNC) → mmap 保留区(1 MiB) → 得到 img_buf / st
[若启用 DMA] mmap DMA 寄存器 → axidma_init() → axidma_reset()
socket/bind/listen(0.0.0.0:port)
loop:
    accept 一个客户端
    receive_frame():
        magic = 0                                    ← 通知读端"正在收"
        循环 512 次:
            recv_exact(6 字节包头)
            校验 row/length；越界 → desync，放弃本连接
            recv_exact(1536 字节) → 直接读进 图像基址 + row*1536
            CRC16 校验；错 → crc_errors++，该行不计入 rows_ok
        统计 rows_ok / bytes_recv 写入状态块
        若 rows_ok != 512 → 报错返回，不推 DMA
        逐行累加整幅 CRC16 → frame_crc16
    [若启用 DMA] axidma_start_mm2s(ddr_base, 786432) → axidma_wait_done()
    magic = FRAME_MAGIC                              ← 通知读端"整帧就绪"
```

关键点：

- **`recv_exact()`**：TCP 是字节流，一次 `recv()` 不保证读满，必须循环读；
  同时处理 `EINTR`（信号打断）。
- **数据直接读进目标位置**：`recv_exact(cfd, img_buf + row*1536, 1536)`，
  省掉一次 786 KB 的 `memcpy`。
- **CRC 失败的行仍留在缓冲区里**，但 `rows_ok` 不增加；整幅不全就不推 DMA，
  避免把半张图送给 PL。
- 控制台日志一律英文；中文只在注释与文档里（串口/日志被抓取后中文可能变乱码）。

---

## 7. 工程搭建步骤

### 7.1 Vivado：建含 AXI DMA 的硬件平台

1. 建 Vivado 工程，器件选 `xazu3eg-sfvc784-1-i`（无 board_part，只用 part）。
2. 建 Block Design，加入 `zynq_ultra_ps_e`，**按 FZ3B 的板级预设配置 PS**：
   DDR4（MT40A512M16LY）、GEM3 走 MIO64..77、SD、UART1 等。
   > ⚠ 这部分 DDR4 参数必须来自厂商板级文件或已跑通的工程，本工程**不提供**
   > 也**不臆造** DDR4 配置。
3. 在 PS 上使能：PL 时钟 `pl_clk0`(FCLK0)、PL 复位 `pl_resetn0`、
   至少一个 PL→PS 从口（`S_AXI_HPC0_FPD`）、至少一个 PS→PL 主口（`M_AXI_HPM0_LPD`）。
4. 打开工程后，在 Tcl Console 里 source 本工程的脚本：

   ```tcl
   source E:/fpga/my_project/tcp_image_rx/bd/create_axi_dma_stream.tcl
   ```

   它会加入 AXI DMA(MM2S, simple) + SmartConnect + proc_sys_reset，连好时钟/复位，
   把 `M_AXIS_MM2S` 导出为外部接口，并打印 **AXI DMA 的寄存器基址**
   （程序里用 `--dma-addr` 传入）。脚本注释是 ASCII 的（见 7.5）。
5. Validate → Save → 导出 XSA（把脚本顶部 `export_xsa` 置 1，或用 GUI 导出）。

### 7.2 PetaLinux：建工程并接入硬件

```bash
petalinux-create -t project --template zynqMP -n tcp_img
cd tcp_img
petalinux-config --get-hw-description=<含 XSA 的目录>   # 导入硬件
```

在 `petalinux-config` 里确认以太网使能（GEM3）、根文件系统包含我们的程序。

### 7.3 设备树：预留 DDR 窗口

把 `dts/zynqmp-image-reserved-memory.dtsi` include 进
`project-spec/meta-user/recipes-bsp/device-tree/files/system-user.dtsi`：

```dts
/include/ "zynqmp-image-reserved-memory.dtsi"
```

然后 `petalinux-build`。**务必确认生成了 `reserved-memory` 且带 `no-map`**
（原因见 4.2）。

### 7.4 编译用户程序

```bash
source <PetaLinux SDK>/environment-setup-aarch64-xilinx-linux
make -C E:/fpga/my_project/tcp_image_rx
```

产出 `tcp_image_server`（ARM64 Linux 可执行文件）。拷到板上：

```bash
scp tcp_image_server root@<板子IP>:/tmp/
```

> ⚠ Vitis 安装目录里的 aarch64 工具链通常是**裸机**版（`aarch64-none-elf`），
> 缺 Linux 的 libc 与系统头文件，**编不了本程序**，别拿它来编。

### 7.5 关于脚本注释用英语

`bd/create_axi_dma_stream.tcl` 的注释刻意全用 ASCII。原因是 Vivado 在中文
（cp936）Windows 下可能误读 Tcl 里的非 ASCII 字节。中文说明放在本文档里。

---

## 8. 上板运行

板上：

```bash
# 默认端口 5000，DDR 0x30000000，DMA 基址用脚本打印出来的那个
./tcp_image_server -p 5000 -a 0x30000000 -d 0x40400000 -v
```

PC：

```bash
python script/send_image.py --selftest                 # 先验 CRC 实现
python script/send_image.py -H <板子IP> -P 5000 --pattern bars -v
python script/send_image.py -H <板子IP> --in my.ppm --dump sent.rgb
```

板上正常输出形如：

```
[self] OK: CRC16 check value 0x29B1, image 512x512 RGB888, 786432 bytes/frame, 512 pkts x 1542 bytes
[tcp] listening on 0.0.0.0:5000, waiting for sender...
[tcp] client 192.168.1.100:53214 connected
[rx] frame done: rows_ok=512/512, crc_err=0, seq_err=0, bytes=789504
[rx] frame CRC16 = 0xXXXX
[ok ] frame 1 stored at 0x30000000, 78 ms, 80.9 Mbit/s
[dma] mm2s done: 786432 bytes streamed to PL
[dma] DMASR=0x00001002 [running idle simple IOC]
[ok ] status magic set, frame_count=1, frame_crc16=0xXXXX
```

---

## 9. 联调方法

1. **先验 CRC**：`python script/send_image.py --selftest` 必须打印 `OK`；
   板上启动时的 `[self] OK` 也必须出现。两边都对上 `0x29B1` 才有意义。
2. **先不接 DMA**：板上加 `--no-dma`，PC 发彩条图。看到 `rows_ok=512/512,
   crc_err=0` 就说明**网络 + 协议 + DDR 写入**这条链路通了。
3. **对图**：`--dump sent.rgb` 存下 PC 实际发的原始图；在板上读回同一区间，
   逐字节比对：

   ```bash
   dd if=/dev/mem bs=1 skip=$((0x30000000)) count=786432 2>/dev/null > got.rgb
   cmp sent.rgb got.rgb && echo "IDENTICAL"
   ```

4. **再开 DMA**：去掉 `--no-dma`。若出现 `mm2s FAILED rc=-1`（超时），
   绝大多数情况是 **PL 侧没拉 `tready`**（见 5.3）。
5. **PL 侧联调**：把 `M_AXIS_MM2S` 接到你的消费逻辑，先用 ILA 抓 `tvalid/tready/
   tdata/tlast`，确认每帧 786 432 字节收发完毕。

---

## 10. 故障排查

| 现象 | 可能原因 | 处理 |
| --- | --- | --- |
| `open /dev/mem: Permission denied` | 不是 root | 用 root 运行 |
| `mmap ddr ... : Operation not permitted` | `CONFIG_STRICT_DEVMEM` 拦了 | 确认设备树是 `no-map`（推荐）；仍不行则在 cmdline 加 `iomem=relaxed` |
| `bind ... : Address already in use` | 端口被占 | 换 `-p`，或等上一条连接超时（已设 `SO_REUSEADDR`） |
| `crc_err` 不为 0 | 网络丢/错包，或两端 CRC 实现不一致 | 先跑 `--selftest`；再查网线/交换机 |
| `desync at pkt N` | 两边包格式不一致（行数/长度/字段顺序） | 对照第 3 节核对 `send_image.py` |
| `rows_ok != 512` | PC 提前断开或发少了 | 看 PC 侧是否报异常 |
| 图像整体偏移若干行 | 保留区没加 `no-map`，DMA 读到旧数据 | 按 4.2 改设备树 |
| `mm2s FAILED rc=-1`（超时） | PL 侧没拉 `tready` | 见 5.3，先 `--no-dma` 排掉软件问题 |
| `mm2s` 报 `DMADecErr` | DMA 源地址非法或越界 | 核对 `--ddr-addr` 与 PS 从口地址范围 |
| `axidma_init failed rc=-3` | IP 被配成 SG 模式 | 重新生成 IP，`c_include_sg=0` |

---

## 11. 验证状态（如实说明）

本机 **Linux 沙箱不可用**（bash 工具起不来），但本机的 **MinGW-w64 gcc 6.2.0**、
**aarch64 Linux 交叉编译器（Vivado 自带，GCC 13.4.0）**、**Python 3.13.0** 与
**Vivado 2025.2.1 的 Tcl** 都被真的调起来跑了。下面严格区分"实测"与"未验证"。

### 11.1 已实测：编译与构建

| # | 验证项 | 手段 | 结果 |
| --- | --- | --- | --- |
| 1 | **ARM64 Linux 交叉编译** | `gnu/aarch64/nt/aarch64-linux/bin/aarch64-linux-gnu-gcc.exe`（GCC 13.4.0）编三个 .c | **0 warning、0 error**；产出 76112 字节 ELF，`e_machine=0xB7`(AArch64) |
| 2 | **`makefile` 真的跑通了** | `make clean` → `make CC=<上面的交叉编译器>` | 两次都正常退出；产出的就是上面那个 ELF |
| 3 | `crc16.c` / `axidma.c` 主机编译 | `gcc -std=c99 -Wall -Wextra` | **0 warning、0 error** |
| 4 | `tcp_image_server.c` 语法/类型 | 同上 + POSIX 垫片头（MinGW 缺 `netinet/`、`sys/mman.h`） | **0 warning、0 error** |
| 5 | `axidma_format_status()` 行为 | C 测试程序，含 8 字节极小缓冲区 | 输出可读；极小缓冲区正确 NUL 结尾、**不越界** |

> 第 2 项顺带抓到一个**真 bug**：`makefile` 原来用 `ifeq ($(OS),Windows_NT)` 判别平台，
> 而被 Vivado 直接拉起时 `OS` 环境变量是**空的**，于是误判成 Linux 分支，
> `mkdir -p build` 落到 cmd.exe 上直接报"子目录或文件 -p 已经存在"而失败。
> 已改成测 shell 语义本身：`ifeq ($(shell echo "test"),"test")`。

### 11.2 已实测：算法与协议

| # | 验证项 | 手段 | 结果 |
| --- | --- | --- | --- |
| 6 | CRC16 标准向量 | Tcl 独立实现 + 编译后的 C 各算一次 | 两边都得 `0x29B1` |
| 7 | **C 与 Python 的 CRC 是否一致** | 对同一段 1536 字节负载（公式 `(i*7+3)&0xFF`）分别算 | C = `0xF189`，Python = `0xF189`，**完全一致** |
| 8 | 一次性 vs 分段累加 API | C 测试程序 | 二者一致（整幅 CRC 就是逐行累加出来的） |
| 9 | 包头字节序与偏移 | Python `struct.unpack_from('<HHH')` | row@0 / len@2 / crc@4 全小端，负载自第 6 字节起 |
| 10 | 协议常量算术 | Tcl 实算 | 行 1536 / 图 786 432 / 包 1542 / 流 789 504，全部吻合 |
| 11 | 内存布局自洽 | Tcl 实算 | 状态区偏移 786 432 **正好**接在图像区之后（不重叠），且 +32 ≤ 1 MiB |
| 12 | DMA 能否一次传完 | Tcl 实算 | 786 432 ≤ `0x03FFFFFF`（LENGTH 26 位上限），单次即可 |
| 13 | 三张内置图样尺寸 / PPM 与裸 RGB 读回 | Python | 均为 786 432 字节；往返逐字节一致 |
| 14 | 交付脚本本身可运行 | `python script/send_image.py --selftest` | 输出 `CRC16("123456789") = 0x29B1 ... OK` |

### 11.3 已实测：接收路径的功能测试（最关键的一项）

`test/host_test_rx.c` 直接 `#include` 交付的 `src/tcp_image_server.c`，于是可以
**原样调用那份代码里 `static` 的 `receive_frame()`——一行都没改**；只把数据来源从真实
socket 换成内存字节流，从而精确模拟 TCP 的粘包、拆包、截断与失步。

| 用例 | 期望 | 实测 |
| --- | --- | --- |
| 分块 = 1 / 3 / 7 / 1000 / 1541 / 1542 / 4096 / 65536 B，以及一次给完 | 都能收满，且缓冲区与发送的原图**逐字节一致** | **9/9 PASS**，`frame_crc16` 均 = `0xC35A` |
| 篡改第 100 行的 CRC 字段 | `rc=-3`，`rows_ok=511`，`crc_err=1` | PASS |
| 第 5 个包的行号改成非法值 | 失步：`rc=-1`，`seq_err=1` | PASS |
| 字节流截断 100 字节 | `rc=-1` | PASS |
| 抽查第 0 / 100 / 511 行 CRC | `0x6106` / `0x75CE` / `0x98CD` | PASS（证明行**确实放对了位置**） |

合计 **0 失败**。`0xC35A` 是 Python 端用 `script/send_image.py` 的 CRC 实现**独立算出来的**，
两端对上说明协议实现一致。复现：`test\build_host_test.bat`。

### 11.4 已实测：真机端到端（2026-09-29，FZ3B 实板）

板子从 SD 卡启动，跑的是 **Ubuntu 18.04.5 / 内核 4.14.0-xilinx-v2018.3**，
板上自带 **gcc / make / python3**，所以是**在板上原生编译**、通过 SSH 驱动测试的。

| 项 | 结果 |
| --- | --- |
| 板上原生编译 | `make CC=gcc` 一次通过，**0 warning** |
| 三种图样端到端（`--anon-buf --no-dma`） | `rows_ok=512/512`、`crc_err=0`、`seq_err=0` |
| 整幅 CRC 对账 | `bars`=`0xF9F9`、`checker`=`0x1529`、`gradient`=`0x7BB7`，**三张全部与 PC 端独立算出的值一致** |
| 真实 `/dev/mem` 路径（`-a 0x40000000`） | 512 行收满，整幅图写进 DDR 保留区 |
| **读回逐字节比对** | **786 432 字节完全相同**（用 mmap 读回，见 4.4 的说明） |
| 吞吐 | 整帧 671 ms ≈ 9.4 Mbit/s |

过程中发现并修掉的真问题：

1. **`recv()` 无法直接写进 `/dev/mem` 映射区**（返回 `EFAULT`）→ 改成两级缓冲（见 4.4）。
2. **`0x30000000` 确实落在 `System RAM` 里**，而且该内核 **`CONFIG_STRICT_DEVMEM is not set`**
   ——内核**完全不拦** `/dev/mem`，往那儿写会**真的写坏内存**。
   实际可用的是厂商已经留好的 `0x40000000`（`no-map`，1 GB）。
3. 板子时钟停留在 2021 年，与 PC 文件时间差太大，`make` 会误判"无需重建"，
   得 `make clean` 强制重编。

### 11.5 仍未验证

- **AXI DMA 的真实传输**与 PL 侧 AXI-Stream 握手。PL 里现在跑的是**百度 EdgeBoard 自己的
  比特流**（`FPGA Version 20210310-3410-1800 ZU3`），要下我们自己的 PL 设计就会顶掉它
  原有功能——这个取舍还没定。
- `bd/create_axi_dma_stream.tcl` **未在 Vivado 里执行过**（只做了静态检查）。
- `0x40000000` 那块 no-map 保留区**长期是否会被 EdgeBoard 框架用到**——本次写进去没有
  出问题，但正式用之前最好确认，或者自己加一个 reserved-memory 节点。

### 11.6 可用来对账的已知值

发图脚本对三张内置图样算出的 **整幅 CRC16**，可以直接和板上 `[rx] frame CRC16 = 0x….`
那一行对照：

| 图样 | 整幅 CRC16 | 首行 CRC16 |
| --- | --- | --- |
| `bars`（彩条） | `0xF9F9` | `0x14D2` |
| `checker`（棋盘） | `0x1529` | `0x8A49` |
| `gradient`（渐变） | `0x7BB7` | `0x768F` |

例如发彩条图，板上应打印 `[rx] frame CRC16 = 0xF9F9`；对不上就说明传输或解析有出入。

### 11.7 复现

```bash
# 1) CRC 与协议常量
python script/send_image.py --selftest

# 2) 接收路径功能测试(主机上, 期望 0 失败)
test\build_host_test.bat

# 3) 交叉编译出板端可执行文件
make CC=<Vivado>/gnu/aarch64/nt/aarch64-linux/bin/aarch64-linux-gnu-gcc.exe
```

**下一步**：让板子启动进 PetaLinux，然后按第 9 节的顺序上板验证——
先 `--selftest`，再 `--no-dma` 收图，对图，最后建 BD 并开 DMA。

---

## 12. 性能优化：往返延迟从 1250 ms 降到 27 ms

（2026-09-29，FZ3B 实测。优化前后都是同一套协议、同一张图。）

| 阶段 | 往返耗时 | 关键改动 |
| --- | --- | --- |
| 初始 | **1250 ms** | — |
| ① | 114 ms | **PC 端 CRC 从纯 Python 逐位换成 `binascii.crc_hqx`** |
| ② | 40 ms | 板端 CRC 改**查表**；回发整帧一次发；加大 socket 缓冲 |
| ③ | 27 ms | **连接复用** + 整帧大块读 + 回发复用行 CRC + 诊断 CRC 按需 |

### 12.1 最大的一刀在 PC 端，不在板子

`proto.py` 原来是**纯 Python 逐位** CRC：每字节循环 8 次，一帧 786 432 字节就是
630 万次 Python 循环，**收发各算一遍**：

```
pure-python 逐位 :  621.7 ms
binascii.crc_hqx :    1.2 ms     ← 同一算法、结果逐位相同、快 500 倍
```

`binascii.crc_hqx` 用的就是 CRC-CCITT（0x1021、MSB-first、不反转、不异或），
**只要初值传 0xFFFF 就是 CRC-16/CCITT-FALSE**。`proto.py` 导入时会用逐位参考实现
交叉验证，防止搞错。

### 12.2 板端的账（`-v` 会打印细分）

```
[rx] recv detail: wait-first-byte=7755 us, transfer=6627 us
[rx] timing: recv=15 ms  parse(crc+copy)=8 ms  frame_crc=0 ms  total=22 ms
[mem] copy_to_ddr=493 us  publish=1 us
[echo] frame sent back: 789504 bytes in 6 ms
```

- **实际传输 6.6 ms ≈ 940 Mbit/s —— 已经是千兆线速。**（裸 TCP 对照实验：
  同一块板收 64 MB 跑到 714 Mbit/s，收 789 504 字节最快 6.1 ms ≈ 1041 Mbit/s。）
- **`copy_to_ddr` 只有 0.49 ms** —— 往 `/dev/mem` 映射拷 786 KB 并不慢，不用担心。
- 板子 CPU 是 **A53 @ 1.07 GHz**（`scaling_cur_freq`）；逐位 CRC 在它上面一帧要 ~15 ms，
  而它**同时拖慢收发两个方向**（算 CRC 时进程不读 socket，TCP 窗口就关上了）——
  所以第 ② 步换查表 CRC 收益很大。

### 12.3 "每帧重连"是延迟抖动的元凶

板子是**单线程**：收完一帧还要拷 DDR、回发、发布状态，这段时间它没回到 `accept()`，
PC 的 SYN 只能干等。实测：

```
PC connect:  0.68 / 15.95 / 16.13 / 16.33 ms   ← 和板端 wait-first-byte 完全吻合
```

**改成"一个连接上连收多帧"** 之后，`wait-first-byte` 从 ~20 ms 掉到 ~8 ms。
对应的，PC 端 `imagelink.py` 也改成复用连接（出错才重连）。

### 12.4 还剩什么

按当前实测，往返 ≈ 27 ms 的构成大致是：**传输 2×6.6 ms + 板端 parse 8 ms
+ 回发组帧 4 ms + PC 解包校验 2 ms**。要再往下压，方向是：

- 解包那 8 ms 里的**逐行 CRC 校验**是必要开销（它保证了正确性），除非换硬件 CRC 或 SIMD；
- 想真正"跑满千兆"，**锁步式一问一答是压不到线速的** —— 链路上虽然是 940 Mbit/s，
  但每帧要等一个来回。要打满带宽得改成**流水线**（板子连续发、不等回复），
  那属于另一个形态，不是延迟优化能解决的。

### 12.5 复现

```bash
# 板端(带 -v 会打印上面的耗时细分和整帧 CRC16)
./tcp_image_server -p 5060 -a 0x40000000 --no-dma --echo -v

# PC 端
python tcp_image_ui/imagelink.py 192.168.1.254 5060 bars --repeat 5
```

> 注：整帧 CRC16 现在是**诊断信息**，默认不算（省 7 ms），要它得加 `-v`。
> 逐行 CRC 一直算，正确性不受影响。

