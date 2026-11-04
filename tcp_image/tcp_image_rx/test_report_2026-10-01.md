# tcp_image_rx 工程测试报告

> 测试日期：**2026-10-01**（本次会话实测）
> 被测工程：`E:\fpga\my_project\tcp_image_rx\`（板端程序 + PL 图像处理 RTL）
> 配套 PC 端：`E:\fpga\my_project\tcp_image_ui\`
> 测试范围：离线自检套件 + 板子连通性探测 + 重跑 RTL 综合/实现
> **本报告只做"测试与记录"，没有修改任何代码或工程。**
> ⚠ 报告里有几处与 `progress_and_handoff.md` / 记忆不一致的地方，均以本报告实测为准（见 §5）。

---

## 0. 结论摘要

**板子目前是"活的"**：`192.168.1.254` 能 ping 通、能 SSH，跑的是**原厂 boot.bin**、
`tcp-image-server` 服务在线，网口收图/回显通路**实测逐字节正确**。

（**2026-10-01 上板后已把板上程序换成新版**，PS 侧新协议现已跑通，见 §9。）
但继续上板测 PL 侧时发现：**PS→PL 的 AXI4-Lite 写数据被丢成 0**（只有 16 字节对齐的地址能带数据），
所以 `IN_PIX`(0x08) 永远写不进像素 → 图像处理整链**仍未跑通**（见 §9）。

三条并行分支的现状：

| 分支 | 内容 | 状态 |
| --- | --- | --- |
| ① 网口收图 + 回显（纯 PS，`--no-dma --echo`） | 板端老版程序 | ✅ **真机实测通过**（本报告 §3.1） |
| ② PL→PS（AXI DMA 读 DDR） | `img_proc` / `boot_prj` 设计 | ⛔ 厂商 PS 未使能 PL→PS 从口，**通不了**（沿用既有结论，本次未推翻） |
| ③ PS→PL 寄存器往返（`img_proc_axil`） | 当前主线 | ✅ **两个 bug 都已修并经 PC 上位机端到端验证**（写通道 §10 + 下移一行 §11）；4/4 PASS |

自建 boot.bin 那条路（想打通 ②）**已经试过、失败、并回滚**——板子一度起不来，现已恢复原厂镜像。

---

## 1. 测试环境与方法

| 项 | 值 |
| --- | --- |
| 本机 | Windows 11，Vivado/Vitis **2025.2.1**（`E:/fpga/vivado/2025.2.1/`） |
| 执行通道 | 本会话的 Linux 沙箱**不可用**（`RPC pipe closed`），改用 **Vivado Tcl `exec` 调本机 gcc/python**，另用 **paramiko** 走 SSH 探板 |
| 交叉编译器 | `E:/fpga/vivado/2025.2.1/gnu/aarch64/nt/aarch64-linux/bin/aarch64-linux-gnu-gcc.exe` |
| MinGW gcc | `E:/fpga/vivado/2025.2.1/Vivado/tps/mingw/6.2.0/.../gcc.exe` |
| Python | `E:/fpga/vivado/2025.2.1/Vivado/tps/win64/python-3.13.0/python.exe`（含 Pillow） |
| 开发板 | FZ3B（`XAZU3EG-1SFVC784I`），Ubuntu 18.04 / 内核 `4.14.0-xilinx-v2018.3`，`root/root@192.168.1.254` |

> ⚠ 过程中踩到一个坑并已绕开：**`mcp__vivado__*` 里任何单次调用只要超过约 50 秒，整个 Vivado 会话就被杀掉**（本次连杀 c5/c6/c7/c8 四个会话）。可靠做法是
> `launch_runs`（异步、秒级返回）+ 短轮询，**绝不用 `wait_on_run` / 长 `after`**。

---

## 2. 测试项与结果一览

| # | 测试项 | 方法 / 命令 | 结果 |
| --- | --- | --- | --- |
| 1 | 主机功能测试（收图路径） | `test\build_host_test.bat` | ✅ **TOTAL FAILURES: 0**（全部 PASS） |
| 2 | 板端程序交叉编译 | `make CC=<aarch64-linux-gnu-gcc>` | ✅ **0 warning**，ELF64 **AArch64** |
| 3 | 板上工具编译检查 | `aarch64-gcc -c test/img_proc_axil_test.c` | ✅ 通过 |
| 4 | CRC 自检（板端脚本） | `python script/send_image.py --selftest` | ✅ `0x29B1` OK |
| 5 | PC 端自检 | `python proto.py` / `python _verify_pc.py` | ✅ PC VERIFY OK（Pillow 在） |
| 6 | 板子连通性 | `ping` + SSH（paramiko） | ✅ 3/3 通，SSH 通 |
| 7 | **真机往返（旧协议）** | 自写 512×512 bars → TCP 5060 | ✅ **逐字节一致**，23.7 ms |
| 8 | 真机往返（新协议） | 带 4 字节控制头发 | ⛔ 被 RST（板端二进制不认） |
| 9 | RTL 重跑：`img_proc_axil` | 复位 + 重综合 + 重实现 | ✅ 时序收敛，0 CW |
| 10 | RTL 现状：`img_proc`（DMA 版） | 读已有报告 | ⚪ 完整、时序收敛（未重跑） |
| 11 | RTL 现状：`boot_prj`（HP0 版） | 读已有报告 | ⚪ 完整、时序收敛（未重跑） |
| 12 | RTL 静态 lint | `iverilog` / `verilator` | ⏭ SKIP（本机未安装） |

---

## 3. 已测通过的功能（附证据）

### 3.1 网口收图 + 回显（**真机实测通过**）

板上 `tcp-image-server` 正以 `-p 5060 -a 0x40000000 --no-dma --echo -n 0` 在线运行（systemd，`enabled`）。
本次从 PC 实时发了一整帧**旧协议**（789 504 B：512 个行包，无控制头）：

```
built bars frame: 786432 B, frame CRC16 = 0xF9F9 (expect 0xF9F9)   ← 与既有对账值一致
=== Test A: OLD format (789504 B) -> deployed service ===
send=789504 B ok, recv=789504 B (ok)  send=0.1 ms reply=23.6 ms total=23.7 ms
  A result: rows_ok=512 crc_err=0  reply_bytes == sent image? True
