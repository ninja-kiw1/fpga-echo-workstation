# -*- coding: utf-8 -*-
"""progwin.py —— ②「PL 程序台」：同一颗 PL 引擎上任选程序（学出来的模型 / 编译出来的算子）并串成流水线。

图源：主界面当前的图（512x512 RGB），或序列窗口当前帧的前 / 中 / 后三帧（放进 R / G / B，给三帧类算子）。
最多 3 个程序：程序 1 吃图源；程序 2、3 可以吃图源，也可以吃上一个程序的输出（板端用偏移码 logit+128 当灰度）。
一次紧凑帧请求（板端 v5.5+），板上依次执行，所有输出一起回传；这里并排显示并给出板端耗时。
算子输出按 logit * scale + mid 还原成数值（1.0 = 255 灰度）；分割模型的输出显示 logit（亮 = 目标）。
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import proto
import uistyle
from uistyle import FONT, FONT_MONO

TILE = 256
LABELS = ["%d %s" % (m["index"], m["label"]) for m in proto.MODELS]


def _down(rgb512, w):
    """512x512 RGB -> w x w RGB（每隔 512/w 取一点，与板端的档位取样一致）。"""
    k = 512 // w
    out = bytearray(w * w * 3)
    for y in range(w):
        row = rgb512[(k * y) * 1536:(k * y) * 1536 + 1536]
        line = bytearray(w * 3)
        line[0::3] = row[0::3 * k]
        line[1::3] = row[1::3 * k]
        line[2::3] = row[2::3 * k]
        out[y * w * 3:(y + 1) * w * 3] = line
    return bytes(out)


def _decode(logit, model):
    """int8 logit -> 0..255 灰度 bytes。"""
    if model.get("op"):
        scale, mid = model["scale"], model["mid"]
        lo, hi = model.get("out_range", (0.0, 1.0))
        span = max(hi - lo, 1e-9)
        lut = bytes(max(0, min(255, int(round(((v if v < 128 else v - 256) * scale + mid - lo) / span * 255))))
                    for v in range(256))
    else:
        lut = bytes(max(0, min(255, 128 + 2 * (v if v < 128 else v - 256))) for v in range(256))
    return bytes(logit).translate(lut)


def _photo(gray_or_rgb, w, rgb=False):
    from PIL import Image, ImageTk
    im = Image.frombytes("RGB" if rgb else "L", (w, w), gray_or_rgb)
    return ImageTk.PhotoImage(im.resize((TILE, TILE), Image.NEAREST))


class ProgWindow(object):
    def __init__(self, app, parent=None):
        """parent：嵌进平台扩展窗口的一页（platformwin.py），不另开窗口、不带自己的状态栏。"""
        self.app = app
        self.embedded = parent is not None
        if self.embedded:
            self.top = self.frame = tk.Frame(parent, bg=uistyle.C_BG)
        else:
            self.top = tk.Toplevel(app.root)
            self.top.title("PL 程序台（②：同一颗 PL 引擎，学出来的模型 / 编译出来的算子 / 板上流水线）")
        self.queue = queue.Queue()
        self.busy = False
        self.photos = []
        self._build()
        self.top.after(50, self._poll)

    def _build(self):
        t = uistyle.window(self.top, None)
        tools = uistyle.topbar(t, "PL 程序台", "同一颗 PL 引擎 · 最多 3 个程序串成板上流水线")
        uistyle.label(tools, "图源", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 6))
        self.var_src = tk.StringVar(value="主界面当前图")
        ttk.Combobox(tools, textvariable=self.var_src, width=18, state="readonly", font=FONT,
                     values=["主界面当前图", "序列窗口前/中/后三帧"]).pack(side="left")
        uistyle.label(tools, "分辨率", fg=uistyle.C_MUTED).pack(side="left", padx=(12, 6))
        self.var_res = tk.StringVar(value="128")
        ttk.Combobox(tools, textvariable=self.var_res, width=5, state="readonly", font=FONT,
                     values=["128", "256", "512"]).pack(side="left")
        self.btn = uistyle.primary_button(tools, "在板上运行 ▶", self.run)
        self.btn.pack(side="left", padx=(12, 0), pady=8)

        body = tk.Frame(t, bg=uistyle.C_BG)
        body.grid(row=1, column=0, sticky="n" if self.embedded else "nw", padx=12, pady=(12, 0))
        pc, prog = uistyle.panel(body, "流水线", "程序 1 吃图源；程序 2、3 可吃图源或上一个程序的输出（logit+128 当灰度）")
        pc.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.var_p, self.var_in = [], []
        defaults = ["6", "2", None]
        for i in range(3):
            uistyle.label(prog, "程序 %d" % (i + 1), fg=uistyle.C_MUTED).grid(
                row=0, column=3 * i, padx=(0 if i == 0 else 16, 4))
            first = next((l for l in LABELS if defaults[i] and l.startswith(defaults[i] + " ")), None)
            v = tk.StringVar(value=first or (LABELS[0] if i == 0 else "（不用）"))
            ttk.Combobox(prog, textvariable=v, width=18, state="readonly", font=FONT,
                         values=LABELS if i == 0 else ["（不用）"] + LABELS).grid(row=0, column=3 * i + 1)
            self.var_p.append(v)
            if i > 0:
                src = tk.StringVar(value="吃上一个输出" if i == 1 else "吃图源")
                ttk.Combobox(prog, textvariable=src, width=11, state="readonly", font=FONT,
                             values=["吃图源", "吃上一个输出"]).grid(row=0, column=3 * i + 2, padx=(4, 0))
                self.var_in.append(src)
        self.tiles = []
        for i in range(4):
            title = tk.StringVar(value="图源" if i == 0 else "程序 %d 输出" % i)
            c, b = uistyle.panel(body, title)
            c.grid(row=1, column=i, padx=(0 if i == 0 else 12, 0), pady=(12, 0), sticky="n")
            lab = uistyle.image_slot(b, TILE, TILE, bg=uistyle.C_EMPTY)    # 按像素占位（tk.Label 的 width 按字符算）
            lab.master.pack()
            cap = tk.StringVar(value="")
            uistyle.label(b, var=cap, font=FONT_MONO, fg=uistyle.C_ACCENT, anchor="w").pack(fill="x", pady=(4, 0))
            self.tiles.append((lab, cap, title))
        info = uistyle.card(body)
        info.grid(row=2, column=0, columnspan=4, sticky="ew", pady=12)
        self.var_txt = tk.StringVar(value="选程序后「在板上运行」。7 号以上是编译出来的算子，只能走紧凑帧（板端 v5.5+）")
        # 结果是一长串不带空格的中文：tk.Label 只在空格处折行，会把窗口撑出屏幕（10-18 演示时发现），改用自动折行的文本框
        uistyle.text_box(info, self.var_txt, height=3, font=FONT_MONO, fg=uistyle.C_TEXT) \
            .pack(fill="x", padx=14, pady=10)
        if not self.embedded:
            uistyle.statusbar(t, 2)
            self.top.protocol("WM_DELETE_WINDOW", self.close)

    def close(self):
        uistyle.close_when_idle(self.top, lambda: self.busy)

    def _source(self, w):
        if self.var_src.get().startswith("序列"):
            sw = getattr(self.app, "seq_win", None)
            if sw is None or not sw.top.winfo_exists() or sw.frames is None:
                raise ValueError("序列窗口没有载入序列")
            tr = sw.tracker
            t = tr.steps[-1].frame if tr is not None and tr.steps else 0
            n = len(sw.frames)
            idx = [max(t - 1, 0), t, min(t + 1, n - 1)]
            out = bytearray(512 * 512 * 3)
            for c, i in enumerate(idx):
                out[c::3] = sw.frames[i][0::3]
            return _down(bytes(out), w), "序列第 %d/%d/%d 帧 → R/G/B" % tuple(i + 1 for i in idx)
        if self.app.image is None:
            raise ValueError("主界面没有图")
        return _down(self.app.image, w), "主界面当前图"

    def run(self):
        if self.busy or self.app.busy:
            self.var_txt.set("板子正忙，稍等")
            return
        try:
            w = int(self.var_res.get())
            payload, note = self._source(w)
        except ValueError as e:
            self.var_txt.set(str(e))
            return
        progs = [int(self.var_p[0].get().split()[0])]
        extra = []
        if self.var_p[1].get().startswith("（") and not self.var_p[2].get().startswith("（"):
            self.var_txt.set("程序 2 是「不用」时程序 3 不会执行：先选程序 2，或把程序 3 挪到程序 2")
            return
        for i in (1, 2):
            v = self.var_p[i].get()
            if v.startswith("（"):
                break
            m = int(v.split()[0])
            extra.append((m, 1 if self.var_in[i - 1].get() == "吃上一个输出" else 0, 0, proto.MODELS[m]["thr"]))
            progs.append(m)
        self.busy = self.app.busy = True
        self.btn.state(["disabled"])
        m0 = proto.MODELS[progs[0]]
        try:                                          # 地址在主线程里读（后台线程不碰 Tk 变量）
            hp = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except ValueError:
            hp = self.app.link.host, self.app.link.port
        threading.Thread(target=self._worker, args=(payload, w, m0, extra, progs, note, hp), daemon=True).start()

    def _worker(self, payload, w, m0, extra, progs, note, hp):
        try:
            link = self.app.link
            link.host, link.port = hp
            flags = proto.FLAG_STATS | proto.FLAG_C_LOGIT
            t0 = time.perf_counter()
            st, logits, _, _ = link.v6c_round_trip(payload, w, kind=proto.V6C_KIND_RGB, model0=m0["index"],
                                                   thr=m0["thr"], flags=flags, extra=extra)
            ms = (time.perf_counter() - t0) * 1000.0
            if not extra:
                logits = [logits]
            self.queue.put(("ok", payload, w, logits, progs, st, ms, note))
        except Exception as e:
            self.app.link.close()
            self.queue.put(("err", str(e)))

    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                self.busy = self.app.busy = False
                self.btn.state(["!disabled"])
                if item[0] == "err":
                    self.var_txt.set("失败：%s（紧凑帧要板端 v5.4+，算子程序要 v5.5+）" % item[1])
                    continue
                self._show(*item[1:])
        except queue.Empty:
            pass
        if self.top.winfo_exists():
            self.top.after(50, self._poll)

    def _show(self, payload, w, logits, progs, st, ms, note):
        self.photos = [_photo(payload, w, rgb=True)]
        self.tiles[0][0].configure(image=self.photos[0])
        self.tiles[0][1].set("%dx%d" % (w, w))
        for i in range(1, 4):
            lab, cap, title = self.tiles[i]
            if i <= len(logits):
                m = proto.MODELS[progs[i - 1]]
                self.photos.append(_photo(_decode(logits[i - 1], m), w))
                lab.configure(image=self.photos[-1])
                title.set("程序 %d：%s" % (i, m["label"]))
                cap.set("%s %s" % ("编译算子" if m.get("op") else "学习模型", m["name"]))
            else:
                lab.configure(image="")
                title.set("程序 %d 输出" % i)
                cap.set("")
        self.var_txt.set("%s｜%d 个程序在板上依次执行（一次请求）｜往返 %.0f ms，其中程序 1 板端 %.1f ms｜"
                         "统计块回报模型 %s｜输出逐个 CRC 校验通过" % (note, len(logits), ms, (st or {}).get("ai_us", 0) / 1000.0,
                                                    (st or {}).get("model")))
