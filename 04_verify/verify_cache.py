# -*- coding: utf-8 -*-
"""板端版 workstation.py 的离线自检（跑在 PC 上，用真 tkinter，无窗口）。

验三件事：
  1) 能 import、能建界面（HeartApp + EchoView，root.withdraw()）。
  2) 拖动缓存层自洽：_goto(defer=True) 合并成一次 _render_deferred；
     _frame_photo 命中 _vcache；掩膜变了 _vclear 会整批作废；_vprep 命中。
  3) 画面逐像素没变：_frame_photo(t) 出来的图（除 ED/ES 角标那块）与
     "老 _goto 的合成方式"（compose_preview + 全心标记 + 点击标记）逐字节相同。

用法： python verify_cache.py <板端 app 目录>
"""
from __future__ import print_function

import os
import sys

APP = sys.argv[1] if len(sys.argv) > 1 else "."
sys.path.insert(0, APP)

import numpy as np
import tkinter as tk

import workstation as W
from PIL import Image, ImageDraw, ImageTk

FAIL = []
OK = []


def chk(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print("%-46s %s %s" % (name, "PASS" if cond else "FAIL", detail))


class _Cap(object):
    """把 ImageTk.PhotoImage 换成"截获 PIL 图"的桩：_frame_photo 出来的就是这张 PIL 图。"""
    last = None

    def __init__(self, im):
        _Cap.last = im


import PIL.ImageTk as _ITK
_REAL_PHOTO = _ITK.PhotoImage


def _use_cap():
    _ITK.PhotoImage = _Cap


def _use_real():
    _ITK.PhotoImage = _REAL_PHOTO


def photo_to_array(ph):
    """取 _frame_photo 刚做出来的 PIL 图（PhotoImage 桩里截下来的）。"""
    return np.array(_Cap.last.convert("RGB"))


def main():
    root = tk.Tk()
    root.withdraw()
    app = W.HeartApp(root, "127.0.0.1", 5060, "keyframe")
    app.switch("keyframe")
    view = app.views["keyframe"]
    chk("build UI (HeartApp + EchoView)", view is not None)

    # ---- 造一段假视频：36 帧 112x112
    n = 36
    rng = np.random.RandomState(7)
    frames = [rng.randint(0, 255, (112, 112)).astype(np.uint8) for _ in range(n)]
    view.frames = frames
    view.fps = 25.0
    view.keyv = [i % 6 == 0 for i in range(n)]
    view.masks = {}
    for t in range(n):
        m = np.zeros((112, 112), bool)
        m[30:70, 30:70] = True
        m[40:60, 40:60] = False
        view.masks[t] = m
    view.vols = [float(1000 + 100 * np.sin(i / 3.0)) for i in range(n)]
    view.result = dict(beats=[dict(ed=6, es=12, ef=55.0, v_ed=1000.0, v_es=450.0),
                              dict(ed=18, es=24, ef=57.0, v_ed=1010.0, v_es=440.0)], ef=56.0)
    view.fixed = {}
    view.clicks = {5: [(200, 180, True), (260, 220, False)]}
    view.rh_frames = {}
    view.heart = None
    view.native = None
    view.live = False
    view._vclear()
    view._apply_view()

    # ---- 1) 逐像素：_frame_photo 与"老 _goto 的合成"比
    t = 5
    view.cur = t
    old = view._compose(t, view.masks[t], [])
    d = ImageDraw.Draw(old)
    sx, r = view.s, min(max(4.0, 6.0 * view.s), 16.0)
    for x, y, pos in view.clicks.get(t, []):
        col = (0, 230, 90) if pos else (255, 60, 60)
        X, Y = x * sx, y * sx
        d.ellipse((X - r, Y - r, X + r, Y + r), outline=col, width=2)
        d.line((X - r * 0.67, Y, X + r * 0.67, Y), fill=col, width=2)
        if pos:
            d.line((X, Y - r * 0.67, X, Y + r * 0.67), fill=col, width=2)
    want = np.array(old.convert("RGB"))

    view._vclear()
    _use_cap()
    try:
        ph = view._frame_photo(t)
        got = photo_to_array(ph)
    finally:
        _use_real()
    chk("frame size", got.shape[:2] == want.shape[:2], "%s vs %s" % (got.shape[:2], want.shape[:2]))
    hh, ww = min(got.shape[0], want.shape[0]), min(got.shape[1], want.shape[1])
    a, b = got[:hh, :ww].astype(int), want[:hh, :ww].astype(int)
    if view._phase(t):                       # 角标是故意画进图里的，那块不比
        a2, b2 = a.copy(), b.copy()
        a2[0:34, 0:60] = 0
        b2[0:34, 0:60] = 0
    else:
        a2, b2 = a, b
    diff = int(np.abs(a2 - b2).max()) if a2.size else 0
    chk("pixels identical (tag area excluded)", diff == 0, "maxdiff=%d" % diff)

    # ---- 2) 缓存命中
    view._vclear()
    _use_cap()
    try:
        p1 = view._frame_photo(t)
        a1 = photo_to_array(p1)
        prep_hit_before = len(view._vprep)
        p2 = view._frame_photo(t)
        a2 = photo_to_array(p2)
    finally:
        _use_real()
    chk("_vprep caches per frame", len(view._vprep) == prep_hit_before, "n=%d" % len(view._vprep))
    chk("_frame_photo twice same pixels", np.array_equal(a1, a2))

    # ---- 3) _vclear 作废
    view._vcache[t] = p1
    view._vclear()
    chk("_vclear empties caches", not view._vcache and not view._vprep)
    chk("_vclear bumps generation", view._vgen > 0)

    # ---- 4) 拖动合并：连续 20 次 defer 只画最后一帧
    view._vclear()
    calls = {"n": 0}
    orig_render = view._render_frame

    def counting(t_, settle=True):
        calls["n"] += 1
        return orig_render(t_, settle=settle)
    view._render_frame = counting
    for k in range(20):
        view._goto(k, defer=True)
    chk("defer only records cur", view.cur == 19)
    chk("defer schedules once", view._render_pending is True, "pending=%s" % view._render_pending)
    root.update()                            # 跑 after_idle
    chk("after_idle renders exactly once", calls["n"] == 1, "calls=%d" % calls["n"])
    view._render_frame = orig_render

    # ---- 5) 非 defer 直接画
    view._vclear()
    view.cur = 3
    view._goto(7)
    chk("direct _goto renders immediately", view.cur == 7 and 7 in view._vcache)

    # ---- 6) 预取线程（分析完的入口）
    view._vprefetch(delay=1)
    root.update()
    import time
    for _ in range(60):
        root.update()
        if len(view._vprep) >= min(len(view.frames), view._V_PREP_MAX):
            break
        time.sleep(0.02)
    chk("_vprefetch fills prep cache", len(view._vprep) >= 10, "n=%d" % len(view._vprep))

    # ---- 7) _vwarm_photos 补照片
    view._vcache = {}
    view._vorder = []
    view._vwarm_photos(delay=1)
    for _ in range(120):
        root.update()
        if view._vcache:
            break
        time.sleep(0.02)
    chk("_vwarm_photos fills photo cache", bool(view._vcache), "n=%d" % len(view._vcache))

    # ---- 8) 触边判定关闭：HC01 末 8 字节 = 0
    hc = dict(wall_motion=dict(mu=[0.0] * 6, sd=[1.0] * 6, t_seg=0.5, w=[0.0] * 4, t_rec=0.5),
              right_heart=dict(rv_model=21, ra_model=22, min_frac=0.1, max_overlap=0.2, border_px=3.0))
    blk = W.heart_block(hc)
    import struct
    tail = struct.unpack("<d", blk[-8:])[0]
    chk("HC01 border_px forced 0", tail == 0.0, "tail=%r" % tail)

    root.destroy()
    print("\n%d PASS, %d FAIL" % (len(OK), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