```

**512 行全部 CRC 正确、回传图像与发送图像逐字节相同、往返 23.7 ms** —— 与记忆里"1251→27 ms"的优化结论吻合。

### 3.2 离线自检套件（**全部 PASS**）

- **主机功能测试** `test/build_host_test.bat`：末行 `=== TOTAL FAILURES: 0 ===`，覆盖
  9 种 TCP 分块（0/1/3/7/1000/1541/1542/4096/65536）、4 个负例（篡改行 CRC / 非法行号 / 非法魔数 / 非法 mode）、
  字节流截断、行放置抽查、以及**像素格式转换往返**（786 432 → 1 048 576 → 786 432 逐字节还原，首像素布局 `{00,R,G,B}`）。
  这一份是**直接 `#include` 板端那份 `receive_frame()`** 跑的，等价于验证交付代码本体。
- **交叉编译**：`src/` 下 5 个 `.c`（`axigpio` / `axidma` / `tcp_image_server` / `crc16` / `axilproc`）**0 warning**，
  链接出 `ELF64 / AArch64 / PIE`（entry `0x2700`）。
- **CRC 自检**：`CRC16("123456789") = 0x29B1` 通过；PC 端 `proto.py`（含快速 C 实现 vs 逐位参考实现交叉验证）通过。
- **PC 端 `_verify_pc.py`**：proto 自检 + 4 个文件 `py_compile` + `save_image` 的 `.rgb/.ppm/.png` 往返全 OK。

### 3.3 RTL：`img_proc_axil`（当前主线，本次**重跑通过**）

工程 `vivado/img_proc_axil/`，顶层 `system_wrapper`，器件 `xazu3eg-sfvc784-1-i`。
本次**复位后从零重综合 + 重实现**：

```
synth_1 : synth_design Complete!  / 100%
impl_1  : route_design  Complete! / 100%

Design Timing Summary（7468 端点）
  WNS +4.029 ns   TNS 0.000   失败端点 0
  WHS +0.016 ns   THS 0.000   失败端点 0
  WPWS +3.500 ns              失败端点 0
  → 时序全部收敛（Timing MET）

CRITICAL WARNING: 0

资源（整机 system_wrapper）
  CLB LUTs       1861 / 70560   2.64%
  CLB Registers  1653 / 141120  1.17%
  Block RAM Tile   64 / 216     29.63%   （全部 RAMB36E2，即 2 Mb 帧缓冲）
  DSPs              1 / 360      0.28%
```

### 3.4 RTL 现状：`img_proc`（DMA 版）与 `boot_prj`（HP0 版）——完整但**未重跑**

（读它们已有的报告，均为 2026-10-01 早些时候的结果）

| 工程 | synth | impl | WNS | WHS | 失败端点 |
| --- | --- | --- | --- | --- | --- |
| `img_proc`（PS+AXI DMA+img_proc_axis+GPIO） | Complete | route Complete | **+6.017 ns** | +0.011 ns | 0 / 28071 |
| `boot_prj`（PS 使能 S_AXI_HP0_FPD 的启动设计） | Complete | route Complete | **+4.085 ns** | +0.010 ns | 0 / 31980 |

> 说明：这两个工程对应的是**已放弃的 PL→PS(DMA) 路线**，本次没有重跑（只读了它们的既有结果）。
> `img_proc_axil` 的本次实测值与交接文档记录的 `WNS +4.45 / WHS +0.008` 略有出入 —— 属实现结果非逐比特可复现的正常现象，**结论一致：都收敛**。

---

## 4. 板子当前实况（只读探测）

| 项 | 值 |
| --- | --- |
| 内核 | `Linux EdgeBoard 4.14.0-xilinx-v2018.3 ... aarch64` |
| 运行时长 | 约 34 分钟（`/proc/uptime` ≈ 2027 s） |
| **启动镜像** | `/run/media/mmcblk1p1/boot.bin`，6 650 296 B，md5 **`cc316f785c3b40f46caef59e569bc988`**（= 原厂，与 `boot/orig_BOOT.BIN` 一致） |
| 分区残留 | 同目录还有 `boot.bin.old`、`boot.bin.vendor`（各 6 650 296 B，原厂备份） |
| FPGA 管理器 | `/sys/class/fpga_manager/fpga0/state` = `operating`（PL 跑的是厂商比特流） |
| 服务 | `tcp-image-server` = **active + enabled**，PID 3340，监听 `0.0.0.0:5060` |
| 板端二进制 | `/root/tcp_image_rx/tcp_image_server`（27 968 B，**旧版**：`--help` 无 `-g/--thr`） |
| 板端源码 | `/root/tcp_image_rx/src/` 只有 `axidma.* / crc16.* / ddr_layout.h / image_proto.h / tcp_image_server.c` —— **缺 `axigpio.*`、`axilproc.*`** |
| 板上其它 | 有 `axil_test`（13 976 B 可执行）+ `axil_test.c`；另有残留文件 `0`（786 432 B） |

