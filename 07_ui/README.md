# 上位机（单文件版，2026-11-15 打包）

> 心超工作站 + 眼底平台扩展，主体是**一个 `workstation.py`**（18 个模块机械合并），
> 另加 `echo_ws.py` / `proto.py` 两个**必须**同目录存放的模块（关键帧、现场两种模式依赖）。
> 启动器在**上一级目录**：`启动上位机.bat`。

## 怎么用

| 操作 | 命令 |
| --- | --- |
| 正常启动（默认连 192.168.1.254:5060） | 双击上一级的 **`启动上位机.bat`** |
| 先自检（依赖 / 路径 / 能不能连上板子，不开界面） | `启动上位机.bat --check` |
| 换板卡地址 | `启动上位机.bat 192.168.1.x 5060` |
| 留一个黑窗口方便看报错 | `启动上位机.bat --console` |
| 只开眼底那套 | `启动上位机.bat --fundus` |

启动器会自动挑一个**能 import 全套依赖**的解释器（找的顺序：本目录 `.venv` →
`%USERPROFILE%\anaconda3\envs\srtp-vision` → `Python314` → `Python313` → Vivado 自带 python 3.13），
所以机器上装了几个 Python 也不会挑错。

## 依赖

`requirements.txt` 里是这一套（本机已验证可用：Python 3.14.7 + Tk 9.0.4）：

```
numpy / scipy / opencv-python / Pillow / scikit-image
```

tkinter 是 CPython 自带的，`pip` 装不了；`import tkinter` 失败要修 Python 安装本身。

装法：`py -3.14 -m pip install -r requirements.txt`

## 这个目录里都是什么

| 文件 | 作用 |
| --- | --- |
| `workstation.py` | **主程序**：心超工作站（快照/关键帧/现场三种模式、双平面 Simpson、EF、节律、应变为算法层、证据报告）+ 眼底平台扩展（AI、ROI、置信度、形态、大图精查）。约 10 450 行，由原来 18 个模块合并而成 |
| `echo_ws.py` | **关键帧 / 现场模式的算法层**：读 AVI、t3 编码、单平面圆盘法容积、逐拍分析与校准、质控、证据报告。合并脚本的 `ORDER` 里没有它，所以必须作为独立文件一起拷（2026-11-15 首版打包漏了，报 `No module named 'echo_ws'`） |
| `proto.py` | `echo_ws` 的板端协议常量（`PP_OP_MASK` / `PP_OP_CLEAN` / `pp_mask_array`），只被 `pp_request` / `pp_clean` 用到 |
| `check.py` | 自检：依赖、资产路径、配置读取、连板子 + `caps` |
| `echo_kf_config.json` | 关键帧模式的参数（质量灯、保形 EF 区间那套数字） |
| `echo_heart_config.json` | 节段室壁运动 + 右心参数 |
| `camus_live_model.json` | 现场模式的模型参数 |
| `echonet_config.json` / `tile_config.json` / `tile_pyramid.json` | EchoNet 与 tile 引擎的配置 |
| `requirements.txt` | 依赖清单 |
| `wallmotion/results/` | 室壁运动结果（没有也能跑，只是那一栏空着） |

