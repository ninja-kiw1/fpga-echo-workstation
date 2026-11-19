# -*- coding: utf-8 -*-
"""无头验证：事件时间线只剩心拍一行（11-19）。

沙箱里没有 tkinter，所以先塞一套假的 tkinter 模块，再按值调用 HeartView 的那两个画图函数，
用记账用的假 Canvas 收下所有图元，最后断言：
  1. 标签只有「心拍」一个（不再有「关键帧」「纠错」）；
  2. 没有 31/37/43/49/55/69 那几条竖线（关键帧 / 纠错栏）；
  3. 没有图例文字；
  4. 每拍一个矩形 + 一个 EF 文本；
  5. 两条光标线都画到 TH-3。
用法：python verify_timeline_beats_only.py <PC 版目录或板端版目录>
"""
import os
import sys
import types

DIR = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")


# ---------------------------------------------------------------- 假 tkinter
class _Fake(object):
    def __init__(self, *a, **kw):
        pass

    def __getattr__(self, name):
        return _Fake()

    def __call__(self, *a, **kw):
        return _Fake()


class FakeCanvas(object):
    """把 create_* 记下来，方便断言画了什么。"""

    def __init__(self, *a, **kw):
        self.ops = []

    def delete(self, *a, **kw):
        self.ops.append(("delete",) + a)

    def create_text(self, *a, **kw):
        self.ops.append(("text",) + a + (kw.get("text"),))

    def create_line(self, *a, **kw):
        self.ops.append(("line",) + a)

    def create_rectangle(self, *a, **kw):
        self.ops.append(("rect",) + a)

    def create_oval(self, *a, **kw):
        self.ops.append(("oval",) + a)

    def create_polygon(self, *a, **kw):
        self.ops.append(("poly",) + a)

    def create_image(self, *a, **kw):
        self.ops.append(("image",) + a)


def _fake_tkinter():
    tk = types.ModuleType("tkinter")
    tk.__getattr__ = lambda name: _Fake
    for sub in ("ttk", "filedialog", "messagebox", "font", "colorchooser", "simpledialog"):
        m = types.ModuleType("tkinter." + sub)
        m.__getattr__ = lambda name: _Fake
        setattr(tk, sub, m)
        sys.modules["tkinter." + sub] = m
    tk.Canvas = FakeCanvas
    sys.modules["tkinter"] = tk


_fake_tkinter()
sys.path.insert(0, DIR)
import workstation as W                                              # noqa: E402


# ---------------------------------------------------------------- 造一个最小视图
class View(object):
    fps = 30.0
    win = None
    cur = 3
    vols = [0.0, 1.0]
    fixed = {2: "fix", 5: "prop"}
    keyv = [i % 6 == 0 for i in range(12)]

    def __init__(self):
        self.tl = FakeCanvas()
        self.cv = FakeCanvas()
        self.frames = [None] * 12
        self.result = {"beats": [dict(ed=1, es=4, v_ed=10.0, v_es=4.0, ef=60.0),
                                 dict(ed=6, es=9, v_ed=11.0, v_es=5.0, ef=55.0)]}

    def _xy(self):
        return (lambda t: 10 + 5 * t), (lambda v: 100 - v)

    def _span(self):
        return 0, 12

    def _no_beat_ef(self):
        return False

    def _cal(self, v):
        return v

    _draw_cursor = W.EchoView._draw_cursor
    _draw_timeline = W.EchoView._draw_timeline


v = View()
v._draw_timeline()
ops = v.tl.ops
tl_ops = [o for o in ops if o[0] != "delete"]

labels = [o[-1] for o in tl_ops if o[0] == "text"]
rects = [o for o in tl_ops if o[0] == "rect"]
lines = [o for o in tl_ops if o[0] == "line"]

bad = []
if "关键帧" in labels or "纠错" in labels:
    bad.append("还有被删掉那两栏的标签：%s" % labels)
if "心拍" not in labels:
    bad.append("没有「心拍」标签")
if len(rects) != 2:
    bad.append("心拍矩形 %d 个（期望 2）" % len(rects))
if len([t for t in labels if t.endswith("%")]) != 2:
    bad.append("每拍 EF 文本 %d 个（期望 2）" % len([t for t in labels if t.endswith("%")]))
if any(o[0] == "text" and ("%" not in o[-1] and o[-1] != "心拍") for o in tl_ops):
    bad.append("有多余文字：%s" % [o[-1] for o in tl_ops if o[0] == "text"])
vert = [o for o in lines if len(o) == 5 and abs(o[1] - o[3]) < 1e-9]      # x1 == x2 的竖线
if len(vert) != 1:
    bad.append("时间线上的竖线 %d 条（期望 1 条光标）：%s" % (len(vert), vert))
elif abs(vert[0][2] - 6) > 1e-9 or abs(vert[0][4] - (W.TH - 3)) > 1e-9:
    bad.append("光标 y 范围 %s..%s（期望 6..%s）" % (vert[0][2], vert[0][4], W.TH - 3))
for o in lines:
    for y in (31, 37, 43, 49, 55, 69):
        if y in o[1:5]:
            bad.append("还画着旧栏位的线 y=%d：%s" % (y, o))

print("TH = %d，时间线图元 %d 个：%s" % (W.TH, len(tl_ops), [o[0] for o in tl_ops]))
print("文字：%s" % labels)
for o in tl_ops:
    if o[0] == "rect":
        print("  心拍矩形 x %s..%s  y %s..%s" % (o[1], o[3], o[2], o[4]))
print("光标：%s" % (vert[0] if vert else "无",))
print("FAIL: " + "；".join(bad) if bad else "TIMELINE PASS —— 只剩心拍一行")
sys.exit(1 if bad else 0)
