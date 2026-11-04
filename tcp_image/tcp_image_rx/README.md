# tcp_image_rx

在 FZ3B（XAZU3EG-1SFVC784I）上，用**千兆网口 TCP** 接收 **512×512 RGB888** 图像，
存入 **DDR 保留区**，再由 **AXI DMA（MM2S）** 把图像以 **AXI4-Stream** 推给 PL。

上位机用 Python 发图；板上跑 PetaLinux，TCP 协议栈由 Linux 内核提供，
我们的程序是一个用户态 socket 服务端。

> 硬件前提：板上网口 PHY（KSZ9031）接在 **PS 的 MIO（RGMII）**，PL 拿不到网口信号，
> 所以 TCP 必须在 PS 上跑。详见 `docs/design_notes.md` 第 1 节。

---

## 目录结构

```
tcp_image_rx/
├── README.md                      本文件
├── makefile                       交叉编译(aarch64 Linux)
├── src/                           板上运行的 C 源码
│   ├── tcp_image_server.c         主程序: socket 收图 + 写 DDR + 触发 DMA
│   ├── image_proto.h              线上协议(包格式、常量)
│   ├── crc16.h / crc16.c          CRC-16/CCITT-FALSE
│   ├── axidma.h / axidma.c        AXI DMA(MM2S, simple)寄存器驱动
│   └── ddr_layout.h               DDR 保留区布局 + frame_status 状态块
├── script/
│   └── send_image.py              PC 端发图脚本(纯标准库)
├── dts/
│   └── zynqmp-image-reserved-memory.dtsi   设备树预留 DDR(no-map)
├── bd/
│   └── create_axi_dma_stream.tcl  Vivado:给已有 BD 加 AXI DMA 并导出 AXI-Stream
├── test/                          接收路径的主机端功能测试(板端运行不需要)
│   ├── host_test_rx.c             直接 include 主程序, 原样调用 receive_frame()
│   ├── build_host_test.bat        一键编译并跑测试
│   └── shim/                      MinGW 缺的 POSIX 头垫片(仅供该测试用)
└── docs/
    └── design_notes.md                 完整设计文档(协议/内存映射/DMA 时序/搭建/排查)
```

---

## 快速上手

### 1. 先验 CRC（不需要板子）

```bash
python script/send_image.py --selftest
```

期望输出 `CRC16("123456789") = 0x29B1 ... OK`。

### 2. 编译并拷到板上

本机（Windows）可以直接用 Vivado 自带的 aarch64 **Linux** 交叉编译器，不必另装 SDK：

```bash
make CC="E:/fpga/vivado/2025.2.1/gnu/aarch64/nt/aarch64-linux/bin/aarch64-linux-gnu-gcc.exe"
```

> ⚠ `gnu/aarch64/nt/` 下有**两套**：`aarch64-none` 是**裸机**（没有 libc），
> `aarch64-linux` 才能编本程序。也别指望把 `aarch64-none` 编出来的东西跑在 Linux 上。

拷到板上并运行（先不开 DMA，只验证收图）：

```bash
scp tcp_image_server root@<板子IP>:/tmp/
ssh root@<板子IP>
/tmp/tcp_image_server -p 5000 -a 0x30000000 --no-dma -v
```

### 3. PC 发图

```bash
python script/send_image.py -H <板子IP> -P 5000 --pattern bars -v
```

板上应出现 `rows_ok=512/512, crc_err=0`。

### 4. 打开 DMA，把图像推给 PL

去掉 `--no-dma`，并用 Vivado 脚本打印出的地址传 `-d`：

```bash
/tmp/tcp_image_server -a 0x30000000 -d 0x40400000
```

> PL 侧消费端必须拉高 `tready`，否则 DMA 超时。详见 `docs/design_notes.md` 5.3。

---

## 命令速查

| 命令 | 作用 |
| --- | --- |
| `make` | 交叉编译出 `tcp_image_server` |
| `make host` | 用本机 gcc 冒烟编译（不能真跑） |
| `make clean` | 清理 |
| `python script/send_image.py --selftest` | 校验 CRC 实现 |
| `... --pattern gradient\|bars\|checker` | 发内置测试图样 |
| `... --in x.ppm` / `--in x.rgb` | 发外部图像（P6 PPM 或 786432 字节裸 RGB） |
| `... --dump sent.rgb` | 存下实际发送的原始图，便于和板上比对 |
| `tcp_image_server --help` | 全部运行参数 |

---

## PL 图像处理往返（2026-10-01 新增）

除了"收图存 DDR"，板端程序现在还能走**完整的 PL 处理往返**并把**处理后的图回传 PC**：

```
PC 发图(带模式) ─► PS 收图 ─► 展开成 32bit/像素 ─► 写模式寄存器 ─►
     AXI DMA MM2S ─► img_proc_axis(PL 处理) ─► AXI DMA S2MM ─► DDR 输出图区 ─►
     还原成紧包 RGB888 ─► 回传 PC
```

对应新增/变化的命令行参数：

| 参数 | 说明 |
| --- | --- |
| `-g, --gpio-addr <hex>` | AXI GPIO（模式寄存器）基址；**`0` = 不写模式寄存器**（默认 `0xA0000000`） |
| `--thr <n>` | 模式 1（阈值分割）的阈值 0..255（默认 128） |

`--echo` 的含义也明确了：**开 DMA 时回传的是 PL 处理后的图；`--no-dma` 时回显原图**
（保持老的纯软件联调行为不变）。

