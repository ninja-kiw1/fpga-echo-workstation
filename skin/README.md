# 同一套 FPGA 硬件换权重：ISIC 2016 皮肤镜病灶分割

更新：2026-10-08。对应 `赛题对照与创新点.md` 创新点 7。**10-08 已在 FZ3B 实板上验收通过**（见“实板验收”）。

## 实板验收（2026-10-08，FZ3B，同一比特流）

| 项目 | 结果 |
|---|---|
| 部署 | `tcp_image_server_skin` 板上原生编译 0 警告，装为 `tcp-image-server-skin.service`（与眼底服务互斥，不开机自启）；眼底程序 / unit / 比特流文件 SHA-256 全程不变 |
| 金标准 ISIC_0000010 | 板端回传 128×128 logit 与整数参考 **16384/16384 逐值一致** |
| 门限切换 | 门限 1 → −3：logit 不变，掩膜随门限变化（20784 → 23648 像素，512 网格） |
| 6 张演示图（上位机实际发送的 PPM） | 全部逐值一致 |
| **ISIC 官方测试集 379 张** | **379/379 张 logit 逐值一致；用板端 logit 重算 Jaccard 0.8182 / Dice 0.8906，与离线结果逐张相同** |
| **板端 AI 耗时（128×128）** | **平均 37.7 ms**（37.6–38.7，379 帧） |
| 往返耗时（回传掩膜 + logit 两幅 512 图） | 平均 115.3 ms（111.3–323.1；最大值是首帧建连） |
| 切回眼底后回归 `verify_board_v4.py` | 75 帧 PASS，整图 golden 262144 logit 0 差异；眼底 128 档 AI 37.8 ms，与皮肤相同 |

证据：`board_check/acceptance.json`、`board/board_logs/`（部署 / 切换日志、`skin_deployment.json`）、
`tcp_image/tcp_image_ui/board_check_v4/regression_after_skin_20261008.json`。

切换命令（上位机先断开）：`board\deploy_skin.py switch skin` / `switch fundus`；板上也可直接 `/root/skin_build/<tag>/skin_mode.sh skin|fundus`。

**10-08 更新：方案 A 已被 v5 模型库取代**（`tcp_image/tcp_image_rx/progress_and_handoff.md` §9.38）。皮肤是模型库第 1 号模型，上位机「AI 模型」选「皮肤镜病灶」即可，不用切服务；
`verify_board_skin.py` 已改为逐帧带模型号，v5 上 379 张测试图再次全部逐值一致（`board_check/acceptance.json`；独立服务那次的记录改名为 `acceptance_skin_service_20261008.json`）。
独立皮肤服务还在板上（未启用），不需要时 `board\deploy_skin.py uninstall`。

仅用于工程教学与算法演示，不作为临床诊断依据。

## 一句话结论

眼底血管系统的 U-Net（34,201 参数，30 个节点，层表 / HLS 核 / 比特流都不变），只换权重、量化参数和板端参数表，
就能做皮肤镜病灶分割。在 **ISIC 2016 Task 1 官方独立测试集（379 张）** 上：

| 模型（128×128 推理） | Jaccard | Dice | 准确率 | 敏感度 | 特异度 |
|---|---:|---:|---:|---:|---:|
| FP32 | 0.8187 | 0.8911 | 0.9452 | 0.9097 | 0.9582 |
| 全 INT8（对照） | 0.8025 | 0.8781 | 0.9389 | 0.8583 | 0.9764 |
| **部署版：混合位宽（2 个 INT16 节点）** | **0.8182** | **0.8906** | 0.9449 | 0.8947 | 0.9661 |

- 量化损失：混合位宽比 FP32 平均 Jaccard 低 **0.0005**；全 INT8 低 0.016。
- **379 张测试图在 FZ3B 实板上跑过，板端 logit 与整数模型全部逐值一致**（离线 HLS 逐位参考也一致），
  所以上表就是板子实际输出的精度；板端 128 档 AI 实测 37.7 ms。
- 参照：ISIC 2016 Task 1 官方排行榜第 1 名 0.843、第 3 名 0.822、第 4 名 0.811（28 支队伍）；0.818 落在第 3、4 名之间（自算，未提交官方服务器，见下文“文献对比”）。

