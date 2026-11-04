# -*- coding: utf-8 -*-
"""heartview.py —— 心超工作站的「关键帧模式」「现场模式」视图（设计报告 7.3–7.5 节），嵌在 heartapp.py 主窗口里。

一个视图 = 本模式的工具条 + 三栏：
  左  视频（左室红、右室紫、右房橙轮廓；右上角 6 节段示意）+ 播放 / 逐帧 + 点击纠错（撤销 / 重置）；
  中  左心室容积曲线（▲ED ▼ES，点曲线跳到该帧）、事件时间线（心拍 / 关键帧与跟踪帧 / 纠错帧）、全心卡片；
  右  结果：EF、心率、80 % 区间条（40 / 50 分界）、LVEF 分档、复核提示、质量灯与原因、逐拍列表、临床测量；
  下  计算过程：调度、PL 推理帧数、每关键帧 PL 耗时、跟踪耗时、板上用时、延迟（现场）+ 进度。
关键帧模式：PC 解码整段 A4C 视频（录屏素材自动找扇区、裁成方块，echo_ws.read_avi），整段交给板子（jobs.echo_ef mode 2）。
现场模式：板上信号源逐帧"采"帧（jobs.echo_live，mode 3），每帧回掩膜和容积，滚动 EF；结束后与关键帧模式一样出全部结果。
两种模式都可选「关键帧推荐档」或「每帧推理（对照）」（echo_kf_config.json 的 keyframe / full）。

点击纠错（7.5 节）：分析结束后暂停在某一帧，左键补漏、右键去多 -> 板上 echo_inter（3 号，128 档整网约 22 ms）按
"本帧 + 当前掩膜 + 全部点击"重算这一帧；随后后面的每一帧（不分关键帧 / 跟踪帧）都由板上 echo_seq（4 号）从纠正后的掩膜
逐帧往后重算，一直到视频末尾，或到下一个人工纠过的帧为止（那一帧保留医生的纠正）。规则只有一条：纠一帧，后面跟着改。
10-30 板上实测（ES000108，33 帧）：不加点击从第 2 / 11 帧一路传到末尾，与每帧独立 PL 分割的 Dice 约 0.90、越往后不漂，约 25 ms / 帧。
容积按单平面圆盘法重算，逐拍 EF 与临床测量在上位机按纠正后的容积重算（板上原始结果保留，用于对照、撤销、重置）；
80 % 区间与质量灯沿用板上原始结果（标"纠错前"）。
仅用于工程教学与算法演示，不作为临床诊断依据；EchoNet-Dynamic 数据不得外传。
"""

import csv
import json
import math
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, ttk

import jobs
import proto
import sequence
import uistyle
from uistyle import FONT, FONT_HEAD, FONT_MONO, FONT_SMALL

VIEW = 448                                   # 112 x 4
CW, CH, TH = 560, 200, 92                    # 容积曲线 / 时间线画布
PLOT_L, PLOT_B = 44, CH - 22
RW = 404                                     # 右栏宽
FONT_EF = ("Microsoft YaHei UI", 30, "bold")
FONT_HR = ("Microsoft YaHei UI", 15, "bold")
FONT_TAG = ("Microsoft YaHei UI", 14, "bold")
HERE = os.path.dirname(os.path.abspath(__file__))
PLD_DIR = os.path.normpath(os.path.join(HERE, "..", ".."))
ECHONET_DIR = os.path.join(PLD_DIR, "EchoNet-Dynamic")
LIVE_DIR = os.path.join(PLD_DIR, "live", "src")
SCHEDS = (("keyframe", "关键帧推荐档（PL 约 18% 帧，其余板上跟踪）"), ("full", "每帧推理（对照：每帧都上 PL）"))
CROP_FIXED, CROP_AUTO = "固定（测试源的已知方块）", "自动找画面（前 1 s 里动的方块）"
CROPS = (CROP_FIXED, CROP_AUTO)              # 测试源默认固定方块（与设计报告图 7 / 5.3 节同口径）；采集卡总是自动
DEMO_SRC = "0X100009310A3BD7FC_z4.y8"        # 设计报告 5.3 节的 50 fps、174 帧源：推荐档 26 % 对每帧推理 121 %
LAT_H = 100                                  # 现场模式逐帧延迟图的高度
CAPTURE = ("采集卡 video0（YUYV 640×480，最长 20 s）", dict(kind="v4l2", name="video0", fps=60.0, frames=1200,
                                                         video=None, truth=None))
BAND_COL = ("#c2410c", "#b45309", "#047857")       # LVEF ≤40 / 41–49 / ≥50 的字色
BAND_BG = ("#fde2d6", "#fdf0c8", "#d8f3e6")
LIGHT_COL = {0: "#16a34a", 1: "#eab308", 2: "#dc2626"}
LIGHT_TXT = {0: "绿灯", 1: "黄灯", 2: "红灯"}
# 现场模式画面刷新间隔：PC 0.035 s；板端 DP（GTK 兼容层）把 112 帧放大到 448 叠轮廓一次要几十 ms，
# 按 0.035 s 刷主线程会被占满、画面越积越落后（10-31 实测），改 0.12 s（约 8 帧/秒，数据仍每帧都收）
RENDER_GAP = 0.12 if getattr(tk, "_IS_GTK_SHIM", False) else 0.035
VIEW_TXT = {0: "切面判断中", 1: "切面：未见右室，可能不是心尖四腔", 2: "切面：心尖四腔（看得到右室）"}
WIN_LENS = ("全段", "放好后", "2 s", "3 s", "4 s", "5 s")   # 分析片段长度（「放好后」= 最后一次判定锁定到结束，v5.26 CAMUS 源）
PAD, N = 8, 112


def live_sources():
    """现场模式的信号源：E:/PLD/live/src/index.json 里的 .y8（已传到板上 /root/echo_src）+ 采集卡。"""
    out = []
    p = os.path.join(LIVE_DIR, "index.json")
    if not os.path.exists(p):                        # 板上 DP 工作站：没有工程目录，用上位机目录里的拷贝
        p = os.path.join(HERE, "live_sources.json")
    try:
        with open(p, encoding="utf-8") as f:
            idx = json.load(f)
        for fn, m in sorted(idx.items()):
            out.append(("%s（%d 帧 %.0f fps，%d×%d）" % (fn, m["frames"], m["fps"], m["W"], m["H"]),
                        dict(kind="file", name=fn, fps=m["fps"], frames=m["frames"], video=m["video"],
                             truth=(m["x"], m["y"], m["side"]), zoom=m.get("zoom", 1.0),
                             quality=m.get("quality"),        # "camus"：v5.26 用 CAMUS 口径的滚动质量灯 + 切面
                             segments=m.get("segments"))))    # 模拟扫查各段（病人 / 切面 / 专家质量等级），自检用
    except (OSError, ValueError, KeyError):
        pass
    return out + [CAPTURE]


HMCQU_A4C = os.path.join(PLD_DIR, "HMC-QU Dataset-Kaggle v1.0 (1)", "HMC-QU Dataset-Kaggle v1.0", "HMC-QU", "A4C")


