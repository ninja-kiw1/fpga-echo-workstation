# -*- coding: utf-8 -*-
"""verify_hr_ui.py —— 心率那一格的界面冒烟测试（11-19）。

心率改成「最近 5 拍瞬时心率平均」以后，确认三件事：
  1. 主窗口还能建起来（关键帧 / 现场两个视图），结果卡片的标题写的是「心率（近 5 拍）」；
  2. 这一格没有被撑宽（11-19「输出文字把边框撑得左右跳」那条的回归检查，reqwidth 要远小于 RW）；
  3. 现场模式喂一段真实容积曲线走 _on_prog：显示的是最近 5 拍平均；曲线太短时退回板上那个数。

需要图形环境（Windows 直接跑；没有 Tk 的环境会跳过并返回 0）。
    python verify_hr_ui.py [上位机目录]
仅用于工程教学与算法演示，不作为临床诊断依据。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
UI = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(HERE), "07_ui")
sys.path.insert(0, UI)

try:
    import tkinter as tk
    import workstation as W
    root = tk.Tk()
except Exception as e:                                            # 没有图形环境 / 没有 tkinter
    print("跳过（建不了 Tk 窗口：%s）" % e)
    sys.exit(0)

fx = json.load(open(os.path.join(HERE, "golden", "hr_es000108.json"), encoding="utf-8"))
ok = True
root.withdraw()                                                   # 只建控件，不真显示
app = W.HeartApp(root, "192.168.1.254", 5060, mode="keyframe")
root.update_idletasks()


def walk(w, hits):
    for c in w.winfo_children():
        try:
            t = c.cget("text")
        except Exception:
            t = None
        if t and "心率" in str(t):
            hits.append((str(t), c))
        walk(c, hits)


hits = []
walk(app.views["keyframe"].frame, hits)
print("心率标签：%r" % ([h[0] for h in hits],))
if not any("近 %d 拍" % W.HR_RECENT in t for t, _ in hits):
    print("  [FAIL] 标签没写清口径")
    ok = False
box = hits[0][1].master if hits else None
if box is not None:
    print("心率那一格 reqwidth = %d（结果栏 RW = %d）" % (box.winfo_reqwidth(), W.RW))
    if box.winfo_reqwidth() > W.RW:
        print("  [FAIL] 撑宽了结果栏")
        ok = False

app.switch("live")
root.update_idletasks()
lv = app.views["live"]
lv.vols, lv.fps = list(fx["vols"]), fx["fps"]
lv._hr_key, lv._hr_val = None, None
lv._on_prog(dict(stage="live", beats=len(fx["beats"]), ef=42.0, hr=fx["hr_board"]))
print("现场滚动：心率显示 %r（板上给的是 %.0f）" % (lv.var_hr.get(), fx["hr_board"]))
if lv.var_hr.get() != "112 次/分":
    print("  [FAIL] 现场滚动没用最近 5 拍平均")
    ok = False
lv.vols, lv._hr_key, lv._hr_val = [0.5, 0.6], None, None
lv._on_prog(dict(stage="live", beats=2, ef=42.0, hr=88.0))
print("曲线太短：心率显示 %r（应退回板上 88）" % lv.var_hr.get())
if lv.var_hr.get() != "88 次/分":
    print("  [FAIL] 退回板上值不对")
    ok = False

root.destroy()
print("\n%s" % ("通过" if ok else "有不通过项"))
sys.exit(0 if ok else 1)
