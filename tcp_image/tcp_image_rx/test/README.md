# test/ —— 接收路径的主机端功能测试

这个目录**不是板端运行需要的**，它是**验证手段**：在 PC 上驱动交付代码里那段"收图"
逻辑，确认协议解析、CRC 校验、行放置、统计都正确。

## 为什么可以这样测

`host_test_rx.c` 直接 `#include "../src/tcp_image_server.c"`。于是那个文件里 `static`
的 `receive_frame()` 在本文件里可见，可以**原样调用交付的那份代码——一行都没改**。
数据来源从真实 socket 换成内存里的字节流（文件里伪造的 `recv()`），
因而能精确模拟 TCP 的粘包、拆包、截断与失步。

`shim/` 是 MinGW 上缺失的 POSIX 头垫片（`netinet/`、`sys/socket.h`、`sys/mman.h`），
只为了让主程序在 Windows 主机上能编过。**Linux / 板端不需要这些垫片**——
在 Linux 上直接用真实系统头文件即可。

## 怎么跑

需要 `gcc`（MinGW）和 `python` 在 PATH 上。在 `test\` 目录里直接执行：

```
build_host_test.bat
```

或者手工两步：

```
gcc -std=c99 -Wall -Wextra -Wno-unused-parameter -D_GNU_SOURCE -D_POSIX_C_SOURCE=200809L -DSIGPIPE=13 -include shim/shim_extra.h -Ishim -I../src -o build/host_test_rx.exe host_test_rx.c ../src/crc16.c ../src/axidma.c ../src/axigpio.c
build\host_test_rx.exe
```

期望最后一行是 `=== TOTAL FAILURES: 0 ===`。

`expected.py` 用 Python 端（`../script/send_image.py` 的 CRC 实现）独立算出期望的
整幅 CRC，用来和 C 端结果对照：

```
python expected.py
```

## 覆盖了什么

正向——各种分块大小下都能收满整幅，且缓冲区与发送的原图**逐字节一致**：
分块 = 1 / 3 / 7 / 1000 / 1541 / 1542 / 4096 / 65536 字节，以及一次给完。

负向：

- 篡改第 100 行的 CRC 字段 → 返回 `-3`，`rows_ok=511`，`crc_err=1`；
- 把第 5 个包的"行号"改成非法值 → 流失步，返回 `-1`，`seq_err=1`；
- 字节流截断 100 字节 → 返回 `-1`。

另外抽查第 0 / 100 / 511 行的 CRC，确认"行确实放对了位置"（每行内容都不同，
放错位置一定会被发现）。

还核对**整幅 frame CRC16 = 0xC35A**（`receive_frame` 只在 `verbose` 时才顺便算它，
所以测试用 `verbose=1` 调它）。

**2026-10-01 新增**：像素格式转换往返 —— `expand_rgb888_to_ddr()` +
`pack_ddr_to_rgb888()`，786 432 → 1 048 576 → 786 432 必须逐字节还原，
且 DDR 首像素排布是 `{00,R,G,B}`。这两个函数正是板端在"接收 → DMA"和"DMA → 回发"
之间新加的两步，纯算法不碰硬件，所以在这里真跑。

> 2026-10-01 顺手修好了这个测试本身：`sigaction`（09-30 才引入）没垫片、
> 正例期望的 `frame_crc` 没开 `verbose`，导致它在 09-29 之后其实已经编不过/跑不过了。

## 板上测试（需要板子跑着 Linux）

板子能 ssh 之后，用这两个脚本做**真机端到端**测试（在 PC 上跑，通过 SSH 驱动板子）：

| 脚本 | 作用 |
| --- | --- |
| `board_e2e.py` | 板子用**真实 `/dev/mem` 路径**收图（`-a 0x40000000`），再用 mmap 读回缓冲区，**与 PC 实际发出的原图逐字节比对** |
| `board_multi.py` | 跑 `bars`/`checker`/`gradient` 三种图样，自动比对板上 `frame CRC16` 与 PC 端算出的期望值 |

用法（板子默认 `192.168.1.254`，`root`/`root`）：

```
python test/board_e2e.py
python test/board_multi.py
```

前置：源码已 scp 到板子并编译过。板上编译直接用自带 gcc：

```
scp -r src makefile root@<板子IP>:/root/tcp_image_rx/
ssh root@<板子IP> 'cd /root/tcp_image_rx && make clean >/dev/null; make CC=gcc'
```

> ⚠ 板子时钟停在 2021 年、比 PC 慢很多，`make` 会误判"无需重建"，
> 所以**必须带 `make clean`**。

## 不覆盖什么

- AXI DMA / AXI GPIO 的寄存器读写（`axidma.c` / `axigpio.c`）—— 需要真实 PL。
- PL 侧 AXI-Stream 握手、以及 `--echo` 回传"处理后的图"那条路径
  （行 CRC 重算那段）—— 要等 PL 侧设计定了才能验。
- 服务主循环（`main`）—— 它在测试里被 `#define main` 顶掉了，只当链接占位。
