# PL 优化指导（给有 Vivado 2025.2.1 的队友，2026-10-09）

目标：在**不改 PS 侧驱动接口、不改模型算法**的前提下，让同一套 U-Net 张量引擎在 PL 上更快。
任何改动都必须保证**所有模型逐位结果不变**（6 个模型都有逐位参考，见第五节的验收门槛）。

> **2026-10-11 进展：P1 已完成并上板验收、已合并。** 板上固件现在是 `ai_sys_xu2dp.bin`（`8f8c27fe…`），
> 17 个程序全部逐位一致、5000 帧稳定性通过。同一测试程序实测：512 档 `ap / put / get / 整网` =
> 356.9 / 107.7 / 90.0 / 566.3 → **306.9 / 75.2 / 79.6 / 473.0 ms（−16.5%）**，128 档 37.8 → **32.0 ms（−15%）**。
> 比预计的 8% 多，是因为双口 BRAM 顺带让 PS 写 BRAM 快了 1.5 倍（`put` −32 ms）。
> 证据：`E:/PLD/board_acceptance_xu2dp_20261011/验收报告.md`；记录：`progress_and_handoff.md` §9.49。
> 下一步是 **P2（eltwise）**，其次 P3（提频，按新的 `ap` 约省 98 ms）。后续构建以交付包里改过路径的脚本为准。
>
> **2026-10-12 进展：P2 也完成了（在我们这台电脑上做的），已上板验收、已部署。** 你改过路径的 60 个脚本已合并进主工程
> （`run.bat` 和 `deploy_board_v5.py` 没合），本机工具在 `E:\AMD\Xilinx\2025.2`。板上固件现在是 `ai_sys_xu2dp_e2b.bin`（`3bbd9396…`）。
> 收益很小：`ap` 306.9 → 304.5 ms，BRAM 192 → 188。两个发现对你有用：
> ① 双口 BD 本身已经让 Add 降到读口下限（0.65×），eltwise 能动的只剩 Concat / MaxPool；
> ② eltwise 合并循环里，`gmem_a` / `gmem_b` 默认 16 个在途突发会让两口数据在互连上交替到达、拖慢先消费的那一口，
> 要把 `num_read_outstanding` 限到 2（协同仿真看不出这个问题）。细节：`progress_and_handoff.md` §9.50、`ai/opt_hls_elt/README.md`。
> **下一步只剩 P3（提频）**：按 `ap` 304.5 ms 估约省 96 ms。
> 下面“板端现状”是 10-09 写的，保留作对照。

板端现状（v5.3，比特流 `ai_sys.bin` = 交付版 V9b，conv XU=2）：
- 512 档整网约 564 ms，其中 `ap`（PL 计算）≈354 ms、`put`（PS 写 BRAM）≈108 ms、`get`（GDMA 读回）≈125 ms；
- 128 档（心超、皮肤）整网 37.7 ms。

细节都在 `progress_and_handoff.md` §四、§9.18、§9.26–9.29，以及 `ai/opt_hls/README.md`、`docs/ai_full_resolution_strip_plan.md`。

---

## 一、做什么（按“收益 / 风险”排序）

| 顺位 | 项 | 预期收益（交接文档估计） | 风险 | 前置 |
|---|---|---|---|---|
| **P1** | **V12 + 真双口张量 BRAM** | `ap` 354 → 约 309 ms（−45 ms，约 8%） | 中：BRAM 块数不变（RAMB36 本身就支持双口），但 BD 改动会重走 PS 侧通路 | 代码、IP、BD 脚本都已就绪 |
| P2 | eltwise IP 同样改成合并搬运 | `ap` 里 eltwise 约占 49 ms，能省一部分 | 中 | P1 先落地 |
| P3 | PL 时钟 101 → 148 MHz | 约 −112 ms（约 19%，最大的一项） | **高**：PL 时钟由 FSBL / boot.bin 设定，只加载 `.bit` 改不了（10-01 试过，无效） | P1 落地，148 MHz 下时序收敛，SD 卡有完整备份 |
| P4 | `in`/`out` 加宽到 256 bit | 理论上搬运再减半 | 高：BD、PS 张量布局、互联位宽都要改（历史上位宽转换器丢过写数据） | 单独立项，并和我沟通 PS 侧的改动 |

