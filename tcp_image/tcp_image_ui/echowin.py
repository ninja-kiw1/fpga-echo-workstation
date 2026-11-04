# -*- coding: utf-8 -*-
"""echowin.py —— ③「实时心超工作站」：整段心超视频在 FPGA 上逐帧分割左心室，PC 上逐拍分析。

流程：打开 EchoNet-Dynamic 的 .avi（112x112）
  -> 每个分析帧拼 t3 负载（前约 40 ms / 本帧 / 后约 40 ms，补边到 128）-> 紧凑帧送板（模型库 echonet_lv，2 帧在途）
  -> 板端 int8 logit 裁回 112 -> 左心室掩膜 -> 单平面圆盘法容积
  -> 容积曲线上找舒张末 / 收缩末，逐拍 EF（中位数，按训练集拟合的关系校准）、心率、质控、事件时间线
  -> 「生成证据报告」写一份 HTML（关键帧、曲线、逐拍表、质控、计算过程）。
实时模式：每 k 帧分析 1 帧（k = ceil(帧率 / 25)），并按视频自身的时间节拍送帧，模拟边采集边分析；全帧模式每帧都算、尽快跑完。
计算部分在 echo_ws.py，与离线评测（E:/PLD/echonet）一致。
运行方式（顶栏）：
  板上整病例（默认，板端 v5.12+）：PC 解码视频（OpenCV），灰度帧整段交给板子一次，编码、推理、清理、容积、逐拍分析、
    校准、质控全在板上做（jobs.echo_ef / 板端 src/job_echo.c），逐帧结果边算边回来显示；实时模式板上按视频帧率节拍处理。
  逐帧流式：老流程（PC 编码和分析，每帧一个紧凑帧）。两种方式结果相同（verify_board_echo_jobs.py）。
模式「关键帧」（默认，板端 v5.21+，echo_ef mode 2，只走板上整病例）：①PL 只在约 1/6 的帧上分割，其余帧板上 PS 块匹配跟踪；
  ③EF 下方给 80% 区间（跨过 40% / 50% 提示复核，90% 区间写进报告）；②质量灯只作原因提示。参数 echo_kf_config.json；
  板上结果与主机参考逐位一致（verify_board_echo_kf.py）。
运行方式「板上信号源（现场）」（板端 v5.22+，echo_ef mode 3）：帧不由 PC 上传，而是板上的信号源（E:/PLD/live/src 的 .y8 测试源，
  已传到板上 /root/echo_src；采集卡到了以后是 /dev/video0）按帧率"采到"，板子自己找出动的 B 超画面、缩到 112、逐帧跑 ①②③；
  窗口逐帧显示轮廓、容积曲线和滚动 EF（板端 v5.23+ 每帧连图像一起回，v5.22 运行中只画轮廓、结束后补图像），
  结束后与关键帧模式一样给 80% 区间、质量提示、报告，另给画面方块和延迟。「停止」= 断开连接，板上任务随之结束（没有最终结果）。
仅用于工程教学与算法演示，不作为临床诊断依据；EchoNet-Dynamic 数据不得外传。
"""

import csv
import json
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from collections import deque
from tkinter import filedialog, messagebox, ttk

import jobs
import proto
import timeline
import uistyle
from uistyle import FONT, FONT_MONO

FONT_BIG = ("Microsoft YaHei UI", 20, "bold")
FONT_MID = ("Microsoft YaHei UI", 12, "bold")
VIEW = 448
CW, CH, TH = 560, 220, 84
PLOT_L, PLOT_B = 44, CH - 22                          # 曲线区左边界 / 底边（底下是时间刻度）
HERE = os.path.dirname(os.path.abspath(__file__))
ECHONET_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "EchoNet-Dynamic"))
PLD_DIR = os.path.normpath(os.path.join(HERE, "..", ".."))
MODES = (("keyframe", "关键帧：PL 约 1/6 帧，其余板上跟踪"),       # 要板端 v5.21+（运行时查 caps）
         ("realtime", "实时：跟上采集（每 k 帧分析 1 帧）"), ("full", "全帧：每帧都分析"))
QC_TXT = {0: ("通过", "#080"), 1: ("注意", "#c80"), 2: ("不可靠", "#c00")}
RUNS = (("job", "板上整病例（PC 只显示）"), ("stream", "逐帧流式（PC 分析）"), ("live", "板上信号源（现场）"))
LIVE_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "live", "src"))
CAPTURE = ("采集卡 video0（YUYV 640×480，最长 20 s）", dict(kind="v4l2", name="video0", fps=60.0, frames=1200,
                                                         video=None, truth=None))
CROPS = ("自动找画面（前 1 s 里动的方块）", "固定（测试源的真值方块）")


class _Stopped(Exception):
    pass


def live_sources():
    """现场模式的信号源：E:/PLD/live/src/index.json 里的 .y8（make_y8.py 做的，要先传到板上 /root/echo_src）+ 采集卡。"""
    out = []
    try:
        import json
        with open(os.path.join(LIVE_DIR, "index.json"), encoding="utf-8") as f:
            idx = json.load(f)
        for fn, m in sorted(idx.items()):
            out.append(("%s（%d 帧 %.0f fps，%d×%d，放大 %g）" % (fn, m["frames"], m["fps"], m["W"], m["H"], m["zoom"]),
                        dict(kind="file", name=fn, fps=m["fps"], frames=m["frames"], video=m["video"],
                             truth=(m["x"], m["y"], m["side"]))))
    except (OSError, ValueError, KeyError):
        pass
    return out + [CAPTURE]


_TAG_FONT = []


def _tag_font():
    """视频左上角 ED / ES 标记的字体（PIL 默认字体只有 10 像素高，448 的画面上几乎看不见）。"""
    if not _TAG_FONT:
        from PIL import ImageFont
        for name in ("arialbd.ttf", "msyhbd.ttc", "DejaVuSans-Bold.ttf"):
            try:
                _TAG_FONT.append(ImageFont.truetype(name, 22))
                break
            except OSError:
                continue
        else:
            _TAG_FONT.append(ImageFont.load_default())
    return _TAG_FONT[0]


def en_model():
    return next((m for m in proto.MODELS if m["name"] == "echonet_lv"), None)


