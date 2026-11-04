# -*- coding: utf-8 -*-
"""uistyle.py —— 主界面和各子窗口共用的界面风格：配色、字体、卡片 / 顶栏 / 状态栏 / 指标卡。

风格：浅灰底 + 白卡片，一种强调色（蓝）；医学图像本身的深色底保持不变。
只注册带前缀的 ttk 样式（Card.* / Detail.*），不改默认样式。
"""

import tkinter as tk
from tkinter import ttk

FONT = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_HEAD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 13, "bold")
FONT_BIG = ("Microsoft YaHei UI", 18, "bold")
FONT_MONO = ("Consolas", 10)

C_BG = "#eef1f5"
C_CARD = "#ffffff"
C_BAR = "#e3e7ee"
C_BORDER = "#d5dbe4"
C_EMPTY = "#e5e8ed"         # 还没有图时的画布底色
C_TEXT = "#1f2933"
C_MUTED = "#6b7280"
C_ACCENT = "#2563eb"
C_ACCENT_D = "#1d4ed8"
C_STOP = "#dc2626"
C_WARN = "#b91c1c"
C_GREEN = "#059669"
C_RED = "#c2410c"
C_PURPLE = "#7c3aed"
C_IDLE = "#9ca3af"

DISCLAIMER = "仅用于工程教学与算法演示，不作为临床诊断依据"


def setup_style(root):
    st = ttk.Style(root)
    for w in ("TFrame", "TLabel", "TCheckbutton"):
        st.configure("Card." + w, background=C_CARD, foreground=C_TEXT, font=FONT)
    st.configure("Detail.TNotebook", background=C_BG, borderwidth=0)
    st.configure("Detail.TNotebook.Tab", font=FONT_SMALL, padding=(14, 3))


def card(parent):
    return tk.Frame(parent, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)


def label(parent, text=None, var=None, font=FONT, fg=C_TEXT, wrap=False, **kw):
    """白底标签。wrap=True：按实际分到的宽度折行（要配合 fill="x" / sticky="ew"）。"""
    if var is not None:
        kw["textvariable"] = var
    else:
        kw["text"] = text
    if wrap:
        kw.setdefault("justify", "left")
        kw.setdefault("anchor", "w")
        kw["wraplength"] = 120       # 先给小值，免得长文字把所在栏撑宽
    lb = tk.Label(parent, font=font, fg=fg, bg=kw.pop("bg", C_CARD), **kw)
    if wrap:
        lb.bind("<Configure>", lambda e: lb.configure(wraplength=max(60, e.width - 4)))
    return lb