**明确不要做（都已实测否决，理由见交接文档“⛔ 明确不建议做”）**：
- XU=3；
- 给 `rows` 加行槽；
- 把 m_axi 折进计算流水线（V10、V11 都已否掉）；
- 搬运与计算重叠；
- 分组层间链式；
- **HLS int8 打包**：读的耗时只和访问次数有关，打包后读这一侧收益为 0；
- 可缓存映射；
- `put` 走 DMA；
- NEON 宽读。

---

## 二、P1 具体步骤

### 0. 准备
- 在**你自己的副本**上做（这台电脑上不是 git 仓库）。把 `E:\PLD\tcp_image\tcp_image_rx\ai\` 整个拷过去，再加上 `vivado\`、`bitstream\` 目录里需要的部分。
- ⚠ **陷阱**：`ai/build/conv_ip_xu2/` 里现在装的是 **V12**，不是交付版 V9b（V12 那次导出把它覆盖了，`csynth.rpt` 的时间戳是 `Tue Oct 6 23:45:55 2026`）。
  - 做 P1 时这正好是要的版本，**不用重新导出**；
  - 但如果你想重建 V9b 作对照，必须先把 `ai/opt_hls/src/conv_layer_v9.cpp.txt` 拷成 `src/conv_layer.cpp` 再导出 IP。
- 先确认 `csynth.rpt` 里有 V12 特有的 `Pipeline_MERGE`。

### 1. 修脚本
`ai/bd/build_ai_sys_xu2dp.tcl` 里**删掉** `CONFIG.Write_Depth_B {16384}` 这一行。
真双口模式下没有这个参数，它会留下一条 `CRITICAL WARNING [BD 41-1276]`，挡住 `generate_bitstream`。

### 2. 构建（后台跑，约 1–2 小时）
运行 `ai/bd/run_build_xu2dp.bat`。
- **以日志里出现 `EXITCODE 0` 为准**（`vivado/ai_sys_xu2dp_build.log`），**不要看 `.done` 文件**：旧的 `.done` 是更早一次运行留下的，并不说明这次成功。
- 产物应该有：`bitstream/ai_sys_xu2dp.{bit,bin}`、`vivado/ai_sys_xu2dp_util.rpt`、`vivado/ai_sys_xu2dp_timing.rpt`。

### 3. 构建后自查（不上板）
- **时序**：101.376 MHz 下 WNS ≥ 0、WHS ≥ 0，没有未约束路径的告警。
- **资源**：BRAM 仍是 **192/216**（双口不应多占块）；DSP、LUT 和交付版对比，变化写进报告。
- **双口确实生效**：实现日志里 BRAM 原语是 `TRUE_DP`。

### 4. 上板（**先约好时间段**，这期间我不跑板上测试）
板上比特流放在 `/lib/firmware/ai_sys.bin`，开机时由 `ai-sys-bitstream.service`（`/usr/local/sbin/load_ai_sys.sh`）烧进 PL，并读回 `0xA0000010` = `0x494D5003` 校验。

```sh
# 板上（root/root，192.168.1.254）
systemctl stop tcp-image-server                      # ⚠ 先停业务：IP 正在计算时重载 PL 会整板失联，只能断电
sha256sum /lib/firmware/ai_sys.bin                   # 记下旧哈希（现在是 4a744c89…）
cp -a /lib/firmware/ai_sys.bin /lib/firmware/ai_sys.bin.v9b_backup
cp ai_sys_xu2dp.bin /lib/firmware/ai_sys.bin && sync
reboot                                               # 用开机流程加载，不在运行中重载
# 开机后：
journalctl -u ai-sys-bitstream -b --no-pager | tail  # 应看到 VERSION 0x494D5003
systemctl is-active tcp-image-server
```

**回退**：`cp /lib/firmware/ai_sys.bin.v9b_backup /lib/firmware/ai_sys.bin && sync && reboot`。

---

## 三、P3（提频）的注意事项（先评估，再决定做不做）

- 先在实现里把约束改成 148 MHz，看**全部 IP**（conv、eltwise、img_proc 预处理）能否收敛；不收敛就到此为止。
- 时钟在 FSBL 里。可行的路径是重新生成带 148 MHz PS 配置的 boot.bin（工程里有 `boot/build_fsbl_bsp.bat`、`boot/deploy_bootbin.py`）。
  **boot.bin 坏了板子起不来**，必须：
  - 先完整备份 SD 卡；
  - 确认 SW1 拨码可以切到 SD 启动；
  - 知道 JTAG 在本机不稳定，指望不上。
- 运行时写 `CRL_APB` 改时钟有挂死板子的风险，不建议。

---

## 四、接口约定（保证我们两边能并行）

- **不变的东西**：IP 寄存器映射、张量 BRAM 地址窗口与布局、`network.txt` 和 `params/*.bin` 格式、模型库（`src/unet_models.c`）。
  P1 和 P3 按设计都不需要改 PS 侧代码。
- **归你的文件**：`ai/` 下的 HLS / BD / 构建脚本、`bitstream/`、`src/unet_ai.c`（PS 搬运驱动），以及 `kmod/`（如果要动 GDMA）。
  我这段时间**不改** `unet_ai.c`。
- **归我的文件**：`src/tcp_image_server.c`（协议、实时化流水线）、`src/ai_view.*`、模型库生成脚本，以及上位机 `tcp_image_ui/`。
- 如果 P2 或 P4 必须改 PS 侧张量布局，先告诉我。改动落在 `unet_ai.c` 里，我这边的验收脚本不用改。

---

## 五、验收门槛（全部通过才合并）

> **10-11 补充**：板端现在是 v5.8，模型库 17 个程序（0–16）。除下面 9 项外，还要过
> `E:\PLD\opcomp\verify_board_ops.py`（8 个算子 + 串联 / 分叉）、`E:\PLD\ct\verify_board_roi.py --fine roi_prior`（CT 两阶段）、
> `E:\PLD\echonet\verify_board_echonet.py --tag t3 --model 16`（EchoNet）。板子在我们这边，交回比特流后由我们跑全套；
> CT 和 EchoNet 的回归需要各自的数据（EchoNet 不能转发）。

上板后按顺序跑（脚本都在仓库里，PC 上用 `E:\PLD\skin\.venv\Scripts\python.exe`；界面脚本要先把 TCL / TK 路径固定到 srtp-vision 环境，见 `tcp_image_ui/run.bat`）：

1. `ai/net/run_fullres.py`：眼底 512 全分辨率与 HLS golden **0/262144**；
2. 板上 `/root/Fz3b_BoardCheck`（本机没有副本，在板子上编译运行）：31 项全 PASS；
3. `E:\PLD\echo\verify_board_echo.py`：心超 200 帧 + 三模型切换 60 帧；
4. `verify_board_inter.py --tag inter2_r128`：2400 个点击输入；
5. `verify_board_f3.py --tag seq_none_ex`：板上闭环 3716 步；
6. `verify_board_reg.py`：F5 配准，PL 的 |a−b| 和 PS 的分块和逐值一致；
7. `tcp_image_ui/verify_board_v4.py --output board_check_v5/fundus_regression_<版本>.json`：眼底 v4 客户端 75 帧；
8. `tcp_image_ui/verify_board_stability_v5.py --frames 5000 --out stability_<版本>.json`：5 个模型混合 5000 帧、内存不增长；
9. **性能对比**：同一张图、同一档位，记录 `ap / put / get / 整网`。服务端日志和 `Fz3b_BoardCheck` 的基准都有。
   另外重跑 `_memtest` 和 `_plbram_bench`，看读路径有没有变化（双口可能顺带影响 PS 侧通路）。

**要交回来的东西**：
- `ai_sys_xu2dp.bin`（以及 `.bit`）和 sha256；
- 构建日志（含 `EXITCODE 0`）、`util.rpt`、`timing.rpt`；
- 改过的源码和脚本清单；
- 上面 9 项的输出（JSON / 日志）；
- 一段“前后耗时对比”。

通过后，我把新的比特流写进部署记录和报告的性能表。
