# -*- coding: utf-8 -*-
"""_gtkbase.py —— tkinter 兼容层的底层：GTK 初始化、颜色/字体/CSS、事件、变量、定时器、几何管理。

设计要点（为什么要这么写）
------------------------------------------------
板上没有 tkinter，也没有外网装 tcl/tk 开发包（详见 docs/环境探测.md），
但 **GTK3 + PyGObject 是现成的**。所以这里用纯 Python 复刻 tkinter 的**公开 API**，
后端落到 GTK3，让 `02_上位机` 里那 10 个界面文件**一字不改**在板上跑起来。

真正难的只有三件事，都放在这个文件里：
1. **几何管理**：tk 的 pack / grid 语义与 GTK 的 Box/Grid 不是一一对应。
   - pack：`side` 决定轴（top/bottom → 竖，left/right → 横），`fill` → Gtk 的
     halign/valign=Fill + hexpand/vexpand，`expand` → 沿轴 expand，`padx/pady` → margin。
     同一个父容器里**混用两个轴**时自动套一层 Box（tk 也允许，代码里极少见，
     真出现会打一条 TKWARN 到日志，方便排查）。
   - grid：`sticky` → align/expand，`rowconfigure(weight=1)` → 该行列的子控件 expand。
2. **事件**：tk 的 `<Button-1>` / `<B1-Motion>` / `<MouseWheel>` / `<Configure>` / `<Control-o>`
   这些序列要映射到 GTK 的 signal，并包装出一个带 `.x/.y/.width/.height/.delta/.keysym`
   的事件对象（界面上真的用到了这几个字段）。
3. **视觉**：tk 的 `bg/fg/font` 逐控件生效（`uistyle.py` 全靠这个），
   用「每个唯一组合一个 CSS class」实现；Windows 字体名映射到板上有的字体。
"""

from __future__ import print_function

import sys
import traceback

# 打开后，每次「把已挂父的控件重新摆放」都打一行，用于定位 GTK 的 reparent 警告来源
import os as _os
_DEBUG_REPARENT = _os.environ.get("TK_DEBUG_REPARENT") == "1"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

# ---------------------------------------------------------------- 日志

def warn(msg):
    """兼容层自己的告警（不影响被测程序运行，但必须可见）。"""
    sys.stderr.write("TKWARN: %s\n" % (msg,))


def _log_exc(where):
    sys.stderr.write("TKEXC in %s:\n%s\n" % (where, traceback.format_exc()))


# ---------------------------------------------------------------- 颜色

_NAMED = {
    "white": "#ffffff", "black": "#000000", "red": "#ff0000", "green": "#00ff00",
    "blue": "#0000ff", "yellow": "#ffff00", "cyan": "#00ffff", "magenta": "#ff00ff",
    "gray": "#808080", "grey": "#808080", "darkgray": "#a9a9a9", "lightgray": "#d3d3d3",
    "orange": "#ffa500", "purple": "#800080", "pink": "#ffc0cb", "brown": "#a52a2a",
}


def color(spec, default=None):
    """tk 颜色 → "#rrggbb"；认不出来就返回 default（不让一个怪颜色把界面搞崩）。"""
    if spec is None:
        return default
    s = str(spec).strip().lower()
    if not s:
        return default
    if s in _NAMED:
        return _NAMED[s]
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            return "#" + "".join(c * 2 for c in h)
        if len(h) == 6:
            return "#" + h
        if len(h) == 12:                      # #rrrrggggbbbb（tk 也认）
            return "#" + h[0:2] + h[4:6] + h[8:10]
    return default


# ---------------------------------------------------------------- 字体

# Windows 上用的字体板上没有；映射到板上实测存在的（fc-list 45 条里挑的）
_FONT_MAP = {
    "microsoft yahei ui": "WenQuanYi Zen Hei",
    "microsoft yahei": "WenQuanYi Zen Hei",
    "msyh": "WenQuanYi Zen Hei",
    "consolas": "DejaVu Sans Mono",
    "courier new": "DejaVu Sans Mono",
    "courier": "DejaVu Sans Mono",
    "segoe ui": "DejaVu Sans",
    "arial": "DejaVu Sans",
    "helvetica": "DejaVu Sans",
    "tahoma": "DejaVu Sans",
    "times new roman": "DejaVu Serif",
}


