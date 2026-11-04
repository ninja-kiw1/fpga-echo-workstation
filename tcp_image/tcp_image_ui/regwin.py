# -*- coding: utf-8 -*-
"""regwin.py —— F5「连续帧配准」窗口（从序列跟踪窗口打开，用它载入的帧和板子连接）。

左边 2x2：帧 A、帧 B、对齐前 |A − B|、对齐后 |A − B|（两幅差异图都是板端 PL 算的 logit 图）；
右边：代价曲面（行 = dy，列 = dx，128 网格 ±7；颜色越深代价越小），三步搜索的路径和最优点。
「三步搜索」3 帧、「全搜索」15 帧，每帧 16 个候选在 PL 上并行算 |a − b|、PS 分块求和；平移和搜索在 PC。
「给 B 加已知平移」用来演示：B 先被人为挪动 (dx, dy)，看配准能不能找回来。
「搜索在板上」（默认，板端 v5.11+）：只发 A、B 两帧，三步 / 全搜索的循环在板上的 PS 跑（v7 病例任务 reg_search，
src/job_reg.c），回 (dx, dy)、路径和每个候选的代价，与 PC 上搜索逐位相同（verify_board_jobs.py）；
板端太旧时自动退回 PC 搜索。对齐前后的差异图仍用一帧紧凑帧取回（显示用）。
「PL 配准引擎」（默认，v5.16 + r2 固件）：平移、代价和三步 / 全搜索全在 PL 的 vis_layer 上，整数结果与上面两条路逐位相同；
板子没有 vis_layer 时板端自动走 U-Net 老路，结果行写明实际走的是哪条。
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import confidence
import imageio
import jobs
import proto
import registration as reg
import uistyle
from uistyle import FONT, FONT_MONO

R = 7
CELL = 22


def _panel(gray128, title_rgb=None):
    """128x128 灰度 -> 256x256 RGB（2 倍最近邻）。"""
    out = bytearray(256 * 256 * 3)
    for y in range(128):
        row = gray128[y * 128:(y + 1) * 128]
        line = bytearray(256 * 3)
        line[0::6] = row
        line[1::6] = row
        line[2::6] = row
        line[3::6] = row
        line[4::6] = row
        line[5::6] = row
        o = (2 * y) * 768
        out[o:o + 768] = line
        out[o + 768:o + 1536] = line
    return bytes(out)


def _quad(panels):
    """4 幅 256x256 RGB -> 512x512 RGB（左上、右上、左下、右下）。"""
    out = bytearray(512 * 512 * 3)
    for k, p in enumerate(panels):
        ox, oy = (k % 2) * 256, (k // 2) * 256
        for y in range(256):
            o = ((oy + y) * 512 + ox) * 3
            out[o:o + 768] = p[y * 768:(y + 1) * 768]
    return bytes(out)


def _diff_from_logit(plane, tile):
    """logit 平面（512x512 偏移码）里第 tile 块 -> 128x128 灰度，值 = 2 * logit ≈ |a − b|。"""
    ty, tx = (tile // 4) * 128, (tile % 4) * 128
    lut = bytes(max(0, min(255, 2 * (v - proto.LOGIT_IMG_OFFSET))) for v in range(256))
    return b"".join(plane[(ty + y) * 512 + tx:(ty + y) * 512 + tx + 128] for y in range(128)).translate(lut)


class RegWindow(object):
    def __init__(self, seqwin):
        self.sw = seqwin
        self.app = seqwin.app
        self.top = tk.Toplevel(seqwin.top)
        self.top.title("连续帧配准（F5）— PL 逐像素 |a−b|，PS 分块求和，PC 三步搜索")
        self.queue = queue.Queue()
        self.busy = False
        self.result = None
        self.photo = None
        self._build()
        self.top.after(50, self._poll)

    def _build(self):
        t = uistyle.window(self.top, None)
        tools = uistyle.topbar(t, "连续帧配准", "F5 · PL 配准引擎")
        n = len(self.sw.frames)
        uistyle.label(tools, "帧 A", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 4))
        self.var_a = tk.StringVar(value="1")
        ttk.Spinbox(tools, from_=1, to=n, textvariable=self.var_a, width=4, font=FONT).pack(side="left")
        uistyle.label(tools, "帧 B", fg=uistyle.C_MUTED).pack(side="left", padx=(10, 4))
        self.var_b = tk.StringVar(value=str(min(2, n)))
        ttk.Spinbox(tools, from_=1, to=n, textvariable=self.var_b, width=4, font=FONT).pack(side="left")
        uistyle.label(tools, "给 B 加已知平移 dx, dy", fg=uistyle.C_MUTED).pack(side="left", padx=(10, 4))
        self.var_dx = tk.StringVar(value="0")
        self.var_dy = tk.StringVar(value="0")
        ttk.Spinbox(tools, from_=-R, to=R, textvariable=self.var_dx, width=3, font=FONT).pack(side="left")
        ttk.Spinbox(tools, from_=-R, to=R, textvariable=self.var_dy, width=3, font=FONT).pack(side="left", padx=(4, 0))
        uistyle.vsep(tools)
        self.var_board = tk.BooleanVar(value=True)
        ttk.Checkbutton(tools, text="搜索在板上", style="Card.TCheckbutton", variable=self.var_board).pack(side="left")
        self.var_vis = tk.BooleanVar(value=True)
        ttk.Checkbutton(tools, text="PL 配准引擎", style="Card.TCheckbutton",
                        variable=self.var_vis).pack(side="left", padx=(8, 0))
        uistyle.vsep(tools)
        self.btn_tss = ttk.Button(tools, text="三步搜索", command=lambda: self.start("tss"))
        self.btn_tss.pack(side="left")
        self.btn_full = uistyle.primary_button(tools, "全搜索 ±7 ▶", lambda: self.start("full"))
        self.btn_full.pack(side="left", padx=(8, 0), pady=8)

        body = tk.Frame(t, bg=uistyle.C_BG)
        body.grid(row=1, column=0, sticky="nw", padx=12, pady=(12, 0))
        lp, lb = uistyle.panel(body, "帧与差异", "左上 A　右上 B　左下对齐前 |A−B|　右下对齐后 |A−B|（差异图在 PL 上算）")
        lp.grid(row=0, column=0, sticky="n", padx=(0, 12))
        self.lbl = uistyle.image_slot(lb, 512, 512, bg=uistyle.C_EMPTY)      # 按像素占位（width 按字符算会撑爆窗口）
        self.lbl.master.pack()
        rp, rb = uistyle.panel(body, "代价曲面", "行 dy、列 dx；深 = 代价小")
        rp.grid(row=0, column=1, sticky="n")
        size = CELL * (2 * R + 1)
        self.cv = tk.Canvas(rb, width=size + 30, height=size + 30, bg="white", highlightthickness=0)
        self.cv.pack()
        uistyle.label(rb, "○ 三步搜索路径　★ 最优　绿框 = 人为加的已知平移", font=uistyle.FONT_SMALL,
                      fg=uistyle.C_MUTED, anchor="w").pack(fill="x", pady=(6, 0))
        info = uistyle.card(body)
        info.grid(row=1, column=0, columnspan=2, sticky="ew", pady=12)
        self.var_txt = tk.StringVar(value="选帧后点「三步搜索」或「全搜索」")
        uistyle.label(info, var=self.var_txt, font=FONT_MONO, wrap=True).pack(fill="x", padx=14, pady=10)
        uistyle.statusbar(t, 2)
        self.top.protocol("WM_DELETE_WINDOW", self.close)

    def close(self):
        uistyle.close_when_idle(self.top, lambda: self.busy)

    def _frames(self):
        n = len(self.sw.frames)
        ia = min(max(int(self.var_a.get()), 1), n) - 1
        ib = min(max(int(self.var_b.get()), 1), n) - 1
        a = reg.down128(self.sw.frames[ia])
        b = reg.down128(self.sw.frames[ib])
        dx0, dy0 = int(self.var_dx.get()), int(self.var_dy.get())
        if (dx0, dy0) != (0, 0):
            b = reg.shifted(b, b, -dx0, -dy0)      # 人为挪动 B：对齐它需要的平移正好是 (dx0, dy0)
        return ia, ib, a, b, (dx0, dy0)

    def start(self, kind):
        if self.busy or self.sw.busy or self.app.busy:
            self.var_txt.set("板子正忙，稍等")
            return
        try:
            ia, ib, a, b, known = self._frames()
        except ValueError:
            self.var_txt.set("帧号 / 平移要填整数")
            return
        self.busy = self.sw.busy = self.app.busy = True
        self.btn_tss.state(["disabled"])
        self.btn_full.state(["disabled"])
        self.var_txt.set("在板子上计算…")
        host, port = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        threading.Thread(target=self._worker, args=(kind, ia, ib, a, b, known, host, port, self.var_board.get(),
                                                           self.var_vis.get()),
                         daemon=True).start()

    def _worker(self, kind, ia, ib, a, b, known, host, port, on_board=True, use_vis=True):
        try:
            link = self.app.link
            link.host, link.port = host, port
            stats = []
            where = "PC"
            evals = None                                   # PL 配准引擎算的候选数（不走 U-Net 帧）

            def send(frame, flags=proto.FLAG_STATS | proto.FLAG_REG):
                imgs, tim, _, st = link.round_trip(frame, 0, flags=flags, aiparam=reg.aiparam())
                if st is None or st.get("model") != reg.MODEL or "reg" not in st:
                    raise RuntimeError("板端没回分块求和（要 v5.3 以上，统计块 version >= 6）")
                stats.append((st, tim))
                return st["reg"]["sums"], dict(imgs=imgs)
            t0 = time.perf_counter()
            j = None
            if on_board:
                try:
                    link.connect()
                    j = jobs.reg_search(link, a, b, mode=0 if kind == "tss" else 1, radius=R, engine=1 if use_vis else 0)
                except Exception as e:                     # 老板端不认 v7 任务：退回 PC 搜索
                    link.close()
                    j = None
                    where = "PC（板端不支持任务：%s）" % e
            if j is not None and j.get("ok"):
                best = (j["dx"], j["dy"])
                path = [tuple(q) for q in j["path"]] if kind == "tss" else None
                cost, frames, search_s, ai_ms = j["costs"], j["frames"], j["wall_s"], j["pl_ms"]
                if j.get("engine") == "vis":
                    evals = j["evals"]
                    where ="板上 PL 引擎（平移 + 代价 + 搜索都在 PL，%d 个候选，PL %.1f ms）" % (j["evals"], j["pl_ms"])
                else:
                    where = "板上（U-Net 算代价、PS 循环 %.0f ms%s）" % (
                        j["total_ms"], "；板子没有 PL 配准引擎" if use_vis else "")
                link.connect()
            else:
                r = reg.Registrar(a, b, send)
                if kind == "tss":
                    best, path = r.three_step()
                else:
                    best, path = r.full(R), None
                cost, frames, search_s = dict(r.cost), r.frames, time.perf_counter() - t0
                ai_ms = None
            # 再发一帧取差异图（块 0 = 对齐前，块 1 = 对齐后），也是 PL 算的
            imgs = send(reg.compose(a, [reg.shifted(b, a, 0, 0), reg.shifted(b, a, best[0], best[1])]),
                        proto.FLAG_STATS | proto.FLAG_REG | proto.FLAG_ECHO_LOGIT)[1]["imgs"]
            plane = confidence.logit_plane(imgs["logit"])
            if ai_ms is None:
                ai_ms = sum(s["ai_us"] for s, _ in stats[:-1]) / 1000.0
            self.queue.put(("ok", dict(kind=kind, ia=ia, ib=ib, a=a, b=b, known=known, best=best, path=path,
                                       cost=cost, frames=frames, search_s=search_s, where=where,
                                       diff0=_diff_from_logit(plane, 0), diff1=_diff_from_logit(plane, 1),
                                       ai_ms=ai_ms, evals=evals, ps_us=[s["reg"]["run_us"] for s, _ in stats])))
        except Exception as e:
            self.queue.put(("err", str(e)))

    def _poll(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                self.busy = self.sw.busy = self.app.busy = False
                self.btn_tss.state(["!disabled"])
                self.btn_full.state(["!disabled"])
                if kind == "err":
                    self.var_txt.set("失败：%s" % payload)
                else:
                    self.show(payload)
        except queue.Empty:
            pass
        if self.top.winfo_exists():
            self.top.after(50, self._poll)

    def show(self, res):
        self.result = res
        self.photo = imageio.make_photo(tk, _quad([_panel(res["a"]), _panel(res["b"]), _panel(res["diff0"]),
                                                   _panel(res["diff1"])]))
        self.lbl.configure(image=self.photo)
        self._draw_surface(res)
        c0, c1 = res["cost"][(0, 0)], res["cost"][res["best"]]
        g = 255.0 / 127.0                         # logit 单位 -> 灰度级
        known = res["known"]
        tail = ""
        if known != (0, 0):
            tail = "｜已知平移 (%d, %d)：%s" % (known[0], known[1], "找回 ✓" if res["best"] == known else
                                                 "差 (%d, %d)" % (res["best"][0] - known[0], res["best"][1] - known[1]))
        if res.get("evals") is not None:                # PL 配准引擎：候选直接在 vis_layer 上算，不走 U-Net 帧
            work = "PL 引擎算了 %d 个候选｜搜索 %.2f s，其中 PL %.1f ms" % (res["evals"], res["search_s"], res["ai_ms"])
        else:
            work = "用了 %d 帧（每帧 16 个候选）｜搜索 %.2f s，其中板端整网合计 %.0f ms，PS 分块求和每帧 %d µs" % (
                res["frames"], res["search_s"], res["ai_ms"], max(res["ps_us"]) if res["ps_us"] else 0)
        self.var_txt.set(
            "帧 %d → 帧 %d｜%s：最优平移 dx=%+d, dy=%+d（128 网格；512 画布上 ×4）%s\n"
            "平均灰度差：对齐前 %.1f → 对齐后 %.1f｜%s\n搜索循环：%s" % (
                res["ia"] + 1, res["ib"] + 1, "三步搜索" if res["kind"] == "tss" else "全搜索 ±7", res["best"][0],
                res["best"][1], tail, c0 * g, c1 * g, work, res.get("where", "PC")))

    def _draw_surface(self, res):
        cv = self.cv
        cv.delete("all")
        cost = res["cost"]
        vals = list(cost.values())
        lo, hi = min(vals), max(vals)
        o = 24
        for dy in range(-R, R + 1):
            for dx in range(-R, R + 1):
                x, y = o + (dx + R) * CELL, o + (dy + R) * CELL
                v = cost.get((dx, dy))
                if v is None:
                    fill = "#f4f4f4"
                else:
                    q = int(255 * (v - lo) / max(hi - lo, 1e-9))
                    fill = "#%02x%02x%02x" % (q, q, 255)
                cv.create_rectangle(x, y, x + CELL, y + CELL, fill=fill, outline="#ddd")
        for k in (-R, 0, R):
            cv.create_text(o + (k + R) * CELL + CELL / 2, 12, text=str(k), font=("Consolas", 8))
            cv.create_text(10, o + (k + R) * CELL + CELL / 2, text=str(k), font=("Consolas", 8))
        if res["path"]:
            pts = []
            for dx, dy in res["path"]:
                cx, cy = o + (dx + R) * CELL + CELL / 2, o + (dy + R) * CELL + CELL / 2
                pts += [cx, cy]
                cv.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, outline="#e80", width=2)
            if len(pts) >= 4:
                cv.create_line(*pts, fill="#e80", width=2, arrow="last")
        bx, by = res["best"]
        cv.create_text(o + (bx + R) * CELL + CELL / 2, o + (by + R) * CELL + CELL / 2, text="★", fill="#c00",
                       font=("Microsoft YaHei UI", 12))
        if res["known"] != (0, 0):
            kx, ky = res["known"]
            x, y = o + (kx + R) * CELL, o + (ky + R) * CELL
            cv.create_rectangle(x + 1, y + 1, x + CELL - 1, y + CELL - 1, outline="#0a0", width=2)
