# AI 识别：从单层到整网、到上位机链路（P4 / P5 / P6）

> 2026-10-01 ~ 10-02。这一目录是"把 PLD 的 U-Net 血管分割接进 tcp_image_rx"的
> 全部脚本与验证产物。上游设计见 `../../docs/ai_inference_design.md`，交接见 `../../progress_and_handoff.md` 第九节。

## 0. 一句话现状

PL 里跑 **conv_layer + eltwise_layer 两个 HLS 算子**（一字未改的 PLD 源码）；
PS 端 **C 驱动**按 `network.txt` 逐节点调度，张量常驻 PS 内存、按**条带 + halo**
搬进片上的张量 BRAM（**A1 后 256 KB**，见 §8；A1 前是 128 KB）。

* **全分辨率 512×512**：与 PLD 自己的 golden **262144/262144 逐值一致**，**1.95 s/帧**
  （A1 前 2.11 s）；
* 分辨率可调（512/256/128/64/32），32 档整帧 94 ms；
* 上位机可按 flags 选回传 **预处理图 / 血管掩膜 / 掩膜叠加 / 数值统计**。

---

## 1. P4：整网 bring-up（32×32，逐节点 bit-exact）

**关键取舍**：`in_h/in_w` 是运行时参数 → 整网能在任意分辨率跑（权重不变）。
32×32 时全部张量峰值只占 **5120 字 / 80 KB**，那片张量 BRAM 装得下 →
**一个新比特流都没出**，直接复用 P3 的 `ai_sys.bin`。

**判据**：PLD 未修改的 `conv_layer.cpp`/`eltwise_layer.cpp` 在 Vitis HLS 里跑
`csim_design`（10 秒）逐节点 dump golden → 板上逐字节比对。

**结果**：**30/30 节点 bit-exact**（两组不同输入：im0002 抽样相位 0 / 相位 8，
相差 2597/8192 lane，都是 0 差异）。覆盖 3×3 / 1×1 / 2×2 转置 conv、Add、Concat、
MaxPool 与张量缓冲复用。

流程：
```
gen_small_vectors.py   # 512 向量 -> 32x32 向量 + plan.json(BRAM 内的 buffer 分配, 含复用与重叠校验)
run_csim_small.bat     # vitis-run --mode hls: 跑 tb_network_dump.cpp, 逐节点 dump golden/
run_p4.py              # sftp 上传 -> 板上跑(unet_board.py) -> 拉回 -> 逐节点比对
```

---

## 2. P5：接进上位机链路（协议 flags + 掩膜/叠加/统计）

### 2.1 板端驱动从 Python 换成 C

`../../src/unet_ai.c`（+ 生成出来的 `src/unet_net_data.c`）在**服务进程内**跑整网，
不再 fork Python。**同一份工作实测 2.87 ms vs Python 8.99 ms，快 3.1×**。

板上自检（`test/unet_selftest.c`，`make CC=gcc unet_selftest`）：
```
=== vectors_small : C driver vs golden ===
run: 2884 us, logits min=-86 max=14
compare: mismatch = 0 / 1024  ->  PASS
```
两组向量都 PASS。跑法：`python run_p5_c.py`。

### 2.2 控制头 v3 + flags

板子**先读 2 字节 magic 再决定读几字节**，所以老客户端照旧能用：

```
v2(旧)  4B: magic=0x4D43 + mode(u16)
v3(新)  8B: magic=0x4D44 + mode(u16) + flags(u16) + aiparam(u16)
```

| flags 位 | 含义 |
| --- | --- |
| b0 `FLAG_ECHO_PROC` | 回传**预处理图**（默认，等价于旧行为） |
| b1 `FLAG_ECHO_MASK` | 回传 **AI 血管掩膜图**（0/255 展开成 RGB） |
| b2 `FLAG_ECHO_OVER` | 回传**掩膜叠加图**（掩膜以红色叠在预处理图上） |
| b3 `FLAG_STATS` | 图像流**之前**先回一个 64B 统计块 |
| b4 `FLAG_NO_IMAGE` | 只做识别，不回传任何图像 |

