# -*- coding: utf-8 -*-
"""tlprobe.py —— 板端真跑：事件时间线只剩心拍（11-19）。

板上那套 tkinter 是 GTK 兼容层，画布没有 find_all / type / coords，没法像 PC 侧那样把图元捞出来数。
所以这里改成"真画 + 截图"：建 HeartApp、进关键帧模式、灌一组假的逐拍结果，调一次
EchoView._draw_timeline，然后把时间线画布在屏幕上的位置打印出来（TL_BOX x y w h），
窗口停住 N 秒，由外面 scrot 截图核对。

    python3 tlprobe.py            # 画完停 3 秒
    python3 tlprobe.py --hold 25  # 停 25 秒（截图用）
"""
import sys
import time
import traceback

ROOT = "/data/ws2026"
sys.path.insert(0, ROOT + "/shim")
import bootstrap  # noqa: E402
bootstrap.install(app_dir=ROOT + "/app")

import tkinter as tk  # noqa: E402
import workstation as W  # noqa: E402

HOLD = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 3.0


def pump(root, n=6, dt=0.05):
    for _ in range(n):
        root.update_idletasks()
        root.update()
        time.sleep(dt)


def main():
    root = tk.Tk()
    app = W.HeartApp(root, "127.0.0.1", 5060, "keyframe")
    pump(root, 20)
    app.switch("keyframe")
    pump(root, 14)
    v = app.views.get("keyframe")
    print("VIEW %r  TH=%d" % (type(v).__name__, W.TH))

    # ---- 灌一组假的逐拍结果（形状与板上 res 一致）
    n = 12
    v.frames = [None] * n
    v.vols = [10.0, 8.0, 6.0, 4.0, 7.0, 9.0, 11.0, 9.0, 6.0, 5.0, 7.0, 10.0]
    v.fps = 30.0
    v.win = None
    v.cur = 3
    v.kf_cfg = {"a": 1.0, "b": 0.0}
    v.res = {}
    v.keyv = [i % 6 == 0 for i in range(n)]
    v.fixed = {2: "fix", 5: "prop"}
    v.result = {"beats": [dict(ed=1, es=3, v_ed=10.0, v_es=4.0, ef=60.0),
                          dict(ed=6, es=9, v_ed=11.0, v_es=5.0, ef=55.0)]}
    v._draw_timeline()
    v._draw_curve() if hasattr(v, "_draw_curve") else None
    pump(root, 10)

    try:
        x, y = v.tl.winfo_rootx(), v.tl.winfo_rooty()
        w, h = v.tl.winfo_width(), v.tl.winfo_height()
        print("TL_BOX %d %d %d %d" % (x, y, w, h))
    except Exception as exc:
        print("TL_BOX ? %r" % (exc,))
    print("TL_DRAWN hold=%.0fs" % HOLD)
    t0 = time.time()
    while time.time() - t0 < HOLD:
        pump(root, 4)
    app.close()
    print("TLPROBE_DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
