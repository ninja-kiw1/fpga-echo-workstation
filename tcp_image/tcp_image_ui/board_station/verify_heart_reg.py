# -*- coding: utf-8 -*-
"""verify_heart_reg.py —— 板上 DP（tkinter -> GTK3 兼容层）：快照模式 -> 「连续帧配准…」窗口能打开、PL 配准引擎找回已知平移。

    python3 -u verify_heart_reg.py
"""
import os
import sys
import time

sys.argv = [sys.argv[0]]
import station_heart  # noqa: E402,F401

import heartapp  # noqa: E402
import tkinter as tk  # noqa: E402
from open_wins import shoot_rect  # noqa: E402


def main():
    root = tk.Tk()
    app = heartapp.HeartApp(root, "127.0.0.1", 5060, "snapshot")

    def pump(c, t):
        t0 = time.time()
        while not c() and time.time() - t0 < t:
            root.update()
            time.sleep(0.01)
        return c()
    sv = app.views["snapshot"]
    ok = sv.load(heartapp.DEMO_SNAPSHOT)
    rw = sv._open_reg()
    rw.var_a.set("1")
    rw.var_b.set("2")
    rw.var_dx.set("3")
    rw.var_dy.set("-2")
    rw.start("full")
    pump(lambda: rw.result is not None or "失败" in rw.var_txt.get(), 60)
    print("loaded", ok, "|", rw.var_txt.get().replace("\n", " | "))
    for _ in range(30):
        root.update()
        time.sleep(0.03)
    try:
        shoot_rect("/data/board_station/logs/heart_reg.png", rw.top.winfo_rootx(), rw.top.winfo_rooty(),
                   rw.top.winfo_width(), rw.top.winfo_height())
    except Exception as ex:
        print("shot skipped", ex)
    good = rw.result is not None and rw.result["best"] == (3, -2)
    root.destroy()
    print("HEART_REG_PASS" if good else "HEART_REG_FAIL")


if __name__ == "__main__":
    main()
