# -*- coding: utf-8 -*-
"""把几个"灰度下限"切出来的区域画成蒙太奇，用眼睛确认背景到底在哪一段。

输出 512x512 的 2x2：每格 256x256（原图 1/2 抽样），白 = 该像素灰度 >= 下限。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import imageio  # noqa: E402
import proto    # noqa: E402

CUTS = (8, 24, 32, 48)

img, _ = imageio.load_image(os.path.join(HERE, "test.ppm"))
gray = imageio._luma_gray(img)
h = imageio.luma_histogram(img)
tot = sum(h)
for c in CUTS:
    print("灰度 < %2d : %7d 像素 (%.1f%%)" % (c, sum(h[:c]), 100.0 * sum(h[:c]) / tot))

out = bytearray(512 * 512 * 3)
for k, c in enumerate(CUTS):
    ox = (k % 2) * 256
    oy = (k // 2) * 256
    for y in range(256):
        sy = y * 2
        for x in range(256):
            v = 255 if gray[sy * 512 + x * 2] >= c else 0
            p = ((oy + y) * 512 + ox + x) * 3
            out[p] = out[p + 1] = out[p + 2] = v

path = os.path.join(HERE, "_cuts.png")
imageio.save_image(path, bytes(out))
print("wrote %s  (格子顺序: 左上<8  右上<24  左下<32  右下<48)" % path)
