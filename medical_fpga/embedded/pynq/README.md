# PYNQ 板端调度程序（C 负责）

在 FZ3B 的 ARM 上用 Python 驱动 [vivado/](../../vivado/README.md) 生成的 U-Net 加速器：按 `network.txt` 逐层写寄存器、启动、等待完成，再取回血管掩膜。

## 文件

| 文件 | 在哪运行 | 作用 |
|---|---|---|
| `unet_fpga.py` | 板上 / 电脑 | 驱动库：解析层表，分配并复用连续内存，写寄存器，提供 `run()` 和 `segment()` |
| `smoke_test.py` | 板上 / 电脑 | 分步冒烟测试：文件完整性、加载、控制通路、握手、HP0/HP1 读写 DDR，遇到第一个失败就停 |
| `run_board_test.py` | 板上 / 电脑 | 上板验证：im0002 逐节点比对、20 张图最终输出比对、逐层计时 |
| `sim_device.py` | 仅电脑 | 模拟两个 IP 的寄存器和物理内存，调用 NumPy 参考计算，用于在没有板子时测试驱动 |
| `package_for_board.py` | 仅电脑 | 生成上板包 `board_package/`（约 70 MB），并记录每个文件的 SHA-256 |

## 电脑上已完成的验证（2026-09-23）

```powershell
Set-Location E:\PLD\medical_fpga\embedded\pynq
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' .\package_for_board.py
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' .\run_board_test.py --sim --package board_package
```

在模拟设备上，im0002 的 30 个节点与 golden 全部一致，20 张图的最终输出与 lwnet 全部一致（`sim_results.json`）。这说明驱动的寄存器偏移、有符号数写法、64 位地址、数据打包和缓冲区复用都与 HLS 参考实现一致。

它**不能**证明硬件在板上正确，也不代表速度：模拟计时是电脑上 NumPy 的时间。

内存：30 个张量复用到 13 块连续缓冲区，共 34.5 MiB；参数另占约 88 KB。

## 上板步骤

1. **准备 PYNQ 镜像**：`edgeboard/README.md` 中提供了社区制作的 FZ3B PYNQ v2.7 镜像下载链接（阿里云盘，约 10 GB）。这是个人维护的第三方镜像，不是官方发布。用 balenaEtcher 等工具写入 SD 卡（建议 16 GB 以上）。
2. **从 SD 卡启动**：断电后把启动模式拨码开关 **SW1** 拨到 SD 卡启动：开关 4、3、2、1 依次为 **ON、OFF、ON、OFF**（MODE=0101）。这组位置来自百度在线版 FZ3B 硬件手册（https://ai.baidu.com/ai-doc/HWCE/nkq9b5ncu）；eMMC 启动为 ON、OFF、OFF、ON，JTAG 为全 ON。拨之前请对照板上丝印，并记下原来的位置，方便以后切回原系统。注意社区 `board.xml` 里写的是 SW3 和另一套编码，与官方手册不一致，以官方手册为准。仓库里的 PDF 手册有密码保护，无法直接查阅。板子原来的 Ubuntu 如果装在 eMMC 上不会被覆盖；如果原来也是从 SD 卡启动，请换一张卡。
3. **联网登录**：插网线，用浏览器打开 `http://<板子IP>:9090` 进入 Jupyter。PYNQ 镜像通常的默认账号是 xilinx，以镜像说明为准。
4. **拷贝上板包**：把整个 `board_package/` 拷到板上，例如 `/home/xilinx/jupyter_notebooks/unet/`。可以用 Samba 共享（`\\<板子IP>\xilinx`）、`scp`，或者在 Jupyter 页面上传。
5. **分步冒烟测试**：在 Jupyter 的 Terminal 中执行：
   ```bash
   cd /home/xilinx/jupyter_notebooks/unet
   sudo python3 smoke_test.py
   ```
   共 6 步，遇到第一个失败就停下，失败的步骤能说明问题出在哪一层：
   1. 文件完整性：失败说明拷贝过程出了问题；
   2. 加载 bitstream，检查两个 IP 的地址和 PL 时钟；
   3. 两个 IP 都处于空闲：检查控制通路；
   4. 非法配置返回 1：验证启动、完成、返回值这套握手，不访问 DDR；
   5. 小卷积经 HP0 读写 DDR：检查物理地址和缓存处理；
   6. 小 Add 经 HP1 读写 DDR。

   PYNQ 加载 bitstream 需要 root 权限；Jupyter 自带的终端通常已经是 root。
6. **完整验证**：冒烟测试通过后再执行 `sudo python3 run_board_test.py`，这一步约需几分钟。
7. **带回结果**：把 `results/board_results.json` 和终端输出带回电脑，其中包含逐节点比对结果、每层耗时和 20 张图的比对结果。

正确的输出应该类似：`PASS im0002 node ...` 共 30 行，`PASS imXXXX` 共 20 行，最后一行为 `SUMMARY PASS`。

## 常见问题排查

| 现象 | 可能原因 |
|---|---|
| `Overlay` 加载失败，提示器件或 IDCODE 不匹配 | bitstream 按 xazu3eg 生成，而 PYNQ 镜像的基础设计是 xczu3eg。两者通常 IDCODE 相同，但**尚未在板上验证**。如果确实不匹配，需要把 `vivado/create_system.tcl` 和两个 HLS 工程的 part 改为 xczu3eg，重新导出 IP 并重建 |
| 第一个 CONV 节点就超时 | PL 时钟或 HP 端口没有正确配置；可以先查看 `ol.ip_dict` 和时钟设置 |
| 返回值为 1（配置被拒绝） | 层表和 IP 版本不一致，或者寄存器偏移不对。重新打包，并对照 `vivado/README.md` 中的寄存器表 |
| 部分节点不一致 | 先看第一个出错的节点。只有少量像素不同，多半是缓存没有刷新或失效；整层都不对，多半是地址或数据排列问题 |
| `allocate` 失败 | CMA 连续内存不足（需要约 35 MiB），检查镜像的 CMA 大小 |

## 在自己的程序里调用

```python
from unet_fpga import PynqDevice, UNetAccelerator
acc = UNetAccelerator(PynqDevice('unet_system.bit'), '.')
logits, mask = acc.segment(rgb)   # rgb: uint8 (512, 512, 3)；mask: bool (512, 512)
print(acc.last_timings)           # 每层耗时（秒）
```

输入必须已经缩放到 512×512 RGB。lwnet 的做法是用 PIL 双线性缩放；如果板上的 Pillow 版本不同，缩放结果可能有细微差异，所以 `run_board_test.py` 直接使用打包好的输入，保证逐值可比。

仅用于工程教学与算法演示，不作为临床诊断依据。
