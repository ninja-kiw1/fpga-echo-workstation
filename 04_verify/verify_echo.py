# -*- coding: utf-8 -*-
"""verify_echo.py —— 板端版上位机的端到端自检（真跑一段 A4C，再量拖动）。

    WS_ROOT=/tmp/ws_stage DISPLAY=:0 python3 verify_echo.py

检查项：
  1. 载入 /root/echo_src/demo_a4c.y8（99 帧 636x422），原生帧 / 裁剪方块拿到
  2. 真跑一次关键帧分析（引擎 v5.28），出 EF / 掩膜 / 全心
  3. 右心触边判定必须是关的（n_border == 0）
  4. 拖动路径：合并事件后每次换帧的耗时（冷 / 热）
  5. 截图（scrot）
"""
from __future__ import print_function

import os
import subprocess
import sys
import time
import traceback

ROOT = os.environ.get("WS_ROOT", "/data/ws2026")
sys.path.insert(0, ROOT + "/shim")
import bootstrap  # noqa: E402
bootstrap.install(app_dir=ROOT + "/app")

import tkinter as tk  # noqa: E402
import workstation as W  # noqa: E402

VIDEO = os.environ.get("WS_VIDEO", "/root/echo_src/demo_a4c.y8")
SHOT = os.environ.get("WS_SHOT", "/tmp/ws_shot.png")
FAIL = []


def chk(name, cond, detail=""):
    print("%-44s %s %s" % (name, "PASS" if cond else "FAIL", detail))
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


def main():
    root = tk.Tk()
    app = W.HeartApp(root, "127.0.0.1", 5060, "keyframe")
    pump(root, 10)
    app.switch("keyframe")
    pump(root, 6)
    view = app.views["keyframe"]

    # ---- 1) 载入（.y8 是服务端的信号源格式，read_video 读不了：这里自己解出来喂 _on_loaded，
    #         走的就是"打开一段录屏"之后的同一条路）
    import struct
    import numpy as np
    import cv2
    with open(VIDEO, "rb") as f:
        head = f.read(32)
        W_, H_, N_ = struct.unpack("<III", head[4:16])
        fps_ = struct.unpack("<d", head[16:24])[0]
        buf = f.read(W_ * H_ * N_)
    grey = np.frombuffer(buf, np.uint8)[:W_ * H_ * N_].reshape(N_, H_, W_)
    chk("y8 header", head[:4] == b"Y8V1" and N_ > 0, "%dx%d %d frames %.1f fps" % (W_, H_, N_, fps_))
    t0 = time.time()
    v112, crop = view.ws.screen_to_112(grey)
    first_bgr = cv2.cvtColor(grey[0], cv2.COLOR_GRAY2BGR)
    view._on_loaded(VIDEO, v112, fps_,
                    dict(frame=first_bgr, crop=crop, path=VIDEO, frames=grey, dec=1))
    pump(root, 6)
    ok = view.frames is not None and len(view.frames) == int(N_)
    chk("load video", ok, "%d frames %.1fs" % (len(view.frames) if view.frames is not None else 0, time.time() - t0))
    if not ok:
        return 1
    chk("fps", abs(float(view.fps) - 30.0) < 0.5, "fps=%.2f" % view.fps)
    chk("native frames for preview", view.native is not None,
        "native=%r crop=%r dec=%r" % (type(view.native).__name__, view.crop, view.ndec))

    # ---- 2) 真跑分析
    t0 = time.time()
    view.run()
    ok = wait(root, lambda: view.result is not None, 300)
    wall = time.time() - t0
    chk("analysis finished", ok, "%.1f s（%d 帧）" % (wall, len(view.frames)))
    if not ok:
        print("STATUS: %s" % (view.var_status.get() if hasattr(view, "var_status") else "?"))
        return 1
    ef = view.result.get("ef")
    print("EF=%.1f%%  beats=%d  masks=%d" % (ef, len(view.result.get("beats") or []), len(view.masks)))
    chk("EF in range", ef is not None and 0 < float(ef) < 100, "ef=%r" % (ef,))
    chk("masks for all frames", len(view.masks) == len(view.frames), "%d/%d" % (len(view.masks), len(view.frames)))

    res = (view.raw or {}).get("result") or {}
    rh = res.get("right_heart") or {}
    if rh:
        chk("right-heart came back", True, "rv_lv=%r n_rv=%r" % (rh.get("rv_lv"), rh.get("n_rv")))
        chk("border rule OFF (n_border==0)", int(rh.get("n_border") or 0) == 0,
            "n_border=%r" % (rh.get("n_border"),))
    else:
        print("right_heart: 没有（这一版程序可能没配生命体征/全心）")

    # ---- 3) 拖动路径耗时
    # 分开量：①纯计算（缓存命中后换一帧）—— 这一项才反映我们的代码，
    #        ②含 GTK 刷新 —— 这块板上刷新本身就能从 1 ms 抖到 90 ms，只报数不判定。
    n = len(view.frames)
    m = min(n, 30)
    view._vclear()
    t0 = time.time()
    for t in range(m):                       # 冷：从没看过的帧（要现合成）
        view._render_frame(t)
    cold = (time.time() - t0) / m * 1000
    t0 = time.time()
    for t in range(m):                       # 热：全命中
        view._render_frame(t)
    warm = (time.time() - t0) / m * 1000
    chk("warm frame compute < 40 ms", warm < 40.0, "冷 %.1f ms / 热 %.1f ms（纯计算）" % (cold, warm))
    t0 = time.time()
    for t in range(m):
        view._goto(t, from_scale=True, defer=True)
        root.update_idletasks()
    flush = (time.time() - t0) / m * 1000
    print("拖进度条含刷新 %.1f ms/步（GTK 刷新占大头，仅作参考）" % flush)

    # 合并事件：60 个连续 defer 只画 1 帧
    view._vclear()
    cnt = [0]
    orig = view._render_frame

    def counting(t, settle=True):
        cnt[0] += 1
        return orig(t, settle=settle)
    view._render_frame = counting
    t0 = time.time()
    for k in range(60):
        view._goto(k % n, defer=True)
    ev = (time.time() - t0) / 60.0 * 1000
    root.update()
    view._render_frame = orig
    chk("60 drag events -> 1 render", cnt[0] == 1, "renders=%d 事件 %.2f ms/个" % (cnt[0], ev))

    # ---- 4) 回到中间帧、截图
    view._goto(n // 2)
    view._redraw()
    pump(root, 20)
    try:
        subprocess.call(["scrot", SHOT])
        print("SHOT %s" % SHOT)
    except Exception:
        traceback.print_exc()

    try:
        app.close()
    except Exception:
        pass
    print("\n%d FAIL" % len(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
