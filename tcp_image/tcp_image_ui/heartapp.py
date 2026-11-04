# -*- coding: utf-8 -*-
"""heartapp.py —— 床旁超声心动图实时全心功能评估系统：上位机主界面（「心超工作站」，设计报告第 7 节）。

    标题栏：系统名 + 板卡地址 / 连接状态（检测连接 = 问板端 caps）+ 平台扩展（打开原来的多程序上位机 app.py）
    模式条：快照模式（CAMUS）| 关键帧模式 | 现场模式（7.3 节三种工作模式），右侧一句话说明本模式的输入 → 输出
    内容区：快照 = sequence.SeqWindow 嵌入（单帧 / ED-ES 帧对、点击纠错、序列传播、双平面 Simpson、左房 / 左室质量 / 壁厚）；
            关键帧 / 现场 = heartview.EchoView（视频 + 容积曲线 + 时间线 + 结果 + 全心卡片 + 点击纠错 + 证据报告）
    状态栏：左边状态，右边常驻「仅用于工程教学与算法演示」+ 本段画面的数据出处
三个模式共用一条板子连接（板端一次只服务一个连接，busy 互斥）；切换模式不丢各自的结果。
快捷键：空格 播放 / 暂停，← → 逐帧，Ctrl+Z 撤销纠错。

用法：run_heart.bat 双击，或
    python heartapp.py [host] [port] [--mode snapshot|keyframe|live] [--open 视频.avi] [--demo]
--demo：验收演示，三个模式先备好素材、停在现场模式（prepare_demo）。
板上运行（DP 显示器）时 host 用 127.0.0.1。
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import imagelink
import jobs
import uistyle
from uistyle import C_ACCENT, C_BAR, C_BG, C_BORDER, C_CARD, C_GREEN, C_IDLE, C_MUTED, C_TEXT, C_WARN, FONT, FONT_MONO

DEFAULT_HOST = "192.168.1.254"
DEFAULT_PORT = 5060
HERE = os.path.dirname(os.path.abspath(__file__))
APP_TITLE = "床旁超声心动图实时全心功能评估系统"
SUBTITLE = "FZ3B · PL U-Net 分割 + PS 斑点跟踪 · 心尖四腔心（A4C）"
FONT_APP = ("Microsoft YaHei UI", 15, "bold")
FONT_TAB = ("Microsoft YaHei UI", 11, "bold")
MODES = (
    ("snapshot", "快照模式", "输入：单帧 / ED-ES 帧对（CAMUS）→ 左室分割、点击纠错、双平面 Simpson EF、左房容积、左室质量、壁厚、连续帧配准",
     "画面来自 CAMUS（CC BY-NC-SA 4.0）"),
    ("keyframe", "关键帧模式", "输入：整段视频上传 → 逐拍 EF、分型、保形区间、质量灯、复核提示、全心卡片；可点击纠错、导出证据报告", None),
    ("live", "现场模式", "输入：板上信号源逐帧“采”帧 → 每帧回掩膜和容积，每 0.25 s 一次滚动 EF；结束后同关键帧模式", None),
)


def route_hint(host):
    """连不上时看本机走哪个地址去板子：TUN 代理（198.18.x）会假装端口开着、连上就断；没有同网段地址 = 网线没接 / IP 不对。"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, 9))                         # UDP connect 不发包，只选路由
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        return ""
    if ip.startswith("198.18."):
        return "——流量走了代理 TUN（本机 %s）：关掉代理，或让 %s 所在网段直连" % (ip, host)
    if host != "127.0.0.1" and ip.split(".")[:3] != host.split(".")[:3]:
        return "——本机没有和板子同网段的地址（现在走 %s）：检查网线，有线网卡应为 192.168.1.100" % ip
    return ""


EXTRA_TOOLS = []          # 板端工作站（board/station_heart.py）在建窗口前往这里加 (名字, fn(单幅图 App))