## 数据与许可

- 来源：ISIC 2016 Challenge Task 1（Part 1: Lesion Segmentation），官方 S3：
  `https://isic-challenge-data.s3.amazonaws.com/2016/ISBI2016_ISIC_Part1_{Training,Test}_{Data,GroundTruth}.zip`。
  900 张训练图 + 掩膜，379 张测试图 + 掩膜；图像 576×768 ～ 4288×2848，共 74 种尺寸。
- **许可：CC-0**（https://challenge.isic-archive.com/data/ ，2016 表 Task 1 一行，覆盖上述 4 个文件）。
- 官方要求的引用：Gutman D, Codella NCF, Celebi E, Helba B, Marchetti M, Mishra N, Halpern A.
  “Skin Lesion Analysis toward Melanoma Detection: A Challenge at the International Symposium on Biomedical Imaging (ISBI) 2016,
  hosted by the International Skin Imaging Collaboration (ISIC)”. arXiv:1605.01397, 2016.
- 压缩包 SHA-256 见 `data/cache/zip_sha256.json`。`data/`（约 3.6 GB）不需要分发，可由 `prep_data.py` 重建。

## 数据使用纪律（报告里照写）

| 用途 | 数据 |
|---|---|
| 训练 | 官方训练集中的 720 张（固定种子 20261007 划分，`results/split.json`） |
| 选分辨率、选检查点 | 官方训练集中留出的 180 张“验证集” |
| 量化校准（max-abs 范围） | 训练部分随机 8 张（种子 20260923，与眼底同规则） |
| 混合位宽筛选 / 确认 | 筛选：同 8 张校准图；确认：180 张验证集 |
| **最终评估** | **官方测试集 379 张，只跑一次**（`final_test.py` 写锁文件 `results/test/r128/test_set_used.txt`，再跑要 `--force`） |

分辨率、检查点、INT16 节点、阈值（p>0.5）都在看测试集之前冻结，见 `results/selection.json`（冻结时间 2026-10-07 08:59）。

## 和部署链路逐字节对齐

| 环节 | 实际系统 | 训练 / 评估里怎么做 |
|---|---|---|
| 上位机缩放 | Pillow LANCZOS 拉伸到 512×512（`tcp_image_ui/imageio.py _to_target`） | `prep_data.py` 用同一调用缓存 512×512 |
| 板端降分辨率 | `ai_view_downsample_rgb`：最近邻，等于 `x[::4, ::4]` | 同上（`common.board_downsample`） |
| 网络 | 30 节点层表，INT8/INT16 激活 | lwnet `int8_reference.py`（未改）+ HLS 逐位参考 |
| 掩膜放大 | `ai_view_mask_rgb`：每个值复制 4×4 回 512×512 | `common.board_upsample` |
| 评估分辨率 | — | 512 掩膜用 PIL NEAREST 放回原图尺寸，**在原分辨率上**算 ISIC 指标（`prep_data.py` 里断言了这一捷径与真实上采样逐像素相同） |

指标：逐张算 Jaccard / Dice / 准确率 / 敏感度 / 特异度后取平均（ISIC 2016 官方口径，按平均 Jaccard 排名）。
另给“阈值 Jaccard”（单张 <0.65 记 0，ISIC 2017 口径）供参考。

## 选推理分辨率：128×128（按验证集）

结构限制：两级池化 + 12 个 3×3 卷积，感受野约 45 像素。病灶在 512 图上常有几百像素宽，网络只能看局部颜色；
降到 128 后同一个网络能“看到”整个病灶。板端早就支持 `512>>shift` 档位（`--ai-res` / aiparam bits[4:2]），不用改代码。

| 推理分辨率 | 验证集 Jaccard | Dice | 阈值 Jaccard | 训练时间（RTX 4060 Laptop，2 万步） |
|---|---:|---:|---:|---:|
| **128** | **0.8354** | **0.9058** | 0.7957 | 10 min |
| 256 | 0.8053 | 0.8850 | 0.7339 | 29 min |
| 512 | 0.7387 | 0.8361 | 0.6213 | 56 min |

