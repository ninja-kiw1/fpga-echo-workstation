# -*- coding: utf-8 -*-
"""把 0..80 的直方图按 4 一档打出来，看暗端有几个峰/谷。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import imageio  # noqa: E402

for name in ("test.ppm", "test_blackborder.ppm"):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        continue
    img, _ = imageio.load_image(p)
    h = imageio.luma_histogram(img)
    tot = sum(h)
    mx = max(h[1:254])
    print("=== %s (总 %d, 单桶最大 %d) ===" % (name, tot, mx))
    print("   灰度区间 : 计数(占比)   条形(每格=单桶最大/40)")
    for lo in range(0, 84, 4):
        c = sum(h[lo:lo + 4])
        bar = "#" * int(round(40.0 * c / (4.0 * mx)))
        print("   %3d-%3d : %7d (%5.2f%%) %s" % (lo, lo + 3, c, 100.0 * c / tot, bar))
    print()
