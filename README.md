# FPGA 床旁超声心动图实时全心功能评估系统

**FPGA bedside echocardiography workstation** on a Zynq UltraScale+ MPSoC board (FZ3B, XAZU3EG): a fixed-structure
U-Net tensor engine in the PL runs every segmentation model and compiled classic operator by swapping weights only;
the ARM cores run whole-case jobs (beat-by-beat EF, Simpson, keyframe tracking) and a PC / on-board UI displays the results.

> 仅用于工程教学与算法演示，只做"辅助测量与复核"，**不作临床诊断依据**。Not a medical device; not for clinical use.

全国大学生嵌入式芯片与系统设计竞赛 2026 · FPGA 创新设计赛道（AMD）· 自主选题赛道。

## 一句话

FZ3B 板上的 PL 是一台**固定结构的 U-Net 张量引擎**（30 节点、34 201 参数），换"程序"只换权重：学出来的分割模型和
编译出来的经典算子共用同一个比特流。PL 还做图像预处理（行缓冲）和像素后处理（`pp_layer`）；
PS 负责通信、调度、整病例任务和临床测量；上位机只选文件和显示，也可以直接在板上经 DP 显示器运行。

## 平台与工具声明

| 项 | 型号 / 版本 |
| --- | --- |
| 板卡 | FZ3B 开发板（Zynq UltraScale+ MPSoC，器件 **XAZU3EG-1SFVC784I**，4 GB DDR4） |
| 板上系统 | 厂商 Ubuntu 18.04 镜像，Linux 4.14.0-xilinx-v2018.3 |
| FPGA 工具 | Vivado / Vitis HLS **2025.2** |
| 当前固件 | `ai_sys_f150nv`：PL 时钟 MMCM 标称 150 MHz（实测参考时钟换算为 152.15 MHz），回滚版 `ai_sys_f150` |
| 上位机 | Windows 10/11，Python 3.11（依赖见 `environment/`） |

## 主要结果

| 项 | 数值 |
| --- | --- |
| 资源（f150nv） | LUT 75.40 %、FF 34.0 %、BRAM 63.2 %、DSP 80.6 %；按实测时钟签核 WNS +0.486 ns |
| 功耗 | Vivado 估计片上 4.04 W（PL 动态约 0.9 W）；整板实测 6–7 W |
| 速度 | 512×512 整网 292.8 ms，是同板单核 NEON 分块实现的 7.6 倍；现场模式延迟 P50 29 ms |
| EchoNet-Dynamic TEST（预注册后只评一次） | 关键帧逐拍 EF MAE 5.39，80 % 保形区间覆盖率 0.80 |
| CAMUS TEST | 左房容积 MAE 11 mL；F1 左室 VAL Dice 0.922；双平面 Simpson 板上 450/450 逐位一致 |
| CardiacUDA TEST | 右室 Dice 0.776 / 右房 0.877 |
| HMC-QU TEST | 节段室壁运动异常识别 AUC 0.90 |
| 逐位验证 | 模型库 0–22 号板上输出与参考逐位一致；5000 帧稳定性 0 个不一致 |

实时性能在 f150 上测得，f150nv 只去掉了心脏主线不用的 `vis_layer`，其余 IP、时钟与地址相同（见 `acceptance/board_acceptance_nv/`）。
详细数字：`final_test_2026-10-25/final_test_results_2026-10-25.md`、`perf_j/`、`fpga_evidence/`、`report/design_report.pdf`。

## 目录对照（指南 3.3.5.4 推荐结构 → 本仓库）

本仓库按功能线组织（各线的训练、量化、导出、验证脚本放在一起），与推荐结构的对应关系如下：

| 推荐目录 | 内容 | 本仓库位置 |
| --- | --- | --- |
| `src/` | PL 设计源码（HLS / RTL / Block Design） | `tcp_image/tcp_image_rx/ai/`（HLS 核：`opt_hls*`、`pp_hls`、`vis_hls`；BD 脚本：`ai/bd/`）、`tcp_image/tcp_image_rx/rtl*/`、`medical_fpga/hls/` |
| `src/` | PS 侧软件 | `tcp_image/tcp_image_rx/src/`（板上 C 服务端）、`tcp_image/tcp_image_rx/kmod/`（内核模块） |
| `src/` | 上位机 | `tcp_image/tcp_image_ui/`（入口 `heartapp.py`） |
| `src/` | 模型训练 / 量化 / 导出 | `echo/`、`echonet/`、`cardiac_rv/`、`ct/`、`skin/`、`fundus_cdr/`、`lwnet/`、`opcomp/`（算子编译器） |
| `sim/` | 仿真与验证 | 各 HLS 核目录下的 csim / cosim 脚本与报告、`medical_fpga/embedded/pynq/`（逐位 NumPy 参考）、`*/deploy/*/verify_report.json` |
| `build/` | 构建脚本 + 综合实现报告 | `tcp_image/tcp_image_rx/ai/bd/run_build_f_novis.bat`、`tcp_image/tcp_image_rx/vivado/`（`ai_sys_f150nv_util.rpt` / `_timing.rpt`、`f_clock/` 实测时钟重签核） |
| `board/` | 上板工程、运行脚本、实测输出 | `tcp_image/tcp_image_rx/bitstream/`、`tcp_image/tcp_image_rx/boot/`（boot.bin、部署脚本）、`acceptance/`（最新上板验收记录） |
| `data/` | 测试数据与参考结果 | 数据集需自行下载（见下）；参考结果与锁文件：`final_test_2026-10-25/`、各线 `results/` |
| `skill/` | 技能包 | `skill/` |
| `report/` | 设计报告 | `report/design_report.pdf` |
| — | 性能与功能证据 | `perf_j/`、`fpga_evidence/`、`keyframe/`、`quality/`、`live/`、`wallmotion/`、`strain/` |

