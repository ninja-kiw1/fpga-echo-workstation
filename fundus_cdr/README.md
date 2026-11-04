# 部分 D：眼底视盘 / 视杯分割 + 垂直杯盘比（2026-10-17）

**状态**：模型 + 程序包 + 主机逐位核对 + VAL + 测试集（只评测一次，已用）完成；**10-18 程序包 19 / 20 已装上板（v5.19），自检 0 差异，VAL 202 张板上 == 主机（FUNDUS_CDR_BOARD_PASS，约 100 ms / 例）**。
仅作辅助测量与复核，**不作青光眼诊断**。交接文档 `tcp_image/tcp_image_rx/progress_and_handoff.md` §9.71。

## 数据与许可（10-17 在原始出处核对）

| 数据集 | 用途 | 许可（原始出处） |
|---|---|---|
| **Chákṣu IMAGE**（figshare 10.6084/m9.figshare.20123135 v2，1345 张，3 种相机，5 位专家掩膜） | 训练 / VAL / 官方测试 | **CC BY 4.0**（figshare API）。引用 Kumar et al., *Sci Data* 10:70, 2023 |
| **PAPILA**（figshare 10.6084/m9.figshare.14798004 v2，488 张 Topcon，2 位专家轮廓点） | 外部测试（只一次） | **GPL-3.0+**（figshare API）。引用 Kovalyk et al., *Sci Data* 9:291, 2022 |
| 未用：RIGA（Deep Blue，CC BY-NC 4.0）、REFUGE（REFUGE2 论文：CC BY-NC-ND）、Drishti-GS（仅学术、禁止再分发、需注册）、ORIGA（无公开许可）、Zenodo 8009107 合集（标 CC BY 4.0，但与上述原始条款不符） | — | 用户 10-17 选 Chákṣu + PAPILA |

下载：`download.sh`（figshare API 的 md5 全部核对通过）；Chákṣu 解压后 172 GB，**不解压**，`prep_chaksu.py` 直接从 zip 读。

## 流程（`cdr_ref.py` = 板端 C `src/fundus_cdr.c`，逐位一致）

1. 上位机：原图 → RGB → Pillow LANCZOS 拉到 512×512（和血管模型一样；横竖比例不同，**垂直**比值不受影响）。
2. 第一阶段：`payload1 = box4(img512)`（128×128，4×4 平均、四舍五入）→ 程序 **19 `fundus_disc`** → 最大 8 连通域 → 视盘外接框中心、尺寸。
3. 窗口：边长 `side = 128k`，取最小的 k（1..4）使 `side ≥ 2 × 视盘尺寸`；以视盘为中心，夹在图内。
4. 第二阶段：`payload2 = box_k(窗口)` → 程序 19（视盘）+ 程序 **20 `fundus_cup`**（视杯）；一个紧凑帧即可（`model0=19, extra=[(20, 0, 0, 1)]`）。
5. 后处理（PS）：logit 双线性 ×k（整数权重）、≥ thr − ½；视盘 = 填洞(最大连通域)；视杯 = 填洞(最大连通域(视杯 ∧ 视盘))；
   **vCDR = 视杯所跨行数 / 视盘所跨行数**。

参考值：Chákṣu 多数投票掩膜（去掉 1–6 px 离散噪点，保留最大连通域）在原分辨率上量的行数比；与数据集自带 CSV 的 VCDR 相差 ≤ 0.03。

## 模型（同一 U-Net 结构，34 201 参数，128 档）

| 程序 | 训练 | 量化（INT16 节点） | VAL Dice FP32 → 整数 |
|---|---|---|---|
| 19 `fundus_disc`（`disc_mixed_a`）：整图定位 + 窗口内视盘，一个程序两用 | 50% 整图 / 50% 窗口（窗口按专家框加 ±15% 中心 / 尺寸抖动） | `first_block_5` | 窗口 0.9716 → 0.9714；整图 0.9635 → 0.9634 |
| 20 `fundus_cup`（`cup_roi_a`） | 窗口 | `add_4` + `first_block_2`（`first_block_2` 第 5 次被选中） | 0.9074 → 0.9062 |

只做整图 / 只做窗口的对照模型（`disc_full_a` / `disc_roi_a`）训练被会话结束打断，没跑完；两程序方案在 VAL 上已达标，没再补。

**逐位核对**：`export_verify.py` 两个模型均 PASS（金标准 30 节点、全部 VAL 输入 202 / 404 个、512 整表 0 差异）；
程序包自检期望值 == lwnet 整数参考（两个包都 True）；`test/fundus_cdr/compare.py`：C == Python，VAL 202 例 + 合成 500 例，**FUNDUS_CDR_HOST_PASS**。

## 结果（整数模型 = 板上）