class HeartApp(object):
    """主窗口。给各视图提供 root / link / var_host / var_port / busy / status() / board_seen()。"""

    def __init__(self, root, host, port, mode="keyframe"):
        self.root = root
        self.link = imagelink.ImageLink(host, port)
        self.busy = False
        self.queue = queue.Queue()
        self.views = {}
        self.mode = None
        self.platform = None                         # platformwin.PlatformWindow（第一次点「平台扩展…」时建）
        self.calib = {}                              # 屏幕标尺定标：原始录屏文件名 -> 原始画面 mm/像素（heartview）
        self.extra_tools = list(EXTRA_TOOLS)         # 平台扩展「更多」里的额外入口 (名字, fn(单幅图 App))，板端工作站挂
        root.title("%s — 上位机（心超工作站）" % APP_TITLE)
        root.configure(bg=C_BG)
        uistyle.setup_style(root)
        self._build_header()
        self._build_modebar()
        self.body = tk.Frame(root, bg=C_BG)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self.var_status = tk.StringVar(value="就绪")
        self.var_note = tk.StringVar(value=uistyle.DISCLAIMER)
        sbar = tk.Frame(root, bg=C_BAR)
        sbar.grid(row=3, column=0, sticky="ew")
        tk.Label(sbar, textvariable=self.var_note, font=uistyle.FONT_SMALL, fg=C_WARN, bg=C_BAR, padx=12, pady=4) \
            .pack(side="right")
        tk.Label(sbar, textvariable=self.var_status, font=uistyle.FONT_SMALL, anchor="w", fg=C_TEXT, width=1,
                 bg=C_BAR, padx=12, pady=4).pack(side="left", fill="x", expand=True)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)
        for seq, name in (("<space>", "space"), ("<Left>", "left"), ("<Right>", "right"), ("<Control-z>", "undo")):
            root.bind(seq, lambda e, n=name: self._key(e, n))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.switch(mode)
        root.after(50, self._poll)

    # ---------------------------------------------------------------- 界面
    def _build_header(self):
        bar = tk.Frame(self.root, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        bar.grid(row=0, column=0, sticky="ew")
        logo = tk.Canvas(bar, width=30, height=30, bg=C_CARD, highlightthickness=0)
        logo.pack(side="left", padx=(16, 8), pady=10)
        logo.create_oval(3, 3, 27, 27, fill="#dc2626", outline="")
        logo.create_line(6, 16, 11, 16, 14, 8, 17, 23, 20, 14, 24, 14, fill="white", width=2)
        uistyle.label(bar, APP_TITLE, font=FONT_APP).pack(side="left")
        uistyle.label(bar, SUBTITLE, font=uistyle.FONT_SMALL, fg=C_MUTED).pack(side="left", padx=(12, 0), pady=(6, 0))
        tools = tk.Frame(bar, bg=C_CARD)
        tools.pack(side="right", padx=(0, 16))
        uistyle.label(tools, "板卡", fg=C_MUTED).pack(side="left", padx=(0, 6))
        self.var_host = tk.StringVar(value=self.link.host)
        ttk.Entry(tools, textvariable=self.var_host, width=14, font=FONT_MONO).pack(side="left")
        uistyle.label(tools, ":", fg=C_MUTED).pack(side="left", padx=2)
        self.var_port = tk.StringVar(value=str(self.link.port))
        ttk.Entry(tools, textvariable=self.var_port, width=6, font=FONT_MONO).pack(side="left")
        self.lbl_dot = uistyle.label(tools, "●", fg=C_IDLE)
        self.lbl_dot.pack(side="left", padx=(12, 3))
        self.var_link = tk.StringVar(value="未检测")
        uistyle.label(tools, var=self.var_link, fg=C_MUTED, width=22, anchor="w").pack(side="left")
        ttk.Button(tools, text="检测连接", command=self.check_board).pack(side="left", padx=(6, 0))
        uistyle.vsep(tools)
        ttk.Button(tools, text="平台扩展…", command=self._open_platform).pack(side="left")

    def _build_modebar(self):
        bar = tk.Frame(self.root, bg=C_BG)
        bar.grid(row=1, column=0, sticky="ew", padx=12, pady=(10, 0))
        seg = tk.Frame(bar, bg=C_BORDER, padx=1, pady=1)
        seg.pack(side="left")
        self.tabs = {}
        for key, title, _, _ in MODES:
            b = tk.Button(seg, text=title, font=FONT_TAB, relief="flat", bd=0, padx=22, pady=6, cursor="hand2",
                          command=lambda k=key: self.switch(k))
            b.pack(side="left", padx=(0 if not self.tabs else 1, 0))
            self.tabs[key] = b
        self.var_desc = tk.StringVar(value="")
        uistyle.label(bar, var=self.var_desc, fg=C_MUTED, bg=C_BG, anchor="w").pack(side="left", padx=(14, 0))

    def switch(self, mode):
        """切换工作模式：第一次切到时才建视图；切走的视图只暂停播放，结果保留。"""
        if mode == self.mode:
            return
        if self.mode in self.views:
            v = self.views[self.mode]
            if hasattr(v, "_pause"):
                v._pause()
            if getattr(v, "stream_stop", None) is not None:     # 快照模式的实时流：收完在途帧就停
                v.stream_stop.set()
            v.frame.grid_forget()                    # 不用 grid_remove：板上 tkinter 兼容层没有它（下面总是带全参数重新 grid）
        self.mode = mode
        if mode not in self.views:
            try:
                self.views[mode] = self._make_view(mode)
            except ImportError as e:
                messagebox.showerror(APP_TITLE, "这个模式需要 numpy + scipy + OpenCV + Pillow（%s）" % e, parent=self.root)
                self.mode = None
                return
        self.views[mode].frame.grid(row=0, column=0, sticky="nsew")
        for k, b in self.tabs.items():
            on = k == mode
            b.configure(bg=C_ACCENT if on else C_CARD, fg="white" if on else C_TEXT,
                        activebackground=uistyle.C_ACCENT_D if on else uistyle.C_EMPTY,
                        activeforeground="white" if on else C_TEXT)
        m = next(x for x in MODES if x[0] == mode)
        self.var_desc.set(m[2])
        v = self.views[mode]
        txt = v.var_txt.get() if hasattr(v, "var_txt") else v.var_status.get()
        self.status(txt.splitlines()[0] if txt else m[1])

    def _make_view(self, mode):
        if mode == "snapshot":
            import snapview
            return snapview.SnapView(self, self.body)
        import heartview
        return heartview.EchoView(self, self.body, live=(mode == "live"))

    def _update_note(self):
        m = next(x for x in MODES if x[0] == self.mode)
        v = self.views.get(self.mode)
        note = (v.data_note() if v is not None and hasattr(v, "data_note") and (getattr(v, "name", None)
                                                                               or self.mode == "snapshot") else None) or m[3]
        self.var_note.set(uistyle.DISCLAIMER + ("｜" + note if note else ""))

    def status(self, msg):
        self.var_status.set(msg)
        self._update_note()

    # ---------------------------------------------------------------- 板子
    def _hostport(self):
        try:
            return self.var_host.get().strip(), int(self.var_port.get().strip())
        except ValueError:
            return self.link.host, self.link.port

    def check_board(self):
        if self.busy:
            self.status("板子正忙（有任务在途），稍后再检测")
            return
        self.busy = True
        self.lbl_dot.configure(fg=C_IDLE)
        self.var_link.set("检测中…")
        hp = self._hostport()

        def work():
            try:
                self.link.host, self.link.port = hp
                self.link.connect()
                c = jobs.caps(self.link)
                self.link.close()                    # 共用连接用完就放
                self.queue.put(("caps", c))
            except Exception as e:
                self.link.close()
                self.queue.put(("caps_err", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def board_seen(self, server):
        """各视图的任务连上板子时报一下服务端版本。"""
        if server:
            self.lbl_dot.configure(fg=C_GREEN)
            self.var_link.set("已连接 · 服务端 %s" % server)

    def _on_caps(self, c):
        self.busy = False
        modes = c.get("echo_modes", [])
        have = [name for name, ok in (("关键帧", 2 in modes), ("现场", 3 in modes), ("全心", "heart" in c)) if ok]
        self.board_seen(c.get("server", "?"))
        self.status("板端 %s：%s；%d 个内置模型，%d 核" % (
            c.get("server", "?"), ("支持 " + " / ".join(have)) if have else "不支持关键帧 / 现场 / 全心（需要 v5.21+ / v5.22+ / v5.24）",
            c.get("models", 0), c.get("cores", 0)))

    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "caps":
                    self._on_caps(item[1])
                elif item[0] == "caps_err":
                    self.busy = False
                    self.lbl_dot.configure(fg=uistyle.C_STOP)
                    self.var_link.set("连不上")
                    self.status("连不上板子 %s:%s（%s）%s" % (self.link.host, self.link.port, item[1],
                                                         route_hint(self.link.host)))
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(100, self._poll)

    def _open_platform(self):
        """平台扩展：同一比特流换权重的其它程序（眼底血管、皮肤镜、CT 脾脏、PL 程序台），platformwin.PlatformWindow，
        同一进程、共用板子连接与 busy；关掉只是藏起来。"""
        for v in self.views.values():
            if hasattr(v, "_pause"):
                v._pause()
        if not self.busy:                            # 单幅图页的发送不先重连：别留一个板端已经断开的连接给它
            self.link.close()
        if self.platform is None:
            import platformwin
            self.platform = platformwin.PlatformWindow(self)
        else:
            self.platform.show()
        self.status("平台扩展：眼底血管 / 皮肤镜病灶 / CT 脾脏整卷 / PL 程序台（同一比特流，换权重即换程序）")

    def _key(self, e, name):
        if isinstance(e.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        v = self.views.get(self.mode)
        if v is not None and hasattr(v, "key"):
            v.key(name)

    def close(self):
        if self.platform is not None and self.platform.app is not None:
            self.platform.app._stop_repeat()
        for v in self.views.values():
            if hasattr(v, "_stop"):
                v._stop()
            if hasattr(v, "stream_stop") and v.stream_stop is not None:
                v.stream_stop.set()
        uistyle.close_when_idle(self.root, lambda: self.busy)


PLD_DIR = os.path.normpath(os.path.join(HERE, "..", ".."))


def _first(*paths):
    return next((p for p in paths if os.path.exists(p)), paths[0])


# 演示素材：工程目录里的原位置，没有时（板上 DP 工作站）用上位机目录下 demo/ 里的拷贝
DEMO_SNAPSHOT = _first(os.path.join(PLD_DIR, "echo", "samples", "seq_patient0027_4CH"),
                       os.path.join(HERE, "demo", "seq_patient0027_4CH"))            # 设计报告图 6（CAMUS）
DEMO_VIDEO = _first(os.path.join(PLD_DIR, "HMC-QU Dataset-Kaggle v1.0 (1)", "HMC-QU Dataset-Kaggle v1.0", "HMC-QU",
                                 "A4C", "ES000108 _4CH_1.avi"),
                    os.path.join(HERE, "demo", "ES000108 _4CH_1.avi"))               # 设计报告图 7 / 图 8（HMC-QU）


def prepare_demo(app):
    """--demo：三个模式先各备好素材（快照 = CAMUS patient0027 四腔，关键帧 = HMC-QU ES000108，现场 = 50 fps 的
    EchoNet 测试源 + 固定方块），最后停在现场模式；现场验收时不用临场找文件。缺的素材跳过。"""
    app.switch("snapshot")
    if os.path.isdir(DEMO_SNAPSHOT):
        app.views["snapshot"].load(DEMO_SNAPSHOT)
    app.switch("keyframe")
    if os.path.exists(DEMO_VIDEO):
        app.views["keyframe"].load(DEMO_VIDEO)
    app.switch("live")
    app.status("演示准备好了：现场模式默认 50 fps 测试源 + 固定方块；先跑推荐档，再选「每帧推理」跑一遍对比延迟")


def _enable_dpi_awareness():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main():
    argv = sys.argv[1:]
    mode, open_path = "keyframe", None
    demo = "--demo" in argv
    if "--mode" in argv:
        i = argv.index("--mode")
        mode = argv[i + 1]
        del argv[i:i + 2]
    if "--open" in argv:
        i = argv.index("--open")
        open_path = argv[i + 1]
        del argv[i:i + 2]
    argv = [a for a in argv if not a.startswith("--")]
    host = argv[0] if len(argv) > 0 else DEFAULT_HOST
    port = int(argv[1]) if len(argv) > 1 else DEFAULT_PORT
    _enable_dpi_awareness()
    root = tk.Tk()
    app = HeartApp(root, host, port, mode if mode in [m[0] for m in MODES] else "keyframe")
    if open_path and app.mode == "keyframe":
        app.views["keyframe"].load(open_path)
    if demo:
        prepare_demo(app)
    root.update_idletasks()
    try:
        big = root.winfo_reqheight() > root.winfo_screenheight() - 60 or root.winfo_reqwidth() > root.winfo_screenwidth()
        if demo or big:                              # 验收演示 / 屏幕放不下：最大化（内容居中）
            root.state("zoomed")
        else:                                        # 贴屏幕顶、水平居中，免得底部压到任务栏
            root.geometry("+%d+0" % max(0, (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2))
    except Exception:                                # 板上 tkinter 兼容层没有 winfo_req* / winfo_screen*：直接最大化
        try:
            root.state("zoomed")
        except Exception:
            pass
    root.after(300, app.check_board)
    root.mainloop()


if __name__ == "__main__":
    main()
