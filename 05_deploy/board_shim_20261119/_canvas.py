# -*- coding: utf-8 -*-
"""_canvas.py —— tk.Canvas 的 GTK3 实现（**Pillow 合成**，不用 cairo）。

为什么不用 Gtk.DrawingArea + cairo
------------------------------------------------
板上 GTK3 虽然能画，但 `Gtk.DrawingArea` 的 "draw" 回调要 `cairo.Context`，
而 PyGObject 需要 `python3-gi-cairo`（`gi._gi_cairo`）才能做这个类型转换 ——
**板上没装、也没外网装**。实测现象是每次重绘都报
`Couldn't find foreign struct converter for 'cairo.Context'`，画布永远空白。

所以改成：自己维护图元列表 → 用 **Pillow 的 ImageDraw 合成成一张 RGB 位图**
→ 塞进 Gtk.Image。板上 Pillow 5.1.0 现成，`ImageDraw.line/rectangle/ellipse/
polygon/text` 都支持，中文用文泉驿正黑，够用且不依赖任何新包。

代价：没有抗锯齿（PIL 5.1 的 ImageDraw 就是硬边）、每次改动要整张重合成。
对 512×512 的画布这是 ~1–3 ms，界面上完全够。
"""

from __future__ import print_function

import math
import os

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from . import _gtkbase as B  # noqa: E402
from ._widgets import BaseWidget, TclError  # noqa: E402

_ANCHORS = {
    "nw": (0.0, 0.0), "n": (0.5, 0.0), "ne": (1.0, 0.0),
    "w": (0.0, 0.5), "center": (0.5, 0.5), "e": (1.0, 0.5),
    "sw": (0.0, 1.0), "s": (0.5, 1.0), "se": (1.0, 1.0),
}

# 中文可用的字体（板上实测存在）
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_FONT_MONO_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_FONT_CACHE = {}


def _pick(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _font(spec):
    """tk 字体 → PIL ImageFont。按 (文件, 像素大小) 缓存。"""
    family, size, bold = B.font_spec(spec) if spec else ("", 10, False)
    px = max(6, int(round(float(size) * 96.0 / 72.0)))     # 磅 → 像素（96 dpi）
    mono = "mono" in str(family).lower() or "consol" in str(family).lower()
    path = _pick(_FONT_MONO_CANDIDATES if mono else _FONT_CANDIDATES)
    key = (path, px)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    try:
        f = ImageFont.truetype(path, px) if path else ImageFont.load_default()
    except Exception:
        f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _rgb(hexcolor, default=(0, 0, 0)):
    try:
        return (int(hexcolor[1:3], 16), int(hexcolor[3:5], 16), int(hexcolor[5:7], 16))
    except Exception:
        return default


# ---------------------------------------------------------------- 缩放后的贴图（2026-11-19）
# 画布按 UI_SCALE 缩小显示时，整幅图要缩到目标尺寸。缩放结果挂在 PhotoImage 自己身上，
# 照片本身是复用的（_vcache），所以同一帧只会缩一次；分桶缓存两个尺寸就够（当前帧 + 预览）。
def _disp_pil(src, pil, w2, h2):
    if pil is None:
        return None
    if pil.size == (w2, h2):
        return pil
    cache = getattr(src, "_pil_disp", None)
    key = (id(pil), w2, h2)
    if cache is not None and cache[0] == key:
        return cache[1]
    try:
        out = pil.resize((w2, h2), Image.BILINEAR)
    except Exception:
        return pil
    try:
        src._pil_disp = (key, out)
    except Exception:
        pass
    return out


def _disp_pb(src, pb, w2, h2):
    """GdkPixbuf 版的同上（快路 1 直接把 pixbuf 交给 Gtk.Image）。"""
    if pb is None:
        return None
    if pb.get_width() == w2 and pb.get_height() == h2:
        return pb
    cache = getattr(src, "_pb_disp", None)
    key = (id(pb), w2, h2)
    if cache is not None and cache[0] == key:
        return cache[1]
    pil = getattr(src, "_pil", None)
    if pil is None:
        pil = _pixbuf_to_pil(pb)
    if pil is None:
        return None
    im = _disp_pil(src, pil, w2, h2)
    if im is None:
        return None
    if im.mode != "RGB":
        im = im.convert("RGB")
    try:
        out = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(im.tobytes()), GdkPixbuf.Colorspace.RGB, False, 8,
            w2, h2, w2 * 3).copy()
    except Exception:
        B._log_exc("Canvas scale pixbuf")
        return None
    try:
        src._pb_disp = (key, out)
    except Exception:
        pass
    return out


