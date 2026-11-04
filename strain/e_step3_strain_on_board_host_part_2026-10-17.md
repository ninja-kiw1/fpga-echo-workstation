# 部分 E 第三步：心肌应变上板（2026-10-17 主机部分；**2026-10-18 已上板：服务端 v5.19 `bc386abc…`，交接 §9.73**）

> **10-18 更新**：v5.18（部分 A）部署完后并入 `src/`，开发版验收 `STRAIN_BOARD_PASS`（VAL 100 段板上逐位），部署 v5.19（回滚 v5.18 `cb79204a…`），部署版再验 + 上位机真板 + 回归子集全过；
> 追踪每例 P50 **52.9 ms** / P95 82.9 ms（每对帧 1.59 ms）。详见交接 §9.73、`E:/PLD/board_acceptance_v519/`。下文 §0 / §4 是部署前的状态。

> 计划：`strain/step3/e_step3_plan_2026-10-17.md`（用户 10-17 批准：**部分 A 先出 v5.18，应变出 v5.19，回滚目标 v5.18**）。
> 板子状态不变：固件 f150 `30f06745…`、服务端 v5.17 `51780377…`。测试集没碰（只用 CAMUS VAL；偏移来自 TRAIN）。
> 措辞：探索性辅助测量；VAL 上 ES 值 MAE 3.1 个百分点（TRAIN 偏移后），图像质量差时不可靠；不作诊断、不套相对下降阈值。

## 0. ⚠ 源码暂放 `strain/step3/rx_src/`，v5.18 部署后再并入 `src/`

部分 A 的 v5.18 按 §9.64 用 `deploy_board_v5.py prepare`（**不带 --pin**）从工作区 `src/` 编，`SRCS = $(wildcard src/*.c)`，
应变的新文件放进 `src/` 就会被编进 v5.18。所以 `strain.c/.h`、`bm_sad.c/.h`、`job_strain.c` 现在都在 **`E:/PLD/strain/step3/rx_src/`**，
`src/`、`job.c`、`job.h`、`makefile` **一个字节都没改**。v5.18 部署后运行 `rx_src/apply_to_rx.py`（幂等）：拷入 5 个文件，
`job.h` 加 `JOB_STRAIN = 7` 和原型，`job.c` 加分发、caps 任务表加 `"strain"`、caps 版本改 `"v5.19"`，`makefile` 给 `strain.o / job_strain.o` 加 `-ffp-contract=off`、`bm_sad.o` 加 `-O3`；
保留各文件原来的换行（`job.h` 是 CRLF，`job.c` / `makefile` 是 LF）。在副本上试过两遍：改动只有上面这几行。
上位机（`tcp_image_ui/jobs.py`、`sequence.py`、新 `strainwin.py`、`verify_board_strain.py`）不进板端二进制，已直接改在工作区。

## 1. 移植与逐位核对

| 层 | 文件 | 结果 |
| --- | --- | --- |
| Python 参照（不用 scipy / skimage） | `strain/step3/strain_port.py` | 清理（4 连通最大块 + 填洞，128 上做再 ×4）、8 连通最大块、Zhang 骨架（skimage 0.25 `_fast_skeletonize` 的查表，从 v0.25.2 源码取）、最长路径（两次 Dijkstra，(距离, 序号) 出堆）、5 点滑动平均（`np.convolve` = 从左到右累加 x·0.2，实测）、弧长 / `np.floor_divide` / `np.linspace` / `np.interp`（各自的运算顺序，实测）、四舍六入五成双、`bm_track_line`、numpy 成对求和 |
| 参照 vs 原型 | `strain/step3/compare_port.py` → `results/compare_port.json` | **VAL 100 段，每一步都与 `track_val.py` 最终方案逐位相同**：清理后掩膜、最大块、骨架、最长路径、重采样点、每帧追踪坐标、每帧曲线值，100/100；ES 值最大差 0。用 `sqrt` 代替 `np.hypot` 在这 100 段上没有造成任何差别。重算 VAL：原始偏差 **+3.71**、MAE **4.19**、r 0.66；TRAIN 偏移 −3.806 后偏差 −0.10、**MAE 3.14**（= 第二步） |
| C | `rx_src/strain.c/.h`（+ `bm_sad.c/.h` 原样拷自 step2） | 与参照逐行对应；`-ffp-contract=off`；Dijkstra 用 O(n²) 选最小（距离, 序号），与 heapq 出堆顺序等价 |
| C vs 参照 | `tcp_image_rx/test/strain/harness_strain.c` + `compare_host.py` → `strain_host.json` | **`STRAIN_HOST_PASS`**：VAL 100 段的中线像素、重采样点（f64）、每帧追踪坐标（Q8）、每帧长度和曲线（f64）全部逐位相同；WSL gcc `-Wall -Wextra` 0 警告 |

