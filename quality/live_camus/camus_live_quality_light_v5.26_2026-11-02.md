# CAMUS 口径的现场质量灯 + 切面（服务端 v5.26，2026-11-02 已启用）

用户 11-02："就是这个（10-31 模拟扫查离线版），要做进去"；选"按信号源区分"；随后加"先判断好坏，后续不动就不用继续判断"。
只用 CAMUS 训练集定参、验证集报告；测试集没碰。CAMUS 许可 CC BY-NC-SA 4.0（非商业，须引用 Leclerc 2019）。

## 为什么按信号源区分
- 板上 v5.25 的灯 = 程序 16（EchoNet 训练）+ EchoNet 标准化，换到 CAMUS 画面失效（10-31：好片 40/50 亮红灯）。
- 反过来，F1（CAMUS 训练）分不好 EchoNet 画面：`echonet_f1.py`，EchoNet 验证集 1288 段，与程序 16 的 Dice 0.47（最差 10% < 0.19），
  灯与 EF 误差相关 0.05（v5.25 的灯 0.13）。
- 结论：两个灯只在各自的画面上有意义。CAMUS 口径只给 `live/src/index.json` 里 `"quality": "camus"` 的源；EchoNet / HMC-QU 源照旧，界面标"EchoNet 口径，仅作提示"。

## 板上怎么算（`src/job_echo.c`，RQ01 chain 1 + CQ01）
- 源：`CAMUS_scan_demo_z3.y8` = 10-31 模拟扫查（验证集 4 段：两腔 Good → 四腔 Poor → 四腔 Medium → 四腔 Good，50 fps，500 帧），
  128 帧最近邻 ×3 放进 640×480 画面；板上固定裁切 128 48 384 → `lc_crop_resize_n(128)` 逐位还原 CAMUS 帧（F1 的输入与离线验证完全相同）。
  文件 154 MB 放 `/data/echo_src_camus/`，`/root/echo_src/` 里是符号链接（根分区只剩约 330 MB）。
- 每 5 帧在 PL 上跑一次 F1（2 号程序），窗口 = 最近 50 帧：扇区用全部帧，特征只在 F1 帧上累计（`eq_window`，板上与主机 DLL 同一份 C）；
  权重 / 门限 = `quality_camus.json` portable，标准化在 CAMUS 训练集上按这条链重取（`fit_dll.py`）。界面每 10 帧（0.2 s）更新一次。
- 切面：每 25 帧在 F1 帧上取一次，右室程序（21 号）跑同一张 128 图，比值 = 右室最大块 / F1 左室面积，最近 3 次中位数 ≥ 0.219 判四腔。
  右室程序推迟到 F1 帧之后至少 2 帧、且不紧跟关键帧的跟踪帧上跑（避免两次 PL 背靠背，P99 97 → 74.7 ms）。
- **判定后保持**（CQ01 字节 4 = 门限 ×1000，`hold_design.py`）：最近 3 次灯色相同 + 最近 2 次切面相同 → 锁定，停跑 F1 / 右室程序；
  每帧算 1 s 平均画面（112，整数累加）与锁定时的平均绝对差 d，d > 5.258 灰度级 = 探头动了 → 下一帧起重新判断（切面历史清空）。

## 数字（全部只在验证集上报告一次）
| 项目 | 结果 |
|---|---|
| F1 隔帧（k=2/3/4/5）识别 Poor 的 AUC（`design.py`，按板上链路） | 0.900 / 0.910 / 0.904 / **0.911**，选 k=5（PL 约 23%） |
| 板上同一份 C 重取后（`fit_dll.py`） | AUC **0.911**；窗口灯色 Good 234/36/30、Medium 67/56/111、Poor 1/5/60（绿/黄/红） |
| C 与 Python 特征最大差 | 6.7e-16 |
| 切面（右室/左室面积比） | 验证集判断准确率 0.91；判断间隔 / 取几次中位数的各组合 0.89–0.91，无显著差别，保持每 25 帧 + 3 次（`view_cfg.py`） |
| 保持：同一段内误触发 | 验证集 **0/100**（训练集 1/800） |
| 保持：换切面 / 换病人检出 | 100% / 100%；延迟中位 15.5 / 12 帧（约 0.3 s），最慢 28 / 21 帧 |

## 板上验收（`E:/PLD/board_acceptance_v526`）
**11-02 用户要求启用**：release v5_20261102T153944Z（sha `c88bac6f`）V5_ACTIVATE_PASS；启用后 6 组现场模式复查 HOST_CHECK_PASS（`results_post_activate/`），DP 工作站已重启加载新界面。

- 老链路（L0 / L1 / L2 / V1）：除耗时外与今天 v5.25 的结果**完全相同**。
- C1（CAMUS 源 + 保持）：灯 / 切面 / F1 / 右室 / 锁定解锁事件全部与主机逐位一致；锁定在 72、194、352 帧，解锁在 117、217 帧（换段后 17 帧）；
  Medium 段灯在黄红之间来回，一直没锁定（继续判断）；500 帧里 215 帧保持，F1 57 次（不保持 100 次）；P50 31.9 ms、P99 74.3 ms、最大 74.8 ms。
- C0（同一源、关掉保持）= 上一版（无保持）逐位相同。

## 局限（要如实说）
- 只对 CAMUS 这类画面标定；EchoNet / HMC-QU 源仍用 v5.25 的灯（EchoNet 口径）。真实超声机画面两个口径都没验证过。
- CAMUS 片段只有半个心动周期（往返播放），这个源的 EF 没有临床意义，只用来演示灯和切面。
- 切面规则在比值贴近门限的病人上会来回翻（第 4 段 patient0255），保持后锁定在多数结果上。
- "探头动了"只用突然切换验证过（换段）；真实探头慢慢挪、小幅微调可能达不到门限，不会触发重判。
- 公开数据里没有带探头位姿的真实扫查视频（EchoWorld 未公开；CACTUS 是静止仿体图像，CC BY 4.0，只适合做切面识别外部测试）。

## 文件
`design.py`（定 k）、`fit_dll.py` → `camus_live_model.json`（界面读的模型，拷贝在 `tcp_image_ui/`）、`view_cfg.py`、`hold_design.py`、
`echonet_f1.py` / `camus_sub.py`（可行性）、`make_camus_src.py`（信号源）、v5.25 备份 `*.v525`。
板端改动：`job_echo.c`、`echo_quality.c/.h`（`eq_clean`、`eq_window`）、`live_crop.c/.h`（`lc_crop_resize_n`）、`job.c`（caps v5.26 + `live_roll_camus`）；
主机 DLL `keyframe/echo_core.dll` 已重编（112 缩放与 v5.25 逐位相同）。
界面：`heartview.py`、`jobs.py`（PC 与 `board_station/app_merge/jobs.py`）、`_verify_heartapp.py`、`board_station/verify_heart_station.py`。
回滚：服务端 `deploy_board_v5.py rollback` → v5.25 `4c18f497`；板上 DP 文件备份 `/data/board_station/app_heart.bak_v526pre`、`board.bak_v526pre`。
