# -*- coding: utf-8 -*-
"""ctwin.py —— ⑤「CT 整卷分割」：整卷 CT 逐层送进同一颗 PL 引擎（模型库 ct_spleen），实时出脾脏轮廓、体积和三维表面。

流程：
  打开 .nii / .nii.gz（MSD Task09 的 imagesTr 会自动找 labelsTr 里的标注，只用来对照和算 Dice）
  -> 软组织窗 W400 / L40 -> 每层 4x4 块平均到 128 -> z3 编码（R / G / B = 上方约 5 mm、本层、下方约 5 mm）
  -> 紧凑帧连续发送（最多 2 帧在途），板端逐层返回 int8 logit
  -> 上位机：双线性放大到 512、保留最大三维连通块（可选）、体积（mL）、冠状面重建、三维表面渲染（自动旋转）。
第二阶段（可选，板端有 ct_spleen_roi 时默认）：按粗结果定一个 256x256 框，框内每层 2x2 块平均到 128 再送板（15 号模型），
  输出放大 2 倍贴回框内，再取最大三维连通块（与 E:/PLD/ct/roi.py 一致）。
输入编码、放大和连通块与离线评测（E:/PLD/ct）逐字节一致，见 ct_volume.py。

运行方式（顶栏）：
  板上整病例（默认，板端 v5.11+）：把 .nii.gz 文件原样上传一次，解压、窗宽窗位、编码、两阶段推理、放大、三维连通块、
    体积全在板上做完（jobs.py / 板端 src/job_ct.c），PC 只显示；板端太旧时自动改用逐层流式。
  逐层流式：上面描述的老流程（PC 编码和后处理，每层一个紧凑帧）。两种方式结果逐位相同（verify_board_jobs.py）。
仅用于工程教学与算法演示，不作为临床诊断依据。
"""

import os
import queue
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox, ttk

import jobs
import proto
import timeline
import uistyle
from uistyle import FONT, FONT_MONO

VIEW = 512
SIDE = 320
RES_W = 420                                           # 结果栏宽
BOARD_VIEWS = 36                                      # 板上整病例（v5.16+）：三维表面在板上渲染，每 10° 一张


class _BoardViews(object):
    """板上渲染好的三维表面（jobs.CT_RENDER3D，surf3d.c）：render(angle) 取最近的那一张，接口同 ct_volume.Surface。"""

    def __init__(self, views, info):
        self.views, self.info = views, info

    def render(self, angle_deg, size=SIDE):
        n = len(self.views)
        return self.views[int(round(angle_deg / (360.0 / n))) % n]
STRIP_H = 64
STRIP_TXT = 16                                        # 条带顶上的说明行高
POST_ROI = ("roi", "两阶段：粗分割 + ROI 2 倍精分割")
POST = (("bilinear+lcc", "双线性放大 + 最大连通块"), ("bilinear", "双线性放大"),
        ("nearest+lcc", "板端掩膜 + 最大连通块"), ("nearest", "板端掩膜（4x4 复制）"))
HERE = os.path.dirname(os.path.abspath(__file__))
MSD_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "Task09_Spleen", "Task09_Spleen"))
RED, GREEN, YELLOW = (255, 64, 64), (64, 230, 96), (255, 210, 0)
MODES = (("job", "板上整病例（上传文件，一次跑完）"), ("stream", "逐层流式（PC 编码、后处理）"))
STAGES = dict(upload="上传", decode="解码", coarse="粗分割", fine="ROI 精分割", send="回传")


def ct_model(name="ct_spleen"):
    return next((m for m in proto.MODELS if m["name"] == name), None)


