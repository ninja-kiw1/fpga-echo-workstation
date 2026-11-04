# PLD 医学影像边缘工作站（FZ3B / Zynq UltraScale+）

> **队友先读**：`文档/交接与新对话提示词/队友交接_2026-10-25.md`（10-25 现状、怎么跑、最终数字、规则、剩下的事）。
> **2026-10-20 更新（先读这里）**：按指导老师意见，项目收敛为**床旁超声心动图实时全心功能评估**（单场景 + 心尖四腔心单切面）。
> 全景总览：`文档/项目总览_2026-10-20.html`；方案：`文档/规划与构想/心脏聚焦方案_2026-10-20.md`。
> 板上现状（10-25）：固件 **f150** `30f06745…`（实际 152.15 MHz）+ 服务端 **v5.24** `75c294e9…`（交接 §9.82，回滚 v5.23）+ 独立 CPU 切片（§9.78）+ 程序包 17–22。心脏线：①②③ 关键帧 / 质量灯 / 保形区间（mode 2）、现场模式（mode 3，板上信号源）、全心扩展 "HC01"（节段室壁运动 + 右室 / 右房 21 / 22）。四个测试集 10-25 预注册后各评一次：`final_test_2026-10-25/final_test_results_2026-10-25.md`。下面的正文是 10-14 快照，版本与资源数字已过时。
> 10-23：新数据 TED / CardiacUDA / HMC-QU（公开部分）已下载（`文档/数据集下载记录_2026-10-23.md`）；创新 ① 关键帧主机仿真完成（`keyframe/keyframe_simulation_2026-10-23.md`：17.8% 关键帧 EF 不降，TED 94 例 = CAMUS 训练集、4 例 = CAMUS 测试集已排除）。**10-23 用户决定不做 HP0 比特流，f150 按计划 10-27 冻结**，HP0 写进"下一步"。右室 / 右房程序已训练并量化（`cardiac_rv/right_heart_rv_ra_2026-10-23.md`：现用 `rv_pl1_5` / `ra_pl1_5`，VAL Dice 0.82 / 0.88，宽增广 + 伪标签后跨库合理率 EchoNet 97% / 90%，未上板；CardiacUDA 没有官方划分，按目录定死，TEST 锁定）。
> 10-24：创新 ② 质量灯 + ③ 保形 EF 区间主机验证完成（`quality/quality_guidance_and_conformal_ef_2026-10-24.md`：CAMUS VAL 区分 Poor 的 AUC 0.911；EchoNet 上只是弱信号；90% 区间覆盖率达标但宽 ±11.5，建议界面显示 80% 区间）。①②③ 已写成板上 C（`echo_ef` mode 2，`keyframe/board_c_port_123_2026-10-24.md`），**10-24 已上板：服务端 v5.21 `abd8242b…`**（交接 §9.79，回滚 v5.20；40/40 逐位一致，跟踪 0.81 ms/帧）。

> 2026-10-14 快照。赛题：PLD 竞赛选题 1（医学影像）。**设计文档 + 视频 11-01 截止**。
> 仅用于工程教学与算法演示，只做"辅助测量与复核"，不作临床诊断依据。
> 本文件是总入口；细节以各线 README 和 `tcp_image/tcp_image_rx/progress_and_handoff.md` 最新章节为准（证据优先级：最新验收日志和代码 > 交接最新章节 > 各 README > 构想文档）。

## 一句话

FZ3B 板上的 PL 是一台**固定结构的 U-Net 张量引擎**（30 节点、34 201 参数），换"程序"只换权重：学出来的分割模型和编译出来的经典算子共用同一个比特流。
PL 还做图像预处理（行缓冲）和像素后处理（`pp_layer`：阈值、×2/×4 放大、连通块、最大块 + 填洞、分块和）；
PS 负责通信、调度、整病例任务（CT 整卷、整段心超、Simpson、配准搜索）和临床测量；PC 上位机只选文件和显示。

三根支柱（报告按这个组织）：**引擎与数据通路**、**权重即程序**（模型库 + 程序包 + 算子编译器）、**旗舰心超**（CAMUS + EchoNet）。

## 现状（板上）

| 项 | 版本 / 数值 |
| --- | --- |
| 固件 | **xu2dp_e2b_pp_r1a** `c3d5645c…`（回滚 → `/lib/firmware/ai_sys.bin.before_r1`）；VERSION 0x494D5004 |
| PS 服务端 | **v5.15** `2f6ec0aa…`（**不能退到 v5.14 及以下**：v5.14 遇到 r1 固件会退出）；部署只用 `tcp_image/tcp_image_rx/boot/deploy_board_v5.py prepare / activate` |
| 资源（r1a） | LUT 56 050（79%，**最紧**）、BRAM 136.5/216、DSP 290/360，100 MHz（实测 101.376 MHz 时钟） |
| 速度 | 眼底 512 整网约 472 ms（同板 4 核 NEON 565 ms）；128 档约 32 ms；紧凑帧实时通路约 30 帧/秒 |
| 板子 | `192.168.1.254:5060`；SSH 工具在 `tcp_image/.board_tools`（paramiko）；BoardCheck 用 `/root/Fz3b_BoardCheck_r1` |