**关键判断**：板上 `tcp-image_server` 是**旧一代**，与仓库 `src/`（新协议、带控制头 + 模式 + PS→PL）**不同步**。

---

## 5. 已知问题清单（按严重度）

### 🔴 P0 —— 影响"当前目标"能否达成

1. **板上二进制是旧版，新协议跑不通**（实测复现）
   - 现象：按新协议（前置 4 字节控制头 `0x4D43 + mode`，共 789 508 B）发帧，**对端立即 RST**：
     ```
     Test B: send=789508 B, recv=0 B (oserr:[WinError 10054] 远程主机强迫关闭了一个现有的连接)
     ```
   - 根因：`/root/tcp_image_rx/tcp_image_server` 早于"控制头/模式"特性（`--help` 无 `-g/--thr`，`src/` 无 `axigpio/axilproc`）。
   - 影响：**"PC 切模式 → 板子 → PL 处理 → 回图"这条整链，目前只能在源码层看到，板上跑不了。**

2. **PS→PL 寄存器方案（`img_proc_axil`）从未上板验证**
   - RTL 综合/实现都过（本报告 §3.3），但板端推拉一帧的**实机测试没做过**。
   - 上次尝试留在："用纯 Python 逐像素轮询把板子 CPU 打满、随后掉线"；现已改成板上 C 工具 `axil_test`，但**尚未跑过**。

3. **自建 boot.bin 路线失败（已回滚）**
   - 为打通"PL→PS(DMA)"，合成了自建 FSBL/PMUFW + ATF/U-Boot + 自己比特流的 boot.bin，刷进 SD 卡后**板子起不来**（见 `boot/recovery_guide.md`）。
   - 现状：板子上 `boot.bin` 已是**原厂镜像**（md5 对上），**该路线当前是死的**；且推论本身未证（`psu_init.c` 不写 `AFI_FM` 寄存器）。

### 🟠 P1 —— 事实性限制

4. **厂商 PS 未使能任何 PL→PS 从口**，`AXI DMA` 读 DDR 的路线在本板不通（既有排除法结论）。
   本次未重跑该实验，**沿用结论**：`DMASR` 恒 0、无任何 error、跨 2 GB 也不报 DECERR → 事务无人应答。

5. **PL 图像处理算法的正确性从未被验证**：无仿真、无上板、且按用户决定**PC 端不做参考实现/逐像素比对**。
   目前"正确"只等于"综合/实现 0 error 0 CW + 时序收敛"，**不等于算法对**。

### 🟡 P2 —— 文档 / 卫生问题

6. **`progress_and_handoff.md` 已陈旧**：文件时间 12:41，而 boot.bin / axil 上板那些都发生在 **15:00–16:22**，
   该文档**完全没覆盖**（它停在 `build_full_design.tcl` / `build_axil_design.tcl`）。
   同样地，记忆文件也没记 boot.bin 那次失败。→ **接手的人只看交接文档会漏掉最近这半天的关键失败。**
7. **仓库与板子不同步**：`src/` 是新的，板上跑的是旧的（见 §4）。
8. **零碎残留**：工程根目录有个 0 字节的 `&1` 文件（Tcl 重定向写错的产物）；板上 `/root/tcp_image_rx/0`（786 432 B）是测试残留。
9. **本机没装 `iverilog` / `verilator`**，RTL 只能靠 Vivado 综合做门禁（缺一个更轻更快的静态 lint 手段）。

---

## 6. 本次**没有做**的测试（如实说明）

- ❌ **没有把我们的比特流加载到板子上**（`img_proc_axil.bin` 上板、跑 `axil_test` 推拉一帧）——
  这属于"改动硬件状态"，超出本次"测试+总结"的范围；**已确认板子可加载**（历史验证过 `fpga_manager` 通路），**随时可做，需你点头**。
- ❌ 没有重跑 `img_proc` / `boot_prj` 的综合实现（只读了它们的既有报告）。
- ❌ 没有重新导出 `.bit` / `.bin`（本次实现跑到 `route_design` 为止；`bitstream/` 下的 `.bit` 仍是当日 17:02 那份）。
- ❌ 没有做 PL 侧仿真 / 后时序仿真。
- ❌ 没有做 DMA 往返 / 模式寄存器 / `--echo` 回传处理图 的板上验证（依赖真实 PL）。

---

## 7. 建议的下一步（**仅建议，未实施**）

1. **把 `src/` 重新编译并部署到板上**，让板端认识控制头 —— 否则新协议在板上永远跑不通（P0-1）。
   板上自带 gcc，`scp src makefile root@192.168.1.254:/root/tcp_image_rx/ && make clean && make CC=gcc` 即可。
2. **上板验证 PS→PL 通路**：`rmmod fpgadrv`（当前未加载）→
   `echo img_proc_axil.bin > /sys/class/fpga_manager/fpga0/firmware` → 跑板上 C 工具 `axil_test`（**别再用 Python 逐像素轮询**）。
3. **补/刷新 `progress_and_handoff.md`**，把 boot.bin 失败与 axil 上板这两段补进去（P2-6）。
4. 想继续走 PL→PS(DMA) 的话，先做一个**最小 PL→PS 验证**（一个 BRAM + 一个从口）再决定是否再碰 boot.bin。

