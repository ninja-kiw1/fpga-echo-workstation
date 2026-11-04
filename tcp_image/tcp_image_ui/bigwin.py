# -*- coding: utf-8 -*-
"""bigwin.py —— 部分 C「大图精查」窗口：任意大小的眼底图，总览 + 框选放大，板上分块推理（病例任务 JOB_TILE，服务端 v5.20 起）。

用法（界面）
  1. 打开就先算"总览"：整幅图按 1 倍（模型尺度 = 图宽缩到 600，tile_engine.W1X）分 1–4 块在 PL 上推理，左边显示叠加，
     半透明格子 = 每 64 像素格的不确定像素比（|logit − 门限| <= 带宽，与 b5 置信度同一定义），编号虚线框 = 建议精查的区域。
  2. 在总览上拖一个任意大小的框（或点建议框），选放大倍数，点「重新推理」：框映射到该倍数的层（原图按比例缩放），
     自动切 512 块（每边 24 像素真实邻域），按不确定度从高到低逐块在 PL 上算、逐块填进右边；视野外的块跳过。
     拼接结果与"该倍数整幅图一次推理"逐位相同（E:/PLD/hrf/c1_exactness.py）。
  3. 「显示放大」只是插值放大右边的图（不重新推理）；超过放大上限的倍数只给显示放大。
层图来源：板上 /data 有这张图的预制层（tile_pyramid.json）就让板子只裁剪；没有就从 PC 上传需要的区域。
措辞：测量辅助（血管密度、形态指标），不作诊断。
"""
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk

import tile_engine as te
import uistyle
from uistyle import FONT, FONT_MONO, FONT_SMALL

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "tile_config.json")
OV_W = 600            # 总览画布宽 = 1 倍层宽
ZOOM_W, ZOOM_H = 640, 520
NOTE = ("测量辅助：血管分割与形态指标仅供工程演示与复核，不作诊断。分块拼接 = 该倍数整幅一次推理（逐位相同）；"
        "「显示放大」只是插值，不产生新信息。")