def font_spec(spec):
    """tk 字体（("family", size[, "bold"]) 元组 / "TkDefaultFont" 之类名字 / 字符串）
    → (family, size_pt, bold)。"""
    if spec is None:
        return None
    if isinstance(spec, (tuple, list)):
        family = str(spec[0]) if spec else "DejaVu Sans"
        size = 10
        bold = False
        for item in spec[1:]:
            if isinstance(item, (int, float)):
                # tk 里正数是磅、负数是像素；这里统一按磅算（负值当像素÷1.33近似）
                size = abs(float(item)) if item >= 0 else abs(float(item)) * 0.75
            elif str(item).lower() in ("bold", "normal", "italic", "roman"):
                bold = str(item).lower() == "bold"
        return (_FONT_MAP.get(family.strip().lower(), family), size, bold)
    s = str(spec).strip()
    if s.lower().startswith("tk") and s.lower().endswith("font"):
        return ("DejaVu Sans", 10, False)
    # "-family-size-style" 形式
    if s.startswith("-"):
        parts = s.split("-")
        if len(parts) >= 3:
            try:
                return (_FONT_MAP.get(parts[1].lower(), parts[1]), abs(int(parts[2])) * 0.75, False)
            except Exception:
                pass
    return ("DejaVu Sans", 10, False)


def font_to_css(spec):
    got = font_spec(spec)
    if not got:
        return ""
    family, size, bold = got
    css = ' font-family: "%s"; font-size: %.1fpt;' % (family, max(5.0, size))
    if bold:
        css += " font-weight: bold;"
    return css


def make_pango_font(spec):
    got = font_spec(spec)
    if not got:
        return None
    family, size, bold = got
    d = Pango.FontDescription()
    d.set_family(family)
    d.set_size(int(round(size * Pango.SCALE)))
    if bold:
        d.set_weight(Pango.Weight.BOLD)
    return d


# ---------------------------------------------------------------- CSS 样式缓存

_STYLE_CACHE = {}
_STYLE_SEQ = [0]


def style_class(bg=None, fg=None, font=None, border=None, border_width=0,
                radius=0, padx=None, pady=None, extra=""):
    """按 (底色, 前景, 字体, 边框…) 组合复用 CSS class，避免每个控件都注册一个 provider。"""
    key = (bg, fg, font, border, border_width, radius, padx, pady, extra)
    if key in _STYLE_CACHE:
        return _STYLE_CACHE[key]
    _STYLE_SEQ[0] += 1
    cls = "tkstyle%d" % _STYLE_SEQ[0]
    css = ".%s {" % cls
    if bg:
        css += " background-color: %s; background-image: none;" % bg
    if fg:
        css += " color: %s;" % fg
    css += font_to_css(font)
    if border_width and border:
        css += " border: %dpx solid %s;" % (border_width, border)
    if radius:
        css += " border-radius: %dpx;" % radius
    if padx is not None or pady is not None:
        css += " padding: %dpx %dpx;" % (pady or 0, padx or 0)
    css += extra
    css += " }"
    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    except Exception:
        _log_exc("load css: " + css)
    _STYLE_CACHE[key] = cls
    return cls


def apply_style(widget, **kw):
    cls = style_class(**kw)
    if cls:
        widget.get_style_context().add_class(cls)
    return cls


def remove_style(widget, cls):
    if cls:
        widget.get_style_context().remove_class(cls)


# ---------------------------------------------------------------- 定时器

_TIMER_SEQ = [0]


def _run_guarded(fn, args, where):
    try:
        fn(*args)
    except Exception:
        _log_exc(where)


def add_timeout(ms, fn, *args):
    def tick():
        try:
            fn(*args)
        except Exception:
            _log_exc("after")
        return False                      # 一次性

    # 返回真的 GLib source id —— after_cancel 要拿它去 source_remove。
    # 早先返回的是自己的计数器，取消时 source_remove 一个不存在的 id，
    # 刷一堆 "Source ID was not found when attempting to remove it"。
    sid = GLib.timeout_add(max(0, int(ms)), tick)
    return ("after", sid)