---

## 附录：原始证据摘录

**A. 主机功能测试末行**
```
=== TOTAL FAILURES: 0 ===
HOST TEST PASSED
```

**B. 真机往返（旧协议）**
```
built bars frame: 786432 B, frame CRC16 = 0xF9F9 (expect 0xF9F9)
send=789504 B ok, recv=789504 B (ok)  send=0.1 ms reply=23.6 ms total=23.7 ms
  A result: rows_ok=512 crc_err=0  reply_bytes == sent image? True
```

**C. 新协议被拒**
```
=== Test B: NEW format (789508 B, ctrl hdr magic=0x4D43 mode=5) ===
send=789508 B, recv=0 B (oserr:[WinError 10054] 远程主机强迫关闭了一个现有的连接。)
```

**D. 板子实况（节选）**
```
uname: Linux EdgeBoard 4.14.0-xilinx-v2018.3 ... aarch64
md5sum boot.bin: cc316f785c3b40f46caef59e569bc988   (= 原厂)
fpga_manager/fpga0/state: operating
tcp-image-server: active / enabled, 0.0.0.0:5060 LISTEN
tcp_image_server --help: 无 -g/--thr  → 旧版
```

**E. RTL 重跑（img_proc_axil）**
```
synth_1: synth_design Complete! 100%   impl_1: route_design Complete! 100%
WNS +4.029  TNS 0.000  f=0 / 7468      WHS +0.016  THS 0.000  f=0 / 7468
CRITICAL WARNING: 0
BRAM 64/216 (29.63%)   DSP 1/360   CLB LUT 1861/70560   CLB Reg 1653/141120
```

---

*本报告由本次会话实测生成；所有"通过/失败"均来自真实命令输出，未做推测性断言。*

---

## 9. 上板实测（2026-10-01 追加）

> 本次按"先 PS 后 PL"两步真上板。板子一切正常，没有再出现上次的掉线。

### 9.1 第一步：PS 侧（部署新版程序 + 新协议）—— ✅ **通过**

1. 把仓库 `src/`（11 个文件）+ `makefile` scp 到 `/root/tcp_image_rx/`（先 `tar` 备份了板上原状态）。
2. 板上 `make clean; make CC=gcc` → **0 warning，RC=0**。新二进制 33 088 B（旧版 27 968 B），
   `--help` 出现 `-g/--gpio-addr`、`--thr`。
3. `systemctl restart tcp-image-server` → 服务跑新二进制（日志出现新 `[cfg] axi gpio mode / threshold m1` 行，自检 `CRC16 0x29B1` OK）。
4. PC 端按**新协议**（4 B 控制头 `magic=0x4D43` + mode）连发 6 帧：

```
=== valid modes (--no-dma => expect ORIGINAL echoed back) ===
  magic=0x4D43 mode=0    : rows_ok=512 crc_err=0  byte-identical=True  total=25.4 ms
  magic=0x4D43 mode=1    : rows_ok=512 crc_err=0  byte-identical=True  total=26.4 ms
  magic=0x4D43 mode=2    : rows_ok=512 crc_err=0  byte-identical=True  total=24.5 ms
  magic=0x4D43 mode=3    : rows_ok=512 crc_err=0  byte-identical=True  total=25.2 ms
  magic=0x4D43 mode=4    : rows_ok=512 crc_err=0  byte-identical=True  total=25.3 ms
  magic=0x4D43 mode=5    : rows_ok=512 crc_err=0  byte-identical=True  total=25.3 ms
=== negative cases (expect rejection) ===
  bad magic 0x1234       : 连接被重置（拒收）✓
  bad mode 9             : 连接被重置（拒收）✓
```

→ **新协议 6 个模式全部逐字节正确，非法魔数/模式被正确拒收。§5 的 P0-1 关闭。**

### 9.2 第二步：PL 侧（加载 `img_proc_axil.bin` + 板上推拉一帧）—— ❌ **发现真 bug**

1. 把 `bitstream/img_proc_axil.bin` 上传到板上 `/lib/firmware/`，写 `fpga_manager` 加载 →
   `state=operating`、`dmesg` 打印 `writing img_proc_axil.bin`、`RC=0`。**比特流加载成功。**
2. 板上编译并运行自带的 C 工具 `axil_test`（推 262144 像素、拉 262144 像素）：

```
base=0xA0000000  VERSION=0x494D5001 (expect 0x494D5001)      ← 我们的 IP 活了
push 262144 px : 108931 us (416 ns/px)
pull 262144 px :  99174 us (378 ns/px)
STATUS=0x00000001  min=0 max=0 frame_done=0
mode0 check: mismatch = 260608 / 262144
  first mismatch px=1536 (row=3) got=0 expect=1
  out row 0/128/256/384 : R=0 G=0 B=0                        ← 全是黑的
```

3. 读板上 RTL 自带的调试寄存器定位（**只有 6 次寄存器访问，没有 Python 轮询**）：

```
CTRL   @0x00 = 0x00000000      ← 只读寄存(见下)，读回 0 属正常
STATUS @0x04 = 0x00000001      ← in_ready=1, 其余 0
VERSION@0x10 = 0x494D5001      ← 读通路正常
DBGPIX @0x14 = 0x00000000      ← in_pix_r = 0：写进去的像素是 0
DBGWD  @0x1C = 0x00000000      ← wdata_r    = 0：AXI 写数据是 0
```

4. 逐个偏移写唯一值再看 `DBGWD`，**规律一目了然**：