| 集合 | 找到视盘 | 视盘 Dice | 视杯 Dice | vCDR MAE | 偏差 | r | 专家间（单个专家 vs 多数） |
|---|---:|---:|---:|---:|---:|---:|---|
| VAL（202） | 202 | 0.974 | 0.908 | **0.035** | −0.004 | 0.76 | MAE 0.038–0.059 |
| **测试 Chákṣu 官方（336，一次）** | 336 | 0.972 | 0.898 | **0.038** | −0.013 | 0.64 | MAE 0.042–0.050 |
| **外部 PAPILA（488，一次）** | 478 | 0.820 | 0.526 | **0.130** | +0.060 | 0.40 | 两位专家之间 MAE 0.036 |

- Chákṣu 上误差在单个专家相对多数意见的离散范围内；各相机：Bosch 0.033、Forus 0.030、Remidio 0.040。
- **PAPILA 外部测试失败**：30° 视盘居中的 Topcon 图、视盘大得多（220 例要 384 窗口，训练集只有 4 例）、颜色不同。
  10 例没找到视盘，视杯 Dice 只有 0.53。**结论：本模型只适用于与 Chákṣu 相近的相机 / 视野，换相机要重新训练或域适应**（测试后没调参）。
- 锁文件 `results/final_test/lock.json`（status used，含模型 / 参考代码 / 测试缓存的 sha256）。

## 参考范围（核对原文，不用于判定）

Foster PJ et al., *Br J Ophthalmol* 2002;86:238–242（ISGEO 定义，PMC1771026）：结构异常 = vCDR 或两眼差 **≥ 该人群正常视野者的第 97.5 百分位**（第 2 类为 99.5）；
**没有固定数值**。表 1 中孟加拉 / 蒙古 / 新加坡 / 坦桑尼亚的第 97.5 百分位为 0.70 / 0.70 / 0.71 / 0.7，作者说明不能直接外推到其他人群。
因此界面**只显示测量值**，不套阈值、不写"青光眼"。

## 文件

| 文件 | 作用 |
|---|---|
| `download.sh`、`tools/safe_unzip.py` | 下载（md5 核对）、安全解压（PAPILA） |
| `common.py`、`prep_chaksu.py`、`prep_papila.py` | 输入链路、原分辨率 Dice、缓存（`data/cache/`） |
| `cdr_ref.py` | 整条 PS 流程的 NumPy 参考（纯整数，无 scipy） |
| `train.py`、`quantize.py`、`export_verify.py` | 训练、量化、导出 + HLS 逐位核对 → `deploy/<tag>/` |
| `eval_val.py` | VAL 全流程（整数 / FP32），`results/eval/v1/`（`cases.npz` = C 核对输入） |
| `final_test.py` | 测试集一次性评测（先写锁） |
| `tcp_image_rx/src/fundus_cdr.c/.h`、`test/fundus_cdr/` | PS 端 C、主机逐位核对 |
| `tcp_image_rx/programs_d/fundus_disc.upk`（19）、`fundus_cup.upk`（20） | 程序包 |
| `tcp_image_ui/verify_board_fundus_cdr.py` | 上板核对脚本（10-18 PASS，`board_check_v5/fundus_cdr_board.json`） |

```powershell
$P = 'E:\PLD\skin\.venv\Scripts\python.exe'; cd E:\PLD\fundus_cdr
& $P -I prep_chaksu.py; & $P -I prep_papila.py
& $P -I train.py --task disc_mixed --tag disc_mixed_a; & $P -I train.py --task cup_roi --tag cup_roi_a
& $P -I quantize.py --tag disc_mixed_a; & $P -I quantize.py --tag cup_roi_a
& $P -I export_verify.py --tag disc_mixed_a; & $P -I export_verify.py --tag cup_roi_a
& $P -I eval_val.py --disc disc_mixed_a --cup cup_roi_a --name v1 --fp32
```

## 上板说明（10-18 已按此完成，第 1、2 步通过）

前提：服务端 ≥ v5.9（程序包 + 紧凑帧额外程序），当前 v5.17 即可；**不换比特流、不重编服务端**。
1. `python boot/install_program.py install programs_d/fundus_disc.upk`，再装 `fundus_cup.upk`；`LOADED.txt` 两个都 OK（板上自检逐位一致）。
   sha256：disc `0393e680…`、cup `5930b94f…`。回滚 = `install_program.py remove`。
2. `tcp_image_ui/verify_board_fundus_cdr.py --eval-name v1`：VAL 202 张，板上 logit == `cases.npz`、vCDR == VAL 结果 → `FUNDUS_CDR_BOARD_PASS`。
3. 之后可选：把 `fundus_cdr.c` 接成病例任务（`JOB_FUNDUS_CDR`，job.c / job.h 加一种），排在部分 A（v5.18）和部分 E（v5.19）之后；
   上位机"眼底综合筛查"页（血管 + 杯盘比）还没做。