模型库（v5.15，17 个内置程序；7 号以上只能经紧凑帧 v6c 调用；另可用 `.upk` 程序包在运行时安装，编号 17–254）：

| 号 | 程序 | 用途 | 数据 / 关键结果 |
| ---: | --- | --- | --- |
| 0 | fundus | 眼底血管（512） | STARE；整网逐位验证（`medical_fpga/`、`lwnet/`） |
| 1 | skin | 皮肤镜病灶（128） | ISIC 2016 官方测试集 Jaccard 0.818 / Dice 0.891（板上逐值一致） |
| 2–5 | echo_lv / echo_inter / echo_seq / reg_absdiff | CAMUS 心超：F1 分割、F2 点击纠错、F3 序列传播、F5 配准代价 | F1 VAL Dice 0.922；F4 双平面 Simpson 板上 450/450 一致；测试集 10-09 已用一次（`echo/README.md` 文末） |
| 6–13 | op_* | 算子编译器编出来的经典算子（中值、形态学、血管增强等） | `opcomp/` |
| 14–15 | ct_spleen / ct_spleen_roi | CT 脾脏两阶段 | MSD Task09，测试 Dice 0.968（只用一次） |
| 16 | echonet_lv | EchoNet 心超左心室，逐拍 EF | VAL EF MAE 5.4（TEST 未动） |

整病例任务（控制头 v7 "HM"）：`caps`、`ct_seg`（整卷 NIfTI 上传 → 板上全部做完，含脾脏测量）、`reg_search`（F5 搜索）、`echo_ef`（整段心超 + LVEF 分档 / RR）、`simpson_ef`。
临床测量定义、门限与出处：`tcp_image/tcp_image_rx/docs/clinical_measurements_2026-10-13.md`。

## 正在做：P1（`待办优先级_2026-10-14.md`，交接 §9.60）

| # | 内容 | 状态（10-14） |
| --- | --- | --- |
| 1.1 + 1.2 | 下一版比特流 r2：PL 配准引擎（几何变换 + ¼ 像素插值 + 代价 + 搜索）+ PL 图形叠加（alpha / 轮廓 / 热力图），合成 HLS 核 `vis_layer` | `tcp_image/tcp_image_rx/ai/vis_hls/`：614 向量逐位通过，已知平移 100/100；OOC 7.3k LUT，**整片估 88% 超 86%，等用户定压缩方案** |
| 1.3 | 控制互联改"一次协议转换 + AXI-Lite 交叉开关" | BD 脚本 `ai/bd/build_ai_sys_xu2dp_e2b_pp_r2.tcl` 已写，未构建 |
| 1.4 | 三维表面重建搬到板上（PS，C） | `src/surf3d.c`、`src/job_render.c`；本机与 Python 版逐像素一致，未上板 |
| 1.5 | 板子独立运行（DP 显示器 + 鼠标 + 板上界面） | 未开始 |
| 1.6 | CAMUS 新程序：左房、左室心外膜 → LAVI、LV 质量、壁厚 | 已训练（VAL Dice 0.882 / 0.937），测试集锁文件已写 |
| 1.7 | 可观测面板（整病例时间线，软件测得） | 未开始 |

## 目录