## 2. 板端任务 `strain`（`rx_src/job_strain.c`，`JOB_STRAIN = 7`）

参数 32 字节：f64 sx、f64 sy（512 网格上每像素 mm）、u32 左室模型（0 → 2）、u32 心外膜模型（0 → 18）、8 字节 0；数据 T 帧 512×512 u8（2 ≤ T ≤ 128，第 0 帧 ED、最后一帧 ES）。
板上：ED 帧 `[::4, ::4]` 灰度复制成 3 通道（同 v6c 灰度路径）→ PL 跑模型 2 和程序 18 → **清理默认在 PL pp_layer**（§9.57），PS 清理一律也跑一遍做交叉核对（`clean_pl_eq_ps`）
→ PS 提线 → 逐帧追踪（NEON neon2）→ 长度 / 曲线。flags：bit0 PS 清理，bit1 纯 C 核（计时对照），bit2 多回两张 logit。
结果：`gls_pct`、`length_mm`、`es_raw_pct`、`es_cal_pct`、`offset_pct`、`peak_raw_pct`、`edge_points`、`ncomp`、`ms`（分割 / U-Net / pp / 提线 / 追踪 / 每对帧 / 长度 / 合计）、`pl_split`、`note`；
块：`points`（i32 T×n×2）、`line`、`myo`（512 位图）、可选 `logits`。WSL 下 `-Wall -Wextra` 语法检查 0 警告（完整链接要板上的 zlib，只能在板上编）。

## 3. 上位机

- `jobs.py`：`JOB_STRAIN`、`STRAIN_PS_CLEAN / STRAIN_C_KERNEL / STRAIN_LOGITS`、`strain_params()`、`strain()`。
- `strainwin.py`（新）：序列窗口右栏多一个「心肌应变…」按钮 → 新窗口：左边帧 + ED 心肌轮廓 + ED 位置（灰）+ 当前追踪点（黄点、青线），可拖动 / 播放；
  右边 GLS-like 曲线（原始）+ ES 值（TRAIN 偏移后，红圈）；下面数值、耗时、固定的探索性说明；两切面都算过时给平均。板端太旧时提示「需要服务端 v5.19」，不做 PC 回落。
- 离线核对 `strain/step3/check_ui_offline.py` → **`STRAIN_UI_OFFLINE_PASS`**：窗口自己的 NIfTI 读取 + 512 拉伸给出的帧与 `track_val` 逐像素相同、sx / sy 相同（6 段）；
  请求打包 / CRC 正确；按 `job_strain.c` 格式造的回复经 `strainwin.unpack` 还原出参照的追踪点 / 中线 / 心肌；窗口截图 `results/strainwin_offline.png`。

## 4. 上板（**还没做，每一步先问用户**）

1. 等部分 A 的 v5.18 部署完（它的会话按 §9.64 做）→ `python strain/step3/rx_src/apply_to_rx.py`。
2. 开发版验收（板上 `/root/jobdev` 编、`devstart.sh` 临时顶替服务，结束恢复）：`tcp_image_ui/verify_board_strain.py`
   → VAL 100 段 logit = 整数参考、PL 清理 = PS 清理、所有追踪坐标 / 曲线逐位 = 参照；前 20 段纯 C 核对照、前 10 段 PS 清理；**每例追踪耗时 P50 / P95**（第 4 项任务）。
3. `deploy_board_v5.py prepare / activate`（v5.19，回滚目标 v5.18），交接新开一节；部署版再跑一遍第 2 项 + 回归子集。