class CTWindow(object):
    def __init__(self, app, parent=None):
        """parent：嵌进平台扩展窗口的一页（platformwin.py），不另开窗口、不带自己的状态栏。"""
        self.app = app
        self.embedded = parent is not None
        try:
            import numpy  # noqa: F401
            import scipy  # noqa: F401
            import ct_volume
        except ImportError as e:
            messagebox.showerror("CT 整卷分割", "需要 numpy + scipy（%s）" % e, parent=app.root)
            raise
        self.cv = ct_volume
        self.np = numpy
        self.model = ct_model()
        self.fine = ct_model("ct_spleen_roi")
        self.post = ((POST_ROI,) if self.fine else ()) + POST
        self.win = self.fine_logits = self.fine_payloads = self.m0 = None
        self.stats_fine = None
        if self.embedded:
            self.top = self.frame = tk.Frame(parent, bg=uistyle.C_BG)
        else:
            self.top = tk.Toplevel(app.root)
            self.top.title("CT 整卷分割（⑤：同一颗 PL 引擎，逐层分割脾脏 → 体积与三维表面）")
        self.queue = queue.Queue()
        self.busy = False
        self.vol = self.u8 = self.payloads = None
        self.logits = self.done = self.live = self.final = None
        self.area = self.gt_area = None
        self.surface = None
        self.jump = False
        self.angle = 0.0
        self.z = 0
        self.stop = None
        self.t_render = 0.0
        self.stats = None
        self.job = None                               # 板上整病例的结果（jobs.ct_seg 的回复）
        self.board_masks = {}                         # 后处理方案 -> 板上算好的最终掩膜
        self.meas = None                              # 脾脏测量（ct_volume.spleen_measure）
        self.baseline = None                          # 随访对比的基线 dict(name, ml)
        self.photos = {}
        self._build()
        if not self.embedded:
            self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.after(30, self._poll)
        self.top.after(120, self._spin)

    # ---------------------------------------------------------------- 界面
    # 顶栏：打开 / 后处理 / 显示选项 / 停止 / 开始；左：横断面卡片（层滑条 + 逐层面积条带）；
    # 右：冠状面、三维表面两张卡片；下：结果卡片（进度 + 体积 + 计时说明）；底部声明。
    def _build(self):
        t = uistyle.window(self.top, None)
        tools = uistyle.topbar(t, "CT 整卷分割", "脾脏 · ⑤ 同一颗 PL 引擎逐层分割")
        ttk.Button(tools, text="打开 CT…", command=self._open).pack(side="left")
        uistyle.vsep(tools)
        uistyle.label(tools, "运行方式", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 6))
        self.var_mode = tk.StringVar(value=MODES[0][1])
        ttk.Combobox(tools, textvariable=self.var_mode, width=30, font=FONT, state="readonly",
                     values=[m[1] for m in MODES]).pack(side="left")
        uistyle.vsep(tools)
        self.btn_stop = ttk.Button(tools, text="停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left")
        self.btn_run = uistyle.primary_button(tools, "在板上分割整卷 ▶", self.run)
        self.btn_run.pack(side="left", padx=(8, 0), pady=8)

        body = tk.Frame(t, bg=uistyle.C_BG)          # 两栏放在一个容器里，顶栏再宽也不会把两栏拉开
        body.grid(row=1, column=0, sticky="n", padx=12, pady=(12, 0))
        left, lb = uistyle.panel(body, "横断面", "红 = 板上分割；滚轮 / ↑↓ 翻层")
        left.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 12))
        self.var_gt = tk.BooleanVar(value=True)
        ttk.Checkbutton(left.head, text="标注轮廓（绿）", style="Card.TCheckbutton", variable=self.var_gt,
                        command=self._show).pack(side="right")
        self.lab_ax = uistyle.image_slot(lb, VIEW, VIEW)
        self.lab_ax.master.pack()
        wheel = lambda e: self._goto(self.z + (-1 if e.delta > 0 else 1))
        self.lab_ax.bind("<MouseWheel>", wheel)
        self.var_z = tk.IntVar(value=0)
        self.scale = ttk.Scale(lb, from_=0, to=0, orient="horizontal", length=VIEW,
                               command=lambda v: self._goto(int(float(v)), from_scale=True))
        self.scale.pack(pady=(8, 0))
        self.strip = tk.Canvas(lb, width=VIEW, height=STRIP_H, bg="#1b1b1b", highlightthickness=0)
        self.strip.pack(pady=(4, 0))
        self.strip.bind("<Button-1>", self._strip_click)
        self.strip.bind("<B1-Motion>", self._strip_click)              # 按住拖 = 连续翻层
        self.strip.bind("<MouseWheel>", wheel)
        self.var_zt = tk.StringVar(value="（未载入）")
        uistyle.label(lb, var=self.var_zt, font=FONT_MONO, anchor="w").pack(fill="x", pady=(6, 0))

        col = tk.Frame(body, bg=uistyle.C_BG)          # 冠状面 + 三维表面上下紧挨着（不跟第三栏的行高走）
        col.grid(row=0, column=1, sticky="n")
        f1, b1 = uistyle.panel(col, "冠状面重建", "黄线 = 当前层")
        f1.pack(fill="x")
        self.blank_cor = tk.PhotoImage(width=SIDE, height=240)     # 没图时占位，尺寸按像素
        self.lab_cor = tk.Label(b1, image=self.blank_cor, bg="#111", bd=0)
        self.lab_cor.pack()
        self.lab_cor.bind("<MouseWheel>", wheel)
        f2, b2 = uistyle.panel(col, "三维表面", "板上分割结果")
        f2.pack(fill="x", pady=(12, 0))
        self.var_rot = tk.BooleanVar(value=True)
        ttk.Checkbutton(f2.head, text="自动旋转", style="Card.TCheckbutton",
                        variable=self.var_rot).pack(side="right")
        self.lab_3d = uistyle.image_slot(b2, SIDE, SIDE, bg="#181818", fg="#888", font=FONT,
                                         text="分割完成后显示")
        self.lab_3d.master.pack()

        # 结果放第三栏（和横断面一样高）：放在下面时整个窗口超过 1080 屏高，测量文字被截掉
        res, rb = uistyle.panel(body, "结果")
        res.grid(row=0, column=2, sticky="ns", padx=(12, 0))
        self.var_post = tk.StringVar(value=self.post[0][1])
        cb = ttk.Combobox(res.head, textvariable=self.var_post, width=28, font=FONT, state="readonly",
                          values=[p[1] for p in self.post])
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda e: self._postprocess())
        uistyle.label(res.head, "后处理", fg=uistyle.C_MUTED).pack(side="right", padx=(12, 6))
        self.pbar = ttk.Progressbar(rb, length=RES_W, mode="determinate")
        self.pbar.pack(fill="x")
        self.var_txt = tk.StringVar(value="「打开 CT…」选一个 .nii.gz（例如 MSD Task09 的 imagesTs / imagesTr），再「在板上分割整卷」。"
                                          "需要板端 v5.6+（模型库里有 ct_spleen）")
        uistyle.label(rb, var=self.var_txt, font=uistyle.FONT_SMALL, fg=uistyle.C_MUTED, wrap=True) \
            .pack(fill="x", pady=(4, 0))
        self.var_res = tk.StringVar(value="")
        uistyle.label(rb, var=self.var_res, font=uistyle.FONT_TITLE, fg=uistyle.C_RED, wrap=True) \
            .pack(fill="x", pady=(6, 0))
        mrow = tk.Frame(rb, bg=uistyle.C_CARD)
        mrow.pack(fill="x", pady=(8, 0))
        self.var_wt = tk.StringVar(value="")
        self.var_weeks = tk.StringVar(value="")
        for text, var in (("体重 kg（可不填）", self.var_wt), ("随访间隔 周", self.var_weeks)):
            uistyle.label(mrow, text, fg=uistyle.C_MUTED).pack(side="left", padx=(0 if var is self.var_wt else 14, 4))
            e = ttk.Entry(mrow, textvariable=var, width=6, font=FONT)
            e.pack(side="left")
            e.bind("<Return>", lambda ev: self._show_meas())
            e.bind("<FocusOut>", lambda ev: self._show_meas())
        brow = tk.Frame(rb, bg=uistyle.C_CARD)
        brow.pack(fill="x", pady=(6, 0))
        ttk.Button(brow, text="设为随访基线", command=self._set_baseline).pack(side="left")
        ttk.Button(brow, text="时间线（软件计时）", command=self._timeline).pack(side="left", padx=(6, 0))
        self.var_meas = tk.StringVar(value="")
        # 测量 + 出处很长：放进可滚动的文字框，高度跟着栏高走，不再把窗口撑出屏幕
        uistyle.text_box(rb, self.var_meas, height=12, fg="#245").pack(fill="both", expand=True, pady=(8, 0))
        if not self.embedded:
            uistyle.statusbar(t, 2)
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(1, weight=1)
        for key in ("<Up>", "<Prior>"):
            t.bind(key, lambda e: self._goto(self.z + 1))
        for key in ("<Down>", "<Next>"):
            t.bind(key, lambda e: self._goto(self.z - 1))

    def _variant(self):
        return next(k for k, v in self.post if v == self.var_post.get())

    def _mode(self):
        return next((k for k, v in MODES if v == self.var_mode.get()), "stream")

    # ---------------------------------------------------------------- 载入
    def _open(self):
        if self.busy:
            return
        init = MSD_DIR if os.path.isdir(MSD_DIR) else HERE
        path = filedialog.askopenfilename(parent=self.top, initialdir=init, title="打开 CT 卷",
                                          filetypes=[("NIfTI", "*.nii.gz *.nii"), ("全部", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        self.var_txt.set("载入 %s …" % os.path.basename(path))
        self.busy = True

        def work():
            try:
                vol = self.cv.load_ct(path)
                u8 = self.cv.window_u8(vol["hu"])
                pl = self.cv.payloads(u8, vol["spacing"][2], self.model.get("enc", "z3") if self.model else "z3")
                self.queue.put(("loaded", vol, u8, pl))
            except Exception as e:
                self.queue.put(("err", "载入失败：%s" % e))
        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, vol, u8, pl):
        self.meas = None
        self.var_meas.set("")
        np = self.np
        self.vol, self.u8, self.payloads = vol, u8, pl
        n = len(u8)
        self.logits = np.zeros((n, 128, 128), np.int8)
        self.done = np.zeros(n, bool)
        self.live = np.zeros((n, 512, 512), bool)
        self.ppm = np.zeros((n, 512, 512), bool)      # 每层在 PL（pp_layer）上放大好的掩膜
        self.ppm_ok = np.zeros(n, bool)
        self.ppf = self.ppf_ok = None                 # 精分割框内的（256x256）
        self.final = self.surface = self.stats = None
        self.job, self.board_masks = None, {}
        self.area = np.zeros(n)
        lab = vol["label"]
        self.gt_area = None if lab is None else lab.reshape(n, -1).sum(1) * vol["spacing"][0] * vol["spacing"][1]
        self.scale.configure(to=n - 1)
        z0 = int(np.argmax(self.gt_area)) if self.gt_area is not None and self.gt_area.any() else n // 2
        self.pbar.configure(maximum=n, value=0)
        self.var_res.set("")
        self.lab_3d.configure(image="", text="分割完成后显示")
        sp = vol["spacing"]
        self.var_txt.set("%s：%d 层，层内 %.2f mm，层厚 %.1f mm%s。z3 编码相邻层偏移 %d 层（约 5 mm）"
                         % (vol["name"], n, sp[0], sp[2], "，带标注" if lab is not None else "，无标注",
                            self.cv.neighbour_offset(sp[2])))
        self._goto(z0)

    # ---------------------------------------------------------------- 板上整卷分割（第一阶段：整层粗分割）
    def run(self):
        if self.payloads is None:
            self.var_txt.set("先「打开 CT…」")
            return
        if self.model is None:
            self.var_txt.set("proto.MODELS 里没有 ct_spleen（上位机太旧）")
            return
        if self.busy or self.app.busy:
            self.var_txt.set("板子正忙，稍等")
            return
        self._reset_run()
        if self._mode() == "job":
            self._run_job()
            return
        self.busy = self.app.busy = True
        self.btn_run.state(["disabled"])
        self.pbar.configure(maximum=len(self.payloads), value=0)
        self._stream("coarse", self.model, list(self.payloads))

    def _reset_run(self):
        self.done[:] = False
        self.live[:] = False
        self.ppm_ok[:] = False
        self.ppf = self.ppf_ok = None
        self.area[:] = 0
        self.final = self.surface = self.stats = self.stats_fine = None
        self.win = self.fine_logits = self.fine_payloads = self.m0 = None
        self.job, self.board_masks = None, {}
        self.lab_3d.configure(image="", text="分割中…")
        self.var_res.set("")
        self.jump = True

    # ---------------------------------------------------------------- 板上整病例（v7 病例任务）
    def _host_port(self):
        try:
            return self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except (ValueError, AttributeError):
            return self.app.link.host, self.app.link.port

    def _run_job(self):
        """整卷文件上传一次，板上做完全部步骤；只能整体停（停止按钮在任务期间不起作用）。"""
        v = self._variant()
        fine = self.fine["index"] if (v == "roi" and self.fine is not None) else None
        self.stop = None                             # 整病例任务中途停不了：「停止」置灰
        self.busy = self.app.busy = True
        self.btn_run.state(["disabled"])
        self.pbar.configure(maximum=100, value=0)
        self.var_txt.set("板上整病例：上传 %s …" % os.path.basename(self.vol["path"]))
        host, port = self._host_port()
        threading.Thread(target=self._job_worker, args=(host, port, self.vol["path"], fine), daemon=True).start()

    def _job_worker(self, host, port, path, fine):
        link = self.app.link
        try:
            link.host, link.port = host, port
            link.connect()                           # 板端 10 s 空闲超时：任务前总是重连
            r = jobs.ct_seg(link, path, coarse=self.model["index"], fine=fine,
                            flags=jobs.CT_LOGITS | jobs.CT_MASK_COARSE | jobs.CT_MASK_FINAL | jobs.CT_RENDER3D,
                            on_progress=lambda p: self.queue.put(("jobprog", p)), views=BOARD_VIEWS, size=SIDE)
            self.queue.put(("job", r, fine))
        except Exception as e:
            link.close()
            self.queue.put(("job_fail", str(e)))

    def _on_jobprog(self, p):
        tot = max(1, int(p.get("total") or 1))
        self.pbar.configure(maximum=tot, value=min(tot, int(p.get("done", 0))))
        st = STAGES.get(p.get("stage"), p.get("stage"))
        if p.get("stage") == "upload":
            self.var_txt.set("板上整病例：上传 %.1f / %.1f MB" % (p["done"] / 1e6, p["total"] / 1e6))
        elif p.get("total"):
            self.var_txt.set("板上整病例：%s %d / %d 层（板上已用 %.1f s）" % (st, p["done"], p["total"], p.get("ms", 0) / 1000))
        else:
            self.var_txt.set("板上整病例：%s（板上已用 %.1f s）" % (st, p.get("ms", 0) / 1000))

    def _on_job_fail(self, msg):
        self.busy = self.app.busy = False
        self.btn_run.state(["!disabled"])
        if "peer closed" in msg or "magic" in msg:
            self.var_mode.set(MODES[1][1])            # 老板端不认 v7 任务：自动改用逐层流式
            self.run()
            self.var_txt.set("板端不支持整病例任务（需要 v5.11+：%s），已改用逐层流式" % msg)
            return
        self.var_txt.set("板上整病例失败：%s" % msg)

    def _on_job(self, r, fine):
        np = self.np
        res = r["result"]
        self.busy = self.app.busy = False
        self.btn_run.state(["!disabled"])
        n = len(self.u8)
        if not res.get("ok") or res.get("nz") != n:
            self.var_txt.set("板上整病例失败：%s" % (res.get("error") or "层数不符（板上 %s，本地 %d）" % (res.get("nz"), n)))
            return
        b = r["blobs"]
        self.logits = np.frombuffer(b["logits_coarse"], np.int8).reshape(n, 128, 128).copy()
        self.done[:] = True
        m0 = jobs.unpack_mask(b["mask_coarse"], (n, 512, 512))
        mf = jobs.unpack_mask(b["mask_final"], (n, 512, 512))
        self.board_masks = {"bilinear+lcc": m0}
        if fine is not None:
            self.board_masks["roi"] = mf
            self.m0 = m0
            self.win = tuple(res["win"]) if res.get("win") else None
            self.fine_logits = (np.frombuffer(b["logits_fine"], np.int8).reshape(-1, 128, 128).copy()
                                if "logits_fine" in b else None)
        self.live = mf.copy()
        sp = self.vol["spacing"]
        self.area = self.live.reshape(n, -1).sum(1) * sp[0] * sp[1]
        self.job_raw = r
        self.job = dict(res=res, upload_s=r["upload_s"], wall=r["wall_s"], renders=jobs.unpack_render3d(r),
                        board_final="roi" if fine is not None else "bilinear+lcc")
        self.stats = dict(n=n, wall=r["wall_s"], fps=n / r["wall_s"] if r["wall_s"] > 0 else 0,
                          ai_ms=res["pl_ms"] / max(1, sum(res["slices"])), rt_ms=0.0)
        self.var_txt.set(self._timing_text())
        self._postprocess()
        self._show()

    def _stream(self, tag, model, payloads):
        self.stop = threading.Event()
        self.ai_us, self.rt_ms = [], []
        try:
            host, port = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except ValueError:
            host, port = self.app.link.host, self.app.link.port
        threading.Thread(target=self._worker, args=(host, port, payloads, self.stop, model, tag), daemon=True).start()

    def _worker(self, host, port, payloads, stop, m, tag):
        """后台线程：紧凑帧连续发送（最多 2 帧在途），逐帧把结果交给界面线程。"""
        link = self.app.link
        flags = proto.FLAG_STATS | proto.FLAG_C_LOGIT
        up = 4 if tag == "coarse" else self.cv.ROI_K       # 粗分割整层 x4，精分割框内 x2
        t0 = time.perf_counter()
        try:
            link.host, link.port = host, port
            inflight = deque()
            k = 0
            while k < len(payloads) or inflight:
                while len(inflight) < 2 and k < len(payloads) and not stop.is_set():
                    # 板端说过能做 PL 后处理才带 b10；放大结果与 PC 上的 upsample_bilinear 逐位相同
                    pp = self.cv.pp_request(up) if getattr(link, "pp_available", False) else None
                    link.send_v6c(payloads[k], 128, kind=proto.V6C_KIND_RGB, model0=m["index"], thr=m["thr"],
                                  flags=flags, pp=pp)
                    inflight.append((k, time.perf_counter(), flags | (proto.FLAG_C_PP if pp else 0)))
                    k += 1
                if not inflight:
                    break
                z, ts, fl = inflight.popleft()
                st, logit, _ = link.recv_v6c(fl, 128)
                self.queue.put(("slice", tag, z, st, logit, (time.perf_counter() - ts) * 1000.0))
            self.queue.put(("done", tag, time.perf_counter() - t0, k, stop.is_set()))
        except Exception as e:
            link.close()
            self.queue.put(("err", "板上分割失败：%s（需要板端 v5.6+，两阶段要 v5.7+）" % e))

    def _on_slice(self, tag, i, st, logit, rt):
        np = self.np
        want = self.model if tag == "coarse" else self.fine
        if st is None or st.get("model") != want["index"]:
            self.stop.set()
            self.var_txt.set("板端没按 %d 号模型回结果（统计块 %s）——板端程序太旧？"
                             % (want["index"], None if st is None else st.get("model")))
            return
        q = np.frombuffer(logit, np.int8).reshape(128, 128)
        sp = self.vol["spacing"]
        if tag == "coarse":
            z = i
            self.logits[z] = q
            self.done[z] = True
            v = self._variant()
            pm = self.cv.pp_mask(st.get("pp"), 512)
            if pm is not None:
                self.ppm[z], self.ppm_ok[z] = pm, True
            if pm is not None and not v.startswith("nearest"):
                self.live[z] = pm
            else:
                self.live[z] = self.cv.postprocess(q[None], "nearest" if v.startswith("nearest") else "bilinear")[0]
            self.pbar.configure(value=int(self.done.sum()))
        else:                                        # 精分割：框内结果直接替换这一层的显示
            y0, x0, z0, z1 = self.win
            z = z0 + i
            self.fine_logits[i] = q
            self.live[z] = False
            pm = self.cv.pp_mask(st.get("pp"), 256)
            if pm is not None:
                self.ppf[i], self.ppf_ok[i] = pm, True
            self.live[z, y0:y0 + 256, x0:x0 + 256] = pm if pm is not None else self.cv.upsample_bilinear(q[None], 2)[0] > 0.5
            self.pbar.configure(value=len(self.done) + i + 1)
        self.area[z] = self.live[z].sum() * sp[0] * sp[1]
        self.ai_us.append(st.get("ai_us", 0))
        self.rt_ms.append(rt)
        now = time.perf_counter()
        if now - self.t_render > 0.04:
            self.t_render = now
            self._goto(z)

    def _pass_stats(self, n, wall):
        np = self.np
        return dict(n=n, wall=wall, fps=n / wall if wall > 0 else 0,
                    ai_ms=float(np.mean(self.ai_us)) / 1000 if self.ai_us else 0,
                    rt_ms=float(np.mean(self.rt_ms)) if self.rt_ms else 0)

    def _timing_text(self):
        if self.job is not None:
            res, ms = self.job["res"], self.job["res"]["ms"]
            t = ("板上整病例：上传 %.1f MB %.2f s → 板上 %.2f s（解码 %.2f s，与 PL 并行；粗分割 %d 层 %.2f s"
                 % (res["upload_bytes"] / 1e6, self.job["upload_s"], ms["total"] / 1000, ms["decode"] / 1000,
                    res["slices"][0], ms["coarse"] / 1000))
            if res["slices"][1]:
                t += "；ROI 精分割 %d 层 %.2f s" % (res["slices"][1], ms["fine"] / 1000)
            t += "；三维连通块 %.2f s）" % ((ms["lcc0"] + ms["lcc1"]) / 1000)
            up = res.get("upsample", {})
            t += "，其中 PL 计算 %.2f s；双线性放大：%s；合计 %.2f s" % (
                res["pl_ms"] / 1000, "PL" if up.get("pl") else "PS 工作线程", self.job["wall"])
            return t
        c, f = self.stats, self.stats_fine
        if c is None:
            return ""
        t = "粗分割 %d 层 %.2f s（%.1f 层/秒）" % (c["n"], c["wall"], c["fps"])
        if f is not None:
            t += " + ROI 精分割 %d 层 %.2f s = 合计 %.2f s" % (f["n"], f["wall"], c["wall"] + f["wall"])
        t += "，每层 PL 计算 %.1f ms，单帧往返 %.0f ms（2 帧在途流水）" % (c["ai_ms"], c["rt_ms"])
        n_pp = int(self.ppm_ok.sum()) + (int(self.ppf_ok.sum()) if self.ppf_ok is not None else 0)
        n_all = c["n"] + (f["n"] if f is not None else 0)
        return t + ("｜双线性放大：PL %d/%d 层" % (n_pp, n_all) if n_pp else "｜双线性放大：PC")

    def _on_done(self, tag, wall, sent, stopped):
        if tag == "coarse":
            n = int(self.done.sum())
            self.stats = self._pass_stats(n, wall)
            complete = not stopped and n == len(self.done)
            self.var_txt.set(self._timing_text() + ("｜已停止，只分割了一部分" if not complete else ""))
            if complete and self._variant() == "roi" and self.fine is not None:
                self._start_fine()
                return
        else:
            self.stats_fine = self._pass_stats(len(self.fine_payloads), wall)
            self.var_txt.set(self._timing_text() + ("｜精分割被停止" if stopped else ""))
            if stopped:
                self.fine_logits = None
        self.busy = self.app.busy = False
        self.btn_run.state(["!disabled"])
        self._postprocess()
        self._show()

    # ---------------------------------------------------------------- 第二阶段：ROI 精分割
    def _start_fine(self):
        """粗结果 -> 定框 -> 框内负载（后台线程算）-> 15 号模型逐层送板。"""
        self.busy = self.app.busy = True
        self.btn_run.state(["disabled"])
        logits, u8, dz = self.logits.copy(), self.u8, self.vol["spacing"][2]
        ppm = self.ppm.copy() if self.ppm_ok.all() else None
        mode = self.fine.get("enc", "roi_prior")

        def work():
            try:
                if ppm is None:
                    m0 = self.cv.postprocess(logits, "bilinear+lcc")
                else:
                    m0 = self.cv.postprocess_masks(ppm, "bilinear+lcc")
                win = self.cv.roi_window(m0, dz)
                pl = None if win is None else self.cv.roi_payloads(u8, m0, win, mode, dz)
                self.queue.put(("roi_ready", m0, win, pl))
            except Exception as e:
                self.queue.put(("err", "定框失败：%s" % e))
        threading.Thread(target=work, daemon=True).start()

    def _on_roi_ready(self, m0, win, pl):
        self.m0, self.win, self.fine_payloads = m0, win, pl
        if win is None:                              # 粗结果为空：没有框，两阶段结果 = 粗结果
            self.fine_logits = None
            self.busy = self.app.busy = False
            self.btn_run.state(["!disabled"])
            self.var_txt.set(self._timing_text() + "｜粗结果为空，不做精分割")
            self._postprocess()
            return
        self.fine_logits = self.np.zeros((len(pl), 128, 128), self.np.int8)
        self.ppf = self.np.zeros((len(pl), 256, 256), bool)
        self.ppf_ok = self.np.zeros(len(pl), bool)
        self.pbar.configure(maximum=len(self.done) + len(pl))
        self.var_txt.set(self._timing_text() + "｜ROI 框 (%d, %d) 256x256，第 %d–%d 层，精分割中…"
                         % (win[0], win[1], win[2] + 1, win[3] + 1))
        self._goto((win[2] + win[3]) // 2)
        self._stream("fine", self.fine, pl)

    def _stop(self):
        if self.stop is not None:
            self.stop.set()

    # ---------------------------------------------------------------- 整卷后处理 + 三维
    def _postprocess(self):
        if self.done is None or not self.done.all() or self.busy:
            return
        v = self._variant()
        if v == "roi" and self.fine is not None and self.m0 is None:
            self._start_fine()                       # 之前是单阶段跑的：补跑第二阶段
            return
        logits, spacing, label = self.logits.copy(), self.vol["spacing"], self.vol["label"]
        board = self.board_masks.get(v)                  # 板上整病例已经算好的这一种：直接用
        job = self.job or {}
        views = job.get("renders") if board is not None and v == job.get("board_final") else None
        fine = (None if self.fine_logits is None else self.fine_logits.copy(), self.win, self.m0)
        ppm = self.ppm.copy() if self.ppm_ok.all() else None             # 每层都在 PL 上放大过就直接用
        ppf = self.ppf.copy() if self.ppf_ok is not None and self.ppf_ok.all() else None
        if v == "roi" and fine[1] is not None and fine[0] is None:
            return                                   # 精分割被停止：保持现状
        self.lab_3d.configure(image="", text="三维重建中…")

        def work():
            try:
                t0 = time.perf_counter()
                if board is not None:
                    final = board
                elif v == "roi":
                    if fine[1] is None:
                        final = fine[2]
                    elif ppf is not None:
                        final = self.cv.roi_combine_masks(ppf, fine[1], len(logits))
                    else:
                        final = self.cv.roi_combine(fine[0], fine[1], len(logits))
                elif ppm is not None and v.startswith("bilinear"):
                    final = self.cv.postprocess_masks(ppm, v)
                else:
                    final = self.cv.postprocess(logits, v)
                if views:                                # 三维表面已在板上渲染（v5.16+）
                    surf = _BoardViews(views, job["res"].get("surface", {}))
                else:
                    surf = self.cv.Surface(final, spacing)
                res = dict(variant=v, ml=self.cv.volume_ml(final, spacing), ms=(time.perf_counter() - t0) * 1000)
                res["meas"] = self.cv.spleen_measure(final, spacing)
                if label is not None and label.any():
                    res["meas_gt"] = self.cv.spleen_measure(label, spacing)
                zs = self.np.nonzero(final.reshape(len(final), -1).any(1))[0]
                res["z"] = (int(zs.min()), int(zs.max())) if len(zs) else None
                if label is not None:
                    res["dice"] = self.cv.dice3d(final, label)
                    res["gt_ml"] = self.cv.volume_ml(label, spacing)
                self.queue.put(("post", final, surf, res))
            except Exception as e:
                self.queue.put(("err", "后处理失败：%s" % e))
        threading.Thread(target=work, daemon=True).start()

    def _on_post(self, final, surf, res):
        self.final, self.surface = final, surf
        if self.stats is not None:
            sp = self.vol["spacing"]
            self.area = final.reshape(len(final), -1).sum(1) * sp[0] * sp[1]
        txt = "脾脏体积 %.0f mL" % res["ml"]
        if res["z"]:
            txt += "\n第 %d–%d 层（共 %d 层）" % (res["z"][0] + 1, res["z"][1] + 1, res["z"][1] - res["z"][0] + 1)
        if "dice" in res:
            txt += "\n与标注 3D Dice %.3f（标注 %.0f mL）" % (res["dice"], res["gt_ml"])
        self.var_res.set(txt)
        self.meas, self.meas_res = res.get("meas"), res
        self._show_meas()
        if self.stats:
            tail = ""
            if isinstance(surf, _BoardViews):
                si = surf.info
                tail = "｜三维表面在板上渲染（%d 个角度，建表面 %.0f ms、每张 %.0f ms）" % (
                    len(surf.views), si.get("build_ms", 0), si.get("render_ms", 0))
            else:
                tail = "｜三维表面在 PC 上渲染"
            self.var_txt.set(self._timing_text() + "｜后处理（%s）%.0f ms" % (self.var_post.get(), res["ms"]) + tail)
        self.lab_3d.configure(text="")
        if self.jump and final.any():                  # 整卷刚跑完：跳到脾脏面积最大的一层
            self.jump = False
            self._goto(int(self.np.argmax(self.area)))
        self._show()

    # ---------------------------------------------------------------- 脾脏测量与随访（辅助测量与复核，不作诊断）
    def _num(self, var):
        try:
            v = float(var.get().strip())
            return v if v > 0 else None
        except ValueError:
            return None

    def _show_meas(self):
        m = self.meas
        if not m:
            self.var_meas.set("")
            return
        res = self.meas_res
        band = self.cv.SPLEEN_VOL_ERR.get(res["variant"])
        a = self.cv.spleen_assess(m, self._num(self.var_wt), band[:2] if band else None)
        lines = list(a["lines"])
        job = (self.job or {}).get("res", {})
        bm = job.get("spleen")
        if bm is not None and res["variant"] in self.board_masks and self.board_masks.get(res["variant"]) is not None:
            same = all(bm[k] == m[k] for k in ("length_mm", "width_mm", "thickness_mm", "splenic_index", "width_slice"))
            lines.append("长 / 宽 / 厚 / 脾指数在板上计算（与 PC 复算%s）" % ("逐位一致" if same else "不一致！"))
        g = res.get("meas_gt")
        if g:
            lines.append("对照（标注掩膜）：体积 %.0f mL，长 %.1f cm、宽 %.1f cm、厚 %.1f cm，脾指数 %.0f" % (
                g["volume_ml"], g["length_mm"] / 10, g["width_mm"] / 10, g["thickness_mm"] / 10, g["splenic_index"]))
        if self.baseline:
            f = self.cv.spleen_followup(self.baseline["ml"], m["volume_ml"], self._num(self.var_weeks))
            lines.append("随访：基线 %s → 本次 %s。%s" % (self.baseline["name"], self.vol["name"], f["line"]))
            if self.baseline["name"] != self.vol["name"]:
                lines.append("（演示：MSD 数据集没有同一患者的两次扫描，这两卷来自不同患者，只演示计算方法）")
        lines.append("仅辅助测量与复核，不作诊断。出处：" + "；".join(self.cv.SPLEEN_REFS.values()))
        self.var_meas.set("\n".join(lines))

    def _timeline(self):
        """1.7：最近一次板上整病例的时间线；再用 PS 工作线程做放大（flags bit3）跑一遍作对照（输出应逐位相同）。"""
        r = getattr(self, "job_raw", None)
        if r is None or self.busy:
            self.var_txt.set("先用「板上整病例」跑一卷，再看时间线")
            return
        win = timeline.TimelineWin(self.top, "整病例时间线：%s（软件计时）" % self.vol["name"])
        win.add("PL 后处理（默认，放大在 pp_layer）", "ct", r)
        fine = 15 if r["result"].get("win") is not None or "logits_fine" in r["blobs"] else None
        host, port = self._host_port()
        path = self.vol["path"]

        def work():
            link = self.app.link
            try:
                link.host, link.port = host, port
                link.connect()
                r2 = jobs.ct_seg(link, path, coarse=self.model["index"], fine=fine,
                                 flags=jobs.CT_MASK_FINAL | jobs.CT_PS_UPSAMPLE)
                same = r2["blobs"].get("mask_final") == r["blobs"].get("mask_final")
                self.top.after(0, lambda: win.add("PS 后处理（放大在 PS 工作线程）", "ct", r2,
                                                  "最终掩膜与 PL 版%s" % ("逐位相同" if same else "不同！")))
            except Exception as e:
                self.top.after(0, lambda: win.var.set(win.var.get() + "\n对照运行失败：%s" % e))
        threading.Thread(target=work, daemon=True).start()

    def _set_baseline(self):
        if self.meas:
            self.baseline = dict(name=self.vol["name"], ml=self.meas["volume_ml"])
            self._show_meas()

    def _spin(self):
        if not self.top.winfo_exists():
            return
        if self.surface is not None and (self.var_rot.get() or "3d" not in self.photos):
            if self.var_rot.get():
                self.angle = (self.angle + 6.0) % 360.0
            self._show_3d()
        self.top.after(90, self._spin)

    def _show_3d(self):
        from PIL import Image, ImageTk
        img = self.surface.render(self.angle, SIDE)
        self.photos["3d"] = ImageTk.PhotoImage(Image.fromarray(img))
        self.lab_3d.configure(image=self.photos["3d"])

    # ---------------------------------------------------------------- 显示
    def _goto(self, z, from_scale=False):
        if self.u8 is None:
            return
        self.z = max(0, min(len(self.u8) - 1, int(z)))
        if not from_scale:
            self.scale.set(self.z)
        self._show()

    def _mask(self):
        return self.final if self.final is not None else self.live

    def _show(self):
        if self.u8 is None:
            return
        from PIL import Image, ImageTk
        np, cv = self.np, self.cv
        z, n = self.z, len(self.u8)
        g = self.u8[z]
        rgb = np.repeat(g[..., None], 3, -1)
        lab = self.vol["label"]
        if lab is not None and self.var_gt.get():
            rgb[cv.contour(lab[z])] = GREEN
        m = self._mask()
        if m is not None and m[z].any():
            e = cv.contour(m[z])
            rgb[e] = RED
            rgb[np.roll(e, 1, 1)] = RED                   # 2 px wide so it reads at 512
        if self.win is not None and self.win[2] <= z <= self.win[3]:     # 第二阶段的 ROI 框（黄）
            y0, x0 = self.win[0], self.win[1]
            rgb[y0, x0:x0 + 256] = YELLOW
            rgb[y0 + 255, x0:x0 + 256] = YELLOW
            rgb[y0:y0 + 256, x0] = YELLOW
            rgb[y0:y0 + 256, x0 + 255] = YELLOW
        self.photos["ax"] = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.lab_ax.configure(image=self.photos["ax"])
        sp = self.vol["spacing"]
        st = "已分割" if self.done is not None and self.done[z] else "未分割"
        self.var_zt.set("第 %d / %d 层（%s）  板上面积 %.1f cm²%s" % (
            z + 1, n, st, (self.area[z] if self.area is not None else 0) / 100,
            "  标注 %.1f cm²" % (self.gt_area[z] / 100) if self.gt_area is not None else ""))
        # coronal reformat through the object's centre (rows = anterior -> posterior)
        mm = m if m is not None and m.any() else (lab if lab is not None and lab.any() else None)
        rc = int(np.nonzero(mm.any(axis=(0, 2)))[0].mean()) if mm is not None else 256
        cor = self.u8[::-1, rc, :]
        h = max(1, int(round(n * sp[2] / sp[0] * SIDE / 512)))
        h = min(h, 360)
        crgb = np.array(Image.fromarray(np.repeat(cor[..., None], 3, -1)).resize((SIDE, h), Image.BILINEAR))
        rs = lambda a: np.array(Image.fromarray(a.astype(np.uint8) * 255).resize((SIDE, h), Image.NEAREST)) > 0
        if lab is not None and self.var_gt.get():
            crgb[cv.contour(rs(lab[::-1, rc, :]))] = GREEN
        if m is not None:
            crgb[cv.contour(rs(m[::-1, rc, :]))] = RED
        yz = int((n - 1 - z + 0.5) * h / n)
        crgb[max(0, min(h - 1, yz)), :] = YELLOW
        self.photos["cor"] = ImageTk.PhotoImage(Image.fromarray(crgb))
        self.lab_cor.configure(image=self.photos["cor"], height=h)
        self._draw_strip()

    def _draw_strip(self):
        c = self.strip
        c.delete("all")
        n = len(self.u8)
        top = max([1.0] + [float(self.area.max())] + ([float(self.gt_area.max())] if self.gt_area is not None else []))
        w = VIEW / n
        bar_h = STRIP_H - STRIP_TXT - 2                  # 顶上留一行写说明，柱子不压字
        for i in range(n):
            x0 = i * w
            if self.done is not None and self.done[i]:
                hh = bar_h * self.area[i] / top
                c.create_rectangle(x0, STRIP_H - hh, x0 + max(w - 1, 1), STRIP_H, fill="#e04848", width=0)
            if self.gt_area is not None and self.var_gt.get() and self.gt_area[i] > 0:
                yy = STRIP_H - bar_h * self.gt_area[i] / top
                c.create_line(x0, yy, x0 + w, yy, fill="#40e060", width=2)
        xz = (self.z + 0.5) * w
        c.create_line(xz, STRIP_TXT, xz, STRIP_H, fill="#ffd200")
        c.create_text(4, 1, anchor="nw", fill="#aaa", font=("Microsoft YaHei UI", 8),
                      text="每层脾脏面积（红 = 板上，绿 = 标注；左足侧 → 右头侧）　点击 / 拖动 / 滚轮翻层")

    def _strip_click(self, e):
        if self.u8 is not None:
            self._goto(int(e.x / VIEW * len(self.u8)))

    # ---------------------------------------------------------------- 消息循环
    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "loaded":
                    self.busy = False
                    self._on_loaded(*item[1:])
                elif kind == "slice":
                    self._on_slice(*item[1:])
                elif kind == "done":
                    self._on_done(*item[1:])
                elif kind == "post":
                    self._on_post(*item[1:])
                elif kind == "roi_ready":
                    self._on_roi_ready(*item[1:])
                elif kind == "jobprog":
                    self._on_jobprog(*item[1:])
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
        stoppable = self.busy and self.stop is not None and not self.stop.is_set()    # 只有逐层流式能中途停
        self.btn_stop.state(["!disabled"] if stoppable else ["disabled"])
        if self.top.winfo_exists():
            self.top.after(30, self._poll)

    def close(self):
        self._stop()
        uistyle.close_when_idle(self.top, lambda: self.busy)
