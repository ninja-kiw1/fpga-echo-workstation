# -*- coding: utf-8 -*-
"""临时验证脚本: 检查 PC 端 proto 自检 + save_image 往返 + 各文件语法。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

errs = []

# 1) proto 自检
import proto
if proto.SELF_CHECK_ERRORS:
    errs.append("proto SELF_CHECK_ERRORS=%r" % proto.SELF_CHECK_ERRORS)
else:
    print("proto self-check OK; WIRE_TOTAL_BYTES=%d" % proto.WIRE_TOTAL_BYTES)

# 2) 各文件能 import / 编译
import py_compile
for f in ("proto.py", "imageio.py", "imagelink.py", "app.py"):
    try:
        py_compile.compile(os.path.join(HERE, f), doraise=True)
        print("py_compile OK: %s" % f)
    except Exception as e:
        errs.append("py_compile %s: %s" % (f, e))

# 3) imageio.save_image 往返(rgb / ppm), 顺带 png(需 Pillow)
import imageio
img = imageio.make_colorbars()
assert len(img) == proto.IMG_TOTAL_BYTES

tmp_rgb = os.path.join(HERE, "__v_rgb.rgb")
tmp_ppm = os.path.join(HERE, "__v_ppm.ppm")
tmp_png = os.path.join(HERE, "__v_png.png")

imageio.save_image(tmp_rgb, img)
with open(tmp_rgb, "rb") as f:
    if f.read() != img:
        errs.append("rgb save round-trip mismatch")
os.remove(tmp_rgb)
print("save_image .rgb round-trip OK")

imageio.save_image(tmp_ppm, img)
with open(tmp_ppm, "rb") as f:
    if f.read() != imageio.to_ppm_bytes(img):
        errs.append("ppm save round-trip mismatch")
os.remove(tmp_ppm)
print("save_image .ppm round-trip OK")

try:
    from PIL import Image
    imageio.save_image(tmp_png, img)
    im = Image.open(tmp_png)
    try:
        if im.size != (proto.IMG_WIDTH, proto.IMG_HEIGHT):
            errs.append("png size mismatch: %r" % (im.size,))
    finally:
        im.close()
    os.remove(tmp_png)
    print("save_image .png OK (Pillow present)")
except ImportError:
    print("save_image .png SKIPPED (no Pillow)")

if errs:
    print("PC VERIFY FAILED:")
    for e in errs:
        print("  -", e)
    sys.exit(1)
print("PC VERIFY OK")