def text_box(parent, var, height=10, font=FONT, fg=C_TEXT):
    """只读、自动折行、可滚动的多行文字，内容跟着 StringVar 走（长说明放标签里会把窗口撑出屏幕）。
    返回外框（里面是 Text + 竖滚动条）。滚动条始终占位（11-03）：原来放得下时藏起来，但竖滚动条的最小高度
    比 2 行的框高，显隐一次框就变高 / 变矮一截，内容卡在放得下的边界上时整个界面跟着上下跳。"""
    box = tk.Frame(parent, bg=C_CARD)
    txt = tk.Text(box, height=height, width=1, wrap="char", font=font, fg=fg, bg=C_CARD, bd=0,
                  highlightthickness=0, relief="flat", cursor="arrow", padx=0, pady=0)
    sb = ttk.Scrollbar(box, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    txt.pack(side="left", fill="both", expand=True)

    def refresh(*_):
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        txt.insert("1.0", var.get())
        txt.configure(state="disabled")
    var.trace_add("write", refresh)
    refresh()
    box.text = txt
    return box


class PrimaryButton(tk.Button):
    """强调色的大按钮（顶栏里的「发送 / 开始」）。额外支持 ttk 风格的 .state([...])，
    原来按 ttk.Button 写的 btn.state(["disabled"]) / (["!disabled"]) 不用改。"""

    def state(self, spec=None):
        if spec is None:
            return ("disabled",) if str(self.cget("state")) == "disabled" else ()
        for s in spec:
            if s == "disabled":
                self.configure(state="disabled", bg=C_IDLE)
            elif s == "!disabled":
                self.configure(state="normal", bg=C_ACCENT)

    def instate(self, spec):
        disabled = str(self.cget("state")) == "disabled"
        return all(disabled if s == "disabled" else not disabled
                   for s in spec if s in ("disabled", "!disabled"))


def primary_button(parent, text, command, **kw):
    return PrimaryButton(parent, text=text, font=FONT_HEAD, fg="white", bg=C_ACCENT,
                         activebackground=C_ACCENT_D, activeforeground="white",
                         disabledforeground="#e5e7eb", relief="flat", bd=0,
                         padx=kw.pop("padx", 20), pady=kw.pop("pady", 5), cursor="hand2",
                         command=command, **kw)


def image_slot(parent, w, h, bg="#111111", **kw):
    """固定 w×h 像素的图像位：tk.Label 没有图时 width/height 按字符算，会把窗口撑得极大，
    所以放进一个不随内容伸缩的框里。返回里面的 Label（.master 是外框）。"""
    box = tk.Frame(parent, width=w, height=h, bg=bg)
    box.pack_propagate(False)
    lb = tk.Label(box, bg=bg, bd=0, **kw)
    lb.pack(fill="both", expand=True)
    return lb


def window(top, title):
    """子窗口的底色 + 样式；返回 top 本身。"""
    top.configure(bg=C_BG)
    setup_style(top)
    if title:
        top.title(title)
    return top


def close_when_idle(top, busy):
    """关子窗口：板上任务还在途时先把窗口藏起来，等它回来（窗口自己的轮询把 busy 清掉）再销毁。
    直接销毁会连带停掉轮询，主界面的 busy 互斥就永远不释放，之后所有窗口都"板子正忙"。"""
    top._closing = True
    if busy():
        top.withdraw()
        top.after(150, lambda: close_when_idle(top, busy))
        return
    top.destroy()


def alive(win):
    """子窗口对象还能复用（没关、也没在关）。"""
    return win is not None and win.top.winfo_exists() and not getattr(win.top, "_closing", False)


def bring_up(top):
    """把已开的子窗口拿到前面（最小化了也还原）。"""
    top.deiconify()
    top.lift()
    top.focus_set()


def fmt_mag(m):
    """倍数的显示：1.1666… -> 1.17，2.0 -> 2。"""
    return ("%.2f" % m).rstrip("0").rstrip(".")


def topbar(parent, title, subtitle=None, columnspan=1):
    """顶栏：左边标题（+ 灰色副标题），返回放按钮的右侧容器（从左往右 pack）。"""
    bar = tk.Frame(parent, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
    bar.grid(row=0, column=0, columnspan=columnspan, sticky="ew")
    label(bar, title, font=FONT_TITLE).pack(side="left", padx=(16, 0), pady=10)
    if subtitle:
        label(bar, subtitle, font=FONT_SMALL, fg=C_MUTED).pack(side="left", padx=(10, 0), pady=(14, 8))
    tools = tk.Frame(bar, bg=C_CARD)
    tools.pack(side="right", padx=(0, 16))
    return tools


def panel(parent, title, extra=None):
    """带标题的卡片：返回 (卡片, 内容区)。extra 是标题右侧的灰色说明；
    卡片的 .head 是标题行，标题行右侧的按钮往里 pack(side="right")。title 可以是 StringVar（标题会变）。"""
    c = card(parent)
    head = tk.Frame(c, bg=C_CARD)
    head.pack(fill="x", padx=12, pady=(8, 6))
    if isinstance(title, tk.Variable):
        label(head, var=title, font=FONT_HEAD).pack(side="left")
    else:
        label(head, title, font=FONT_HEAD).pack(side="left")
    if extra:
        label(head, extra, font=FONT_SMALL, fg=C_MUTED).pack(side="left", padx=(10, 0))
    body = tk.Frame(c, bg=C_CARD)
    body.pack(fill="both", expand=True, padx=12, pady=(0, 10))
    c.head = head
    return c, body


def scale(parent, **kw):
    """扁平的 tk.Scale（蓝色滑块 + 浅灰轨道），用法同 tk.Scale。"""
    return tk.Scale(parent, orient="horizontal", showvalue=0, bg=C_ACCENT, troughcolor=C_EMPTY,
                    activebackground=C_ACCENT_D, highlightthickness=0, bd=0, sliderrelief="flat",
                    sliderlength=18, width=10, **kw)


def vsep(parent):
    """顶栏里按钮分组之间的竖分隔线。"""
    tk.Frame(parent, bg=C_BORDER, width=1, height=22).pack(side="left", padx=12)


def metric(parent, title, var, note=None, color=C_ACCENT, big=FONT_BIG):
    """指标卡：灰色小标题 + 大号数值 + 灰色口径说明。"""
    c = card(parent)
    label(c, title, font=FONT_SMALL, fg=C_MUTED).pack(anchor="w", padx=14, pady=(6, 0))
    value = label(c, var=var, font=big, fg=color)
    value.pack(anchor="w", padx=14)
    if note is not None:
        label(c, var=note, font=FONT_SMALL, fg=C_MUTED, wrap=True).pack(fill="x", padx=14, pady=(0, 6))
    else:
        tk.Frame(c, bg=C_CARD, height=6).pack()
    c.value = value
    return c


def statusbar(parent, row, var=None, note=DISCLAIMER, columnspan=1):
    """底部状态栏：左边状态文字，右边常驻使用声明（赛题基础要求⑥）。"""
    sbar = tk.Frame(parent, bg=C_BAR)
    sbar.grid(row=row, column=0, columnspan=columnspan, sticky="ew")
    tk.Label(sbar, text=note, font=FONT_SMALL, fg=C_WARN, bg=C_BAR, padx=12, pady=4).pack(side="right")
    if var is not None:
        # width=1：状态文字再长也不撑宽窗口，放不下的部分被截掉（声明先 pack，永远完整显示）
        tk.Label(sbar, textvariable=var, font=FONT_SMALL, anchor="w", fg=C_TEXT, width=1,
                 bg=C_BAR, padx=12, pady=4).pack(side="left", fill="x", expand=True)
    return sbar
