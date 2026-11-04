# -*- coding: utf-8 -*-
"""造一张"纯黑背景 + 眼底盘"的图，用来演示"自动算阈值时排除纯黑/纯白像素"。

圆窗外全部写成纯黑 (0,0,0)，圆窗内是 im0002 原图。
这类图（很多眼底/内窥采集都是黑底圆形视野）正是排除规则要解决的场景。

用法: python make_blackborder_ppm.py [输出路径]
默认: E:/fpga/my_project/tcp_image_ui/test_blackborder.ppm
"""
import os
import struct
import sys

SRC = (r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/"
       r"unet_network/vectors/images/im0002/input.bin")
OUT = (sys.argv[1] if len(sys.argv) > 1
       else r"E:/fpga/my_project/tcp_image_ui/test_blackborder.ppm")
W = H = 512
RADIUS = 232          # 圆窗半径（像素）


def main():
    raw = open(SRC, "rb").read()
    vals = struct.unpack("<%dh" % (W * H * 8), raw)

    rgb = bytearray(W * H * 3)
    cx = cy = (W - 1) / 2.0
    r2 = float(RADIUS) * RADIUS
    black = 0
    for y in range(H):
        dy = y - cy
        for x in range(W):
            dx = x - cx
            p = (y * W + x) * 3
            if dx * dx + dy * dy > r2:
                rgb[p] = rgb[p + 1] = rgb[p + 2] = 0     # 纯黑背景
                black += 1
            else:
                b = (y * W + x) * 8
                rgb[p] = vals[b] & 0xFF
                rgb[p + 1] = vals[b + 1] & 0xFF
                rgb[p + 2] = vals[b + 2] & 0xFF

    hdr = (b"P6\n# test_blackborder.ppm -- im0002 in a pure-black circular window\n"
           b"%d %d\n255\n" % (W, H))
    with open(OUT, "wb") as f:
        f.write(hdr + bytes(rgb))
    print("[ppm] wrote %s (%d bytes)" % (OUT, os.path.getsize(OUT)))
    print("      纯黑像素 %d / %d = %.1f%%（半径 %d 的圆窗）"
          % (black, W * H, 100.0 * black / (W * H), RADIUS))


if __name__ == "__main__":
    main()
