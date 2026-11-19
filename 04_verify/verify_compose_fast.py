# -*- coding: utf-8 -*-
"""verify_compose_fast.py —— 加速版 compose_preview 与原版**逐字节相同**的对拍。

2026-11-19：合成路径里「PIL resize + paste(颜色, 掩膜)」换成 numpy 布尔上色
（`_up_nearest` + `arr[em] = col`），关键帧换帧 21→9 ms、现场 480² 41→20 ms。
最近邻的索引是按 PIL 的定义取的，但**必须实测确认**：这里把原版实现整段抄进来，
用随机掩膜 / 多种方块尺寸（放大、缩小、越界裁切、dec=2）逐像素比对。

    DISPLAY=:0 python3 verify_compose_fast.py          # 板上跑（用板上的 numpy / Pillow）
    python verify_compose_fast.py                      # PC 上也能跑（需 numpy + Pillow）
"""
from __future__ import print_function

import os
import sys

ROOT = os.environ.get("WS_ROOT", "/data/ws2026")
APP = os.environ.get("WS_APP", os.path.join(ROOT, "app"))
if os.path.isdir(os.path.join(ROOT, "shim")):
    sys.path.insert(0, os.path.join(ROOT, "shim"))
    try:
        import bootstrap
        bootstrap.install(app_dir=APP)
    except Exception:
        sys.path.insert(0, APP)
else:
    sys.path.insert(0, APP)

import numpy as np            # noqa: E402
from PIL import Image         # noqa: E402
import echo_ws as ws          # noqa: E402

FAIL = []


def chk(name, cond, detail=""):
    print("%-46s %s %s" % (name, "PASS" if cond else "FAIL", detail))
    if not cond:
        FAIL.append(name)


# ---------------------------------------------------------------- 原版（11-19 之前的实现）
def compose_old(native, t, crop, dec, mask, rh_items, frame112, vw, vh):
    if native is None:
        k = max(1, int(round(vw / float(ws.N))))
        rgb = ws.contour_rgb(frame112, mask, k)
        for m_, col in rh_items:
            rgb[ws._edge(m_, k)] = col
        im = Image.fromarray(rgb)
        return im if im.size == (vw, vh) else im.resize((vw, vh), Image.BILINEAR)
    f = native[t]
    H, W = f.shape[:2]
    im = Image.fromarray(np.ascontiguousarray(f)).convert("RGB")
    kd = vw / float(W)
    if (vw, vh) != (W, H):
        im = im.resize((vw, vh), Image.NEAREST if kd >= 1 else Image.BILINEAR)
    if crop:
        d = float(dec or 1)
        side = int(round(crop["side"] / d * kd))
        x0 = int(round(crop["x"] / d * kd))
        y0 = int(round(crop["y"] / d * kd))
        xs0, ys0 = max(0, x0), max(0, y0)
        xs1, ys1 = min(vw, x0 + side), min(vh, y0 + side)
        if side > 1 and xs1 > xs0 and ys1 > ys0:
            for m_, col in [(mask, (255, 64, 64))] + list(rh_items):
                e = ws._edge(np.asarray(m_, bool), 1)
                em = Image.fromarray((e * 255).astype(np.uint8)).resize((side, side), Image.NEAREST)
                if (xs0, ys0, xs1, ys1) != (x0, y0, x0 + side, y0 + side):
                    em = em.crop((xs0 - x0, ys0 - y0, xs1 - x0, ys1 - y0))
                im.paste(col, (xs0, ys0), em)
    return im


def same(a, b):
    if a.size != b.size:
        return False, "尺寸 %r vs %r" % (a.size, b.size)
    if a.mode != b.mode:
        return False, "模式 %r vs %r" % (a.mode, b.mode)
    x, y = np.asarray(a), np.asarray(b)
    if x.shape != y.shape:
        return False, "形状 %r vs %r" % (x.shape, y.shape)
    d = np.abs(x.astype(np.int16) - y.astype(np.int16))
    n = int((d > 0).sum())
    return n == 0, "不同像素 %d 个（最大差 %d）" % (n, int(d.max()) if n else 0)


def rand_mask(rng, n=112):
    m = np.zeros((n, n), bool)
    for _ in range(6):
        y, x = rng.randint(4, n - 24), rng.randint(4, n - 24)
        h, w = rng.randint(6, 28), rng.randint(6, 28)
        m[y:y + h, x:x + w] = True
    return m


def main():
    rng = np.random.RandomState(20261119)
    cases = 0
    # 原生帧尺寸 / 显示尺寸 / 方块（含越界、放大、缩小、dec）
    for (H, W) in ((422, 636), (480, 480), (240, 320)):
        native = rng.randint(0, 255, (2, H, W)).astype(np.uint8)
        for (vw, vh) in ((W, H), (675, 448), (360, 270)):
            kd = vw / float(W)
            for side in (112, 150, 300, 480, 60, 37):
                for (cx, cy) in ((20, 30), (-40, -25), (W - 30, H - 20)):
                    for dec in (1, 2):
                        crop = dict(x=cx, y=cy, side=side * dec)
                        mask = rand_mask(rng)
                        rh = [(rand_mask(rng), (150, 80, 220))]
                        if rng.randint(0, 2):
                            rh.append((rand_mask(rng), (240, 120, 40)))
                        a = compose_old(native, 0, crop, dec, mask, rh, None, vw, vh)
                        b = ws.compose_preview(native, 0, crop, dec, mask, rh, None, vw, vh)
                        ok, why = same(a, b)
                        cases += 1
                        if not ok:
                            chk("native %dx%d vw=%d side=%d dec=%d crop=(%d,%d)"
                                % (W, H, vw, side, dec, cx, cy), False, why)
    chk("原生帧路径：随机用例逐字节相同", not FAIL, "%d 个用例" % cases)

    # 没有原生帧（112 放大）那条路没改，确认与原版一致
    frame112 = rng.randint(0, 255, (112, 112)).astype(np.uint8)
    mask = rand_mask(rng)
    rh = [(rand_mask(rng), (150, 80, 220))]
    a = compose_old(None, 0, None, 1, mask, rh, frame112, 448, 448)
    b = ws.compose_preview(None, 0, None, 1, mask, rh, frame112, 448, 448)
    ok, why = same(a, b)
    chk("112 放大路径不变（同一掩膜）", ok, why)

    # 无掩膜 / 无裁剪：不该多拷贝
    a = compose_old(native, 0, None, 1, mask, [], None, 675, 448)
    b = ws.compose_preview(native, 0, None, 1, mask, [], None, 675, 448)
    ok, why = same(a, b)
    chk("无裁剪路径", ok, why)

    print("\n%d FAIL" % len(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