128 档的模型拿到别的分辨率上跑会明显变差（验证集：256 时 0.769，512 时 0.633）——**所以皮肤模式不能用 ROI 局部推理**（ROI 固定按 512 跑）。

板端耗时（**10-08 实测**）：128 档 AI 平均 37.7 ms（379 帧），同一板子 512 档约 564–572 ms，计算量是 512 档的 1/16。
（10-07 时按旧版本比例估计 35–60 ms，实测落在区间内。）

## 量化：规则与眼底完全相同，INT16 节点重新筛选

- 规则：`lwnet/integer_spec.md` v2 + `mixed_precision_spec.md`，代码直接复用 `lwnet/int8_reference.py`（未改）。
- 与眼底流程的两处不同（都写在 `quantize_skin.py` 文档串里）：
  1. 筛选用全部 8 张校准图（眼底用 2 张）：128 档很便宜，而且 2 张图上的病灶 Jaccard 噪声太大；
  2. 输出节点 `final` 不进 INT16 候选：板端 `unet_run()` 按 `int8_t` 交出 logit，掩膜 / 叠加 / 热力图都按 int8 处理，规范也写了“输出仍 INT8”。
     （第一次冒烟测试时筛选确实挑中了 `final`，这是排除它的原因。）
- 结果：选中 **`first_block_2`（首个编码块第一个 Conv/ReLU/BN）+ `down_path_0_block_2`**。
  `first_block_2` 正好也是眼底模型选中的 INT16 节点 —— 两个任务都说明首层输出是量化敏感点。
- 硬件代价：零。HLS 核所有激活本来就用 int16 容器存（`unet_conv/README.md`），INT16 节点放在哪一层都不增加存储和带宽；
  静态累加上界 211,613,251 < 2^31。
- 输出 scale 0.472376，病灶判定 **q ≥ 1**（等价于概率 > 0.5）。

| 子集 | FP32 Jaccard | 全 INT8 | 混合位宽 | 混合位宽下降 |
|---|---:|---:|---:|---:|
| 验证集 180 张 | 0.8354 | 0.8130 | 0.8336 | 0.0019 |
| **测试集 379 张** | **0.8187** | **0.8025** | **0.8182** | **0.0005** |

注意：平均下降小，但单张图有正有负。测试集单张 Jaccard 的 |FP32−混合| 平均 0.017，最大 0.093。

## 测试集误差分布

| 病灶面积占比 | 张数 | 平均 Jaccard | 中位数 | <0.5 的张数 |
|---|---:|---:|---:|---:|
| <5% | 40 | 0.743 | 0.811 | 4 |
| 5–15% | 89 | 0.810 | 0.841 | 4 |
| 15–40% | 159 | 0.836 | 0.872 | 4 |
| ≥40% | 91 | 0.828 | 0.871 | 6 |

全体中位数 0.861；18 张 <0.5，41 张 <0.65。`results/test/r128/panels_worst3_median3_best3.png` 是最差 3 / 中位 3 / 最好 3
（左原图、中人工标注、右部署模型）。典型失败：

1. **极小病灶**（ISIC_0011152 只占 0.3%）：128 档降采样后只剩几个像素，直接漏掉；
2. **深色皮肤 + 黄色标记贴纸**：把整片背景判成病灶；
3. **低对比度淡色病灶**：几乎不出掩膜。

## 逐位验证（`export_verify.py`、`final_test.py`）

导出层表复用 `medical_fpga/deployment/unet_network/export_network.py` 的 `build_table`（未改），格式就是 `gen_unet_c.py` 读的那份。
验证方法同 `roi_exactness.CropRunner`：把 512 基的层表按运行尺寸缩放，用真驱动 `unet_fpga.py` + `sim_device.py`（两个 HLS 核的 NumPy 逐位参考）跑整网。

| 检查 | 结果 |
|---|---|
| 金标准图 ISIC_0000010（128×128）30 个节点逐个比对 | 30/30 节点 0 差异 |
| 校准 8 + 验证 180 张最终输出 | 188/188 张 0 差异 |
| 同一参数用 512 整表跑 1 张 | 262,144 像素 0 差异 |
| **测试集 379 张最终输出** | **379/379 张 0 差异** |

