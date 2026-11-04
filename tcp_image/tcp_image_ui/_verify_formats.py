# -*- coding: utf-8 -*-
"""_verify_formats.py —— Netpbm 原生读写(P1..P7)与各格式往返自检。

覆盖: 二进制/ASCII 两种写法、PBM 1=黑 语义、16 位 maxval、头里注释、
CRLF 分隔、宽度非 8 倍数时的位打包、以及**"光栅首字节恰好是 0x0A"**这个经典陷阱。
不需要 Pillow, 也不需要开发板。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import imageio                                          # noqa: E402
import proto                                            # noqa: E402

TMP = os.path.join(HERE, "__fmttmp")
os.makedirs(TMP, exist_ok=True)
errs = []
made = []


def write(name, blob):
    p = os.path.join(TMP, name)
    with open(p, "wb") as f:
        f.write(blob)
    made.append(p)
    return p


def check(label, got, want):
    if got == want:
        print("  OK   %s" % label)
    else:
        errs.append("%s: got %r want %r" % (label, _brief(got), _brief(want)))
        print("  FAIL %s: got %r want %r" % (label, _brief(got), _brief(want)))


def _brief(v):
    """比对失败时不要把 786 KB 的 bytes 整个打出来(会刷爆日志)。"""
    if isinstance(v, (bytes, bytearray)) and len(v) > 24:
        return "%s(...%d bytes, head=%s)" % (type(v).__name__, len(v),
                                             bytes(v[:8]).hex())
    return v


def decode_native(path):
    """按原生尺寸解码（不缩放），返回 (rgb, note)。"""
    return imageio.load_netpbm(path, size=None)


print("=== 1) 512x512 各格式 save -> load 往返 ===")
img = imageio.make_colorbars()
for ext, exact in ((".ppm", True), (".pnm", True), (".pam", True),
                   (".pgm", False), (".pbm", False), (".rgb", True)):
    path = os.path.join(TMP, "rt" + ext)
    imageio.save_image(path, img)
    data, note = imageio.load_image(path)
    if exact:
        check("save/load %s 逐字节一致" % ext, data == img, True)
    else:
        check("save/load %s 长度正确" % ext, len(data), proto.IMG_TOTAL_BYTES)
    made.append(path)

# pgm: 解出来应当等于 PL 同一套权重的灰度
data, _ = imageio.load_image(os.path.join(TMP, "rt.pgm"))
gray = imageio._luma_gray(img)
want = bytearray()
for v in gray:
    want += bytes((v, v, v))
check("pgm 解码 == (77R+150G+29B)>>8 灰度", data, bytes(want))

# pbm: 解出来应当等于阈值二值
data, _ = imageio.load_image(os.path.join(TMP, "rt.pbm"))
want = bytes((0 if v < 128 else 255) for v in gray for _ in range(3))
check("pbm 解码 == 灰度<128 为黑", bytes(data), want)

print("\n=== 2) ASCII 变体 (P1/P2/P3) ===")
p = write("a.p3", b"P3\n# comment here\n2 1\n255\n10 20 30\n# mid-comment\n40 50 60\n")
rgb, note = decode_native(p)
check("P3 ASCII 像素", rgb, bytes((10, 20, 30, 40, 50, 60)))
check("P3 ASCII 尺寸(字节数)", len(rgb), 2 * 1 * 3)

p = write("a.p2", b"P2\r\n3 1\r\n15\r\n0 7 15\r\n")
rgb, _ = decode_native(p)
check("P2 ASCII (maxval=15, CRLF)", rgb,
      bytes((0, 0, 0, 119, 119, 119, 255, 255, 255)))

p = write("a.p1", b"P1\n# bitmap\n3 2\n0 1 0\n1 0 1\n")
rgb, _ = decode_native(p)
check("P1 ASCII (1=黑)", rgb,
      bytes((255, 255, 255, 0, 0, 0, 255, 255, 255,
             0, 0, 0, 255, 255, 255, 0, 0, 0)))

print("\n=== 3) 二进制边界 ===")
# P6 光栅首字节恰好是 0x0A —— 头解析时绝不能被多吞一个空白
p = write("t.p6", b"P6\n2 1\n255\n" + bytes((0x0A, 0x00, 0x00, 0x01, 0x02, 0x03)))
rgb, _ = decode_native(p)
check("P6 首字节=0x0A 不被吞", rgb, bytes((10, 0, 0, 1, 2, 3)))

# P5 16 位 (maxval 65535, 大端)
p = write("t.p5", b"P5\n3 1\n65535\n" + bytes((0xFF, 0xFF, 0x80, 0x00, 0x00, 0x00)))
rgb, _ = decode_native(p)
check("P5 16bit 缩放 65535->255 / 32768->128 / 0->0", rgb,
      bytes((255, 255, 255, 128, 128, 128, 0, 0, 0)))

# P4 宽度 5 (非 8 倍数): 行内位打包 10100 -> 黑 白 黑 白 白
p = write("t.p4", b"P4\n5 1\n" + bytes((0b10100000,)))
rgb, _ = decode_native(p)
check("P4 宽度=5 位打包", rgb,
      bytes((0, 0, 0, 255, 255, 255, 0, 0, 0, 255, 255, 255, 255, 255, 255)))

# P7 PAM, DEPTH=4, 丢掉 alpha
p = write("t.p7", b"P7\nWIDTH 2\nHEIGHT 1\nDEPTH 4\nMAXVAL 255\n"
                   b"TUPLTYPE RGB_ALPHA\nENDHDR\n"
                   b"\x01\x02\x03\xFF\x04\x05\x06\x80")
rgb, note = decode_native(p)
check("P7 PAM RGBA 丢 alpha", rgb, bytes((1, 2, 3, 4, 5, 6)))

print("\n=== 4) load_image 分发(按文件头, 不看扩展名) ===")
p = write("weird.xyz", b"P6\n2 1\n255\n" + bytes((9, 8, 7, 6, 5, 4)))
data, note = imageio.load_image(p)          # 会按契约拉伸到 512x512
check("扩展名不是 .ppm 也按 netpbm 解(长度)", len(data), proto.IMG_TOTAL_BYTES)
check("note 认出是 Netpbm", note.startswith("Netpbm"), True)
print("     note: %s" % note)

print("\n=== 5) 非 512x512 的 netpbm 会被拉伸到 512x512 ===")
data, note = imageio.load_image(os.path.join(TMP, "a.p1"))
check("3x2 -> 512x512 字节数", len(data), proto.IMG_TOTAL_BYTES)
print("     note: %s" % note)

print("\n=== 6) 存回来的 .pgm/.pbm 能被自己读回 (闭环) ===")
for ext in (".pgm", ".pbm", ".pam", ".pnm"):
    path = os.path.join(TMP, "loop" + ext)
    imageio.save_image(path, imageio.make_checker())
    back, _ = imageio.load_image(path)
    check("闭环 %s 长度" % ext, len(back), proto.IMG_TOTAL_BYTES)
    made.append(path)

for p in made:
    try:
        os.remove(p)
    except OSError:
        pass
try:
    os.rmdir(TMP)
except OSError:
    pass

if errs:
    print("\nFORMAT VERIFY FAILED:")
    for e in errs:
        print("  -", e)
    sys.exit(1)
print("\nFORMAT VERIFY OK")
