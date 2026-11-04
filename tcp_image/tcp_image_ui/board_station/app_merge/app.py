# -*- coding: utf-8 -*-
"""app.py —— 基于 FPGA 的眼底血管实时分析系统：上位机界面。

    顶栏：板卡地址 + 链路状态 + 发送 / 连续发送
    左栏：AI 推理 · 回传内容 · PL 预处理 · 点击纠错 · 专项工作站
    中间：发给板子的原图 | FPGA 处理后回传的图
    下方：往返总耗时 / 帧率 / 板端推理 / 目标占比 四张指标卡 + 详情页签（链路 / AI / 形态）
    菜单栏与界面控件是同一套变量，两边改哪边都行。

用法：run.bat 双击，或
    python app.py [host] [port]
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import confidence
import interactive
import imageio
import imagelink
import proto
import sequence

import uistyle
from uistyle import (C_ACCENT, C_ACCENT_D, C_BAR, C_BG, C_BORDER, C_CARD, C_EMPTY,  # noqa: F401
                     C_GREEN, C_IDLE, C_MUTED, C_PURPLE, C_RED, C_STOP, C_TEXT, C_WARN,
                     FONT, FONT_BIG, FONT_HEAD, FONT_MONO, FONT_SMALL, FONT_TITLE)

DEFAULT_HOST = "192.168.1.254"
DEFAULT_PORT = 5060
HISTORY_MAX = 10
FPS_WINDOW = 10            # 连续模式下按最近这么多帧的完成时刻算实测帧率

APP_TITLE = "基于 FPGA 的眼底血管实时分析系统 — 上位机"
DISCLAIMER = "仅用于工程教学与算法演示，不作为临床诊断依据"
DATA_NOTE = "内置示例图来自 STARE 公开数据集"

# 右图可选的显示内容：(值, 菜单里的名字)。值与板端回传图的名字一致。
SHOW_CHOICES = (("auto", "自动"), ("proc", "预处理图"),
                ("overlay", "掩膜叠加"), ("mask", "血管掩膜"),
                ("heat", "置信度热力图"), ("uncertain", "不确定区域"),
                ("morph", "形态骨架"))
SHOW_LABELS = dict(SHOW_CHOICES)
SHOW_KEYS = {label: value for value, label in SHOW_CHOICES}
AI_VIEWS = ("overlay", "mask", "heat", "uncertain")   # 有 logit 时可在本地重算的视图
UNSURE_BAND_DEFAULT = 8    # 不确定带：|logit - 门限| <= 8（约 ±0.8 个 logit 单位）
ROI_MIN_SIZE = 8           # 拖出的框小于 8 像素当作误点
ZOOM_1X = "1×（ROI，帧内推理）"   # 框选放大 1 倍 = 原来的 ROI 局部推理（v5 控制头，逐位相同）


def zoom_choices():
    """框选放大的选项：1 倍 = 老 ROI；更大的倍数 = 在原图上按该倍数重新推理（大图精查窗口，病例任务 JOB_TILE）。
    倍数和上限来自 tile_config.json（HRF 训练集上量的 Dice-倍数曲线，见 E:/fpga/my_project/pld2026final/03_算法与数据/hrf/README.md）。"""
    import bigwin
    cfg = bigwin.load_config()
    return [ZOOM_1X] + ["%g× 重新推理（大图精查）" % m for m in cfg["mags"] if 1 < m <= float(cfg["cap"])]


class App(object):
    # busy：嵌在平台扩展窗口里时与心超工作站共用一个（板端一次只服务一个连接）
    busy = property(lambda self: self.shared.busy if self.shared is not None else self._busy,
                    lambda self, v: setattr(self.shared, "busy", v) if self.shared is not None
                    else setattr(self, "_busy", v))

    def __init__(self, root, host, port, selftest_path=None, preset_flags=None,
                 preset_ai_src=None, preset_ai_thr=None, preset_ai_res=None,
                 preset_thr=None, preset_mode=None, parent=None, shared=None, program=None):
        """parent / shared / program：嵌进 platformwin.PlatformWindow 的一页（parent = 页框，shared = 心超工作站
        heartapp.HeartApp：共用板子连接、地址、busy 和状态栏；program = 这一页的程序 dict，见 platformwin.PROGRAMS）。
        不给时与原来的独立主界面完全相同。"""
        self.root = root
        self.parent, self.shared, self.program = parent, shared, program
        self.queue = queue.Queue()
        self.selftest_path = selftest_path
        self.thr_fixed = preset_thr is not None

        self.link = shared.link if shared is not None else imagelink.ImageLink(host, port)

        self.image = None          # 当前要发送的原图(bytes)
        self.inter = None          # 点击纠错的一轮会话（interactive.Session）；None = 没在纠错
        self.inflight_inter = None  # 在途那一帧是点击纠错帧时，它带的点击列表
        self.photo_left = None     # 必须持有引用, 否则会被 GC 掉
        self.photo_right = None
        self.last_reply = None     # 最近一次收到的回图(bytes), 供"保存回图"用
        self.reply_images = {}     # 最近一次收到的所有回传图 {名字: bytes}
        self.view_images = {}      # 右图可显示的图：回传图 + 由 logit 本地生成的视图
        self.local_views = set()   # view_images 里哪些是本地重算的(门限与板端不同)
        self.logit_plane = None    # 本帧 logit（偏移码，每像素 1 字节）；没回传时为 None
        self.logit_base = None     # 本地叠加用的底图（板端叠加的底图是预处理图）
        self.logit_base_note = ""
        self.heat_cache = None     # 热力图与门限无关，每帧算一次
        self.frame_ai_thr = proto.AIPARAM_THR_DEFAULT   # 本帧板端用的 AI 门限
        self.inflight_ai_thr = proto.AIPARAM_THR_DEFAULT
        self.frame_ai_res = 512
        self.verify_note = ""      # 本地复算 vs 板端回传图的一致性
        self.conf_job = None
        self.inflight_key = None
        self.roi = None           # 界面上选的 ROI (x, y, w, h)；None = 整图推理
        self.roi_drag_start = None
        self.inflight_roi = None   # 在途那一帧发出去的 ROI
        self.frame_roi = None      # 当前显示的这一帧是哪个 ROI 的结果（整图帧为 None）
        self.full_ref = None       # 同一张图上一帧整图推理的 logit，用来核对 ROI 结果
        self.roi_verify_note = ""
        self.board_stat_version = None   # 最近一次统计块的 version（判断板端认不认 ROI）
        self.orig_rgb = None       # 载入的原图（任意尺寸，numpy）；框选放大 > 1 倍时在它上面重新推理
        self.orig_name = None
        self.reply_stats = {}      # 每幅回图的 {rows_ok, crc_errors, seq_errors}
        self.ai_stats = None       # 最近一次的 AI 统计块(dict)或 None
        self.showing = None        # 当前右图显示的是哪一种
        self.last_thr = proto.CTRL_THR_DEFAULT   # 最近一帧用的阈值分割门限
        self.hist = []             # 最近若干次的 total_ms
        self.done_times = []       # 连续模式下最近若干帧的完成时刻(perf_counter), 算实测帧率
        if shared is None:         # 共用时不动心超工作站的 busy（它可能正有任务在途）
            self.busy = False
        self.repeat = False
        self.repeat_job = None

        self._build_ui()
        if preset_flags is not None:
            self.var_f_proc.set(bool(preset_flags & proto.FLAG_ECHO_PROC))
            self.var_f_mask.set(bool(preset_flags & proto.FLAG_ECHO_MASK))
            self.var_f_over.set(bool(preset_flags & proto.FLAG_ECHO_OVER))
            self.var_f_stats.set(bool(preset_flags & proto.FLAG_STATS))
            self.var_f_logit.set(bool(preset_flags & proto.FLAG_ECHO_LOGIT))
            self.var_f_morph.set(bool(preset_flags & proto.FLAG_MORPH))
        if preset_ai_src is not None:
            self.var_ai_src.set("预处理结果" if preset_ai_src == proto.AIPARAM_SRC_PROC
                                else "原始 RGB")
        if preset_ai_thr is not None:
            self.var_ai_thr.set(str(preset_ai_thr))
        if preset_ai_res is not None:
            self.var_ai_res.set(str(preset_ai_res))
        self._load_pattern(self._default_pattern())
        if preset_mode is not None and 0 <= preset_mode < len(self.mode_labels):
            self.var_mode.set(self.mode_labels[preset_mode])
            self._on_mode_change()          # 同步阈值标签 + 按该模式重算推荐值
        if preset_thr is not None:
            self.var_thr.set(str(preset_thr))
            self.var_thr_note.set("命令行 --thr %d（固定，不随图片自动重算）" % preset_thr)
        self.root.after(50, self._poll)

    # ------------------------------------------------------------------ UI
    # 布局：顶栏（板卡地址 / 链路状态 / 发送）
    #       左侧参数栏（AI 推理 · 回传内容 · PL 预处理 · 点击纠错 · 专项工作站）
    #       右侧两幅图 + 四张指标卡 + 详情页签
    #       底部状态栏（常驻使用声明）
    # 只用带前缀的 ttk 样式（Card.* / Detail.*），不改默认样式，免得影响各个子窗口。
    # 配色 / 字体 / 卡片等通用部件都在 uistyle.py，子窗口共用
    def _setup_style(self):
        uistyle.setup_style(self.root)

    _card = staticmethod(uistyle.card)
    _label = staticmethod(uistyle.label)

    def _section(self, parent, title, first=False):
        """左侧参数栏的一节：分隔线 + 小标题 + 两列网格（标签 | 控件）。"""
        if not first:
            tk.Frame(parent, bg=C_BORDER, height=1).pack(fill="x", padx=14, pady=(10, 0))
        self._label(parent, title, font=FONT_HEAD, fg=C_ACCENT) \
            .pack(anchor="w", padx=14, pady=(10 if first else 6, 2))
        body = tk.Frame(parent, bg=C_CARD)
        body.pack(fill="x", padx=14)
        body.grid_columnconfigure(1, weight=1)
        return body

    def _field(self, body, row, label, widget=None, label_var=None):
        self._label(body, label, var=label_var, fg=C_MUTED) \
            .grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
        if widget is not None:
            widget.grid(row=row, column=1, sticky="ew", pady=2)

    def _slider(self, parent, var, lo, hi, extra=None):
        """数值框 + 滑条（+ 可选的按钮），和同一个 StringVar 双向同步，写回的总是整数。"""
        f = tk.Frame(parent, bg=C_CARD)
        ent = ttk.Entry(f, textvariable=var, width=5, font=FONT_MONO)
        ent.pack(side="left")

        def from_scale(v):
            s = str(int(round(float(v))))
            if var.get() != s:
                var.set(s)

        sc = ttk.Scale(f, from_=lo, to=hi, orient="horizontal", length=120, command=from_scale)
        sc.pack(side="left", fill="x", expand=True, padx=(8, 0))

        def from_var(*_):
            try:
                v = max(lo, min(hi, float(var.get())))
            except ValueError:
                return                   # 正在输入（比如只打了个负号）
            if abs(sc.get() - v) >= 0.5:
                sc.set(v)

        var.trace_add("write", from_var)
        from_var()
        if extra is not None:
            text, cmd = extra
            btn = ttk.Button(f, text=text, width=len(text) + 1, command=cmd)
            btn.pack(side="left", padx=(8, 0))
            return f, ent, sc, btn
        return f, ent, sc

    def _build_ui(self):
        embedded = self.parent is not None
        r = self.parent if embedded else self.root
        if not embedded:
            r.title(APP_TITLE)
        r.configure(bg=C_BG)
        uistyle.setup_style(r)

        self._build_topbar(r)
        side = self._card(r)
        side.grid(row=1, column=0, sticky="nsw", padx=(12, 0), pady=12)
        self._build_sidebar(side)
        main = tk.Frame(r, bg=C_BG)
        main.grid(row=1, column=1, sticky="nsew" if not embedded else "n", padx=12, pady=12)
        self._build_images(main)
        self._build_metrics(main)

        # ---- 状态栏：左边是状态，右边常驻使用声明（赛题基础要求⑥）；嵌入时用平台窗口的状态栏 ----
        self.var_status = tk.StringVar(value="就绪")
        if embedded:
            self.var_status.trace_add("write", lambda *a: self.shared.status(self.var_status.get()))
        else:
            uistyle.statusbar(r, 2, self.var_status, "%s｜%s" % (DISCLAIMER, DATA_NOTE), columnspan=2)

        r.grid_rowconfigure(1, weight=1)
        r.grid_columnconfigure(1, weight=1)
        self._update_conf_note()
        self._sync_thr_widgets()
        self._sync_send_btn()
        if not embedded:
            self._build_menu()
        else:                                          # 嵌入时没有菜单栏：常用快捷键绑在平台窗口上
            for seq, cmd in (("<Control-o>", self._load_file), ("<F5>", self._on_send), ("<Escape>", self._roi_clear)):
                self.parent.winfo_toplevel().bind(seq, lambda e, c=cmd: c() if self.parent.winfo_ismapped() else None)

    def _build_topbar(self, r):
        bar = tk.Frame(r, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        if self.shared is not None:                   # 嵌入：标题 = 本页程序，地址在心超工作站标题栏
            self.var_prog_title = tk.StringVar(value=(self.program or {}).get("title", "单幅图像程序"))
            self.var_prog_note = tk.StringVar(value=(self.program or {}).get("note", ""))
            self._label(bar, var=self.var_prog_title, font=FONT_TITLE).pack(side="left", padx=(16, 0), pady=10)
            self._label(bar, var=self.var_prog_note, font=FONT_SMALL, fg=C_MUTED) \
                .pack(side="left", padx=(12, 0), pady=(6, 0))
        else:
            self._label(bar, APP_TITLE.split(" — ")[0], font=FONT_TITLE) \
                .pack(side="left", padx=(16, 0), pady=10)

        # 右侧从右往左：发送按钮 / 连续发送 / 板卡地址 + 链路灯
        self.btn_send = tk.Button(bar, text="发送 ▶", font=FONT_HEAD, fg="white", bg=C_ACCENT,
                                  activebackground=C_ACCENT_D, activeforeground="white",
                                  disabledforeground="#c7d2fe", relief="flat", bd=0,
                                  padx=20, pady=5, cursor="hand2", command=self._on_send_button)
        self.btn_send.pack(side="right", padx=(12, 16), pady=8)

        rep = tk.Frame(bar, bg=C_CARD)
        rep.pack(side="right", padx=(20, 0))
        self.var_repeat = tk.BooleanVar(value=False)
        ttk.Checkbutton(rep, text="连续发送，间隔", style="Card.TCheckbutton",
                        variable=self.var_repeat, command=self._on_repeat_toggle).pack(side="left")
        self.var_interval = tk.StringVar(value="200")
        ttk.Entry(rep, textvariable=self.var_interval, width=5, font=FONT_MONO) \
            .pack(side="left", padx=(4, 3))
        self._label(rep, "ms", fg=C_MUTED).pack(side="left")

        conn = tk.Frame(bar, bg=C_CARD)
        conn.pack(side="right")
        if self.shared is not None:
            self.var_host, self.var_port = self.shared.var_host, self.shared.var_port
        else:
            self._label(conn, "板卡", fg=C_MUTED).pack(side="left", padx=(0, 6))
            self.var_host = tk.StringVar(value=self.link.host)
            ttk.Entry(conn, textvariable=self.var_host, width=14, font=FONT_MONO).pack(side="left")
            self._label(conn, ":", fg=C_MUTED).pack(side="left", padx=2)
            self.var_port = tk.StringVar(value=str(self.link.port))
            ttk.Entry(conn, textvariable=self.var_port, width=6, font=FONT_MONO).pack(side="left")
        self.lbl_link = self._label(conn, "●", fg=C_IDLE)
        self.lbl_link.pack(side="left", padx=(12, 3))
        self.var_link = tk.StringVar(value="未连接")
        self._label(conn, var=self.var_link, fg=C_MUTED, width=8, anchor="w").pack(side="left")

    def _build_sidebar(self, side):
        # ---- AI 推理：模型库同一比特流换一套参数（板端统计块 version >= 5 才认）----
        body = self._section(side, "AI 推理", first=True)
        self.var_ai_model = tk.StringVar(value=proto.MODELS[0]["label"])
        models = [m for m in proto.MODELS if not m.get("hidden")]
        if self.shared is not None:                   # 平台扩展里只放心脏以外的单图程序（心超在主界面）
            models = [m for m in models if not m["name"].startswith("echo")]
        self._field(body, 0, "模型", ttk.Combobox(
            body, textvariable=self.var_ai_model, width=16, font=FONT, state="readonly",
            values=[m["label"] for m in models]))
        self.var_ai_model.trace_add("write", lambda *a: self._on_model_change())
        self.var_ai_res = tk.StringVar(value="512")
        self._field(body, 1, "分辨率", ttk.Combobox(
            body, textvariable=self.var_ai_res, width=16, font=FONT, state="readonly",
            values=[str(v) for v in proto.AI_RES_CHOICES]))
        self.var_ai_src = tk.StringVar(value="原始 RGB")
        self._field(body, 2, "输入", ttk.Combobox(
            body, textvariable=self.var_ai_src, width=16, font=FONT, state="readonly",
            values=["原始 RGB", "预处理结果"]))
        # 判定门限 + 不确定带：有 logit 时拖动只在本地重算，不重跑 FPGA
        self.var_ai_thr = tk.StringVar(value=str(proto.AIPARAM_THR_DEFAULT))
        self._field(body, 3, "判定门限", self._slider(body, self.var_ai_thr, -64, 64)[0])
        self.var_band = tk.StringVar(value=str(UNSURE_BAND_DEFAULT))
        self._field(body, 4, "不确定带 ±", ttk.Spinbox(
            body, from_=1, to=64, textvariable=self.var_band, width=5, font=FONT_MONO))
        body.grid_slaves(row=4, column=1)[0].grid_configure(sticky="w")
        self.var_conf_note = tk.StringVar(value="")
        self._label(body, var=self.var_conf_note, font=FONT_SMALL, fg=C_MUTED, wrap=True) \
            .grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self.var_ai_thr.trace_add("write", lambda *a: self._schedule_conf())
        self.var_band.trace_add("write", lambda *a: self._schedule_conf())
        # 框选放大（部分 C）：1 倍 = 原来的 ROI；> 1 倍 = 原图上按该倍数分块重新推理（大图精查窗口）
        self.zoom_values = zoom_choices()
        self.var_zoom = tk.StringVar(value=ZOOM_1X)
        self._field(body, 6, "框选放大", ttk.Combobox(
            body, textvariable=self.var_zoom, width=16, font=FONT, state="readonly", values=self.zoom_values))
        self.var_zoom.trace_add("write", lambda *a: self._draw_roi())

        # ---- 回传内容（flags）----
        body = self._section(side, "回传内容")
        self.var_f_proc = tk.BooleanVar(value=True)
        self.var_f_mask = tk.BooleanVar(value=False)
        self.var_f_over = tk.BooleanVar(value=False)
        self.var_f_stats = tk.BooleanVar(value=False)
        self.var_f_logit = tk.BooleanVar(value=False)
        self.var_f_morph = tk.BooleanVar(value=False)
        for i, (text, var) in enumerate((
                ("预处理图", self.var_f_proc), ("血管掩膜", self.var_f_mask),
                ("掩膜叠加", self.var_f_over), ("数值统计", self.var_f_stats),
                ("置信度 logit", self.var_f_logit), ("形态指标", self.var_f_morph))):
            ttk.Checkbutton(body, text=text, variable=var, style="Card.TCheckbutton") \
                .grid(row=i // 2, column=i % 2, sticky="w", pady=2, padx=(0, 12))

        # ---- PL 预处理：处理模式 + 阈值（逐帧随控制头发给板子）----
        body = self._section(side, "PL 预处理")
        self.mode_labels = ["%d %s" % (k, proto.MODE_NAMES[k]) for k in sorted(proto.MODE_NAMES)]
        self.var_mode = tk.StringVar(value=self.mode_labels[0])
        mcb = ttk.Combobox(body, textvariable=self.var_mode, width=16, font=FONT,
                           state="readonly", values=self.mode_labels)
        mcb.bind("<<ComboboxSelected>>", lambda e: self._on_mode_change())
        self._field(body, 0, "模式", mcb)
        self.var_thr_label = tk.StringVar(value="分割门限")
        self.var_thr = tk.StringVar(value=str(proto.CTRL_THR_DEFAULT))
        thr_row, self.ent_thr, self.scale_thr, self.btn_otsu = self._slider(
            body, self.var_thr, 0, 255, extra=("Otsu", lambda: self._auto_thr(force=True)))
        self._field(body, 1, None, thr_row, label_var=self.var_thr_label)
        self.var_thr_note = tk.StringVar(value="载入图片后会自动给一个推荐值")
        self._label(body, var=self.var_thr_note, font=FONT_SMALL, fg=C_MUTED, wrap=True) \
            .grid(row=2, column=0, columnspan=2, sticky="ew", pady=(2, 0))

        # ---- 点击纠错（F2）：有配套交互模型的任务（心超）才可用；平台扩展里没有心超，不放 ----
        self.var_inter = tk.BooleanVar(value=False)
        if self.shared is None:
            body = self._section(side, "点击纠错")
            ttk.Checkbutton(body, text="启用（左键是 / 右键否）", variable=self.var_inter,
                            style="Card.TCheckbutton", command=self._on_inter_toggle) \
                .grid(row=0, column=0, sticky="w")
            btns = tk.Frame(body, bg=C_CARD)
            btns.grid(row=0, column=1, sticky="e")
            ttk.Button(btns, text="撤销", width=6, command=self._inter_undo).pack(side="left")
            ttk.Button(btns, text="重置", width=6, command=self._inter_reset) \
                .pack(side="left", padx=(6, 0))

        # ---- 专项工作站：各自开子窗口，和主界面共用板子连接 ----
        if self.shared is not None:                   # 平台扩展：心超 / 序列在主界面，CT / PL 程序台是平台窗口的页签
            tools = [("大图精查", self._open_bigwin)] + [
                (label, (lambda f=fn: f(self))) for label, fn in getattr(self.shared, "extra_tools", [])]
        else:
            tools = [("序列跟踪", self._open_sequence), ("PL 程序台", self._open_progwin),
                     ("CT 整卷", self._open_ctwin), ("心超工作站", self._open_echowin),
                     ("大图精查", self._open_bigwin)]
        body = self._section(side, "专项工作站" if self.shared is None else "更多")
        body.grid_columnconfigure(0, weight=1, uniform="tools")
        body.grid_columnconfigure(1, weight=1, uniform="tools")
        for i, (text, cmd) in enumerate(tools):
            ttk.Button(body, text=text, command=cmd).grid(
                row=i // 2, column=i % 2, sticky="ew", pady=3,
                padx=(0, 4) if i % 2 == 0 else (4, 0))
        tk.Frame(side, bg=C_CARD, height=12).pack()

    def _build_images(self, main):
        imgs = tk.Frame(main, bg=C_BG)
        imgs.grid(row=0, column=0, sticky="n")

        # ---- 左：发给板子的原图（画布：要在图上拖框选 ROI / 点击纠错）----
        lc = self._card(imgs)
        lc.grid(row=0, column=0, padx=(0, 12))
        head = tk.Frame(lc, bg=C_CARD)
        head.pack(fill="x", padx=12, pady=(10, 8))
        self._label(head, "原图", font=FONT_HEAD).pack(side="left")
        ttk.Button(head, text="载入图片…", command=self._load_file).pack(side="right")
        # 下拉框 = samples/ 里的**各模型样片**（samples/<组>/图，组 = 模型）。
        # 2026-10-28 起界面不再列 bars / checker / gradient 三个内置图样（它们只留给
        # 命令行和自检脚本）；切模型时会自动切到该模型的样片，见 _sync_sample_to_model。
        # 往 samples/<组>/ 里丢新图后**重启工具**即可看到（列表在启动时扫一次）。
        self.var_pattern = tk.StringVar(value="")
        cb = ttk.Combobox(head, textvariable=self.var_pattern, width=20, font=FONT,
                          state="readonly", values=imageio.ui_names())
        cb.pack(side="right", padx=(0, 6))
        cb.bind("<<ComboboxSelected>>", lambda e: self._load_pattern(self.var_pattern.get()))
        # 边框和高亮都设 0，画布坐标 = 图像像素坐标
        self.cv_left = tk.Canvas(lc, width=512, height=512, bg=C_EMPTY,
                                 bd=0, highlightthickness=0, cursor="crosshair")
        self.cv_left.pack(padx=12)
        self.cv_img = self.cv_left.create_image(0, 0, anchor="nw")
        self.cv_left.bind("<ButtonPress-1>", self._roi_press)
        self.cv_left.bind("<B1-Motion>", self._roi_drag)
        self.cv_left.bind("<ButtonRelease-1>", self._roi_release)
        self.cv_left.bind("<Button-3>", self._right_click)
        self.root.bind("<Control-z>", lambda e: self._inter_undo())
        self.var_roi_note = tk.StringVar(value="")
        self._label(lc, var=self.var_roi_note, font=FONT_SMALL, fg=C_MUTED, anchor="w") \
            .pack(fill="x", padx=12, pady=(6, 10))

        # ---- 右：板子回传（按「回传内容」生成）----
        rc = self._card(imgs)
        rc.grid(row=0, column=1)
        head = tk.Frame(rc, bg=C_CARD)
        head.pack(fill="x", padx=12, pady=(10, 8))
        self._label(head, "板子回传", font=FONT_HEAD).pack(side="left")
        self.btn_save = ttk.Button(head, text="保存…", command=self._save_reply)
        self.btn_save.pack(side="right")
        # var_show 存的是回传图的名字（auto/proc/…），下拉框显示中文名
        self.var_show = tk.StringVar(value="auto")
        self.var_show_label = tk.StringVar(value=SHOW_LABELS["auto"])
        scb = ttk.Combobox(head, textvariable=self.var_show_label, width=12, font=FONT,
                           state="readonly", values=[label for _, label in SHOW_CHOICES])
        scb.pack(side="right", padx=(0, 6))
        scb.bind("<<ComboboxSelected>>", lambda e: (
            self.var_show.set(SHOW_KEYS[self.var_show_label.get()]), self._render_right()))
        self.var_show.trace_add("write", lambda *a: self.var_show_label.set(
            SHOW_LABELS.get(self.var_show.get(), self.var_show.get())))
        self._label(head, "显示", fg=C_MUTED).pack(side="right", padx=(0, 6))
        self.cv_right = tk.Canvas(rc, width=512, height=512, bg=C_EMPTY,
                                  bd=0, highlightthickness=0)
        self.cv_right.pack(padx=12)
        self.cv_right_img = self.cv_right.create_image(0, 0, anchor="nw")
        self.cv_right_hint = self.cv_right.create_text(
            256, 256, text="发送一帧后，这里显示板子回传的结果", fill=C_MUTED, font=FONT)
        self.var_reply_note = tk.StringVar(value="")
        self._label(rc, var=self.var_reply_note, font=FONT_SMALL, fg=C_MUTED, anchor="w") \
            .pack(fill="x", padx=12, pady=(6, 10))

    def _build_metrics(self, main):
        # ---- 四张指标卡：往返总耗时 / 帧率 / 板端推理 / 目标占比，各带一行口径说明 ----
        row = tk.Frame(main, bg=C_BG)
        row.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.var_total = tk.StringVar(value="—")
        self.var_fps = tk.StringVar(value="—")
        self.var_fps_note = tk.StringVar(value="")
        self.var_infer = tk.StringVar(value="—")
        self.var_infer_note = tk.StringVar(value="勾选「数值统计」后显示")
        self.var_target = tk.StringVar(value="—")
        self.var_target_note = tk.StringVar(value="勾选「数值统计」后显示")
        self.var_total_note = tk.StringVar(value="上位机发图 → 收齐回图")
        for col, (title, var, note, color) in enumerate((
                ("往返总耗时", self.var_total, self.var_total_note, C_GREEN),
                ("帧率", self.var_fps, self.var_fps_note, C_ACCENT),
                ("板端推理（整网）", self.var_infer, self.var_infer_note, C_RED),
                ("目标占比", self.var_target, self.var_target_note, C_PURPLE))):
            row.grid_columnconfigure(col, weight=1, uniform="metric")
            c = self._card(row)
            c.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 12, 0))
            self._label(c, title, font=FONT_SMALL, fg=C_MUTED).pack(anchor="w", padx=14, pady=(6, 0))
            self._label(c, var=var, font=FONT_BIG, fg=color).pack(anchor="w", padx=14)
            self._label(c, var=note, font=FONT_SMALL, fg=C_MUTED, wrap=True) \
                .pack(fill="x", padx=14, pady=(0, 6))

        # ---- 详情：链路计时 / AI 统计 / 血管形态，分页签放，默认只看链路 ----
        nb = ttk.Notebook(main, style="Detail.TNotebook")
        nb.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self.var_detail = tk.StringVar(value="")
        self.var_stats = tk.StringVar(value="统计：还没有数据")
        self.var_hist = tk.StringVar(value="")
        self.var_ai = tk.StringVar(value="AI：还没有数据")
        self.var_morph = tk.StringVar(value="形态：勾「形态指标」后由板端计算")
        labels = []
        for title, lines in (("链路计时", (self.var_detail, self.var_stats, self.var_hist)),
                             ("AI 统计", (self.var_ai,)),
                             ("血管形态", (self.var_morph,))):
            page = tk.Frame(nb, bg=C_CARD, pady=4)
            for var in lines:
                lb = self._label(page, var=var, font=FONT_MONO, justify="left", anchor="w",
                                 wraplength=600)
                lb.pack(fill="x", padx=12, pady=2)
                labels.append(lb)
            nb.add(page, text=title)
        # 没选中的页签不会收到 <Configure>，所以按整个页签栏的宽度统一折行
        nb.bind("<Configure>", lambda e: [lb.configure(wraplength=max(200, e.width - 40))
                                          for lb in labels])

    def _build_menu(self):
        """菜单栏。所有项都绑在界面控件用的同一批变量上，所以两边始终同步。"""
        r = self.root
        menubar = tk.Menu(r)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="载入图片…", accelerator="Ctrl+O", command=self._load_file)
        m_file.add_command(label="保存当前回图…", accelerator="Ctrl+S",
                           command=self._save_reply)
        m_file.add_separator()
        m_file.add_command(label="退出", command=r.destroy)
        menubar.add_cascade(label="文件", menu=m_file)

        m_run = tk.Menu(menubar, tearoff=0)
        m_run.add_command(label="发送一帧", accelerator="F5", command=self._on_send)
        m_run.add_checkbutton(label="连续发送", variable=self.var_repeat,
                              command=self._on_repeat_toggle)
        menubar.add_cascade(label="运行", menu=m_run)

        m_proc = tk.Menu(menubar, tearoff=0)
        for label in self.mode_labels:
            m_proc.add_radiobutton(label=label, variable=self.var_mode, value=label,
                                   command=self._on_mode_change)
        m_proc.add_separator()
        m_proc.add_command(label="自动阈值", command=self._auto_thr)
        menubar.add_cascade(label="处理", menu=m_proc)

        m_ai = tk.Menu(menubar, tearoff=0)
        for label, var in (("回传预处理图", self.var_f_proc), ("回传血管掩膜", self.var_f_mask),
                           ("回传掩膜叠加", self.var_f_over), ("回传数值统计", self.var_f_stats),
                           ("回传置信度（logit）", self.var_f_logit),
                           ("回传形态指标", self.var_f_morph)):
            m_ai.add_checkbutton(label=label, variable=var)
        m_ai.add_separator()
        m_src = tk.Menu(m_ai, tearoff=0)
        for label in ("原始 RGB", "预处理结果"):
            m_src.add_radiobutton(label=label, variable=self.var_ai_src, value=label)
        m_ai.add_cascade(label="AI 输入", menu=m_src)
        m_res = tk.Menu(m_ai, tearoff=0)
        for res in proto.AI_RES_CHOICES:
            m_res.add_radiobutton(label="%d×%d" % (res, res), variable=self.var_ai_res,
                                  value=str(res))
        m_ai.add_cascade(label="AI 分辨率", menu=m_res)
        m_model = tk.Menu(m_ai, tearoff=0)
        for m in proto.MODELS:
            if not m.get("hidden"):
                m_model.add_radiobutton(label=m["label"], variable=self.var_ai_model, value=m["label"])
        m_ai.add_cascade(label="AI 模型", menu=m_model)
        m_ai.add_separator()
        m_ai.add_command(label="清除 ROI（恢复整图推理）", accelerator="Esc",
                         command=self._roi_clear)
        m_zoom = tk.Menu(m_ai, tearoff=0)
        for label in self.zoom_values:
            m_zoom.add_radiobutton(label=label, variable=self.var_zoom, value=label)
        m_ai.add_cascade(label="框选放大", menu=m_zoom)
        menubar.add_cascade(label="AI", menu=m_ai)

        m_view = tk.Menu(menubar, tearoff=0)
        for value, label in SHOW_CHOICES:
            m_view.add_radiobutton(label="右图：" + label, variable=self.var_show, value=value,
                                   command=self._render_right)
        menubar.add_cascade(label="视图", menu=m_view)

        m_tools = tk.Menu(menubar, tearoff=0)
        m_tools.add_command(label="序列跟踪（心超，F3）…", command=self._open_sequence)
        m_tools.add_command(label="PL 程序台（模型 / 编译算子 / 板上流水线）…", command=self._open_progwin)
        m_tools.add_command(label="CT 整卷分割（脾脏，⑤）…", command=self._open_ctwin)
        m_tools.add_command(label="实时心超工作站（EchoNet，③）…", command=self._open_echowin)
        m_tools.add_command(label="大图精查（眼底大图分块推理，部分 C）…", command=self._open_bigwin)
        menubar.add_cascade(label="工具", menu=m_tools)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="关于与使用声明", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=m_help)

        r.configure(menu=menubar)
        # 只绑主窗口（不用 bind_all）：子窗口里按 Esc / F5 / Ctrl+O 不该清主界面的 ROI、给主界面发帧
        r.bind("<Control-o>", lambda e: self._load_file())
        r.bind("<Control-s>", lambda e: self._save_reply())
        r.bind("<F5>", lambda e: self._on_send())
        r.bind("<Escape>", lambda e: self._roi_clear())

    def _show_about(self):
        messagebox.showinfo(
            "关于",
            "%s\n\n"
            "链路：上位机经以太网发图 → FZ3B（Zynq UltraScale+ XAZU3EG）\n"
            "PL 完成图像预处理与 U-Net 血管分割推理 → 结果回传上位机显示。\n\n"
            "%s。\n\n%s。" % (APP_TITLE, DATA_NOTE, DISCLAIMER))

    # -------------------------------------------------------------- 行为
    def _status(self, msg):
        self.var_status.set(msg)

    def _current_mode(self):
        """从下拉框取出模式编号（形如 "2 边缘增强"）。"""
        try:
            return int(self.var_mode.get().split()[0])
        except (ValueError, IndexError):
            return 0

    def _on_mode_change(self):
        """换处理模式时：同步阈值标签，并按新模式重算推荐阈值。

        阈值在模式 1 是"灰度门限"、在模式 6 是"边缘门限"，含义不同，
        所以换模式就重算一次（用户手填的会被覆盖，这是有意的）。
        """
        self._sync_thr_widgets()
        self._auto_thr(force=True)

    def _sync_thr_widgets(self):
        """阈值标签跟着模式走；只有模式 1 / 6 用这个门限，其它模式把控件置灰。"""
        mode = self._current_mode()
        used = mode in (1, proto.MODE_EDGE_OVERLAY)
        if mode == proto.MODE_EDGE_OVERLAY:
            self.var_thr_label.set("边缘门限")
        elif mode == 1:
            self.var_thr_label.set("分割门限")
        else:
            self.var_thr_label.set("门限（不用）")
        for w in (self.ent_thr, self.scale_thr, self.btn_otsu):
            w.state(["!disabled"] if used else ["disabled"])

    def _auto_thr(self, force=False):
        """按当前这张图 + 当前模式，算一个推荐阈值并填进控件。

        模式 1：Otsu 大津法（灰度用与 PL 完全相同的 (77R+150G+29B)>>8，
        所以算出来的就是板子上参与比较的那个灰度上的门限）。
        模式 6：Sobel 幅度的分位数（边缘幅度直方图极度偏向 0，Otsu 不适用）。

        命令行用 --thr 固定过阈值时不覆盖（force=True 除外，按按钮就是 force）。
        """
        if self.thr_fixed and not force:
            return
        if self.image is None:
            return
        try:
            if self._current_mode() == proto.MODE_EDGE_OVERLAY:
                thr, note = imageio.edge_threshold(self.image)
            else:
                thr, _hist, note = imageio.auto_threshold(self.image)
        except Exception as e:                        # noqa: BLE001 - 界面上报错即可
            self.var_thr_note.set("算阈值失败：%s" % e)
            return
        self.thr_fixed = False
        self.var_thr.set(str(thr))
        self.var_thr_note.set(note)

    def _default_pattern(self, m=None):
        """某个模型启动时该先载哪张图：该模型样片组的第一张；一张都没有就退回内置彩条。"""
        m = m or self._ui_model()
        return imageio.first_sample(m.get("sample") or "") or "bars"

    def _sync_sample_to_model(self, m, notes):
        """把左图换成该模型的样片 —— **只在当前图本来就是样片时才换**。

        用户自己"载入图片…"的图不会被模型切换覆盖：那种情况下下拉框里显示的是
        文件名，不在 ``imageio.SAMPLES`` 里，这里直接返回。已经在该模型的样片上
        也不重复载入（免得白丢一次点击纠错 / 回图）。
        """
        cur = self.var_pattern.get()
        if cur not in imageio.SAMPLES:
            return
        group = m.get("sample") or ""
        if imageio.sample_group(cur) == group:
            return
        name = imageio.first_sample(group)
        if name is None:
            return                      # 这个模型没有配套样片（如手工配的算子模型）
        self._load_pattern(name)
        notes.append("样片已切到 %s" % name)

    def _load_pattern(self, name):
        """下拉框里选一项：samples/ 里的样片（界面）或三个内置图样（命令行 / 自检）。

        两处都走 `imageio.load_pattern`，所以拿到的就是真正会被发出去的
        786432 字节（样片在这里被拉伸到 512x512；.ppm.gz 就地解压）。
        """
        try:
            self.image, note = imageio.load_pattern(name)
        except KeyError:
            return                          # 下拉框里不会有这种名字，忽略
        except Exception as e:                          # noqa: BLE001
            messagebox.showerror("读图失败", "读 '%s' 失败：%s" % (name, e))
            return
        self._after_load(imageio.SAMPLES.get(name) if name not in imageio.PATTERNS else None, name)
        self._status("已载入 '%s'（%d 字节）—— %s" % (name, len(self.image), note))

    def _load_path(self, path):
        """读入一个图片文件并显示到左侧（任意格式/尺寸，会自动拉伸为 512x512）。"""
        try:
            data, note = imageio.load_image(path)
        except Exception as e:
            messagebox.showerror("读图失败", str(e))
            return
        self.image = data
        self._after_load(path, os.path.basename(path))
        self._status("已载入 %s —— %s" % (os.path.basename(path), note))

    def _after_load(self, path, name):
        """换图之后的共同收尾：下拉框显示当前图名（载入文件时显示文件名），丢掉上一张图的点击纠错和回图。"""
        self.var_pattern.set(name)
        self.inter = None
        self._draw_clicks()
        self._set_orig(path, name)
        self._show_left()
        self._clear_right()
        self._auto_thr()

    @staticmethod
    def _load_dir():
        """「载入图片…」的起始目录：**题目一的案例样片包**优先，其次 samples/，最后当前目录。

        2026-10-28 起心超/眼底/皮肤三组的样片都直接从
        ``PLD2026_题目一_三个案例样片/`` 读（见 imageio.EXT_SAMPLE_DIRS），对话框也开在那里。
        """
        for d in (imageio.EXT_SAMPLE_ROOT, imageio.SAMPLE_DIR):
            if os.path.isdir(d):
                return d
        return os.getcwd()

    def _load_file(self):
        path = filedialog.askopenfilename(
            title="选择图片（任意格式/尺寸，会自动拉伸为 512x512 RGB888）",
            initialdir=self._load_dir(),
            filetypes=[("常见图片 (PNG/JPG/BMP/TIF/WebP)", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp"),
                       ("Netpbm (PPM/PGM/PBM/PNM/PAM)", "*.ppm *.pgm *.pbm *.pnm *.pam"),
                       ("NIfTI 病例 (CAMUS .nii/.nii.gz)", "*.nii *.nii.gz"),
                       ("裸 RGB888 (Xilinx .rgb/.raw/.bin)", "*.rgb *.raw *.bin"),
                       ("所有文件", "*.*")])
        if path:
            self._load_path(path)

    def _save_reply(self):
        """把右边那幅图存成文件（格式由扩展名决定）。"""
        if not self.view_images:
            messagebox.showinfo("保存回图", "还没有收到回图，先发送一帧。")
            return
        name = self.showing or self._pick_show("auto")
        reply = self.view_images.get(name)
        if reply is None:
            messagebox.showinfo("保存回图", "本帧没有回传图像。")
            return
        # 勾着"数据统计"时, 顺便把统计一起导出(同名 .json)
        extra = {"stats_images": sorted(self.reply_images), "showing": name}
        if self.ai_stats:
            extra["ai"] = self.ai_stats
        path = filedialog.asksaveasfilename(
            title="保存回图（当前显示的是 %s）" % name,
            initialfile="reply_%s.png" % name,
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png"),
                       ("PPM (P6 二进制, 无损)", "*.ppm"),
                       ("PGM (P5 灰度)", "*.pgm"),
                       ("PBM (P4 二值)", "*.pbm"),
                       ("PAM (P7, 带表头)", "*.pam"),
                       ("裸 RGB888", "*.rgb"),
                       ("JPEG 图片", "*.jpg"),
                       ("其它 (由 Pillow 决定)", "*.*")])
        if not path:
            return
        try:
            imageio.save_image(path, reply)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        self._status("回图(%s)已保存到 %s" % (name, path))
        if self.ai_stats:
            try:
                import json
                with open(os.path.splitext(path)[0] + "_stats.json", "w",
                          encoding="utf-8") as fh:
                    json.dump(extra, fh, ensure_ascii=False, indent=1)
            except OSError:
                pass

    def _show_left(self):
        if self.image is None:
            return
        self.photo_left = imageio.make_photo(tk, self.image)
        self.cv_left.itemconfigure(self.cv_img, image=self.photo_left)
        self._draw_roi()

    # ---- ROI：在左图上拖框；有框时 AI 只在框附近的裁剪窗口上算 ----
    @staticmethod
    def _clamp_xy(e):
        return (max(0, min(proto.IMG_WIDTH, e.x)), max(0, min(proto.IMG_HEIGHT, e.y)))

    def _ui_model(self):
        """当前选中的模型（proto.MODELS 的一项）。"""
        label = self.var_ai_model.get()
        return next((m for m in proto.MODELS if m["label"] == label), proto.MODELS[0])

    def _on_model_change(self):
        """换模型：门限 / 分辨率换成该模型的默认值；不支持的 ROI / 形态指标关掉；
        样片跟着换成该模型的（手动载入的图不动，见 _sync_sample_to_model）。"""
        m = self._ui_model()
        self.var_ai_thr.set(str(m["thr"]))
        self.var_ai_res.set(str(m["res"]))
        notes = []
        if not m["morph"] and self.var_f_morph.get():
            self.var_f_morph.set(False)
            notes.append("形态指标只对眼底血管有意义，已关闭")
        if not m["roi"] and self.roi is not None:
            self.roi = None
            self._draw_roi()
            notes.append("该模型在 %d×%d 上训练，不支持 ROI，已清除" % (m["res"], m["res"]))
        if not m.get("inter") and self.var_inter.get():
            self.var_inter.set(False)
            self.inter = None
            self._draw_clicks()
            notes.append("该任务没有点击纠错模型，已关闭点击纠错")
        self._sync_sample_to_model(m, notes)
        self._status("AI 模型：%s（门限 %d，分辨率 %d）%s"
                     % (m["label"], m["thr"], m["res"], "；" + "；".join(notes) if notes else ""))

    def _roi_press(self, e):
        if self._inter_ready():
            self._inter_click(e, True)
            return
        if not self._ui_model()["roi"]:
            self._status("当前模型（%s）不支持 ROI 局部推理" % self._ui_model()["label"])
            return
        self.roi_drag_start = self._clamp_xy(e)

    def _roi_drag(self, e):
        if self.roi_drag_start is None or self._inter_ready():
            return
        (x0, y0), (x1, y1) = self.roi_drag_start, self._clamp_xy(e)
        self._draw_roi((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))

    def _roi_release(self, e):
        if self.roi_drag_start is None or self._inter_ready():
            return
        (x0, y0), (x1, y1) = self.roi_drag_start, self._clamp_xy(e)
        self.roi_drag_start = None
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w >= ROI_MIN_SIZE and h >= ROI_MIN_SIZE:      # 太小的当作误点，保持原来的框
            self.roi = (min(x0, x1), min(y0, y1), w, h)
            if self._zoom_mag() > 1:
                self._status("已选框：「发送」在原图上按 %g 倍分块重新推理（右键或 Esc 清除）" % self._zoom_mag())
            else:
                self._status("已选 ROI：下一帧 AI 只算框附近（右键或 Esc 清除）")
        self._draw_roi()

    def _roi_clear(self):
        self.roi = None
        self.roi_drag_start = None
        self._draw_roi()
        self._status("已清除 ROI：下一帧恢复整图推理")

    def _draw_roi(self, box=None):
        """左图上画 ROI 框（黄）和板端实际计算的裁剪窗口（青色虚线）。"""
        self.cv_left.delete("roi")
        box = box or self.roi
        if box is None:
            self.var_roi_note.set("左键拖框选 ROI：AI 只算框附近，结果与整图一致")
            return
        x, y, w, h = box
        if w < 1 or h < 1:
            return
        mag = self._zoom_mag()
        if mag > 1 and self.orig_rgb is not None:            # 框选放大：画品红框，说明会在原图上重新推理
            import tile_engine as te
            self.cv_left.create_rectangle(x, y, x + w - 1, y + h - 1, outline="#ff3cf0", width=2, tags="roi")
            import bigwin
            h0, w0 = self.orig_rgb.shape[:2]
            m_inf, extra = te.effective_mag(mag, w0, bigwin.load_config()["cap"])
            s = te.scale_for_mag(m_inf, w0)
            (lw, lh), (W, H) = te.level_size(w0, h0, s)
            lb = te.box_to_level(self._roi_to_orig(box), s, W, H)
            n = len(te.plan(lb, W, H))
            self.var_roi_note.set("框 %d×%d → 原图 %.0f×%.0f → %.3g 倍层上 %d×%d、%d 块%s（「发送」= 在大图精查窗口重新推理）"
                                  % (w, h, w * w0 / 512.0, h * h0 / 512.0, m_inf, lb[2], lb[3], n,
                                     "，再插值放大 %.2f 倍显示（原图分辨率不够）" % extra if extra > 1 else ""))
            return
        cx, cy, cw, ch = proto.roi_crop(x, y, w, h)
        self.cv_left.create_rectangle(cx, cy, cx + cw - 1, cy + ch - 1, outline="#00e5ff",
                                      dash=(4, 3), tags="roi")
        self.cv_left.create_rectangle(x, y, x + w - 1, y + h - 1, outline="#ffd400",
                                      width=2, tags="roi")
        self.var_roi_note.set("ROI %d×%d @(%d,%d) → 板端算 %d×%d（占整图 %.1f%%）"
                              % (w, h, x, y, cw, ch,
                                 100.0 * cw * ch / (proto.IMG_WIDTH * proto.IMG_HEIGHT)))

    # ---- 框选放大（部分 C）：> 1 倍时框映射回原图，在大图精查窗口里按该倍数分块重新推理 ----
    def _set_orig(self, path, name):
        """记下原图（任意尺寸）。读不了（裸 RGB、内置图样）就用发出去的 512 帧本身。"""
        import numpy as np
        self.orig_name = name
        self.orig_rgb = None
        if path:
            try:
                # imageio 这条路认 .gz（STARE 样片是 im0001.ppm.gz）和 Netpbm 原生解码，
                # 比直接 PIL.open 覆盖面广；读不了自然回退到下面的 512 帧
                self.orig_rgb = imageio.load_native_array(path)
            except Exception:                              # noqa: BLE001 - 退回 512 帧
                self.orig_rgb = None
        if self.orig_rgb is None and self.image is not None:
            self.orig_rgb = np.frombuffer(self.image, np.uint8).reshape(proto.IMG_HEIGHT, proto.IMG_WIDTH, 3)

    def _zoom_mag(self):
        """框选放大的倍数（1 = 老 ROI）。"""
        v = self.var_zoom.get() if hasattr(self, "var_zoom") else ZOOM_1X
        return 1.0 if v == ZOOM_1X else float(v.split("×")[0])

    def _roi_to_orig(self, box):
        """512 帧上的框 -> 原图坐标（帧是原图直接拉伸到 512x512 的）。"""
        h0, w0 = self.orig_rgb.shape[:2]
        fx, fy = w0 / float(proto.IMG_WIDTH), h0 / float(proto.IMG_HEIGHT)
        x, y, w, h = box
        return (x * fx, y * fy, w * fx, h * fy)

    def _open_bigwin(self, box=None, mag=None):
        """大图精查窗口：box = 原图坐标的框（None = 只看总览），mag = 放大倍数。"""
        self._stop_repeat()
        if self.orig_rgb is None:
            self._status("先载入一张图")
            return None
        import bigwin
        win = getattr(self, "big_win", None)
        if uistyle.alive(win) and win.key == bigwin.rgb_key(self.orig_rgb):
            uistyle.bring_up(win.top)
            if box is not None:
                win.zoom_to(box, mag)
            return win
        if uistyle.alive(win):
            win.close()                                  # 换了图：旧窗口关掉（板上任务在途时等它回来再销毁）
        self.big_win = bigwin.BigWindow(self, self.orig_rgb, self.orig_name or "图像", box0=box, mag=mag)
        return self.big_win

    def _clear_right(self):
        self.photo_right = None
        self.cv_right.itemconfigure(self.cv_right_img, image="")
        self.cv_right.itemconfigure(self.cv_right_hint, state="normal")
        self.var_reply_note.set("")
        # 换了图就丢掉上一帧的 logit，免得新图配旧的热力图 / 叠加
        self.view_images = {}
        self.local_views = set()
        self.logit_plane = None
        self.heat_cache = None
        self.verify_note = ""
        self.frame_roi = None
        self._update_conf_note()

    def _on_repeat_toggle(self):
        self.repeat = self.var_repeat.get()
        self.done_times = []            # 实测帧率从这次打开连续模式重新算
        if not self.repeat and self.repeat_job is not None:
            self.root.after_cancel(self.repeat_job)    # 关掉时把排好的下一帧也撤掉
        self.repeat_job = None
        self._sync_send_btn()
        if self.repeat and not self.busy:
            self._on_send()

    def _on_send_button(self):
        """顶栏大按钮：平时是「发送」，连续发送时变成「停止」。"""
        if self.repeat:
            self.var_repeat.set(False)
            self._on_repeat_toggle()
        else:
            self._on_send()

    def _sync_send_btn(self):
        if self.repeat:
            self.btn_send.configure(text="停止 ■", state="normal", bg=C_STOP,
                                    activebackground=C_STOP)
        else:
            self.btn_send.configure(text="发送 ▶", state="disabled" if self.busy else "normal",
                                    bg=C_ACCENT, activebackground=C_ACCENT_D)

    def _set_busy(self, busy):
        self.busy = busy
        self._sync_send_btn()

    def _set_link(self, ok, text):
        self.lbl_link.configure(fg=C_GREEN if ok else C_STOP)
        self.var_link.set(text)

    # ---- 专项工作站子窗口：和主界面共用板子连接，所以先停掉连续发送；已开着就拿到前面 ----
    def _stop_repeat(self):
        if self.repeat:
            self.var_repeat.set(False)
            self._on_repeat_toggle()

    def _open_tool(self, attr, make):
        self._stop_repeat()
        win = getattr(self, attr, None)
        if uistyle.alive(win):
            uistyle.bring_up(win.top)
            return win
        try:
            win = make()
        except ImportError:                    # 缺 numpy / scipy / OpenCV：子窗口自己已经弹框说明
            return None
        setattr(self, attr, win)
        return win

    def _open_progwin(self):                   # PL 程序台（②）
        import progwin
        return self._open_tool("prog_win", lambda: progwin.ProgWindow(self))

    def _open_ctwin(self):                     # CT 整卷分割（⑤）：需要 numpy + scipy
        import ctwin
        return self._open_tool("ct_win", lambda: ctwin.CTWindow(self))

    def _open_echowin(self):                   # 实时心超工作站（③）：需要 numpy + scipy + OpenCV
        import echowin
        return self._open_tool("echo_win", lambda: echowin.EchoWindow(self))

    def _open_sequence(self):                  # 序列跟踪（F3）
        return self._open_tool("seq_win", lambda: sequence.SeqWindow(self))

    # ---- 点击纠错（F2）：左键 = 这里是目标，右键 = 这里不是；每次点击 FPGA 重算一次 ----
    def _inter_ready(self):
        return bool(self.var_inter.get() and self._ui_model().get("inter") and self.inter is not None)

    def _on_inter_toggle(self):
        m = self._ui_model()
        if self.var_inter.get() and not m.get("inter"):
            self.var_inter.set(False)
            self._status("当前任务（%s）没有点击纠错模型" % m["label"])
            return
        if self.var_inter.get():
            self.var_f_logit.set(True)
            self._status("点击纠错：先发送一帧拿到自动分割，然后在左图上左键点漏掉的、右键点多出的；Ctrl+Z 撤销")
        else:
            self.inter = None
            self._draw_clicks()

    def _right_click(self, e):
        if self._inter_ready():
            self._inter_click(e, False)
        else:
            self._roi_clear()

    def _inter_click(self, e, positive):
        if self.busy:
            self._status("上一帧还在算，稍等再点")
            return
        x, y = self._clamp_xy(e)
        clicks = self.inter.clicks + [(x, y, positive)]
        self._draw_clicks(clicks)
        self._on_send(inter=(interactive.compose(self.image, self.inter.prior, clicks), clicks))

    def _update_inter(self, images, ai_stats):
        """收到一帧后：纠错帧接到会话上；普通帧（自动分割）作为新一轮纠错的起点。"""
        if self.inflight_inter is not None and self.inter is not None:
            prior = interactive.prior_from_plane(self.logit_plane, self.frame_ai_thr)
            self.inter.push(prior, self.inflight_inter, images, ai_stats)
            self.var_show.set("overlay")
        elif self.var_inter.get() and self._ui_model().get("inter") and self.logit_plane is not None:
            prior = interactive.prior_from_plane(self.logit_plane, self.frame_ai_thr)
            self.inter = interactive.Session(prior, images, ai_stats)
        self.inflight_inter = None
        self._draw_clicks()

    def _inter_restore(self, state):
        if state is None:
            self._status("已经是自动分割的结果，没有可撤销的点击")
            return
        images, ai_stats = state
        self.reply_images, self.ai_stats = images, ai_stats
        self._load_logits(images)
        self._rebuild_views()
        self._render_right()
        self._draw_clicks()
        self._status("纠错：当前 %d 次点击（本地恢复，未重跑 FPGA）" % len(self.inter.clicks))

    def _inter_undo(self):
        if self.inter is not None and not self.busy:
            self._inter_restore(self.inter.undo())

    def _inter_reset(self):
        if self.inter is not None and not self.busy:
            self._inter_restore(self.inter.reset())

    def _draw_clicks(self, clicks=None):
        self.cv_left.delete("clicks")
        if clicks is None:
            clicks = self.inter.clicks if self.inter is not None else []
        for x, y, positive in clicks:
            color = "#00e000" if positive else "#ff3030"
            self.cv_left.create_oval(x - 6, y - 6, x + 6, y + 6, outline=color, width=2, tags="clicks")
            self.cv_left.create_line(x - 4, y, x + 4, y, fill=color, width=2, tags="clicks")
            if positive:
                self.cv_left.create_line(x, y - 4, x, y + 4, fill=color, width=2, tags="clicks")

    def _on_send(self, inter=None):
        """发一帧。inter = (要发的合成图, 点击列表) 时发点击纠错帧（交互模型、只回掩膜 + logit）。"""
        if self.busy or self.image is None:
            return
        if inter is None and self.roi is not None and self._zoom_mag() > 1:
            self._open_bigwin(self._roi_to_orig(self.roi), self._zoom_mag())    # 框选放大 > 1 倍：分块重新推理
            return
        try:
            host = self.var_host.get().strip()
            port = int(self.var_port.get().strip())
        except ValueError:
            messagebox.showerror("参数错误", "端口必须是数字")
            return

        self._set_busy(True)
        self._status("正在发送并等待处理后的回图…")
        self.var_total.set("…")
        self.var_detail.set("")

        # 从下拉框取出模式编号（形如 "2 边缘增强"）
        mode = self._current_mode()

        flags = 0
        if self.var_f_proc.get():
            flags |= proto.FLAG_ECHO_PROC
        if self.var_f_mask.get():
            flags |= proto.FLAG_ECHO_MASK
        if self.var_f_over.get():
            flags |= proto.FLAG_ECHO_OVER
        if self.var_f_stats.get():
            flags |= proto.FLAG_STATS
        if self.var_f_logit.get():
            # 要 logit 必须同时要统计块：上位机靠它的 version 判断板端认不认 b5
            flags |= proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS
            self.var_f_stats.set(True)
        if self.var_f_morph.get():
            # 形态指标块跟在统计块后面，板端也只在带了统计块时才发
            flags |= proto.FLAG_MORPH | proto.FLAG_STATS
            self.var_f_stats.set(True)

        roi = self.roi
        if roi is not None and self.board_stat_version is not None \
                and self.board_stat_version < proto.AI_STAT_VERSION_ROI:
            roi = None                     # 老板端不认 v5 头，发了会被当成坏帧
            self._status("板端程序不支持 ROI（统计块 version=%d），本帧按整图推理"
                         % self.board_stat_version)
        if roi is not None:
            # ROI 帧必须让板子跑 AI，并带统计块（里面有板端实际计算的裁剪尺寸）
            if not flags & (proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_OVER | proto.FLAG_ECHO_LOGIT):
                flags |= proto.FLAG_ECHO_OVER
                self.var_f_over.set(True)
            flags |= proto.FLAG_STATS
            self.var_f_stats.set(True)
        image = self.image
        self.inflight_inter = None
        if inter is not None:
            image, self.inflight_inter = inter
            flags = proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS
            roi = None
        elif self.var_inter.get() and self._ui_model().get("inter"):
            flags |= proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS     # 自动结果要当纠错的起点
        self.inflight_roi = roi
        if flags == 0:
            self._status("「回传内容」至少要勾一项（否则板子不会回任何东西）")
            self._set_busy(False)
            return

        ai_thr = self._ui_ai_thr()
        self.inflight_ai_thr = ai_thr      # 本帧板端用的门限(同一时刻只有一帧在途)
        ai_src = (proto.AIPARAM_SRC_PROC if self.var_ai_src.get() == "预处理结果"
                  else proto.AIPARAM_SRC_RAW)
        try:
            ai_res = int(self.var_ai_res.get())
        except ValueError:
            ai_res = 512
        model = self._ui_model()
        if inter is not None:
            model = proto.MODELS[model["inter"]]
            ai_thr, ai_res = model["thr"], model["res"]
            self.inflight_ai_thr = ai_thr
        self.inflight_model = model["index"]
        aiparam = proto.make_aiparam(ai_src, ai_thr, ai_res, model["index"])

        # 阈值分割的门限(只在 v4 控制头里带; 其它模式板子会忽略)
        try:
            thr = int(self.var_thr.get())
        except ValueError:
            thr = proto.CTRL_THR_DEFAULT
        thr = max(proto.CTRL_THR_MIN, min(proto.CTRL_THR_MAX, thr))
        self.var_thr.set(str(thr))
        self.last_thr = thr

        self.inflight_key = (hash(image), ai_src, mode if ai_src == proto.AIPARAM_SRC_PROC
                             else None)    # 哪张图、什么输入：判断能不能拿整图结果核对 ROI
        threading.Thread(target=self._worker,
                         args=(host, port, image, mode, flags, aiparam, thr, roi),
                         daemon=True).start()

    # ---- 后台线程：只做网络, 结果丢进 queue ----
    def _worker(self, host, port, image, mode, flags, aiparam, thr, roi):
        try:
            # round_trip 内部自己建连(板端每连接可连收多帧), 这里只更新目标
            self.link.host = host
            self.link.port = port
            images, timings, rowstats, ai_stats = self.link.round_trip(
                image, mode, flags=flags, aiparam=aiparam, thr=thr, roi=roi)
            self.queue.put(("ok", images, timings, rowstats, ai_stats, mode, flags))
        except Exception as e:
            if roi is not None and self.board_stat_version is None:
                e = "%s（发的是 ROI 帧；如果板端还是旧程序，它不认 v5 头。先清除 ROI 发一帧整图）" % e
            self.queue.put(("err", str(e)))

    # ---- GUI 线程：轮询 queue ----
    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "ok":
                    self._on_result(item[1], item[2], item[3], item[4], item[5], item[6])
                else:
                    self._on_error(item[1])
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _on_error(self, msg):
        self.var_total.set("—")
        self.var_fps.set("—")
        self.var_fps_note.set("")
        self.done_times = []
        self.var_detail.set("")
        self._status("出错：%s" % msg)
        self.link.close()
        self._set_link(False, "连接失败")
        if self.repeat:
            self.repeat = False
            self.var_repeat.set(False)
        self._set_busy(False)

    def _pick_show(self, wanted):
        """决定右图显示哪一幅: auto = 按 叠加/掩膜/预处理图/热力图 的优先级取第一幅有的
        （AI 结果优先；只勾了「预处理图」时显示预处理图）。"""
        if wanted != "auto" and wanted in self.view_images:
            return wanted
        for name in ("overlay", "mask", "proc", "heat"):
            if name in self.view_images:
                return name
        return None

    def _render_right(self):
        """按「显示」的选择把最近一帧的图画到右边；返回实际显示的图名，没有图时返回 None。"""
        name = self._pick_show(self.var_show.get())
        if name is None:
            self.var_reply_note.set("没有可显示的回传图")
            return None
        reply = self.view_images[name]
        self.last_reply = reply
        self.showing = name
        if self.frame_roi is not None:          # ROI 帧：框外没有算，把框描出来
            reply = self._with_box(reply, self.frame_roi)
        self.photo_right = imageio.make_photo(tk, reply)
        self.cv_right.itemconfigure(self.cv_right_img, image=self.photo_right)
        self.cv_right.itemconfigure(self.cv_right_hint, state="hidden")
        if name in self.local_views:
            src = "本地按门限 %d 重算，未重跑 FPGA" % self._ui_ai_thr()
        elif name in ("heat", "uncertain"):
            src = "由板端 logit 本地生成"
        else:
            src = "板端回传"
        self.var_reply_note.set("%s · %s" % (SHOW_LABELS.get(name, name), src))
        return name

    # ---- 置信度：板端回传 logit 后，换门限只在本地重算 ----
    def _ui_ai_thr(self):
        """界面上的 AI 判定门限，夹到 int8 范围。"""
        try:
            return max(-127, min(127, int(float(self.var_ai_thr.get().strip()))))
        except ValueError:
            return proto.AIPARAM_THR_DEFAULT

    def _ui_band(self):
        try:
            return max(0, min(127, int(self.var_band.get().strip())))
        except ValueError:
            return UNSURE_BAND_DEFAULT

    def _load_logits(self, images):
        """从本帧回传里取出 logit 平面和本地叠加用的底图；没有 logit 就清空。"""
        self.heat_cache = None
        self.verify_note = ""
        if "logit" not in images:
            self.logit_plane = None
            return
        self.logit_plane = confidence.logit_plane(images["logit"])
        self.frame_ai_thr = self.inflight_ai_thr
        # 概率换算按这一帧实际用的模型（v5 板端在统计块里报；老板端只有眼底）
        st_model = (self.ai_stats or {}).get("model")
        self.frame_model = proto.model_by_index(st_model)
        confidence.set_scale(self.frame_model["scale"])
        self.frame_ai_res = (self.ai_stats or {}).get("ai_res") or 512
        # 板端叠加的底图是预处理结果（直通模式时就是原图）；没回传预处理图时只能退用原图
        if "proc" in images:
            self.logit_base, self.logit_base_note = images["proc"], ""
        else:
            self.logit_base = self.image
            self.logit_base_note = "底图用原图（未回传预处理图）"
        # 自检：板端门限下本地复算的掩膜 / 叠加图应与板端回传的逐字节一致
        checks = []
        if "mask" in images:
            checks.append(confidence.mask_rgb(self.logit_plane, self.frame_ai_thr)
                          == images["mask"])
        if "overlay" in images and "proc" in images:
            checks.append(confidence.overlay_rgb(images["proc"], self.logit_plane,
                                                 self.frame_ai_thr) == images["overlay"])
        if checks:
            self.verify_note = ("与板端逐字节一致 ✓" if all(checks)
                                else "与板端回传不一致 ✗")

    def _show_morph(self, flags, ai_stats):
        """板端算的血管形态指标。长度 / 管径换算成 512 尺度的像素（低分辨率档按网格放大）。"""
        m = (ai_stats or {}).get("morph")
        if m is None:
            if flags & proto.FLAG_MORPH and ai_stats is not None \
                    and ai_stats["version"] < proto.AI_STAT_VERSION_MORPH:
                self.var_morph.set("形态：板端程序不支持（统计块 version=%d）" % ai_stats["version"])
            elif not flags & proto.FLAG_MORPH:
                self.var_morph.set("形态：勾「形态指标」后由板端计算")
            else:
                self.var_morph.set("形态：本帧板端没有算出")
            return
        f = 1.0 if self.frame_roi is not None else proto.IMG_WIDTH / max(1, m["w"])
        where = ("ROI %d×%d" % (m["w"], m["h"]) if self.frame_roi is not None
                 else "%d×%d" % (m["w"], m["h"]))
        self.var_morph.set(
            "形态(%s, 门限 %d)：总长 %.0f px  管径 %.2f px  分支 %d  端点 %d  段 %d  迂曲度 %.3f  "
            "分形维数 %.3f  FOV 密度 %.1f%%(%s)  PS %.1f ms"
            % (where, self.frame_ai_thr, m["length"] * f, m["caliber"] * f, m["branches"],
               m["endpoints"], m["segments"], m["tortuosity"], m["fractal"], m["density_pct"],
               "/".join("%.1f" % q for q in m["quad_pct"]), m["run_us"] / 1000.0))

    def _check_roi_against_full(self):
        """实板核对“ROI 局部推理 == 整图推理”：同一张图、同样输入的上一帧整图 logit 留作参照，
        ROI 帧回来后在框内逐字节比。没回传 logit（没勾「置信度」）时没法比，只给提示。"""
        self.roi_verify_note = ""
        if self.logit_plane is None:
            if self.frame_roi is not None:
                self.roi_verify_note = "勾「置信度」并先发一帧整图，可核对 ROI 与整图逐值一致"
            return
        if self.frame_roi is None:
            if self.frame_ai_res == proto.IMG_WIDTH:       # 只有全分辨率整图能当参照
                self.full_ref = (self.inflight_key, self.logit_plane)
            return
        if self.full_ref is None or self.full_ref[0] != self.inflight_key:
            self.roi_verify_note = "先对同一张图发一帧整图（不框 ROI），才能核对 ROI 与整图一致"
            return
        n, bad = confidence.roi_compare(self.logit_plane, self.full_ref[1], self.frame_roi)
        self.roi_verify_note = ("ROI 与整图推理逐值一致 ✓（%d 像素）" % n if bad == 0
                                else "ROI 与整图推理有 %d/%d 像素不同 ✗" % (bad, n))

    @staticmethod
    def _with_box(rgb, box, color=(255, 212, 0)):
        """在 RGB 图上描一个 2 像素宽的框（只用于显示）。"""
        x, y, w, h = box
        out = bytearray(rgb)
        px = bytes(color)
        W = proto.IMG_WIDTH
        for yy in (y, y + 1, y + h - 2, y + h - 1):
            if 0 <= yy < proto.IMG_HEIGHT:
                out[(yy * W + x) * 3:(yy * W + x + w) * 3] = px * w
        for yy in range(y, y + h):
            for xx in (x, x + 1, x + w - 2, x + w - 1):
                if 0 <= xx < W:
                    out[(yy * W + xx) * 3:(yy * W + xx) * 3 + 3] = px
        return bytes(out)

    def _rebuild_views(self):
        """回传图 + 由 logit 本地生成的视图。门限与板端相同时，掩膜 / 叠加用板端原图。"""
        views = {k: v for k, v in self.reply_images.items() if k != "logit"}
        self.local_views = set()
        plane = self.logit_plane
        if plane is not None:
            thr, band = self._ui_ai_thr(), self._ui_band()
            same = (thr == self.frame_ai_thr)
            if not same or "mask" not in views:
                views["mask"] = confidence.mask_rgb(plane, thr)
                self.local_views.add("mask")
            if not same or "overlay" not in views:
                views["overlay"] = confidence.overlay_rgb(self.logit_base, plane, thr)
                self.local_views.add("overlay")
            if self.heat_cache is None:
                self.heat_cache = confidence.heat_rgb(plane)
            views["heat"] = self.heat_cache
            views["uncertain"] = confidence.uncertain_rgb(self.logit_base, plane, thr, band)
        self.view_images = views
        self._update_conf_note()

    def _update_conf_note(self):
        thr, band = self._ui_ai_thr(), self._ui_band()
        head = "门限 %d ≈ 概率 %.2f" % (thr, confidence.probability(thr))
        if self.logit_plane is None:
            self.var_conf_note.set(head + "\n勾「置信度」后，拖门限只在本地重算")
            return
        st = confidence.stats(self.logit_plane, thr, band, self.frame_ai_res, roi=self.frame_roi)
        target = getattr(self, "frame_model", proto.MODELS[0])["target"]
        parts = [head, "%s %.2f%%" % (target, st["vessels_pct"]), "不确定 %.2f%%" % st["unsure_pct"]]
        if thr != self.frame_ai_thr:
            parts.append("本地重算（板端本帧 %d）" % self.frame_ai_thr)
        elif self.verify_note:             # 一致性只对板端本帧的门限成立
            parts.append(self.verify_note)
        if self.logit_base_note:
            parts.append(self.logit_base_note)
        self.var_conf_note.set("  |  ".join(parts))

    def _schedule_conf(self):
        """门限 / 不确定带变化：合并连续的拖动事件，空闲时重算一次。"""
        if self.conf_job is not None:
            self.root.after_cancel(self.conf_job)
        self.conf_job = self.root.after(30, self._on_conf_change)

    def _on_conf_change(self):
        self.conf_job = None
        if self.logit_plane is None:
            self._update_conf_note()
            return
        self._rebuild_views()
        if self.showing in AI_VIEWS or self.var_show.get() in AI_VIEWS:
            self._render_right()

    def _update_fps(self, total_ms):
        """连续模式：按最近几帧的完成时刻实测帧率；单帧：按往返耗时折算。"""
        if self.repeat:
            self.done_times.append(time.perf_counter())
            del self.done_times[:-(FPS_WINDOW + 1)]
        else:
            self.done_times = []
        if len(self.done_times) >= 2:
            n = len(self.done_times) - 1
            span = max(self.done_times[-1] - self.done_times[0], 1e-6)
            self.var_fps.set("%.2f fps" % (n / span))
            self.var_fps_note.set("连续实测：近 %d 帧，含发送间隔 %s ms"
                                  % (n, self.var_interval.get().strip()))
        else:
            self.var_fps.set("%.2f fps" % (1000.0 / max(total_ms, 1e-3)))
            self.var_fps_note.set("按单帧往返折算（勾「连续」可实测）")

    def _on_result(self, images, tim, rowstats, ai_stats, mode, flags):
        self.reply_images = images
        self.reply_stats = rowstats
        self.ai_stats = ai_stats
        if ai_stats is not None:
            self.board_stat_version = ai_stats["version"]
        self.frame_ai_thr = self.inflight_ai_thr          # 本帧板端用的门限（形态指标也按它算）
        # 板端真的按 ROI 算了（统计块里有裁剪尺寸），这一帧才算 ROI 帧
        self.frame_roi = (self.inflight_roi
                          if ai_stats is not None and ai_stats.get("crop_w") else None)

        self._load_logits(images)
        self._update_inter(images, ai_stats)
        self._check_roi_against_full()
        self._rebuild_views()
        name = self._render_right()

        self.var_total.set("%.1f ms" % tim["total_ms"])
        self._update_fps(tim["total_ms"])
        if ai_stats is None:
            self.var_infer.set("—")
            self.var_infer_note.set("勾选「数值统计」后显示")
        elif ai_stats["ai_us"] == 0:
            self.var_infer.set("—")
            self.var_infer_note.set("本帧板端没有运行 AI")
        elif self.frame_roi is not None:
            self.var_infer.set("%.1f ms" % (ai_stats["ai_us"] / 1000.0))
            self.var_infer_note.set("ROI：只算 %d×%d（占整图 %.1f%%），含搬运与计算"
                                    % (ai_stats["crop_w"], ai_stats["crop_h"],
                                       100.0 * ai_stats["crop_w"] * ai_stats["crop_h"]
                                       / (proto.IMG_WIDTH * proto.IMG_HEIGHT)))
        else:
            self.var_infer.set("%.1f ms" % (ai_stats["ai_us"] / 1000.0))
            self.var_infer_note.set("%d×%d，含 PS 搬运与 PL 计算"
                                    % (ai_stats["ai_res"], ai_stats["ai_res"]))
        st = rowstats.get(name, {}) if name else {}
        self.var_detail.set(
            "模式 %d %s  |  flags=0x%04X %s  |  阈值 %d\n发送 %.1f ms  |  回传 %.1f ms  |  行 %s  |  CRC错 %s"
            % (mode, proto.MODE_NAMES.get(mode, "?"), flags,
               "+".join(proto.expected_images(flags, ai_stats)) or "无图", self.last_thr,
               tim["send_ms"], tim["reply_ms"],
               ("%d/%d" % (st["rows_ok"], proto.PKT_COUNT)) if st else "—",
               st.get("crc_errors", "—")))

        self.hist.append(tim["total_ms"])
        if len(self.hist) > 1000:
            del self.hist[:len(self.hist) - 1000]
        n = len(self.hist)
        self.var_stats.set(
            "统计(n=%d)：本次 %.1f ms   最小 %.1f   最大 %.1f   平均 %.1f"
            % (n, tim["total_ms"], min(self.hist), max(self.hist),
               sum(self.hist) / n))
        self.var_hist.set("最近 %d 次：%s"
                          % (min(n, HISTORY_MAX),
                             "  ".join("%.0f" % v for v in self.hist[-HISTORY_MAX:])))

        if ai_stats is not None:
            self.var_ai.set(
                "AI(%s，%s)：%s %d/%d = %.1f%%   连通域 %d   logit[%d..%d]   板端整网 %d µs   灰度[%d..%d]"
                % (proto.model_by_index(ai_stats.get("model"))["label"],
                   "ROI %d×%d" % self.frame_roi[2:] if self.frame_roi
                   else "%d×%d" % (ai_stats["ai_res"], ai_stats["ai_res"]),
                   proto.model_by_index(ai_stats.get("model"))["target"],
                   ai_stats["vessels"],
                   ai_stats["total"], ai_stats["vessels_pct"], ai_stats["components"],
                   ai_stats["logit_min"], ai_stats["logit_max"], ai_stats["ai_us"],
                   ai_stats["proc_min"], ai_stats["proc_max"]))
            self.var_target.set("%.2f%%" % ai_stats["vessels_pct"])
            self.var_target_note.set("%s · 连通域 %d"
                                     % (proto.model_by_index(ai_stats.get("model"))["target"],
                                        ai_stats["components"]))
        else:
            self.var_ai.set("AI：本帧没要统计（勾上「数值统计」就能看到）")
            self.var_target.set("—")
            self.var_target_note.set("勾选「数值统计」后显示")
        self._show_morph(flags, ai_stats)

        self._set_link(True, "在线")
        self._status("完成" + ("  ·  " + self.roi_verify_note if self.roi_verify_note else ""))
        self._set_busy(False)

        if self.repeat:
            try:
                iv = max(0, int(self.var_interval.get()))
            except ValueError:
                iv = 200
            self.repeat_job = self.root.after(iv, self._on_send)

        if self.selftest_path is not None:
            try:
                if self.last_reply is not None:
                    imageio.save_image(self.selftest_path, self.last_reply)
                    print("GUI_SELFTEST saved %s (showing=%s, %d bytes)"
                          % (self.selftest_path, self.showing, len(self.last_reply)))
                print("GUI_SELFTEST flags=0x%04X images=%s ai_vessels=%s ai_comps=%s"
                      % (flags, sorted(images),
                         (self.ai_stats or {}).get("vessels"),
                         (self.ai_stats or {}).get("components")))
            except Exception as e:                        # noqa: BLE001 - selftest
                print("GUI_SELFTEST save failed:", e)
            self.root.after(300, self.root.destroy)


def _enable_dpi_awareness():
    """Windows 上让 tkinter 在高 DPI 下不发虚。"""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main():
    argv = sys.argv[1:]
    auto = "--autosend" in argv           # 冒烟测试/演示: 起来后自动发一帧
    img_path = None                       # 可选: 启动时预载一张图片
    selftest_path = None                  # 可选: 收到第一帧后存图并退出(自检)
    preset_flags = None
    preset_ai_src = None
    preset_ai_thr = None
    preset_ai_res = None
    preset_thr = None
    preset_mode = None
    if "--image" in argv:
        i = argv.index("--image")
        if i + 1 < len(argv):
            img_path = argv[i + 1]
            del argv[i:i + 2]
    if "--selftest" in argv:
        i = argv.index("--selftest")
        if i + 1 < len(argv):
            selftest_path = argv[i + 1]
            del argv[i:i + 2]
        auto = True
    if "--flags" in argv:
        i = argv.index("--flags")
        preset_flags = imagelink._parse_flags(argv[i + 1])
        del argv[i:i + 2]
    if "--ai-src" in argv:
        i = argv.index("--ai-src")
        preset_ai_src = (proto.AIPARAM_SRC_PROC if argv[i + 1] == "proc"
                         else proto.AIPARAM_SRC_RAW)
        del argv[i:i + 2]
    if "--ai-thr" in argv:
        i = argv.index("--ai-thr")
        preset_ai_thr = int(argv[i + 1])
        del argv[i:i + 2]
    if "--ai-res" in argv:
        i = argv.index("--ai-res")
        preset_ai_res = int(argv[i + 1])
        del argv[i:i + 2]
    if "--thr" in argv:
        i = argv.index("--thr")
        preset_thr = int(argv[i + 1])
        del argv[i:i + 2]
    if "--mode" in argv:
        i = argv.index("--mode")
        preset_mode = int(argv[i + 1])
        del argv[i:i + 2]
    hold = 0
    if "--hold" in argv:
        i = argv.index("--hold")
        if i + 1 < len(argv):
            hold = int(argv[i + 1])
            del argv[i:i + 2]
    argv = [a for a in argv if not a.startswith("--")]

    host = argv[0] if len(argv) > 0 else DEFAULT_HOST
    port = int(argv[1]) if len(argv) > 1 else DEFAULT_PORT

    if proto.SELF_CHECK_ERRORS:
        print("proto self-check FAILED:", proto.SELF_CHECK_ERRORS)

    _enable_dpi_awareness()
    root = tk.Tk()
    app = App(root, host, port, selftest_path=selftest_path,
              preset_flags=preset_flags, preset_ai_src=preset_ai_src,
              preset_ai_thr=preset_ai_thr, preset_ai_res=preset_ai_res,
              preset_thr=preset_thr, preset_mode=preset_mode)
    if img_path:
        root.after(200, lambda: app._load_path(img_path))
    if auto:
        root.after(1500, app._on_send)
    if selftest_path is not None:
        # 看门狗: 自检模式下 25 秒必退, 免得链路不通时把窗口挂在那儿
        root.after(25000, lambda: (print("GUI_SELFTEST timeout"), root.destroy()))
    if hold > 0:
        root.after(hold * 1000, root.destroy)     # 演示/截图用: 停留 N 秒后自己退出
    root.mainloop()


if __name__ == "__main__":
    main()