| 目录 / 文件 | 内容 |
| --- | --- |
| `待办优先级_2026-10-14.md` | **当前计划**（P0–P3 + 资源账 + 当天进度） |
| `tcp_image/tcp_image_rx/` | 板端主工程：`src/`（PS 服务端，部署时整个上传到板上编译，**必须时刻能编译**）、`ai/`（HLS 源码：`opt_hls*` 卷积 / eltwise、`pp_hls` 后处理、`vis_hls` r2 新核；`ai/bd/` 整系统构建脚本；`ai/net/` 模型库与程序包工具）、`rtl_lb/`（行缓冲预处理 RTL）、`vivado/`（构建日志、资源 / 时序报告）、`bitstream/`（比特流 + 部署记录）、`boot/`（部署、程序包安装）、`test/`、`docs/`、**`progress_and_handoff.md`（总交接，最新在末尾）** |
| `tcp_image/tcp_image_ui/` | PC 上位机（`run.bat` / `app.py`；CT `ctwin.py`、心超 `echowin.py`、配准 `regwin.py`、程序台 `progwin.py`、任务协议 `jobs.py`）和全部 `verify_board_*.py` 上板验收脚本 |
| `echo/` | CAMUS 心超 F1–F5 + 1.6 新程序（训练 / 量化 / 逐位导出 / 上板验收） |
| `echonet/` | EchoNet-Dynamic 实时心超工作站、临床测量（LVEF 分档、RR）门限评测 |
| `ct/` | MSD 脾脏 CT 两阶段；`ct/demo_3d_20261011/`（三维重建演示） |
| `skin/` | ISIC 2016 皮肤镜 |
| `opcomp/` | 算子编译器（权重即指令） |
| `lwnet/`、`medical_fpga/` | 最早的眼底模型、两个 HLS IP 的来历和 JTAG 上板历史（主线复用了其中的 IP 与整数参考） |
| `environment/` | 本机工具链配置（`local-tools.json` 指向 `E:/AMD/Xilinx/2025.2`）、`build-env.bat`、预检 |
| `edgeboard/` | FZ3B 板级文件（BD 脚本从这里读 PS 预设，**不要移动**） |
| `fz3b_led_blink/` | 板子点灯例程（上板流程验证） |
| `board_acceptance_*/` | 每次换固件 / 服务端的上板验收证据和脚本，按时间：`xu2dp_20261011` → `e2` → `e2b` → `v59_programs` → `pp` → `v510_pp` → `v511_jobs` → `v512_echo` → `v513_simpson` → `v514_pl_default` → `v515` → **`r1`（当前模板：`acceptance.py baseline / deploy_tests`、`regression.py` 17 组）**。脚本互相引用上一个目录的 `inputs/`，**不要改名或移动** |
| `xu2dp_review_20261011/` | 队友 xu2dp 交付包解压后的审阅目录（验收脚本引用其中的比特流） |
| `交付包_xu2dp_20261011/` | 队友交付的 zip + sha256 + 接手说明 + 上板验收说明 |
| `文档/规划与构想/` | 创新点筛选与项目规划、板端化规划、边缘影像工作站总体构想、特色与优势强化方案（§7 措辞）、赛题对照、分工、历史计划 |
| `文档/赛题与设计方案/` | 赛题选题指南（`917c3753-….docx`）、设计方案 v1 / v2、勘误对照 |
| `文档/交接与新对话提示词/` | 新对话提示词（当前：`新对话提示词_P1_2026-10-14.md`）、10-12 队友说明、SRTP 环境说明 |
| `文档/整理记录_2026-10-14.json` | 10-14 整理时每个文件的原位置 → 新位置 |
| `make_share_package.py`、`share/` | 给队友打包（排除数据集、虚拟环境、可重建的构建目录） |
| `EchoNet-Dynamic/`、`Task09_Spleen/` | 下载的数据集（**EchoNet 不允许转发**；MSD 公开） |
| `FZ3B_ubuntu18.04_211115.img` | 板子 SD 卡镜像（8 GB） |

## 环境

| 用途 | 环境 |
| --- | --- |
| 上位机、需要 cv2 的回归 | `C:\Users\21888\anaconda3\envs\srtp-vision\python.exe` |
| 训练 / 量化（torch + CUDA）、连板 SSH | `E:\PLD\skin\.venv`（或 anaconda base），并把 `tcp_image/.board_tools` 加进 `sys.path` |
| HLS / Vivado | 2025.2，入口 `environment\build-env.bat`；HLS 脚本如 `ai/vis_hls/run_hls_vis.bat csim\|synth\|evaluate\|cosim` |
| 板端 C 的本机检查 | WSL Ubuntu 的 gcc（Git Bash 里调用要加 `MSYS_NO_PATHCONV=1`） |

## 必须遵守的规则（踩过的坑）

- **PS 访问没有从机的 PL 地址会让板子永久挂死**：新 IP 的控制口只能在固件指纹确认后访问（照 `src/pp_ai.c` 的 `pp_firmware_ok()`）；新固件的 sha256 必须追加到板上 `/lib/firmware/ai_sys_pp.sha256`。
- 绝不在 HLS 核运行时重编 PL；CPU 不在 IP 运行期间访问同一片张量 BRAM。
- 板上操作串行：板子同一时间只服务一个客户端，换比特流要重启；验收和重启期间不跑别的板上测试。
- 每次部署服务端都在交接文档新开一节，写清哈希和回滚目标；新固件按 `board_acceptance_r1/acceptance.py` 派生（失败自动回滚）。
- 回归被打断时不会自动还原仓库里的证据文件，要从 `repo_reports_backup` 拷回。
- 跑 HLS 时命令行不要停在 `ai/build/...` 子目录里（Windows 会锁目录）；跑 `evaluate` 前先删 `solution1/impl`。
- 已用过一次的测试集（CAMUS、CT、ISIC）不再用来调参；EchoNet TEST 还没用过；新模型的测试集只评一次，评测前写锁文件。
- 临床阈值必须核对原文出处，不凭记忆写（10-13 曾把"314 mL"读错）。

## 从哪里接着读

1. `待办优先级_2026-10-14.md`（计划）→ `tcp_image/tcp_image_rx/progress_and_handoff.md` 最新几节（§9.50 起）。
2. 新对话：`文档/交接与新对话提示词/新对话提示词_P1_2026-10-14.md`。
3. 某条线的细节：各目录 `README.md`；PL 优化：`tcp_image/tcp_image_rx/docs/pl_optimization_guide_2026-10-09.md`。