| 写地址 | 0x00 | 0x04 | 0x08 | 0x0C | 0x10 | 0x14 | 0x18 | 0x1C | 0x20 | 0x24 | 0x28 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 写数据是否到达 | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |

**只有 16 字节对齐的地址能带写数据；其它偏移一律变成 0。**
而**读**通路不受影响（`STATUS` 在 0x04、`VERSION` 在 0x10 都读得到）。

### 9.3 结论（PL 侧）

- ✅ **PS→PL 的 AXI4-Lite 通路本身是通的**：地址译码、握手、读数据、VERSION 全对。
- ⛔ **写数据的字节通道（byte lane）映射坏了**：PS 是宽主口，`SmartConnect` 插的 `auto_pc`
  （AXI4→AXI4-Lite）没把数据搬到对应通道，导致"非 16 字节对齐地址的 32bit 写"数据全丢。
  → `IN_PIX` 在 **0x08**（非 16 对齐）→ 永远写 0 → 帧缓冲全 0 → 输出全黑、min=max=0、frame_done=0。
- ⚠ RTL 里那段"改成规范现握手修 wdata"的改动（`img_proc_axil.v` 135–139 行注释）**没有解决这个 bug** ——
  我加载的比特流就是那次改动之后的构建（RTL 改于 16:55，比特流 17:02 生成，0 error），问题依旧。
  说明真正的原因是**协议转换器的数据通道对齐**，不是握手时序。
- 这不是算法问题：`img_proc_axis` / `mode_map` 等处理逻辑一行都没跑到（输入恒 0）。

### 9.4 这次留在板上的东西（便于下次继续）

- 板上 `/root/tcp_image_rx/`：新版 `tcp_image_server`（已由 systemd 服务在跑）、`axil_test`(重编过)、
  以及 3 个调试小脚本 `axil_regs.py / axil_wr.py / axil_sweep.py`；另有备份 `~/Tcp_Image_Rx_backup_*.tgz`。
- **PL 里现在是我们自己的 `img_proc_axil` 比特流**（不是厂商的了）——**重启即恢复厂商 PL**。
- `tcp-image-server` 服务参数未变（`--no-dma --echo`），与 PL 状态无关，照常可用。

### 9.5 建议的修法（未实施）

1. 根因在 PL 的 AXI 从机侧数据通道。可行方向：
   - 换掉这条从口的 `SmartConnect`+`auto_pc`，改用 `axi_interconnect` + 显式 `axi_protocol_converter`（对齐/通道由工具配好）；
   - 或把寄存器**整体搬到 16 字节对齐的布局**（如 `IN_PIX` 放 0x10、其余顺延），先绕过去再把通道修对；
   - 或让从机**读正确的 byte lane**（按 `s_axi_awaddr[3:2]` 选 `s_axi_wdata` 的哪 8/16/32 位）。
2. 修完重新出比特流，再跑 §9.2 那两步验证（现在是"写 0xA5A5xxxx 看 DBGWD"即可秒判）。

---

## 10. 上板修复：AXI4-Lite 写数据丢通道（2026-10-01 第三轮，**已修并验证**）

> 用户选定"方法一"：改用 `axi_interconnect` + **显式** `axi_protocol_converter`。

### 10.1 根因（已在 §9.3 定位，这里给结论）

PS 主口 `M_AXI_HPM0_FPD` 是 **128-bit**（`CONFIG.PSU__MAXIGP0__DATA_WIDTH = 128`）。
`axi_interconnect` 按需自动插了 `axi_dwidth_converter`(**auto_ds**, 128→32) + `axi_protocol_converter`(**auto_pc**)。
正是那个 **128→32 的宽度转换**把"非 16 字节对齐地址的 32bit 写"的数据丢了。

### 10.2 改法

1. **PS 主口设成 32-bit**（`CONFIG.PSU__MAXIGP0__DATA_WIDTH = 32`）→ 宽度一致，`auto_ds` 直接消失。
2. 协议转换改成**显式**的一个 `axi_protocol_converter`（`pc_axil`, AXI4 32bit → AXI4-Lite 32bit），
   放在 `axi_interconnect(M00_AXI)` 与 `img_proc_axil_0/S_AXI` 之间 —— 不再让互联自动插。
3. 脚本 `bd/build_axil_design.tcl` 已更新（可复现）；同时就地改了 `vivado/img_proc_axil` 的 BD。

**重跑结果**：综合/实现/比特流 **0 error / 0 critical warning**，
**WNS +3.750 ns / WHS +0.029 ns**（时序收敛），CLB LUT 1246(1.77%)、Block RAM 64/216、DSP 1/360。
新比特流 `bitstream/img_proc_axil.{bit,bin}`（md5 bin = `d23efd530cd8ce635ea3ce4c80cddc20`）；
旧的坏版本备份为 `bitstream/img_proc_axil_v1_dataloss.*`。

### 10.3 上板验证 —— ✅ 写通道修好了

写各偏移、读 `DBGWD`：

| 写地址 | 0x00 | 0x04 | 0x08 | 0x0C | 0x10 | 0x14 | 0x18 | 0x1C | 0x20 | 0x24 | 0x28 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **修复前** | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| **修复后** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

（`DBGPIX` 也能看到写进 `IN_PIX` 的像素了。）

整帧 `axil_test`（mode0，512×512 灰度斜坡）：

```
修复前: STATUS=0x00000001  min=0 max=0  frame_done=0   mismatch=260608/262144   ← 全黑
修复后: STATUS=0x00FF0001  min=0 max=255 frame_done=0   ← 帧统计正确, 图像出来了
```