```bash
# 完整 PL 往返（基址以 BD / XSA 实际分配为准）
./tcp_image_server -p 5060 -a 0x40000000 -d <dma基址> -g <gpio基址> --echo -n 0
```

设计细节与算法见 [`docs/image_processing_design.md`](docs/image_processing_design.md)，RTL 在 `rtl/`。

> ⚠ `-d` / `-g` 的默认值是**占位**，必须先跑 `bd/create_image_proc_bd.tcl`、从 XSA 里
> 拿到真实地址再改。目前 PL 里跑的仍是百度自带比特流，所以**这一段还没上板验证过**。

---

## 关键约定（改代码前先看清楚）

| 约定 | 值 |
| --- | --- |
| 行包 | `row(u16) + length(u16)=1536 + crc16(u16)` + 1536 B，共 **1542** B |
| 包数 / 流总量 | 512 包 / **789 504** B |
| CRC | CRC-16/CCITT-FALSE，poly `0x1021` init `0xFFFF`，校验值 `0x29B1` |
| DDR 保留区 | `0x3000_0000`，1 MiB，**设备树必须 `no-map`** |
| 图像区 / 状态区 | 偏移 `0x0`（786 432 B） / 偏移 `0xC0000`（32 B） |
| 状态魔数 | `0x314D5246`（小端即 ASCII `FRM1`），**最后写** |

---

## 开机自启（把程序固化到 SD 卡上）

程序放在 `/root/tcp_image_rx/`（在 SD 卡的根文件系统 `mmcblk1p2` 上），**重启不会丢**。
但文件在 ≠ 会自动跑 —— 所以做成 systemd 服务：

```bash
# 查看状态 / 启停
systemctl status tcp-image-server
systemctl stop  tcp-image-server
systemctl start tcp-image-server
systemctl disable tcp-image-server     # 取消开机自启

# 看日志（程序所有输出都在这里）
journalctl -u tcp-image-server -f
```

服务文件在 `/etc/systemd/system/tcp-image-server.service`，启动参数就写在里面的
`ExecStart=` 那一行，改完执行：

```bash
systemctl daemon-reload && systemctl restart tcp-image-server
```

默认参数是 `-p 5060 -a 0x40000000 --no-dma --echo -n 0`（和 PC 端 GUI 配套）。
**等 PL 侧的 AXI DMA 做好之后，把 `--no-dma` 去掉、`--echo` 也就可以去掉了。**

> 板子开机后 1 分钟内还在忙着起桌面等服务（4 核 A53 会满载），
> 那段时间首次连接可能超时，等一会儿再连就正常了。

> 厂商自己也在 `/etc/rc.local` 里留了自启钩子（注释写着 "Users can add autostart
> programs here"），不喜欢 systemd 的话也可以把启动命令加在那里。

## 验证状态

**已在真实板子上跑通**（FZ3B，Ubuntu 18.04 / 内核 4.14，在板上用自带 gcc 原生编译）：

- **端到端收图成功**：PC 发 512×512 彩条图 → 板子经 TCP 收满 512 行 → 存进 DDR 保留区
  `0x40000000` → **读回来逐字节比对，786 432 字节完全相同**；
- 三种图样的整幅 CRC 与 PC 端独立算出的值**全部对上**（`0xF9F9` / `0x1529` / `0x7BB7`）；
- 板上 `make CC=gcc` 一次通过，**0 warning**。

真机上发现并修掉的两个真问题：

1. **`recv()` 不能直接写进 `/dev/mem` 映射区**（内核返回 `EFAULT`，`errno=14`）
   → 改成两级缓冲：先收进普通内存，再 `memcpy` 到 DDR。
2. **`0x30000000` 落在 Linux 的 `System RAM` 里**，而且该内核**没开 `STRICT_DEVMEM`**，
   写下去会真写坏内存 → 改用厂商已经保留好的 `0x40000000`（`no-map`）。

**仍未验证**：AXI DMA 的真实传输与 PL 侧 AXI-Stream 握手（PL 里现在跑的是百度 EdgeBoard
自己的比特流，要下我们自己的设计就会顶掉它的功能，这个取舍还没定）；
`bd/create_axi_dma_stream.tcl` 未在 Vivado 里执行过。

对账用的**整幅 CRC16**：彩条 `0xF9F9`、棋盘 `0x1529`、渐变 `0x7BB7`。

### 2026-10-01：PL 处理往返的板端改造（编译 + 主机测试，未上板）

- **交叉编译 0 warning**：`aarch64-linux-gnu-gcc -O2 -Wall -Wextra` 编 `src/` 下四个
  `.c` 全部通过，链接出 AArch64 ELF（`e_machine = 0xB7`）。
- **主机功能测试 15/15 通过**：`test/build_host_test.bat`（`test/host_test_rx.c`
  直接 include 板端那份 `receive_frame()`）覆盖 9 种 TCP 分块、4 个负例、行放置，
  外加**新增的像素格式转换往返**（786 432 → 1 048 576 → 786 432 逐字节还原）。
- ⛔ **DMA 往返 / 模式寄存器 / 回传处理图**这三条**尚未上板**，要等第 ⑤ 步的比特流。
  另外 `--echo` 回传"处理后的图"这条路径**没有自动化测试**（它依赖真实 PL）。

完整清单见 `docs/design_notes.md` 第 11 节。

