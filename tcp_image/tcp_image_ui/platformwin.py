# -*- coding: utf-8 -*-
"""platformwin.py —— 心超工作站的「平台扩展」窗口（设计报告第 9 节：权重即程序）。

同一比特流、同一颗 PL U-Net 引擎，换一套 INT8 权重（约 34 KB）就是另一个程序，不重新综合、不改服务端。
窗口与心超工作站同一套外观：标题栏 + 程序页签 + 内容 + 状态栏（常驻声明 + 本页数据出处）。

    眼底血管（512 档）/ 皮肤镜病灶（128 档）  app.App 嵌入（单幅图：预处理 7 种模式、AI、ROI、置信度、形态、大图精查）
    CT 脾脏整卷                                ctwin.CTWindow 嵌入（板上两阶段分割 + 三维 + 脾脏测量）
    PL 程序台                                  progwin.ProgWindow 嵌入（学出来的模型 + 编译出来的算子串成板上流水线）

与心超工作站在同一个进程里（不再另起 app.py 进程）：共用一条板子连接、地址和 busy 互斥（板端一次只服务一个连接），
板上 DP 工作站也能直接打开（板上另起的进程装不上 tkinter 兼容层）。关窗口只是藏起来，再开时结果都在。
仅用于工程教学与算法演示，不作为临床诊断依据。
"""

import tkinter as tk

import uistyle
from uistyle import C_ACCENT, C_BG, C_BORDER, C_CARD, C_MUTED, C_TEXT, C_WARN

TITLE = "平台扩展 · 权重即程序"
SUBTITLE = "同一比特流、同一颗 PL U-Net 引擎；每套 INT8 权重约 34 KB，换程序不重新综合、不改服务端"
FONT_APP = ("Microsoft YaHei UI", 15, "bold")
FONT_TAB = ("Microsoft YaHei UI", 11, "bold")
# (键, 页签名, 页, 程序说明 dict)；页 = "image"（单幅图 App）/ "ct" / "prog"
PROGRAMS = (
    ("fundus", "眼底血管", "image",
     dict(model="fundus", group="fundus", sample="im0002", title="眼底血管分割（512 档，程序 0）",
          note="STARE / DRIVE · 视野内 AUC 0.978（开发验证）· 512 档整网 292.8 ms，比同板 4 核 NEON 快 1.93 倍",
          desc="单幅眼底图：PL 预处理 7 种模式 + U-Net 血管分割；框选 ROI / 大图精查、置信度热力图、形态指标",
          data="示例图来自 STARE 公开数据集")),
    ("skin", "皮肤镜病灶", "image",
     dict(model="skin", group="skin", sample="ISIC_0011336", title="皮肤镜病灶分割（128 档，程序 1）",
          note="ISIC 2016 · 官方测试集 379 张 Jaccard 0.818 · 板上输出与离线逐值一致",
          desc="单幅皮肤镜图：同一颗引擎换一套权重分割病灶；同屏原图 / 掩膜 / 叠加 / 热力图",
          data="示例图 ISIC_0011336 / ISIC_0011092 来自 ISIC 2016 官方测试集（CC-0）")),
    ("ct", "CT 脾脏整卷", "ct",
     dict(title="CT 脾脏整卷", desc="整卷 NIfTI 上传：板上两阶段分割（程序 14 / 15）、三维最大连通块、体积与脾脏测量、三维表面",
          data="CT 来自 MSD Task09 Spleen（CC BY-SA 4.0）")),
    ("prog", "PL 程序台", "prog",
     dict(title="PL 程序台", desc="学出来的模型 + 编译出来的算子（中值 / Sobel / 线检测 …）最多 3 个串成板上流水线",
          data="图源为单幅图页当前图或快照模式的序列")),
)


