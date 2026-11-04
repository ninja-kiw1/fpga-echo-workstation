# -*- coding: utf-8 -*-
"""_widgets.py —— tkinter 兼容层的控件：Tk/Toplevel/Frame/Label/Button/Entry/…

约定：每个控件包一层，`self._gtk` 是真正放进 GTK 树里的那个 widget。
容器的子控件由 `_gtkbase.LayoutHost` 负责摆放。
"""

from __future__ import print_function

import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

from . import _gtkbase as B  # noqa: E402


class TclError(Exception):
    pass


# ---------------------------------------------------------------- 常量

TRUE = 1
FALSE = 0
N = "n"
S = "s"
E = "e"
W = "w"
NW = "nw"
NE = "ne"
SW = "sw"
SE = "se"
CENTER = "center"
NSEW = "nsew"
NS = "ns"
EW = "ew"
TOP = "top"
BOTTOM = "bottom"
LEFT = "left"
RIGHT = "right"
X = "x"
Y = "y"
BOTH = "both"
NONE = "none"
HORIZONTAL = "horizontal"
VERTICAL = "vertical"
END = "end"
DISABLED = "disabled"
NORMAL = "normal"
ACTIVE = "active"
HIDDEN = "hidden"
TkVersion = 8.6

_ROOT = [None]

# ---------------------------------------------------------------- 宽松模式（只用于「发现缺口」）
# 默认严格：缺 API 就 AttributeError，和真 tk 一致。
# 打开 TK_SHIM_LENIENT=1 后，未知属性不再抛异常，而是返回一个「什么都不做」的对象并
# 立刻把名字打到 stderr。这样跑一遍就能拿到**完整的**缺口清单，而不是一次只修一个。
_LENIENT = os.environ.get("TK_SHIM_LENIENT") == "1"
_MISSING_SEEN = set()


def _note_missing(owner, name):
    key = "%s.%s" % (owner, name)
    if key in _MISSING_SEEN:
        return
    _MISSING_SEEN.add(key)
    sys.stderr.write("TKMISSING %s\n" % key)


class _Missing(object):
    """宽松模式下未知属性的占位：可调用、可当下标、可迭代（空），一律返回 None。"""

    def __init__(self, owner, name):
        self._owner = owner
        self._name = name

    def __call__(self, *a, **kw):
        return None

    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)
        return _Missing(self._owner, self._name + "." + item)

    def __getitem__(self, item):
        return None

    def __setitem__(self, k, v):
        return None

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    __nonzero__ = __bool__

    def __repr__(self):
        return "<TK missing %s.%s>" % (self._owner, self._name)


def missing_report():
    """退出时把缺口清单打出来（宽松模式）。"""
    if _MISSING_SEEN:
        sys.stderr.write("TKMISSING_TOTAL %d\n" % len(_MISSING_SEEN))
        for k in sorted(_MISSING_SEEN):
            sys.stderr.write("TKMISSING_LIST %s\n" % k)


def _root():
    if _ROOT[0] is None:
        raise TclError("no default root window; create tkinter.Tk() first")
    return _ROOT[0]


# ---------------------------------------------------------------- 控件基类

_STYLE_OPTS = ("background", "bg", "foreground", "fg", "font", "activebackground",
               "activeforeground", "disabledforeground", "selectbackground",
               "selectforeground", "highlightbackground", "highlightcolor",
               "highlightthickness", "bd", "borderwidth", "relief", "troughcolor",
               "insertbackground", "readonlybackground")


