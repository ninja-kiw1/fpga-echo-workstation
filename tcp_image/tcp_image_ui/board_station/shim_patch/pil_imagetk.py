# -*- coding: utf-8 -*-
"""pil_imagetk.py —— 顶替 `PIL.ImageTk`。

板上的 Pillow 5.1.0 自带 `PIL/ImageTk.py`，但它 `import tkinter`（板上没有），
而且它要的是真 Tk 的 PhotoImage。这里给它一个同名模块：把 PIL.Image 转成
GdkPixbuf，接口保持 `PhotoImage(image)` + `.width()/.height()`，
供 `Canvas.create_image(image=...)` 与 `Label(image=...)` 使用。

`bootstrap.py` 会把本模块注册成 `sys.modules['PIL.ImageTk']` 并挂到 `PIL.ImageTk` 属性上，
所以被测代码里 `from PIL import Image, ImageTk` 不需要任何改动。
"""

from __future__ import print_function

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402


def _to_pixbuf(im):
    """PIL.Image → GdkPixbuf（统一成 RGB / RGBA 8bit）。"""
    mode = im.mode
    if mode == "P":
        im = im.convert("RGBA" if "transparency" in im.info else "RGB")
        mode = im.mode
    elif mode == "1":
        im = im.convert("L")
        mode = im.mode
    elif mode == "CMYK":
        im = im.convert("RGB")
        mode = im.mode

    has_alpha = mode in ("RGBA", "LA")
    if mode in ("L", "I", "F", "I;16"):
        im = im.convert("L")
        gray = im.tobytes()
        w, h = im.size
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
        # 灰度 → RGB（Pixbuf 没有单通道模式）
        px = bytearray(w * h * 3)
        for i, v in enumerate(bytearray(gray)):
            px[i * 3] = v
            px[i * 3 + 1] = v
            px[i * 3 + 2] = v
        return GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(bytes(px)), GdkPixbuf.Colorspace.RGB, False, 8, w, h, w * 3).copy()  # .copy()：板上老 PyGObject 里 new_from_bytes 的像素图交给 Gtk.Image 显示后整帧泄漏（10-31 实测），拷一份就不漏

    if mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if has_alpha else "RGB")
        has_alpha = im.mode == "RGBA"

    w, h = im.size
    nch = 4 if has_alpha else 3
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(im.tobytes()), GdkPixbuf.Colorspace.RGB, has_alpha, 8,
        w, h, w * nch).copy()  # .copy()：板上老 PyGObject 里 new_from_bytes 的像素图交给 Gtk.Image 显示后整帧泄漏（10-31 实测），拷一份就不漏


class PhotoImage(object):
    """`ImageTk.PhotoImage` 的替身。"""

    def __init__(self, image=None, size=None, **kw):
        if image is None:
            raise ValueError("PhotoImage 需要一张 PIL.Image")
        im = image
        if size is not None:
            im = im.resize(size)
        self._pil = im
        self._pixbuf = _to_pixbuf(im)

    # --- tk PhotoImage 的常用查询
    def width(self):
        return self._pixbuf.get_width()

    def height(self):
        return self._pixbuf.get_height()

    def cget(self, key):
        return getattr(self, key, None)

    def __getitem__(self, key):
        return self.cget(key)

    def __repr__(self):
        return "<ImageTk.PhotoImage %dx%d>" % (self.width(), self.height())


def getimage(photo):
    """GdkPixbuf → PIL.Image（少数代码会反过来用）。"""
    from PIL import Image
    pb = getattr(photo, "_pixbuf", None)
    if pb is None:
        raise ValueError("not a PhotoImage")
    w, h = pb.get_width(), pb.get_height()
    nch = pb.get_n_channels()
    data = bytes(bytearray(pb.get_pixels()))[:w * h * nch]
    mode = "RGBA" if pb.get_has_alpha() else "RGB"
    return Image.frombytes(mode, (w, h), data)