class PlatformWindow(object):
    """heart = heartapp.HeartApp（提供 root / link / var_host / var_port / busy / status / extra_tools）。"""

    def __init__(self, heart):
        self.heart = heart
        self.top = tk.Toplevel(heart.root)
        self.top.title("%s — 床旁超声心动图实时全心功能评估系统" % TITLE)
        self.top.configure(bg=C_BG)
        uistyle.setup_style(self.top)
        self.pages, self.app, self.cur = {}, None, None
        self.var_status = tk.StringVar(value="就绪")
        self.var_note = tk.StringVar(value=uistyle.DISCLAIMER)
        self._build()
        self.top.protocol("WM_DELETE_WINDOW", self.hide)
        self.switch("fundus")

    # 页里的 App / CTWindow / ProgWindow 按 app 接口调 status()，经这里进平台窗口自己的状态栏
    def status(self, msg):
        self.var_status.set(msg)

    def __getattr__(self, name):                    # link / var_host / var_port / busy / extra_tools 都转给心超工作站
        if name in ("link", "var_host", "var_port", "extra_tools", "root"):
            return getattr(self.heart, name)
        raise AttributeError(name)

    @property
    def busy(self):
        return self.heart.busy

    @busy.setter
    def busy(self, v):
        self.heart.busy = v

    def _build(self):
        t = self.top
        bar = tk.Frame(t, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        bar.grid(row=0, column=0, sticky="ew")
        logo = tk.Canvas(bar, width=30, height=30, bg=C_CARD, highlightthickness=0)
        logo.pack(side="left", padx=(16, 8), pady=10)
        for i in range(3):                           # 3x3 小方块 = 同一颗引擎上的多个程序
            for j in range(3):
                logo.create_rectangle(3 + 9 * i, 3 + 9 * j, 10 + 9 * i, 10 + 9 * j, outline="",
                                      fill=C_ACCENT if (i + j) % 2 == 0 else "#93c5fd")
        uistyle.label(bar, TITLE, font=FONT_APP).pack(side="left")
        uistyle.label(bar, SUBTITLE, font=uistyle.FONT_SMALL, fg=C_MUTED).pack(side="left", padx=(12, 0), pady=(6, 0))
        tools = tk.Frame(bar, bg=C_CARD)
        tools.pack(side="right", padx=(0, 16))
        uistyle.label(tools, var=self.heart.var_link, fg=C_MUTED).pack(side="left", padx=(0, 10))
        tk.Button(tools, text="← 返回心超工作站", font=uistyle.FONT_HEAD, fg="white", bg=C_ACCENT,
                  activebackground=uistyle.C_ACCENT_D, activeforeground="white", relief="flat", bd=0,
                  padx=16, pady=5, cursor="hand2", command=self.hide).pack(side="left")

        mb = tk.Frame(t, bg=C_BG)
        mb.grid(row=1, column=0, sticky="ew", padx=12, pady=(10, 0))
        seg = tk.Frame(mb, bg=C_BORDER, padx=1, pady=1)
        seg.pack(side="left")
        self.tabs = {}
        for key, label, _, _ in PROGRAMS:
            b = tk.Button(seg, text=label, font=FONT_TAB, relief="flat", bd=0, padx=22, pady=6, cursor="hand2",
                          command=lambda k=key: self.switch(k))
            b.pack(side="left", padx=(0 if not self.tabs else 1, 0))
            self.tabs[key] = b
        self.var_desc = tk.StringVar(value="")
        uistyle.label(mb, var=self.var_desc, fg=C_MUTED, bg=C_BG, anchor="w").pack(side="left", padx=(14, 0))

        self.body = tk.Frame(t, bg=C_BG)
        self.body.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        sbar = tk.Frame(t, bg=uistyle.C_BAR)
        sbar.grid(row=3, column=0, sticky="ew")
        tk.Label(sbar, textvariable=self.var_note, font=uistyle.FONT_SMALL, fg=C_WARN, bg=uistyle.C_BAR, padx=12,
                 pady=4).pack(side="right")
        tk.Label(sbar, textvariable=self.var_status, font=uistyle.FONT_SMALL, anchor="w", fg=C_TEXT, width=1,
                 bg=uistyle.C_BAR, padx=12, pady=4).pack(side="left", fill="x", expand=True)
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(2, weight=1)

    def _page(self, kind):
        """页第一次用到时才建（CT 要 numpy + scipy；PL 程序台要单幅图页当图源，所以先建单幅图页）。"""
        if kind in self.pages:
            return self.pages[kind]
        if kind == "image":
            import app as appmod
            frame = tk.Frame(self.body, bg=C_BG)
            self.app = appmod.App(self.top, self.heart.link.host, self.heart.link.port, parent=frame, shared=self)
            snap = self.heart.views.get("snapshot")
            if snap is not None:                     # PL 程序台的「序列窗口前/中/后三帧」= 快照模式的序列
                self.app.seq_win = snap
            page = frame
        elif kind == "ct":
            import ctwin
            self._page("image")
            self.ct_win = ctwin.CTWindow(self.app, parent=self.body)
            page = self.ct_win.frame
        else:
            import progwin
            self._page("image")
            self.prog_win = progwin.ProgWindow(self.app, parent=self.body)
            page = self.prog_win.frame
        self.pages[kind] = page
        return page

    def switch(self, key):
        _, label, kind, prog = next(p for p in PROGRAMS if p[0] == key)
        if not self.heart.busy:                      # 换页先放掉共用连接：单幅图页发送前不重连，别给它一个板端已断开的
            self.heart.link.close()
        try:
            page = self._page(kind)
        except ImportError as e:
            self.status("这一页需要 numpy + scipy（%s）" % e)
            return
        for k, p in self.pages.items():
            if p is not page:
                p.grid_forget()                      # 板上 tkinter 兼容层没有 grid_remove
        page.grid(row=0, column=0, sticky="nsew")
        if self.app is not None and self.app.repeat and kind != "image":
            self.app._stop_repeat()                  # 别的页要用板子：先停掉单幅图的连续发送
        if kind == "image" and self.app is not None and self.app.program is not prog:
            self._set_program(prog)
        for k, b in self.tabs.items():
            on = k == key
            b.configure(bg=C_ACCENT if on else C_CARD, fg="white" if on else C_TEXT,
                        activebackground=uistyle.C_ACCENT_D if on else uistyle.C_EMPTY,
                        activeforeground="white" if on else C_TEXT)
        self.cur = key
        self.var_desc.set(prog["desc"])
        self.var_note.set("%s｜%s" % (uistyle.DISCLAIMER, prog["data"]))

    def _set_program(self, prog):
        """单幅图页换程序：标题 / 说明、AI 模型（门限与分辨率随模型）、示例图。"""
        import imageio
        import proto
        a = self.app
        a.program = prog
        a.var_prog_title.set(prog["title"])
        a.var_prog_note.set(prog["note"])
        m = next((m for m in proto.MODELS if m["name"] == prog["model"]), None)
        if m is not None:
            a.var_ai_model.set(m["label"])          # trace -> _on_model_change：门限 / 分辨率 / ROI / 形态
        pick = None
        if hasattr(imageio, "first_sample"):        # 交付包 / 板上版 imageio：samples/<组>/ 按模型分组
            pick = imageio.first_sample(prog.get("group") or "")
        elif prog.get("sample") in imageio.figure_names():
            pick = prog["sample"]
        if pick is not None:
            a._load_pattern(pick)
        a.var_f_over.set(True)
        a.var_f_stats.set(True)

    def show(self):
        uistyle.bring_up(self.top)

    def hide(self):
        if self.app is not None:
            self.app._stop_repeat()
        self.top.withdraw()
        uistyle.bring_up(self.heart.root)