`aiparam`：bit0 = AI 输入源（0 原始 RGB / 1 预处理结果），bits[15:8] = 阈值（int8，默认 -3）。

**下行顺序**：`[64B 统计块]` → 预处理图 → 叠加图 → 掩膜图（按 flags 取子集）。

统计块（64 B，全 u32 小端，magic `'SIA1'`）：
`magic/version/flags/ai_res/vessels/total/permille/logit_min/logit_max/ai_us/
components/proc_min/proc_max/mode/frame_count/预留`

### 2.3 上位机

* `tcp_image_ui/proto.py`：v3 控制头、flags、`expected_images()`、`unpack_stats()`；
  **导入时自检**，常量漂移立刻报错。
* `tcp_image_ui/imagelink.py`：`--flags proc,mask,overlay,stats`、`--ai-src raw|proc`、
  `--ai-thr N`、`--v2`、`--save-{proc,mask,overlay}`；第三个参数**既可以是内置图样名，
  也可以直接是一个图片文件路径**。
* `tcp_image_ui/app.py`：多了"回传内容"复选框、"AI 输入"下拉、"AI 阈值"输入框、
  右图"显示"选择器，底部多一行 AI 统计。

### 2.4 端到端验证（真机，用真实眼底图 `tcp_image_ui/test.ppm`）

`test.ppm` = im0002（STARE 眼底图）转成的 512×512 P6 PPM，`make_test_ppm.py` 生成。

| 用例 | flags | 板端 recv | 预处理 | AI 整网 | 回传 | PC 端总耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| 旧客户端 | v2 头 | ~21 ms | 20 ms | — | 11 ms | **55.8 ms** |
| 默认 | proc,stats | ~21 ms | 20 ms | 3 ms | 11 ms | ~60 ms |
| 掩膜 | mask,stats | ~21 ms | 20 ms | 3 ms | 13 ms | **62.9 ms** |
| 掩膜+叠加 | proc,mask,stats | 21 ms | 20 ms | 3 ms | 25 ms | **74 ms** |
| 三幅图 | proc,mask,overlay,stats | ~22 ms | 20 ms | 3 ms | ~50 ms | **94.9 ms** |
| 只算不传 | noimage,stats | 22 ms | 20 ms | 3 ms | ~0 | **42.2 ms** |
| 伪彩+掩膜 | mode3 proc,mask,stats | ~21 ms | **55 ms**(伪彩 1 像素/字) | 3 ms | 25 ms | **111.5 ms** |

真实眼底图上的 AI：`vessels=37/1024 (3.6%) comps=4 logit[-86..14]` ——
掩膜与叠加图上的红色正好落在**视盘/血管汇聚区**，是合理结果。
把 AI 输入换成"预处理结果"后变成 `324/1024 (31.6%)`，说明输入源开关确实生效。
阈值从 -3 收到 0，血管 37→28，方向正确。

---

## 3. P6-1 提速：瓶颈不在 AI 了

| 阶段 | 32×32 整网 | 说明 |
| --- | --- | --- |
| TCP 收整帧 789512 B | 21–32 ms | 板子侧，~197 Mbit/s |
| PS↔PL 预处理（灰度） | 20 ms | 已是"一次搬 4 像素"优化后的值 |
| **AI 整网（30 节点）** | **3 ms** | 从 Python 的 9 ms 降到 3 ms |
| 回传 1 幅图 | 11 ms | 512 行包 + 逐行 CRC |
| 回传 3 幅图 + 统计 | ~50 ms | 线性于幅数 |

**结论（如实说）**：P6 的"GDMA 搬运 / 片上缓存"是为**全分辨率**准备的——
32×32 下张量总共才 80 KB，AI 只占整帧的 ~4%，已经不再是瓶颈。
现在真正的瓶颈是 **TCP 收图 + PS↔PL 预处理 + 多幅回传**。
要做"实时"，下一步该动的是**预处理通路**（比如把 img_proc 也搬到 BRAM 直读），
而不是 AI 的搬运。

