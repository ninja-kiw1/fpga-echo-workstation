# plbram 内核模块 —— 把 PL 的 BRAM 以 Normal-NC 映射给用户态

> 2026-10-02。解决"PS 访问 PL BRAM 每次 198 ns"这件事（其中约 103 ns 是
> `/dev/mem` 的 Device 映射语义造成的）。

## 1. 为什么

板上的实测（`ai/net/_memtest.py` / `_memtest2.py`，同代码同样 16 B 步长）：

| 目标 | ns/次 | MB/s |
| --- | --- | --- |
| PL 张量 BRAM `0xB0000000`（`/dev/mem`） | **197.7** | 80.9 |
| PS 自己的 DDR `0x40000000`（同样 `/dev/mem`） | **102.9** | 155.5 |
| 普通可缓存内存（malloc） | **4.2** | 3810 |

⇒ 198 ns 里 **~103 ns 纯粹是 Device 映射语义**（强序、不可缓存），另外 ~95 ns 是
PL 通路。把 BRAM 换成 **Normal-NC** 映射后，CPU 可以有多笔未完成访问、允许合并，
读应当显著变快；而且**不缓存 ⇒ 不需要任何 cache 维护**，跟 PL 不存在一致性问题。

**预期**：搬运（读 1112 ms + 写 288 ms）可能降到几百 ms —— 整帧 1950 ms 有望降到
~1200 ms 量级（**先测，不预设**）。

> **实测结论（2026-10-02，见 §6）：只有"写"变快了，读一点没变。整帧 −9.4%。**

## 2. 现状：代码写好了，只缺内核源码树

* `plbram.c` —— 模块（~90 行）：`misc_register` 出 `/dev/plbram`，
  mmap 时把 vma 的 `vm_page_prot` 换成 `pgprot_writecombine()`（= Normal-NC），
  再 `remap_pfn_range()`。窗口由模块参数 `base`/`size` 决定（默认 `0xB0000000` / 256 KB）。
* `makefile` —— kbuild 的三行。
* `build_on_board.sh` —— 在板上原生编译（**板载 GCC 7.5.0 正好配 4.14 内核**，
  完全避开工具链不匹配，也不需要交叉编译）。
* `../ai/net/_plbram_bench.c` / `_plbram_bench.py` —— 用新映射重跑那套基准，
  直接和 `/dev/mem` 的数字对比。

**唯一缺的东西：`/root/linux-xlnx`（内核源码树）。** 板上没有 `kernel build` 目录，
本地工作区也没有源码。

## 3. 需要你做的：拿到内核源码

运行中的内核是 **`4.14.0-xilinx-v2018.3`**（`uname -r`；`CONFIG_LOCALVERSION="-xilinx-v2018.3"`），
板上 `CONFIG_MODULES=y`、**`CONFIG_MODVERSIONS` 未开**、**`CONFIG_MODULE_SIG` 未开**
—— 所以编第三方模块很容易（不需要 `Module.symvers`、不需要签名）。

源码 = Xilinx 的 **linux-xlnx** 树、tag **`xlnx_rebase_v4.14_2018.3`**（已用 GitHub API
核对存在，SHA `cc5acc7c6c9681031b78811c14f1820f3d6892d0`）。任选其一：

* **一键脚本（推荐）**：双击 `E:\fpga\my_project\_kernel_src\get_linux_xlnx.bat`
  —— 用 Windows 自带的 `curl.exe` + `tar.exe` 下载并解到 `E:\fpga\my_project\_kernel_src\`。
* 有 git：`git clone --depth 1 -b xlnx_rebase_v4.14_2018.3 https://github.com/Xilinx/linux-xlnx.git`
* 或直接下 tarball：
  `https://github.com/Xilinx/linux-xlnx/archive/refs/tags/xlnx_rebase_v4.14_2018.3.tar.gz`

> 用 **tarball**（而不是 git clone）有个好处：包里**没有 `.git`**，
> `LOCALVERSION_AUTO` 自然是空的 → 版本号正好是 `4.14.0-xilinx-v2018.3`。

> **怎么确认拿对了**：解开后 `make ARCH=arm64 kernelrelease` 必须打印
> **`4.14.0-xilinx-v2018.3`**（注意：解出来的目录里**不要有 `.git`**，
> 否则 `LOCALVERSION_AUTO` 会加 `-g<hash>` 后缀，vermagic 就对不上了）。
> 版本号不对的话，模块会以 "version magic ... should be ..." 拒绝加载 —— 那时再来处理。

拿到之后**放到板上** `~/linux-xlnx`（约 900 MB；rootfs 还剩 2.4 GB），
或者放进工作区我来传。

## 4. 拿到源码后我会做的