class BaseWidget(object):
    """所有控件的公共行为：配置、绑定、定时器、布局入口、尺寸查询。"""

    _is_container = False
    _gtk_container = False        # True → 自身是 Gtk.Box，可当布局宿主

    def __init__(self, master=None, cnf=None, **kw):
        self.master = master
        # master=None 有两种情况：正在构造根窗口（_ROOT 还没建好），或者用默认根窗口
        self.tk = getattr(master, "tk", None) if master is not None else _ROOT[0]
        self.children = {}
        self._gtk = None
        self._layout = None
        self._cfg = {}
        self._binds = []          # [(seq, mods, key, sig, func, gtk_handler_id)]
        self._style_classes = []
        self._destroyed = False
        self._timers = []
        self._propagate = True
        self._pending_configure = []
        if master is not None:
            try:
                master.children[self._name_of()] = self
            except Exception:
                pass
        if cnf:
            kw.update(cnf)
        self._build()
        if kw:
            self.configure(**kw)
        self._flush_deferred()

    # --- 子类实现
    def _build(self):
        raise NotImplementedError

    def _name_of(self):
        return "%s%d" % (self.__class__.__name__.lower(), id(self) % 100000)

    def _flush_deferred(self):
        for fn in self._pending_configure:
            try:
                fn()
            except Exception:
                B._log_exc("deferred configure")
        self._pending_configure = []

    # --- 配置
    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        touched_style = False
        for k, v in kw.items():
            self._cfg[k] = v
            if k in _STYLE_OPTS:
                touched_style = True
            try:
                self._on_opt(k, v)
            except Exception:
                B.warn("%s 不认配置项 %s（已忽略）: %r" % (self.__class__.__name__, k, v))
        if touched_style:
            self._restyle()
        return None

    config = configure

    def _on_opt(self, name, value):
        pass

    def cget(self, name):
        return self._cfg.get(name)

    def __setitem__(self, k, v):
        self.configure(**{k: v})

    def __getitem__(self, k):
        return self.cget(k)

    def keys(self):
        return list(self._cfg.keys())

    # --- 样式
    def _restyle(self):
        for cls in self._style_classes:
            B.remove_style(self._gtk, cls)
        self._style_classes = []
        bg = B.color(self._cfg.get("background", self._cfg.get("bg")), None)
        fg = B.color(self._cfg.get("foreground", self._cfg.get("fg")), None)
        font = self._cfg.get("font")
        bw = self._cfg.get("borderwidth", self._cfg.get("bd")) or 0
        hb = B.color(self._cfg.get("highlightbackground"), None)
        cls = B.style_class(bg=bg, fg=fg, font=font,
                            border=hb, border_width=int(bw or 0) if hb else 0)
        if cls:
            self._gtk.get_style_context().add_class(cls)
            self._style_classes.append(cls)
        self._restyle_extra()

    def _restyle_extra(self):
        pass

    # --- 布局
    def _layout_host(self):
        """**本控件内部**的布局宿主（容纳本控件的子控件）。"""
        if self._layout is None:
            outer = self._gtk if self._gtk_container else None
            if outer is None:
                raise TclError("%s 不能容纳子控件" % self.__class__.__name__)
            self._layout = B.LayoutHost(outer)
        return self._layout

    def _parent_layout(self):
        """**父容器**的布局宿主 —— pack/grid 是把自己放进父容器里。
        早期版本这里错用了 self._layout_host()，等于把控件装进自己内部
        （GTK 会把一个 widget attach 到它自己的子孙里，直接死循环 + 100% CPU）。"""
        m = self.master
        if m is None:
            m = _ROOT[0]
        if m is None:
            raise TclError("%s 没有 master，也没有默认根窗口" % self.__class__.__name__)
        return m._layout_host()

    def pack(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        # 同 grid：pack_configure 不传的项沿用原值
        if "side" not in kw:
            kw.setdefault("side", "top")
        self._parent_layout().pack(
            self._gtk,
            side=kw.get("side", "top"),
            fill=kw.get("fill", "none"),
            expand=kw.get("expand", False),
            padx=kw.get("padx"), pady=kw.get("pady"),
            before=kw.get("before"), after=kw.get("after"),
            ipadx=kw.get("ipadx", 0), ipady=kw.get("ipady", 0), owner=self,
            anchor=kw.get("anchor"))
        self._gtk.show_all()
        return self

    def pack_forget(self):
        try:
            self._parent_layout().forget(self._gtk)
        except Exception:
            pass
        return self

    pack_forget_ = pack_forget

    def grid(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        # grid_configure 只改你传的那几项，没传的 row/column 必须**沿用原来的**。
        # 早先版本把 grid_configure(sticky="w") 当成全新的 grid(row=0, column=0)，
        # 结果控件被挪到左上角、被别的控件盖住（「不确定带」的数字框就这么消失的）。
        if "row" not in kw or "column" not in kw:
            info = self.grid_info()
            kw.setdefault("row", info.get("row", 0))
            kw.setdefault("column", info.get("column", 0))
            kw.setdefault("rowspan", info.get("rowspan", 1))
            kw.setdefault("columnspan", info.get("columnspan", 1))
        self._parent_layout().grid(
            self._gtk,
            row=kw.get("row", 0), column=kw.get("column", 0),
            rowspan=kw.get("rowspan", 1), columnspan=kw.get("columnspan", 1),
            sticky=kw.get("sticky", ""), padx=kw.get("padx"), pady=kw.get("pady"),
            ipadx=kw.get("ipadx", 0), ipady=kw.get("ipady", 0), owner=self)
        self._gtk.show_all()
        return self

    def grid_slaves(self, row=None, column=None):
        """tk 的 grid_slaves(row=, column=)：返回本容器里满足条件的子控件。"""
        try:
            return self._layout_host().slaves(row=row, column=column)
        except Exception:
            return []

    def pack_slaves(self):
        try:
            return [i.get("owner") for i in self._layout_host()._packed
                    if i.get("owner") is not None]
        except Exception:
            return []

    def grid_info(self):
        try:
            return self._parent_layout().info_of(self._gtk) or {}
        except Exception:
            return {}

    def grid_forget(self):
        try:
            self._parent_layout().forget(self._gtk)
        except Exception:
            pass
        return self

    # --- 行/列权重（tk 有两套写法：w.rowconfigure 与 w.grid_rowconfigure，两个都要有）
    def rowconfigure(self, index, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        self._layout_host().set_weight("row", index, kw.get("weight", 0))
        if kw.get("uniform"):
            self._set_uniform()
        return None

    def columnconfigure(self, index, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        self._layout_host().set_weight("col", index, kw.get("weight", 0))
        if kw.get("uniform"):
            self._set_uniform()
        return None

    def _set_uniform(self):
        """tk 的 uniform="xxx" 让同组列等宽；Gtk.Grid 对应的是 column_homogeneous。"""
        try:
            self._layout_host()._host.set_column_homogeneous(True)
        except Exception:
            pass

    grid_rowconfigure = rowconfigure
    grid_columnconfigure = columnconfigure
    grid_rowconfig = rowconfigure
    grid_columnconfig = columnconfigure

    # --- 尺寸传播
    def pack_propagate(self, flag=None):
        if flag is None:
            return self._propagate
        self._propagate = bool(flag)
        self._apply_propagate()
        return None

    def grid_propagate(self, flag=None):
        return self.pack_propagate(flag)

    propagate = pack_propagate

    def _apply_propagate(self):
        """pack_propagate(False)：容器固定成 width×height，不跟着子控件缩。
        uistyle.image_slot 就靠这个把 512×512 的图像位钉死。"""
        if getattr(self, "_propagate", True):
            return
        w = self._cfg.get("width")
        h = self._cfg.get("height")
        if w or h:
            try:
                self._gtk.set_size_request(int(w) if w else -1, int(h) if h else -1)
            except Exception:
                pass

    def pack_configure(self, cnf=None, **kw):
        return self.pack(cnf, **kw)

    def grid_configure(self, cnf=None, **kw):
        return self.grid(cnf, **kw)

    # --- 事件
    def bind(self, sequence=None, func=None, add=None):
        mods, key, sig, button = B.parse_sequence(sequence)
        if sig is None and key is None:
            return None
        if sig == "__virtual__":
            # 虚拟事件（如 <<ComboboxSelected>>）：记下来，由对应控件在合适的时机触发
            self._binds.append(dict(seq=sequence, mods=mods, key=key, sig=sig,
                                    button=button, func=func, id=None))
            self._virtual(key, func)
            return None
        rec = dict(seq=sequence, mods=mods, key=key, sig=sig, button=button,
                   func=func, id=None)
        target = self._bind_target()
        if sig is not None:
            handler = self._make_signal_handler(rec)
            try:
                rec["id"] = target.connect(sig, handler)
            except Exception as exc:
                B.warn("bind %s 失败（%s 不支持该信号: %s）" % (sequence, type(target).__name__, exc))
                return None
            if sig in ("button-press-event", "button-release-event", "motion-notify-event",
                       "scroll-event", "key-press-event", "key-release-event"):
                target.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                                  Gdk.EventMask.BUTTON_RELEASE_MASK |
                                  Gdk.EventMask.POINTER_MOTION_MASK |
                                  Gdk.EventMask.SCROLL_MASK |
                                  Gdk.EventMask.KEY_PRESS_MASK |
                                  Gdk.EventMask.KEY_RELEASE_MASK)
        else:
            # 纯按键绑定（<Control-o> / <Escape> / <F5>）：挂到 key-press-event 上按 keysym 过滤
            rec["sig"] = "key-press-event"
            try:
                rec["id"] = target.connect("key-press-event", self._make_key_handler(rec))
                target.add_events(Gdk.EventMask.KEY_PRESS_MASK)
            except Exception as exc:
                B.warn("bind %s 失败: %s" % (sequence, exc))
                return None
        self._binds.append(rec)
        return rec.get("id")

    def _virtual(self, name, func):
        """子类覆写：登记虚拟事件的回调。默认什么都不做。"""
        return None

    def _bind_target(self):
        return self._gtk

    def _make_signal_handler(self, rec):
        def handler(widget, event, *rest):
            try:
                return self._dispatch(rec, event, rest)
            except Exception:
                B._log_exc("bind " + str(rec["seq"]))
                return False

        return handler

    def _make_key_handler(self, rec):
        want = B._norm_keysym(rec["key"])

        def handler(widget, event):
            try:
                got = B._keysym_name(event)
                if got != want:
                    return False
                mods = B._mods(event)
                if mods != rec["mods"]:
                    return False
                return self._call_user(rec["func"], B.Event(
                    self, keysym=got, state=int(event.state), type="KeyPress"))
            except Exception:
                B._log_exc("keybind " + str(rec["seq"]))
                return False

        return handler

    def _dispatch(self, rec, event, rest):
        sig = rec["sig"]
        mod = rec.get("mods") or set()
        if mod:
            got = B._mods(event)
            if not mod.issubset(got):
                return False
        if sig == "size-allocate":
            # GTK 的 size-allocate 回调是 (widget, allocation)：allocation 就在 event 这个位置上。
            # 早先取 rest[0]（空）→ <Configure> 的 width / height 永远是 0 → uistyle.label 把折行宽度设成最小值，
            # 文字压成一窄列；每设一次又触发重新排版，循环不停（板端 DP 单核 100 %，10-31 接 2560×1440 显示器时发现）
            alloc = rest[0] if rest else event
            # tk 只在尺寸 / 位置真的变了才发 <Configure>；GTK 每次重新分配都发 size-allocate。
            # 不过滤的话：画布重画 -> 重新分配 -> <Configure> -> 程序重画（如延迟图）-> … 空闲时单核 100 %（10-31 采样确认）
            key = (alloc.x, alloc.y, alloc.width, alloc.height) if alloc else None
            if key is not None and rec.get("_last") == key:
                return None
            rec["_last"] = key
            ev = B.Event(self, width=alloc.width if alloc else 0,
                         height=alloc.height if alloc else 0,
                         x=alloc.x if alloc else 0, y=alloc.y if alloc else 0,
                         type="Configure")
            self._call_user(rec["func"], ev)
            return None
        if sig == "destroy":
            self._call_user(rec["func"], B.Event(self, type="Destroy"))
            return None
        if sig in ("enter-notify-event", "leave-notify-event",
                   "focus-in-event", "focus-out-event"):
            return self._call_user(rec["func"], B.Event(self, type=sig))
        if sig == "scroll-event":
            d = 0
            if event.direction == Gdk.ScrollDirection.UP:
                d = 1
            elif event.direction == Gdk.ScrollDirection.DOWN:
                d = -1
            elif event.direction == Gdk.ScrollDirection.SMOOTH:
                ok, dx, dy = event.get_scroll_deltas()
                d = 1 if dy < 0 else -1
            ev = B.Event(self, x=int(event.x), y=int(event.y), delta=d,
                         state=int(event.state), type="MouseWheel")
            return self._call_user(rec["func"], ev)
        if sig == "key-press-event":
            got = B._keysym_name(event)
            ev = B.Event(self, keysym=got, char=_event_char(event),
                         state=int(event.state), type="KeyPress")
            return self._call_user(rec["func"], ev)
        # --- 鼠标：按绑定时指定的键号过滤
        want_b = rec.get("button")
        if sig == "button-press-event":
            if want_b is not None and int(event.button) != int(want_b):
                return False
            ev = B.Event(self, x=int(event.x), y=int(event.y),
                         x_root=int(event.x_root), y_root=int(event.y_root),
                         num=int(event.button), state=int(event.state),
                         type="ButtonPress")
            return self._call_user(rec["func"], ev)
        if sig == "button-release-event":
            if want_b is not None and int(event.button) != int(want_b):
                return False
            ev = B.Event(self, x=int(event.x), y=int(event.y),
                         x_root=int(event.x_root), y_root=int(event.y_root),
                         num=int(event.button), state=int(event.state),
                         type="ButtonRelease")
            return self._call_user(rec["func"], ev)
        if sig == "motion-notify-event":
            # <B1-Motion> 只在左键按着时触发
            if want_b is not None:
                mask = (Gdk.ModifierType.BUTTON1_MASK if int(want_b) == 1
                        else Gdk.ModifierType.BUTTON2_MASK if int(want_b) == 2
                        else Gdk.ModifierType.BUTTON3_MASK)
                if not (event.state & mask):
                    return False
            ev = B.Event(self, x=int(event.x), y=int(event.y),
                         x_root=int(event.x_root), y_root=int(event.y_root),
                         state=int(event.state), type="Motion")
            return self._call_user(rec["func"], ev)
        ev = B.Event(self, x=int(getattr(event, "x", 0)), y=int(getattr(event, "y", 0)),
                     num=int(getattr(event, "button", 0)),
                     state=int(getattr(event, "state", 0)), type=sig)
        return self._call_user(rec["func"], ev)

    @staticmethod
    def _call_user(func, ev):
        if func is None:
            return False
        ret = func(ev)
        # 返回 "break" 表示吃掉事件（tk 语义）
        return ret == "break"

    def unbind(self, sequence, funcid=None):
        for rec in list(self._binds):
            if rec["seq"] == sequence and (funcid is None or rec.get("id") == funcid):
                try:
                    if rec.get("id"):
                        self._gtk.disconnect(rec["id"])
                except Exception:
                    pass
                self._binds.remove(rec)

    def bind_all(self, sequence=None, func=None, add=None):
        """极少用；挂到 root 上。"""
        return _root().bind(sequence, func, add) if _ROOT[0] else None

    # --- 定时器
    def after(self, ms, func=None, *args):
        if func is None:
            B.pump_events()
            return None
        tid = B.add_timeout(ms, func, *args)
        self._timers.append(tid)
        return tid

    def after_idle(self, func=None, *args):
        if func is None:
            B.pump_events()
            return None
        return B.add_idle(func, *args)

    def after_cancel(self, tid):
        try:
            if isinstance(tid, tuple):
                GLib.source_remove(tid[1]) if tid[1] else None
        except Exception:
            pass

    # --- 查询 / 动作
    def winfo_exists(self):
        return not self._destroyed

    def winfo_ismapped(self):
        try:
            return bool(self._gtk.get_mapped()) or bool(self._gtk.get_visible())
        except Exception:
            return False

    def winfo_width(self):
        try:
            return self._gtk.get_allocated_width()
        except Exception:
            return 0

    def winfo_height(self):
        try:
            return self._gtk.get_allocated_height()
        except Exception:
            return 0

    def winfo_rootx(self):
        try:
            return self._gtk.get_window().get_root_coords(0, 0)[1] if self._gtk.get_window() else 0
        except Exception:
            return 0

    def winfo_rooty(self):
        try:
            return self._gtk.get_window().get_root_coords(0, 0)[2] if self._gtk.get_window() else 0
        except Exception:
            return 0

    def winfo_toplevel(self):
        return self._toplevel()

    def _toplevel(self):
        w = self
        while w is not None and not isinstance(w, (Tk, Toplevel)):
            w = w.master
        return w

    def focus_set(self):
        try:
            self._gtk.grab_focus()
        except Exception:
            pass

    focus = focus_set

    def lift(self, above=None):
        try:
            self._gtk.grab_focus()
        except Exception:
            pass

    def update(self):
        B.pump_events()

    def update_idletasks(self):
        B.pump_events()

    def destroy(self):
        if self._destroyed:
            return
        self._destroyed = True
        for tid in self._timers:
            try:
                if isinstance(tid, tuple) and tid[0] == "after":
                    pass
            except Exception:
                pass
        self._timers = []
        try:
            self._gtk.destroy()
        except Exception:
            pass
        if self.master is not None:
            for k, v in list(getattr(self.master, "children", {}).items()):
                if v is self:
                    del self.master.children[k]

    def _show(self):
        try:
            self._gtk.show_all()
        except Exception:
            pass

    # --- 启用/禁用（tk 的 state / instate；Entry、Scale 之类没有独立实现的都走这里）
    def state(self, spec=None):
        try:
            sens = self._gtk.get_sensitive()
        except Exception:
            return ()
        if spec is None:
            return () if sens else ("disabled",)
        for s in spec:
            if s == "disabled":
                self._gtk.set_sensitive(False)
            elif s == "!disabled":
                self._gtk.set_sensitive(True)
        return None

    def instate(self, spec):
        try:
            dis = not self._gtk.get_sensitive()
        except Exception:
            return True
        return all(dis if s == "disabled" else not dis
                   for s in spec if s in ("disabled", "!disabled"))

    # --- 宽松模式下的未知 API
    def __getattr__(self, name):
        if name.startswith("_") or name in ("master", "tk"):
            raise AttributeError(name)
        if _LENIENT:
            _note_missing(type(self).__name__, name)
            return _Missing(type(self).__name__, name)
        raise AttributeError("%s 没有属性 %s（兼容层未实现）" % (type(self).__name__, name))


def _event_char(event):
    try:
        v = Gdk.keyval_to_unicode(event.keyval)
        if v:
            return chr(v)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- 顶层窗口

class _ToplevelBase(BaseWidget):
    _gtk_container = True

    def _build(self):
        self._win = Gtk.Window()
        self._outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._win.add(self._outer)
        self._gtk = self._outer
        self._menubar = None
        self._menu = None
        self._title = ""
        self._geometry = None
        self._resizable = (True, True)
        self._close_cb = None
        self._win.set_border_width(0)

    # --- 标题 / 几何
    def title(self, text=None):
        if text is None:
            return self._title
        self._title = str(text)
        self._win.set_title(self._title)
        return None

    def geometry(self, spec=None):
        if spec is None:
            return self._geometry or ""
        self._geometry = str(spec)
        rest = self._geometry
        wh, _, xy = rest.partition("+")
        try:
            w, h = wh.lower().split("x")
            self._win.resize(int(w), int(h))
        except Exception:
            pass
        if xy or rest.count("+") == 2:
            parts = rest.split("+")
            if len(parts) >= 3:
                try:
                    self._win.move(int(parts[1]), int(parts[2]))
                except Exception:
                    pass
        return None

    wm_geometry = geometry

    def resizable(self, width=None, height=None):
        if width is None:
            return self._resizable
        h = width if height is None else height
        self._resizable = (bool(width), bool(h))
        try:
            self._win.set_resizable(bool(width) or bool(h))
        except Exception:
            pass
        return None

    def minsize(self, w, h):
        try:
            self._win.set_size_request(int(w), int(h))
        except Exception:
            pass

    def maxsize(self, w=None, h=None):
        pass

    def protocol(self, name, func=None):
        if name in ("WM_DELETE_WINDOW", "wm_delete_window"):
            self._close_cb = func
            self._win.connect("delete-event", self._on_delete)
        return None

    def _on_delete(self, *_a):
        if self._close_cb:
            try:
                self._close_cb()
            except Exception:
                B._log_exc("WM_DELETE_WINDOW")
            return True
        return False

    def state(self, newstate=None):
        if newstate is None:
            return "normal"
        s = str(newstate)
        if s == "zoomed":
            self._win.maximize()
        elif s in ("normal", "iconic", "withdrawn"):
            self._win.unmaximize() if s == "normal" else None
            if s == "withdrawn":
                self._win.hide()
        return None

    def withdraw(self):
        self._win.hide()

    def deiconify(self):
        self._win.show_all()
        self._win.deiconify()
        self._win.present()

    def iconify(self):
        self._win.iconify()

    def attributes(self, *args, **kw):
        if kw.get("-alpha") is not None:
            try:
                self._win.set_opacity(float(kw["-alpha"]))
            except Exception:
                pass
        if kw.get("-topmost"):
            try:
                self._win.set_keep_above(bool(kw["-topmost"]))
            except Exception:
                pass
        return None

    wm_attributes = attributes

    def overrideredirect(self, flag=None):
        if flag is None:
            return False
        try:
            self._win.set_decorated(not bool(flag))
        except Exception:
            pass
        return None

    def transient(self, master=None):
        try:
            m = master if master is not None else self.master
            win = m._win if isinstance(m, _ToplevelBase) else None
            if win is not None:
                self._win.set_transient_for(win)
        except Exception:
            pass

    def grab_set(self):
        try:
            self._win.set_modal(True)
        except Exception:
            pass

    def grab_release(self):
        try:
            self._win.set_modal(False)
        except Exception:
            pass

    def wait_window(self, window=None):
        B.pump_events(2000)

    def focus_force(self):
        try:
            self._win.present()
        except Exception:
            pass

    def _bind_target(self):
        return self._win

    def _restyle(self):
        super(_ToplevelBase, self)._restyle()
        bg = B.color(self._cfg.get("background", self._cfg.get("bg")), None)
        if bg:
            cls = B.style_class(bg=bg)
            self._outer.get_style_context().add_class(cls)
            self._style_classes.append(cls)

    # --- 菜单
    def _set_menu(self, menu):
        self._menu = menu
        if self._menubar is None:
            self._menubar = Gtk.MenuBar()
            self._outer.pack_start(self._menubar, False, False, 0)
            self._outer.reorder_child(self._menubar, 0)
        for ch in self._menubar.get_children():
            self._menubar.remove(ch)
        if menu is not None:
            for item in menu._items:
                self._menubar.append(item)
        self._menubar.show_all()

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if "menu" in kw:
            self._set_menu(kw.pop("menu"))
        return super(_ToplevelBase, self).configure(None, **kw)

    def destroy(self):
        BaseWidget.destroy(self)
        # 根的 destroy 要把 Gtk.main() 拉出来。
        # 注意别写成「只有 _ROOT[0] is self 才 quit」——窗口的 destroy 信号会先把
        # _ROOT[0] 清成 None，那样永远进不了 main_quit，进程就吊在 mainloop 里。
        # 也要判 Gtk.main_level()：没进 mainloop 时调 main_quit 会刷
        # `gtk_main_quit: assertion 'main_loops != NULL' failed`。
        _ROOT[0] = None
        try:
            if Gtk.main_level() > 0:
                Gtk.main_quit()
        except Exception:
            pass


class Tk(_ToplevelBase):
    """根窗口。整个进程只能有一个（与 tk 一致）。"""

    def __init__(self, *a, **kw):
        if _ROOT[0] is not None:
            raise TclError("只能创建一个 Tk 根窗口")
        kw.setdefault("background", "#eef1f5")
        super(Tk, self).__init__(None, **kw)
        _ROOT[0] = self
        self._win.connect("destroy", self._on_win_destroy)
        self._win.set_position(Gtk.WindowPosition.CENTER)
        # 上位机界面按 ~1400×900 设计；板上屏是 2560×1440，给个稍大的默认尺寸看起来更舒展
        self._win.set_default_size(1500, 950)
        # tk.Tk() 一建出来窗口就该看得见（Windows 上也是先出空窗再画内容）。
        # 少了这一句，后面的 winfo_width() 全是 0、get_window() 返回 None，截图也拿不到。
        self._win.show_all()

    def _on_win_destroy(self, *_a):
        _ROOT[0] = None

    def mainloop(self, n=0):
        try:
            Gtk.main()
        except KeyboardInterrupt:
            pass

    def quit(self):
        try:
            Gtk.main_quit()
        except Exception:
            pass

    def destroy(self):
        _ToplevelBase.destroy(self)


class Toplevel(_ToplevelBase):
    def __init__(self, master=None, cnf=None, **kw):
        m = master if master is not None else _root()
        kw.setdefault("background", "#eef1f5")
        super(Toplevel, self).__init__(m, cnf, **kw)
        self._win.set_default_size(600, 400)
        try:
            self._win.set_transient_for(_ROOT[0]._win)
        except Exception:
            pass
        self._win.show_all()


# ---------------------------------------------------------------- 基础控件

class Frame(BaseWidget):
    _is_container = True
    _gtk_container = True

    def _build(self):
        # 不要无条件 set_size_request(1,1)：tk.Frame 不给 width/height 时应当「随内容」，
        # 强制一个最小尺寸会让它在 grid/pack 里长不大（踩过：整窗都是 1×1）。
        self._gtk = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

    def _on_opt(self, name, value):
        if name in ("width", "height"):
            w = self._cfg.get("width")
            h = self._cfg.get("height")
            try:
                self._gtk.set_size_request(int(w or 1), int(h or 1))
            except Exception:
                pass


class Label(BaseWidget):
    """tk.Label。

    **必须同时支持文字和图片**：上位机里有 5 个窗口、11 处用 `Label(image=…)` 显示图像
    （心超的视频帧、CT 的 3D/轴位/冠状面、程序台的瓦片、配准图、EF 四格图）。
    GTK3 的 `Gtk.Label` **不能直接放 pixbuf**，所以这里的 `_gtk` 是一个 Gtk.Box，
    里面装一个 Gtk.Label + 一个 Gtk.Image，按 `image` 选项切换谁可见。
    （早先只做了文字版，结果这些窗口"数据算出来了但画面是黑的/空的"。）
    """

    def _build(self):
        self._gtk = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._label = Gtk.Label()
        self._label.set_xalign(0.0)
        self._label.set_yalign(0.5)
        self._img = Gtk.Image()
        self._img.set_no_show_all(True)      # show_all 不会把它显出来，只有 image= 才显
        self._gtk.pack_start(self._label, True, True, 0)
        self._gtk.pack_start(self._img, True, True, 0)
        self._mode = "text"
        self._var_watch = None

    # --- 模式切换
    def _show_text(self):
        self._mode = "text"
        try:
            self._img.hide()
            self._label.set_no_show_all(False)
            self._label.show()
        except Exception:
            pass
        self._apply_resize()

    def _show_image(self):
        self._mode = "image"
        try:
            # 10-31：文字部分藏起来后要 no_show_all，否则之后哪次 pack() 的 show_all() 又把它显出来；
            # 它还带着 width=228 按"字符"折成的 1824 像素宽，把快照模式整个窗口撑到 3306 像素（屏幕 2560）
            self._label.set_no_show_all(True)
            self._label.hide()
            self._img.show()
        except Exception:
            pass
        self._apply_resize()

    def _apply_resize(self):
        w = self._cfg.get("width")
        h = self._cfg.get("height")
        try:
            if self._mode == "image":
                # tk 的 Label 显示图片时 width/height 是**像素**
                self._gtk.set_size_request(
                    int(w) if isinstance(w, (int, float)) and w else -1,
                    int(h) if isinstance(h, (int, float)) and h else -1)
            else:
                self._gtk.set_size_request(-1, -1)
        except Exception:
            pass

    def _on_opt(self, name, value):
        lb = self._label
        if name == "text":
            lb.set_text("" if value is None else str(value))
            if self._mode != "image":
                self._show_text()
        elif name == "textvariable":
            var = value
            if self._var_watch is not None:
                try:
                    self._var_watch[0].trace_remove("write", self._var_watch[1])
                except Exception:
                    pass

            def upd(*_a):
                lb.set_text("" if var.get() is None else str(var.get()))

            if hasattr(var, "trace_add"):
                var.trace_add("write", upd)
            self._var_watch = (var, upd)
            upd()
        elif name == "image":
            photo = value
            pb = getattr(photo, "_pixbuf", None)
            if pb is None:
                # image="" / None → 回到文字模式（ctwin 就是靠这个把提示文字换回来的）
                self._img.clear()
                self._show_text()
            else:
                self._img.set_from_pixbuf(pb)
                self._show_image()
                if pb is not None:
                    try:
                        self._img.set_size_request(pb.get_width(), pb.get_height())
                    except Exception:
                        pass
        elif name == "compound":
            pass
        elif name == "anchor":
            self._set_anchor(str(value))
        elif name == "justify":
            m = {"left": Gtk.Justification.LEFT, "right": Gtk.Justification.RIGHT,
                 "center": Gtk.Justification.CENTER}
            lb.set_justify(m.get(str(value), Gtk.Justification.LEFT))
        elif name == "wraplength":
            px = int(value or 0)
            if px == getattr(self, "_wrap_px", None):    # 同一宽度不再设：GTK 每设一次都会重新排版、再发 <Configure>
                return
            self._wrap_px = px
            if px > 0:
                lb.set_line_wrap(True)
                lb.set_max_width_chars(max(8, int(px / 8.0)))
            else:
                lb.set_line_wrap(False)
        elif name == "width":
            try:                                  # 有图时 tk 的 width 是像素（_apply_resize 处理），不再按字符设文字宽度
                lb.set_width_chars(-1 if self._mode == "image" else max(1, int(value)))
            except Exception:
                pass
            self._apply_resize()
        elif name == "height":
            self._apply_resize()
        elif name == "padx" or name == "pady":
            self._apply_padding()
        elif name == "cursor":
            pass

    def _apply_padding(self):
        pl, pr = B._pad2(self._cfg.get("padx"))
        pt, pb = B._pad2(self._cfg.get("pady"))
        cls = B.style_class(padx=max(pl, pr), pady=max(pt, pb))
        for w in (self._gtk, self._label):
            w.get_style_context().add_class(cls)
            self._style_classes.append(cls)

    def _set_anchor(self, a):
        amap = {"w": (0.0, 0.5), "e": (1.0, 0.5), "n": (0.5, 0.0), "s": (0.5, 1.0),
                "nw": (0.0, 0.0), "ne": (1.0, 0.0), "sw": (0.0, 1.0), "se": (1.0, 1.0),
                "center": (0.5, 0.5)}
        xa, ya = amap.get(a, (0.0, 0.5))
        self._label.set_xalign(xa)
        self._label.set_yalign(ya)

    def _restyle(self):
        super(Label, self)._restyle()
        # 颜色/字体也直接加到内层 Gtk.Label 上：GTK3 的 CSS 会继承 color/font，
        # 但显式加一份更稳妥（背景只加在外层 Box 上）。
        bg = B.color(self._cfg.get("background", self._cfg.get("bg")), None)
        fg = B.color(self._cfg.get("foreground", self._cfg.get("fg")), None)
        font = self._cfg.get("font")
        cls = B.style_class(fg=fg, font=font)
        if cls:
            self._label.get_style_context().add_class(cls)
            self._style_classes.append(cls)
        del bg


class Button(BaseWidget):
    def _build(self):
        self._gtk = Gtk.Button()
        self._gtk.set_relief(Gtk.ReliefStyle.NONE)
        self._cmd = None
        self._gtk.connect("clicked", self._on_clicked)

    def _on_clicked(self, *_a):
        if self._cmd:
            try:
                self._cmd()
            except Exception:
                B._log_exc("Button command")

    def _on_opt(self, name, value):
        b = self._gtk
        if name == "text":
            b.set_label("" if value is None else str(value))
        elif name == "textvariable":
            var = value

            def upd(*_a):
                b.set_label(str(var.get()))

            if hasattr(var, "trace_add"):
                var.trace_add("write", upd)
            upd()
        elif name == "command":
            self._cmd = value
        elif name == "image":
            # tk 的 Button 也支持 image=；GTK 这边是 set_image + always-show-image
            pb = getattr(value, "_pixbuf", None)
            if pb is None:
                b.set_image(None)
            else:
                b.set_image(Gtk.Image.new_from_pixbuf(pb))
                b.set_always_show_image(True)
        elif name == "compound":
            pass
        elif name == "state":
            b.set_sensitive(str(value) != "disabled")
        elif name in ("padx", "pady"):
            self._apply_padding()
        elif name == "anchor":
            pass
        elif name == "cursor":
            pass
        elif name == "width":
            try:
                b.set_size_request(int(value) * 8, -1)
            except Exception:
                pass

    def _apply_padding(self):
        pl, pr = B._pad2(self._cfg.get("padx"))
        pt, pb = B._pad2(self._cfg.get("pady"))
        cls = B.style_class(padx=max(pl, pr), pady=max(pt, pb))
        self._gtk.get_style_context().add_class(cls)
        self._style_classes.append(cls)

    def _restyle(self):
        super(Button, self)._restyle()
        bg = B.color(self._cfg.get("background", self._cfg.get("bg")), None)
        ab = B.color(self._cfg.get("activebackground"), bg)
        if bg:
            extra = ""
            if ab:
                extra += " background-image: none;"
            cls = B.style_class(bg=bg, extra=extra)
            self._gtk.get_style_context().add_class(cls)
            self._style_classes.append(cls)

    def invoke(self):
        self._on_clicked()

    def flash(self):
        pass

    def state(self, spec=None):
        if spec is None:
            return () if self._gtk.get_sensitive() else ("disabled",)
        for s in spec:
            if s == "disabled":
                self._gtk.set_sensitive(False)
            elif s == "!disabled":
                self._gtk.set_sensitive(True)

    def instate(self, spec):
        dis = not self._gtk.get_sensitive()
        return all(dis if s == "disabled" else not dis
                   for s in spec if s in ("disabled", "!disabled"))


class Entry(BaseWidget):
    def _build(self):
        self._gtk = Gtk.Entry()
        self._var = None
        self._syncing = False
        self._gtk.connect("changed", self._on_changed)

    def _on_changed(self, *_a):
        if self._var is not None and not self._syncing:
            self._syncing = True
            try:
                self._var.set(self._gtk.get_text())
            finally:
                self._syncing = False

    def _on_opt(self, name, value):
        e = self._gtk
        if name == "textvariable":
            self._var = value

            def upd(*_a):
                if self._syncing:
                    return
                self._syncing = True
                try:
                    e.set_text("" if value.get() is None else str(value.get()))
                finally:
                    self._syncing = False

            if hasattr(value, "trace_add"):
                value.trace_add("write", upd)
            upd()
        elif name == "width":
            try:
                e.set_width_chars(max(2, int(value)))
            except Exception:
                pass
        elif name == "justify":
            m = {"left": Gtk.Justification.LEFT, "right": Gtk.Justification.RIGHT,
                 "center": Gtk.Justification.CENTER}
            e.set_alignment({"left": 0.0, "right": 1.0, "center": 0.5}.get(str(value), 0.0))
            e.set_justification(m.get(str(value), Gtk.Justification.LEFT))
        elif name == "state":
            e.set_sensitive(str(value) != "disabled")
            e.set_editable(str(value) != "disabled")
        elif name == "show":
            e.set_visibility(False)
            e.set_invisible_char(str(value)[0] if value else "*")

    def get(self):
        return self._gtk.get_text()

    def insert(self, index, text):
        pos = self._index(index)
        self._gtk.insert_text(str(text), pos)

    def delete(self, first, last=None):
        a = self._index(first)
        b = len(self._gtk.get_text()) if last in (None, "end") else self._index(last)
        self._gtk.delete_text(a, max(a, b))

    def _index(self, idx):
        s = str(idx)
        if s == "end":
            return len(self._gtk.get_text())
        try:
            return int(s)
        except Exception:
            return 0

    def icursor(self, index):
        self._gtk.set_position(self._index(index))

    def selection_range(self, a, b):
        self._gtk.select_region(self._index(a), self._index(b))

    def focus_set(self):
        self._gtk.grab_focus()


class Checkbutton(BaseWidget):
    def _build(self):
        self._gtk = Gtk.CheckButton()
        self._var = None
        self._syncing = False
        self._on_value = 1
        self._off_value = 0
        self._cmd = None
        self._gtk.connect("toggled", self._on_toggled)

    def _on_toggled(self, *_a):
        if self._syncing:
            return
        val = self._on_value if self._gtk.get_active() else self._off_value
        if self._var is not None:
            self._syncing = True
            try:
                self._var.set(val)
            finally:
                self._syncing = False
        if self._cmd:
            try:
                self._cmd()
            except Exception:
                B._log_exc("Checkbutton command")

    def _on_opt(self, name, value):
        if name == "text":
            self._gtk.set_label("" if value is None else str(value))
        elif name == "variable":
            self._var = value
            self._sync_from_var()
            if hasattr(value, "trace_add"):
                value.trace_add("write", lambda *_a: self._sync_from_var())
        elif name in ("onvalue", "on_value"):
            self._on_value = value
        elif name in ("offvalue", "off_value"):
            self._off_value = value
        elif name == "command":
            self._cmd = value
        elif name == "state":
            self._gtk.set_sensitive(str(value) != "disabled")
        elif name == "anchor":
            pass

    def _sync_from_var(self):
        if self._var is None:
            return
        self._syncing = True
        try:
            self._gtk.set_active(self._var.get() == self._on_value)
        finally:
            self._syncing = False

    def select(self):
        self._gtk.set_active(True)

    def deselect(self):
        self._gtk.set_active(False)

    def invoke(self):
        self._gtk.set_active(not self._gtk.get_active())

    def state(self, spec=None):
        if spec is None:
            return () if self._gtk.get_sensitive() else ("disabled",)
        for s in spec:
            if s == "disabled":
                self._gtk.set_sensitive(False)
            elif s == "!disabled":
                self._gtk.set_sensitive(True)

    def instate(self, spec):
        dis = not self._gtk.get_sensitive()
        return all(dis if s == "disabled" else not dis
                   for s in spec if s in ("disabled", "!disabled"))

    def _restyle(self):
        super(Checkbutton, self)._restyle()
        bg = B.color(self._cfg.get("background", self._cfg.get("bg")), None)
        if bg:
            cls = B.style_class(bg=bg, extra=" background-image: none;")
            self._gtk.get_style_context().add_class(cls)
            self._style_classes.append(cls)


class Scale(BaseWidget):
    """tk.Scale（扁平滑块）。command 收到的是**字符串**形式的数值，和 tk 一致。"""

    def _build(self):
        self._gtk = Gtk.Scale()
        self._gtk.set_draw_value(False)
        self._var = None
        self._cmd = None
        self._syncing = False
        self._gtk.connect("value-changed", self._on_changed)

    def _on_changed(self, *_a):
        if self._syncing:
            return
        v = self._gtk.get_value()
        if self._is_int:
            v = int(round(v))
        if self._var is not None:
            self._syncing = True
            try:
                self._var.set(v)
            finally:
                self._syncing = False
        if self._cmd:
            try:
                self._cmd(str(int(v)) if self._is_int else str(v))
            except Exception:
                B._log_exc("Scale command")

    def _on_opt(self, name, value):
        s = self._gtk
        if name in ("from_", "from"):
            s.set_range(float(value), s.get_adjustment().get_upper())
            self._is_int = float(value) == int(float(value))
        elif name == "to":
            s.set_range(s.get_adjustment().get_lower(), float(value))
        elif name == "resolution":
            try:
                s.set_round_digits(0)
                s.set_digits(0)
                s.set_increments(float(value), float(value) * 10)
            except Exception:
                pass
        elif name == "orient":
            s.set_orientation(Gtk.Orientation.HORIZONTAL
                              if str(value) == "horizontal" else Gtk.Orientation.VERTICAL)
        elif name == "variable":
            self._var = value
            self._sync()
            if hasattr(value, "trace_add"):
                value.trace_add("write", lambda *_a: self._sync())
        elif name == "command":
            self._cmd = value
        elif name == "showvalue":
            s.set_draw_value(bool(value))
        elif name == "length":
            try:
                s.set_size_request(int(value) if
                                   s.get_orientation() == Gtk.Orientation.HORIZONTAL else -1,
                                   -1 if s.get_orientation() == Gtk.Orientation.HORIZONTAL
                                   else int(value))
            except Exception:
                pass
        elif name in ("sliderlength", "width", "troughcolor", "sliderrelief",
                      "highlightthickness", "activebackground", "bd"):
            pass

    def _sync(self):
        if self._var is None:
            return
        self._syncing = True
        try:
            self._gtk.set_value(float(self._var.get() or 0))
        finally:
            self._syncing = False

    def get(self):
        v = self._gtk.get_value()
        return int(round(v)) if getattr(self, "_is_int", True) else v

    def set(self, value):
        self._gtk.set_value(float(value))


class Spinbox(BaseWidget):
    def _build(self):
        self._gtk = Gtk.SpinButton()
        self._gtk.set_numeric(True)
        self._var = None
        self._gtk.connect("value-changed", self._on_changed)

    def _on_changed(self, *_a):
        if self._var is not None:
            self._var.set(int(round(self._gtk.get_value())))

    def _on_opt(self, name, value):
        s = self._gtk
        if name in ("from_", "from"):
            a = s.get_adjustment()
            a.set_lower(float(value))
            s.set_adjustment(a)
        elif name == "to":
            a = s.get_adjustment()
            a.set_upper(float(value))
            s.set_adjustment(a)
        elif name == "increment":
            s.set_increments(float(value), float(value) * 10)
        elif name == "textvariable":
            self._var = value
            if hasattr(value, "trace_add"):
                value.trace_add(
                    "write", lambda *_a: s.set_value(float(value.get() or 0)))
            try:
                s.set_value(float(value.get() or 0))
            except Exception:
                pass
        elif name == "values":
            vals = list(value or [])
            if vals:
                a = s.get_adjustment()
                a.set_lower(min(float(v) for v in vals))
                a.set_upper(max(float(v) for v in vals))
                s.set_adjustment(a)
        elif name == "width":
            try:
                s.set_width_chars(max(2, int(value)))
            except Exception:
                pass
        elif name == "command":
            pass

    def get(self):
        return int(round(self._gtk.get_value()))

    def set(self, v):
        self._gtk.set_value(float(v))

    def delete(self, *a):
        pass

    def insert(self, *a):
        pass


class _Scrollable(BaseWidget):
    """带滚动条的容器（Text / Canvas 共用）：把 tk 的 yview/xview 语义映射到 Gtk 调整。"""

    def yview(self, *args):
        adj = self._vadj()
        if not args:
            return self._frac(adj)
        if args[0] == "moveto":
            adj.set_value(adj.get_lower() + float(args[1]) * self._span(adj))
        elif args[0] == "scroll":
            n = int(args[1])
            unit = args[2] if len(args) > 2 else "units"
            step = adj.get_page_size() / 10.0 if unit == "pages" else adj.get_step_increment()
            adj.set_value(adj.get_value() + n * step)

    def xview(self, *args):
        adj = self._hadj()
        if not args:
            return self._frac(adj)
        if args[0] == "moveto":
            adj.set_value(adj.get_lower() + float(args[1]) * self._span(adj))
        elif args[0] == "scroll":
            n = int(args[1])
            unit = args[2] if len(args) > 2 else "units"
            step = adj.get_page_size() / 10.0 if unit == "pages" else adj.get_step_increment()
            adj.set_value(adj.get_value() + n * step)

    def yview_scroll(self, number, what):
        adj = self._vadj()
        step = adj.get_page_size() / 10.0 if what == "pages" else adj.get_step_increment()
        adj.set_value(adj.get_value() + int(number) * step)

    def xview_scroll(self, number, what):
        adj = self._hadj()
        step = adj.get_page_size() / 10.0 if what == "pages" else adj.get_step_increment()
        adj.set_value(adj.get_value() + int(number) * step)

    def yview_moveto(self, frac):
        adj = self._vadj()
        adj.set_value(adj.get_lower() + float(frac) * self._span(adj))

    def xview_moveto(self, frac):
        adj = self._hadj()
        adj.set_value(adj.get_lower() + float(frac) * self._span(adj))

    @staticmethod
    def _span(adj):
        return max(1e-6, adj.get_upper() - adj.get_lower())

    def _frac(self, adj):
        lo = (adj.get_value() - adj.get_lower()) / self._span(adj)
        hi = lo + adj.get_page_size() / self._span(adj)
        return (lo, min(1.0, hi))

    def _vadj(self):
        return self._vadj_

    def _hadj(self):
        return self._hadj_


class Text(_Scrollable):
    def _build(self):
        self._sw = Gtk.ScrolledWindow()
        # 用 AUTOMATIC 而不是 EXTERNAL：EXTERNAL 表示"滚动条由外部提供、本控件不滚动"，
        # 于是它的**最小高度 = 全文高度**，长文本会把窗口顶到几千像素高（实测 2624px）。
        # AUTOMATIC 才允许它在 min/max_content_height 之间收缩；内部滚动条自己隐掉，
        # 交给 uistyle.text_box 里那个外部 ttk.Scrollbar 显示。
        self._sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self._view = Gtk.TextView()
        self._view.set_wrap_mode(Gtk.WrapMode.CHAR)
        self._view.set_editable(False)
        self._view.set_cursor_visible(False)
        self._sw.add(self._view)
        try:
            vsb = self._sw.get_vscrollbar()
            vsb.set_no_show_all(True)
            vsb.set_visible(False)
        except Exception:
            pass
        self._gtk = self._sw
        self._req_w = -1
        self._req_h = -1
        self._vadj_ = self._sw.get_vadjustment()
        self._hadj_ = self._sw.get_hadjustment()
        self._ycmd = None
        self._vadj_.connect("value-changed", self._on_vchange)

    def _apply_req(self):
        self._sw.set_size_request(self._req_w, self._req_h)

    def _on_vchange(self, *_a):
        if self._ycmd:
            lo, hi = self._frac(self._vadj_)
            try:
                self._ycmd(str(lo), str(hi))
            except Exception:
                B._log_exc("yscrollcommand")

    def _on_opt(self, name, value):
        if name == "height":
            px = max(16, int(value) * 16)
            self._req_h = px
            self._apply_req()
            # AUTOMATIC 策略下，min/max_content_height 决定"内容区多高开始出滚动条"；
            # 不设的话长文本会把窗口顶到几千像素高（实测 2624px）。
            for setter, arg in (("set_min_content_height", px),
                                ("set_max_content_height", px),
                                ("set_propagate_natural_height", True),
                                ("set_propagate_natural_width", False)):
                try:
                    getattr(self._sw, setter)(arg)
                except Exception:
                    pass
        elif name == "width":
            # tk 的 width 是**字符数**；按 8px/字符折成像素（等宽字体下足够准）
            self._req_w = max(8, int(value) * 8)
            self._apply_req()
        elif name == "wrap":
            m = {"char": Gtk.WrapMode.CHAR, "word": Gtk.WrapMode.WORD,
                 "none": Gtk.WrapMode.NONE}
            self._view.set_wrap_mode(m.get(str(value), Gtk.WrapMode.CHAR))
        elif name == "yscrollcommand":
            self._ycmd = value
        elif name == "state":
            self._view.set_editable(str(value) != "disabled")
        elif name in ("padx", "pady", "bd", "relief", "cursor"):
            pass
        elif name in ("background", "bg", "foreground", "fg", "font"):
            pass

    def _restyle(self):
        super(Text, self)._restyle()
        bg = B.color(self._cfg.get("background", self._cfg.get("bg")), None)
        fg = B.color(self._cfg.get("foreground", self._cfg.get("fg")), None)
        font = self._cfg.get("font")
        cls = B.style_class(bg=bg, fg=fg, font=font)
        if cls:
            self._view.get_style_context().add_class(cls)
            self._style_classes.append(cls)

    def insert(self, index, chars):
        buf = self._view.get_buffer()
        it = buf.get_iter_at_offset(self._offset(index))
        buf.insert(it, str(chars))

    def delete(self, first, last=None):
        buf = self._view.get_buffer()
        a = self._offset(first)
        b = buf.get_char_count() if last in (None, "end") else self._offset(last)
        si = buf.get_iter_at_offset(a)
        ei = buf.get_iter_at_offset(max(a, b))
        buf.delete(si, ei)

    def _offset(self, index):
        s = str(index)
        if s == "end" or s.endswith(".end"):
            return self._view.get_buffer().get_char_count()
        if "." in s:
            line, _, col = s.partition(".")
            try:
                return int(line) * 0  # 本工程只用 "1.0" / "end"
            except Exception:
                return 0
        try:
            return int(s)
        except Exception:
            return 0

    def get(self, first, last=None):
        buf = self._view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)


class Menu(BaseWidget):
    """tk.Menu：既可能是菜单栏（挂在 root 上），也可能是下拉菜单。"""

    def _build(self):
        self._gtk = Gtk.Menu()
        self._items = []
        self._cascades = {}          # label -> 子 Menu（板端工作站靠它往「工具」里挂新项）

    def find_cascade(self, label):
        return self._cascades.get(str(label))

    def _on_opt(self, name, value):
        if name == "tearoff":
            pass

    def add_command(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        mi = Gtk.MenuItem.new_with_label(str(kw.get("label", "")))
        cmd = kw.get("command")
        if cmd is not None:
            mi.connect("activate", lambda *_a: self._run(cmd))
        if kw.get("state") == "disabled":
            mi.set_sensitive(False)
        self._bind_accel(kw.get("accelerator"), cmd)
        self._items.append(mi)
        self._gtk.append(mi)
        mi.show()
        return mi

    def add_cascade(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        mi = Gtk.MenuItem.new_with_label(str(kw.get("label", "")))
        sub = kw.get("menu")
        if sub is not None:
            mi.set_submenu(sub._gtk)
            self._cascades[str(kw.get("label", ""))] = sub
        self._items.append(mi)
        self._gtk.append(mi)
        mi.show()
        return mi

    def add_separator(self, cnf=None, **kw):
        si = Gtk.SeparatorMenuItem()
        self._items.append(si)
        self._gtk.append(si)
        si.show()
        return si

    def add_checkbutton(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        mi = Gtk.CheckMenuItem.new_with_label(str(kw.get("label", "")))
        var = kw.get("variable")
        cmd = kw.get("command")
        self._bind_accel(kw.get("accelerator"), cmd)
        if var is not None:
            try:
                mi.set_active(bool(var.get()))
            except Exception:
                pass

            def toggled(w):
                var.set(1 if w.get_active() else 0)
                if cmd is not None:
                    self._run(cmd)

            mi.connect("toggled", toggled)
        elif cmd is not None:
            mi.connect("toggled", lambda *_a: self._run(cmd))
        self._items.append(mi)
        self._gtk.append(mi)
        mi.show()
        return mi

    def add_radiobutton(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        mi = self._add_radio_impl(kw)
        return mi

    def _add_radio_impl(self, kw):
        mi = Gtk.RadioMenuItem.new_with_label(None, str(kw.get("label", "")))
        var = kw.get("variable")
        value = kw.get("value")
        cmd = kw.get("command")
        if var is not None:
            try:
                mi.set_active(str(var.get()) == str(value))
            except Exception:
                pass

            def toggled(w):
                if w.get_active():
                    var.set(value)
                    if cmd is not None:
                        self._run(cmd)

            mi.connect("toggled", toggled)
        self._bind_accel(kw.get("accelerator"), cmd)
        self._items.append(mi)
        self._gtk.append(mi)
        mi.show()
        return mi

    def entryconfigure(self, index, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        try:
            it = self._items[int(index)]
        except Exception:
            return None
        if "label" in kw:
            it.set_label(str(kw["label"]))
        if "state" in kw:
            it.set_sensitive(str(kw["state"]) != "disabled")
        return None

    entryconfig = entryconfigure

    def delete(self, index, last=None):
        try:
            it = self._items.pop(int(index))
            self._gtk.remove(it)
        except Exception:
            pass

    @staticmethod
    def _run(cmd):
        try:
            cmd()
        except Exception:
            B._log_exc("Menu command")

    # --- 加速键（tk 的 accelerator="Ctrl+O" / "F5"）
    def _bind_accel(self, accelerator, command):
        """tk 的 accelerator 只是"显示用"，真正生效要靠 accel group；
        板上没有 Tk，所以这里直接把它绑成顶层窗口的按键事件 —— 效果一样。"""
        if not accelerator or command is None:
            return
        try:
            top = self._toplevel()
            if top is None:
                return
            seq = "<%s>" % str(accelerator).replace("Ctrl+", "Control-").replace(
                "Ctrl-", "Control-")
            top.bind(seq, lambda e: (self._run(command), "break")[1])
        except Exception:
            B._log_exc("accelerator " + str(accelerator))


class PhotoImage(BaseWidget):
    """tk.PhotoImage 的替身，用 **Pillow** 解码。

    上位机是这么造图的（`imageio.make_photo`）：
        tk.PhotoImage(data=base64.b64encode(PPM字节))
    真 Tk 认 base64 的 PPM/GIF/PNG；板上没有 Tk，但**有 Pillow**，
    所以这里统一 base64 → 字节 → PIL 解码 → GdkPixbuf。
    另外 `file=` 也走 PIL（GdkPixbuf 读不了 PPM）。
    """

    def _build(self):
        self._gtk = Gtk.Image()
        self._pil = None
        self._pixbuf = None

    def __init__(self, master=None, cnf=None, **kw):
        self._w0 = int(kw.get("width", 1) or 1)
        self._h0 = int(kw.get("height", 1) or 1)
        BaseWidget.__init__(self, master, cnf, **kw)

    def _blank(self, w, h):
        from PIL import Image as _Im
        self._set_pil(_Im.new("RGB", (max(1, w), max(1, h)), (229, 232, 237)))

    def _set_pil(self, im):
        self._pil = im
        try:
            self._pixbuf = _pil_to_pixbuf(im)
            self._gtk.set_from_pixbuf(self._pixbuf)
        except Exception:
            B._log_exc("PhotoImage set")

    def _on_opt(self, name, value):
        if name == "width":
            self._w0 = int(value or 1)
            if self._pil is None:
                self._blank(self._w0, self._h0)
        elif name == "height":
            self._h0 = int(value or 1)
            if self._pil is None:
                self._blank(self._w0, self._h0)
        elif name == "data":
            self._load_data(value)
        elif name == "file":
            self._load_file(value)

    def _load_data(self, value):
        import base64
        import io
        try:
            raw = value
            if isinstance(raw, str):
                s = raw.strip()
                if s[:2] in ("P6", "P5", "P3", "P2", "P4", "P1"):
                    raw = s.encode("latin-1")
                else:
                    raw = base64.b64decode(s)
            elif isinstance(raw, (bytes, bytearray)):
                head = bytes(raw[:2])
                if head not in (b"P6", b"P5", b"P3", b"P2", b"P4", b"P1"):
                    raw = base64.b64decode(bytes(raw))
            from PIL import Image as _Im
            im = _Im.open(io.BytesIO(bytes(raw)))
            im.load()
            self._set_pil(im)
        except Exception:
            B.warn("PhotoImage(data=...) 解码失败（%d 字节）" % (len(value) if value else 0))

    def _load_file(self, path):
        try:
            from PIL import Image as _Im
            im = _Im.open(str(path))
            im.load()
            self._set_pil(im)
        except Exception:
            B.warn("PhotoImage 读不了 %s" % path)

    def put(self, data, to=None):
        """把 "#rrggbb" 网格填进去（本工程只在占位时用到）。"""
        if self._pil is None:
            self._blank(self._w0, self._h0)
        colors = []
        if isinstance(data, str):
            for row in data.strip().strip("{}").split("}"):
                for tok in row.split():
                    colors.append(B.color(tok, "#000000"))
        elif isinstance(data, (list, tuple)):
            colors = [B.color(c, "#000000") for c in data]
        if not colors:
            return
        from PIL import ImageDraw as _ID
        d = _ID.Draw(self._pil)
        x0, y0 = (to or (0, 0))
        w, h = self._pil.size
        i, y = 0, int(y0)
        while i < len(colors) and y < h:
            x = int(x0)
            while i < len(colors) and x < w:
                d.point((x, y), fill=_hex_to_rgb(colors[i]))
                i += 1
                x += 1
            y += 1
        self._set_pil(self._pil)

    def width(self):
        return self._pil.size[0] if self._pil else self._w0

    def height(self):
        return self._pil.size[1] if self._pil else self._h0

    def zoom(self, x, y=None):
        if self._pil is not None:
            from PIL import Image as _Im
            self._set_pil(self._pil.resize(
                (self._pil.size[0] * int(x), self._pil.size[1] * int(y or x)),
                _Im.NEAREST))
        return self

    def subsample(self, x, y=None):
        if self._pil is not None:
            from PIL import Image as _Im
            self._set_pil(self._pil.resize(
                (max(1, self._pil.size[0] // int(x)),
                 max(1, self._pil.size[1] // int(y or x))), _Im.NEAREST))
        return self

    def _bind_target(self):
        return self._gtk


def _hex_to_rgb(h):
    try:
        return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))
    except Exception:
        return (0, 0, 0)


def _pil_to_pixbuf(im):
    """PIL.Image → GdkPixbuf（统一 RGB/RGBA 8bit）。"""
    gi.require_version("GdkPixbuf", "2.0")
    mode = im.mode
    if mode == "P":
        im = im.convert("RGBA")
        mode = "RGBA"
    elif mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
        mode = "RGB"
    w, h = im.size
    nch = 4 if mode == "RGBA" else 3
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(im.tobytes()), GdkPixbuf.Colorspace.RGB,
        mode == "RGBA", 8, w, h, w * nch).copy()  # .copy()：板上老 PyGObject 里 new_from_bytes 的像素图交给 Gtk.Image 显示后整帧泄漏（10-31 实测），拷一份就不漏
