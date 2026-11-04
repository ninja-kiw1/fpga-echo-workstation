# -*- coding: utf-8 -*-
"""strainwin.py —— 部分 E「心肌应变（探索性）」窗口：对序列窗口当前载入的一段（CAMUS 半周期，第 0 帧 ED、最后一帧 ES）
在板上跑病例任务 strain（jobs.strain，src/job_strain.c，服务端 v5.19 起），显示追踪点随帧运动和 GLS-like 曲线。

板上做的事：ED 帧在 PL 上跑模型 2（F1 左室）+ 程序 18（心外膜）→ PL 清理 → PS 提心肌中线（每 3 mm 一个 16×16 块）
→ 逐帧 SAD 块匹配（NEON）→ 每帧中线长度 → GLS-like(t) = L(t)/L(0) − 1。与主机原型 strain/step2/track_val.py 逐位一致。
措辞：探索性辅助测量；CAMUS 验证集上 ES 值误差 MAE 约 3.1 个百分点（TRAIN 偏移后），图像质量差时不可靠；
不作诊断，不套「相对下降 >15%」之类的临床阈值。
"""
import threading
import tkinter as tk
from tkinter import ttk

import imageio
import jobs
import uistyle
from uistyle import FONT, FONT_MONO

W = H = 512
CURVE_W, CURVE_H = 520, 512
NOTE = ("探索性辅助测量（心肌中线长度变化，近似 GLS）：CAMUS 验证集上 ES 值误差 MAE 约 3.1 个百分点（TRAIN 偏移后），"
        "图像质量差时不可靠；不作诊断，不套「相对下降 >15%」等临床阈值")
STORE = {}          # 病人 -> {"2CH" / "4CH": TRAIN 偏移后的 ES 值}：两切面都算过时给平均


def strain_inputs(frames, info):
    """序列窗口的 512 RGB 帧 + info -> (T 帧 R 通道 bytes, sx, sy)；sx / sy = 512 网格上每像素 mm（原图尺寸 × 间距 / 512）"""
    sp = info.get("spacing_mm")
    if not sp:
        raise ValueError("没有像素间距，算不了长度（用 NIfTI，或在文件夹里放 sequence.json 写 spacing_mm）")
    if len(frames) < 2:
        raise ValueError("至少要两帧")
    sx = info["width"] * float(sp[0]) / W
    sy = info["height"] * float(sp[1]) / H
    return [bytes(f[0::3]) for f in frames], sx, sy


def unpack(out):
    """jobs.strain 的返回 -> dict(points (T, n, 2) float px (y, x), line (m, 2) int, myo (512, 512) bool, result)"""
    import numpy as np
    r = out["result"]
    T, n = r["frames"], r["n_points"]
    pts = np.frombuffer(out["blobs"]["points"], np.int32).reshape(T, n, 2) / 256.0
    line = np.frombuffer(out["blobs"]["line"], np.int32).reshape(-1, 2)
    myo = np.unpackbits(np.frombuffer(out["blobs"]["myo"], np.uint8), bitorder="little")[:W * H].reshape(H, W) > 0
    return dict(points=pts, line=line, myo=myo, result=r)


def run_strain(link, frames, info, flags=0):
    """上传 + 板上计算 -> unpack(...) 的 dict，外加 wall_s / upload_s；板端不支持或算不出来时抛 RuntimeError"""
    f512, sx, sy = strain_inputs(frames, info)
    link.connect()
    out = jobs.strain(link, f512, sx, sy, flags=flags)
    r = out["result"]
    if not r.get("ok"):
        err = r.get("error", "")
        if "unknown job" in err:
            raise RuntimeError("板端不支持心肌应变任务（需要服务端 v5.19）")
        raise RuntimeError(err)
    d = unpack(out)
    d.update(wall_s=out["wall_s"], upload_s=out["upload_s"], sx=sx, sy=sy)
    return d


def render(frame_rgb, pts_t, pts_0, myo=None):
    """当前帧 + 心肌轮廓（淡）+ ED 位置（灰）+ 当前追踪点（黄点、青线）-> 512x512 RGB bytes"""
    from PIL import Image, ImageDraw
    im = Image.frombytes("RGB", (W, H), bytes(frame_rgb))
    if myo is not None:
        import numpy as np
        edge = myo & ~(np.roll(myo, 1, 0) & np.roll(myo, -1, 0) & np.roll(myo, 1, 1) & np.roll(myo, -1, 1))
        a = np.array(im)
        a[edge] = (a[edge] * 0.4 + np.array([120, 60, 200]) * 0.6).astype(np.uint8)
        im = Image.fromarray(a)
    d = ImageDraw.Draw(im)
    d.line([(x, y) for y, x in pts_0], fill=(150, 150, 150), width=1)
    d.line([(x, y) for y, x in pts_t], fill=(0, 220, 230), width=2)
    for y, x in pts_t:
        d.ellipse([x - 2.5, y - 2.5, x + 2.5, y + 2.5], fill=(255, 220, 0))
    return im.tobytes()


