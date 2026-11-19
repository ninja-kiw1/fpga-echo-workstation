# -*- coding: utf-8 -*-
"""bench_drag.py —— 换一帧的耗时拆解（新旧两版都能跑，用 WS_ROOT 切）。

    WS_ROOT=/tmp/ws_stage DISPLAY=:0 python3 bench_drag.py
    WS_ROOT=/data/ws2026   DISPLAY=:0 python3 bench_drag.py

打印：合成（冷）/ 照片命中 / _goto 全路径 / 合并事件批量 四组，外加缓存命中计数。
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

VIDEO = os.environ.get("WS_VIDEO", "/root/echo_src/demo_a4c.y8")


def pump(root, n=4, dt=0.05):
    for _ in range(n):
        root.update_idletasks()
        root.update()
        time.sleep(dt)


def wait(root, cond, timeout, step=0.2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        pump(root, 1, step)
        if cond():
            return True
    return False


def bench(fn, n, root, flush=True):
    t0 = time.time()
    for i in range(n):
        fn(i)
        if flush:
            root.update_idletasks()
    return (time.time() - t0) / n * 1000.0


def main():
    print("=== WS_ROOT=%s ===" % ROOT)
    print("workstation: %s  %.1f KB" % (W.__file__, os.path.getsize(W.__file__) / 1024.0))
    root = tk.Tk()
    app = W.HeartApp(root, "127.0.0.1", 5060, "keyframe")
    pump(root, 10)
    app.switch("keyframe")
    pump(root, 6)
    view = app.views["keyframe"]

    with open(VIDEO, "rb") as f:
        head = f.read(32)
        Wd, Hd, Nd = struct.unpack("<III", head[4:16])
        fps = struct.unpack("<d", head[16:24])[0]
        buf = f.read(Wd * Hd * Nd)
    grey = np.frombuffer(buf, np.uint8)[:Wd * Hd * Nd].reshape(Nd, Hd, Wd)
    v112, crop = view.ws.screen_to_112(grey)
    first = cv2.cvtColor(grey[0], cv2.COLOR_GRAY2BGR)
    view._on_loaded(VIDEO, v112, fps, dict(frame=first, crop=crop, path=VIDEO, frames=grey, dec=1))
    pump(root, 6)
    print("载入 %d 帧  %dx%d  显示 %dx%d" % (len(view.frames), Wd, Hd,
                                          getattr(view, "vw", 0), getattr(view, "vh", 0)))

    # 等自动定标跑完（它会抢 CPU，抢着量不准）
    t0 = time.time()
    while time.time() - t0 < 90:
        pump(root, 1, 0.2)
        pend = getattr(view, "_calib_pending", None)
        if (pend is not None and not pend) and (app.calib or time.time() - t0 > 30):
            break
    if hasattr(view, "_show_scale"):
        print("定标：%s" % (view.var_scale.get() if hasattr(view, "var_scale") else "?"))

    # 分析
    t0 = time.time()
    view.run()
    ok = wait(root, lambda: view.result is not None, 300)
    print("分析 %s  %.1f s  EF=%s" % ("OK" if ok else "FAIL", time.time() - t0,
                                     (view.result or {}).get("ef")))

    # 等后台预取 / 补图跑完，不然它们抢 GIL，量出来的都是噪声
    if os.environ.get("WS_V_WARM", "1") != "0":
        pass
    for _ in range(40):
        pump(root, 1, 0.25)

    # ---- 计数：合成函数被调了几次
    cnt = {"build": 0, "photo": 0}
    if hasattr(view, "_vprep_build"):
        orig_build = view._vprep_build
        orig_photo = view._frame_photo

        def build(t):
            cnt["build"] += 1
            return orig_build(t)

        def photo(t):
            cnt["photo"] += 1
            return orig_photo(t)
        view._vprep_build = build
        view._frame_photo = photo

    n = min(30, len(view.frames))
    if hasattr(view, "_vclear"):
        view._vclear()
        cold = bench(lambda i: view._render_frame(i), n, root)
        print("冷（合成+照片+贴图，含 flush）      %6.1f ms/帧   合成调用 %d 次" % (cold, cnt["build"]))
        b0 = cnt["build"]
        warm = bench(lambda i: view._render_frame(i), n, root)
        print("热（全命中，含 flush）              %6.1f ms/帧   合成调用 +%d 次" % (warm, cnt["build"] - b0))
        noflush = bench(lambda i: view._render_frame(i), n, root, flush=False)
        print("热（全命中，不 flush）              %6.1f ms/帧" % noflush)
    idle = bench(lambda i: None, 20, root)
    print("空转（只 update_idletasks）          %6.1f ms/次" % idle)
    g = bench(lambda i: view._goto(i), n, root)
    print("_goto 全路径（含进度条/文字/flush）  %6.1f ms/帧" % g)
    nf = bench(lambda i: view._goto(i), n, root, flush=False)
    print("_goto 全路径（不 flush）            %6.1f ms/帧" % nf)

    # 合并事件
    cnt["photo"] = 0
    t0 = time.time()
    for k in range(60):
        view._goto(k % len(view.frames), defer=True)
    ev = (time.time() - t0) / 60.0 * 1000
    root.update()
    print("60 个拖动事件 %6.2f ms/个（真正出图 %d 次）" % (ev, cnt["photo"]))

    try:
        app.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