def add_idle(fn, *args):
    def tick():
        try:
            fn(*args)
        except Exception:
            _log_exc("after_idle")
        return False

    sid = GLib.idle_add(tick)
    return ("idle", sid)


def pump_events(max_rounds=200):
    """tk 的 update()/update_idletasks()：把已经排队的事件跑掉，不进入 mainloop。"""
    for _ in range(max_rounds):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration_do(False)
    ctx = GLib.MainContext.default()
    for _ in range(max_rounds):
        if not ctx.pending():
            break
        ctx.iteration(False)


# ---------------------------------------------------------------- 事件

class Event(object):
    """tk 事件对象的替身。界面上实际用到 .x .y .width .height .delta。"""

    def __init__(self, widget=None, **kw):
        self.widget = widget
        self.x = kw.get("x", 0)
        self.y = kw.get("y", 0)
        self.x_root = kw.get("x_root", 0)
        self.y_root = kw.get("y_root", 0)
        self.width = kw.get("width", 0)
        self.height = kw.get("height", 0)
        self.delta = kw.get("delta", 0)
        self.keysym = kw.get("keysym", "")
        self.char = kw.get("char", "")
        self.state = kw.get("state", 0)
        self.num = kw.get("num", 0)
        self.type = kw.get("type", "")

    def __repr__(self):
        return "<Event %s x=%s y=%s>" % (self.type, self.x, self.y)


def _keysym_name(ev):
    try:
        return Gdk.keyval_name(ev.keyval) or ""
    except Exception:
        return ""


def _mods(ev):
    """→ set of 'Control','Shift','Alt'"""
    out = set()
    st = ev.state
    if st & Gdk.ModifierType.CONTROL_MASK:
        out.add("Control")
    if st & Gdk.ModifierType.SHIFT_MASK:
        out.add("Shift")
    if st & Gdk.ModifierType.MOD1_MASK:
        out.add("Alt")
    return out


# tk 序列 → (gtk signal, 需要一个包装器)
_SIGNAL_MAP = {
    "Button": "button-press-event",
    "ButtonPress": "button-press-event",
    "ButtonRelease": "button-release-event",
    "Motion": "motion-notify-event",
    "B1-Motion": "motion-notify-event",
    "B2-Motion": "motion-notify-event",
    "B3-Motion": "motion-notify-event",
    "Enter": "enter-notify-event",
    "Leave": "leave-notify-event",
    "Key": "key-press-event",
    "KeyPress": "key-press-event",
    "KeyRelease": "key-release-event",
    "Configure": "size-allocate",
    "Destroy": "destroy",
    "FocusIn": "focus-in-event",
    "FocusOut": "focus-out-event",
    "MouseWheel": "scroll-event",
    "Expose": "draw",
}


def parse_sequence(seq):
    """解析 tk 的事件序列 → (修饰键集合, 键名或None, GTK 信号名或None, 鼠标键号或None)。

    这里必须返回 **GTK 的信号名**（`size-allocate` 之类），
    早先版本直接返回 tk 的名字（`Configure`），`connect()` 直接 TypeError，
    结果所有鼠标与 <Configure> 绑定都静默失效 —— 界面上表现为「点了没反应」。
    """
    import re
    s = str(seq).strip()
    if s.startswith("<<") and s.endswith(">>"):
        return (set(), s[2:-2], "__virtual__", None)
    if not (s.startswith("<") and s.endswith(">")):
        return (set(), s, None, None)

    body = s[1:-1]
    mods = set()
    toks = body.split("-")
    double = False
    while toks and toks[0] in ("Control", "Shift", "Alt", "Lock", "Double", "Triple"):
        t = toks.pop(0)
        if t in ("Control", "Shift", "Alt"):
            mods.add(t)
        elif t in ("Double", "Triple"):
            double = True
            mods.add(t)
    rest = "-".join(toks)

    # --- 鼠标
    m = re.match(r"^B(\d)-Motion$", rest)
    if m:
        return (mods, None, "motion-notify-event", int(m.group(1)))
    m = re.match(r"^(?:Button|ButtonPress)-?(\d)?$", rest)
    if m:
        return (mods, None, "button-press-event",
                int(m.group(1)) if m.group(1) else None)
    m = re.match(r"^ButtonRelease-?(\d)?$", rest)
    if m:
        return (mods, None, "button-release-event",
                int(m.group(1)) if m.group(1) else None)
    if double:
        return (mods, None, "button-press-event", 1)
    if rest in ("MouseWheel",):
        return (mods, None, "scroll-event", None)

    # --- 生命周期 / 其它
    simple = {
        "Configure": "size-allocate", "Destroy": "destroy",
        "Enter": "enter-notify-event", "Leave": "leave-notify-event",
        "FocusIn": "focus-in-event", "FocusOut": "focus-out-event",
        "Expose": "draw",
    }
    if rest in simple:
        return (mods, None, simple[rest], None)
    if rest in ("Key", "KeyPress"):
        return (mods, None, "key-press-event", None)
    if rest == "KeyRelease":
        return (mods, None, "key-release-event", None)

    # --- 剩下的都当按键
    return (mods, rest, None, None)


