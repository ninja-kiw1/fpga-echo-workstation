# -*- coding: utf-8 -*-
"""PC 版 07_ui/workstation.py 的冒烟：触边判定关掉之后，界面还能建、HC01 末 8 字节 = 0。

用法： python verify_pc_border.py <07_ui 目录>
"""
from __future__ import print_function

import struct
import sys

APP = sys.argv[1]
sys.path.insert(0, APP)

import tkinter as tk

import workstation as W

FAIL = []


def chk(name, cond, detail=""):
    print("%-50s %s %s" % (name, "PASS" if cond else "FAIL", detail))
    if not cond:
        FAIL.append(name)


def all_widgets(w):
    out = []

    def walk(x):
        for c in x.winfo_children():
            out.append(c)
            walk(c)
    walk(w)
    return out


def main():
    root = tk.Tk()
    root.withdraw()
    app = W.HeartApp(root, "127.0.0.1", 5060, "keyframe")
    app.switch("keyframe")
    view = app.views["keyframe"]
    chk("PC builds main window", view is not None)

    texts = []
    for c in all_widgets(view.frame):
        for opt in ("text",):
            try:
                v = c.cget(opt)
            except Exception:
                continue
            if v:
                texts.append(str(v))
    joined = "\n".join(texts)
    chk("no 「触边判定」 label on UI", "触边判定" not in joined)
    chk("no 触边 anywhere in widget texts", "触边" not in joined)
    chk("no var_border attribute", not hasattr(view, "var_border"))
    chk("no border_px attribute", not hasattr(view, "border_px"))

    hc = dict(wall_motion=dict(mu=[0.0] * 6, sd=[1.0] * 6, t_seg=0.5, w=[0.0] * 4, t_rec=0.5),
              right_heart=dict(rv_model=21, ra_model=22, min_frac=0.1, max_overlap=0.2, border_px=3.0))
    blk = W.heart_block(hc)
    tail = struct.unpack("<d", blk[-8:])[0]
    chk("HC01 border_px forced 0 (json says 3)", len(blk) == 176 and tail == 0.0,
        "len=%d tail=%r" % (len(blk), tail))

    hs = W.jobs.load_heart_config()
    chk("heart config loads", hs is not None)
    if hs:
        bp = hs.get("right_heart", {}).get("border_px")
        chk("json border_px == 0", bp is not None and float(bp) == 0.0, "border_px=%r" % (bp,))

    root.destroy()
    print("\n%d FAIL" % len(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