## 板端：方案 A（两个程序共存，眼底代码一行不改）

- `board/gen_board.py`：把 `tcp_image_rx/ai/net/gen_unet_c.py` 当工具调用（未改），生成 `board/gen/unet_net.{h,c}`，并检查：
  - 头文件与 `src/unet_net.h` 去掉注释和空白后完全相同（只多改了未使用的 `UNET_THRESHOLD_DEFAULT` = 1 和注释）；
  - C 数组反解析回来与 `network.txt`、`params/*.bin` 逐字节相同；
  - 与眼底表的结构字段（种类、连接、通道、尺寸、ReLU）30 个节点全相同，参数块同为 41,024 字节；
    不同的只有 shift（1 个节点）、qmin/qmax（2 个）、Add/Concat 乘数（7 个）和权重内容。
- `board/stage/` = `src/` + `makefile` 的副本，替换了两份生成文件。WSL gcc 按 makefile 选项全编译 + 链接通过，0 警告（x86 主机编译，只是语法 / 链接检查）。
- `board/install_skin.sh`（板上运行）：原生编译 `tcp_image_server_skin` → `/root/skin/`；复制眼底服务 unit，只改 `ExecStart`（换程序、追加 `--ai-res 128`）并加
  `Conflicts=tcp-image-server.service`，生成 `tcp-image-server-skin.service`，**不 enable**，开机默认仍是眼底。sed 改写在 WSL 里干跑过。
- `board/skin_mode.sh skin | fundus`：一条命令切换（两个 unit 互斥，start 一个自动停另一个）。

### 上位机用法（不用改上位机）

- 「AI 分辨率」保持 512（= 用板端默认，也就是皮肤服务的 128）；**不要选 256 / 512**。
- 「AI 门限」设 **1**（界面默认 −3 是眼底的；皮肤用 −3 相当于概率 > 0.20，会偏大）。
- 不要用 ROI 框选（见上）；「形态指标」是血管专用的，皮肤模式下无意义。
- 演示图：`samples/*.ppm`（6 张测试集图，按病灶面积分位数事先固定挑选，附 `_gt.pgm` 标注）。

### 上板步骤（10-08 已按此执行并全部通过）

10-08 眼底 v4 上板时修了两个板端 bug（`ai_view.c` 连通域栈 `uint16_t`→`uint32_t`；`unet_ai.c` 条带输出多两行的缓冲区）。
`board/stage/` 已用修复后的 `src/` **重新生成**（10-08，WSL 编译 0 警告），旧的 10-07 副本作废。

```powershell
$P = 'E:\PLD\skin\.venv\Scripts\python.exe'
cd E:\PLD\skin
& $P board\deploy_skin.py status              # 只看状态
& $P board\deploy_skin.py install             # 上传 stage、板上编译、装互斥 unit；不切换，眼底继续服务
& $P board\deploy_skin.py switch skin         # 切到皮肤（会停掉眼底服务）
& $P verify_board_skin.py                     # 验收：金标准 / 门限 / 6 张演示图 / 379 张测试集，约 2–3 分钟
& $P board\deploy_skin.py switch fundus       # 切回眼底
& 'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe' E:\PLD\tcp_image\tcp_image_ui\verify_board_v4.py   # 眼底回归
```

- `deploy_skin.py` 每个动作前后都对眼底程序、眼底 unit、比特流文件做 SHA-256，变了就报错；不加载比特流。
- `verify_board_skin.py` 判定：板端回传的 logit 图按 4×4 取样还原 128×128 logit，**与整数参考逐值相等**；
  379 张测试图用**板端 logit** 重算 ISIC 指标，必须与离线 `results/test/r128/summary.json` 逐张相同；同时记录板端 AI 耗时（`ai_us`）和往返耗时。
  通过时打印 `SKIN_BOARD_ACCEPTANCE_PASS`，结果写 `board_check/acceptance.json`。
- 干跑：`board/fake_skin_board.py` 是协议相同的本机假板端（用 HLS 位级模拟器算 logit）。10-08 干跑 `--test-limit 30` 全部通过
  （`board_check/dryrun_acceptance.json`）—— 只证明脚本和协议没问题，不代表板子。