---

## 4. P6-2 换权重 = 换功能（真机验证）

**做法**：`make_model_b.py` 把 `final` 层的 `mult` 与 `offset` 同时取负。
conv 的 requantize 是 `v = acc*M + offset` → 取负后 `v' = -v`，而
"对 |v| 就近舍入再取符号"是对称的、qmin/qmax 也对称 → 于是
**logit_B == -logit_A（逐值精确）**。

**离线校验**（`check_model_b.py`）：
```
模型 A logit 范围 [-86..14]      模型 B logit 范围 [-14..86]
logit_B == -logit_A 的像素: 1024/1024  ->  PASS
掩膜(thr=-3): A 血管 37 (3.6%), B 血管 1011 (98.7%), 两掩膜相同的像素 24/1024 (2.3%)
```

**上板**（`run_model_swap.py`：部署模型 B → 跑 test.ppm → 恢复模型 A）：
```
AI: 32x32 thr=-3  vessels=1011/1024 (98.7%) comps=1 logit[-14..86]
```
与 golden 预测完全一致，掩膜图就是模型 A 的补集。

**这就是"换模型"的接口**：换 `network.txt` + `params/*.bin`，
跑 `gen_unet_c.py` 重新生成 `src/unet_net.h` + `src/unet_net_data.c`，重新编译部署。
**RTL 一行不用改、驱动一行不用改。**

---

## 5. 文件速查

| 文件 | 作用 |
| --- | --- |
| `gen_small_vectors.py` | 512 向量 → 32×32 向量 + `plan.json`（`P4_OFFSET` 换抽样相位） |
| `tb_network_dump.cpp` | HLS C 仿真 testbench，逐节点 dump golden |
| `run_csim_small.tcl` / `.bat` | 启动 C 仿真（`P4_VECDIR` 指定向量目录） |
| `gen_unet_c.py` | 向量 → 板端 C 用的 `src/unet_net.h` + `src/unet_net_data.c`（`plan.json` 可选；没有就是条带调度版） |
| `unet_board.py` | **Python 版**整网调度器（32×32 bring-up 用，`--bench N` 出基准） |
| `run_p4.py` | 上板跑整网 + 逐节点比对 golden（32×32 片上方案） |
| `run_fullres.py` | **全分辨率**：档位回归 + 512 对 PLD golden |
| `dbg_fullres.py` | 单档位调试（`<shift> <x.bin> <golden.bin> [debug]`） |
| `run_res_sweep.py` | 部署 + 用 test.ppm 做分辨率扫描（512/256/128/64/32） |
| `run_p5_c.py` | 上板验证 **C 驱动**（`src/unet_ai.c`）与 golden 一致，并做 C vs Python 计时 |
| `run_p5_e2e.py` | 部署 + 8 组 flags 组合的端到端验证 |
| `run_testppm.bat` | 用真实眼底图 `test.ppm` 跑 6 组用例 |
| `make_test_ppm.py` | im0002 → `tcp_image_ui/test.ppm`（512×512 P6 PPM） |
| `make_model_b.py` / `check_model_b.py` / `run_model_swap.py` | 换权重的生成 / 校验 / 真机演示 |
| `load_ai_sys.py` / `peek_pl.py` / `probe_board.py` / `rshell.py` | 加载比特流与只读探测 |
| `vectors_small/`、`vectors_small_im0001/`、`vectors_small_alt/` | 三组向量与 golden |
| `e2e_out/` | 回传的预处理图 / 掩膜 / 叠加图 / 界面截图 |
| `net_out/` | P4 逐节点输出 |

## 6. 已知边界（如实说明）

* **全分辨率（512×512）已跑通**：条带（strip+halo）调度，结果与 PLD 自己的 golden
  **262144/262144 逐值一致**，耗时 **2.1 s/帧**。详见 `../../docs/ai_full_resolution_strip_plan.md`。
