# -*- coding: utf-8 -*-
"""P5: 用 PLD 的 im0002(真实眼底图)生成一张 512x512 的 P6 PPM 当 test.ppm。

来源是 unet_network 的向量: images/im0002/input.bin = 512x512x8 int16,
lane 0..2 = R,G,B(值域 0..255)。这里抽出来写成标准 P6 PPM。

用法: python make_test_ppm.py [输出路径]
默认: E:/fpga/my_project/tcp_image_ui/test.ppm
"""
import os
import struct
import sys

SRC = (r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/"
       r"unet_network/vectors/images/im0002/input.bin")
OUT = sys.argv[1] if len(sys.argv) > 1 else r"E:/fpga/my_project/tcp_image_ui/test.ppm"
W = H = 512


def main():
    raw = open(SRC, "rb").read()
    assert len(raw) == W * H * 8 * 2, "unexpected input.bin size %d" % len(raw)
    vals = struct.unpack("<%dh" % (W * H * 8), raw)

    rgb = bytearray(W * H * 3)
    for p in range(W * H):
        b = p * 8
        rgb[p * 3 + 0] = vals[b + 0] & 0xFF     # R
        rgb[p * 3 + 1] = vals[b + 1] & 0xFF     # G
        rgb[p * 3 + 2] = vals[b + 2] & 0xFF     # B

    hdr = b"P6\n# test.ppm -- im0002 fundus image (STARE), 512x512 RGB888\n%d %d\n255\n" % (W, H)
    with open(OUT, "wb") as f:
        f.write(hdr + bytes(rgb))

    lo = min(rgb)
    hi = max(rgb)
    nz = sum(1 for v in rgb if v)
    print("[ppm] wrote %s  (%d bytes: header %d + raster %d)"
          % (OUT, os.path.getsize(OUT), len(hdr), len(rgb)))
    print("      %dx%d RGB888  灰度范围 [%d..%d]  非零 %d/%d" % (W, H, lo, hi, nz, len(rgb)))


if __name__ == "__main__":
    main()
