# -*- coding: utf-8 -*-
"""PL 程序台的上板冒烟测试：主界面图 -> [三帧中值 -> 心超分割(吃上一个输出)]，以及序列三帧 -> [运动能量]。"""
import sys, json, time
from pathlib import Path
import tkinter as tk
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import app, imageio
root = tk.Tk(); root.withdraw()
ui = app.App(root, "192.168.1.254", 5060)
ui._load_path(str(HERE.parents[1] / "echo" / "samples" / "patient0027_4CH_ED.png"))
pw = ui._open_progwin()
out = {}
def step(i=0):
    if pw.busy:
        root.after(100, lambda: step(i)); return
    if i == 0:
        pw.var_p[0].set("6 三帧中值"); pw.var_p[1].set("2 心超左心室"); pw.var_in[0].set("吃上一个输出"); pw.var_p[2].set("（不用）")
        pw.run(); root.after(200, lambda: step(1)); return
    if i == 1:
        out["chain"] = pw.var_txt.get()
        imageio.save_image(str(HERE / "board_check_v5" / "progwin_chain_src.png"), ui.image)
        sw = ui._open_sequence(); sw.load(str(HERE.parents[1] / "echo" / "samples" / "seq_patient0027_4CH"))
        pw.var_src.set("序列窗口前/中/后三帧"); pw.var_p[0].set("11 三帧运动能量"); pw.var_p[1].set("（不用）")
        pw.run(); root.after(200, lambda: step(2)); return
    out["motion"] = pw.var_txt.get()
    print("PROGWIN", json.dumps(out, ensure_ascii=False), flush=True)
    ui.link.close(); root.destroy()
root.after(300, step)
root.after(60000, root.destroy)
root.mainloop()