* 想快就降分辨率：`--ai-res 512|256|128|64|32`（板端 `--ai-res` 是默认值，
  客户端可以在 `aiparam` bits[4:2] 里逐帧覆盖）。实测 512→2185 ms、128→205 ms、32→94 ms。
* 板上跑的是我们自己的 `ai_sys` 比特流；**重启板子会回到厂商 PL**，
  要重新 `echo ai_sys.bin > /sys/class/fpga_manager/fpga0/firmware`（或跑 `load_ai_sys.py`）。

## 7. P4-full：全分辨率（条带 + halo）

`src/unet_ai.c` 现在跑的是**条带调度**：张量常驻 PS 内存（30 个节点共 ~85 MB），
那片张量 BRAM（**A1 后 256 KB**）只当"一层条带的暂存"。

* 行依赖：3×3 conv 要 `y±1`（喂 S+2 行、丢首尾各 1 行）；1×1 与转置**无 halo**；
  Add/Concat 逐像素；MaxPool 要 `2y,2y+1`。完整推导见设计文档第 2 节。
* **图像边界的零填充由我们自己补**（缓冲第 j 行 = 真实行 `y0-1+j`，越界写 0），
  不依赖 IP 自己的边界填充 —— 否则真边界会被多填一次零。
* 验证：`ai/net/run_fullres.py`（档位回归 + 全分辨率对 PLD golden）；
  调试用 `dbg_fullres.py <shift> <x.bin> <golden.bin> [debug]`。

```
shift=0 -> input 512x512, output 512x512
run: 2106523 us   timing: 2106.57 ms/frame
compare: mismatch = 0 / 262144  ->  PASS
```

⚠ **踩过的坑：`/dev/mem` 映射区不能用 `memset`**。glibc 的 `memset` 对大块对齐内存会用
`DC ZVA`，而 `DC ZVA` 对 **Device 内存是未定义行为** → 直接 `SIGBUS`，
而且崩在第一个"补零行"上，非常难猜。`memcpy` 反而安全（走 `ldp/stp`）。
现在代码里：数据搬运用 `memcpy`，补零用自己写的 volatile 32bit 循环。

## 8. A1：张量 BRAM 128 KB → 256 KB（2026-10-02）

把条带暂存加大到片上能放下的最大（128 KB 版用 160/216 块 RAMB36，翻倍到 256 KB
是 192/216 = 88.9%；384 KB 会超）。只改了 BD 的 `Write_Depth_A` ＋ 地址段范围
＋ `UNET_BRAM_WORDS`，**RTL 一行没改**。

* 重出比特流：0 err/0 warn/0 CW，**WNS +2.013 / WHS +0.0115**；
  `.bin` md5 `9f141b1b…`（旧 `3e9051a4…`，备份 `../../bitstream/_pre_a1/ai_sys_128kb.bin`）。
* `run_fullres.py`：**512 2106.6 → 1946.8 ms**、32 档 9.07 → 8.62 ms，
  两档 `mismatch = 0` **PASS**，logit 逐值不变。
* ⚠ **只快 7.6%** —— 输出搬运量与条带高度无关、halo 只在输入侧、params 拷贝只占 0.9%；
  预算脚本 `_a1_estimate.py` 事前算出 5.6%，实测 7.6%。
  完整分析见 `../../docs/ai_full_resolution_strip_plan.md` §9.4。
* 部署脚本：`run_a1_verify.bat`（跑 `run_fullres.py`）、`run_a1_deploy.bat`（上传 src
  ＋ 板上重编 ＋ 重启服务）。

## 9. 第 0 步：整网时间分解（2026-10-02，实测）

`src/unet_ai.c` 加了插桩（`unet_stats_t` 多了 `ap_us/put_us/get_us/zero_us/strips/
put_words/get_words/param_words`；设 `UNET_PROFILE=1` 还会按节点 dump）。
跑法：`python _profile_run.py`（上传 + 板上重编 `unet_selftest` + 跑 512/32 两档）。