```
# 板上（build_on_board.sh 已写好）
zcat /proc/config.gz > ~/linux-xlnx/.config
make -C ~/linux-xlnx ARCH=arm64 olddefconfig
make -C ~/linux-xlnx ARCH=arm64 modules_prepare        # 第一次约 10–30 min (4×A53)
make -C ~/linux-xlnx M=~/tcp_image_rx/kmod modules     # 出 plbram.ko
insmod ~/tcp_image_rx/kmod/plbram.ko
```

然后：
1. **基准对比**：`_plbram_bench`（走 `/dev/plbram`）vs `_memtest2`（走 `/dev/mem`），
   看每次读是不是从 197.7 ns 掉下来。
2. **如果有效**，把 `src/unet_ai.c` 的 BRAM 访问切到 `/dev/plbram`（一处 `mmap` 改动），
   重跑 `run_fullres.py` 确认 golden 仍逐值一致，并记录新耗时。
3. 生效的话再回头看 **A-ov（重叠）能不能顺带复活**（`_unet_ai_aov_overlap_reference.c.txt`）。

## 5. 风险与回退

* 模块只做 mmap，不碰寄存器 → 风险很低。
* 万一 `insmod` 后系统异常：`rmmod plbram` 即可；真挂了用 JTAG 恢复
  （`progress_and_handoff.md` 里有 xsdb 复位流程）。
* **不需要重出比特流**，也不动 Linux 系统/boot.bin。
* 回退：不用这个映射即可，`/dev/mem` 的老路一行没改。

## 6. 实测结果（2026-10-02，已完成）

**三个过程中踩到的坑**：

1. **板上没有 `bc`** —— Kbuild 用它生成 `include/generated/timeconst.h`
   （`echo 250 | bc -q kernel/time/timeconst.bc`），于是 `make modules_prepare` 直接
   `bc: not found` 挂掉，而板子没外网装不了。→ 写了 `kmod/bc`：**把 timeconst.bc 的
   gcd/fmul/fadj/fmuls 算法原样用 Python 实现**（带数值自检：对 msec/usec 抽样验证
   `(n*MUL+ADJ)>>SHR == n*UNIT/hz`），装到 `/usr/local/bin/bc`。**内核树一行没改。**
2. **4.14 里没有 `misc_unregister`** —— 是 `misc_deregister`。
3. 其余顺利：板上 gcc 7.5.0 原生编译，`vermagic=4.14.0-xilinx-v2018.3 SMP mod_unload
   aarch64` 与运行内核**完全一致**。

**基准（`_plbram_bench.c`，同代码、同 16 B 步长）**：

| 目标 | 操作 | `/dev/mem` (Device) | `/dev/plbram` (Normal-NC) |
| --- | --- | --- | --- |
| **PL BRAM** `0xB0` | 读 | 197.8 ns/字 | **197.7 ns/字（没变）** |
| **PL BRAM** `0xB0` | 写 | 39.5 ns/字 | **14.8 ns/字（2.67×）** |
| PS DDR `0x40` | 读 | 102.7 ns/字 | **102.9 ns/字（没变）** |
| PS DDR `0x40` | 写 | 9.4 ns/字 | **3.2 ns/字（2.9×）** |

**读一点没变——连 PS 自己的 DDR 也一样。** ⇒ 这颗 CPU 的"非缓存读"就是一次完整
往返、不流水，**与映射属性无关**；Normal-NC 只对"写"有效（写合并/写缓冲）。

**整帧**：512 **1950.9 → 1767.9 ms（−9.4%）**，put 287.9→107.9 ms，读/ap 不变；
**golden 仍 0/262144 PASS**。服务端到端 `ai_us` 1948708 → **1765383**、
掩膜 `vessels=35257/262144` 与改前**完全一致**。32 档 8.88 → 7.98 ms。

⚠ **写合并的正确性保护（别删）**：`bram_commit()` —— Normal-NC 下写可能还在 CPU
写缓冲里，而 `AP_START` 走**另一个口**（HPM0 控制口），两条路无顺序保证；
所以组装完要**读回程序顺序里最后一个写入字**，靠"同 master 对同从机的写保序"
确保它之前的写都已到 BRAM。

**⛔ 顺带重试 A-ov（重叠）：仍然坏，且与映射无关。** 用新映射跑，失配数
**253787/262144** 与用 `/dev/mem` 时**一模一样**；同布局只关重叠则 PASS。
⇒ **并发访问的破坏在 PL 互联/控制器那一层**，本模块救不了它。

**部署**：`plbram.ko` 已放进 `/lib/modules/<ver>/extra/`，开机自加载
（`/etc/systemd/system/plbram.service`，enabled）。驱动侧优先 `/dev/plbram`、
没有就自动退回 `/dev/mem`（`UNET_BRAM_MEM=1` 强制走 mem）——**模块不在也不影响功能**。