def read_video(ws, path):
    """与 echo_ws.read_avi 逐帧相同的读法（112 的 EchoNet 取三通道平均；录屏取灰度后 screen_to_112），
    另回录屏的原始首帧（BGR）和扇区方块，给「标尺定标」用；112 的视频没有原始画面，回 None。"""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(path)
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 50.0
    out, raw, first = [], [], None
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if fr.shape[:2] == (N, N):
            f = fr.astype(np.uint16)
            out.append(((f[..., 0] + f[..., 1] + f[..., 2] + 1) // 3).astype(np.uint8))
        else:
            if first is None:
                first = fr.copy()
            raw.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
    cap.release()
    if not out and not raw:
        raise ValueError("读不出视频帧")
    if raw:
        v, crop = ws.screen_to_112(np.stack(raw))
        return v, fps, dict(frame=first, crop=crop, path=path)
    return np.stack(out), fps, None


def raw_video_path(name):
    """现场模式信号源对应的原始录屏（定标要看原始画面上的深度刻度）：工程里的 HMC-QU 目录或上位机 demo/。"""
    for d in (HMCQU_A4C, os.path.join(HERE, "demo")):
        p = os.path.join(d, name or "")
        if name and os.path.exists(p):
            return p
    return None


class Ev_(object):
    """自动定标时代替鼠标事件（只用 x / y）"""
    def __init__(self, x, y):
        self.x, self.y = x, y


def depth_ticks(frame_bgr):
    """读超声机屏幕上的深度刻度（黄色短线，1 cm 一个；带数字的长刻度 5 cm 一个）-> (点 1, 点 2, 厘米, 说明) 或 None。
    只用 numpy / scipy（板上 numpy 1.13 也能跑）：黄色像素连通块 -> 面积 <= 25 的当刻度候选 -> 穷举两点连线、取共线最多的
    一条（距离 < 2.5 px）-> 相邻刻度间距的中位数 = 1 cm -> 头两个长刻度（面积 >= 8、宽 >= 6）相距必须正好 5 个间距才算数。
    HMC-QU 292 段首帧：236 段找到（其余回 None，照旧手点），长 / 短刻度两种量法差 < 1.8 %，1 cm = 18.9–38.4 像素；
    ES000108 自动 27.80 像素/cm，手点 5→10 是 27.88。"""
    np = __import__("numpy")
    from scipy import ndimage
    f = np.asarray(frame_bgr).astype(np.int32)
    if f.ndim != 3 or f.shape[2] < 3:
        return None
    b, g, r = f[..., 0], f[..., 1], f[..., 2]
    yel = (r - b > 25) & (g - b > 25) & (r > 90)
    lab, n = ndimage.label(yel, structure=np.ones((3, 3), int))
    if n < 6:
        return None
    idx = np.arange(1, n + 1)
    area = np.asarray(ndimage.sum(yel, lab, idx))
    cy, cx = np.array(ndimage.center_of_mass(yel, lab, idx)).T
    bw = np.array([s[1].stop - s[1].start for s in ndimage.find_objects(lab)])
    cand = np.nonzero(area <= 25)[0]
    if len(cand) < 8 or len(cand) > 400:
        return None
    best = None
    for ii in range(len(cand)):
        for jj in range(ii + 1, len(cand)):
            i, j = cand[ii], cand[jj]
            dx, dy = cx[j] - cx[i], cy[j] - cy[i]
            ln = (dx * dx + dy * dy) ** 0.5
            if ln < 8:
                continue
            ux, uy = dx / ln, dy / ln
            inl = cand[np.abs((cx[cand] - cx[i]) * uy - (cy[cand] - cy[i]) * ux) < 2.5]
            if best is None or len(inl) > len(best[0]):
                best = (inl, ux, uy, cx[i], cy[i])
    inl, ux, uy, x0, y0 = best
    if len(inl) < 8:
        return None
    t = (cx[inl] - x0) * ux + (cy[inl] - y0) * uy
    o = np.argsort(t)
    inl, t = inl[o], t[o]
    d = np.diff(t)
    d = d[d > 5]
    if len(d) < 4:
        return None
    p0 = np.median(d)
    k = np.round(d / p0)
    ok = (k >= 1) & (np.abs(d / np.maximum(k, 1) - p0) < 0.15 * p0)
    if ok.sum() < 4:
        return None
    period = float(d[ok].sum() / k[ok].sum())              # 1 cm 多少原始像素
    major = [m for m in range(len(inl)) if area[inl[m]] >= 8 and bw[inl[m]] >= 6]
    if not 10 <= period <= 80 or len(major) < 2:
        return None
    a, c = major[0], major[1]
    if abs((t[c] - t[a]) / period - 5.0) > 0.2:
        return None
    return ((float(cx[inl[a]]), float(cy[inl[a]])), (float(cx[inl[c]]), float(cy[inl[c]])), 5.0,
            dict(period=period, ticks=len(inl), px_5cm=float(t[c] - t[a])))


class CalibDialog(object):
    """屏幕标尺定标：在原始录屏首帧上点两个深度刻度，填它们相距几厘米 -> 原始画面每像素多少毫米。
    这和接上采集卡以后要做的一样（超声机屏幕上都有深度标尺）；扇区缩到 112 的换算由调用方做。
    打开时先自动读刻度（depth_ticks）：找到就把两点放在相邻两个长刻度（5 cm）上，核对后点「确定」；找不到照旧手点。"""

    def __init__(self, master, frame_bgr, title, on_done, cm=5.0):
        from PIL import Image, ImageTk
        self.on_done, self.pts = on_done, []
        h, w = frame_bgr.shape[:2]
        self.k = min(2.0, 960.0 / w)
        self.top = uistyle.window(tk.Toplevel(master), "屏幕标尺定标 — %s" % title)
        bar = tk.Frame(self.top, bg=uistyle.C_CARD)
        bar.pack(fill="x", padx=12, pady=(10, 6))
        uistyle.label(bar, "在扇区边上的深度刻度里点两个带数字的刻度（例如 5 和 10），再填它们相距几厘米：",
                      fg=uistyle.C_MUTED).pack(side="left")
        self.var_cm = tk.StringVar(value="%g" % cm)
        ttk.Entry(bar, textvariable=self.var_cm, width=4, font=FONT).pack(side="left", padx=(4, 2))
        uistyle.label(bar, "cm", fg=uistyle.C_MUTED).pack(side="left")
        ttk.Button(bar, text="重点", command=self._reset).pack(side="left", padx=(12, 0))
        uistyle.primary_button(bar, "确定", self._ok).pack(side="left", padx=(8, 0))
        self.cv = tk.Canvas(self.top, width=int(w * self.k), height=int(h * self.k), bg="#000", highlightthickness=0,
                            cursor="crosshair")
        self.cv.pack(padx=12)
        im = Image.fromarray(frame_bgr[..., ::-1].copy()).resize((int(w * self.k), int(h * self.k)), Image.NEAREST)
        self.photo = ImageTk.PhotoImage(im)
        self.cv.create_image(0, 0, anchor="nw", image=self.photo)
        self.cv.bind("<ButtonPress-1>", self._click)
        self.var_txt = tk.StringVar(value="还没点")
        uistyle.label(self.top, var=self.var_txt, fg=uistyle.C_TEXT, bg=uistyle.C_BG, anchor="w") \
            .pack(fill="x", padx=12, pady=(6, 10))
        try:
            self.auto = depth_ticks(frame_bgr)
        except Exception:                            # 读不出刻度不影响手点
            self.auto = None
        if self.auto is not None:
            (x0, y0), (x1, y1), cm_auto, info = self.auto
            self.var_cm.set("%g" % cm_auto)
            self._click(Ev_(x0 * self.k, y0 * self.k))
            self._click(Ev_(x1 * self.k, y1 * self.k))
            self.var_txt.set("已自动读出深度刻度：两个长刻度相距 %g cm = %.1f 个原始像素（共 %d 个刻度，1 cm = %.1f 像素）；"
                             "核对后点「确定」，不对就点「重点」手点" % (cm_auto, self.dist(), info["ticks"], info["period"]))

    def _click(self, e):
        if len(self.pts) >= 2:
            self._reset()
        self.pts.append((e.x, e.y))
        self.cv.create_oval(e.x - 6, e.y - 6, e.x + 6, e.y + 6, outline="#22ff66", width=2, tags="pt")
        if len(self.pts) == 2:
            (x0, y0), (x1, y1) = self.pts
            self.cv.create_line(x0, y0, x1, y1, fill="#22ff66", width=2, tags="pt")
            self.var_txt.set("两点相距 %.1f 个原始像素；点「确定」" % (self.dist()))
        else:
            self.var_txt.set("再点一个刻度")

    def dist(self):
        (x0, y0), (x1, y1) = self.pts
        return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / self.k

    def _reset(self):
        self.pts = []
        self.cv.delete("pt")
        self.var_txt.set("还没点")

    def _ok(self):
        try:
            cm = float(self.var_cm.get())
        except ValueError:
            self.var_txt.set("距离要填数字（厘米）")
            return
        if len(self.pts) != 2 or self.dist() < 5 or cm <= 0:
            self.var_txt.set("先在两个刻度上各点一下")
            return
        self.on_done(cm * 10.0 / self.dist())
        self.top.destroy()


def model_by_name(name):
    return next((m for m in proto.MODELS if m["name"] == name), None)


def chip(parent, title, width=None):
    """计算过程条里的一个小指标：灰色小标题 + 粗体数值，返回数值的 StringVar。"""
    f = tk.Frame(parent, bg=uistyle.C_CARD)
    f.pack(side="left", padx=(0, 22))
    uistyle.label(f, title, font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
    v = tk.StringVar(value="—")
    kw = dict(width=width, anchor="w") if width else {}
    uistyle.label(f, var=v, font=FONT_HEAD, **kw).pack(anchor="w")
    return v


class _Stopped(Exception):
    pass


class EchoView(object):
    """live=False：关键帧模式（整段视频上传）；live=True：现场模式（板上信号源）。app 提供 root / link / var_host /
    var_port / busy（板端一次只服务一个连接）/ status(msg)。"""

    def __init__(self, app, parent, live=False):
        import numpy
        import echo_ws
        self.app, self.live = app, live
        self.np, self.ws = numpy, echo_ws
        self.model = model_by_name("echonet_lv")
        self.m_inter, self.m_seq = model_by_name("echo_inter"), model_by_name("echo_seq")
        self.conf = echo_ws.load_config()
        if self.model:
            echo_ws.SCALE[0] = self.model["scale"]
        echo_ws.THRESH = tuple(self.conf.get("qc_thresholds", echo_ws.THRESH))
        self.ref = self._load_filelist()
        self.hmc = self._load_hmcqu()
        self.queue = queue.Queue()
        self.busy = False
        self.stop = None
        self.playing = False
        self.open_report = True                      # 生成报告后用浏览器打开（自检脚本关掉）
        self.frames = self.fps = self.name = None
        self.src = None
        self.rawvid = None                           # 关键帧模式：录屏原始首帧 + 扇区方块（read_video）
        self.photos = {}
        self.prev_run = None                         # 现场模式上一次跑完的逐帧延迟（灰线对照）
        self._reset_state()
        self.frame = tk.Frame(parent, bg=uistyle.C_BG)
        self._build()
        self.frame.after(30, self._poll)

    def _reset_state(self):
        self.masks, self.vols, self.keyv = {}, [], []
        self.res = self.raw = None                   # 板上结果（原样）/ jobs.run 的整个返回
        self.result = None                           # 显示用的逐拍结果（纠错后为上位机重算）
        self.kf_cfg = self.hc_cfg = None
        self.heart, self.rh_frames = None, {}
        self.clinical = None
        self.ef_board = self.ef_now = None
        self.orig = None                             # 板上原始掩膜 / 容积（重置用）
        self.clicks, self.undo, self.fixed = {}, [], {}   # 帧 -> 点击；撤销栈；帧 -> "fix" / "prop"
        self.fix_ms = []
        self.stats = None
        self.live_info = None
        self.roll = (None, 0)                        # 现场：板上滚动质量灯 / 切面（服务端 v5.25 RQ01）的当前值
        self.roll_tag = ""                           # 灯的口径（v5.26：CAMUS 源 = CAMUS 专家等级标定，其余 = EchoNet 口径）
        self.roll_hold = self.roll_moved = False     # v5.26：已判定保持中 / 保持后探头动过（重新判断）
        self.lat, self.proc = [], []                 # 现场模式逐帧：采到 → 掩膜 + 容积 / 处理线程用时（ms，板上时间戳）
        self.cur = 0
        self.t_render = self.t_curve = 0.0
        self.win = None                              # 分析片段 (a, b)：EF / 逐拍 / 临床测量只用第 a .. b-1 帧；None = 全段

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
        d = os.path.join(PLD_DIR, "wallmotion", "results")
        try:
            with open(os.path.join(d, "labels_a4c.json"), encoding="utf-8") as f:
                rows = json.load(f)["rows"]
            with open(os.path.join(d, "split.json"), encoding="utf-8") as f:
                split = json.load(f)["split"]
        except (OSError, ValueError, KeyError):
            return {}
        return {r["name"] + ".avi": (r["seg"], split.get(r["name"], "?")) for r in rows}

    # ================================================================ 界面
    def _build(self):
        t = self.frame
        self._build_toolbar(t)
        body = tk.Frame(t, bg=uistyle.C_BG)
        body.grid(row=1, column=0, sticky="n", padx=12, pady=(10, 0))
        self._build_video(body)
        self._build_middle(body)
        self._build_results(body)
        self._build_process(body)
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(1, weight=1)
        self._redraw()                               # 还没数据时曲线 / 时间线里先写上会显示什么

    def _build_toolbar(self, t):
        bar = tk.Frame(t, bg=uistyle.C_CARD, highlightthickness=1, highlightbackground=uistyle.C_BORDER)
        bar.grid(row=0, column=0, sticky="ew")
        row = tk.Frame(bar, bg=uistyle.C_CARD)
        row.pack(fill="x", padx=14, pady=8)
        if self.live:
            uistyle.label(row, "信号源", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 4))
            self.sources = live_sources()
            self.var_src = tk.StringVar(value=next((s[0] for s in self.sources if s[1]["name"] == DEMO_SRC),
                                                   self.sources[0][0]))
            ttk.Combobox(row, textvariable=self.var_src, width=36, font=FONT, state="readonly",
                         values=[s[0] for s in self.sources]).pack(side="left")
            uistyle.label(row, "画面方块", fg=uistyle.C_MUTED).pack(side="left", padx=(12, 4))
            self.var_crop = tk.StringVar(value=CROPS[0])
            ttk.Combobox(row, textvariable=self.var_crop, width=22, font=FONT, state="readonly",
                         values=list(CROPS)).pack(side="left")
        else:
            ttk.Button(row, text="打开视频…", command=self._open).pack(side="left")
            self.var_name = tk.StringVar(value="（未打开视频：A4C 心尖四腔 .avi，录屏素材自动找扇区）")
            uistyle.label(row, var=self.var_name, fg=uistyle.C_MUTED, width=44, anchor="w").pack(side="left", padx=(8, 0))
        uistyle.vsep(row)
        uistyle.label(row, "运行方式", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 4))
        self.var_sched = tk.StringVar(value=SCHEDS[0][1])
        ttk.Combobox(row, textvariable=self.var_sched, width=32 if self.live else 36, font=FONT, state="readonly",
                     values=[s[1] for s in SCHEDS]).pack(side="left")
        self.btn_run = uistyle.primary_button(row, "▶ 开始采集" if self.live else "▶ 开始分析", self.run)
        self.btn_run.pack(side="right")
        self.btn_stop = ttk.Button(row, text="■ 停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="right", padx=(0, 8))
        self.btn_rep = ttk.Button(row, text="导出证据报告", command=self._report, state="disabled")
        self.btn_rep.pack(side="right", padx=(0, 8))

    def _build_video(self, body):
        col = tk.Frame(body, bg=uistyle.C_BG)
        col.grid(row=0, column=0, sticky="n", padx=(0, 12))
        f, b = uistyle.panel(col, "视频", "红 左室　紫 右室　橙 右房")
        f.pack(fill="x")
        self.btn_scale = ttk.Button(f.head, text="标尺定标…", command=self._calibrate)
        self.btn_scale.pack(side="right")
        self.cv_v = tk.Canvas(b, width=VIEW, height=VIEW, bg="#000", highlightthickness=0, cursor="crosshair")
        self.cv_v.pack()
        self.cv_img = self.cv_v.create_image(0, 0, anchor="nw")
        self.cv_v.create_text(VIEW // 2, VIEW // 2, fill="#9aa3ad", font=FONT, tags="hint", justify="center",
                              text=("点右上角「▶ 开始采集」\n\n板上信号源按真实帧率逐帧送入，\n轮廓、容积曲线、延迟图边采边更新"
                                    if self.live else "「打开视频…」选一段 A4C 视频\n再点「▶ 开始分析」\n\n"
                                    "超声机录屏会自动找扇区、裁成方块"))
        self.cv_v.bind("<ButtonPress-1>", lambda e: self._click(e, True))
        self.cv_v.bind("<Button-3>", lambda e: self._click(e, False))
        nav = tk.Frame(b, bg=uistyle.C_CARD)
        nav.pack(fill="x", pady=(8, 0))
        ttk.Button(nav, text="◀", width=3, command=lambda: self.step(-1)).pack(side="left")
        self.btn_play = ttk.Button(nav, text="▶ 播放", width=8, command=self.toggle_play)
        self.btn_play.pack(side="left", padx=4)
        ttk.Button(nav, text="▶|", width=3, command=lambda: self.step(1)).pack(side="left")
        self.var_ft = tk.StringVar(value="（未载入）")
        # 固定宽度：播放时这行每帧变长变短，会挤动各列、让右侧文本框折行来回变（11-03 界面上下闪）
        uistyle.label(nav, var=self.var_ft, font=FONT_MONO, anchor="w", width=50).pack(side="left", padx=(10, 0))
        self._scale_busy = False                     # _goto 自己设进度条时为真：板上兼容层的 set() 会回调 command，不拦就同一帧画两遍
        self.scale = ttk.Scale(b, from_=0, to=0, orient="horizontal", length=VIEW,
                               command=lambda v: None if self._scale_busy else self._goto(int(float(v)), from_scale=True))
        self.scale.pack(pady=(6, 0))
        fx, bx = uistyle.panel(col, "点击纠错", "暂停后左键补漏、右键去多；FPGA 重算本帧，后面各帧跟着改")
        fx.pack(fill="x", pady=(10, 0))
        ttk.Button(fx.head, text="重置", width=6, command=self.reset_fix).pack(side="right")
        ttk.Button(fx.head, text="撤销", width=6, command=self.undo_fix).pack(side="right", padx=(0, 6))
        self.var_fix = tk.StringVar(value="分析结束后可用（Ctrl+Z 撤销）")
        uistyle.label(bx, var=self.var_fix, fg=uistyle.C_MUTED, wrap=True).pack(fill="x")

    def _build_middle(self, body):
        col = tk.Frame(body, bg=uistyle.C_BG)
        col.grid(row=0, column=1, sticky="n", padx=(0, 12))
        f1, b1 = uistyle.panel(col, "左心室容积曲线", "▲ 舒张末 ED　▼ 收缩末 ES　点曲线跳到该帧")
        f1.pack(fill="x")
        self.cv = tk.Canvas(b1, width=CW, height=CH, bg="#fff", highlightthickness=0)
        self.cv.pack()
        self.cv.bind("<Button-1>", lambda e: self._curve_click(e.x))
        self.cv.bind("<B1-Motion>", lambda e: self._curve_click(e.x))
        # 分析片段（11-02）：滚动条的滑块 = 参与 EF / 逐拍 / 临床测量的那段；CAMUS 源默认「放好后」（最后一次判定锁定起）
        wr = tk.Frame(b1, bg=uistyle.C_CARD)
        wr.pack(fill="x", pady=(4, 0))
        uistyle.label(wr, "分析片段", font=FONT).pack(side="left")
        self.var_winlen = tk.StringVar(value=WIN_LENS[0])
        cb = ttk.Combobox(wr, textvariable=self.var_winlen, width=8, font=FONT, state="readonly", values=WIN_LENS)
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda e: self._win_len_changed())
        self.var_win = tk.StringVar(value="全段")
        uistyle.label(wr, var=self.var_win, font=FONT).pack(side="right", padx=(6, 6))
        self.sb_win = ttk.Scrollbar(wr, orient="horizontal", command=self._win_cmd)
        self.sb_win.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.sb_win.set(0.0, 1.0)
        f2, b2 = uistyle.panel(col, "事件时间线", "关键帧与跟踪帧分开显示，方便核对 ED / ES")
        f2.pack(fill="x", pady=(10, 0))
        self.tl = tk.Canvas(b2, width=CW, height=TH, bg="#fafafa", highlightthickness=0)
        self.tl.pack()
        self.tl.bind("<Button-1>", lambda e: self._curve_click(e.x))
        f3, b3 = uistyle.panel(col, "全心", "节段室壁运动 + 右室 / 右房（板上，ED / ES 关键帧加跑）；仅复核提示")
        f3.pack(fill="x", pady=(10, 0))
        row = tk.Frame(b3, bg=uistyle.C_CARD)
        row.pack(fill="x")
        self.lab_glyph = tk.Label(row, bg=uistyle.C_CARD, bd=0)
        self.lab_glyph.pack(side="left", anchor="n", padx=(0, 10))
        right = tk.Frame(row, bg=uistyle.C_CARD)
        right.pack(side="left", fill="both", expand=True)
        self.var_wm = tk.StringVar(value="节段室壁运动：—")
        self.lab_wm = uistyle.label(right, var=self.var_wm, font=FONT_HEAD, wrap=True)
        self.lab_wm.pack(fill="x")
        hr = tk.Frame(right, bg=uistyle.C_CARD)
        hr.pack(fill="x", pady=(6, 0))
        self.var_rv = tk.StringVar(value="—")
        self.var_ra = tk.StringVar(value="—")
        for title, var in (("RV/LV 舒张末面积比", self.var_rv), ("RA/LV 面积比", self.var_ra)):
            c = tk.Frame(hr, bg=uistyle.C_CARD)
            c.pack(side="left", padx=(0, 26))
            uistyle.label(c, title, font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
            uistyle.label(c, var=var, font=FONT_HR, fg=uistyle.C_PURPLE if var is self.var_rv else "#c2410c").pack(anchor="w")
        self.var_heart = tk.StringVar(value="关键帧 / 现场模式跑完后显示（板端 v5.24）")
        uistyle.text_box(b3, self.var_heart, height=4, font=FONT_SMALL, fg="#245").pack(fill="x", pady=(6, 0))
        self._draw_glyph(None)

    def _build_results(self, body):
        col = tk.Frame(body, bg=uistyle.C_BG, width=RW)
        col.grid(row=0, column=2, sticky="n")
        f, b = uistyle.panel(col, "结果", "不作诊断")
        f.pack(fill="x")
        ttk.Button(f.head, text="送到快照测量 →", command=self._send_snapshot).pack(side="right")
        top = tk.Frame(b, bg=uistyle.C_CARD)
        top.pack(fill="x")
        efc = tk.Frame(top, bg=uistyle.C_CARD)
        efc.pack(side="left")
        uistyle.label(efc, "射血分数 EF", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
        self.var_ef = tk.StringVar(value="—")
        self.lab_ef = uistyle.label(efc, var=self.var_ef, font=FONT_EF, fg=uistyle.C_TEXT)
        self.lab_ef.pack(anchor="w")
        hrc = tk.Frame(top, bg=uistyle.C_CARD)
        hrc.pack(side="left", padx=(26, 0), anchor="n")
        uistyle.label(hrc, "心率", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
        self.var_hr = tk.StringVar(value="—")
        uistyle.label(hrc, var=self.var_hr, font=FONT_HR).pack(anchor="w", pady=(6, 0))
        qc = tk.Frame(top, bg=uistyle.C_CARD)
        qc.pack(side="left", padx=(26, 0), anchor="n")
        uistyle.label(qc, "图像质量", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
        qrow = tk.Frame(qc, bg=uistyle.C_CARD)
        qrow.pack(anchor="w", pady=(6, 0))
        self.cv_light = tk.Canvas(qrow, width=22, height=22, bg=uistyle.C_CARD, highlightthickness=0)
        self.cv_light.pack(side="left")
        self.var_light = tk.StringVar(value="—")
        uistyle.label(qrow, var=self.var_light, font=FONT_HR).pack(side="left", padx=(4, 0))
        self.var_efnote = tk.StringVar(value="")
        uistyle.label(b, var=self.var_efnote, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x")
        self.cv_iv = tk.Canvas(b, width=RW - 24, height=62, bg=uistyle.C_CARD, highlightthickness=0)
        self.cv_iv.pack(pady=(6, 0))
        self.var_band = tk.StringVar(value="LVEF 分档：—")
        self.lab_band = uistyle.label(b, var=self.var_band, font=FONT_HEAD, anchor="w")
        self.lab_band.pack(fill="x", pady=(4, 0))
        self.var_review = tk.StringVar(value="")
        self.lab_review = tk.Label(b, textvariable=self.var_review, font=FONT_HEAD, fg="white", bg=uistyle.C_CARD,
                                   anchor="w", justify="left", padx=8, pady=4, wraplength=RW - 40)
        self.lab_review.pack(fill="x", pady=(4, 0))
        self.var_qwhy = tk.StringVar(value="")
        uistyle.label(b, var=self.var_qwhy, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x", pady=(4, 0))
        uistyle.label(b, "提示方向（辅助复核，不作诊断；判据见报告）", font=FONT_SMALL, fg=uistyle.C_MUTED) \
            .pack(anchor="w", pady=(6, 0))
        self.var_hint = tk.StringVar(value="")       # clinical_hints：测量值 -> 可能与哪类问题有关
        uistyle.text_box(b, self.var_hint, height=4, font=FONT_SMALL, fg="#9a3412").pack(fill="x")
        uistyle.label(b, "逐拍", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w", pady=(4, 0))
        self.var_beats = tk.StringVar(value="")
        uistyle.text_box(b, self.var_beats, height=2, font=FONT_MONO).pack(fill="x")
        uistyle.label(b, "临床测量", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w", pady=(4, 0))
        self.var_clin = tk.StringVar(value="")
        uistyle.text_box(b, self.var_clin, height=3, font=FONT_SMALL, fg="#245").pack(fill="x")
        self.var_ref = tk.StringVar(value="")
        uistyle.label(b, var=self.var_ref, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x", pady=(4, 0))
        self._draw_interval()
        self._draw_light(None)

    def _build_process(self, body):
        c = uistyle.card(body)
        c.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 10))
        row = tk.Frame(c, bg=uistyle.C_CARD)
        row.pack(fill="x", padx=14, pady=(8, 0))
        head = tk.Frame(row, bg=uistyle.C_CARD)
        head.pack(side="left", padx=(0, 18), anchor="n")
        uistyle.label(head, "计算过程", font=FONT_HEAD).pack(anchor="w")
        uistyle.label(head, "分割阈值 logit ≥ %d · 128 档" % (self.model["thr"] if self.model else 1),
                      font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
        self.var_k_sched = chip(row, "运行方式", 8)
        self.var_k_pl = chip(row, "PL 推理帧", 17)
        self.var_k_plms = chip(row, "每关键帧 PL", 8)
        self.var_k_trk, self.var_k_total, self.var_k_proc, self.var_k_lat, self.var_k_fix = (tk.StringVar(value="—")
                                                                                            for _ in range(5))
        if self.live:                                    # 设计报告 5.3 节的口径：每帧处理占帧间隔、采到 → 掩膜 + 容积
            self.var_k_proc = chip(row, "每帧处理 / 帧间隔（预算占用）", 22)
            self.var_k_lat = chip(row, "延迟 P50 / P99（采到 → 掩膜 + 容积）", 18)
            self.var_k_fps = chip(row, "帧率（信号源 · 处理帧数）", 18)
        else:
            self.var_k_trk = chip(row, "板上跟踪", 10)
            self.var_k_total = chip(row, "板上用时 / 视频", 14)
            self.var_k_fps = chip(row, "帧率（视频 / 板上处理）", 14)
            self.var_k_fix = chip(row, "纠错", 6)
        self.pbar = ttk.Progressbar(c, mode="determinate")
        if self.live:                                    # 逐帧延迟图代替进度条（x 本身就是进度）
            self.cv_lat = tk.Canvas(c, height=LAT_H, bg="#fafafa", highlightthickness=0)
            self.cv_lat.pack(fill="x", padx=14, pady=(8, 10))
            self.cv_lat.bind("<Configure>", lambda e: self._draw_latency())
        else:
            self.pbar.pack(fill="x", padx=14, pady=(8, 10))
        # 详细文字走主窗口状态栏（省一行高度，1080p 屏也放得下）
        self.var_txt = tk.StringVar(value=("选信号源，点「开始采集」：板上逐帧采集、分割、跟踪，每帧回掩膜和容积"
                                           if self.live else "「打开视频…」选一段 A4C 视频，再「开始分析」（整段交给板子，PC 只显示）"))

    def _sched(self):
        return next((k for k, v in SCHEDS if v == self.var_sched.get()), "keyframe")

    def _hostport(self):
        try:
            return self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except ValueError:
            return self.app.link.host, self.app.link.port

    def _say(self, msg):
        self.var_txt.set(msg)
        if self.app.views.get(self.app.mode) is self or not self.app.views:   # 别的模式在前台时不抢状态栏
            self.app.status(msg.splitlines()[0] if msg else "")

    # ================================================================ 载入（关键帧模式）
    def _open(self):
        if self.busy:
            return
        vd = os.path.join(ECHONET_DIR, "Videos")
        path = filedialog.askopenfilename(parent=self.frame, initialdir=vd if os.path.isdir(vd) else HERE,
                                          title="打开 A4C 心超视频", filetypes=[("AVI", "*.avi"), ("全部", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        self._say("载入 %s …" % os.path.basename(path))

        def work():
            try:
                v, fps, raw = read_video(self.ws, path)
                self.queue.put(("loaded", path, v, fps, raw))
            except Exception as e:
                self.queue.put(("err", "载入失败：%s" % e))
        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, path, v, fps, raw=None):
        self._pause()
        self.frames, self.fps, self.name = v, fps, os.path.basename(path)
        self.rawvid = raw                            # 录屏的原始首帧 + 扇区方块（标尺定标用）；EchoNet 112 视频为 None
        self._show_scale()
        self._reset_state()
        self._clear_results()
        self.var_name.set("%s　%d 帧 · %.0f fps · %.2f s" % (self.name, len(v), fps, len(v) / fps))
        self.scale.configure(to=len(v) - 1)
        self._ref_line()
        self._say("%s：%d 帧，%.0f 帧/秒（%.2f s）。点「开始分析」" % (self.name, len(v), fps, len(v) / fps))
        self._goto(0)
        self._redraw()

    def _ref_line(self):
        name = self.name if not self.live else (self.src or {}).get("video")
        r = self.ref.get(name or "")
        h = self.hmc.get(name or "")
        if r:
            self.var_ref.set("数据集标注 EF（EchoNet FileList，仅作对照）：%.1f%%，划分 %s" % r)
        elif h:
            mi = [self.ws.WM_NAMES[k] for k, f in enumerate(h[0]) if f]
            self.var_ref.set("HMC-QU 标注（仅作对照，%s）：%s" % ({"dev": "开发集", "test": "测试集"}.get(h[1], h[1]),
                                                            ("心梗节段 " + "、".join(mi)) if mi else "无心梗节段"))
        else:
            self.var_ref.set("")

    def _report_quality(self):
        """报告里的质量灯：CAMUS 源（v5.26 chain 1）用扫查最后 1 s 的 CAMUS 口径灯（整段的灯是程序 16 链 = EchoNet 口径）。"""
        ro = (self.res or {}).get("rolling") or {}
        if ro.get("chain", 0) in (1, 2) and ro.get("updates"):
            return dict(light=ro["updates"][-1][1], reasons=[])
        return (self.res or {}).get("quality")

    def data_note(self):
        """本段素材的出处（状态栏 / 报告页脚）。"""
        if self.live and (self.src or {}).get("quality") == "camus":
            return "画面来自 CAMUS 验证集（Leclerc et al. 2019，CC BY-NC-SA 4.0，非商业），模拟扫查"
        name = (self.src or {}).get("video") if self.live else self.name
        name = name or self.name or ""
        if name in self.ref:
            return "画面来自 EchoNet-Dynamic（Stanford AIMI，研究使用协议，不得外传）"
        if name in self.hmc or name.upper().startswith("ES0"):
            return "画面来自 HMC-QU（CC BY-NC-SA 3.0 IGO）"
        return "画面来自公开数据集"

    # ================================================================ 板上分析
    def run(self):
        if self.busy or self.app.busy:
            self._say("板子正忙，稍等")
            return
        if self.model is None:
            self._say("proto.MODELS 里没有 echonet_lv（上位机太旧）")
            return
        if self.live:
            self._run_live()
            return
        if self.frames is None:
            self._say("先「打开视频…」")
            return
        self._pause()
        self._reset_state()
        self._clear_results()
        sched = self._sched()
        self.pbar.configure(maximum=len(self.frames), value=0)
        self._set_busy(True, stoppable=False)
        self._say("关键帧模式：上传 %s（%d 帧）到板子，%s …" % (self.name, len(self.frames), self.var_sched.get()))
        threading.Thread(target=self._job_worker, args=(self._hostport(), sched), daemon=True).start()

    def _job_worker(self, hp, sched):
        link = self.app.link
        t0 = time.perf_counter()
        try:
            link.host, link.port = hp
            link.connect()                           # 板端 10 s 空闲超时：任务前总是重连
            caps = jobs.caps(link)
            if 2 not in caps.get("echo_modes", []):
                self.queue.put(("err", "板端不支持关键帧模式（需要服务端 v5.21+，现在是 %s）" % caps.get("server", "?")))
                return
            link.connect()
            kf = jobs.load_kf_config(sched)
            hc = jobs.load_heart_config() if "heart" in caps else None
            self.queue.put(("cfg", kf, hc, caps.get("server")))
            r = jobs.echo_ef(link, self.conf, self.model["scale"], self.frames, self.fps, model=self.model["index"],
                             flags=jobs.ECHO_MASKS, on_progress=lambda p: self.queue.put(("prog", p)),
                             keyframe=kf, heart=hc)
            link.close()                             # 连接与平台扩展共用：用完就放，下一个用的人重连（板端 10 s 空闲会断）
            self.queue.put(("job", r, time.perf_counter() - t0))
        except Exception as e:
            link.close()
            self.queue.put(("err", "板上分析失败：%s" % e))

    def _run_live(self):
        label, src = next((s for s in self.sources if s[0] == self.var_src.get()), self.sources[0])
        crop = src["truth"] if self.var_crop.get() == CROP_FIXED and src["truth"] else None
        if src.get("quality") == "camus" and src.get("truth"):
            crop = src["truth"]                          # CAMUS 口径要固定方块（板上 128 帧 = 原 CAMUS 帧，逐位）
        np = self.np
        self._pause()
        if self.lat and self.res is not None:            # 上一次完整跑完的延迟留作灰线对照（推荐档 vs 每帧推理）
            lat = self.res["stream"]["latency_ms"]
            self.prev_run = dict(lat=list(self.lat), fps=self.fps, p50=lat["p50"], p99=lat["p99"], src=self.name,
                                 label="%s · %s" % ("推荐档" if self.res["keyframe"]["key_frac"] < 1 else "每帧推理",
                                                    self.name))
        self._reset_state()
        self._clear_results()
        self.src = src
        self.name, self.fps = src["name"], src["fps"]
        self.frames = np.zeros((src["frames"], N, N), np.uint8)
        self.scale.configure(to=src["frames"] - 1)
        self._ref_line()
        self.pbar.configure(maximum=src["frames"], value=0)
        self.stop = threading.Event()
        self._set_busy(True, stoppable=True)
        self._say("现场模式：板上信号源 %s，%s …" % (src["name"], "固定方块 %s" % (crop,) if crop else "先找画面（约 1 s）"))
        self._goto(0)
        self._redraw()
        threading.Thread(target=self._live_worker, args=(self._hostport(), self._sched(), src, crop, self.stop),
                         daemon=True).start()

    def _live_worker(self, hp, sched, src, crop, stop):
        link = self.app.link
        t0 = time.perf_counter()
        try:
            link.host, link.port = hp
            link.connect()
            caps = jobs.caps(link)
            if 3 not in caps.get("echo_modes", []):
                self.queue.put(("err", "板端不支持现场模式（需要服务端 v5.22+，现在是 %s）" % caps.get("server", "?")))
                return
            link.connect()
            kf = jobs.load_kf_config(sched)
            hc = jobs.load_heart_config() if "heart" in caps else None
            # v5.25：扫查时滚动灯 + 切面；板端 DP（GTK 兼容层）上不开：DP 界面自己占 CPU，开了现场推荐档 P99 在 71–83 ms 浮动
            roll = jobs.ROLL if caps.get("live_roll") and hc is not None and not getattr(tk, "_IS_GTK_SHIM", False) else None
            tag = "EchoNet 口径，仅作提示" if roll else ""
            heart = hc
            if src.get("quality") == "camus":            # v5.26：CAMUS 源用 CAMUS 口径（这个源就是为了看灯，DP 上也开）
                # 不请求全心：半个心动周期往返播放，节段室壁运动 / 右心比值不成立（右室程序号单独写在 RQ01 里，切面不受影响）
                heart = None
                if caps.get("live_roll_camus"):
                    roll, tag = jobs.load_roll_camus(), "CAMUS 专家等级标定"
                    if roll["chain"] == 2 and caps.get("live_f1_engine"):   # 关键帧也用 F1：EF 按 CAMUS TRAIN 校准
                        kf = dict(kf, a=roll["camus"]["ef_a"], b=roll["camus"]["ef_b"])
                    else:
                        roll["chain"] = 1                    # 老服务端：灯照样，曲线仍是程序 16
                else:
                    roll, tag = None, "CAMUS 口径的灯需要服务端 v5.26（现在是 %s）" % caps.get("server", "?")
            self.queue.put(("rolltag", tag))
            self.queue.put(("cfg", kf, hc, caps.get("server")))

            def on_live(d):
                if stop.is_set():
                    raise _Stopped()
                self.queue.put(("lframe", d))
            r = jobs.echo_live(link, self.conf, self.model["scale"], kf, src["name"], source=src["kind"], crop=crop,
                               model=self.model["index"], max_frames=src["frames"], every=1,
                               out_flags=jobs.LIVE_FRAMES112 | jobs.LIVE_FRAME, v4l2=(640, 480, src["fps"]),
                               on_live=on_live, on_progress=lambda p: self.queue.put(("prog", p)), heart=heart,
                               roll=roll)
            link.close()
            self.queue.put(("job", r, time.perf_counter() - t0))
        except _Stopped:
            link.close()
            self.queue.put(("err", "已停止：连接已断开，板上任务随之结束（现场模式中途停止没有最终结果）"))
        except Exception as e:
            link.close()
            self.queue.put(("err", "现场模式失败：%s" % e))

    def _stop(self):
        if self.stop is not None:
            self.stop.set()

    def _set_busy(self, busy, stoppable=False):
        self.busy = self.app.busy = busy
        self.btn_run.state(["disabled"] if busy else ["!disabled"])
        self.btn_stop.state(["!disabled"] if busy and stoppable else ["disabled"])
        if busy:
            self.btn_rep.state(["disabled"])

    def _on_prog(self, p):
        if p.get("stage") == "live":                       # 现场：板上滚动 EF（已按这套调度校准）
            if p.get("beats", 0) > 0 and p.get("ef", -1) >= 0:
                self.var_ef.set("%.0f%%" % p["ef"])
                self.var_hr.set(("%.0f 次/分" % p["hr"]) if p.get("hr") else "—")
                self.var_efnote.set("滚动 EF（板上每 0.25 s 更新）")
            if "key_frac" in p:
                self.var_k_pl.set("%.0f%%" % (100 * p["key_frac"]))
                self.var_k_lat.set("当前 %.0f ms" % p["lag_ms"])
                self.var_txt.set("现场：已分析 %d 帧（已采到 %d），关键帧 %.0f%%，当前延迟 %.0f ms"
                                 % (p["done"], p["captured"], 100 * p["key_frac"], p["lag_ms"]))
            return
        if p.get("stage") == "frames" and p.get("total"):
            self.pbar.configure(maximum=p["total"], value=p.get("done", 0))

    def _on_lframe(self, d):
        np = self.np
        t = d["t"]
        if t >= len(self.frames):
            return
        if d["frame"] is not None:
            self.frames[t] = np.frombuffer(d["frame"], np.uint8).reshape(N, N)
        self.masks[t] = np.unpackbits(np.frombuffer(d["mask"], np.uint8), bitorder="little")[:N * N].reshape(N, N) > 0
        self.vols.append(d["vol"])
        self.keyv.append(bool(d["key"]))
        s = d.get("stamps") or {}
        if "end" in s:
            self.lat.append((s["end"] - s["captured"]) / 1000.0)
            self.proc.append((s["end"] - s["start"]) / 1000.0)
        self.pbar.configure(value=len(self.vols))
        roll = (d.get("roll_light"), d.get("view", 0))
        hold = d.get("hold", False)
        if (roll != self.roll or hold != self.roll_hold) and (roll[0] is not None or roll[1] or self.roll_hold):
            self.roll_moved = self.roll_moved or (self.roll_hold and not hold)
            self.roll, self.roll_hold = roll, hold
            self._show_roll()
        now = time.perf_counter()
        if now - self.t_render > RENDER_GAP:             # 现场：画面只画最新一帧，按 RENDER_GAP 限速（数据每帧都收）
            self.t_render = now
            self._goto(t)
        if now - self.t_curve > 0.25:
            self.t_curve = now
            self._redraw()
            self._show_budget()
            self._draw_latency()

    def _show_roll(self):
        """现场扫查时（服务端 v5.25）：最近 1 s 的质量灯（每 0.2 s 更新）+ 切面（右室程序每 0.5 s 一次，最近 3 次中位数）。"""
        light, view = self.roll
        if light is not None:
            self._draw_light(dict(light=light))
            self.var_light.set(LIGHT_TXT[light])
        if self.roll_hold:                           # v5.26 CAMUS：灯定了、探头没动 -> 板上停了 F1 / 右室程序
            state = "已判定，探头未动，暂停判断"
        elif self.roll_moved and not view:
            state = "探头移动了，重新判断中"
        else:
            state = "扫查中"
        self.var_qwhy.set("%s（最近 1 s%s）：%s%s" % (
            state, "，" + self.roll_tag if self.roll_tag else "",
            {0: "画面可用", 1: "注意，可微调探头", 2: "画面差，建议调整探头"}.get(light, "质量灯攒满 1 s 后出"),
            ("；" + VIEW_TXT[view]) if view else ""))      # view = 0：没开切面判断（jobs.ROLL view_every=0）或还没出

    def _show_budget(self, captured=None):
        """每帧处理（处理线程从取帧到出掩膜 + 容积）的均值占帧间隔多少；设计报告 5.3 节：推荐档 26 %、每帧推理 121 %。"""
        if not self.proc or not self.fps:
            return
        gap = 1000.0 / self.fps
        m = sum(self.proc) / len(self.proc)
        self.var_k_proc.set("%.1f / %.0f ms（%.0f%%）" % (m, gap, 100 * m / gap))
        self.var_k_fps.set("%.0f fps · %d%s 帧" % (self.fps, len(self.proc), (" / %d" % captured) if captured else ""))

    def _on_job(self, r, wall):
        res = r["result"]
        self._set_busy(False)
        if not res.get("ok"):
            self._say("板上分析失败：%s" % res.get("error"))
            return
        np = self.np
        n = res["n_frames"]
        if res.get("mode") == 3:                            # 现场：换成板上裁好的 112 帧
            if "frames112" in r["blobs"]:
                self.frames = np.frombuffer(r["blobs"]["frames112"], np.uint8)[:n * N * N].reshape(n, N, N).copy()
            else:
                self.frames = self.frames[:n]
            self.fps = res["fps"]
            self.scale.configure(to=n - 1)
        self.res, self.raw, self.wall = res, r, wall
        self.vols = list(res["vols"])
        self.keyv = [c == "1" for c in res["key"]]
        if "masks" in r["blobs"]:
            mm = jobs.unpack_mask(r["blobs"]["masks"], (n, N, N))
            self.masks = {t: mm[t] for t in range(n)}
        self.orig = dict(masks={t: m.copy() for t, m in self.masks.items()}, vols=list(self.vols))
        self.ef_board = res["ef"]
        self.result = dict(ef=res["ef_raw"] if res["ef_raw"] is not None else float("nan"), fallback=res["fallback"],
                           hr=res["hr"], cycle_s=res["cycle_s"], beats=[dict(ed=b["ed"], es=b["es"], v_ed=b["v_ed"],
                                                                          v_es=b["v_es"], ef=b["ef"]) for b in res["beats"]])
        if "ef_sd" in res:
            self.result["ef_sd"] = res["ef_sd"]
        q = res["qc"]
        self.qc_level, self.qc_reasons = q["level"], self.ws.qc_reasons(q["why"], q, self.result)
        self.clinical, self.clinical_where = res.get("clinical"), "板上计算"
        self.ef_now = self.ef_board
        self._take_heart(r)
        k = res["keyframe"]
        self.stats = dict(n=n, wall=wall, ai_ms=k["pl_ms_per_key"])
        self._show_process()
        self.win = None
        self.var_winlen.set(WIN_LENS[1] if self._settled_from() is not None else WIN_LENS[0])
        self._win_len_changed(redraw=False)          # CAMUS 源默认「放好后」；其余全段（= 板上原始结果）
        self.btn_rep.state(["!disabled"] if n >= 3 else ["disabled"])
        self.var_fix.set("暂停在任意一帧，左键补漏、右键去多：FPGA 重算这一帧，后面各帧跟着重算到视频末尾")
        self._show_scale()
        if self.result["beats"] and not getattr(tk, "_IS_GTK_SHIM", False) and not self.playing:
            self.toggle_play()                       # 讲解时画面一直在动（点画面 / 曲线就停）；板上兼容层逐帧重绘太费 CPU，不自动播
        if self.result["beats"]:
            self._goto(self.result["beats"][len(self.result["beats"]) // 2]["es"])
        self._redraw()

    def _show_process(self):
        res, k = self.res, self.res["keyframe"]
        n = res["n_frames"]
        dur = n / self.fps
        self.var_k_sched.set("推荐档" if k["key_frac"] < 1 else "每帧推理")
        self.var_k_pl.set("%d / %d（%.1f%%）" % (k["n_key"], n, 100 * k["key_frac"]))
        self.var_k_plms.set("%.1f ms" % k["pl_ms_per_key"])
        self.var_k_trk.set("%.2f ms/帧" % k["track_ms_per_frame"] if k["key_frac"] < 1 else "—")
        self.var_k_total.set("%.2f s / %.2f s" % (res["ms"]["total"] / 1000, dur))
        self.pbar.configure(maximum=n, value=n)
        txt = ("%s：%d 帧，PL 推理 %d 帧（间隔触发 %d、残差触发 %d），其余帧在 PS 上块匹配跟踪；板上 %.2f s，视频本身 %.2f s"
               % ("现场模式（板上信号源）" if self.live else "关键帧模式（板上整病例）", n, k["n_key"],
                  k["reasons"]["interval"], k["reasons"]["residual"], res["ms"]["total"] / 1000, dur))
        if self.live and res.get("stream"):
            st = res["stream"]
            c, lat = st["crop"], st["latency_ms"]
            how = "自动找到" if c["auto"] else "固定"
            self.var_k_lat.set("%.0f / %.0f ms%s" % (lat["p50"], lat["p99"], "（含找画面 1 s）" if c["auto"] else ""))
            self.live_info = "现场（板上信号源 %s，%d×%d %.0f fps；画面方块%s (%d, %d) 边长 %d；%s）" % (
                st["name"], st["W"], st["H"], st["src_fps"], how, c["x"], c["y"], c["side"],
                "关键帧推荐档" if k["key_frac"] < 1 else "每帧推理")
            txt += "\n信号源 %s（%d×%d），画面方块%s (%d, %d) 边长 %d；延迟（采到 → 掩膜 + 容积）P50 %.0f ms、P99 %.0f ms、最大 %.0f ms" % (
                st["name"], st["W"], st["H"], how, c["x"], c["y"], c["side"], lat["p50"], lat["p99"], lat["max"])
            src_n = (self.src or {}).get("frames")
            self._show_budget(captured=src_n if src_n and src_n != n else None)
            if src_n == n:
                self.var_k_fps.set("%.0f fps · %d 帧不丢" % (self.fps, n))
            self._draw_latency()
        else:
            self.var_k_fps.set("%.0f / %.0f fps" % (self.fps, n / max(res["ms"]["total"] / 1000.0, 1e-6)))
        self._say(txt)

    def _take_heart(self, r):
        res = r["result"]
        self.heart, self.rh_frames = None, {}
        if "wall_motion" not in res and "right_heart" not in res:
            self.var_heart.set("CAMUS 模拟扫查源不做全心分析（半个心动周期往返播放，节段运动 / 右心比值不成立）"
                               if (res.get("rolling") or {}).get("chain", 0) in (1, 2) else "这次没有全心结果（要板端 v5.24）")
            self._draw_glyph(None)
            return
        self.heart = dict(wall_motion=res.get("wall_motion"), right_heart=res.get("right_heart"))
        rh = res.get("right_heart") or {}
        if "rh_masks" in r["blobs"] and rh.get("pairs"):
            for p, (rv_ed, rv_es, ra_es) in zip(rh["pairs"], jobs.unpack_rh(r["blobs"]["rh_masks"], len(rh["pairs"]))):
                self.rh_frames.setdefault(p["ed"], []).append((rv_ed, self.ws.RV_COLOR) if p["rv_ok_ed"] else None)
                self.rh_frames.setdefault(p["es"], []).extend(
                    [(rv_es, self.ws.RV_COLOR) if p["rv_ok_es"] else None, (ra_es, self.ws.RA_COLOR) if p["ra_ok"] else None])
        wm = self.heart["wall_motion"]
        if wm is None:
            self.var_wm.set("节段室壁运动：没有可用的心动周期")
            self.lab_wm.configure(fg=uistyle.C_MUTED)
        else:
            low = [self.ws.WM_NAMES[k] for k, f in enumerate(wm["seg"]) if f]
            self.var_wm.set(("提示运动减低：%s（建议复核）" % "、".join(low)) if low else "6 个节段均未达减低提示门限")
            self.lab_wm.configure(fg="#c2410c" if low or wm.get("flag") else uistyle.C_GREEN)
        self.var_rv.set("%.2f" % rh["rv_lv"] if rh.get("rv_lv") is not None else "—")
        self.var_ra.set("%.2f" % rh["ra_lv"] if rh.get("ra_lv") is not None else "—")
        self.var_heart.set("\n".join(self.ws.heart_text(res, self.hc_cfg)))
        self._draw_glyph(wm)

    # ================================================================ 结果卡片
    def _clear_results(self):
        self.var_ef.set("—")
        self.lab_ef.configure(fg=uistyle.C_TEXT)
        self.var_hr.set("—")
        self.var_efnote.set("")
        self.var_band.set("LVEF 分档：—")
        self.lab_band.configure(fg=uistyle.C_TEXT)
        self.var_review.set("")
        self.lab_review.configure(bg=uistyle.C_CARD)
        self.var_qwhy.set("")
        self.var_light.set("—")
        self.var_beats.set("")
        self.var_clin.set("")
        self.var_hint.set("")
        self.hints = None
        self.var_wm.set("节段室壁运动：—")
        self.lab_wm.configure(fg=uistyle.C_TEXT)
        self.var_rv.set("—")
        self.var_ra.set("—")
        self.var_heart.set("关键帧 / 现场模式跑完后显示（板端 v5.24）")
        for v in (self.var_k_sched, self.var_k_pl, self.var_k_plms, self.var_k_trk, self.var_k_total, self.var_k_lat,
                  self.var_k_proc, self.var_k_fps, self.var_k_fix):
            v.set("—")
        if self.live:
            self._draw_latency()
        self.var_fix.set("分析结束后可用（Ctrl+Z 撤销）")
        self.btn_rep.state(["disabled"])
        self._draw_interval()
        self._draw_light(None)
        self._draw_glyph(None)

    def _cal(self, e):
        """逐拍原始 EF -> 校准 EF（这套调度自己的 a / b，echo_kf_config.json，训练子集上拟合）。"""
        if self.kf_cfg:
            return self.kf_cfg["a"] * e + self.kf_cfg["b"]
        return self.ws.calibrated(e, self.conf, "full")

    def _show_results(self):
        ws, res, r = self.ws, self.res, self.result
        ef = self.ef_now
        band = ws.lvef_band(ef) if ef is not None else None
        fixed = bool(self.fixed)
        self.var_ef.set("%.0f%%" % ef if ef is not None else "—")
        self.lab_ef.configure(fg=BAND_COL[band] if band is not None else uistyle.C_TEXT)
        self.var_hr.set(("%.0f 次/分" % r["hr"]) if r.get("hr") else "—")
        if fixed:
            self.var_efnote.set("已人工纠错 %d 处：EF 由上位机按纠正后的容积重算（板上原始 %.0f%%）"
                                % (len(self.undo), self.ef_board))
        else:
            self.var_efnote.set("逐拍中位数，按本调度校准；%d 拍" % len(r["beats"]))
        if self.win is not None:
            a, b = self.win
            self.var_efnote.set("分析片段 %.1f–%.1f s：上位机按同一规则重算，%d 拍（全段板上 %.0f%%）%s" % (
                a / self.fps, b / self.fps, len(r["beats"]), self.ef_board if self.ef_board is not None else float("nan"),
                "；含人工纠错" if fixed else ""))
        iv = res.get("interval") if self.win is None and not self._no_beat_ef() else None
        # 80 % 区间是板上按全段、用 EchoNet 上校准的保形模型算的：片段不适用，CAMUS 源也不适用
        self._draw_interval(iv, ef)
        if band is not None:
            self.var_band.set("LVEF 分档：%s（ESC 2021）" % ws.LVEF_BANDS[band])
            self.lab_band.configure(fg=BAND_COL[band])
        else:
            self.var_band.set("LVEF 分档：未能测出")
        if iv:
            lo, hi = max(0.0, iv["lo80"]), min(100.0, iv["hi80"])
            cross = [b for b in (40, 50) if lo < b < hi]
            if iv["review"]:
                self.var_review.set("分型待定，建议复核：80%% 区间 %.0f–%.0f%% 跨过 %s 分界%s" % (
                    lo, hi, "、".join("%d%%" % b for b in cross), "（纠错前）" if fixed else ""))
                self.lab_review.configure(bg="#dc2626")
            else:
                self.var_review.set("80%% 区间 %.0f–%.0f%% 整体落在同一分档，无需按区间复核%s" % (
                    lo, hi, "（纠错前）" if fixed else ""))
                self.lab_review.configure(bg="#059669")
        else:
            self.var_review.set("")
            self.lab_review.configure(bg=uistyle.C_CARD)
        q = res.get("quality")
        ro = res.get("rolling")
        if ro and ro.get("chain", 0) in (1, 2) and ro.get("updates"):   # v5.26 CAMUS 源：整段的灯（程序 16 链）是 EchoNet 口径，不显示
            ls = [u[1] for u in ro["updates"]]
            vs = [v[3] for v in ro.get("views", [])]
            self._draw_light(dict(light=ls[-1]))
            self.var_light.set(LIGHT_TXT[ls[-1]])
            note = ("EF：F1（CAMUS 训练）分割 + 板上跟踪，CAMUS 训练集校准（验证集误差约 6 个百分点，单切面）；"
                    "片段只有半个心动周期、往返播放，不做全心分析" if ro.get("chain") == 2 else
                    "CAMUS 片段只有半个心动周期、往返播放：这个源只演示质量灯和切面，EF 没有临床意义，不做全心分析")
            self.var_qwhy.set("质量灯（CAMUS 专家等级标定，最后 1 s）：%s。扫查过程（每 %.1f s 一次）：绿 %d / 黄 %d / 红 %d 次%s\n%s" % (
                {0: "画面可用", 1: "注意，可微调探头", 2: "画面差，建议调整探头"}[ls[-1]], ro["every"] / (self.fps or 50.0),
                ls.count(0), ls.count(1), ls.count(2),
                ("；切面 %d 次判断里 %d 次是心尖四腔" % (len(vs), vs.count(2))) if vs else "", note))
            ho = ro.get("hold")
            if ho:                                       # 判定后保持：停了多少帧的 F1 / 右室程序，探头动了几次
                ev = ho.get("events", [])
                self.var_qwhy.set(self.var_qwhy.get() + "\n判定后保持：%d / %d 帧没再跑 F1（%.0f%%），判定 %d 次、探头移动后重判 %d 次" % (
                    ho["held_frames"], res["n_frames"], 100.0 * ho["held_frames"] / max(res["n_frames"], 1),
                    sum(1 for e_ in ev if e_[1] == 1), sum(1 for e_ in ev if e_[1] == 0)))
            q = None
        else:
            self._draw_light(q)
        if q:
            why = [ws.QUALITY_REASON[k] for k in q.get("reasons", []) if k in ws.QUALITY_REASON] if q["light"] > 0 else []
            self.var_light.set(LIGHT_TXT[q["light"]])
            self.var_qwhy.set("质量灯原因：%s（零额外模型，由分割输出和画面算出；只作提示，不参与复核判断）"
                              % ("；".join(why) if why else "无") + ("（纠错前）" if fixed else ""))
            if ro and ro.get("updates"):                 # 现场 v5.25：扫查过程中的滚动灯 / 切面
                ls = [u[1] for u in ro["updates"]]
                vs = [v[3] for v in ro.get("views", [])]
                self.var_qwhy.set(self.var_qwhy.get() + "\n扫查过程（每 0.2 s 一次，最近 1 s）：绿 %d / 黄 %d / 红 %d 次%s" % (
                    ls.count(0), ls.count(1), ls.count(2),
                    ("；切面 %d 次判断里 %d 次是心尖四腔" % (len(vs), vs.count(2))) if vs else ""))
        elif not (ro and ro.get("chain", 0) in (1, 2)):
            self.var_light.set("—")
            self.var_qwhy.set("")
        lines = []
        for j, b in enumerate(r["beats"]):
            lines.append("第 %d 拍  ED %3d → ES %3d   EF %.0f%%" % (j + 1, b["ed"], b["es"], self._cal(b["ef"])))
        if self.qc_reasons:
            lines.append("质控：" + "；".join(self.qc_reasons))
        self.var_beats.set("\n".join(lines) if lines else "没有找到完整心动周期")
        if self.clinical is not None and ef is not None:
            self.var_clin.set("（%s）\n" % self.clinical_where + "\n".join(ws.clinical_text(self.clinical, ef)))
        else:
            self.var_clin.set("")
        import clinical_hints as ch
        heart = self.heart or {}
        self.hints = ch.echo_hints(ef, bool(iv and iv["review"]), r.get("hr"), bool((self.clinical or {}).get("irregular")),
                                   heart.get("wall_motion"), heart.get("right_heart"), (q or {}).get("light"), ws.WM_NAMES)
        if (res.get("rolling") or {}).get("chain", 0) in (1, 2):   # v5.26 CAMUS 源：EF 无临床意义，不给提示方向
            self.hints = []
        # 区间跨界那条上面红条已经写了，框里不重复（报告里照常列）
        self.var_hint.set(ch.text([h for h in self.hints if "区间跨过" not in h[1]]))

    def _draw_interval(self, iv=None, ef=None):
        """80 % / 90 % 区间条：横轴 EF 10–90 %，底色按 LVEF 三档，竖线 = 40 / 50 分界，黑三角 = EF。"""
        c = self.cv_iv
        c.delete("all")
        w = int(c.cget("width"))
        x0, x1, y0, y1 = 8, w - 8, 14, 38
        X = lambda v: x0 + (x1 - x0) * (min(max(v, 10.0), 90.0) - 10.0) / 80.0
        for (a, b_), bg in zip(((10, 40), (40, 50), (50, 90)), BAND_BG):
            c.create_rectangle(X(a), y0, X(b_), y1, fill=bg, outline="")
        for v in (40, 50):
            c.create_line(X(v), y0 - 4, X(v), y1 + 4, fill="#555", dash=(3, 2))
        for v in (10, 20, 30, 40, 50, 60, 70, 80, 90):
            c.create_text(X(v), y1 + 12, text="%d" % v, fill="#777", font=("Consolas", 8))
        c.create_text(x0, 5, text="EF 区间（%）", anchor="w", fill="#777", font=("Microsoft YaHei UI", 8))
        if not iv or ef is None:
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                          text="分析后显示 80% 区间" if ef is None else
                          "80% 区间模型在 EchoNet 上校准，CAMUS 源不给" if self._no_beat_ef() else
                          "80% 区间按全段估计，所选片段不给" if self.win is not None else "分析后显示 80% 区间", fill="#999",
                          font=("Microsoft YaHei UI", 9))
            return
        ym = (y0 + y1) / 2
        c.create_line(X(iv["lo90"]), ym, X(iv["hi90"]), ym, fill="#1e3a8a", width=2)
        c.create_rectangle(X(iv["lo80"]), ym - 6, X(iv["hi80"]), ym + 6, fill="#2563eb", outline="#1e3a8a")
        c.create_polygon(X(ef), y0 - 1, X(ef) - 6, y0 - 9, X(ef) + 6, y0 - 9, fill="#111")
        c.create_text(x1, 5, text="■ 80%%  ─ 90%%%s" % ("" if not self.fixed else "（纠错前）"), anchor="e",
                      fill="#555", font=("Microsoft YaHei UI", 8))

    def _draw_latency(self):
        """现场模式逐帧延迟（采到 → 掩膜 + 容积，板上时间戳）：蓝 = 本次，灰 = 上一次（例如每帧推理）；
        虚线 = 帧间隔与 80 ms 最坏目标；0–100 ms 占下面 55 % 高度，100 ms 以上压缩（每帧推理会涨到几百 ms）。"""
        if not self.live:
            return
        c = self.cv_lat
        c.delete("all")
        w, h = max(c.winfo_width(), 200), LAT_H
        x0, x1, yb, yt = 46, w - 8, h - 14, 16
        # 灰线只和同一个信号源比（推荐档 vs 每帧推理）；换了源就不画，免得拿 50 fps 的曲线去比 30 fps 的
        prev = self.prev_run if self.prev_run and self.prev_run.get("src") == (self.src or {}).get("name") else None
        runs = [r["lat"] for r in ([prev] if prev else [])] + [self.lat]
        top = max([100.0] + [max(r) for r in runs if r])
        split = yb - 0.55 * (yb - yt)

        def Y(v):
            if v <= 100 or top <= 100:
                return yb - (yb - split) * min(v, 100.0) / 100.0
            return split - (split - yt) * (v - 100.0) / (top - 100.0)
        n = max([len(self.frames) if self.frames is not None else 0] + [len(r) for r in runs] + [2])
        X = lambda t: x0 + (x1 - x0) * t / max(n - 1, 1)
        c.create_rectangle(x0, yt, x1, yb, outline="#ddd")
        gap = 1000.0 / self.fps if self.fps else 20.0
        for v, txt, col in ((gap, "帧间隔 %.0f" % gap, "#059669"), (80.0, "80 ms 目标", "#dc2626")):
            c.create_line(x0, Y(v), x1, Y(v), fill=col, dash=(4, 3))
            c.create_text(x1 - 2, Y(v) - 7, text=txt, anchor="e", fill=col, font=("Microsoft YaHei UI", 7))
        for v in ([0, 50, 100] + ([int(top)] if top > 100 else [])):
            c.create_text(x0 - 4, Y(v), text="%d" % v, anchor="e", fill="#777", font=("Consolas", 8))
        if prev and len(prev["lat"]) >= 2:
            pts = []
            for t, v in enumerate(prev["lat"]):
                pts += [X(t), Y(v)]
            c.create_line(*pts, fill="#a8b0bc", width=2)
        if len(self.lat) >= 2:
            pts = []
            for t, v in enumerate(self.lat):
                pts += [X(t), Y(v)]
            c.create_line(*pts, fill="#1e5ac8", width=2)
        for t, k in enumerate(self.keyv[:len(self.lat)]):
            if k:
                c.create_line(X(t), yb, X(t), yb + 5, fill="#2864c8", width=2)
        s = "延迟（ms，采到 → 掩膜 + 容积，板上时间戳）　蓝：本次"
        if self.res is not None and self.res.get("stream"):
            lat = self.res["stream"]["latency_ms"]
            s += " P50 %.0f / P99 %.0f" % (lat["p50"], lat["p99"])
        if prev:
            s += "　灰：上次 %s P50 %.0f / P99 %.0f" % (prev["label"], prev["p50"], prev["p99"])
        c.create_text(x0, 7, text=s, anchor="w", fill="#444", font=("Microsoft YaHei UI", 8))
        c.create_text(4, yb + 6, text="关键帧", anchor="w", fill="#2864c8", font=("Microsoft YaHei UI", 7))

    def _draw_light(self, q):
        c = self.cv_light
        c.delete("all")
        col = LIGHT_COL.get(q["light"]) if q else "#d1d5db"
        c.create_oval(2, 2, 20, 20, fill=col, outline="#666")

    def _draw_glyph(self, wm):
        from PIL import Image, ImageTk
        im = Image.new("RGB", (76, 96), (255, 255, 255))
        if wm:
            self.ws.draw_wm_glyph(im, wm["seg"], 6, 6)
        else:
            self.ws.draw_wm_glyph(im, [0] * 6, 6, 6)
            im = Image.blend(im, Image.new("RGB", im.size, (255, 255, 255)), 0.65)
        self.photos["glyph"] = ImageTk.PhotoImage(im)
        self.lab_glyph.configure(image=self.photos["glyph"])

    # ================================================================ 视频 / 曲线 / 时间线
    def _goto(self, t, from_scale=False):
        if self.frames is None:
            return
        from PIL import Image, ImageDraw, ImageTk
        np = self.np
        t = max(0, min(len(self.frames) - 1, int(t)))
        self.cur = t
        if not from_scale:
            self._scale_busy = True
            try:
                self.scale.set(t)
            finally:
                self._scale_busy = False
        m = self.masks.get(t)
        rgb = self.ws.contour_rgb(self.frames[t], m if m is not None else np.zeros((N, N), bool), VIEW // N)
        for item in self.rh_frames.get(t, []):
            if item is not None:
                self.ws.add_contour(rgb, item[0], VIEW // N, item[1])
        im = Image.fromarray(rgb)
        wm = (self.heart or {}).get("wall_motion")
        if wm:
            self.ws.draw_wm_glyph(im, wm["seg"], VIEW - 74, 6)
        tag = self._phase(t)
        d = ImageDraw.Draw(im)
        for x, y, pos in self.clicks.get(t, []):
            col = (0, 230, 90) if pos else (255, 60, 60)
            d.ellipse((x - 6, y - 6, x + 6, y + 6), outline=col, width=2)
            d.line((x - 4, y, x + 4, y), fill=col, width=2)
            if pos:
                d.line((x, y - 4, x, y + 4), fill=col, width=2)
        self.photos["v"] = ImageTk.PhotoImage(im)
        self.cv_v.itemconfigure(self.cv_img, image=self.photos["v"])
        self.cv_v.delete("tag")
        self.cv_v.delete("hint")
        if tag:
            self.cv_v.create_text(12, 10, text=tag, anchor="nw", font=FONT_TAG, tags="tag",
                                  fill="#ff6060" if tag == "ED" else "#5aa0ff")
        kind = ""
        if self.keyv and t < len(self.keyv):
            kind = "关键帧（PL 分割）" if self.keyv[t] else "跟踪帧（PS 块匹配）"
        if t in self.fixed:
            kind = "人工纠错（FPGA 重算）" if self.fixed[t] == "fix" else "纠错后跟着重算（FPGA）"
        self.var_ft.set("第 %d / %d 帧  %.2f s  %s %s" % (t + 1, len(self.frames), t / self.fps, tag, kind))
        self._draw_cursor()

    def _phase(self, t):
        if self.result:
            for b in self.result["beats"]:
                if b["ed"] == t:
                    return "ED"
                if b["es"] == t:
                    return "ES"
        return ""

    def _span(self):
        """曲线 / 时间线显示的帧范围 [a, b)：选了分析片段就自动放大到片段（11-03），否则全段。"""
        n = len(self.frames) if self.frames is not None else 0
        return self.win if self.win is not None else (0, n)

    def _xy(self):
        a, b = self._span()
        x = lambda t: PLOT_L + (CW - PLOT_L - 10) * (t - a) / max(b - 1 - a, 1)
        vs = self.vols[a:b] or self.vols or [0.0, 1.0]
        lo, hi = min(vs), max(vs)
        top, bot = 22, PLOT_B - 24
        y = lambda v: bot - (bot - top) * (v - lo) / max(hi - lo, 1e-9)
        return x, y

    def _redraw(self):
        self._draw_curve()
        self._draw_timeline()

    def _draw_curve(self):
        c = self.cv
        c.delete("all")
        c.create_text(4, 14, text="容积", anchor="w", fill="#777", font=("Microsoft YaHei UI", 8))
        if self.frames is None or (self.live and not self.vols):
            c.create_text(CW / 2, CH / 2, text="分析时逐帧画出左心室容积（单平面圆盘法，像素单位）\n"
                                               "▲ 舒张末 ED　▼ 收缩末 ES，每拍下面标 EF", fill="#999",
                          font=("Microsoft YaHei UI", 9), justify="center")
            if self.frames is None:
                return
        x, y = self._xy()
        a, b = self._span()
        c.create_rectangle(PLOT_L, 8, CW - 10, PLOT_B, outline="#ddd")
        step = 0.5 if (b - a) / self.fps < 5 else 1.0     # 放大到片段时半秒一个刻度
        s = math.ceil(a / self.fps / step - 1e-9) * step
        while s * self.fps <= b - 1 + 1e-9:
            xs = x(s * self.fps)
            c.create_line(xs, PLOT_B, xs, PLOT_B + 4, fill="#999")
            c.create_text(xs, CH - 9, text="%g s" % s, fill="#777", font=("Consolas", 8))
            s += step
        if self.win is not None:                         # 放大中：标出是哪一段
            c.create_text(CW - 12, 14, anchor="e", fill="#1e5ac8", font=("Microsoft YaHei UI", 8),
                          text="已放大到分析片段 %.1f–%.1f s（全段 %.1f s）" % (a / self.fps, b / self.fps,
                                                                       len(self.frames) / self.fps))
        if self.orig and self.fixed:                     # 纠错前的曲线（灰）
            pts = []
            for t in range(a, min(b, len(self.orig["vols"]))):
                pts += [x(t), y(self.orig["vols"][t])]
            if len(pts) >= 4:
                c.create_line(*pts, fill="#c0c7d2", width=1, dash=(4, 2))
        if len(self.vols) >= 2:
            pts = []
            for t in range(a, min(b, len(self.vols))):
                pts += [x(t), y(self.vols[t])]
            if len(pts) >= 4:
                c.create_line(*pts, fill="#1e5ac8", width=2)
        for t in self.fixed:
            if a <= t < min(b, len(self.vols)):
                c.create_oval(x(t) - 3, y(self.vols[t]) - 3, x(t) + 3, y(self.vols[t]) + 3, fill="#f59e0b", outline="")
        if self.result:
            for b in self.result["beats"]:
                xe, ye, xs_, ys = x(b["ed"]), y(b["v_ed"]), x(b["es"]), y(b["v_es"])
                c.create_polygon(xe, ye - 10, xe - 6, ye - 2, xe + 6, ye - 2, fill="#d83232")
                c.create_polygon(xs_, ys + 10, xs_ - 6, ys + 2, xs_ + 6, ys + 2, fill="#2878dc")
                if not self._no_beat_ef():
                    c.create_text(xs_, ys + 18, text="%.0f%%" % self._cal(b["ef"]), fill="#333", font=("Consolas", 9))
        self._draw_cursor()

    def _draw_cursor(self):
        if self.frames is None:
            return
        x, _ = self._xy()
        a, b = self._span()
        for cv, y1 in ((self.cv, PLOT_B), (self.tl, TH - 20)):
            cv.delete("cursor")
            if a <= self.cur < b:                        # 放大到片段时，片段外的帧不画光标
                cv.create_line(x(self.cur), 6, x(self.cur), y1, fill="#e0a000", width=2, tags="cursor")

    def _draw_timeline(self):
        c = self.tl
        c.delete("all")
        for yy, txt in ((15, "心拍"), (40, "关键帧"), (62, "纠错")):
            c.create_text(4, yy, text=txt, anchor="w", fill="#555", font=("Microsoft YaHei UI", 8))
        if self.frames is None or not self.keyv:
            c.create_text(56, 40, anchor="w", fill="#999", font=("Microsoft YaHei UI", 9),
                          text="分析时分开标出：蓝 = PL 分割的关键帧（约 18%），灰 = PS 跟踪帧")
        if self.frames is None:
            return
        x, _ = self._xy()
        if self.result:
            for j, b in enumerate(self.result["beats"]):
                c.create_rectangle(x(b["ed"]), 6, x(b["es"]), 24, fill="#f4c7c7" if j % 2 == 0 else "#c7d8f4", outline="")
                if not self._no_beat_ef():
                    c.create_text((x(b["ed"]) + x(b["es"])) / 2, 15, text="%.0f%%" % self._cal(b["ef"]), font=("Consolas", 8))
        a, b = self._span()
        for t in range(a, min(b, len(self.keyv))):
            k = self.keyv[t]
            c.create_line(x(t), 31 if k else 37, x(t), 49 if k else 43, fill="#2864c8" if k else "#b8bec8",
                          width=2 if k else 1)
        for t, kind in self.fixed.items():
            if not a <= t < b:
                continue
            c.create_line(x(t), 55, x(t), 69, fill="#f59e0b" if kind == "fix" else "#fcd34d", width=3 if kind == "fix" else 2)
        c.create_text(CW - 4, TH - 9, text="蓝 关键帧（PL 分割）　灰 跟踪帧（PS 块匹配）　橙 人工纠错 / 浅橙 跟着重算",
                      anchor="e", fill="#777", font=("Microsoft YaHei UI", 8))
        self._draw_cursor()

    def _curve_click(self, px):
        if self.frames is None:
            return
        self._pause()
        a, b = self._span()
        f = min(1.0, max(0.0, (px - PLOT_L) / max(CW - PLOT_L - 10, 1)))
        self._goto(a + int(round(f * (b - 1 - a))))

    # ---- 播放 ----
    def toggle_play(self):
        if self.frames is None or (self.busy and self.live):
            return
        self.playing = not self.playing
        self.btn_play.configure(text="❚❚ 暂停" if self.playing else "▶ 播放")
        if self.playing:
            self._tick()

    def _pause(self):
        if self.playing:
            self.playing = False
            self.btn_play.configure(text="▶ 播放")

    def _tick(self):
        if not self.playing or self.frames is None:
            return
        a, b = self._span()
        t = self.cur + 1
        self._goto(t if a <= t < b else a)               # 选了分析片段：只在片段里循环
        self.frame.after(max(15, int(1000 / (self.fps or 30))), self._tick)

    def step(self, d):
        if self.frames is None:
            return
        self._pause()
        self._goto(self.cur + d)

    def key(self, name):
        """主窗口转来的快捷键。"""
        if name == "space":
            self.toggle_play()
        elif name == "left":
            self.step(-1)
        elif name == "right":
            self.step(1)
        elif name == "undo":
            self.undo_fix()

    # ================================================================ 点击纠错（7.5 节）
    def _click(self, e, positive):
        if self.res is None or self.frames is None:
            self.var_fix.set("先分析一段视频，再在帧上点击纠错")
            return
        if self.busy:
            self.var_fix.set("板子正忙，稍等")
            return
        if self.playing:
            self._pause()
            self.var_fix.set("已暂停；在这一帧上左键补漏、右键去多")
            return
        if self.m_inter is None or self.m_seq is None:
            self.var_fix.set("模型表里没有 echo_inter / echo_seq（上位机太旧）")
            return
        if self.app.busy:
            self.var_fix.set("板子正忙，稍等")
            return
        t = self.cur
        x, y = min(max(e.x, 0), VIEW - 1), min(max(e.y, 0), VIEW - 1)
        clicks = self.clicks.get(t, []) + [(x, y, positive)]
        n = len(self.frames)
        end = t + 1                                        # 往后一直传到视频末尾；碰到医生纠过的帧就停（保留那次纠正）
        while end < n and self.fixed.get(end) != "fix":
            end += 1
        prior = self.masks.get(t)
        if prior is None:
            prior = self.np.zeros((N, N), bool)
        self._set_busy(True)
        self.var_fix.set("FPGA 重算第 %d 帧 …" % (t + 1))
        threading.Thread(target=self._fix_worker, args=(self._hostport(), t, end, prior, clicks), daemon=True).start()

    def _payload(self, t, prior, clicks=None):
        """128 档 RGB：R = 本帧（补边到 128），G = 先验掩膜 0 / 255，B = 点击锥形（128 = 无点击）。"""
        np = self.np
        rgb = np.zeros((128, 128, 3), np.uint8)
        rgb[PAD:PAD + N, PAD:PAD + N, 0] = self.frames[t]
        rgb[PAD:PAD + N, PAD:PAD + N, 1] = np.where(prior, 255, 0)
        if clicks:                                         # 448 视图坐标 -> 128 档 -> 512 画布（click_plane128 的口径）
            pl = sequence.click_plane128([((x / 4.0 + PAD) * 4, (y / 4.0 + PAD) * 4, p) for x, y, p in clicks])
            rgb[..., 2] = np.frombuffer(pl, np.uint8).reshape(128, 128)
        else:
            rgb[..., 2] = 128
        return rgb.tobytes()

    def _fix_worker(self, hp, t, end, prior, clicks):
        np, ws = self.np, self.ws
        link = self.app.link
        flags = proto.FLAG_STATS | proto.FLAG_C_LOGIT
        out = {}
        try:
            link.host, link.port = hp
            link.connect()
            mi, mq = self.m_inter, self.m_seq
            t0 = time.perf_counter()
            for k in range(t, end):
                model = mi if k == t else mq
                pl = self._payload(k, prior if k == t else out[k - 1], clicks if k == t else None)
                st, logit, _, _ = link.v6c_round_trip(pl, 128, kind=proto.V6C_KIND_RGB, model0=model["index"],
                                                      thr=model["thr"], flags=flags)
                if st is None or st.get("model") != model["index"] or logit is None:
                    raise RuntimeError("板端没按 %d 号模型回结果" % model["index"])
                _, raw = ws.mask_from_logit(logit)
                m, _ = ws.clean(raw) if raw.any() else (raw, 0)
                out[k] = m
                if k == t:
                    ai_ms = st.get("ai_us", 0) / 1000.0
                elif (k - t) % 10 == 0:
                    self.queue.put(("fix_prog", "第 %d 帧已重算（FPGA %.1f ms），后面各帧跟着重算 %d / %d …"
                                    % (t + 1, ai_ms, k - t, end - t - 1)))
            link.close()
            self.queue.put(("fixed", t, clicks, out, ai_ms, (time.perf_counter() - t0) * 1000.0))
        except Exception as e:
            link.close()
            self.queue.put(("fix_err", "纠错失败：%s" % e))

    def _on_fixed(self, t, clicks, out, ai_ms, wall_ms):
        self._set_busy(False)
        self.btn_rep.state(["!disabled"])
        before = dict(t=t, clicks=list(self.clicks.get(t, [])), masks={k: self.masks.get(k) for k in out},
                      vols={k: self.vols[k] for k in out}, fixed={k: self.fixed.get(k) for k in out})
        self.undo.append(before)
        self.clicks[t] = clicks
        for k, m in out.items():
            self.masks[k] = m
            self.vols[k] = self.ws.volume_pca(m)
            self.fixed[k] = "fix" if k == t else "prop"
        self.fix_ms.append(ai_ms)
        self._reanalyze()
        last = max(out)
        if len(out) == 1:
            how = "这是最后一帧，后面没有帧" if t == len(self.frames) - 1 else "下一帧是人工纠过的帧，保留那次纠正"
        elif last == len(self.frames) - 1:
            how = "后面 %d 帧跟着重算到视频末尾" % (len(out) - 1)
        else:
            how = "后面 %d 帧跟着重算（第 %d 帧是人工纠过的，保留）" % (len(out) - 1, last + 2)
        self.var_fix.set("第 %d 帧：%d 个点击，FPGA 整网 %.1f ms；%s，共 %.0f ms。可撤销 / 重置"
                         % (t + 1, len(clicks), ai_ms, how, wall_ms))
        self._goto(t)
        self._redraw()

    # ---- 分析片段（11-02）：只拿一段（如「放好后」）算 EF / 逐拍 / 临床测量，滚动条拖动选段 ----
    def _settled_from(self):
        """现场 v5.26 CAMUS 源：最后一次判定锁定（= 探头放好、灯定下来）的帧；没有则 None。"""
        ho = ((self.res or {}).get("rolling") or {}).get("hold") or {}
        locks = [e[0] for e in ho.get("events", []) if e[1] == 1]
        n = len(self.vols)
        return locks[-1] if locks and n - locks[-1] >= int(self.fps or 50) else None   # 至少 1 s 才算一段

    def _win_len_changed(self, redraw=True):
        if self.res is None or not self.vols:
            return
        n, sel = len(self.vols), self.var_winlen.get()
        if sel == WIN_LENS[0]:
            self.win = None
        elif sel == WIN_LENS[1]:
            a = self._settled_from()
            self.win = (a, n) if a is not None else None
            if a is None:
                self.var_winlen.set(WIN_LENS[0])
        else:
            L = min(n, int(round(float(sel.split()[0]) * self.fps)))
            a = self.win[0] if self.win else n - L     # 换长度时保留起点，第一次放在最后
            a = max(0, min(a, n - L))
            self.win = (a, a + L)
        self._apply_win(redraw)

    def _win_cmd(self, *args):
        """滚动条拖动 / 点两侧：平移分析片段（长度不变）。"""
        if self.res is None or not self.vols or self.busy:
            return
        n = len(self.vols)
        a, b = self.win if self.win else (0, n)
        L = b - a
        if args[0] == "moveto":
            a = int(round(float(args[1]) * n))
        elif args[0] == "scroll":
            a += int(args[1]) * (L if args[2] == "pages" else max(1, int(self.fps / 4)))
        a = max(0, min(a, n - L))
        if L >= n:
            self.win = None
        else:
            self.win = (a, a + L)
            if self.var_winlen.get() == WIN_LENS[1] and a != self._settled_from():
                self.var_winlen.set("%.1f s" % (L / self.fps))   # 拖离「放好后」的起点：长度保留，显示成秒数
        self._apply_win(True)

    def _apply_win(self, redraw):
        n = len(self.vols)
        a, b = self.win if self.win else (0, n)
        self.sb_win.set(a / max(n, 1), b / max(n, 1))
        self.var_win.set("全段 %.1f s" % (n / self.fps) if self.win is None else
                         "%.1f–%.1f s（%d 帧）" % (a / self.fps, b / self.fps, b - a))
        self._reanalyze()
        if self.frames is not None and not a <= self.cur < b:
            self._goto(a)                                # 光标挪进片段（放大后的曲线只画片段）
        if redraw:
            self._redraw()

    def _no_beat_ef(self):
        """v5.26 CAMUS 源：曲线 / 时间线上不标逐拍 EF（半个心动周期往返播放，逐拍 EF 没有意义）。"""
        return ((self.res or {}).get("rolling") or {}).get("chain", 0) in (1, 2)

    def _reanalyze(self):
        """纠错后 / 选了分析片段：逐拍分析与临床测量在上位机按当前容积重算（规则与板上相同，echo_ws.analyze /
        clinical；片段内的帧号换回原视频帧号）；没有纠错也没选片段时回到板上原始结果。"""
        ws, res = self.ws, self.res
        if not self.fixed and self.win is None:
            self.result = dict(ef=res["ef_raw"] if res["ef_raw"] is not None else float("nan"), fallback=res["fallback"],
                               hr=res["hr"], cycle_s=res["cycle_s"], beats=[dict(ed=b["ed"], es=b["es"], v_ed=b["v_ed"],
                                                                              v_es=b["v_es"], ef=b["ef"]) for b in res["beats"]])
            self.ef_now = self.ef_board
            self.clinical, self.clinical_where = res.get("clinical"), "板上计算"
        else:
            a, b = self.win if self.win else (0, len(self.vols))
            r = ws.analyze(self.vols[a:b], self.fps, 1, self.conf["cfg"])
            for bt in r["beats"]:
                bt["ed"] += a
                bt["es"] += a
            self.result = r
            self.ef_now = self._cal(r["ef"]) if r["ef"] == r["ef"] else None
            self.clinical = ws.clinical(r["beats"], self.fps, self.ef_now, self._cal, ws.clinical_conf(self.conf, "full")) \
                if self.ef_now is not None else None
            self.clinical_where = "上位机按同一规则重算：%s" % "，".join(
                ([("所选片段 %.1f–%.1f s" % (a / self.fps, b / self.fps))] if self.win else []) + (["纠错后"] if self.fixed else []))
        self.var_k_fix.set(("%d 处" % len(self.undo)) if self.undo else "—")
        self._show_results()

    def undo_fix(self):
        if self.busy or not self.undo:
            return
        b = self.undo.pop()
        for k, m in b["masks"].items():
            if m is None:
                self.masks.pop(k, None)
            else:
                self.masks[k] = m
            self.vols[k] = b["vols"][k]
            if b["fixed"][k] is None:
                self.fixed.pop(k, None)
            else:
                self.fixed[k] = b["fixed"][k]
        if b["clicks"]:
            self.clicks[b["t"]] = b["clicks"]
        else:
            self.clicks.pop(b["t"], None)
        self._reanalyze()
        self.var_fix.set("已撤销第 %d 帧的最后一次点击（还剩 %d 步可撤销）" % (b["t"] + 1, len(self.undo)))
        self._goto(b["t"])
        self._redraw()

    def reset_fix(self):
        if self.busy or self.orig is None or not self.undo:
            return
        self.masks = {t: m.copy() for t, m in self.orig["masks"].items()}
        self.vols = list(self.orig["vols"])
        self.clicks, self.undo, self.fixed = {}, [], {}
        self._reanalyze()
        self.var_fix.set("已重置：回到板上原始结果")
        self._goto(self.cur)
        self._redraw()

    # ================================================================ 标尺定标 + 送到快照测量（三个模式打通）
    def _raw_key(self):
        """定标结果按原始录屏的文件名记在主窗口里（关键帧 / 现场两个视图共用：同一段录屏只定一次）。"""
        if self.live:
            return (self.src or {}).get("video")
        return os.path.basename(self.rawvid["path"]) if self.rawvid else None

    def _spacing(self):
        """112 尺度上每像素多少毫米：原始画面 mm/像素 × 扇区方块边长 / 112（现场模式的 .y8 若放大过再除以放大倍数）。"""
        mm = self.app.calib.get(self._raw_key() or "")
        if mm is None:
            return None
        if self.live:
            st = (self.res or {}).get("stream") or {}
            side = (st.get("crop") or {}).get("side") or ((self.src or {}).get("truth") or (0, 0, 0))[2]
            return mm / float((self.src or {}).get("zoom") or 1.0) * side / N if side else None
        return mm * self.rawvid["crop"]["side"] / N

    def _show_scale(self):
        s = self._spacing()
        self.btn_scale.configure(text=("标尺 %.2f mm/像素" % s) if s else "标尺定标…")

    def _calibrate(self, then=None, auto=False):
        """auto=True（送到快照时还没定标）：先自动读屏上深度刻度，读出来就直接定标、不弹框；读不出才弹框手点。
        点「标尺定标…」按钮（auto=False）：照旧弹框，框里已自动放好两点，可核对 / 重点。"""
        import numpy as np
        if self.live:
            p = raw_video_path((self.src or {}).get("video"))
            if p is None:
                self._say("这个信号源没有带深度标尺的原始录屏（EchoNet 视频已缩成 112×112），不能定标")
                return
            import cv2
            cap = cv2.VideoCapture(p)
            ok, fr = cap.read()
            cap.release()
            if not ok:
                self._say("读不出原始录屏 %s" % p)
                return
            frame, title = fr, os.path.basename(p)
        else:
            if self.rawvid is None:
                self._say("先打开一段超声机录屏（如 HMC-QU）；EchoNet 视频已缩成 112×112，没有深度标尺，不能定标")
                return
            frame, title = self.rawvid["frame"], os.path.basename(self.rawvid["path"])

        def done(mm_px):
            self.app.calib[self._raw_key()] = mm_px
            for v in self.app.views.values():         # 另一个视图开着同一段录屏时也用上
                if hasattr(v, "_show_scale"):
                    v._show_scale()
            s = self._spacing()
            self._say("标尺定标：原始画面 %.3f mm/像素%s" % (mm_px, ("，112 尺度 %.2f mm/像素" % s) if s else
                                                         "（现场模式跑完一次后按板上画面方块换算到 112 尺度）"))
            if then is not None:
                then()
        if auto:
            try:
                tk_ = depth_ticks(np.asarray(frame))
            except Exception:
                tk_ = None
            if tk_ is not None:
                (x0, y0), (x1, y1), cm, info = tk_
                self.calib_auto = info
                self._say("已自动读出屏上深度刻度：%g cm = %.1f 个原始像素（%d 个刻度，1 cm = %.1f 像素）" % (
                    cm, info["px_5cm"], info["ticks"], info["period"]))
                done(cm * 10.0 / (((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5))
                return None
        self.calib_dlg = CalibDialog(self.frame, np.asarray(frame), title, done)
        return self.calib_dlg

    def _send_snapshot(self):
        """把中位那一拍的 ED / ES 帧和板上的左室掩膜（含人工纠错）交给快照模式，按标尺做单平面 Simpson（mL）。"""
        if self.res is None or not self.result or not self.result["beats"] or self.busy:
            self._say("先分析出至少一个完整心动周期，再送到快照测量")
            return
        s = self._spacing()
        if s is None:
            can = (self.live and raw_video_path((self.src or {}).get("video"))) or (not self.live and self.rawvid)
            if can:                                  # 还没定标：先自动读刻度直接定标并接着送；读不出才弹框手点
                self._say("先定标：读原始录屏上的深度刻度 …")
                self._calibrate(then=self._send_snapshot, auto=True)
            else:
                self._say("这段视频没有深度标尺（EchoNet 已缩成 112×112），给不出毫米尺度，不能送去测量")
            return
        beats = self.result["beats"]
        here = [b for b in beats if b["ed"] <= self.cur <= b["es"]] if not self.playing else []
        b = here[0] if here else sorted(beats, key=lambda b: b["ef"])[len(beats) // 2]   # 停在哪一拍就送哪一拍
        ed, es = b["ed"], b["es"]
        if self.masks.get(ed) is None or self.masks.get(es) is None:
            self._say("这一拍的掩膜不全，送不过去")
            return
        info = dict(patient=os.path.splitext(self.name)[0].strip(), ed_frame=ed, es_frame=es,
                    ed_img=self.frames[ed].copy(), es_img=self.frames[es].copy(),
                    ed_mask=self.masks[ed].copy(), es_mask=self.masks[es].copy(), spacing=(s, s),
                    mm_px=self.app.calib.get(self._raw_key()), ef_src=self.ef_now, ef_beat_raw=b["ef"],
                    source="现场模式" if self.live else "关键帧模式", data_note=self.data_note(),
                    corrected=bool(self.fixed), which="当前这一拍" if here else "中位那一拍")
        self._pause()
        self.app.switch("snapshot")
        self.app.views["snapshot"].accept_mono(info)

    # ================================================================ 证据报告
    def _report(self):
        if not self.result or self.res is None:
            return
        ws = self.ws
        r = self.result
        keys = []
        if r["beats"]:
            b = sorted(r["beats"], key=lambda b: b["ef"])[len(r["beats"]) // 2]
            for t, lab in ((b["ed"], "舒张末 ED（第 %d 帧）"), (b["es"], "收缩末 ES（第 %d 帧）")):
                m = self.masks.get(t)
                if m is not None:
                    keys.append((ws._png(ws.contour_rgb(self.frames[t], m, 3)), lab % (t + 1)))
        k = self.res["keyframe"]
        mode = self.live_info if self.live and self.live_info else \
            ("关键帧（PL 只分割关键帧，其余帧板上块匹配跟踪；板端 v5.21+）" if k["key_frac"] < 1 else "每帧推理（每帧都上 PL）")
        info = dict(result=r, fps=self.fps, name=self.name, nframes=len(self.frames), ef=self.ef_now, cal_beat=self._cal,
                    keyframes=keys, qc_level=self.qc_level, qc_reasons=self.qc_reasons,
                    clinical=self.clinical, clinical_where=self.clinical_where,
                    ref_ef=self.ref.get(self.name if not self.live else (self.src or {}).get("video") or "", (None,))[0],
                    curve=ws.curve_png([t / self.fps for t in range(len(self.vols))], self.vols, r["beats"], self.fps,
                                       self._cal),
                    model="%d 号 %s" % (self.model["index"], self.model["name"]), mode=mode,
                    n_analyzed=len(self.vols), ai_ms=self.stats["ai_ms"], wall=self.stats["wall"],
                    keyframe=k, quality=self._report_quality(),
                    interval=self.res.get("interval") if self.win is None and not self._no_beat_ef() else None,
                    data_note=self.data_note(), hints=getattr(self, "hints", None))
        if self.fixed:
            info["corrections"] = dict(n=len(self.undo), frames=sorted(t + 1 for t, v in self.fixed.items() if v == "fix"),
                                       prop=sorted(t + 1 for t, v in self.fixed.items() if v == "prop"),
                                       ef_board=self.ef_board, ef_now=self.ef_now, pl_ms=self.fix_ms,
                                       models="%d 号 %s / %d 号 %s" % (self.m_inter["index"], self.m_inter["name"],
                                                                     self.m_seq["index"], self.m_seq["name"]))
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
                        figs.append((ws._png(rgb), lab % (t + 1)))
            info.update(heart=self.heart, heart_cfg=self.hc_cfg, heart_figs=figs)
        d = os.path.join(HERE, "reports")
        os.makedirs(d, exist_ok=True)
        path = ws.write_report(os.path.join(d, "echo_%s_%s.html" % (os.path.splitext(self.name)[0],
                                                                    time.strftime("%Y%m%d_%H%M%S"))), info)
        self.last_report = path
        self._say("证据报告已写到 %s" % path)
        if self.open_report:
            try:
                webbrowser.open("file:///" + path.replace("\\", "/"))
            except Exception:
                pass

    # ================================================================ 消息循环
    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "loaded":
                    self._on_loaded(*item[1:])
                elif kind == "cfg":
                    self.kf_cfg, self.hc_cfg = item[1], item[2]
                    self.app.board_seen(item[3])
                elif kind == "rolltag":
                    self.roll_tag = item[1]
                    if self.roll_tag:
                        self.var_qwhy.set("扫查中：质量灯（%s）攒满 1 s 后出" % self.roll_tag)
                elif kind == "prog":
                    self._on_prog(item[1])
                elif kind == "lframe":
                    self._on_lframe(item[1])
                elif kind == "job":
                    self._on_job(*item[1:])
                elif kind == "fixed":
                    self._on_fixed(*item[1:])
                elif kind == "fix_prog":
                    self.var_fix.set(item[1])
                elif kind == "fix_err":
                    self._set_busy(False)
                    self.btn_rep.state(["!disabled"])
                    self.var_fix.set(item[1])
                elif kind == "err":
                    self._set_busy(False)
                    msg = item[1]
                    if any(k in msg for k in ("peer closed", "timed out", "refused", "WinError")):
                        import heartapp                      # 代理 / 网线问题给一句提示
                        msg += heartapp.route_hint(self._hostport()[0])
                    self._say(msg)
        except queue.Empty:
            pass
        if self.frame.winfo_exists():
            self.frame.after(30, self._poll)

    def close(self):
        self._stop()
        self._pause()