class StrainWindow(object):
    """序列窗口里点「心肌应变…」打开；seqwin 提供 frames / info / app（link、主机端口、busy 互斥）。"""

    def __init__(self, seqwin):
        self.sw, self.app = seqwin, seqwin.app
        self.frames, self.info = seqwin.frames, seqwin.info
        self.res, self.k, self.playing, self.photo = None, 0, False, None
        self.busy = False
        self.top = tk.Toplevel(seqwin.top)
        self.top.title("心肌应变（探索性）— %s" % (self.info.get("name") or ""))
        self._build()
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.after(50, self.start)

    def close(self):
        self.playing = False
        uistyle.close_when_idle(self.top, lambda: self.busy)

    def _build(self):
        t = uistyle.window(self.top, None)
        tools = uistyle.topbar(t, "心肌应变", "探索性 · 斑点追踪（PS）+ 分割（PL）")
        self.btn_run = ttk.Button(tools, text="重新计算", command=self.start)
        self.btn_run.pack(side="left")
        self.btn_play = uistyle.primary_button(tools, "▶ 播放", self._toggle_play, width=8)
        self.btn_play.pack(side="left", padx=(12, 0), pady=8)
        mid = tk.Frame(t, bg=uistyle.C_BG)
        mid.grid(row=1, column=0, sticky="nw", padx=12, pady=(12, 0))
        left, lb = uistyle.panel(mid, "追踪点", "黄点 = 当前位置　灰线 = ED 位置　紫 = ED 心肌")
        left.grid(row=0, column=0, padx=(0, 12), sticky="n")
        self.cv = tk.Canvas(lb, width=W, height=H, bg=uistyle.C_EMPTY, bd=0, highlightthickness=0)
        self.cv.pack()
        self.cv_img = self.cv.create_image(0, 0, anchor="nw")
        right, rb = uistyle.panel(mid, "GLS-like 曲线", "中线长度相对 ED 的变化（%）")
        right.grid(row=0, column=1, sticky="n")
        self.curve = tk.Canvas(rb, width=CURVE_W, height=CURVE_H, bg="white", bd=0, highlightthickness=0)
        self.curve.pack()
        info = uistyle.card(mid)
        info.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 12))
        self.var_k = tk.IntVar(value=0)
        self.scale = uistyle.scale(info, from_=0, to=max(0, len(self.frames) - 1), variable=self.var_k,
                                   command=lambda v: self.show(int(v)))
        self.scale.pack(fill="x", padx=14, pady=(12, 6))
        self.var_txt = tk.StringVar(value="")
        self.var_val = tk.StringVar(value="")
        uistyle.label(info, var=self.var_val, font=FONT_MONO, fg=uistyle.C_ACCENT, anchor="w").pack(fill="x", padx=14)
        uistyle.label(info, var=self.var_txt, fg=uistyle.C_MUTED, wrap=True).pack(fill="x", padx=14, pady=(0, 4))
        uistyle.label(info, text=NOTE, fg=uistyle.C_WARN, wrap=True, font=uistyle.FONT_SMALL) \
            .pack(fill="x", padx=14, pady=(0, 10))
        uistyle.statusbar(t, 2, None, uistyle.DISCLAIMER + "｜心超序列来自 CAMUS（CC BY-NC-SA 4.0）")

    # ---- 板上计算 ----
    def start(self):
        if self.sw.busy or self.app.busy:
            self.var_txt.set("板子正忙，稍等")
            return
        self.busy = self.sw.busy = self.app.busy = True
        self.btn_run.state(["disabled"])
        self.var_txt.set("上传 %d 帧、在板上计算…" % len(self.frames))
        host, port = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        threading.Thread(target=self._worker, args=(host, port), daemon=True).start()
        self.top.after(100, self._poll)

    def _worker(self, host, port):
        try:
            link = self.app.link
            link.host, link.port = host, port
            self.result = ("ok", run_strain(link, self.frames, self.info))
        except Exception as e:
            try:
                self.app.link.close()
            except Exception:
                pass
            self.result = ("err", str(e))

    def _poll(self):
        r = getattr(self, "result", None)
        if r is None:
            self.top.after(100, self._poll)
            return
        self.result = None
        self.busy = self.sw.busy = self.app.busy = False
        self.btn_run.state(["!disabled"])
        if r[0] == "err":
            self.var_txt.set("算不出来：%s" % r[1])
            return
        self.res = r[1]
        self._report()
        self.show(len(self.frames) - 1)

    def _report(self):
        r = self.res["result"]
        view = self.info.get("view")
        patient = self.info.get("patient") or self.info.get("name")
        if view in ("2CH", "4CH"):
            STORE.setdefault(patient, {})[view] = r["es_cal_pct"]
        both = STORE.get(patient, {})
        mean = ("｜两切面平均 %.1f%%" % ((both["2CH"] + both["4CH"]) / 2)) if len(both) == 2 else ""
        self.var_val.set("ES %.1f%%（TRAIN 偏移 %+.2f 后）｜原始 %.1f%%｜曲线最低 %.1f%%%s" % (
            r["es_cal_pct"], r["offset_pct"], r["es_raw_pct"], r["peak_raw_pct"], mean))
        ms = r["ms"]
        self.var_txt.set("%d 帧 · %d 个追踪块（中线每 3 mm）· 出界 %d · 清理在 %s · 板上 %.0f ms（分割 %.0f / 提线 %.0f / "
                         "追踪 %.0f，每对帧 %.2f ms，%s）· 上传 %.2f s · 合计 %.2f s" % (
                             r["frames"], r["n_points"], r["edge_points"], "PL" if r["clean"] == "pl" else "PS",
                             ms["total"], ms["segment"], ms["line"], ms["track"], ms["track_per_pair"], r["kernel"],
                             self.res["upload_s"], self.res["wall_s"]))

    # ---- 显示 ----
    def show(self, k):
        self.k = k
        if self.var_k.get() != k:
            self.var_k.set(k)
        if self.res is None:
            self.photo = imageio.make_photo(tk, bytes(self.frames[k]))
        else:
            p = self.res["points"]
            self.photo = imageio.make_photo(tk, render(self.frames[k], p[k], p[0], self.res["myo"]))
        self.cv.itemconfigure(self.cv_img, image=self.photo)
        self.draw_curve(k)

    def draw_curve(self, cur):
        c = self.curve
        c.delete("all")
        x0, x1, y0, y1 = 56, CURVE_W - 20, 24, CURVE_H - 44
        c.create_line(x0, y1, x1, y1, fill="#888")
        c.create_line(x0, y0, x0, y1, fill="#888")
        c.create_text((x0 + x1) / 2, y1 + 26, text="帧（0 = ED，最后 = ES）", font=FONT, fill="#666")
        if self.res is None:
            return
        g = self.res["result"]["gls_pct"]
        lo, hi = min(min(g), -20.0), max(max(g), 2.0)
        n = len(g)
        fx = lambda k: x0 + k * (x1 - x0) / max(1, n - 1)
        fy = lambda v: y0 + (hi - v) / (hi - lo) * (y1 - y0)
        for v in range(int(hi) // 5 * 5, int(lo) - 1, -5):
            c.create_line(x0 - 3, fy(v), x1, fy(v), fill="#eee" if v else "#bbb")
            c.create_text(x0 - 6, fy(v), text="%d%%" % v, anchor="e", font=("Consolas", 8), fill="#666")
        pts = []
        for k, v in enumerate(g):
            pts += [fx(k), fy(v)]
        c.create_line(*pts, fill="#06b", width=2)
        for k, v in enumerate(g):
            c.create_oval(fx(k) - 3, fy(v) - 3, fx(k) + 3, fy(v) + 3, fill="#06b", outline="")
        es = self.res["result"]["es_cal_pct"]
        c.create_oval(fx(n - 1) - 4, fy(es) - 4, fx(n - 1) + 4, fy(es) + 4, outline="#c00", width=2)
        c.create_text(fx(n - 1) - 8, fy(es) + 14, anchor="e", text="ES 偏移后 %.1f%%" % es, fill="#c00", font=FONT)
        c.create_line(fx(cur), y0, fx(cur), y1, fill="#bbb", dash=(3, 3))
        c.create_text(x1, y0 - 12, anchor="ne", font=FONT, fill="#666", text="蓝 = 原始曲线　红圈 = ES 值（TRAIN 偏移后）")

    def _toggle_play(self):
        self.playing = not self.playing
        self.btn_play.configure(text="⏸ 暂停" if self.playing else "▶ 播放")
        if self.playing:
            self._tick()

    def _tick(self):
        if not self.playing:
            return
        self.show((self.k + 1) % len(self.frames))
        self.top.after(120, self._tick)