**→ §5 的 P0-2（PS→PL 从没上板验证）关闭；PS→PL 寄存通路端到端可用。**

### 10.4 但暴露出一个新问题：输出图像**整体下移一行**

逐像素比对整帧 262144 个（预期 = 输入灰度 `gy = 255*row/511`）：

```
bad rows: 255   first bad row: 3          ← 只有"灰度发生台阶"的那些行错
  row 3 got 0 expect 1 (delta -1) ; row 5 got 1 expect 2 ; row 7 got 2 expect 3 ; ...
  x!=0 mismatch: 130305 个 ; x==0 mismatch: 255 个
```

- 错的是**整行**（x>0 也错），不是某一列 → 不是列的问题。
- 规律 `out(row r) = in(row r−1)` → **输出图像整体下移了一行**（第 0 行是 line-buffer 预置残留，值不稳定：一次测到 0、一次测到 255）。
- 与写通道无关，是**帧缓冲读出 / 3×3 窗口对齐的 off-by-one（1 行延迟没补偿）**。
- 影响：图像内容对，但整体错位一行（对均匀图看不出，对真实图就是一条行错位）。

### 10.5 下一步（未做）

修 `gray_frame_store.v` / `img_proc_axis.v` 读出侧的 1 行对齐（让第一行输出对应输入第 0 行），
重出比特流，用同一个"灰度斜坡 + 逐像素比对"工具复验（`axil_dump` 已在板上）。

### 10.6 板上现状

- PL = 我们**修好写通道的** `img_proc_axil`（重启即恢复厂商 PL）。
- `/lib/firmware/img_proc_axil.bin` = 新版；`tcp-image-server` 跑新版（PS 侧 `--no-dma --echo` 可用）。
- 板上新增：`axil_test`、`axil_dump`、`axil_dump2`（灰度斜坡逐像素比对工具）、`axil_regs/wr/sweep.py`。

---

## 11. 第二轮修复：图像整体下移一行（2026-10-01 第四轮，**已修 + PC 上位机验证**）

### 11.1 根因

`gray_frame_store.v` 的读出把"读到的那一拍"当成"数据可用的那一拍"来喂列移位寄存器，
但 `rd_data <= ram[rd_addr]` 是**寄存器**（块 RAM 同步读）—— **数据比状态晚一拍**。
于是 R0 读到的「上一行」被当成了「当前行」的抽头：窗口中心取到**上一行**的像素，
整幅输出**下移一行**（正是 §10.4 实测的 `out(行 r) = in(行 r−1)`；且是整行全体像素错位，不是某一列）。

### 11.2 改法

把读出重写成干净的 **4 态流水**：`R0` 读上一行 → `R1` 读当前行 → `R2` 读下一行 → `EMIT` 三行齐后移列并输出。
在 `R1`/`R2` 拍把晚一拍的 `rd_data` 分别捕获成 `d_top` / `d_mid`，`EMIT` 拍的 `rd_data` 就是下一行 —— 三行严格对齐。
**端口未变**，`img_proc_axis` 一行都不用改。

### 11.3 验证（三级，全过）

1. **xsim 仿真**（`sim_gfs/tb_gfs.v`）：8×8 图、像素值 = `row*W+col`，逐像素比对
   `win_mid_c / top_c / bot_c / mid_l / mid_r`（含边界复制）：
   ```
   outputs=64 errors=0
   TB_PASS
   ```
2. **重出比特流**：0 error / 0 critical warning，**WNS +3.705 / WHS +0.033 ns**，LUT 1246、BRAM 64/216、DSP 1。
3. **PC 上位机程序端到端**（用 `tcp_image_ui` 的 `proto`/`imageio`/`imagelink`；板端服务开 `-x 0xA0000000` 走 PS→PL 通路）：

```
proto self-check: OK
checker   mode=0  rows_ok=512 crc_err=0  round-trip=232.5 ms  ->  PASS   (逐字节原样返回)
checker   mode=1  rows_ok=512 crc_err=0  round-trip=230.2 ms  ->  PASS
checker   mode=5  rows_ok=512 crc_err=0  round-trip= 29.4 ms  ->  PASS   (原图直通, 不走 PL)
gradient  mode=0  rows_ok=512 crc_err=0  round-trip=229.6 ms  ->  PASS   (与独立复算的期望灰度图逐字节一致)
4/4 PASS
```

- **checker 是纯灰(0/255)图**，mode0(归一化)/mode1(阈值) 都应**逐字节原样**返回 —— 只要还有
  "下移一行 / 左右错一列"，这两个比对必然失败。这里是 4/4 PASS。
- 另用程序自带 CLI `imagelink.py … --save xxx.png` 存回图，并核对 PNG 像素：
  **512×512 里 0 个像素**偏离标准 32×32 棋盘（`first difference: None`）。
- 模式 5（29 ms）与模式 0/1（230 ms）的耗时差本身也证明这次**真的走了 PL**（不再是纯回显）。
- 板端日志确认通路就位：`[axil] register file at 0xA0000000, VERSION ok`。

### 11.4 顺带踩到并记下的一个**构建坑**（很重要）

