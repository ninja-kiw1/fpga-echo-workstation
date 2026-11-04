# -*- coding: utf-8 -*-
"""界面实测帧率：序列窗口在板上连续跑 N 步（往返循环），记录界面收到结果的速率和实际刷新次数。
    E:\\PLD\\skin\\.venv\\Scripts\\python.exe _measure_seq_fps.py [--steps 200]   （TCL / TK 路径要先固定）
"""
import argparse
import json
import sys
import time
from pathlib import Path
import tkinter as tk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import app       # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--steps", type=int, default=200)
ap.add_argument("--folder", default=str(HERE.parents[1] / "echo" / "samples" / "seq_patient0027_4CH"))
args = ap.parse_args()
root = tk.Tk()
root.withdraw()
ui = app.App(root, "192.168.1.254", 5060)
win = ui._open_sequence()
win.load(args.folder)
win.var_pingpong.set(True)
win._on_pingpong()
renders = {"n": 0}
orig = win._show_step


def counting(k, curve=True):
    renders["n"] += 1
    return orig(k, curve)


win._show_step = counting
t0 = time.perf_counter()
win._toggle_play()


def poll():
    if len(win.tracker.steps) >= args.steps:
        if win.stream_stop is not None:
            win.stream_stop.set()
    if win.stream_stop is None and not win.busy and len(win.tracker.steps) >= args.steps:
        el = time.perf_counter() - t0
        res = dict(steps=len(win.tracker.steps), seconds=round(el, 2), results_per_s=round(len(win.tracker.steps) / el, 2),
                   window_fps_estimate=round(win.fps or 0, 2), screen_updates=renders["n"],
                   updates_per_s=round(renders["n"] / el, 2), compact=win.compact,
                   all_verified=all(s.verified for s in win.tracker.steps))
        print("SEQ_FPS", json.dumps(res), flush=True)
        (HERE / "board_check_v5" / "ui_seq_fps.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        ui.link.close()
        root.destroy()
        return
    root.after(50, poll)


root.after(100, poll)
root.after(120000, root.destroy)
root.mainloop()
