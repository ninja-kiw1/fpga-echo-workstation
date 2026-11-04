# -*- coding: utf-8 -*-
"""timeline.py —— P1 1.7 可观测面板（软件部分）：一个病例任务从上传到回来，时间花在哪。

数据全部来自**软件计时**（界面上写明）：PC 的上传 / 往返墙钟（jobs.run），板端任务结果里的 "ms" 各阶段、
"pl_ms"（U-Net 驱动时间）、"pl_split"（v5.16：驱动自己的 ap 计算 / put 搬入 / get 搬出 / 补零，unet_stats_t）、
"pp_ms"（v5.16：pp_layer 内核 / 含拷贝）。没有硬件周期计数器（那是 P3）。

画法：横轴是时间（ms），每条泳道一行：
  网络       上传（PC 墙钟）、回传 + 收尾（墙钟 − 上传 − 板端合计）
  PS 主线程  板端各阶段依次排开（CT：解析头 → 粗分割 → 三维连通块 → ROI → 精分割 → 连通块 → 发送；心超：逐帧 → 分析）
  PS 解码    CT 的解码线程（与 PL 循环并行）
  PL         PL 阶段的窗口里按比例分成：U-Net 计算（ap）、搬运（put + get + 补零）、PL 后处理（pp_layer）、其余（PS 调度 / 等待）
对照（强化方案 H3）：同一病例 PL 后处理 vs PS 后处理各跑一遍，输出逐位相同、完成时间不同，两条时间线上下对齐画。
"""
import tkinter as tk

import uistyle

LANES = ("网络", "PS 主线程", "PS 解码线程", "PL（推理 / 后处理）")
COL = {"upload": "#7aa6d8", "return": "#a9c6e8", "ps": "#b9b9b9", "decode": "#d8c27a", "ap": "#d96b4f",
       "move": "#e8a76f", "pp": "#5aa36a", "plother": "#efe2d6", "wait": "#f2f2f2", "ps_post": "#8f8fd0"}


def _f(x):
    return float(x or 0.0)


def segments(kind, r):
    """jobs.run 的回复 -> (总时长 ms, [(泳道, 起, 止, 颜色键, 标签)], 汇总 dict)。"""
    res = r["result"]
    ms = res.get("ms", {})
    up = _f(r.get("upload_s")) * 1000.0
    wall = _f(r.get("wall_s")) * 1000.0
    board = _f(ms.get("total"))
    seg = [(0, 0.0, up, "upload", "上传 %.0f" % up)]
    split = res.get("pl_split") or {}
    pp = res.get("pp_ms") or {}
    ap, move = _f(split.get("ap_ms")), _f(split.get("put_ms")) + _f(split.get("get_ms")) + _f(split.get("zero_ms"))
    if not split:                                   # v5.15 及以前：只有 U-Net 合计（计算 + 搬运不分）
        ap = _f(res.get("pl_ms"))
    pp_pl = _f(pp.get("pl"))
    t = up
    pl_windows = []
    if kind == "ct":
        steps = [("header", "解析头"), ("coarse", "粗分割（PL 循环）"), ("lcc0", "三维连通块"), ("roi", "ROI"),
                 ("fine", "精分割（PL 循环）"), ("lcc1", "连通块"), ("send", "发送")]
        for key, name in steps:
            d = _f(ms.get(key))
            if d <= 0:
                continue
            if key in ("coarse", "fine"):
                pl_windows.append((t, t + d))
                seg.append((1, t, t + d, "wait", name))
            else:
                seg.append((1, t, t + d, "ps", name))
            t += d
        dh = _f(ms.get("header"))
        seg.append((2, up + dh, up + dh + _f(ms.get("decode")), "decode", "解码 %.0f" % _f(ms.get("decode"))))
        ps_post = _f(ms.get("lcc0")) + _f(ms.get("lcc1")) + _f(ms.get("roi")) + _f(ms.get("upsample_ps"))
    else:
        d = _f(ms.get("frames"))
        pl_windows.append((t, t + d))
        seg.append((1, t, t + d, "wait", "逐帧（PL 循环）"))
        t += d
        a = _f(ms.get("analysis"))
        seg.append((1, t, t + a, "ps", "逐拍分析"))
        t += a
        ps_post = a + (_f(ms.get("worker")) if _f(pp_pl) == 0 else 0.0)
    seg.append((0, up + board, max(wall, up + board), "return", "回传 + 收尾 %.0f" % max(0.0, wall - up - board)))
    # PL lane: inside the PL windows, proportional split (software timers, not cycle-accurate placement)
    win = sum(b - a for a, b in pl_windows) or 1.0
    parts = [("ap", ap, "U-Net 计算"), ("move", move, "搬运"), ("pp", pp_pl, "PL 后处理")]
    used = sum(v for _, v, _ in parts)
    parts.append(("plother", max(0.0, win - used), "PS 调度 / 等待"))
    for a, b in pl_windows:
        x = a
        for key, v, name in parts:
            w = (b - a) * v / win
            if w > 0:
                seg.append((3, x, x + w, key, name))
                x += w
    total = max(wall, up + board)
    summ = dict(total_ms=total, upload_ms=up, return_ms=max(0.0, wall - up - board), board_ms=board, pl_compute_ms=ap,
                pl_move_ms=move, pl_post_ms=pp_pl, ps_post_ms=ps_post, pl_ms=_f(res.get("pl_ms")),
                decode_wait_ms=_f(ms.get("decode_wait")), split=bool(split))
    return total, seg, summ


