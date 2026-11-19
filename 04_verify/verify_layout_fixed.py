# -*- coding: utf-8 -*-
"""verify_layout_fixed.py —— 板端回归：关键帧分析完成、全心卡片出文字后，三栏不许移位。

为什么要有这条（2026-11-19）：
    用户报「关键帧模式分析结束后，中间框全心判断输出文字时左右两个框向两侧移动、还有残留」。
    根因在板端 GTK 兼容层：`wraplength`（像素）换算 `max_width_chars` 时写死 8 px/字符，
    中文实际约 16 px，于是折行标签的自然宽度被放宽近一倍 —— 全心卡片里那句
    「提示运动减低：……（建议复核）」把中间栏从 450 撑到 558、右栏 404 撑到 441，
    body 1577→1722，而 body 在窗口里是 sticky="n"（居中）的 → 左栏向左、右栏向右各移 72 px。

    修法：兼容层改用 Pango 量当前文本的实际字宽再换算字符数（`Label._chars_for_px`）。

用法（板上）：
    DISPLAY=:0 python3 verify_layout_fixed.py            # 真跑一次分析，比对前后几何
    DISPLAY=:0 python3 verify_layout_fixed.py --shot /tmp/v   # 另外存前后截图
退出码 0 = 全部 PASS。
"""
from __future__ import print_function

import os
import struct
import subprocess
import sys
import time

ROOT = os.environ.get("WS_ROOT", "/data/ws2026")
sys.path.insert(0, ROOT + "/shim")
import bootstrap  # noqa: E402
bootstrap.install(app_dir=ROOT + "/app")

import tkinter as tk  # noqa: E402
import workstation as W  # noqa: E402

VIDEO = os.environ.get("WS_VIDEO", "/root/echo_src/demo_a4c.y8")
PFX = sys.argv[sys.argv.index("--shot") + 1] if "--shot" in sys.argv else None
FAIL = []


def chk(name, cond, detail=""):
    print("%-34s %s %s" % (name, "PASS" if cond else "FAIL", detail))
    if not cond:
        FAIL.append(name)


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


def pos(w, top):
    try:
        r = w._gtk.translate_coordinates(top._gtk, 0, 0)
        return (r[1], r[2]) if isinstance(r, tuple) and len(r) == 3 else r
    except Exception:
        return None


def geom(root, view):
    body = view.frame.grid_slaves(row=1, column=0)[0]
    cols = {}
    for c in body.grid_slaves():
        gi = c.grid_info()
        if gi.get("row", 0) == 0:
            cols[gi.get("column")] = (pos(c, root), c.winfo_width(), c.winfo_height())
    return dict(body_w=body.winfo_width(), cols=cols)


def shot(tag):
    if not PFX:
        return
    try:
        subprocess.call(["scrot", "-u", "%s_%s.png" % (PFX, tag)])
    except Exception:
        pass


def main():
    if not os.path.exists(VIDEO):
        print("SKIP：没有样例视频 %s（设 WS_VIDEO 指一个 .y8）" % VIDEO)
        return 0
    root = tk.Tk()
    root.geometry("1920x1080+0+0")
    app = W.HeartApp(root, "127.0.0.1", 5060, "keyframe")
    pump(root, 12)
    app.switch("keyframe")
    pump(root, 8)
    view = app.views["keyframe"]

    import numpy as np
    import cv2
    with open(VIDEO, "rb") as f:
        head = f.read(32)
        W_, H_, N_ = struct.unpack("<III", head[4:16])
        fps_ = struct.unpack("<d", head[16:24])[0]
        buf = f.read(W_ * H_ * N_)
    grey = np.frombuffer(buf, np.uint8)[:W_ * H_ * N_].reshape(N_, H_, W_)
    v112, crop = view.ws.screen_to_112(grey)
    first = cv2.cvtColor(grey[0], cv2.COLOR_GRAY2BGR)
    view._on_loaded(VIDEO, v112, fps_, dict(frame=first, crop=crop, path=VIDEO, frames=grey, dec=1))
    pump(root, 12)
    a = geom(root, view)
    shot("before")

    view.run()
    ok = wait(root, lambda: view.result is not None, 300)
    pump(root, 30)
    chk("analysis finished", ok)
    b = geom(root, view)
    shot("after")

    print("body  %d -> %d" % (a["body_w"], b["body_w"]))
    chk("body 宽度不变", a["body_w"] == b["body_w"], "%d -> %d" % (a["body_w"], b["body_w"]))
    for col in sorted(a["cols"]):
        pa, wa, _ha = a["cols"][col]
        pb, wb, _hb = b["cols"][col]
        print("col%d  x %s -> %s   w %d -> %d" % (col, pa, pb, wa, wb))
        chk("col%d 位置不变" % col, pa == pb, "%s -> %s" % (pa, pb))
        chk("col%d 宽度不变" % col, wa == wb, "%d -> %d" % (wa, wb))

    # 全心卡片确实出了文字（否则这条回归是空跑）
    chk("全心文字已输出", len(view.var_heart.get()) > 20, "%d 字" % len(view.var_heart.get()))

    try:
        app.close()
    except Exception:
        pass
    print("\n%d FAIL" % len(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