**512×512，整帧 1948.8 ms**：

```
profile us : ap(PL)=531361  put(->BRAM)=287858  get(BRAM->)=1111980  zero=2091  other=15532
profile mem: put=7322564 get=5341184 params=48068 words  strips=826
```

| 段 | 占比 | 吞吐 |
| --- | --- | --- |
| **get BRAM→PS** | **57.1%** | 76.9 MB/s（208 ns/字） |
| ap PL 计算 | 27.3% | — |
| put PS→BRAM | 14.8% | 407 MB/s（39 ns/字） |

**读比写慢 5.3×**，是 PS 读 Device 内存的强序代价 → **下一步做重叠（双缓冲）**。
完整分析见 `../../docs/ai_full_resolution_strip_plan.md` §10。

## 10. A-ov（读回与计算重叠）—— **被硬件挡住，已回退**（2026-10-02）

重叠"读回"(1112ms) 与"计算"(531ms) 理论上能把计算藏起来。做了、上板了，**结果是错的**：

| 版本 | 512 耗时 | 与 golden |
| --- | --- | --- |
| A1（不重叠） | 1949 ms | **0/262144 PASS** |
| A-ov（重叠） | **1626 ms** | **253787/262144 FAIL** |
| A-ov 布局 + `UNET_NOAOV=1`（只关重叠） | 1992 ms | **0/262144 PASS** |

第三行布局/条带数/字数完全相同，只把读回挪回 wait 之后 → PASS。
⇒ **布局对，坏在"CPU 在 IP 运行期间访问同一片 BRAM"**（本工程第一次并发访问 BRAM）。
重叠本身值 18%，但数据不能用。已回退到单缓冲（A1 语义）。
代码存档 `_unet_ai_aov_overlap_reference.c.txt`；完整分析见
`../../docs/ai_full_resolution_strip_plan.md` §11。

## 11. A3b（int8 打包）**实测无效**（2026-10-02）

微基准（`_memtest.py` / `_memtest2.py`）：

```
PL BRAM 0xB0   197.7 ns/次  (128bit 与 64bit 访问同价!)   80.9 MB/s
PS DDR  0x40   102.9 ns/次                               155.5 MB/s
heap(可缓存)     4.2 ns/次                              3810   MB/s
```

**读 BRAM 是"访问次数 × ~198 ns"主导，与每次搬多少字节无关** ⇒ 只压窄不减少
访问次数的 int8 打包 **0 收益**。198 ns 里 ~103 ns 是 `/dev/mem` Device 映射语义、
~95 ns 是 PL 通路。有效的是"改映射（Normal-NC/可缓存，需内核模块）"或"上 DMA"，
不是"减字节"。见设计文档 §12。

## 12. `plbram.ko` 上板实测（2026-10-02）—— **写快 2.7×、整体 −9.4%；但读不变**

微基准（`_plbram_bench.c`，同代码同 16B 步长）：

| 目标 | 操作 | `/dev/mem` | `/dev/plbram` |
| --- | --- | --- | --- |
| PL BRAM | 读 | 197.8 ns | **197.7 ns（不变）** |
| PL BRAM | 写 | 39.5 ns | **14.8 ns（2.67×）** |
| PS DDR | 读 | 102.7 ns | **102.9 ns（不变）** |
| PS DDR | 写 | 9.4 ns | **3.2 ns（2.9×）** |

⇒ **非缓存读不流水，跟映射属性无关**（连 PS 自己的 DDR 都一样）；Normal-NC 只帮写。
整帧 512 **1950.9 → 1767.9 ms**，golden 仍 **0/262144 PASS**；
服务端到端 `ai_us` 1948708 → **1765383**，掩膜逐值不变。

顺带用新映射**重试 A-ov → 仍 253787/262144 FAIL**（与用 `/dev/mem` 时失配数一模一样）
⇒ 并发破坏在 PL 互联那层，模块救不了。完整分析见设计文档 §14。
