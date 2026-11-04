# 板子起不来了 —— 恢复与现状说明

> 2026-10-01。把自建 boot.bin 刷进 SD 卡启动分区并 `reboot` 后，**板子没有起来**
> （10 分钟 ping 一直 `Destination host unreachable`，SSH 22 端口也不通）。

## 一、怎么恢复（需要动手拔卡）

板子启动分区是 **SD 卡 = mmcblk1**，卡槽在**背面**（正面卡槽是 FZ3A 的）。
boot.bin 在启动分区（FAT）根目录。**卡上已经留好了两份原厂镜像**：

```
/run/media/mmcblk1p1/
├── boot.bin          6060800  ← 自建的（起不来）
├── boot.bin.old      6650296  ← 原厂（覆盖前的副本）
└── boot.bin.vendor   6650296  ← 原厂（专门留的备份）
```

PC 上也有一份：`E:\fpga\my_project\tcp_image_rx\boot\orig_BOOT.BIN`
（6 650 296 B，md5 `cc316f785c3b40f46caef59e569bc988`）。

**恢复步骤**

1. 断电，拔出 SD 卡，插到电脑读卡器。
2. 打开卡上的 FAT 启动分区，把 `boot.bin` **删掉或改名**，
   再把 `boot.bin.vendor` **复制一份改名为 `boot.bin`**。
   （Windows 上这一步就是复制粘贴 + 重命名，不需要任何工具。）
3. 安全弹出，插回板子，上电。
4. 起来后（约 1 分钟）确认：
   ```
   ssh root@192.168.1.254        # 密码 root
   md5sum /run/media/mmcblk1p1/boot.bin     # 应为 cc316f785c3b40f46caef59e569bc988
   ```

> 提前做好这一步的原因就是自建 boot.bin 有刷砖风险 —— 用户明确接受过。

## 二、这次到底完成了什么（都没丢）

| 产物 | 位置 | 状态 |
| --- | --- | --- |
| 全 FZ3B PS 配置 + S_AXI_HP0_FPD 的比特流 | `bitstream/boot_design.bin` | ✅ 时序收敛（WNS +4.085ns） |
| 对应 XSA | `bitstream/boot_design.xsa` | ✅ |
| 自建 FSBL | `boot/parts/fsbl.elf` | ✅ ELF64/AArch64，entry 0xFFFC0000 |
| 自建 PMUFW | `boot/parts/pmufw.elf` | ✅ ELF32/MicroBlaze，entry 0xFFDC0050 |
| 抽取的 ATF / U-Boot | `boot/parts/bl31.elf`、`u-boot.elf` | ✅ 与原厂一致 |
| 自建 boot.bin | `boot/parts/boot.bin` | ✅ 6 060 800 B，结构对齐原厂 |
| 板端程序（含模式 5 直通） | `src/` | ✅ 交叉编译 0 warning，主机测试 15/15 |
| PC 端 6 档模式 | `tcp_image_ui/` | ✅ 自检通过 |
| 编译脚本与工具链 | `boot/manual_build_{fsbl,pmufw}.py` 等 | ✅ 可复现 |

## 三、下一步可以往哪查

1. **为什么起不来**：没有串口线只能盲改。可选
   - 接串口（板上有 mini-USB CP2102 口）看 FSBL 打到哪一步；
   - 或用 **JTAG** 单步 FSBL；
   - 或**最小化改动**试：只把原厂 boot.bin 的 FSBL 分区替换成自建 FSBL，其余全保留，
     看是不是 PMUFW/bl31 版本不匹配的问题。
2. **怀疑点**：自建 FSBL/PMUFW 是 2025.2 工具链，而原厂 bl31/u-boot 是 2018.3/2021 时代，
   两者对 DDR/时钟/PM API 的假设可能不一致。
3. **更根本的问题**：`psu_init.c` 的 `psu_afi_config()` **不写 AFI_FM 寄存器**，
   所以"换 boot.bin ⇒ 打通 PS-PL 从口"这条推论本身还没被证实。
   建议先做个**最小 PL→PS 验证**（一个 BRAM + 一个从口）再决定要不要继续。
4. **零风险备选**：`bitstream/img_proc_axil.bin` + `rtl/img_proc_axil.v`
   （PS↔PL 寄存器通路，不碰 boot.bin），可以先拿到"FPGA 处理后的图回传 PC"这个结果。
