# -*- coding: utf-8 -*-
"""probe_render.py —— 换一帧的时间到底花在哪：合成 / 画布 _render / GTK 刷新，分开量。

    WS_ROOT=/tmp/ws_stage DISPLAY=:0 python3 probe_render.py
"""
from __future__ import print_function

import os
import struct
import sys
import time
import traceback

ROOT = os.environ.get("WS_ROOT", "/data/ws2026")
sys.path.insert(0, ROOT + "/shim")
import bootstrap  # noqa: E402
bootstrap.install(app_dir=ROOT + "/app")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import tkinter as tk  # noqa: E402
import workstation as W  # noqa: E402

from tkinter import _canvas as CV  # noqa: E402

VIDEO = "/root/echo_src/demo_a4c.y8"
STAT = {"n": 0, "t": 0.0, "fast": 0}


def pump(root, n=3, dt=0.05):
    for _ in range(n):
        root.update_idletasks()
        root.update()
        time.sleep(dt)


def main():
    print("=== %s ===" % ROOT)
    orig_render = CV.Canvas._render

    def timed(self):
        t0 = time.time()
        orig_render(self)
        dt = time.time() - t0
        STAT["n"] += 1
        STAT["t"] += dt
    CV.Canvas._render = timed

    root = tk.Tk()
    app = W.HeartApp(root, "127.0.0.1", 5060, "keyframe")
    pump(root, 8)
    app.switch("keyframe")
    pump(root, 5)
    view = app.views["keyframe"]

    with open(VIDEO, "rb") as f:
        head = f.read(32)
        Wd, Hd, Nd = struct.unpack("<III", head[4:16])
        fps = struct.unpack("<d", head[16:24])[0]
        buf = f.read(Wd * Hd * Nd)
    grey = np.frombuffer(buf, np.uint8)[:Wd * Hd * Nd].reshape(Nd, Hd, Wd)
    v112, crop = view.ws.screen_to_112(grey)
    view._on_loaded(VIDEO, v112, fps,
                    dict(frame=cv2.cvtColor(grey[0], cv2.COLOR_GRAY2BGR), crop=crop,
                         path=VIDEO, frames=grey, dec=1))
    pump(root, 5)
    for _ in range(30):
        pump(root, 1, 0.2)
    t0 = time.time()
    view.run()
    while time.time() - t0 < 120 and view.result is None:
        pump(root, 1, 0.2)
    for _ in range(20):
        pump(root, 1, 0.2)          # 等预取 / 自动定标安静下来

    # 预热若干帧（填照片缓存）
    for t in range(12):
        view._render_frame(t)
        pump(root, 1, 0.02)

    print("%-28s %8s %8s %8s" % ("动作", "计算", "_render", "update"))
    for t in (3, 4, 5, 6, 7, 8):
        STAT.update(n=0, t=0.0)
        t0 = time.time()
        view._render_frame(t)                    # 纯计算（命中缓存）
        t1 = time.time()
        root.update_idletasks()                  # 画布 _render + GTK 布局
        t2 = time.time()
        root.update()                            # GTK 真正画到屏幕上
        t3 = time.time()
        print("%-28s %6.1fms %6.1fms %6.1fms   (画布 _render %.1fms x%d)" % (
            "换到第 %d 帧" % t, (t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000,
            STAT["t"] / max(STAT["n"], 1) * 1000, STAT["n"]))

    # 原地不动再刷一次（同样的帧）
    STAT.update(n=0, t=0.0)
    t0 = time.time()
    root.update_idletasks()
    t1 = time.time()
    print("%-28s %8s %6.1fms" % ("同一帧再刷一次", "-", (t1 - t0) * 1000))

    # 真实拖动 A：拖进度条（scale 是事件源，from_scale=True，不回头设 scale）
    t0 = time.time()
    for t in range(30):
        view._goto(t, from_scale=True, defer=True)
        root.update()
    a = (time.time() - t0) / 30 * 1000
    # 真实拖动 B：点/拖容积曲线（会回头设 scale）
    t0 = time.time()
    for t in range(30):
        view._goto(t, defer=True)
        root.update()
    b = (time.time() - t0) / 30 * 1000
    # 真实拖动 C：走界面的 _curve_click
    t0 = time.time()
    for t in range(30):
        view._curve_click(float(60 + t * 6))
        root.update()
    c = (time.time() - t0) / 30 * 1000
    print("拖动 A（进度条，from_scale）: %.1f ms/次" % a)
    print("拖动 B（曲线，设 scale）    : %.1f ms/次" % b)
    print("拖动 C（_curve_click 真入口）: %.1f ms/次" % c)

    try:
        app.close()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