_KEYSYM_ALIAS = {
    "Escape": "Escape", "Return": "Return", "space": "space", "Tab": "Tab",
    "BackSpace": "BackSpace", "Delete": "Delete", "Up": "Up", "Down": "Down",
    "Left": "Left", "Right": "Right", "Home": "Home", "End": "End",
    "Prior": "Page_Up", "Next": "Page_Down",
}


def _norm_keysym(name):
    return _KEYSYM_ALIAS.get(name, name)


# ---------------------------------------------------------------- 变量

class Variable(object):
    """tk 的 Variable：StringVar / IntVar / BooleanVar / DoubleVar 的基类。

    界面上真的用到 trace_add('write', cb)（uistyle 的文本框跟随变量刷新），
    所以 trace 必须真的能触发。
    """

    _default = ""
    _type = str

    def __init__(self, master=None, value=None, name=None):
        self._name = name or ("PYVAR_%d" % id(self))
        self._cb = []                     # [(mode, callback)]
        self._traces = []
        self._value = self._default if value is None else self._coerce(value)

    def _coerce(self, value):
        try:
            return self._type(value)
        except Exception:
            return self._default

    def get(self):
        return self._value

    def set(self, value):
        new = self._coerce(value)
        if new == self._value:
            return
        self._value = new
        self._fire("write")
        self._fire("change") if False else None

    def _fire(self, mode):
        for m, cb in list(self._cb):
            if m != mode:
                continue
            try:
                cb(self._name, "", mode)
            except Exception:
                _log_exc("trace " + mode)

    # --- 新式 API（3.6+）
    def trace_add(self, mode, callback):
        self._cb.append((mode, callback))
        return (self._name, mode, callback)

    def trace_remove(self, mode, cbname):
        self._cb = [x for x in self._cb if not (x[0] == mode and x[1] is cbname)]

    def trace_variable(self, mode, callback):
        self._cb.append((mode, callback))
        return "%s%s%d" % (self._name, mode, len(self._cb))

    # --- 老式 API
    def trace(self, mode, callback):
        return self.trace_variable(mode, callback)

    def trace_vdelete(self, mode, cbname):
        pass

    def trace_info(self):
        return [(m, "", cb) for m, cb in self._cb]


class StringVar(Variable):
    _default = ""
    _type = str


class IntVar(Variable):
    _default = 0
    _type = int


class DoubleVar(Variable):
    _default = 0.0
    _type = float


class BooleanVar(Variable):
    _default = False

    def _coerce(self, value):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)


# ---------------------------------------------------------------- 几何管理

# 同轴判断：top/bottom 竖排，left/right 横排
_V_AXIS = ("top", "bottom")
_H_AXIS = ("left", "right")


def _pad2(v):
    """tk 的 padx/pady 可以是标量，也可以是 (左, 右) 二元组。"""
    if v is None:
        return (0, 0)
    if isinstance(v, (tuple, list)):
        if len(v) == 2:
            return (int(v[0]), int(v[1]))
        if len(v) == 1:
            return (int(v[0]), int(v[0]))
        return (0, 0)
    try:
        return (int(v), int(v))
    except Exception:
        return (0, 0)