改完 `rtl/gray_frame_store.v` 重跑综合后，**比特流一个字都没变**（.bin md5 前后相同）。
原因：该模块在 BD 里是 **module reference**（`img_proc_axil_0`），它有自己的 **OOC 综合 run**
（`system_img_proc_axil_0_0_synth_1`，上次跑在 16:58）；**只 `reset_run synth_1` 不会重置它**，
Vivado 认为它"还新鲜"，于是继续用旧的模块网表。
→ **改 RTL 后必须连这个 OOC run 一起 `reset_run`**（本次 reset 后 OOC 重跑，比特流才真正变，md5 改变）。
以后凡"RTL 改了但比特流没变"，先查这里。

---

## 12. 优化：减少 PS↔PL 事务 + 提频（2026-10-01 第五轮）

### 12.1 先算账

一轮 ~230 ms 里：PS 推 262144 像素 ~101 ms、拉 ~94 ms、PL 内部读出 ~10 ms、TCP ~25 ms。
大头是 **PS↔PL 的寄存器往返（~195 ms）**，每次 32bit 访存约 **183 ns**（≈18 个 pl_clk0 周期）。

### 12.2 改动①：不再每像素读 STATUS —— **实测 2.3x**

板端 `src/axilproc.c` 原来每个像素都先读一次 STATUS 再访存（**2 次 AXI 事务/像素**）。
其实 **AXI 写 IN_PIX 只有在 PL 真的收下时才会完成（否则背压住 CPU）**，读 OUT_PIX 同理；
而 PL 产出一个像素只要 4 个周期、比 PS 访存快得多，所以完全可以"盲"访存。
改成**每 64 个像素才复核一次 STATUS**（保留卡死检测）→ AXI 事务数 ≈ 减半。

实测（PC 上位机 `imagelink` CLI，checker / mode 0）：

| | 优化前 | 优化后 |
| --- | --- | --- |
| 往返耗时 | 230 ms | **98.4 ms**（3 次：100.6 / 97.6 / 97.0） |
| 正确性 | — | **checker mode0/1 逐字节原样、gradient mode0 与独立复算一致 → 4/4 PASS** |

只需重编板端 C、**不用重出 FPGA**。host 功能测试仍 `TOTAL FAILURES: 0`。

### 12.3 改动②：提频 100 → 148 MHz（BD 已改、时序收敛）

BD 里 PL0 请求 150 MHz（实际 **148.15 MHz**，div=9）。整设计重跑（含 module OOC）：
**0 error / 0 CW，WNS +1.202 ns / WHS +0.010 ns，0 失败端点**（周期 6.75 ns）。
关键路径已被工具从"BRAM 深度级联"换成较短的一条（5.55 ns）。

### 12.4 ⚠ 但上板实测**没有加速**：PS 时钟不由 bitstream 决定

改完上板，往返仍是 **99.5 ms**（与 100 MHz 时基本相同）。查板上实际时钟：

```
pl0_ref = 101375992 Hz      ← 仍是 101.4 MHz
```

根因：**ZynqMP 的 PL 时钟（CRL_APB `PL0_REF_CTRL`）是 FSBL/boot.bin 在启动时配置的**，
**加载我们的 `.bin` 根本不会改它**。板子跑的还是原厂 boot.bin 的 PS 配置 → 频率没变。
（顺带：现在的设计"按 148 MHz 约束、实际跑 101 MHz"，**只会更宽松，不会出问题**。）

→ 想真正吃到频率只有两条路：① 改 FSBL/boot.bin（之前试过、把板子搞砖过，见 `boot/recovery_guide.md`）；
② **运行时直接写 CRL_APB 寄存器改分频**（有挂死风险，需断电重启恢复）。两条都未实施。

### 12.5 还剩一个更大的杠杆：一次搬多个像素

现在每次 32bit 访存只搬 **1 个像素**。把接口改成**一次搬 4 个像素**
（PS 先算灰度 → 4 字节打包成一个 32bit 写；读同理，4 个输出灰度拼成一个 32bit），
事务数直接 /4 → 往返估计 **~25–30 ms**（相对最初 230 ms 约 8–9x）。
代价：改 `img_proc_axil.v`（拆包/打包）+ 板端 C。（**`img_proc_axis` 不用改** ——
喂 `{0,g,g,g}` 走它的灰度公式 `(77g+150g+29g)>>8 = g`，正好还原成 g。）

### 12.6 本轮板上现状

- 板端 `tcp-image-server` = **无轮询版**（往返 ~98 ms），服务参数 `-x 0xA0000000` 走 PS→PL。
- PL = 新比特流 `img_proc_axil.bin`（md5 `d571d1d0…`，按 148 MHz 约束、实际跑 101 MHz）。
- 正确性复验：PC 上位机 **4/4 PASS**。

---

## 13. 再加宽：一次搬 4 个像素（2026-10-01 第六轮，**实测 1.8x，累计 4.3x**）

### 13.1 改法

把 PS↔PL 接口从"一次 1 个像素"改成"**一次 4 个像素**"：

- **`img_proc_axil.v`**：`IN_PIX` 一个 32bit 字 = **4 个灰度**（byte0..byte3），内部拆包依次喂给
  `img_proc_axis`；`OUT_PIX` 一个 32bit 字 = **4 个输出像素**（各取低 8bit），内部把 4 个输出攒满才置 `out_full`。
  `VERSION` 升到 **0x494D_5002**。**`img_proc_axis` / `gray_frame_store` 一行都没改。**
- **板端 `src/axilproc.c`**：PS 侧按 `(77R+150G+29B)>>8` 先算好灰度再打包（与 PL 里公式一致）；
  读回时把 4 个灰度展开成 RGB。
