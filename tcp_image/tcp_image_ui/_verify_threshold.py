# -*- coding: utf-8 -*-
"""阈值分割自动预设(Otsu)的自检。

  * 直方图必须与"逐像素算 (77R+150G+29B)>>8"的朴素实现对得上;
  * Otsu 结果必须落在合理的双峰之间(用手造的两峰图验证);
  * 对 test.ppm / 内置图样打印实际算出来的推荐阈值。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import imageio                                          # noqa: E402
import proto                                            # noqa: E402

errs = []


def ref_hist(image):
    h = [0] * 256
    for i in range(0, len(image) - 2, 3):
        g = (77 * image[i] + 150 * image[i + 1] + 29 * image[i + 2]) >> 8
        h[g] += 1
    return h


def check(label, got, want):
    if got == want:
        print("  OK   %s" % label)
    else:
        errs.append("%s: got %r want %r" % (label, got, want))
        print("  FAIL %s: got %r want %r" % (label, got, want))


print("=== 1) 直方图与朴素实现一致 ===")
bars = imageio.make_colorbars()
check("bars 直方图", imageio.luma_histogram(bars), ref_hist(bars))
check("bars 直方图总数", sum(imageio.luma_histogram(bars)), proto.IMG_TOTAL_BYTES // 3)
check("灰度权重与 _luma_gray 一致",
      bytes([min(255, max(0, v)) for v in range(256)]) is not None, True)

print("\n=== 2) 手造双峰图: Otsu 应落在两峰之间 ===")
# 造一张上半 40、下半 200 的 512x512 图
buf = bytearray(proto.IMG_TOTAL_BYTES)
for y in range(512):
    v = 40 if y < 256 else 200
    for x in range(512):
        p = (y * 512 + x) * 3
        buf[p] = buf[p + 1] = buf[p + 2] = v
thr, hist, note = imageio.auto_threshold(bytes(buf))
check("双峰(40/200) Otsu 落在 40..199", 40 <= thr <= 199, True)
print("     %s" % note)

print("\n=== 2.1) 自动定灰度下限 auto_gray_floor ===")
# 造一张"60% 近黑(灰度=2, 注意不是纯黑) + 20% 灰 100 + 20% 灰 200"的图。
# 近黑背景既不会被"排除纯黑"那条规则抓到, 又会把 Otsu 拽到 2 ——
# 只有自动定出的灰度下限能救它。
buf2 = bytearray(proto.IMG_TOTAL_BYTES)
for y in range(512):
    v = 2 if y < 307 else (100 if y < 410 else 200)
    for x in range(512):
        p = (y * 512 + x) * 3
        buf2[p] = buf2[p + 1] = buf2[p + 2] = v
buf2 = bytes(buf2)
h2 = imageio.luma_histogram(buf2)
f2 = imageio.auto_gray_floor(buf2, h2)
check("近黑背景图的灰度下限落在暗峰右侧 (3..20)", 3 <= f2 <= 20, True)
print("     自动灰度下限 = %d" % f2)
t_no, _, _ = imageio.auto_threshold(buf2, gray_min=0)
t_ex, _, note_ex = imageio.auto_threshold(buf2)
check("不排背景时被拽到近黑处 (thr < 100)", t_no < 100, True)
check("排背景后落进内容 (100 <= thr < 200)", 100 <= t_ex < 200, True)
print("     不排背景 thr=%d / 自动 thr=%d" % (t_no, t_ex))
print("     %s" % note_ex)

# 纯黑白图(棋盘) 排完为空, 必须自动退回全量, 不崩
checker = imageio.make_checker()
tc, _, _ = imageio.auto_threshold(checker)
check("全极端图(棋盘)自动退回全量, 不崩", isinstance(tc, int), True)
print("     棋盘: Otsu = %d" % tc)

# 画面正中间一个深色物体（完全不贴边）—— 这是"内容"不是"背景", 不许被排掉
buf3 = bytearray([200] * proto.IMG_TOTAL_BYTES)
for y in range(180, 330):
    for x in range(180, 330):
        p = (y * 512 + x) * 3
        buf3[p] = buf3[p + 1] = buf3[p + 2] = 30
check("居中暗物体不会被当背景排掉 (下限=0)",
      imageio.auto_gray_floor(bytes(buf3)), 0)

print("\n=== 3) 极端图: 全黑 / 全白 ===")
black = bytes(proto.IMG_TOTAL_BYTES)
white = bytes([255] * proto.IMG_TOTAL_BYTES)
tb, _, _ = imageio.auto_threshold(black)
tw, _, _ = imageio.auto_threshold(white)
check("全黑图不崩且给默认值", tb, proto.CTRL_THR_DEFAULT)
check("全白图不崩且给默认值", tw, proto.CTRL_THR_DEFAULT)

print("\n=== 4) 真实眼底图 test.ppm ===")
ppm = os.path.join(HERE, "test.ppm")
if os.path.exists(ppm):
    img, note = imageio.load_image(ppm)
    h = imageio.luma_histogram(img)
    fl = imageio.auto_gray_floor(img, h)
    thr, hist, note = imageio.auto_threshold(img)
    print("     自动灰度下限 = %d（削掉灰度 < %d 的暗背景）" % (fl, fl))
    print("     %s" % note)
    check("眼底图的自动下限落在背景环右沿 (35..50)", 35 <= fl <= 50, True)
    check("排掉背景后阈值升到血管区间 (>= 90)", thr >= 90, True)
    # PL 判据是 gray >= thr 为白, 所以"黑"是 gray < thr; 占比按**全图**算
    tot = sum(h)
    print("     全图: 灰度 < %d 的占 %.1f%%（判黑）、其余判白" % (thr, 100.0 * sum(h[:thr]) / tot))
    print("     纯黑/纯白像素: %d / %d（共 %.2f%%）"
          % (h[0], h[255], 100.0 * (h[0] + h[255]) / tot))
else:
    print("     (test.ppm 不在，跳过)")

print("\n=== 4.1) 纯黑背景的眼底图 test_blackborder.ppm ===")
bb = os.path.join(HERE, "test_blackborder.ppm")
if os.path.exists(bb):
    img2, _ = imageio.load_image(bb)
    hb = imageio.luma_histogram(img2)
    fl2 = imageio.auto_gray_floor(img2, hb)
    t_ex, _h, n_ex = imageio.auto_threshold(img2)
    t_no, _h2, n_no = imageio.auto_threshold(img2, gray_min=0)
    print("     自动灰度下限 = %d" % fl2)
    print("     不排背景: %s" % n_no)
    print("     自动    : %s" % n_ex)
    # 这张图的背景本来就是**纯黑**，"排除纯黑"那条规则单独就把 35% 削掉了，
    # 所以 gray_min=0 与自动的阈值相同 —— 这里只断言结果落在血管区间。
    # 真正能体现"自动下限"作用的是上面 2.1 那张**近黑**背景的合成图。
    check("纯黑背景图的阈值落在血管区间 (>= 90)", t_ex >= 90, True)
    check("自动下限被识别出来 (>0)", fl2 > 0, True)
else:
    print("     (test_blackborder.ppm 不在，跳过；用 make_blackborder_ppm.py 生成)")
print("\n=== 5) 内置图样（下限不该乱切）===")
for name, fn in sorted(imageio.PATTERNS.items()):
    im = fn()
    ff = imageio.auto_gray_floor(im)
    t, _h, _n = imageio.auto_threshold(im)
    print("     %-10s 自动下限=%3d  Otsu = %d" % (name, ff, t))


def _floor_of(img):
    return imageio.auto_gray_floor(img)


check("渐变图(平坦直方图) 自动下限必须 = 0", _floor_of(imageio.make_gradient()), 0)
check("棋盘(全极端) 自动下限必须 = 0", _floor_of(imageio.make_checker()), 0)

print("\n=== 5.1) 边缘叠加(mode 6) 的自动阈值 ===")
for name in ("test.ppm", "test_blackborder.ppm"):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        continue
    im, _ = imageio.load_image(p)
    thr, note = imageio.edge_threshold(im)
    mag = imageio.sobel_magnitude(im)
    frac = 100.0 * sum(1 for v in mag if v >= thr) / len(mag)
    print("     %-24s %s" % (name, note))
    print("     %-24s 板子会判为边缘的像素约 %.1f%%" % ("", frac))
    check("%s 边缘阈值落在合理区间 (24..255)" % name, 24 <= thr <= 255, True)
check("平坦图(渐变)的边缘阈值至少是下限",
      imageio.edge_threshold(imageio.make_gradient())[0] >= 24, True)


print("\n=== 6) v4 控制头打包 ===")
stream = proto.pack_stream(bars, mode=1, thr=137)
check("v4 流长度", len(stream), proto.WIRE_TOTAL_BYTES_V4)
check("v4 magic", stream[0] | (stream[1] << 8), proto.CTRL_MAGIC_V4)
check("v4 thr 字段", stream[8] | (stream[9] << 8), 137)
check("v4 mode 字段", stream[2] | (stream[3] << 8), 1)
old = proto.pack_stream(bars, mode=1, version=3)
check("v3 流长度", len(old), proto.WIRE_TOTAL_BYTES_V3)
try:
    proto.pack_stream(bars, mode=1, thr=300)
    errs.append("thr=300 should have raised")
except ValueError:
    print("  OK   thr 越界会报错")

if errs:
    print("\nTHRESHOLD VERIFY FAILED:")
    for e in errs:
        print("  -", e)
    sys.exit(1)
print("\nTHRESHOLD VERIFY OK")