更早、更细的说明见 `project_notes_20261014.md`（10-14 快照，部分版本号已过时）、`tcp_image/tcp_image_rx/progress_and_handoff.md`（开发全过程记录）以及各目录下的 README。

## 复现步骤

1. **准备环境**
   - 安装 Vivado / Vitis HLS 2025.2，在 `environment/local-tools.json` 里填写本机安装路径；
   - Python 3.11：`pip install -r environment/requirements-lock.txt`（或 `conda env create -f environment/environment.yml`）；
   - 板卡：FZ3B 写入厂商 Ubuntu 18.04 镜像，用网线与 PC 直连（板子地址 192.168.1.254）。
2. **构建 PL 比特流**（可跳过，直接用仓库里的 `bitstream/ai_sys_f150nv.bin`）
   ```
   python tcp_image/tcp_image_rx/ai/build/unpack_hls_ip.py
   tcp_image\tcp_image_rx\ai\bd\run_build_f_novis.bat 150.0 f150nv p6757
   ```
   产物与报告：`tcp_image/tcp_image_rx/bitstream/ai_sys_f150nv.bin`、`tcp_image/tcp_image_rx/vivado/ai_sys_f150nv_{util,timing}.rpt`，
   可与仓库中已有的报告逐项比对。
3. **部署到板上**：把比特流放到板上 `/lib/firmware/ai_sys.bin`（开机由 `ai-sys-bitstream.service` 加载），
   并把其 sha256 写入 `/lib/firmware/ai_sys_pp.sha256`；PS 服务端用
   `python tcp_image/tcp_image_rx/boot/deploy_board_v5.py prepare` → `activate` 部署（`rollback` 回到上一版），流程见 `skill/board_deploy_rollback.md`。
4. **运行上位机**：`python tcp_image/tcp_image_ui/heartapp.py`（快照 / 关键帧 / 现场三种模式）。
5. **验证**：`tcp_image/tcp_image_ui/verify_board_*.py` 做板上逐位比对与回归，`acceptance/board_acceptance_nv/acceptance.py baseline|deploy_tests`
   做比特流升级验收；结果与 `acceptance/` 中的记录比对。
6. **重新训练 / 评测**：下载数据集后按各线 README 运行预处理、训练、量化、导出脚本；测试集只能评一次（`skill/test_set_lock.md`）。

公开版中所有目录名与文件名已按指南统一为英文小写，脚本中的路径同步修改；板上的安装目录也随之为小写（例如 `/root/tcp_image_rx`）。

## 权重与固件

- 模型库 0–22 的量化权重全部在仓库里：内置程序在 `tcp_image/tcp_image_rx/src/unet_models.c`，运行时安装的程序包
  （`.upk`）在 `tcp_image/tcp_image_rx/programs_*/`，各线导出的逐层参数在 `*/deploy/*/params/`，浮点训练权重在 `*/results/train/`。
- 比特流与启动镜像：`tcp_image/tcp_image_rx/bitstream/`、`tcp_image/tcp_image_rx/boot/boot.bin`。
- HLS IP 只保留官方导出包（`solution1/impl/ip/xilinx_com_hls_*.zip`），构建前用 `unpack_hls_ip.py` 解开。

## 数据集（不在仓库中）

原始数据、缓存数组、视频、测试向量图像都没有放进仓库，请自行下载：

- EchoNet-Dynamic（Stanford AIMI 研究使用协议，禁止再分发）
- CAMUS（CC BY-NC-SA 4.0，Leclerc et al., IEEE TMI 2019）
- CardiacUDA、HMC-QU、TED
- MSD Task09 Spleen、ISIC 2016、STARE、DRIVE、CHASE_DB1、HRF、Chákṣu、PAPILA

仓库中出现的 CAMUS / MSD / ISIC / 眼底图像仅作结果示意，版权归原数据集所有者。

## 协议

代码以 [MIT License](LICENSE) 开源。用非商业或仅限研究使用的数据集训练出的权重
（例如程序 16 `echonet_lv` 与右室 / 右房 21 / 22 用了 EchoNet-Dynamic，CAMUS 系列程序用了 CAMUS）以及结果图中的数据集图像，
仍须遵守对应数据集的使用条款。第三方组件保留各自的协议（`lwnet/LICENSE`、`edgeboard/LICENSE`）。