- 灰度搬到 PS 侧算**不影响结果**：PL 里喂 `{8'h00,g,g,g}` 走同一条灰度公式，`(77g+150g+29g)>>8 = g` 正好还原。

### 13.2 实测

板上 `axil_test`（VERSION 0x494D5002）：

```
push 65536 words (262144 px) :  6133 us ( 94 ns/word)   ← 原 386 ns/像素 × 262144 = 101 ms
pull 65536 words (262144 px) : 12292 us (188 ns/word)
mode0 check: mismatch = 0 / 262144                       ← 逐像素全对
STATUS min=0 max=255
```

PC 上位机（`tcp_image_ui`）：

```
checker  mode=0   53-56 ms   PASS (逐字节原样)
checker  mode=1   54.5 ms    PASS
checker  mode=5   27.8 ms    PASS (直通, 不走 PL)
gradient mode=0   53.9 ms    PASS (与独立复算的期望灰度图一致)
```

**5 次平均 53.9 ms**（rows_ok=512 / crc_err=0）。

### 13.3 加速总账

| 阶段 | 往返 | 相对最初 |
| --- | --- | --- |
| 最初（每像素轮询 + 1 像素/次） | 230 ms | 1.0x |
| ① 去掉每像素 STATUS 轮询（§12.2） | 98.4 ms | 2.3x |
| ② 再加宽成 4 像素/次（§13） | **53.9 ms** | **4.3x** |

分解：TCP 往返 ~28 ms（mode5 实测）+ 推 6 ms + 拉 12 ms + 其它 ~8 ms。

### 13.4 剩下的瓶颈

1. **TCP 往返 ~28 ms** 已是最大单项 —— 再快得动协议（流水化 / 少一次往返）。
2. **拉 12 ms 受 PL 读出速率限制**（1 像素 / 4 周期 → 每字 160 ns）；读出压到 2 周期/像素可再省 ~6 ms
   （**这里提频才会真正有用**）。
3. 推 6 ms 已接近 AXI 访存极限（94 ns/字）。

### 13.5 产物

- 新比特流 `bitstream/img_proc_axil.{bit,bin}`（bin md5 `7caec722…`）；
  时序 **WNS +1.209 / WHS +0.018 ns**，0 error / 0 CW，LUT 1162、BRAM 64/216。
- 板上自测工具 `test/img_proc_axil_test.c` 已同步到新协议。
- 新备份 `_backup/Tcp_Image_Rx_SNAPSHOT_20261001_191913.tar.gz`（+ 刷新过的 MANIFEST）。

---

## 14. 修 bug：伪彩模式变成黑白（2026-10-01 第七轮）

### 14.1 现象与根因

上一步"一次搬 4 个像素"之后，**mode 3(伪彩色映射) / mode 4(组合) 出来的全是黑白**。
实测 `非灰度像素 = 0 / 262144`。

根因：4 像素打包时每个像素**只取了低字节** `m_axis_tdata[7:0]`：

- 灰度模式输出是 `{8'h00, v, v, v}` → 低字节就是灰度值，**没问题**；
- **伪彩模式输出是 `{8'h00, R, G, B}` 真彩色** → 只取低字节 = **只拿了蓝通道**，
  整幅退化成单通道灰度 → 看上去就是黑白。

（这是本轮加宽引入的**回归**；加宽之前是整像素传输，伪彩是好的。）

### 14.2 修法

**打包随模式变**：

- **灰度模式(0/1/2)**：仍然 4 个像素/字（每像素取低字节）；
- **伪彩模式(3/4)**：一个像素一个 32bit 字，直接传完整的 `{8'h00,R,G,B}`。

RTL 里加 `wire color_out = (ctrl_r[2:0]==3'd3) || (ctrl_r[2:0]==3'd4);`，输出打包分两支；
板端 `axilproc.c` 的拉取按 `AXIL_MODE_IS_COLOR(mode)` 分支（灰度读 65536 字 / 伪彩读 262144 字）。
`VERSION` 升到 **0x494D_5003**（协议变了，让板端能立刻发现不匹配）。

### 14.3 验证

```
mode 0: 非灰度像素 =      0 / 262144    （本来就该是灰度 ✓）
mode 3: 非灰度像素 = 262144 / 262144    （彩色恢复 ✓）
   px 0: R=0 G=0 B=184(深蓝)   px 60000: R=0 G=121 B=255(青)   px 200000: R=255 G=193 B=0(橙)
mode 4: 非灰度像素 = 262144 / 262144    （彩色恢复 ✓）
```

回归：checker mode0/1 逐字节原样、gradient mode0 与独立复算一致 → **4/4 PASS**（未回归）。
彩条图 mode3 存成 `verify_bars_mode3_pcolor.png`，肉眼即标准 jet 喷色（橙→黄→绿→青→蓝）。

### 14.4 代价（如实说）

伪彩模式一次只搬 1 个像素，读回要 262144 次（灰度模式只要 65536 次），
所以 **mode 3/4 ≈ 91 ms**，比灰度模式的 ~56 ms 慢。
（单次读 ~188 ns，比写的 ~94 ns 慢一倍 —— AXI 读要 AR→R 一个来回。）

### 14.5 产物

- 新比特流 `bitstream/img_proc_axil.{bit,bin}`（bin md5 `9b55db0c…`）；
  时序 **WNS +1.088 / WHS +0.032 ns**，0 error / 0 CW。
- 样图 `verify_bars_mode3_pcolor.png`、`verify_gradient_mode3_pcolor.png`。
- 板上自测工具 VERSION 同步到 0x494D5003。