class EchoWindow(object):
    def __init__(self, app):
        self.app = app
        try:
            import numpy  # noqa: F401
            import scipy  # noqa: F401
            import cv2  # noqa: F401
            import echo_ws
        except ImportError as e:
            messagebox.showerror("实时心超工作站", "需要 numpy + scipy + OpenCV（%s）" % e, parent=app.root)
            raise
        self.np, self.ws = numpy, echo_ws
        self.model = en_model()
        self.conf = echo_ws.load_config()
        if self.model:
            echo_ws.SCALE[0] = self.model["scale"]
        echo_ws.THRESH = tuple(self.conf.get("qc_thresholds", echo_ws.THRESH))
        self.top = tk.Toplevel(app.root)
        self.top.title("实时心超工作站（③：FPGA 逐帧分割左心室 → 逐拍 EF / 心率 / 质控 / 证据报告）")
        self.queue = queue.Queue()
        self.busy = False
        self.frames = self.fps = self.name = None
        self.ref = self._load_filelist()
        self.hmc = self._load_hmcqu()
        self.photos = {}
        self.stop = None
        self.result = None
        self.open_report = True                      # 生成报告后用浏览器打开（自检脚本关掉）
        self._reset_state()
        self._build()
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.after(30, self._poll)

    def _reset_state(self):
        self.idx, self.masks, self.vols, self.infos, self.qs = [], {}, [], [], {}
        self.keyv, self.kf_cfg, self.kf_res = None, None, None      # 关键帧模式：每帧 0 / 1、所用配置、板上 ②③ 结果
        self.heart, self.hc_cfg, self.rh_frames = None, None, {}    # 全心扩展（v5.24）：板上结果、所用配置、帧 -> 右心掩膜
        self.result, self.ai_us, self.t_render, self.t_an = None, [], 0.0, 0.0
        self.pp_frames, self.pp_ms = 0, []           # 最大块 + 填洞在 PL（pp_layer）上做的帧
        self.cur = 0
        self.stats = None

    @staticmethod
    def _load_filelist():
        p = os.path.join(ECHONET_DIR, "FileList.csv")
        ref = {}
        if os.path.exists(p):
            with open(p, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    n = r["FileName"] if r["FileName"].endswith(".avi") else r["FileName"] + ".avi"
                    ref[n] = (float(r["EF"]), r["Split"])
        return ref

    @staticmethod
    def _load_hmcqu():
        """HMC-QU A4C 标注（E:/PLD/wallmotion/results/labels_a4c.json，节段心梗 0 / 1）+ 划分，只作对照显示。"""
        d = os.path.join(PLD_DIR, "wallmotion", "results")
        try:
            with open(os.path.join(d, "labels_a4c.json"), encoding="utf-8") as f:
                rows = json.load(f)["rows"]
            with open(os.path.join(d, "split.json"), encoding="utf-8") as f:
                split = json.load(f)["split"]
        except (OSError, ValueError, KeyError):
            return {}
        return {r["name"] + ".avi": (r["seg"], split.get(r["name"], "?")) for r in rows}

    # ---------------------------------------------------------------- 界面
    # 顶栏：打开 / 模式 / 停止 / 报告 / 开始；左：视频卡片；右：容积曲线、事件时间线、结果三张卡片；
    # 下：进度卡片；底部声明。
    def _build(self):
        t = uistyle.window(self.top, None)
        tools = uistyle.topbar(t, "实时心超工作站", "EchoNet · ③")
        ttk.Button(tools, text="打开视频…", command=self._open).pack(side="left")
        uistyle.vsep(tools)
        uistyle.label(tools, "模式", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 4))
        self.var_mode = tk.StringVar(value=MODES[0][1])
        ttk.Combobox(tools, textvariable=self.var_mode, width=34, font=FONT, state="readonly",
                     values=[m[1] for m in MODES]).pack(side="left")
        uistyle.vsep(tools)
        uistyle.label(tools, "运行方式", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 4))
        self.var_where = tk.StringVar(value=RUNS[0][1])
        ttk.Combobox(tools, textvariable=self.var_where, width=20, font=FONT, state="readonly",
                     values=[m[1] for m in RUNS]).pack(side="left")
        uistyle.vsep(tools)
        self.btn_rep = ttk.Button(tools, text="生成证据报告", command=self._report, state="disabled")
        self.btn_rep.pack(side="left")
        self.btn_tl = ttk.Button(tools, text="时间线", command=self._timeline, state="disabled")
        self.btn_tl.pack(side="left", padx=(6, 0))
        self.btn_stop = ttk.Button(tools, text="■ 停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(6, 0))
        self.btn_run = uistyle.primary_button(tools, "▶ 开始分析", self.run)
        self.btn_run.pack(side="left", padx=(8, 0), pady=8)

        body = tk.Frame(t, bg=uistyle.C_BG)
        body.grid(row=1, column=0, sticky="n", padx=12, pady=(12, 0))
        lcol = tk.Frame(body, bg=uistyle.C_BG)
        lcol.grid(row=0, column=0, sticky="n", padx=(0, 12))
        left, lb = uistyle.panel(lcol, "视频", "轮廓 = 板端分割的左心室")
        left.pack(fill="x")
        self.lab_v = uistyle.image_slot(lb, VIEW, VIEW, bg="#000")
        self.lab_v.master.pack()
        self.var_ft = tk.StringVar(value="（未载入）")
        uistyle.label(lb, var=self.var_ft, font=FONT_MONO, anchor="w").pack(fill="x", pady=(6, 0))
        self.scale = ttk.Scale(lb, from_=0, to=0, orient="horizontal", length=VIEW,
                               command=lambda v: self._show_frame(int(float(v))))
        self.scale.pack(pady=(4, 0))
        fl, bl = uistyle.panel(lcol, "现场信号源", "运行方式选「板上信号源（现场）」时用")
        fl.pack(fill="x", pady=(12, 0))
        self.sources = live_sources()
        self.var_src = tk.StringVar(value=self.sources[0][0])
        ttk.Combobox(bl, textvariable=self.var_src, width=50, font=FONT, state="readonly",
                     values=[s[0] for s in self.sources]).pack(fill="x")
        rc = tk.Frame(bl, bg=uistyle.C_CARD)
        rc.pack(fill="x", pady=(6, 0))
        uistyle.label(rc, "画面方块", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 4))
        self.var_crop = tk.StringVar(value=CROPS[0])
        ttk.Combobox(rc, textvariable=self.var_crop, width=32, font=FONT, state="readonly",
                     values=list(CROPS)).pack(side="left")
        fh, bh = uistyle.panel(lcol, "全心", "板端 v5.24：节段室壁运动 + 右室紫 / 右房橙；仅复核提示")
        fh.pack(fill="x", pady=(12, 0))
        self.var_heart = tk.StringVar(value="（关键帧 / 现场模式跑完后显示；右上角示意图橙色 = 提示运动减低的节段）")
        uistyle.label(bh, var=self.var_heart, fg="#245", anchor="w", justify="left", wrap=True).pack(fill="x")

        right = tk.Frame(body, bg=uistyle.C_BG)
        right.grid(row=0, column=1, sticky="n")
        f1, b1 = uistyle.panel(right, "左心室容积曲线", "▲ 舒张末 ED　▼ 收缩末 ES；点曲线跳到该帧")
        f1.pack(fill="x")
        self.cv = tk.Canvas(b1, width=CW, height=CH, bg="#fff", highlightthickness=0)
        self.cv.pack()
        self.cv.bind("<Button-1>", lambda e: self._curve_click(e.x))
        self.cv.bind("<B1-Motion>", lambda e: self._curve_click(e.x))      # 按住拖 = 逐帧浏览
        f2, b2 = uistyle.panel(right, "事件时间线", "心动周期 / 质控")
        f2.pack(fill="x", pady=(12, 0))
        self.tl = tk.Canvas(b2, width=CW, height=TH, bg="#fafafa", highlightthickness=0)
        self.tl.pack()
        f3, b3 = uistyle.panel(right, "结果")
        f3.pack(fill="x", pady=(12, 0))
        g = tk.Frame(b3, bg=uistyle.C_CARD)
        g.pack(fill="x")
        self.var_ef = tk.StringVar(value="EF —")
        self.var_hr = tk.StringVar(value="心率 —")
        self.var_qc = tk.StringVar(value="质控 —")
        uistyle.label(g, var=self.var_ef, font=FONT_BIG, fg=uistyle.C_RED).pack(side="left")
        uistyle.label(g, var=self.var_hr, font=FONT_MID).pack(side="left", padx=(24, 0), pady=(6, 0))
        self.lab_qc = uistyle.label(g, var=self.var_qc, font=FONT_MID, fg="#666")
        self.lab_qc.pack(side="left", padx=(24, 0), pady=(6, 0))
        self.var_ci = tk.StringVar(value="")              # ③ 80 % 区间 + 复核提示（关键帧模式）
        self.lab_ci = uistyle.label(b3, var=self.var_ci, font=FONT_MID, fg="#245", anchor="w", wrap=True)
        self.lab_ci.pack(fill="x", pady=(2, 0))
        self.var_beats = tk.StringVar(value="")
        uistyle.label(b3, var=self.var_beats, font=FONT_MONO, anchor="w", justify="left") \
            .pack(fill="x", pady=(4, 0))
        self.var_clin = tk.StringVar(value="")
        uistyle.label(b3, var=self.var_clin, fg="#245", anchor="w", justify="left", wrap=True).pack(fill="x", pady=(4, 0))
        self.var_ref = tk.StringVar(value="")
        uistyle.label(b3, var=self.var_ref, fg=uistyle.C_MUTED, wrap=True).pack(fill="x")

        bottom = uistyle.card(body)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 12))
        self.pbar = ttk.Progressbar(bottom, length=VIEW + CW + 20, mode="determinate")
        self.pbar.pack(fill="x", padx=14, pady=(12, 0))
        self.var_txt = tk.StringVar(value="「打开视频…」选一段 EchoNet-Dynamic 的 .avi，再「开始分析」。需要板端 v5.8+（模型库里有 echonet_lv）")
        uistyle.label(bottom, var=self.var_txt, font=FONT_MONO, fg=uistyle.C_MUTED, wrap=True) \
            .pack(fill="x", padx=14, pady=(6, 10))
        uistyle.statusbar(t, 2)
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(1, weight=1)

    def _mode(self):
        return next(k for k, v in MODES if v == self.var_mode.get())

    def _where(self):
        return next((k for k, v in RUNS if v == self.var_where.get()), "stream")

    # ---------------------------------------------------------------- 载入
    def _open(self):
        if self.busy:
            return
        vd = os.path.join(ECHONET_DIR, "Videos")
        path = filedialog.askopenfilename(parent=self.top, initialdir=vd if os.path.isdir(vd) else HERE,
                                          title="打开心超视频", filetypes=[("AVI", "*.avi"), ("全部", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        self.var_txt.set("载入 %s …" % os.path.basename(path))

        def work():
            try:
                v, fps = self.ws.read_avi(path)
                self.queue.put(("loaded", path, v, fps))
            except Exception as e:
                self.queue.put(("err", "载入失败：%s" % e))
        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, path, v, fps):
        self.frames, self.fps, self.name = v, fps, os.path.basename(path)
        self._reset_state()
        self.scale.configure(to=len(v) - 1)
        self.btn_rep.state(["disabled"])
        self.var_ef.set("EF —")
        self.var_hr.set("心率 —")
        self.var_clin.set("")
        self.var_ci.set("")
        self.clinical = None
        self.var_qc.set("质控 —")
        self.lab_qc.configure(fg="#666")
        self.var_beats.set("")
        r = self.ref.get(self.name)
        self.var_ref.set(("数据集标注 EF（FileList，仅作对照）：%.1f%%，划分 %s" % r) if r else "")
        h = self.hmc.get(self.name)
        if h and not r:
            mi = [self.ws.WM_NAMES[k] for k, f in enumerate(h[0]) if f]
            self.var_ref.set("HMC-QU 标注（仅作对照，划分 %s）：%s" % (
                {"dev": "开发集", "test": "测试集"}.get(h[1], h[1]), ("心梗节段 " + "、".join(mi)) if mi else "无心梗节段"))
        k = self.ws.realtime_step(fps)
        self.var_txt.set("%s：%d 帧，%.0f 帧/秒（%.2f s）。实时模式每 %d 帧分析 1 帧（%.1f 帧/秒）"
                         % (self.name, len(v), fps, len(v) / fps, k, fps / k))
        self._show_frame(0)
        self._draw_curve()
        self._draw_timeline()

    # ---------------------------------------------------------------- 板上分析
    def run(self):
        if self._where() == "live":
            self._run_live()
            return
        if self.frames is None:
            self.var_txt.set("先「打开视频…」")
            return
        if self.model is None:
            self.var_txt.set("proto.MODELS 里没有 echonet_lv（上位机太旧）")
            return
        if self.busy or self.app.busy:
            self.var_txt.set("板子正忙，稍等")
            return
        self._reset_state()
        mode = self._mode()
        step = self.ws.realtime_step(self.fps) if mode == "realtime" else 1
        self.step = step
        if mode == "keyframe" and self._where() != "job":     # 关键帧调度只在板上整病例里
            self.var_where.set(RUNS[0][1])
        if self._where() == "job":
            self._run_job(mode, step)
            return
        self.idx, pl = self.ws.payloads(self.frames, self.fps, self.model.get("enc", "t3"), step)
        self.pbar.configure(maximum=len(pl), value=0)
        self.btn_rep.state(["disabled"])
        self.busy = self.app.busy = True
        self.btn_run.state(["disabled"])
        self.stop = threading.Event()
        try:
            host, port = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except ValueError:
            host, port = self.app.link.host, self.app.link.port
        pace = (step / self.fps) if mode == "realtime" else 0.0
        threading.Thread(target=self._worker, args=(host, port, pl, pace, self.stop), daemon=True).start()

    def _worker(self, host, port, payloads, pace, stop):
        """后台线程：紧凑帧连续发送（最多 2 帧在途）；实时模式按视频自身的节拍送帧（第 i 帧不早于 i × pace 秒）。"""
        link = self.app.link
        m = self.model
        flags = proto.FLAG_STATS | proto.FLAG_C_LOGIT
        t0 = time.perf_counter()
        try:
            link.host, link.port = host, port
            inflight = deque()
            k = 0
            while k < len(payloads) or inflight:
                while len(inflight) < 2 and k < len(payloads) and not stop.is_set():
                    if pace:
                        wait = t0 + k * pace - time.perf_counter()
                        if wait > 0 and inflight:
                            break                       # 先收结果，下一帧还没“采到”
                        if wait > 0:
                            time.sleep(wait)
                    # 板端说过能做 PL 后处理才带 b10（老板端不认那 12 字节）；结果与 PC 上的 clean 逐位相同
                    pp = self.ws.pp_request() if getattr(link, "pp_available", False) else None
                    link.send_v6c(payloads[k], 128, kind=proto.V6C_KIND_RGB, model0=m["index"], thr=m["thr"],
                                  flags=flags, pp=pp)
                    inflight.append((k, time.perf_counter(), flags | (proto.FLAG_C_PP if pp else 0)))
                    k += 1
                if not inflight:
                    break
                i, ts, fl = inflight.popleft()
                st, logit, _ = link.recv_v6c(fl, 128)
                now = time.perf_counter()
                self.queue.put(("frame", i, st, logit, (now - ts) * 1000.0, now - (t0 + i * pace) if pace else None))
            self.queue.put(("done", time.perf_counter() - t0, stop.is_set()))
        except Exception as e:
            link.close()
            self.queue.put(("err", "板上分析失败：%s（需要板端 v5.8+）" % e))

    # ---------------------------------------------------------------- 板上整病例（v7 病例任务 echo_ef）
    def _run_job(self, mode, step):
        self.job = None
        self.idx = []
        n = len(range(0, len(self.frames), step))
        self.pbar.configure(maximum=n, value=0)
        self.btn_rep.state(["disabled"])
        self.busy = self.app.busy = True
        self.btn_run.state(["disabled"])
        self.stop = None                             # 整病例任务中途停不了：「停止」置灰
        try:
            host, port = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except ValueError:
            host, port = self.app.link.host, self.app.link.port
        self.var_txt.set("板上整病例：上传 %s …" % self.name)
        threading.Thread(target=self._job_worker, args=(host, port, mode), daemon=True).start()

    def _job_worker(self, host, port, mode):
        link = self.app.link
        flags = jobs.ECHO_MASKS | jobs.ECHO_LIVE | (jobs.ECHO_PACE if mode == "realtime" else 0)
        t0 = time.perf_counter()
        try:
            link.host, link.port = host, port
            link.connect()                           # 板端 10 s 空闲超时：任务前总是重连
            kf = None
            if mode == "keyframe":                   # v5.21 echo_ef mode 2：先问板端支不支持
                caps = jobs.caps(link)
                if 2 not in caps.get("echo_modes", []):
                    self.queue.put(("err", "板端不支持关键帧模式（需要服务端 v5.21+，现在是 %s）；请改用实时 / 全帧"
                                    % caps.get("server", "?")))
                    return
                link.connect()
                kf = jobs.load_kf_config("keyframe")
                self.kf_cfg = kf
                self.hc_cfg = jobs.load_heart_config() if "heart" in caps else None   # v5.24：节段室壁运动 + 右心
                flags = jobs.ECHO_MASKS               # mode 2 不回逐帧块，只回进度
            r = jobs.echo_ef(link, self.conf, self.model["scale"], self.frames, self.fps,
                             model=self.model["index"], realtime=mode == "realtime",
                             flags=flags, on_frame=lambda f: self.queue.put(("jframe", f)),
                             on_progress=lambda p: self.queue.put(("jprog", p)), keyframe=kf,
                             heart=self.hc_cfg if mode == "keyframe" else None)
            self.queue.put(("job", r, mode, time.perf_counter() - t0))
        except Exception as e:
            link.close()
            self.queue.put(("job_fail", str(e)))

    # ---------------------------------------------------------------- 现场模式（echo_ef mode 3，板端 v5.22+）
    def _run_live(self):
        if self.model is None:
            self.var_txt.set("proto.MODELS 里没有 echonet_lv（上位机太旧）")
            return
        if self.busy or self.app.busy:
            self.var_txt.set("板子正忙，稍等")
            return
        mode = self._mode()
        if mode == "realtime":                       # 现场模式本身就按采集节拍：只有关键帧 / 每帧推理两种
            self.var_mode.set(MODES[0][1])
            mode = "keyframe"
        label, src = next((s for s in self.sources if s[0] == self.var_src.get()), self.sources[0])
        crop = src["truth"] if self.var_crop.get() == CROPS[1] and src["truth"] else None
        np = self.np
        self._reset_state()
        self.keyv = []
        self.step = 1
        self.name, self.fps = src["name"], src["fps"]
        self.frames = np.zeros((src["frames"], 112, 112), np.uint8)
        self.scale.configure(to=src["frames"] - 1)
        r = self.ref.get(src["video"]) if src["video"] else None
        self.var_ref.set(("数据集标注 EF（FileList，仅作对照）：%.1f%%，划分 %s" % r) if r else "")
        self.var_ef.set("EF —")
        self.var_hr.set("心率 —")
        self.var_qc.set("质控 —")
        for v in (self.var_ci, self.var_beats, self.var_clin):
            v.set("")
        self.lab_qc.configure(fg="#666")
        self.pbar.configure(maximum=src["frames"], value=0)
        self.btn_rep.state(["disabled"])
        self.btn_tl.state(["disabled"])
        self.busy = self.app.busy = True
        self.btn_run.state(["disabled"])
        self.stop = threading.Event()
        try:
            host, port = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except ValueError:
            host, port = self.app.link.host, self.app.link.port
        self.var_txt.set("现场模式：板上信号源 %s，%s …" % (src["name"], "固定方块 %s" % (crop,) if crop else "先找画面（约 1 s）"))
        self._show_frame(0)
        self._draw_curve()
        self._draw_timeline()
        threading.Thread(target=self._live_worker, args=(host, port, mode, src, crop, self.stop), daemon=True).start()

    def _live_worker(self, host, port, mode, src, crop, stop):
        link = self.app.link
        t0 = time.perf_counter()
        try:
            link.host, link.port = host, port
            link.connect()
            caps = jobs.caps(link)
            if 3 not in caps.get("echo_modes", []):
                self.queue.put(("err", "板端不支持现场模式（需要服务端 v5.22+，现在是 %s）" % caps.get("server", "?")))
                return
            link.connect()
            kf = jobs.load_kf_config("full" if mode == "full" else "keyframe")
            self.kf_cfg = kf
            self.hc_cfg = jobs.load_heart_config() if "heart" in caps else None       # v5.24：节段室壁运动 + 右心

            def on_live(d):
                if stop.is_set():
                    raise _Stopped()
                self.queue.put(("lframe", d))
            r = jobs.echo_live(link, self.conf, self.model["scale"], kf, src["name"], source=src["kind"], crop=crop,
                               model=self.model["index"], max_frames=src["frames"], every=1,
                               out_flags=jobs.LIVE_FRAMES112 | jobs.LIVE_FRAME, v4l2=(640, 480, src["fps"]),
                               on_live=on_live, on_progress=lambda p: self.queue.put(("jprog", p)), heart=self.hc_cfg)
            self.queue.put(("job", r, "live", time.perf_counter() - t0))
        except _Stopped:
            link.close()
            self.queue.put(("err", "已停止：连接已断开，板上任务随之结束（现场模式没有中途的最终结果）"))
        except Exception as e:
            link.close()
            self.queue.put(("err", "现场模式失败：%s" % e))

    def _on_lframe(self, d):
        np = self.np
        t = d["t"]
        if t >= len(self.frames):
            return
        if d["frame"] is not None:
            self.frames[t] = np.frombuffer(d["frame"], np.uint8).reshape(112, 112)
        self.masks[t] = np.unpackbits(np.frombuffer(d["mask"], np.uint8), bitorder="little")[:112 * 112] \
            .reshape(112, 112) > 0
        self.idx.append(t)
        self.vols.append(d["vol"])
        self.keyv.append(d["key"])
        self.pbar.configure(value=len(self.vols))
        now = time.perf_counter()
        if now - self.t_render > 0.035:
            self.t_render = now
            self._show_frame(t)
            self._draw_curve()
        if now - self.t_an > 0.25:
            self.t_an = now
            self._draw_timeline()

    def _on_job_live(self, r, wall):
        """echo_ef mode 3 结束：板上裁好的 112 帧换进来，其余照关键帧模式显示，另给信号源 / 画面方块 / 延迟。"""
        np = self.np
        res = r["result"]
        n = res["n_frames"]
        if "frames112" in r["blobs"]:
            self.frames = np.frombuffer(r["blobs"]["frames112"], np.uint8)[:n * 112 * 112].reshape(n, 112, 112).copy()
        else:
            self.frames = self.frames[:n]
        self.fps = res["fps"]
        self.scale.configure(to=n - 1)
        self.masks = {}
        self._on_job_kf(r, wall)
        self.mode_used = "live"
        self.btn_tl.state(["disabled"])
        st = res["stream"]
        c = st["crop"]
        lat = st["latency_ms"]
        how = "自动找到" if c["auto"] else "固定"
        self.live_desc = "现场（板上信号源 %s，%d×%d %.0f fps；画面方块%s (%d, %d) 边长 %d；%s）" % (
            st["name"], st["W"], st["H"], st["src_fps"], how, c["x"], c["y"], c["side"],
            "关键帧推荐档" if res["keyframe"]["key_frac"] < 1 else "每帧推理")
        self.var_txt.set(self.var_txt.get() + "\n现场：信号源 %s（%d×%d），画面方块%s (%d, %d) 边长 %d；"
                         "延迟（采到 → 掩膜 + 容积）中位数 %.0f ms、P99 %.0f ms、最大 %.0f ms%s"
                         % (st["name"], st["W"], st["H"], how, c["x"], c["y"], c["side"], lat["p50"], lat["p99"],
                            lat["max"], "（含开头找画面的 1 s）" if c["auto"] else ""))

    def _on_jframe(self, f):
        np = self.np
        t = f["t"]
        m = np.unpackbits(np.frombuffer(f["mask"], np.uint8), bitorder="little")[:112 * 112].reshape(112, 112) > 0
        self.idx.append(t)
        self.masks[t] = m
        self.vols.append(f["vol"])
        self.infos.append(dict(ncomp=f["ncomp"], area=f["area"], unc=f["unc"], border=f["border"]))
        self.pbar.configure(value=len(self.vols))
        now = time.perf_counter()
        if now - self.t_render > 0.035:
            self.t_render = now
            self._show_frame(t)
            self._draw_curve()

    def _on_jprog(self, p):
        if p.get("stage") == "live":                        # 现场模式：板上滚动结果（EF 已按这套调度校准）
            if p.get("beats", 0) > 0 and p.get("ef", -1) >= 0:
                self.var_ef.set("EF %.0f%%" % p["ef"])
                self.var_hr.set(("心率 %.0f 次/分" % p["hr"]) if p.get("hr") else "心率 —")
            if "key_frac" in p:
                self.var_txt.set("现场：已分析 %d 帧（已采到 %d），关键帧 %.0f%%，当前延迟 %.0f ms"
                                 % (p["done"], p["captured"], 100 * p["key_frac"], p["lag_ms"]))
            return
        if p.get("stage") == "frames" and self._mode() == "keyframe" and p.get("total"):
            self.pbar.configure(maximum=p["total"], value=p.get("done", 0))
            return
        if "ef_raw" in p and p.get("beats", 0) > 0:         # 板上滚动的逐拍结果
            mode = "realtime" if self.step > 1 or self._mode() == "realtime" else "full"
            self.var_ef.set("EF %.0f%%" % self.ws.calibrated(p["ef_raw"], self.conf, mode))
            self.var_hr.set(("心率 %.0f 次/分" % p["hr"]) if p.get("hr") else "心率 —")

    def _timeline(self):
        """1.7：最近一次板上整病例的时间线（软件计时）；再用 PS 工作线程做清理（flags bit2）跑一遍作对照。"""
        jr = getattr(self, "job_raw", None)
        if jr is None or self.busy:
            self.var_txt.set("先用「板上整病例」分析一段视频，再看时间线")
            return
        r, mode = jr
        win = timeline.TimelineWin(self.top, "心超整病例时间线：%s（软件计时）" % self.name)
        win.add("PL 后处理（默认，最大块 + 填洞在 pp_layer）", "echo", r)
        try:
            host, port = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except ValueError:
            host, port = self.app.link.host, self.app.link.port

        def work():
            link = self.app.link
            try:
                link.host, link.port = host, port
                link.connect()
                r2 = jobs.echo_ef(link, self.conf, self.model["scale"], self.frames, self.fps, model=self.model["index"],
                                  realtime=mode == "realtime", flags=jobs.ECHO_MASKS | jobs.ECHO_PS_CLEAN)
                same = r2["blobs"].get("masks") == r["blobs"].get("masks") and r2["result"].get("vols") == r["result"].get("vols")
                self.top.after(0, lambda: win.add("PS 后处理（清理在 PS 工作线程）", "echo", r2,
                                                  "掩膜和容积与 PL 版%s" % ("逐位相同" if same else "不同！")))
            except Exception as e:
                self.top.after(0, lambda: win.var.set(win.var.get() + "\n对照运行失败：%s" % e))
        threading.Thread(target=work, daemon=True).start()

    def _on_job_fail(self, msg):
        self.busy = self.app.busy = False
        self.btn_run.state(["!disabled"])
        if self._where() == "live":
            self.var_txt.set("现场模式失败：%s" % msg)
            return
        if "peer closed" in msg or "magic" in msg or "unknown job" in msg:
            self.var_where.set(RUNS[1][1])            # 老板端不认 v7 任务 / echo_ef：自动改用逐帧流式
            self.run()
            self.var_txt.set("板端不支持整病例心超任务（需要 v5.12+：%s），已改用逐帧流式" % msg)
            return
        self.var_txt.set("板上整病例失败：%s" % msg)

    def _on_job(self, r, mode, wall):
        res = r["result"]
        self.job_raw = (r, mode)
        self.btn_tl.state(["!disabled"])
        self.busy = self.app.busy = False
        self.btn_run.state(["!disabled"])
        if not res.get("ok"):
            if "unknown job" in (res.get("error") or ""):
                self._on_job_fail("unknown job")
                return
            self.var_txt.set("板上整病例失败：%s" % res.get("error"))
            return
        ws, np = self.ws, self.np
        self.job = res
        if res.get("mode") == 3:                           # 现场模式（板上信号源）
            self._on_job_live(r, wall)
            return
        if res.get("mode") == 2:                           # 关键帧模式：另一套结果字段
            self._on_job_kf(r, wall)
            return
        self.idx = list(range(0, res["n_frames"], res["step"]))
        self.vols = list(res["vols"])                       # 板上最终值（与逐帧块里的一样）
        self.infos = [dict(ncomp=a, area=b, border=bool(c), unc=d) for a, b, c, d in res["info"]]
        if "masks" in r["blobs"]:
            mm = jobs.unpack_mask(r["blobs"]["masks"], (len(self.idx), 112, 112))
            self.masks = {t: mm[i] for i, t in enumerate(self.idx)}
        self.result = dict(ef=res["ef_raw"] if res["ef_raw"] is not None else float("nan"), fallback=res["fallback"],
                           hr=res["hr"], cycle_s=res["cycle_s"], beats=[dict(ed=b["ed"], es=b["es"], v_ed=b["v_ed"],
                                                                          v_es=b["v_es"], ef=b["ef"]) for b in res["beats"]])
        if "ef_sd" in res:
            self.result["ef_sd"] = res["ef_sd"]
        self.mode_used = mode
        cal = lambda e: ws.calibrated(e, self.conf, mode)
        q = res["qc"]
        self.qc_level = q["level"]
        self.qc_reasons = ws.qc_reasons(q["why"], q, self.result)
        r_ = self.result
        self.var_ef.set("EF %.0f%%" % cal(r_["ef"]) if r_["ef"] == r_["ef"] else "EF —")
        self.var_hr.set(("心率 %.0f 次/分" % r_["hr"]) if r_.get("hr") else "心率 —")
        lines = ["第 %d 拍：ED 第 %d 帧 → ES 第 %d 帧，EF %.0f%%" % (j + 1, b["ed"], b["es"], cal(b["ef"]))
                 for j, b in enumerate(r_["beats"][-6:])]
        if self.qc_reasons:
            lines.append("质控：" + "；".join(self.qc_reasons))
        self.var_beats.set("\n".join(lines))
        if res.get("clinical") is not None:
            self.clinical, self.clinical_where = res["clinical"], "板上计算"
        else:
            self.clinical, self.clinical_where = ws.clinical(
                r_["beats"], self.fps, cal(r_["ef"]), cal, ws.clinical_conf(self.conf, mode)), "PC 计算（板端 v5.14 及以前）"
        self._show_clinical(cal(r_["ef"]))
        txt, col = QC_TXT[self.qc_level]
        self.var_qc.set("质控 " + txt)
        self.lab_qc.configure(fg=col)
        n = len(self.vols)
        self.pp_frames = res["clean"]["pl"]
        self.stats = dict(n=n, wall=wall, fps=n / wall if wall > 0 else 0, ai_ms=res["pl_ms"] / max(n, 1))
        dur = res["n_frames"] / self.fps
        ms = res["ms"]
        self.var_txt.set("板上整病例：%d 帧，分析 %d 帧，板上 %.2f s（逐帧 %.2f s，逐拍分析 + 质控 %.1f ms），"
                         "PL 计算每帧 %.1f ms；视频本身 %.2f s → 实时倍数 %.2f%s｜最大块 + 填洞：%s"
                         % (res["n_frames"], n, ms["total"] / 1000, ms["frames"] / 1000, ms["analysis"],
                            self.stats["ai_ms"], dur,
                            dur / max(wall, 1e-9),
                            "（按采集节拍，最后一帧滞后 %.0f ms）" % (1000 * res["lag_s"]) if res.get("paced") else "",
                            ("PL %d/%d 帧" % (self.pp_frames, n)) if self.pp_frames else "PS 工作线程（与 PL 并行）"))
        if n >= 3:
            self.btn_rep.state(["!disabled"])
        if r_["beats"]:
            self._show_frame(r_["beats"][len(r_["beats"]) // 2]["es"])
        self._draw_curve()
        self._draw_timeline()

    def _cal(self, e):
        """原始 EF -> 校准 EF：关键帧模式用这套调度自己的 a / b（echo_kf_config.json，训练子集上拟合），其余照旧。"""
        if getattr(self, "mode_used", None) in ("keyframe", "live") and self.kf_cfg:
            return self.kf_cfg["a"] * e + self.kf_cfg["b"]
        return self.ws.calibrated(e, self.conf, getattr(self, "mode_used", "full"))

    def _on_job_kf(self, r, wall):
        """echo_ef mode 2（v5.21）：①关键帧 + 跟踪的逐帧掩膜 / 容积，②质量灯（只作原因），③80 % 区间与复核提示。"""
        ws = self.ws
        res = r["result"]
        n = res["n_frames"]
        self.mode_used = "keyframe"
        self.idx = list(range(n))
        self.vols = list(res["vols"])
        self.infos = []
        self.keyv = [c == "1" for c in res["key"]]
        self.kf_res = dict(keyframe=res["keyframe"], quality=res.get("quality"), interval=res.get("interval"))
        if "masks" in r["blobs"]:
            mm = jobs.unpack_mask(r["blobs"]["masks"], (n, 112, 112))
            self.masks = {t: mm[t] for t in range(n)}
        self.result = dict(ef=res["ef_raw"] if res["ef_raw"] is not None else float("nan"), fallback=res["fallback"],
                           hr=res["hr"], cycle_s=res["cycle_s"], beats=[dict(ed=b["ed"], es=b["es"], v_ed=b["v_ed"],
                                                                          v_es=b["v_es"], ef=b["ef"]) for b in res["beats"]])
        if "ef_sd" in res:
            self.result["ef_sd"] = res["ef_sd"]
        r_ = self.result
        ef = res["ef"]
        q = res["qc"]
        self.qc_level = q["level"]
        self.qc_reasons = ws.qc_reasons(q["why"], q, self.result)
        self.var_ef.set("EF %.0f%%" % ef if ef is not None else "EF —")
        self.var_hr.set(("心率 %.0f 次/分" % r_["hr"]) if r_.get("hr") else "心率 —")
        line, review = ws.interval_text(res.get("interval"), ef) if ef is not None else (None, False)
        self.var_ci.set(line or "")
        self.lab_ci.configure(fg="#c00" if review else "#245")
        lines = ["第 %d 拍：ED 第 %d 帧 → ES 第 %d 帧，EF %.0f%%" % (j + 1, b["ed"], b["es"], self._cal(b["ef"]))
                 for j, b in enumerate(r_["beats"][-6:])]
        if self.qc_reasons:
            lines.append("质控（关键帧上）：" + "；".join(self.qc_reasons))
        self.var_beats.set("\n".join(lines))
        self.clinical, self.clinical_where = res["clinical"], "板上计算"
        if ef is not None:
            self._show_clinical(ef)
            qt = ws.quality_text(res.get("quality"))
            if qt:
                self.var_clin.set(self.var_clin.get() + "\n" + qt)
        self._take_heart(r)
        txt, col = QC_TXT[self.qc_level]
        self.var_qc.set("质控 " + txt)
        self.lab_qc.configure(fg=col)
        k = res["keyframe"]
        self.pbar.configure(maximum=n, value=n)
        self.stats = dict(n=n, wall=wall, fps=n / wall if wall > 0 else 0, ai_ms=k["pl_ms_per_key"])
        dur = n / self.fps
        self.var_txt.set("关键帧模式（板上）：%d 帧，PL 推理 %d 帧（%.1f%%：间隔 %d、残差 %d），其余帧板上跟踪 %.2f ms/帧；"
                         "每个关键帧 PL %.1f ms；板上 %.2f s，视频本身 %.2f s → 实时倍数 %.2f"
                         % (n, k["n_key"], 100 * k["key_frac"], k["reasons"]["interval"], k["reasons"]["residual"],
                            k["track_ms_per_frame"], k["pl_ms_per_key"], res["ms"]["total"] / 1000, dur,
                            dur / max(wall, 1e-9)))
        if n >= 3:
            self.btn_rep.state(["!disabled"])
        if r_["beats"]:
            self._show_frame(r_["beats"][len(r_["beats"]) // 2]["es"])
        self._draw_curve()
        self._draw_timeline()

    def _take_heart(self, r):
        """板上全心扩展（v5.24 HC01）：节段室壁运动 + 右室 / 右房 -> 文字、帧上的右心轮廓、示意图。"""
        res = r["result"]
        self.heart, self.rh_frames = None, {}
        if "wall_motion" not in res and "right_heart" not in res:
            self.var_heart.set("（这次没有全心结果：要关键帧 / 现场模式 + 板端 v5.24）")
            return
        self.heart = dict(wall_motion=res.get("wall_motion"), right_heart=res.get("right_heart"))
        rh = res.get("right_heart") or {}
        if "rh_masks" in r["blobs"] and rh.get("pairs"):
            for p, (rv_ed, rv_es, ra_es) in zip(rh["pairs"], jobs.unpack_rh(r["blobs"]["rh_masks"], len(rh["pairs"]))):
                self.rh_frames.setdefault(p["ed"], []).append((rv_ed, self.ws.RV_COLOR) if p["rv_ok_ed"] else None)
                self.rh_frames.setdefault(p["es"], []).extend(
                    [(rv_es, self.ws.RV_COLOR) if p["rv_ok_es"] else None, (ra_es, self.ws.RA_COLOR) if p["ra_ok"] else None])
        self.var_heart.set("\n".join(self.ws.heart_text(res, self.hc_cfg)))

    def _on_frame(self, i, st, logit, rt, lag):
        ws = self.ws
        if st is None or st.get("model") != self.model["index"]:
            self.stop.set()
            self.var_txt.set("板端没按 %d 号模型回结果（统计块 %s）——板端程序太旧？"
                             % (self.model["index"], None if st is None else st.get("model")))
            return
        q, raw = ws.mask_from_logit(logit)
        cleaned = ws.pp_clean(st.get("pp"))
        if cleaned is not None:
            self.pp_frames += 1
            self.pp_ms.append(st["pp"]["pl_us"] / 1000.0)
        mc, info = ws.frame_info(q, raw, cleaned)
        t = self.idx[i]
        self.masks[t] = mc
        self.vols.append(ws.volume_pca(mc))
        self.infos.append(info)
        self.ai_us.append(st.get("ai_us", 0))
        self.lag = lag
        self.pbar.configure(value=len(self.vols))
        now = time.perf_counter()
        if now - self.t_an > 0.25 and len(self.vols) >= 8:       # 边跑边更新逐拍结果
            self.t_an = now
            self._analyze(final=False)
        if now - self.t_render > 0.035:
            self.t_render = now
            self._show_frame(t)
            self._draw_curve()

    def _analyze(self, final):
        ws = self.ws
        mode = "realtime" if self.step > 1 or self._mode() == "realtime" else "full"
        r = ws.analyze(self.vols, self.fps, self.step, self.conf["cfg"])
        self.result = r
        self.mode_used = mode
        cal = lambda e: ws.calibrated(e, self.conf, mode)
        if r["beats"] or final:
            self.var_ef.set("EF %.0f%%" % cal(r["ef"]) if r["ef"] == r["ef"] else "EF —")
        self.var_hr.set(("心率 %.0f 次/分" % r["hr"]) if r.get("hr") else "心率 —")
        lines = ["第 %d 拍：ED 第 %d 帧 → ES 第 %d 帧，EF %.0f%%" % (j + 1, b["ed"], b["es"], cal(b["ef"]))
                 for j, b in enumerate(r["beats"][-6:])]
        self.var_beats.set("\n".join(lines))
        if final:
            lvl, reasons, score = ws.qc(self.infos, r)
            self.qc_level, self.qc_reasons = lvl, reasons
            txt, col = QC_TXT[lvl]
            self.var_qc.set("质控 " + txt)
            self.lab_qc.configure(fg=col)
            if reasons:
                self.var_beats.set(self.var_beats.get() + "\n质控：" + "；".join(reasons))
            self.clinical = ws.clinical(r["beats"], self.fps, cal(r["ef"]), cal, ws.clinical_conf(self.conf, mode))
            self.clinical_where = "PC 计算"
            self._show_clinical(cal(r["ef"]))
        self._draw_timeline()

    def _show_clinical(self, ef):
        """临床测量（辅助测量与复核，不作诊断）：LVEF 区间 / 复核提示 / 逐拍离散度 / RR 与指数心拍。"""
        lines = self.ws.clinical_text(self.clinical, ef)
        self.var_clin.set("临床测量（%s，仅辅助测量与复核）：\n" % self.clinical_where + "\n".join(lines))

    def _on_done(self, wall, stopped):
        np = self.np
        self.busy = self.app.busy = False
        self.btn_run.state(["!disabled"])
        n = len(self.vols)
        self.stats = dict(n=n, wall=wall, fps=n / wall if wall > 0 else 0,
                          ai_ms=float(np.mean(self.ai_us)) / 1000 if self.ai_us else 0)
        if n >= 3:
            self._analyze(final=True)
            self.btn_rep.state(["!disabled"])
        dur = len(self.frames) / self.fps
        self.var_txt.set("板上分析 %d 帧用时 %.2f s（%.1f 帧/秒，每帧 PL 计算 %.1f ms）；视频本身 %.2f s → 实时倍数 %.2f%s%s"
                         % (n, wall, self.stats["fps"], self.stats["ai_ms"], dur, dur / max(wall, 1e-9),
                            "（按采集节拍送帧，最后一帧滞后 %.0f ms）" % (1000 * self.lag) if getattr(self, "lag", None) is not None else "",
                            "｜已停止" if stopped else "")
                         + ("｜最大块 + 填洞：PL %d/%d 帧（每帧 %.2f ms）" % (self.pp_frames, n, float(np.mean(self.pp_ms)))
                            if self.pp_frames else "｜最大块 + 填洞：PC"))
        if self.result and self.result["beats"]:
            b = self.result["beats"][len(self.result["beats"]) // 2]
            self._show_frame(b["es"])
        self._draw_curve()

    def _stop(self):
        if self.stop is not None:
            self.stop.set()

    # ---------------------------------------------------------------- 显示
    def _show_frame(self, t):
        if self.frames is None:
            return
        from PIL import Image, ImageTk
        np = self.np
        t = max(0, min(len(self.frames) - 1, int(t)))
        self.cur = t
        m = self.masks.get(t)
        if m is None and self.masks:                              # 实时模式没分析的帧：用最近一个分析帧的结果
            near = min(self.masks, key=lambda k: abs(k - t))
            m = self.masks[near] if abs(near - t) <= getattr(self, "step", 1) else None
        rgb = self.ws.contour_rgb(self.frames[t], m if m is not None else np.zeros((112, 112), bool), VIEW // 112)
        for item in self.rh_frames.get(t, []):                    # 右室紫 / 右房橙（只画通过位置检查的）
            if item is not None:
                self.ws.add_contour(rgb, item[0], VIEW // 112, item[1])
        tag = ""
        if self.result:
            for b in self.result["beats"]:
                if abs(b["ed"] - t) <= max(1, getattr(self, "step", 1) // 2):
                    tag = "ED"
                elif abs(b["es"] - t) <= max(1, getattr(self, "step", 1) // 2):
                    tag = "ES"
        im = Image.fromarray(rgb)
        wm = (self.heart or {}).get("wall_motion")
        if wm:                                                    # 右上角：6 节段示意（橙 = 提示运动减低）
            self.ws.draw_wm_glyph(im, wm["seg"], VIEW - 74, 6)
        if tag:
            from PIL import ImageDraw
            ImageDraw.Draw(im).text((10, 8), tag, fill=(255, 80, 80) if tag == "ED" else (90, 160, 255), font=_tag_font())
        self.photos["v"] = ImageTk.PhotoImage(im)
        self.lab_v.configure(image=self.photos["v"])
        self.var_ft.set("第 %d / %d 帧  %.2f s%s" % (t + 1, len(self.frames), t / self.fps, ("  " + tag) if tag else ""))
        self._draw_cursor()

    def _xy(self):
        """帧号 / 容积 -> 画布坐标。曲线上下各留出 ED / ES 标记和 EF 数字的位置，免得压到时间刻度。"""
        n = len(self.frames)
        x = lambda t: PLOT_L + (CW - PLOT_L - 10) * t / max(n - 1, 1)
        vs = self.vols or [0.0, 1.0]
        lo, hi = min(vs), max(vs)
        top, bot = 24, PLOT_B - 26
        y = lambda v: bot - (bot - top) * (v - lo) / max(hi - lo, 1e-9)
        return x, y

    def _draw_curve(self):
        c = self.cv
        c.delete("all")
        if self.frames is None:
            return
        x, y = self._xy()
        c.create_rectangle(PLOT_L, 10, CW - 10, PLOT_B, outline="#ddd")
        dur = len(self.frames) / self.fps
        for s in range(int(dur) + 1):
            xs = x(s * self.fps)
            c.create_line(xs, PLOT_B, xs, PLOT_B + 4, fill="#999")
            c.create_text(xs, CH - 9, text="%d s" % s, fill="#777", font=("Consolas", 8))
        if len(self.vols) >= 2:
            pts = []
            for t, v in zip(self.idx, self.vols):
                pts += [x(t), y(v)]
            c.create_line(*pts, fill="#1e5ac8", width=2)
        if self.result:
            cal = self._cal
            for b in self.result["beats"]:
                xe, ye, xs_, ys = x(b["ed"]), y(b["v_ed"]), x(b["es"]), y(b["v_es"])
                c.create_polygon(xe, ye - 10, xe - 6, ye - 2, xe + 6, ye - 2, fill="#d83232")
                c.create_polygon(xs_, ys + 10, xs_ - 6, ys + 2, xs_ + 6, ys + 2, fill="#2878dc")
                c.create_text(xs_, ys + 18, text="%.0f%%" % cal(b["ef"]), fill="#333", font=("Consolas", 9))
        c.create_text(4, 16, text="容积", anchor="w", fill="#777", font=("Microsoft YaHei UI", 8))
        self._draw_cursor()

    def _draw_cursor(self):
        if self.frames is None:
            return
        x, _ = self._xy()
        self.cv.delete("cursor")
        self.cv.create_line(x(self.cur), 10, x(self.cur), PLOT_B, fill="#e0a000", tags="cursor")

    def _draw_timeline(self):
        c = self.tl
        c.delete("all")
        if self.frames is None:
            return
        x, _ = self._xy()
        c.create_text(4, 17, text="心拍", anchor="w", fill="#555", font=("Microsoft YaHei UI", 8))
        c.create_text(4, 50, text="关键帧" if self.keyv else "质控", anchor="w", fill="#555",
                      font=("Microsoft YaHei UI", 8))
        if self.result:
            cal = self._cal
            for j, b in enumerate(self.result["beats"]):
                col = "#f4c7c7" if j % 2 == 0 else "#c7d8f4"
                c.create_rectangle(x(b["ed"]), 6, x(b["es"]), 28, fill=col, outline="")
                c.create_text((x(b["ed"]) + x(b["es"])) / 2, 17, text="%.0f%%" % cal(b["ef"]), font=("Consolas", 8))
        for t, f in zip(self.idx, self.infos):
            bad = f["area"] == 0 or f["border"]
            warn = f["ncomp"] > 1 or f["unc"] > 0.5
            col = "#d33" if bad else ("#e0a000" if warn else "#4a4")
            c.create_line(x(t), 40, x(t), 60, fill=col, width=2)
        if self.keyv:                                   # 关键帧模式：蓝 = PL 分割，灰 = 板上跟踪
            for t, k in enumerate(self.keyv):
                c.create_line(x(t), 40 if k else 46, x(t), 60 if k else 54, fill="#2864c8" if k else "#ccc",
                              width=2 if k else 1)
            legend = "蓝 关键帧（PL 分割）　灰 板上块匹配跟踪"
        else:
            legend = "绿 正常　黄 不连通 / 低置信　红 空 / 碰边"
        c.create_text(CW - 4, TH - 10, text=legend, anchor="e", fill="#777", font=("Microsoft YaHei UI", 8))

    def _curve_click(self, px):
        if self.frames is None:
            return
        n = len(self.frames)
        t = int(round((px - PLOT_L) / max(CW - PLOT_L - 10, 1) * (n - 1)))
        self.scale.set(max(0, min(n - 1, t)))

    # ---------------------------------------------------------------- 证据报告
    def _report(self):
        if not self.result:
            return
        ws = self.ws
        mode = getattr(self, "mode_used", "full")
        cal = self._cal
        r = self.result
        keys = []
        if r["beats"]:
            b = sorted(r["beats"], key=lambda b: b["ef"])[len(r["beats"]) // 2]
            for t, lab in ((b["ed"], "舒张末 ED（第 %d 帧）"), (b["es"], "收缩末 ES（第 %d 帧）")):
                m = self.masks.get(t)
                if m is not None:
                    keys.append((ws._png(ws.contour_rgb(self.frames[t], m, 3)), lab % t))
        info = dict(result=r, fps=self.fps, name=self.name, nframes=len(self.frames), ef=cal(r["ef"]), cal_beat=cal,
                    keyframes=keys, qc_level=self.qc_level, qc_reasons=self.qc_reasons,
                    clinical=getattr(self, "clinical", None), clinical_where=getattr(self, "clinical_where", ""),
                    ref_ef=self.ref.get(self.name, (None,))[0],
                    curve=ws.curve_png([t / self.fps for t in self.idx], self.vols, r["beats"], self.fps, cal),
                    model="%d 号 %s" % (self.model["index"], self.model["name"]),
                    mode=dict(realtime="实时（每 %d 帧分析 1 帧，按采集节拍送帧）" % self.step, full="全帧",
                              keyframe="关键帧（PL 只分割关键帧，其余帧板上块匹配跟踪；板端 v5.21）",
                              live=getattr(self, "live_desc", "现场（板上信号源）"))[mode],
                    n_analyzed=len(self.vols), ai_ms=self.stats["ai_ms"], wall=self.stats["wall"])
        if mode in ("keyframe", "live") and self.kf_res:
            info.update(self.kf_res)
        if self.heart:
            figs = []
            rh = self.heart.get("right_heart") or {}
            for p in rh.get("pairs", [])[:1]:
                for t, lab in ((p["ed"], "舒张末（第 %d 帧）：左室红、右室紫"), (p["es"], "收缩末（第 %d 帧）：左室红、右室紫、右房橙")):
                    m = self.masks.get(t)
                    if m is not None:
                        rgb = ws.contour_rgb(self.frames[t], m, 3)
                        for item in self.rh_frames.get(t, []):
                            if item is not None:
                                ws.add_contour(rgb, item[0], 3, item[1])
                        figs.append((ws._png(rgb), lab % t))
            info.update(heart=self.heart, heart_cfg=self.hc_cfg, heart_figs=figs)
        d = os.path.join(HERE, "reports")
        os.makedirs(d, exist_ok=True)
        path = ws.write_report(os.path.join(d, "echo_%s_%s.html" % (os.path.splitext(self.name)[0], time.strftime("%Y%m%d_%H%M%S"))), info)
        self.last_report = path
        self.var_txt.set("证据报告已写到 %s" % path)
        if self.open_report:
            try:
                webbrowser.open("file:///" + path.replace("\\", "/"))
            except Exception:
                pass

    # ---------------------------------------------------------------- 消息循环
    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "loaded":
                    self._on_loaded(*item[1:])
                elif kind == "frame":
                    self._on_frame(*item[1:])
                elif kind == "done":
                    self._on_done(*item[1:])
                elif kind == "lframe":
                    self._on_lframe(*item[1:])
                elif kind == "jframe":
                    self._on_jframe(*item[1:])
                elif kind == "jprog":
                    self._on_jprog(*item[1:])
                elif kind == "job":
                    self._on_job(*item[1:])
                elif kind == "job_fail":
                    self._on_job_fail(*item[1:])
                elif kind == "err":
                    self.busy = self.app.busy = False
                    self.btn_run.state(["!disabled"])
                    self.var_txt.set(item[1])
        except queue.Empty:
            pass
        stoppable = self.busy and self.stop is not None and not self.stop.is_set()    # 只有逐帧流式能中途停
        self.btn_stop.state(["!disabled"] if stoppable else ["disabled"])
        if self.top.winfo_exists():
            self.top.after(30, self._poll)

    def close(self):
        self._stop()
        uistyle.close_when_idle(self.top, lambda: self.busy)