**资产全部相对本目录解析**（打包时把 `PLD_DIR` 改成了 `HERE`），所以整个 `上位机` 文件夹
可以随便搬 —— 只要和 `启动上位机.bat` 的相对位置不变（启动器按 `%~dp0上位机\` 找）。

## 和板子的关系

界面本身不连板子也能开；AI / EF / 应变这些要板端 `tcp-image-server` 在跑：

```sh
# 板上
systemctl is-active tcp-image-server     # 要 active
ss -ltn | grep 5060                      # 要 LISTEN
```

连不上时先看这两条 —— 2026-11-15 有过一次"上位机打不开"，其实是板端 server 崩在
SIGBUS 循环里（`-x 0xA0000000` 读一个已删掉的 img_proc 寄存器），跟 PC 这边无关。

## 改代码注意

`workstation.py` 是**机械合并**出来的（源在
`fpga-echo-workstation/tcp_image/tcp_image_ui/`，18 个模块）。要改逻辑建议改那边的模块再重新合并
（合并脚本见 `统一驱动与单文件上位机_20261115/03_脚本/merge_ui.py`），不要直接在这个大文件里改——
下次重新合并会被覆盖。

**重新打包必查**：`merge_ui.py` 的 `ORDER` 只有 18 个模块，`echo_ws` 不在里面，而 `heartview`
里留着真实的 `import echo_ws`（两处：`echo_params()` 与 `EchoView.__init__`）。所以每次重打包
都要把源目录的 `echo_ws.py`、`proto.py` 一并拷进本目录，否则关键帧 / 现场两个模式切过去就报
`No module named 'echo_ws'`。不要试图把 `echo_ws` 加进 `ORDER` 内联合并——它和已合并模块
在 `clean` / `_png` / `_font` / `_edge` 上同名不同义，内联会静默串味。

**未随包提供的模块，其界面入口已移除**（2026-11-15）：`echowin`（实时心超工作站浮动窗）、
`ctwin`（CT 整卷）、`progwin`（PL 程序台）、`regwin`（配准）、`strainwin`（应变）。
当时的表现是"按钮／菜单还在、点了没反应"——因为 `pythonw` 下 `sys.stderr` 为 `None`，
Tk 回调里的 ImportError 既不弹框也不留日志。现已从 `workstation.py` 删掉对应入口：

| 位置 | 删掉的内容 |
| --- | --- |
| 主界面「专项工作站」按钮区 | `PL 程序台` / `CT 整卷` / `心超工作站`（留下 `序列跟踪`、`大图精查`） |
| 菜单「工具」 | 上述三条命令（留下 `序列跟踪`、`大图精查`） |
| `App._open_progwin` / `_open_ctwin` / `_open_echowin` | 三个方法整体删除 |
| 快照模式「左心室面积曲线」卡头 | `连续帧配准…` / `心肌应变…` 两个按钮，以及 `_open_reg` / `_open_strain` / `_child` |
| 平台扩展窗口 | `CT 脾脏整卷` / `PL 程序台` 两个页签（`PROGRAMS` 只剩眼底血管、皮肤镜）与 `_page` 的 `ct` / `prog` 分支 |
| 文案 | 快照模式说明里的"连续帧配准"、平台扩展状态栏与 docstring 里的 CT / PL 字样 |

板端的 `job_reg` / `job_strain` / `ct_volume` 协议代码仍然保留在 `workstation.py` 里（只是没有界面），
需要这些窗口就用源工程 `tcp_image_ui/workstation.py` 直接跑。
**下次重新合并会把上面的删除全部还原**，需要按本表再摘一遍（或把这段改成合并后的后处理脚本）。

## 预处理与掩膜文案（2026-11-15 改）

板端 PL 预处理（模式 0–6 + 阈值分割）已从流水线上移除，界面只摘入口，**协议一个字节没动**：

| 项 | 现在的行为 |
| --- | --- |
| 左侧「PL 预处理」面板 | 整块删除（模式下拉 / 分割门限滑块 / Otsu / 备注） |
| 菜单「处理」 | 整个删除（原模式单选 + 自动阈值） |
| 回传内容「预处理图」 | 勾选框删除；`var_f_proc` 仍默认 `True`，`FLAG_ECHO_PROC` 照旧请求，proc 流留着给 auto 取图兜底 |
| AI 推理「输入」 | 下拉删除；`AIPARAM.src` 恒发 `AIPARAM_SRC_RAW`（命令行 `--ai-src` 不再生效） |
| 显示「预处理图」 | 从 `SHOW_CHOICES` 移除（`SHOW_LABELS` 里保留 `proc` 的中文名，只用于回图说明） |
| 控制头 `mode` / `thr` | 字段保留，固定发 `5 原图直通` + `CTRL_THR_DEFAULT`；`--mode` / `--thr` 仍可覆盖（无界面入口，只作调试） |

掩膜在界面上的叫法随程序变（`mask_text(program)`）：眼底血管 = **血管掩膜**，皮肤镜病灶 = **皮肤镜掩膜**。
切程序时由 `App._refresh_mask_text()` 同步四处：左侧「回传内容」勾选框、「AI」菜单、「视图」菜单、
「板子回传」卡头的显示下拉；回图说明（`_show_label`）和大图精查窗口的显示下拉也一并跟随。

**重打包后需重做**：预处理面板/菜单与 `_on_mode_change` / `_sync_thr_widgets` / `_auto_thr` 的删除、
`SHOW_CHOICES` → `show_choices()` 的改造、`mask_text()` 与 `_refresh_mask_text()`，
以及 `_set_program()` 里那一行 `a._refresh_mask_text()`。

## 2026-11-16 提速改动（WS_OPT_V1）

这一版把 PC 侧每帧白烧的 30~40 ms 压到 4~6 ms，冷启动 812 ms → 288 ms，
**屏幕上看到的结果逐字节不变**（等价性验证见 `../上位机优化_20261116/04_报告/上位机提速报告_20261116.md`）。

改了 6 件事，全部在 `workstation.py` 里，代码里搜 `WS_OPT_V1` 能看到每一处：

| # | 位置 | 改动 | 效果 |
| --- | --- | --- | --- |
| 1 | `imageio.make_photo` | PPM+base64 交给 Tk → Pillow `ImageTk` 直传 | 23 ms → 1.2 ms/图 |
| 2 | `App._rebuild_views` | 热力图 / 不确定带改成按需生成（`_LazyViews`） | 8.0 → 1.5 ms/帧 |
| 3 | `App._load_logits` | 「与板端逐字节一致」自检 1 秒最多复算一次 | 3.5 → 0.1 ms/帧 |
| 4 | `App._poll` / `EchoView._poll` / `SeqWindow._poll` | 轮询间隔按 busy 自适应（50→5 ms，30→4 ms） | 每帧少等约 20 ms |
| 5 | 文件头 | `scipy.ndimage` / `skimage` 改惰性导入（`_LazyModule`） | 启动少等 330 ms |
| 6 | `make_gradient` / `make_colorbars` / `make_checker` | 逐像素 Python 循环 → numpy | 34 ms → 0.2 ms/张 |

**重新合并后要补回来**：先合并，再跑 `../上位机优化_20261116/05_交付/apply_opt.py <新的 workstation.py>`。
脚本是幂等的，已经打过会跳过，不会叠加。

**别动的地方**（试过没用，别再花时间）：socket 收发缓冲（1 MB / 4 MB / 16 MB / 交给系统，reply 一模一样）；
`_recv_exact` 的那两次拷贝（< 0.2 ms）。瓶颈在板端，不在 PC 收包。

---

## 2026-11-18 两笔：预览按输入分辨率 + 像素标尺

> 这两笔只在 `workstation.py` / `echo_ws.py` 里，协议、板端一律没动。
> 详见归档 `E:\fpga\旧版归档_20261119\05_2026pld变更记录\12_预览原分辨率与像素标尺_20261118\说明.md`。

**（1）预览按输入分辨率。** 以前预览永远是 448 = 112 网格 ×4 放大，看着糊。现在：

* 录屏（超声机 AVI，非 112）→ 按**原视频分辨率 1:1** 画整幅，掩膜从 112 网格放大到扇区方块再贴回去，轮廓在缩放之后才画；
* 112 的视频（EchoNet）/ 现场模式（板上只回 112）→ 与改造前一样 448，不缩水；
* 超出屏幕可用区等比缩到装得下（笔记本 1366×768 上 800×600 会画成 488×366）。

**（2）像素标尺。** 视频卡片下面多一行「像素标尺」：鼠标悬停读坐标（原视频像素 / 112 网格），
按住 **Shift 拖拽**（或点「量距」按钮再拖）量两点，显示像素长度与毫米；毫米沿用右上角
「标尺定标…」得到的 mm/像素，没定标就只报像素。坐标口径没变——点击纠错仍按 448 视图存。