- 回退：`deploy_skin.py uninstall`（切回眼底、删皮肤 unit 和 `/root/skin`）。
- ⚠ 与眼底同样的规矩：卸 `plbdma` 之前先停当前在跑的那个服务；切换时先关掉上位机连接。

## 文献对比（只作量级参考）

官方排行榜（https://challenge.isic-archive.com/leaderboards/2016/ ，Task 1，28 支队伍，按平均 Jaccard 排名，2026-10-07 查）：

| 名次 | 队伍 | Jaccard |
|---|---|---:|
| 1 | ExB | 0.843 |
| 2 | CUMED | 0.829 |
| 3 | test（Mahmudur Rahman） | 0.822 |
| — | **本作（34,201 参数，128×128 推理，INT8/INT16，即 FPGA 参数）** | **0.818** |
| 4 | sfu-mial | 0.811 |
| 5 | TMUteam | 0.810 |
| 6 | UiT-Seg | 0.806 |
| … | 后面 22 支 | 0.799 … 0.468 |

口径说明：本作**没有提交官方评测服务器**，是用官方测试集标注按官方口径（逐张 Jaccard 取平均、原分辨率）自己算的；
排行榜是 2016 年的方法，比较只说明量级——“3.4 万参数、能在 FPGA 上逐位复现的量化模型，精度落在 2016 年参赛队伍第 3、4 名之间”。

## 局限（报告里如实写）

- 实板验收覆盖了测试集全部 379 张，但只在一块 FZ3B、一次连续运行上做；没有做长时间（上千帧）皮肤模式稳定性测试。
- 单次训练（一个随机种子），没有多种子方差；验证集 180 张也用于选检查点和分辨率。
- 128 档对极小病灶不友好；没做测试时增强、后处理（填洞、最大连通域）——这些都能提分，但为了“板上输出就是报告数字”刻意没加。
- 量化平均损失很小，但单张图会有 ±0.09 的变化。
- 与眼底结果的关系：**STARE 20 张只是开发评估**（都参与过模型选择和量化校准）；**ISIC 379 张是独立测试集**，可信度更高。
- 皮肤病灶分割不是诊断，ISIC 标注也只是一种人工勾画。

## 目录与复现

```
skin/
  prep_data.py        解压 + 512 缓存（与上位机同一缩放）+ 原分辨率指标所需的 GT 统计
  common.py           路径、划分、板端重采样、ISIC 指标
  train_skin.py       训练（GPU 增广、BCE+Dice、AdamW + 余弦）
  quantize_skin.py    校准、敏感度筛选、混合位宽、golden 张量（只用训练集）
  export_verify.py    导出层表 / 参数 + HLS 逐位参考验证
  final_test.py       测试集一次性评估（+ 379 张逐位核对）
  make_samples.py     上位机演示图
  board/              gen_board.py、install_skin.sh、skin_mode.sh、gen/、stage/
  deploy/r128/        network.txt、params/*.bin、金标准向量、verify_report.json
  results/            split / train / quant / test / selection.json
  samples/            演示 PPM
```

```powershell
$P = 'E:\PLD\skin\.venv\Scripts\python.exe'   # torch 2.14.0+cu130
& $P prep_data.py
& $P train_skin.py --shift 2 --tag r128        # 另两档：--shift 1 --tag r256；--shift 0 --batch 8 --tag r512
& $P quantize_skin.py --tag r128
& $P export_verify.py --tag r128
& $P final_test.py --tag r128                  # 只跑一次
& $P board\gen_board.py --tag r128
& $P make_samples.py
```

## 坑

1. 敏感度筛选会挑中输出节点 `final`；板端 logit 是 int8，必须排除（见上）。
2. `src/unet_net.h` 是 CRLF，且有一处空格与生成器输出不同；比较头文件要先去注释、规范化空白。
3. `make clean` 只删默认 `TARGET`；用 `TARGET=tcp_image_server_skin` 编过以后要手动删那个文件。
4. 后台串联训练的最后一条 `tail` 用了多文件 + `-3`，Git Bash 下报错导致任务“失败”，其实三档训练都正常完成。
5. 128 档模型直接拿到 512 上跑 Jaccard 掉到 0.63：模型和分辨率档位要配套部署。