class _Item(object):
    __slots__ = ("id", "kind", "coords", "opt", "tags")

    def __init__(self, iid, kind, coords, opt, tags):
        self.id = iid
        self.kind = kind
        self.coords = list(coords)
        self.opt = dict(opt)
        self.tags = list(tags)


class Canvas(BaseWidget):
    """tk.Canvas 的替身（Pillow 合成后端）。"""

    def _build(self):
        # 为什么是 EventBox + Image，而不是光一个 Gtk.Image：
        # Gtk.Image **没有自己的 GdkWindow**，鼠标事件只会送到拥有窗口的祖先（顶层窗口），
        # 不会派发到 Image 上 —— 于是 `bind("<ButtonPress-1>")` 在真鼠标下永远不触发
        # （键盘/程序内调用看不出来，只有真点击才暴露；ROI 框选就废了）。
        # Gtk.EventBox 天生带自己的窗口，正是为"给无窗口控件接事件"设计的。
        self._img = Gtk.Image()
        self._area = Gtk.EventBox()
        self._area.add(self._img)
        self._gtk = self._area
        self._items = []
        self._seq = 0
        self._scrollregion = (0, 0, 0, 0)
        self._ox = 0.0
        self._oy = 0.0
        self._scan = None
        self._xcmd = None
        self._ycmd = None
        self._req_w = 512
        self._req_h = 512
        self._scale = 0.0           # 渲染时算出来的实际比例（0 = 还没渲染过，用 B.UI_SCALE）
        self._dirty = True
        self._pixbuf = None
        self._base_im = None        # 不含 tags="cursor" 的底图（拖时间轴时复用，见 _render）
        self._base_sig = None
        self._vadj_ = Gtk.Adjustment(value=0, lower=0, upper=0, step_incr=1, page_incr=1)
        self._hadj_ = Gtk.Adjustment(value=0, lower=0, upper=0, step_incr=1, page_incr=1)
        self._vadj_.connect("value-changed", self._on_scroll)
        self._hadj_.connect("value-changed", self._on_scroll)
        self._area.connect("size-allocate", self._on_alloc)
        self._area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                              Gdk.EventMask.BUTTON_RELEASE_MASK |
                              Gdk.EventMask.POINTER_MOTION_MASK |
                              Gdk.EventMask.SCROLL_MASK)

    # -------------------------------------------------- 尺寸
    def _apply_size(self):
        # width / height 是**两个独立选项**，必须一起下发；分开设会把对方清掉
        # → 画布宽度变成 0（踩过：整窗布局正常但画布 1px）。
        # 2026-11-19：_req_* 是**设计尺寸**（图元坐标就是按它写的），真实控件尺寸要乘缩放系数，
        # 否则 1280×720 的屏幕上界面被切成两半。
        self._gtk.set_size_request(B.sx(self._req_w), B.sx(self._req_h))
        self._dirty = True
        self._flush()

    def _k(self):
        """当前缩放比例（设计坐标 → 屏幕像素）。"""
        return self._scale if self._scale > 0 else B.UI_SCALE

    def _on_opt(self, name, value):
        if name == "width":
            self._req_w = int(value or 1)
            self._apply_size()
        elif name == "height":
            self._req_h = int(value or 1)
            self._apply_size()
        elif name == "scrollregion":
            try:
                self._scrollregion = tuple(float(v) for v in value)
            except Exception:
                pass
            self._sync_adjustments()
            self._dirty = True
        elif name == "xscrollcommand":
            self._xcmd = value
        elif name == "yscrollcommand":
            self._ycmd = value
        elif name in ("bg", "background"):
            self._dirty = True
            self._flush()
        elif name in ("highlightthickness", "bd", "relief", "cursor"):
            pass

    def _bg_color(self):
        return B.color(self._cfg.get("background", self._cfg.get("bg")), "#ffffff")

    # -------------------------------------------------- 滚动
    def _sync_adjustments(self):
        w = max(1, self._req_w)
        h = max(1, self._req_h)
        x0, y0, x1, y1 = self._scrollregion
        self._hadj_.set_upper(max(x1 - x0, w))
        self._hadj_.set_page_size(w)
        self._vadj_.set_upper(max(y1 - y0, h))
        self._vadj_.set_page_size(h)
        self._hadj_.set_step_increment(max(1.0, w / 10.0))
        self._vadj_.set_step_increment(max(1.0, h / 10.0))

    def _on_alloc(self, area, alloc):
        self._sync_adjustments()
        return False

    def _on_scroll(self, *_a):
        self._ox = self._hadj_.get_value()
        self._oy = self._vadj_.get_value()
        self._dirty = True
        self._flush()
        if self._xcmd:
            try:
                self._xcmd(*[str(v) for v in self._frac(self._hadj_)])
            except Exception:
                B._log_exc("Canvas xscrollcommand")
        if self._ycmd:
            try:
                self._ycmd(*[str(v) for v in self._frac(self._vadj_)])
            except Exception:
                B._log_exc("Canvas yscrollcommand")

    def _vadj(self):
        return self._vadj_

    def _hadj(self):
        return self._hadj_

    def _view(self, adj, args):
        if not args:
            return self._frac(adj)
        if args[0] == "moveto":
            adj.set_value(adj.get_lower() + float(args[1]) * max(1e-6, adj.get_upper() - adj.get_lower()))
        elif args[0] == "scroll":
            n = int(args[1])
            unit = args[2] if len(args) > 2 else "units"
            step = adj.get_page_size() / 10.0 if unit == "pages" else adj.get_step_increment()
            adj.set_value(adj.get_value() + n * step)
        return None

    @staticmethod
    def _frac(adj):
        span = max(1e-6, adj.get_upper() - adj.get_lower())
        lo = (adj.get_value() - adj.get_lower()) / span
        return (lo, min(1.0, lo + adj.get_page_size() / span))

    def xview(self, *args):
        return self._view(self._hadj_, args)

    def yview(self, *args):
        return self._view(self._vadj_, args)

    def xview_scroll(self, number, what):
        adj = self._hadj_
        step = adj.get_page_size() / 10.0 if what == "pages" else adj.get_step_increment()
        adj.set_value(adj.get_value() + int(number) * step)

    def yview_scroll(self, number, what):
        adj = self._vadj_
        step = adj.get_page_size() / 10.0 if what == "pages" else adj.get_step_increment()
        adj.set_value(adj.get_value() + int(number) * step)

    def scan_mark(self, x, y):
        k = self._k()
        self._scan = (float(x) / k + self._ox, float(y) / k + self._oy)

    def scan_dragto(self, x, y, gain=10):
        if self._scan is None:
            return
        k = self._k()
        self._hadj_.set_value(self._scan[0] - float(x) / k)
        self._vadj_.set_value(self._scan[1] - float(y) / k)

    # -------------------------------------------------- 图元创建
    def _new(self, kind, coords, kw):
        self._seq += 1
        tags = kw.pop("tags", None)
        if tags is None:
            taglist = []
        elif isinstance(tags, str):
            taglist = tags.split()
        else:
            taglist = list(tags)
        it = _Item(self._seq, kind, coords, kw, taglist)
        self._items.append(it)
        self._dirty = True
        self._flush()
        return it.id

    def create_image(self, x, y, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        return self._new("image", (x, y), kw)

    def create_line(self, *args, **kw):
        return self._new("line", args, kw)

    def create_rectangle(self, *args, **kw):
        return self._new("rect", args, kw)

    def create_oval(self, *args, **kw):
        return self._new("oval", args, kw)

    def create_polygon(self, *args, **kw):
        return self._new("poly", args, kw)

    def create_arc(self, *args, **kw):
        return self._new("arc", args, kw)

    def create_text(self, x, y, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        return self._new("text", (x, y), kw)

    def create_window(self, x, y, cnf=None, **kw):
        B.warn("Canvas.create_window 未实现（本工程未使用）")
        return self._new("image", (x, y), kw)

    # -------------------------------------------------- 图元操作
    def _find(self, sel):
        if sel == "all":
            return list(self._items)
        if isinstance(sel, _Item):
            return [sel]
        if isinstance(sel, int):
            return [it for it in self._items if it.id == sel]
        s = str(sel)
        if s.isdigit():
            return [it for it in self._items if it.id == int(s)]
        return [it for it in self._items if s in it.tags]

    def itemconfigure(self, sel, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        for it in self._find(sel):
            it.opt.update(kw)
        self._dirty = True
        self._flush()

    itemconfig = itemconfigure

    def itemcget(self, sel, name):
        got = self._find(sel)
        return got[0].opt.get(name) if got else None

    def coords(self, sel, *args):
        got = self._find(sel)
        if not got:
            return []
        if not args:
            return list(got[0].coords)
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            args = tuple(args[0])
        got[0].coords = [float(v) for v in args]
        self._dirty = True
        self._flush()
        return None

    def move(self, sel, dx, dy):
        for it in self._find(sel):
            it.coords = [c + (dx if i % 2 == 0 else dy) for i, c in enumerate(it.coords)]
        self._dirty = True
        self._flush()

    def delete(self, *sels):
        if not sels:
            return
        kill = set()
        for sel in sels:
            for it in self._find(sel):
                kill.add(it.id)
        self._items = [it for it in self._items if it.id not in kill]
        self._dirty = True
        self._flush()

    def find_all(self):
        return tuple(it.id for it in self._items)

    def find_withtag(self, tag):
        return tuple(it.id for it in self._find(tag))

    def gettags(self, sel):
        got = self._find(sel)
        return tuple(got[0].tags) if got else ()

    def canvasx(self, screenx, gridspacing=None):
        return float(screenx) / self._k() + self._ox

    def canvasy(self, screeny, gridspacing=None):
        return float(screeny) / self._k() + self._oy

    def postscript(self, **kw):
        raise TclError("Canvas.postscript 未实现（板上没有 PS 输出需求）")

    # -------------------------------------------------- 事件（坐标要扣掉滚动）
    def _bind_target(self):
        return self._gtk

    def _dispatch(self, rec, event, rest):
        if rec["sig"] in ("button-press-event", "button-release-event",
                          "motion-notify-event"):
            # 2026-11-19：鼠标坐标是**屏幕像素**，要除以缩放系数换回设计坐标（图元坐标空间）
            k = self._k()
            ev = B.Event(self, x=int(getattr(event, "x", 0) / k + self._ox),
                         y=int(getattr(event, "y", 0) / k + self._oy),
                         x_root=int(getattr(event, "x_root", 0)),
                         y_root=int(getattr(event, "y_root", 0)),
                         num=int(getattr(event, "button", 0)),
                         state=int(getattr(event, "state", 0)),
                         type=rec["sig"])
            return self._call_user(rec["func"], ev)
        return BaseWidget._dispatch(self, rec, event, rest)

    # -------------------------------------------------- 渲染
    def _flush(self):
        """把改动落到屏幕上。真正的合成在 _render() 里。

        10-31 改成延迟合成：每次改动只标脏，空闲时合成一次（GLib.idle_add）。原来每个 create_line 都整张重画，
        一次画 n 个图元就是 n² 次绘制（心超工作站现场模式的事件时间线每帧几百条刻度，界面越跑越慢）。"""
        if not self._dirty or getattr(self, "_flush_pending", False):
            return
        self._flush_pending = True

        def run():
            self._flush_pending = False
            if self._dirty:
                try:
                    self._render()
                except Exception:
                    B._log_exc("Canvas render")
            return False
        GLib.idle_add(run)

    @staticmethod
    def _item_sig(it):
        """图元的"画出来会长什么样"的指纹。用来判断底图能不能复用。"""
        o = it.opt
        return (it.kind, tuple(it.coords),
                o.get("fill"), o.get("outline"), o.get("width"), o.get("dash"),
                o.get("text"), o.get("font"), o.get("anchor"),
                o.get("start"), o.get("extent"), o.get("style"),
                id(o.get("image")) if it.kind == "image" else None)

    def _render(self):
        w = max(1, self._req_w)                  # 设计尺寸：图元坐标就是按它写的
        h = max(1, self._req_h)
        dw = max(1, int(round(w * B.UI_SCALE)))  # 屏幕尺寸：真正合成的位图大小
        dh = max(1, int(round(h * B.UI_SCALE)))
        self._scale = dw / float(w)
        k = self._scale
        bg = _rgb(self._bg_color(), (255, 255, 255))
        ox, oy = self._ox, self._oy
        vis = [it for it in self._items
               if str(it.opt.get("state", "normal")) != "hidden"]

        # ---- 快路 1：整张画布就一张铺满的图（心超的视频帧 / 各种回图都是这样）
        # 不用再"新建一张 RGB → 把图画上去 → tobytes → 建 pixbuf → 再拷一份"，
        # 直接把图自己的 pixbuf 交给 Gtk.Image。448x448 从 15.0 ms 降到 ~0.3 ms。
        if len(vis) == 1 and vis[0].kind == "image":
            it = vis[0]
            src = it.opt.get("image")
            pb = getattr(src, "_pixbuf", None)
            if (pb is not None
                    and _ANCHORS.get(str(it.opt.get("anchor", "center")), (0.5, 0.5)) == (0.0, 0.0)
                    and abs(it.coords[0] - ox) < 0.5 and abs(it.coords[1] - oy) < 0.5
                    and pb.get_width() == w and pb.get_height() == h):
                # 缩放版按图对象缓存（同一帧复用同一张 PhotoImage，所以只缩一次）
                shown = pb if (dw, dh) == (w, h) else _disp_pb(src, pb, dw, dh)
                if shown is not None:
                    self._img.set_from_pixbuf(shown)
                    self._pixbuf = shown
                    self._base_sig = None          # 下次回到慢路时底图必须重算
                    self._dirty = False
                    return

        # ---- 快路 2：底图（不含 tags="cursor" 的图元）没变 → 复用上一张，只重画光标
        # 时间线画布有一百多个刻度图元，每动一下光标就把它们全用 Python 重画一遍要 14 ms；
        # 底图没变时只 copy 一份（0.2 ms）再画光标。
        # 注意：`delete("cursor")` 那一瞬间画布里**没有**光标图元，也必须走缓存 ——
        # 早先只在"有光标图元"时才算指纹，结果每次删光标都把底图缓存清掉，白干两遍全量重画。
        cursor_items = [it for it in vis if "cursor" in it.tags]
        base_items = [it for it in vis if "cursor" not in it.tags]
        sig = (w, h, bg, ox, oy, tuple(self._item_sig(it) for it in base_items))
        if sig == self._base_sig and self._base_im is not None:
            base = self._base_im
        else:
            base = Image.new("RGB", (dw, dh), bg)
            d = ImageDraw.Draw(base)
            for it in base_items:
                try:
                    self._draw(d, base, it, ox, oy)
                except Exception:
                    B._log_exc("Canvas draw %s" % it.kind)
            self._base_im = base.copy() if cursor_items else base
            self._base_sig = sig
        if cursor_items:
            im = base.copy()
            d = ImageDraw.Draw(im)
            for it in cursor_items:
                try:
                    self._draw(d, im, it, ox, oy)
                except Exception:
                    B._log_exc("Canvas draw %s" % it.kind)
        else:
            im = base
        self._pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(im.tobytes()), GdkPixbuf.Colorspace.RGB, False, 8,
            dw, dh, dw * 3).copy()  # .copy()：板上老 PyGObject 里 new_from_bytes 的像素图交给 Gtk.Image 显示后整帧泄漏（10-31 实测），拷一份就不漏
        self._img.set_from_pixbuf(self._pixbuf)
        self._dirty = False

    # --- 各类图元
    def _draw(self, d, im, it, ox, oy):
        getattr(self, "_draw_" + it.kind)(d, im, it, ox, oy)

    def _draw_image(self, d, im, it, ox, oy):
        src = it.opt.get("image")
        if src is None or src == "":
            return                                  # itemconfigure(image="") = 清掉图
        pil = getattr(src, "_pil", None)
        if pil is None:
            pil = _pixbuf_to_pil(getattr(src, "_pixbuf", None))
        if pil is None:
            return
        if pil.mode != "RGB":
            pil = pil.convert("RGB")
        k = self._scale
        if k != 1.0:                                # 缩放显示：贴图也要缩，否则会盖住别的图元
            w2 = max(1, int(round(pil.size[0] * k)))
            h2 = max(1, int(round(pil.size[1] * k)))
            pil = _disp_pil(src, pil, w2, h2)
        x = (it.coords[0] - ox) * k
        y = (it.coords[1] - oy) * k
        ax, ay = _ANCHORS.get(str(it.opt.get("anchor", "center")), (0.5, 0.5))
        x -= pil.size[0] * ax
        y -= pil.size[1] * ay
        im.paste(pil, (int(round(x)), int(round(y))))

    def _draw_line(self, d, im, it, ox, oy):
        c = it.coords
        if len(c) < 4:
            return
        k = self._scale
        pts = [((c[i] - ox) * k, (c[i + 1] - oy) * k) for i in range(0, len(c) - 1, 2)]
        col = _rgb(B.color(it.opt.get("fill"), "#000000"))
        wd = max(1, int(round(float(it.opt.get("width", 1) or 1) * k)))
        dash = it.opt.get("dash")
        if dash:
            try:
                dash = [max(1.0, float(v) * k) for v in dash]
            except Exception:
                pass
            for i in range(len(pts) - 1):
                _dashed_line(d, pts[i], pts[i + 1], col, wd, dash)
        else:
            d.line(pts, fill=col, width=wd)

    def _draw_text(self, d, im, it, ox, oy):
        text = str(it.opt.get("text", ""))
        if not text:
            return
        f = _font(it.opt.get("font"))                # 字号已由 font_spec 按 UI_SCALE 缩过
        k = self._scale
        x = (it.coords[0] - ox) * k
        y = (it.coords[1] - oy) * k
        try:
            tw, th = d.textsize(text, font=f)
        except Exception:
            tw, th = (8 * len(text), 12)
        ax, ay = _ANCHORS.get(str(it.opt.get("anchor", "center")), (0.5, 0.5))
        d.text((x - tw * ax, y - th * ay), text,
               fill=_rgb(B.color(it.opt.get("fill"), "#000000")), font=f)

    def _box(self, it, ox, oy):
        c = it.coords
        k = self._scale
        x0, y0, x1, y1 = (c[0] - ox) * k, (c[1] - oy) * k, (c[2] - ox) * k, (c[3] - oy) * k
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _draw_rect(self, d, im, it, ox, oy):
        if len(it.coords) < 4:
            return
        box = self._box(it, ox, oy)
        fill = B.color(it.opt.get("fill"), None)
        outline = B.color(it.opt.get("outline"), None)
        wd = max(1, int(round(float(it.opt.get("width", 1) or 1) * self._scale)))
        if fill:
            d.rectangle(box, fill=_rgb(fill))
        if outline:
            dash = it.opt.get("dash")
            if dash:
                try:
                    dash = [max(1.0, float(v) * self._scale) for v in dash]
                except Exception:
                    pass
                x0, y0, x1, y1 = box
                for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                             ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
                    _dashed_line(d, a, b, _rgb(outline), wd, dash)
            else:
                _rect_outline(d, box, _rgb(outline), wd)

    def _draw_oval(self, d, im, it, ox, oy):
        if len(it.coords) < 4:
            return
        box = self._box(it, ox, oy)
        fill = B.color(it.opt.get("fill"), None)
        outline = B.color(it.opt.get("outline"), None)
        wd = max(1, int(round(float(it.opt.get("width", 1) or 1) * self._scale)))
        if fill:
            d.ellipse(box, fill=_rgb(fill))
        if outline:
            _ellipse_outline(d, box, _rgb(outline), wd)

    def _draw_poly(self, d, im, it, ox, oy):
        c = it.coords
        if len(c) < 4:
            return
        k = self._scale
        pts = [((c[i] - ox) * k, (c[i + 1] - oy) * k) for i in range(0, len(c) - 1, 2)]
        fill = B.color(it.opt.get("fill"), None)
        outline = B.color(it.opt.get("outline"), None)
        wd = max(1, int(round(float(it.opt.get("width", 1) or 1) * k)))
        if fill:
            d.polygon(pts, fill=_rgb(fill))
        if outline:
            d.line(pts + [pts[0]], fill=_rgb(outline), width=wd)

    def _draw_arc(self, d, im, it, ox, oy):
        if len(it.coords) < 4:
            return
        box = self._box(it, ox, oy)
        start = float(it.opt.get("start", 0) or 0)
        extent = float(it.opt.get("extent", 90) or 90)
        col = _rgb(B.color(it.opt.get("outline") or it.opt.get("fill"), "#000000"))
        wd = max(1, int(round(float(it.opt.get("width", 1) or 1) * self._scale)))
        # tk 的角度是逆时针、0 度在 +x；PIL 的 arc 是顺时针、0 度在 3 点方向
        for i in range(wd):
            b = (box[0] - i, box[1] - i, box[2] + i, box[3] + i)
            d.arc(b, start=-start - extent, end=-start, fill=col)


def _rect_outline(d, box, color, wd):
    """PIL 5.1 的rectangle 不认 width 参数（5.3 才有），用嵌套矩形画粗边框。"""
    for i in range(max(1, wd)):
        d.rectangle((box[0] - i, box[1] - i, box[2] + i, box[3] + i), outline=color)


def _ellipse_outline(d, box, color, wd):
    for i in range(max(1, wd)):
        d.ellipse((box[0] - i, box[1] - i, box[2] + i, box[3] + i), outline=color)


def _dashed_line(d, p0, p1, color, width, dash):
    """PIL 的 ImageDraw 没有虚线，按 dash 模式自己切段。"""
    try:
        on, off = float(dash[0]), float(dash[1]) if len(dash) > 1 else float(dash[0])
    except Exception:
        on, off = 4.0, 4.0
    x0, y0 = p0
    x1, y1 = p1
    total = math.hypot(x1 - x0, y1 - y0)
    if total <= 0:
        return
    ux, uy = (x1 - x0) / total, (y1 - y0) / total
    pos = 0.0
    while pos < total:
        a = pos
        b = min(total, pos + on)
        d.line([(x0 + ux * a, y0 + uy * a), (x0 + ux * b, y0 + uy * b)],
               fill=color, width=width)
        pos = b + off


def _pixbuf_to_pil(pb):
    if pb is None:
        return None
    w, h = pb.get_width(), pb.get_height()
    nch = pb.get_n_channels()
    data = bytes(bytearray(pb.get_pixels()))
    stride = pb.get_rowstride()
    if stride != w * nch:
        rows = [data[y * stride:y * stride + w * nch] for y in range(h)]
        data = b"".join(rows)
    else:
        data = data[:w * h * nch]
    mode = "RGBA" if pb.get_has_alpha() else "RGB"
    return Image.frombytes(mode, (w, h), data)