class LayoutHost(object):
    """一个容器控件内部的布局宿主。

    容器控件（Frame / Toplevel / Tk / Canvas 之外的）内部结构：
        <控件自身 Gtk 容器>  └── _host (Gtk.Box)  └── 子控件
    需要换轴/换布局方式时**整块替换 _host**，控件自身不动。
    """

    def __init__(self, outer):
        self.outer = outer                 # 控件自身的 Gtk 容器（Gtk.Box，竖排）
        self.mode = None                   # None / 'pack' / 'grid'
        self.axis = None                   # 'v' / 'h'
        self._host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.outer.pack_start(self._host, True, True, 0)
        # 中间的宿主 Box **必须显示**：子控件自己的 show_all() 不会把祖先显出来，
        # 祖先不可见 → 整棵子树 map=0、allocation 全是 1×1（踩过，整窗空白）。
        self._host.show()
        self._packed = []                  # 顺序记录，用于混轴时重建
        self._weights = {}                 # (kind, index) -> weight

    # ---------- host 替换
    # 注意：PyGObject 里 GObject.ref()/unref() 是**不支持**的（会抛 RuntimeError），
    # 所以这里只靠 Python 引用保活：局部变量 old 活着，被 remove 下来的 widget 就不会被销毁。
    def _replace_host(self, orientation):
        old = self._host
        new = Gtk.Box(orientation=orientation)
        kids = list(old.get_children())
        for k in kids:
            old.remove(k)
            new.pack_start(k, True, True, 0)
        self.outer.remove(old)
        self.outer.pack_start(new, True, True, 0)
        new.show()
        self._host = new

    def _reset_empty_host(self, orientation, keep_children=False):
        """把当前 host 换成一个空的（pack ↔ grid 切换时用）。返回被清出来的旧子控件列表。"""
        old = self._host
        kids = list(old.get_children())
        for k in kids:
            old.remove(k)
        new = Gtk.Box(orientation=orientation)
        self.outer.remove(old)
        self.outer.pack_start(new, True, True, 0)
        new.show()
        self._host = new
        return kids if keep_children else []

    def _reparent_guard(self, child_gtk):
        """tk 允许把控件从一个父容器挪到另一个；GTK 不允许「已挂父的控件再挂一次」。
        所以摆放前先把已有的父关系摘掉（否则刷一堆
        `Can't set a parent on widget which has a parent` 警告，控件还摆不对）。"""
        try:
            p = child_gtk.get_parent()
            if p is not None:
                if _DEBUG_REPARENT:
                    warn("reparent %s（原父 %s）" % (type(child_gtk).__name__,
                                                    type(p).__name__))
                p.remove(child_gtk)
        except Exception:
            pass

    # ---------- pack
    def pack(self, child_gtk, side="top", fill="none", expand=False,
             padx=None, pady=None, before=None, after=None, ipadx=0, ipady=0,
             owner=None, anchor=None):
        self._reparent_guard(child_gtk)
        if self.mode == "grid":
            warn("同一容器里又用 pack 又用 grid（tk 本身会报错），按 pack 处理")
        self.mode = "pack"
        axis = "v" if side in _V_AXIS else "h"
        item = dict(gtk=child_gtk, side=side, fill=fill, expand=bool(expand),
                    padx=padx, pady=pady, ipadx=ipadx, ipady=ipady, owner=owner,
                    anchor=anchor)
        if self.axis is None:
            self.axis = axis
            self._host.set_orientation(
                Gtk.Orientation.VERTICAL if axis == "v" else Gtk.Orientation.HORIZONTAL)
        elif axis != self.axis:
            self._wrap_for_axis(axis)
            self.axis = axis
        self._packed.append(item)
        self._apply_pack(item)
        self._place_pack(item, before, after)

    def _wrap_for_axis(self, new_axis):
        """混轴：把现有内容收进一个旧轴的内层 Box，再放进一个新轴的外层 Box。"""
        warn("pack 在同一个容器里混用了竖排和横排，已自动套一层容器（布局可能与 Windows 略有差异）")
        inner = Gtk.Box(orientation=self._host.get_orientation())
        kids = list(self._host.get_children())
        for k in kids:
            self._host.remove(k)
            inner.pack_start(k, True, True, 0)
        self._replace_host(
            Gtk.Orientation.VERTICAL if new_axis == "v" else Gtk.Orientation.HORIZONTAL)
        self._host.pack_start(inner, True, True, 0)

    def _apply_pack(self, item):
        g = item["gtk"]
        fill = str(item["fill"] or "none")
        hx = fill in ("x", "both")     # 10-31：原来是 "x" in fill —— "both" 里没有 x / y，fill="both" 被当成不填，
        vy = fill in ("y", "both")     # 多行文本框缩成 8 像素宽居中的一窄列（板端 DP 接 2560×1440 显示器时发现）
        expand = item["expand"]
        axis = "v" if item["side"] in _V_AXIS else "h"
        hexp = hx or (expand and axis == "h")
        vexp = vy or (expand and axis == "v")
        g.set_hexpand(hexp)
        g.set_vexpand(vexp)

        # anchor：tk 的 pack(anchor="w") 表示"贴在地块的西边"，不 fill 的那一轴才起作用。
        # 早先完全没实现，于是 uistyle 里常见的 `.pack(anchor="w")`（指标卡标题等）全被居中。
        anc = str(item.get("anchor") or "")
        if hx or not anc:
            g.set_halign(Gtk.Align.FILL if hx else
                         (Gtk.Align.CENTER if not hexp else Gtk.Align.FILL))
        else:
            g.set_halign(Gtk.Align.START if "w" in anc else
                         Gtk.Align.END if "e" in anc else Gtk.Align.CENTER)
        if vy or not anc:
            g.set_valign(Gtk.Align.FILL if vy else
                         (Gtk.Align.CENTER if not vexp else Gtk.Align.FILL))
        else:
            g.set_valign(Gtk.Align.START if "n" in anc else
                         Gtk.Align.END if "s" in anc else Gtk.Align.CENTER)
        # 不 fill 又 expand 的时候，tk 里控件保持自然大小、居中在"地块"里
        if expand and not hx and not anc:
            g.set_halign(Gtk.Align.CENTER)
        if expand and not vy and not anc:
            g.set_valign(Gtk.Align.CENTER)
        pl, pr = _pad2(item["padx"])
        pt, pb = _pad2(item["pady"])
        g.set_margin_start(pl)
        g.set_margin_end(pr)
        g.set_margin_top(pt)
        g.set_margin_bottom(pb)

    def _place_pack(self, item, before, after):
        g = item["gtk"]
        side = item["side"]
        at_start = side in ("top", "left")
        self._host.pack_start(g, True, True, 0) if at_start else \
            self._host.pack_end(g, True, True, 0)
        if before is not None or after is not None:
            self._reorder(before, after)

    def _reorder(self, before, after):
        kids = self._host.get_children()
        ref = None
        if before is not None:
            ref = self._tk2gtk(before)
            idx = kids.index(ref) if ref in kids else None
            if idx is not None and idx > 0:
                self._host.reorder_child(kids[-1], idx)
        elif after is not None:
            ref = self._tk2gtk(after)
            idx = kids.index(ref) if ref in kids else None
            if idx is not None:
                self._host.reorder_child(kids[-1], idx + 1)

    @staticmethod
    def _tk2gtk(w):
        return getattr(w, "_gtk", w)

    # ---------- grid
    def grid(self, child_gtk, row=0, column=0, rowspan=1, columnspan=1, sticky="",
             padx=None, pady=None, ipadx=0, ipady=0, owner=None):
        if self.mode == "pack":
            warn("同一容器里又用 grid 又用 pack（tk 本身会报错），按 grid 处理")
        if self.mode != "grid":
            self.mode = "grid"
            kids = self._reset_empty_host(Gtk.Orientation.VERTICAL)
            gridw = Gtk.Grid()
            gridw.set_row_homogeneous(False)
            gridw.set_column_homogeneous(False)
            self._host.pack_start(gridw, True, True, 0)
            gridw.show()
            self._host = gridw
            self._grid_items = []
        new = dict(gtk=child_gtk, row=row, column=column,
                   rowspan=rowspan, columnspan=columnspan,
                   sticky=sticky, padx=padx, pady=pady, owner=owner)
        # 同一个控件被 grid 第二次（很常见：先摆上去，再 grid_configure 改 sticky）
        # 必须**原地更新**，不能 append + 再 attach —— 重复 attach 到同一格会让 GTK 把控件藏掉。
        old = None
        for it in self._grid_items:
            if it["gtk"] is child_gtk:
                old = it
                break
        if old is not None:
            moved = (int(old["row"]) != int(row) or int(old["column"]) != int(column)
                     or int(old["rowspan"]) != int(rowspan)
                     or int(old["columnspan"]) != int(columnspan))
            old.update(new)
            if moved:
                try:
                    self._host.remove(child_gtk)
                except Exception:
                    pass
                self._host.attach(child_gtk, int(column), int(row),
                                  int(columnspan), int(rowspan))
            self._apply_grid(old)
            self._update_grid_align()
            return
        self._grid_items.append(new)
        self._reparent_guard(child_gtk)
        self._host.attach(child_gtk, int(column), int(row),
                          int(columnspan), int(rowspan))
        self._apply_grid(self._grid_items[-1])
        self._update_grid_align()

    def _update_grid_align(self):
        """Gtk.Grid 在没有任何行需要扩展时，会把整块内容**垂直居中**；
        而 tk 的 grid 是从上往下排的。所以没有行权重时把 valign 定到 START。"""
        if self.mode != "grid":
            return
        has_row_weight = any(v > 0 for (kind, _i), v in self._weights.items() if kind == "row")
        try:
            self._host.set_valign(Gtk.Align.FILL if has_row_weight else Gtk.Align.START)
            self._host.set_halign(Gtk.Align.FILL)
        except Exception:
            pass

    def _apply_grid(self, item):
        g = item["gtk"]
        st = str(item["sticky"] or "")
        # tk 的 sticky 语义：只有**两边都粘**（ew / ns / nsew）才算"填满格子"，
        # 单边（w / n / e / s）只是靠边对齐、**不抢空间**。
        # 早先把 'w' 也当成 hexpand=True，结果左栏（sticky="nw"）把宽度抢走，
        # 右边的信息栏被挤成一字一行（实测踩过）。
        hx_both = ("e" in st and "w" in st)
        vy_both = ("n" in st and "s" in st)
        g.set_hexpand(hx_both)
        g.set_vexpand(vy_both)
        if "e" in st and "w" not in st:
            g.set_halign(Gtk.Align.END)
        elif "w" in st and "e" not in st:
            g.set_halign(Gtk.Align.START)
        else:
            g.set_halign(Gtk.Align.FILL if hx_both else Gtk.Align.CENTER)
        if "s" in st and "n" not in st:
            g.set_valign(Gtk.Align.END)
        elif "n" in st and "s" not in st:
            g.set_valign(Gtk.Align.START)
        else:
            g.set_valign(Gtk.Align.FILL if vy_both else Gtk.Align.CENTER)
        pl, pr = _pad2(item["padx"])
        pt, pb = _pad2(item["pady"])
        g.set_margin_start(pl)
        g.set_margin_end(pr)
        g.set_margin_top(pt)
        g.set_margin_bottom(pb)

    def set_weight(self, kind, index, weight):
        self._weights[(kind, int(index))] = int(weight or 0)
        if self.mode == "grid":
            for it in getattr(self, "_grid_items", []):
                self._apply_grid(it)
            self._update_grid_align()

    def forget(self, child_gtk):
        try:
            parent = child_gtk.get_parent()
            if parent is not None:
                parent.remove(child_gtk)
        except Exception:
            pass
        self._packed = [i for i in self._packed if i["gtk"] is not child_gtk]
        if self.mode == "grid":
            for it in list(getattr(self, "_grid_items", [])):
                if it["gtk"] is child_gtk:
                    self._grid_items.remove(it)

    # ---------- 查询（对应 tk 的 grid_slaves / pack_slaves / grid_info）
    def slaves(self, row=None, column=None):
        out = []
        for it in getattr(self, "_grid_items", []):
            if row is not None and int(it["row"]) != int(row):
                continue
            if column is not None and int(it["column"]) != int(column):
                continue
            if it.get("owner") is not None:
                out.append(it["owner"])
        return out

    def info_of(self, child_gtk):
        for it in getattr(self, "_grid_items", []):
            if it["gtk"] is child_gtk:
                return {k: it[k] for k in ("row", "column", "rowspan", "columnspan",
                                           "sticky", "padx", "pady")}
        return None
