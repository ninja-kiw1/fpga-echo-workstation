# vis_hls：r2 的 PL 配准引擎 + 图形叠加（HLS 核 `vis_layer`，2026-10-14 ~ 10-15）

P1 1.1 + 1.2（赛题点名的"几何变换"和"图形叠加"）合成一个 HLS 核，共用一个控制口和一个 128 位 m_axi 主口
（读写合用，只看 256 KB 张量 BRAM，控制 `0xA004_0000`，数据 `ai_data_ic/S09`）。规格是 `visref.py`（逐位）。

## 两个功能

| op | 做什么 | 片上存储 |
| --- | --- | --- |
| REG（配准） | A、B 两帧（128 / 256 灰度）装进片上；平移（整数或 1/4 像素整数双线性）、代价表（256 项，PS 装入）、SAD、重叠面积、**三步 / ±R 全搜索 + ½ → ¼ 像素细化都在 PL**；回最优位移、路径和每个候选 (qx, qy, SAD, O) | A 16 块 + B 按行奇偶两块各 8 块 RAMB36 |
| OVL（叠加） | 一带输出行：底图（无 / 灰 / RGB / 热力图伪彩表）、alpha 填充（1/16 档）、不确定带、4 邻域轮廓；掩膜来自 int8 logit（≥ thr，最近邻）或位掩膜 | 行缓冲（BRAM）+ 3 行掩膜环 |

接口：两个寄存器 `mem`（32 位地址，0x18）、`desc`（描述符字号，0x20）；参数全在 BRAM 里的描述符（`src/vis_layer.hpp` 有字段表）。
所有偏移先校验，不合法返回 4、一个字都不写。PS 驱动 `src/vis_ai.c`（自动分带），服务端 v5.16 用在 `reg_search`（engine = 1）和 `JOB_OVERLAY`。

## 和现有实现逐位一致

- **代价**：reg_absdiff（模型 5）的 int8 logit **只取决于 |R−G|**（逐位参考跑遍 65 536 对，`absdiff_table.json`），但不是简单公式，所以用表。
  装这张表 = 现在 F5（U-Net 算代价 + PS 分块求和）的 SAD。
- **比较**：现在用 double MAD = SAD / O 比；PL 用 S1·O2 < S2·O1。分母 ≤ 65 536、MAD < 256 时两个不同分数之差 > 2⁻³²，远大于 double 间距，
  所以两种比较的顺序**完全相同**；同分规则 (|qx|+|qy|, qx, qy) 同 `registration.Registrar.best`。
- **叠加**：alpha = 16 时 = `ai_view_overlay_rgb`（板端 C，链进测试平台逐字节比）/ `confidence.overlay_rgb / uncertain_rgb / heat_rgb`。

## 验证

| 项 | 结果 |
| --- | --- |
| `gen_vectors.py`（592 向量，每个都先和独立 golden 比过：registration.py、confidence.py、scipy 轮廓、逐像素标量实现） | 全部一致 |
| 原生 g++（`test_native.sh`）/ Vitis C 仿真 | 592/592；每个向量还检查 BRAM 其余 16 384 字一个没被改；45 个再和 `ai_view_overlay_rgb` 比 |
| RTL 协同仿真（`cases_cosim.txt` 10 例） | **通过**（10-15）。第一次 2 个三步搜索例子失败：RTL 里第一圈把中心 (0,0) 跳过了（C 里 `known` 是循环前的拷贝，RTL 读到了循环里更新的 `have`）；改成按阶段号判断（`ph > 0`，逻辑等价）后通过 |
| 已知平移（CAMUS VAL 相邻帧） | 三步 100/100、全搜索 30/30；256 档 12/12；¼ 像素 10/10 精确 |
| PS 驱动（`test/vis/check_driver.py`，驱动 + C++ 核在假 BRAM 上） | 20 个配准（逐候选 MAD = Registrar）+ 11 个叠加（1–8 带）全部一致；PS 回落实现 `job_overlay.c` 同样 11 例一致 |

## 资源（Vivado OOC，100 MHz）

| 版本 | LUT | DSP | 时序 | 改了什么 |
| --- | ---: | ---: | --- | --- |
| v1 | 13 037 | 40 | 5.4 ns | 16 路、双线性叠加、LUTRAM 行缓冲；两处调用配准核（被实例化两份） |
| v2 | 8 871 | 25 | 5.1 ns | 4 路、单一调用点、去掉叠加的双线性、行缓冲进 BRAM |
| v3 | 7 960 | 19 | 5.1 ns | 伪彩表 4 字节 / 项、去掉除法器（按行步进） |
| v4 | 7 319 | 16 | 5.4 ns | alpha 改 1/16（移位加）、比较乘法进 DSP、32 位地址、读口在途 1 |
| **v5** | **6 434** | 16 | 5.4 ns | 出图循环 RGB 拆包 / 打包改 144 位移位寄存器（固定偏移） |

试过、没用：pp_layer 两个 m_axi 合成一个（`ai/pp_hls` 的 `PP_ONE_BUNDLE`）：OOC 8 472 vs 8 475 LUT，几乎不省，pp_layer 不动。

## 文件 / 用法

`src/vis_layer.hpp/.cpp`（核；`vis_layer_v1.cpp.txt`、`_v4.cpp.txt` 留档）、`visref.py`、`gen_vectors.py [--quick]`（要 srtp-vision）、
`tb/test_vis.cpp`、`test_native.sh`（WSL）、`run_hls_vis.bat csim|synth|evaluate|cosim`（`VIS_COSIM_LIST` 可换用例表；跑之前删 `ai/build/vis_ip/solution1/impl`，
命令行别停在 `ai/build/vis_ip` 里）。导出的 IP 冻结在 `ai/build/vis_ip_r2`。上板：`test/vis_board_test.c`。