def load_config():
    cfg = dict(mags=[1, 1.5, 2, 3, 4], cap=4, thr=-3, band=8, pyramid=None, cap_note="")
    try:
        cfg.update(json.load(open(CONFIG, encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def rgb_key(rgb):
    import hashlib
    return hashlib.sha256(np.ascontiguousarray(rgb, np.uint8).tobytes()).hexdigest()


def load_pyramid(cfg):
    p = cfg.get("pyramid")
    if not p:
        return {}
    if not os.path.isabs(p):
        p = os.path.join(HERE, p)
    try:
        return json.load(open(p, encoding="utf-8")).get("cases", {})
    except (OSError, ValueError):
        return {}


def heat_lut():
    """logit -> 颜色（蓝 = 很确定不是、红 = 很确定是、黄绿 = 门限附近），只用于显示。"""
    lut = np.zeros((256, 3), np.uint8)
    for i in range(256):
        t = i / 255.0
        lut[i] = (int(255 * min(1, max(0, 1.5 - abs(4 * t - 3)))), int(255 * min(1, max(0, 1.5 - abs(4 * t - 2)))),
                  int(255 * min(1, max(0, 1.5 - abs(4 * t - 1)))))
    return lut


LUT = heat_lut()


class BigWindow(object):
    def __init__(self, app, rgb0, name, box0=None, mag=None):
        self.app = app
        self.cfg = load_config()
        self.rgb0 = np.ascontiguousarray(rgb0, np.uint8)
        self.name = name
        self.H0, self.W0 = self.rgb0.shape[:2]
        self.key = rgb_key(self.rgb0)
        self.cap = te.mag_cap(self.W0, self.cfg["cap"])          # 这张图能重新推理的最大倍数
        self.m_ov = min(1.0, self.cap)                           # 总览倍数（原图比 1 倍还小时就用原图）
        self.case = load_pyramid(self.cfg).get(self.key)
        self.levels = {}
        self.overview = None              # dict(logits, overlay, fov, out)
        self.zoom = None                  # 当前放大视图的结果
        self.box1 = None                  # 总览（1 倍层）坐标里的框
        self.q = queue.Queue()
        self.photo_l = self.photo_r = None
        self.drag0 = None
        self.running = False              # 有板上任务在途（关窗时要等它回来，见 uistyle.close_when_idle）
        self.top = tk.Toplevel(app.root)
        self.top.title("大图精查 — %s（%d×%d）" % (name, self.W0, self.H0))
        self._build()
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.pending = (box0, mag)
        self.top.after(100, self.run_overview)
        self.top.after(50, self._poll)

    # ------------------------------------------------------------------ 界面
    def _build(self):
        t = uistyle.window(self.top, None)
        tools = uistyle.topbar(t, "大图精查", "总览 + 框选放大 · 512 分块在 PL 上推理，拼接与整幅推理逐位相同")
        uistyle.label(tools, "放大倍数", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 6))
        self.mag_labels = self._mag_labels()
        dm = float(self.cfg.get("default_mag", 2))
        self.var_mag = tk.StringVar(value=next((l for l in self.mag_labels if float(l.split("×")[0]) == dm),
                                               self.mag_labels[0]))
        cb = ttk.Combobox(tools, textvariable=self.var_mag, values=self.mag_labels, width=22, state="readonly",
                          font=FONT)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._box_status())
        self.btn_run = uistyle.primary_button(tools, "重新推理 ▶", self.run_zoom, width=10)
        self.btn_run.pack(side="left", padx=(10, 0), pady=8)
        uistyle.vsep(tools)
        uistyle.label(tools, "显示", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 6))
        self.var_view = tk.StringVar(value="叠加（板上 vis_layer）")
        cb = ttk.Combobox(tools, textvariable=self.var_view, width=18, state="readonly", font=FONT,
                          values=["叠加（板上 vis_layer）", "血管掩膜", "置信度热图", "不确定区域", "原图"])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.render())
        uistyle.label(tools, "显示放大（仅插值）", fg=uistyle.C_MUTED).pack(side="left", padx=(12, 6))
        self.var_disp = tk.StringVar(value="适应窗口")
        cb = ttk.Combobox(tools, textvariable=self.var_disp, width=8, state="readonly", font=FONT,
                          values=["适应窗口", "1×", "2×", "4×", "8×"])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.render_zoom())

        mid = tk.Frame(t, bg=uistyle.C_BG)
        mid.grid(row=1, column=0, sticky="nsew", padx=12, pady=(12, 0))
        t.grid_rowconfigure(1, weight=1)
        t.grid_columnconfigure(0, weight=1)
        mid.grid_rowconfigure(0, weight=1)
        mid.grid_columnconfigure(1, weight=1)                # 窗口拉大时多出来的地方给放大视图
        lp, lb = uistyle.panel(mid, "总览（1 倍 = 模型尺度）", "拖框选区域　点建议框　双击 / 回车 = 重新推理")
        lp.grid(row=0, column=0, sticky="n", padx=(0, 12))
        self.ov_h = int(round(self.H0 * OV_W / float(self.W0)))
        self.cv_l = tk.Canvas(lb, width=OV_W, height=self.ov_h, bg=uistyle.C_EMPTY, bd=0, highlightthickness=0,
                              cursor="crosshair")
        self.cv_l.pack()
        self.cv_l_img = self.cv_l.create_image(0, 0, anchor="nw")
        self.cv_l.bind("<ButtonPress-1>", self._press)
        self.cv_l.bind("<B1-Motion>", self._drag)
        self.cv_l.bind("<ButtonRelease-1>", self._release)
        self.cv_l.bind("<Double-Button-1>", lambda e: self.run_zoom())
        self.top.bind("<Return>", lambda e: self.run_zoom())
        row = tk.Frame(lb, bg=uistyle.C_CARD)
        row.pack(fill="x", pady=(6, 0))
        self.var_heat = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="不确定度格子", variable=self.var_heat, style="Card.TCheckbutton",
                        command=self.render_overview).pack(side="left")
        self.var_sugg = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="建议框", variable=self.var_sugg, style="Card.TCheckbutton",
                        command=self.render_overview).pack(side="left", padx=(12, 0))
        self.var_ovnote = tk.StringVar(value="")
        uistyle.label(lb, var=self.var_ovnote, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x", pady=(4, 0))

        self.var_rtitle = tk.StringVar(value="放大视图")
        rp, rb = uistyle.panel(mid, self.var_rtitle, "逐块填入：按不确定度从高到低；左键拖动平移")
        rp.grid(row=0, column=1, sticky="nsew")
        fr = tk.Frame(rb, bg=uistyle.C_CARD)
        fr.pack(fill="both", expand=True)
        fr.grid_rowconfigure(0, weight=1)
        fr.grid_columnconfigure(0, weight=1)
        self.cv_r = tk.Canvas(fr, width=ZOOM_W, height=ZOOM_H, bg=uistyle.C_EMPTY, bd=0, highlightthickness=0,
                              cursor="fleur")
        self.sx = ttk.Scrollbar(fr, orient="horizontal", command=self.cv_r.xview)
        self.sy = ttk.Scrollbar(fr, orient="vertical", command=self.cv_r.yview)
        self.cv_r.configure(xscrollcommand=self.sx.set, yscrollcommand=self.sy.set)
        self.cv_r.grid(row=0, column=0, sticky="nsew")
        self.sy.grid(row=0, column=1, sticky="ns")
        self.sx.grid(row=1, column=0, sticky="ew")
        self.cv_r_img = self.cv_r.create_image(0, 0, anchor="nw")
        self.cv_r.bind("<ButtonPress-1>", lambda e: self.cv_r.scan_mark(e.x, e.y))
        self.cv_r.bind("<B1-Motion>", lambda e: self.cv_r.scan_dragto(e.x, e.y, gain=1))
        self.cv_r.bind("<MouseWheel>", lambda e: self.cv_r.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.cv_r.bind("<Shift-MouseWheel>", lambda e: self.cv_r.xview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.cv_r.bind("<Configure>", lambda e: self._on_zoom_resize())
        self.resize_job = None
        self.var_zoomnote = tk.StringVar(value="在左边拖框（或点虚线建议框），选倍数，点「重新推理」")
        uistyle.label(rb, var=self.var_zoomnote, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x", pady=(4, 0))

        info = uistyle.card(mid)
        info.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 12))
        self.var_m1 = tk.StringVar(value="")
        self.var_m2 = tk.StringVar(value="")
        self.var_m3 = tk.StringVar(value="")
        for v in (self.var_m1, self.var_m2, self.var_m3):
            uistyle.label(info, var=v, font=FONT_MONO, anchor="w", justify="left", wraplength=1240) \
                .pack(fill="x", padx=14, pady=(6, 0))
        uistyle.label(info, text=NOTE, fg=uistyle.C_WARN, wrap=True, font=FONT_SMALL).pack(fill="x", padx=14, pady=(4, 10))
        self.var_status = tk.StringVar(value="")
        uistyle.statusbar(t, 2, self.var_status, uistyle.DISCLAIMER + "｜HRF 图像 CC BY 4.0（Budai 2013）")

    def _mag_labels(self):
        """超过本图上限的倍数只能显示放大；括号里写上限，免得下拉框放不下被截断。"""
        why = "上限" if self.cap >= float(self.cfg["cap"]) - 1e-9 else "原图"
        return ["%g× 重新推理" % m if m <= self.cap + 1e-9 else
                "%g× 仅显示放大（%s %s×）" % (m, why, uistyle.fmt_mag(self.cap)) for m in self.cfg["mags"]]

    def close(self):
        uistyle.close_when_idle(self.top, lambda: self.running)

    def _mag(self):
        """当前选择 -> (推理倍数, 显示放大倍数)。超过本图上限：按上限推理，再插值放大。"""
        return te.effective_mag(float(self.var_mag.get().split("×")[0]), self.W0, self.cfg["cap"])

    # ------------------------------------------------------------------ 层
    def level(self, mag):
        """mag 倍的层：(层图, 有效 (宽, 高), 缩放比例, 板上路径或 None)。"""
        if mag not in self.levels:
            s = te.scale_for_mag(mag, self.W0)
            path = None
            if self.case:
                lv = self.case["levels"].get("%.4g" % mag)
                if lv and abs(lv["scale"] - s) < 1e-12:
                    path = lv["path"]
            lvl, valid = te.make_level(self.rgb0, s)
            self.levels[mag] = (lvl, valid, s, path)
        return self.levels[mag]

    # ------------------------------------------------------------------ 板上计算
    def _start(self, what, mag, box, order=None):
        if self.app.busy:
            self.var_status.set("板子正忙，稍等")
            return False
        self.app.busy = self.running = True
        self.btn_run.state(["disabled"])
        host, port =self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        threading.Thread(target=self._worker, args=(what, mag, box, order, host, port), daemon=True).start()
        return True

    def _worker(self, what, mag, box, order, host, port):
        lvl, valid, s, path = self.level(mag)
        H, W = lvl.shape[:2]
        try:
            link = self.app.link
            link.host, link.port = host, port
            out = te.run_board(link, W, H, box, te.TILE_LOGITS | te.TILE_OVERLAY, level_rgb=None if path else lvl,
                               path=path, order=order, thr=self.cfg["thr"], band=0,
                               on_tile=lambda t: self.q.put(("tile", what, t)))
            self.q.put(("done", what, (mag, box, out)))
        except Exception as e:                       # noqa: BLE001 - 界面上报错即可
            try:
                self.app.link.close()
            except Exception:                        # noqa: BLE001
                pass
            self.q.put(("err", what, str(e)))

    def _poll(self):
        try:
            while True:
                kind, what, v = self.q.get_nowait()
                if kind == "tile":
                    self._on_tile(what, v)
                elif kind == "done":
                    self._on_done(what, v)
                else:
                    self.app.busy = self.running = False
                    self.btn_run.state(["!disabled"])
                    msg = v if "unknown job" not in v else "板端不支持分块任务（需要服务端 v5.20）"
                    self.var_status.set("出错：%s" % msg)
        except queue.Empty:
            pass
        if self.top.winfo_exists():
            self.top.after(40, self._poll)

    def run_overview(self):
        lvl, valid, s, path = self.level(self.m_ov)
        H, W = lvl.shape[:2]
        self.var_status.set("总览：%d×%d 层、%d 块，在板上推理…" % (W, H, len(te.plan((0, 0, W, H), W, H))))
        self.ov_canvas = np.array(lvl[:valid[1], :valid[0]])
        self.render_overview()
        self._start("overview", self.m_ov, (0, 0, W, H))

    def run_zoom(self, box1=None):
        box1 = box1 or self.box1
        if box1 is None:
            self.var_status.set("先在总览上拖一个框（或点一个建议框）")
            return
        if self.overview is None:
            self.var_status.set("总览还没算完")
            return
        mag, self.disp_extra = self._mag()
        s1 = self.level(self.m_ov)[2]
        lvl, valid, s, path = self.level(mag)
        H, W = lvl.shape[:2]
        box = te.box_to_level(te.box_to_orig(box1, s1), s, W, H)
        tiles = te.plan(box, W, H)
        te.mark_fov_skip(tiles, lvl)
        ov = self.overview
        order = te.order_by_uncertainty(tiles, W, H, ov["logits"], ov["fov"], self.cfg["thr"], self.cfg["band"])
        x, y, w, h = box
        self.zoom = dict(mag=mag, box=box, box1=box1, tiles=tiles, logits=np.full((h, w), te.OUTSIDE_LOGIT, np.int8),
                         overlay=np.array(lvl[y:y + h, x:x + w]), base=np.array(lvl[y:y + h, x:x + w]), done=0,
                         t0=time.perf_counter(), out=None, path=path)
        c = te.costs(tiles)
        self.var_rtitle.set("放大视图（%s× 重新推理%s）" % (
            uistyle.fmt_mag(mag), "，再插值 %s×" % uistyle.fmt_mag(self.disp_extra) if self.disp_extra > 1 else ""))
        self.var_zoomnote.set("%s× 层 %d×%d，框 %d×%d → %d 块（视野外跳过 %d），halo 开销 %.0f%%，有效像素 %.0f%%；%s"
                              % (uistyle.fmt_mag(mag), W, H, w, h, c["tiles"], c["skipped"], 100 * c["halo_overhead"],
                                 100 * c["useful_ratio"], "板上 /data 层文件（只裁剪）" if path else "上传该区域"))
        self.render_zoom()
        self.render_overview()
        self._start("zoom", mag, box, order)

    def _on_tile(self, what, t):
        if what == "overview":
            return
        z = self.zoom
        if z is None:
            return
        x, y = z["box"][:2]
        sl = (slice(t["oy"] - y, t["oy"] - y + t["oh"]), slice(t["ox"] - x, t["ox"] - x + t["ow"]))
        if not t["skip"]:
            z["logits"][sl] = t["logits"]
            z["overlay"][sl] = t["overlay"]
        z["done"] += 1
        z.setdefault("got", []).append(t)
        self.var_status.set("放大：第 %d / %d 块（块 %d %s，U-Net %.1f ms）" % (
            z["done"], len(z["tiles"]), t["index"], "视野外跳过" if t["skip"] else "%d×%d" % (t["tw"], t["th"]),
            t["run_us"] / 1000.0))
        self.render_zoom()
        self.render_overview()

    def _on_done(self, what, v):
        self.app.busy = self.running = False
        self.btn_run.state(["!disabled"])
        mag, box, out = v
        res = out["result"]
        if not res.get("ok"):
            self.var_status.set("板上失败：%s" % res.get("error"))
            return
        if what == "overview":
            lvl, valid, s, path = self.level(self.m_ov)
            ovl = np.array(lvl)                         # 视野外跳过的块显示原图
            for t in out["tiles"]:
                if not t["skip"]:
                    ovl[t["oy"]:t["oy"] + t["oh"], t["ox"]:t["ox"] + t["ow"]] = t["overlay"]
            self.overview = dict(logits=out["logits"], overlay=ovl, fov=te.fov_mask(lvl), out=out, valid=valid)
            self.sugg = te.suggest(out["logits"][:valid[1], :valid[0]], te.fov_mask(lvl)[:valid[1], :valid[0]],
                                   self.cfg["thr"], self.cfg["band"], grid=96, k=5)
            self.var_ovnote.set("总览 %d×%d：%d 块，板上 %.0f ms（U-Net %.0f ms），往返 %.2f s；%s" % (
                res["level"][0], res["level"][1], res["tiles"], res["ms"], res["unet_ms"], out["wall_s"],
                "层来自板上 /data" if res["source"] == "file" else "上传"))
            self.var_status.set("总览完成。拖框选区域，或点虚线建议框")
            self.render_overview()
            box0, mag0 = self.pending
            self.pending = (None, None)
            if box0 is not None:
                s1 = self.level(self.m_ov)[2]
                lw, lh = self.level(self.m_ov)[0].shape[1], self.level(self.m_ov)[0].shape[0]
                self.box1 = te.box_to_level(box0, s1, lw, lh)
                if mag0 is not None:
                    for lab in self.mag_labels:
                        if abs(float(lab.split("×")[0]) - mag0) < 1e-9:
                            self.var_mag.set(lab)
                self.top.after(10, self.run_zoom)
            return
        z = self.zoom
        z["out"] = out
        self.render_zoom()
        self.render_overview()
        self._metrics()

    # ------------------------------------------------------------------ 指标
    def _metrics(self):
        z = self.zoom
        out, res = z["out"], z["out"]["result"]
        run = [t for t in out["tiles"] if not t["skip"]]
        tm = [t["run_us"] / 1000.0 for t in run]
        lvl, valid, s, path = self.level(z["mag"])
        x, y, w, h = z["box"]
        fov = te.fov_mask(lvl[y:y + h, x:x + w])
        m = z["logits"] >= self.cfg["thr"]
        dens = 100.0 * (m & fov).sum() / max(1, fov.sum())
        skip_save = (res["skipped"] / float(res["tiles"])) if res["tiles"] else 0.0
        self.var_m1.set("计算：%d 块（跑 %d、视野外跳过 %d = 省 %.0f%%）｜算了 %d 像素、框内 %d｜halo 开销 %.1f%%｜有效像素 %.1f%%"
                        % (res["tiles"], res["run"], res["skipped"], 100 * skip_save, res["computed_px"],
                           res["box_px"], 100 * res["halo_overhead"], 100 * res["useful_ratio"]))
        self.var_m2.set("耗时：每块 U-Net 平均 %.1f ms、最长 %.1f ms｜视图 板上 %.0f ms（U-Net %.0f、PL 叠加 %.1f、裁剪 %.1f）｜"
                        "往返 %.2f s（上传 %.2f s）｜%s"
                        % (np.mean(tm) if tm else 0, max(tm) if tm else 0, res["ms"], res["unet_ms"], res["ovl_ms"],
                           res["crop_ms"], out["wall_s"], out["upload_s"],
                           "层来自板上 /data（板子只裁剪）" if res["source"] == "file" else "层区域从 PC 上传"))
        txt = "框内（%s×，原图 %.0f×%.0f 像素）：视野内血管密度 %.1f%%" % (uistyle.fmt_mag(z["mag"]), w / s, h / s, dens)
        try:
            import sys
            ref_dir = os.path.join(HERE, "..", "tcp_image_rx", "ai", "net")
            if ref_dir not in sys.path:
                sys.path.insert(0, ref_dir)
            import vessel_morph_ref
            if w * h <= 1600 * 1600:
                r = vessel_morph_ref.analyze(m, fov, res512=int(round(512 * z["mag"])))
                f = 1.0 / s                                  # 层像素 -> 原图像素
                txt += ("｜骨架总长 %.0f px（原图）分支 %d 端点 %d 段 %d 迂曲度 %.3f 分形维数 %.3f"
                        % (r["length_x10"] / 10.0 * f, r["branches"], r["endpoints"], r["segments"],
                           r["tortuosity_x1000"] / 1000.0, r["fractal_x1000"] / 1000.0))
        except Exception as e:                         # noqa: BLE001 - 指标只是附加信息
            txt += "｜形态指标没算出：%s" % e
        self.var_m3.set(txt)
        self.var_status.set("放大完成：%d 块、板上 %.0f ms；拼接结果 = 该倍数整幅推理（逐位相同）" % (res["run"], res["ms"]))

    # ------------------------------------------------------------------ 显示
    def _view_rgb(self, base, logits, overlay):
        v = self.var_view.get()
        thr, band = self.cfg["thr"], self.cfg["band"]
        if v.startswith("叠加"):
            return overlay
        if v == "血管掩膜":
            return np.repeat(np.where(logits >= thr, 255, 0).astype(np.uint8)[..., None], 3, axis=2)
        if v == "置信度热图":
            return LUT[(logits.astype(np.int16) + 128)]
        if v == "不确定区域":
            return te.overlay_ref(base, logits, thr, band)
        return base

    def render_overview(self):
        if not hasattr(self, "ov_canvas"):
            return
        if self.overview is not None:
            ov = self.overview
            vw, vh = ov["valid"]
            lvl = self.level(self.m_ov)[0]
            rgb = self._view_rgb(lvl[:vh, :vw], ov["logits"][:vh, :vw], ov["overlay"][:vh, :vw])
        else:
            rgb = self.ov_canvas
        # 底图保持 RGB：ImageDraw 的 "RGBA" 模式只在 RGB 底图上做半透明混合；底图若是 RGBA，填充会直接覆盖像素，
        # 转回 RGB 后格子变成实心色、盖住整张总览（10-18 修）
        im = Image.fromarray(np.ascontiguousarray(rgb)).convert("RGB")
        d = ImageDraw.Draw(im, "RGBA")
        if self.overview is not None and self.var_heat.get():
            vw, vh = self.overview["valid"]
            sc = te.tile_scores(self.overview["logits"][:vh, :vw], self.overview["fov"][:vh, :vw], self.cfg["thr"],
                                self.cfg["band"], 64)
            mx = max(1e-6, sc[..., 0].max())
            for j in range(sc.shape[0]):
                for i in range(sc.shape[1]):
                    if sc[j, i, 2] > 0.2:
                        a = int(110 * sc[j, i, 0] / mx)
                        d.rectangle([i * 64, j * 64, i * 64 + 63, j * 64 + 63], fill=(255, 160, 0, a))
        if self.overview is not None and self.var_sugg.get():
            for k, (u, (x, y, w, h)) in enumerate(getattr(self, "sugg", [])):
                d.rectangle([x, y, x + w - 1, y + h - 1], outline=(0, 229, 255, 255), width=1)
                d.text((x + 3, y + 2), "%d" % (k + 1), fill=(0, 229, 255, 255))
        z = self.zoom
        if z is not None:                                   # 放大的块映射回总览：算完的实线、没算的虚
            s1 = self.level(self.m_ov)[2]
            f = s1 / te.scale_for_mag(z["mag"], self.W0)
            done = {t["index"]: t for t in z.get("got", [])}
            for t in z["tiles"]:
                r = [t.ox * f, t.oy * f, (t.ox + t.ow) * f - 1, (t.oy + t.oh) * f - 1]
                if t.index in done:
                    d.rectangle(r, outline=(120, 120, 120, 200) if done[t.index]["skip"] else (0, 200, 80, 255))
                else:
                    d.rectangle(r, outline=(255, 255, 255, 90))
        if self.box1 is not None:
            x, y, w, h = self.box1
            d.rectangle([x, y, x + w - 1, y + h - 1], outline=(255, 212, 0, 255), width=2)
        self.photo_l = ImageTk.PhotoImage(im)
        self.cv_l.itemconfigure(self.cv_l_img, image=self.photo_l)

    def render_zoom(self):
        z = self.zoom
        if z is None:
            return
        rgb = self._view_rgb(z["base"], z["logits"], z["overlay"])
        h, w = rgb.shape[:2]
        cw, ch = self._zoom_area()
        disp = self.var_disp.get()
        extra = getattr(self, "disp_extra", 1.0)
        if disp == "适应窗口":                              # 适应窗口 = 整个框正好放进视图（已含插值倍数）
            f = min(cw / float(w), ch / float(h))
        else:
            f = float(disp.rstrip("×")) * extra
        im = Image.fromarray(np.ascontiguousarray(rgb))
        size = (max(1, int(round(w * f))), max(1, int(round(h * f))))
        if size != (w, h):
            im = im.resize(size, Image.NEAREST if f >= 1 else Image.BILINEAR)
        self.photo_r = ImageTk.PhotoImage(im)
        self.cv_r.itemconfigure(self.cv_r_img, image=self.photo_r)
        ox, oy = max(0, (cw - size[0]) // 2), max(0, (ch - size[1]) // 2)     # 比视图小就居中
        self.cv_r.coords(self.cv_r_img, ox, oy)
        self.cv_r.configure(scrollregion=(0, 0, max(cw, size[0]), max(ch, size[1])))
        for sb, need in ((self.sx, size[0] > cw), (self.sy, size[1] > ch)):     # 放得下就不显示滚动条
            if need:
                sb.grid()
            else:
                sb.grid_remove()
        note = self.var_zoomnote.get().split("｜显示")[0]
        self.var_zoomnote.set("%s｜显示 %.2f 倍（%s）" % (
            note, f, "仅插值放大，不重新推理" if f > 1.0 or extra > 1.0 else "缩小显示"))

    def _zoom_area(self):
        """放大视图的实际像素尺寸（窗口还没画出来时用默认值）。"""
        w, h = self.cv_r.winfo_width(), self.cv_r.winfo_height()
        return (w, h) if w > 20 and h > 20 else (ZOOM_W, ZOOM_H)

    def _on_zoom_resize(self):
        if self.resize_job is not None:
            self.top.after_cancel(self.resize_job)
        self.resize_job = self.top.after(80, self._resized)

    def _resized(self):
        self.resize_job = None
        self.render_zoom()

    # ------------------------------------------------------------------ 框选
    def _xy(self, e):
        return max(0, min(OV_W - 1, e.x)), max(0, min(self.ov_h - 1, e.y))

    def _press(self, e):
        self.drag0 = self._xy(e)
        self.box_before = self.box1                    # 只是点了一下（没点中建议框）时恢复原来的框

    def _drag(self, e):
        if self.drag0 is None:
            return
        (x0, y0), (x1, y1) = self.drag0, self._xy(e)
        self.box1 = (min(x0, x1), min(y0, y1), abs(x1 - x0) + 1, abs(y1 - y0) + 1)
        self.render_overview()

    def _release(self, e):
        if self.drag0 is None:
            return
        (x0, y0), (x1, y1) = self.drag0, self._xy(e)
        self.drag0 = None
        if abs(x1 - x0) < 8 and abs(y1 - y0) < 8:          # 点击：选中所在的建议框
            for u, (x, y, w, h) in getattr(self, "sugg", []):
                if x <= x1 < x + w and y <= y1 < y + h:
                    self.box1 = (x, y, w, h)
                    break
            else:
                self.box1 = self.box_before
                self.render_overview()
                return
        else:
            self.box1 = (min(x0, x1), min(y0, y1), abs(x1 - x0) + 1, abs(y1 - y0) + 1)
        self.render_overview()
        self._box_status()

    def _box_status(self):
        """已选框在当前倍数下要算多少：选框后、换倍数后都更新一次。"""
        if self.box1 is None or self.running:
            return
        mag, extra = self._mag()
        lvl, valid, s, path = self.level(mag)
        box = te.box_to_level(te.box_to_orig(self.box1, self.level(self.m_ov)[2]), s, lvl.shape[1], lvl.shape[0])
        c = te.costs(te.plan(box, lvl.shape[1], lvl.shape[0]))
        self.var_status.set("已选框：%s× 下 %d×%d 像素、%d 块%s；点「重新推理」（或双击 / 回车）" % (
            uistyle.fmt_mag(mag), box[2], box[3], c["tiles"],
            "，再插值放大 %s× 显示" % uistyle.fmt_mag(extra) if extra > 1 else ""))

    def render(self):
        self.render_overview()
        self.render_zoom()

    # ------------------------------------------------------------------ 主窗口调用
    def zoom_to(self, box0, mag=None):
        """主窗口框选放大 > 1 倍：box0 = 原图坐标的框。总览还没算完就等它算完再放大。"""
        if self.overview is None:
            self.pending = (box0, mag)
            return
        lvl = self.level(self.m_ov)[0]
        self.box1 = te.box_to_level(box0, self.level(self.m_ov)[2], lvl.shape[1], lvl.shape[0])
        if mag is not None:
            for lab in self.mag_labels:
                if abs(float(lab.split("×")[0]) - mag) < 1e-9:
                    self.var_mag.set(lab)
        self.run_zoom()