class TimelineWin(object):
    """runs = [(标题, kind 'ct' | 'echo', jobs.run 的回复)]；可后加（对照）。"""

    W, LANE_H, LEFT = 980, 26, 150

    def __init__(self, master, title):
        self.top = uistyle.window(tk.Toplevel(master), title)
        self.runs = []
        c = uistyle.card(self.top)
        c.pack(padx=12, pady=12)
        self.cv = tk.Canvas(c, width=self.W, height=120, bg="white", highlightthickness=0)
        self.cv.pack(padx=8, pady=8)
        leg = tk.Frame(c, bg=uistyle.C_CARD)              # 色块图例（块太窄写不下字时靠它认）
        leg.pack(fill="x", padx=8, pady=(0, 8))
        for key, name in (("upload", "上传"), ("return", "回传 + 收尾"), ("ps", "PS 步骤"), ("wait", "PL 循环窗口"),
                          ("decode", "解码"), ("ap", "U-Net 计算"), ("move", "搬运"), ("pp", "PL 后处理"),
                          ("plother", "PS 调度 / 等待")):
            tk.Frame(leg, bg=COL[key], width=12, height=12, highlightthickness=1,
                     highlightbackground="#666").pack(side="left", padx=(0, 4))
            uistyle.label(leg, name, font=uistyle.FONT_SMALL, fg=uistyle.C_MUTED).pack(side="left", padx=(0, 12))
        self.var = tk.StringVar(value="")
        uistyle.label(self.top, var=self.var, font=uistyle.FONT_SMALL, bg=uistyle.C_BG, justify="left",
                      anchor="w", wraplength=self.W).pack(fill="x", padx=12, pady=(0, 12))

    def add(self, title, kind, r, note=""):
        self.runs.append((title, kind, r, note))
        self.draw()

    def draw(self):
        cv = self.cv
        cv.delete("all")
        data = [(t, k, segments(k, r), note) for t, k, r, note in self.runs]
        tmax = max([d[2][0] for d in data] + [1.0])
        scale = (self.W - self.LEFT - 20) / tmax
        y = 10
        cv.create_text(self.LEFT, y, anchor="nw", text="0", font=("Consolas", 8))
        cv.create_text(self.W - 20, y, anchor="ne", text="%.0f ms" % tmax, font=("Consolas", 8))
        y += 14
        lines = ["数据来源：软件计时（PC 墙钟 + 板端任务结果 ms / pl_ms / pl_split / pp_ms），不是硬件周期计数器；"
                 "PL 泳道是在 PL 阶段窗口内按各项累计时间的比例画的，不是逐次的真实位置。"]
        for title, kind, (total, seg, s), note in data:
            cv.create_text(8, y, anchor="nw", text=title, font=("Microsoft YaHei UI", 9, "bold"))
            y += 18
            for li, name in enumerate(LANES):
                if kind == "echo" and li == 2:
                    continue
                cv.create_text(8, y + 4, anchor="nw", text=name, font=("Microsoft YaHei UI", 8))
                for lane, a, b, key, label in seg:
                    if lane != li:
                        continue
                    x0, x1 = self.LEFT + a * scale, self.LEFT + max(b * scale, a * scale + 1)
                    cv.create_rectangle(x0, y, x1, y + self.LANE_H - 6, fill=COL[key], outline="#666")
                    if x1 - x0 > 24:                      # 字比色块宽就不写（否则溢到旁边的块上）
                        t = cv.create_text((x0 + x1) / 2, y + (self.LANE_H - 6) / 2, text=label,
                                           font=("Microsoft YaHei UI", 7))
                        bx = cv.bbox(t)
                        if bx and bx[2] - bx[0] > x1 - x0 - 4:
                            cv.delete(t)
                y += self.LANE_H
            y += 8
            sh = lambda v: 100.0 * v / s["total_ms"] if s["total_ms"] else 0.0
            lines.append("%s：合计 %.0f ms｜网络 %.0f（%.0f%%）｜U-Net 计算 %.0f（%.0f%%）｜搬运 %.0f（%.0f%%）｜PL 后处理 %.0f（%.0f%%）"
                         "｜PS 后处理 / 分析 %.0f（%.0f%%）%s" % (
                             title, s["total_ms"], s["upload_ms"] + s["return_ms"], sh(s["upload_ms"] + s["return_ms"]),
                             s["pl_compute_ms"], sh(s["pl_compute_ms"]), s["pl_move_ms"], sh(s["pl_move_ms"]), s["pl_post_ms"],
                             sh(s["pl_post_ms"]), s["ps_post_ms"], sh(s["ps_post_ms"]), ("｜" + note) if note else "")
                         + ("" if s["split"] else "｜板端 v5.15 及以前：U-Net 计算与搬运合在一起"))
        cv.configure(height=y + 4)
        self.var.set("\n".join(lines))
