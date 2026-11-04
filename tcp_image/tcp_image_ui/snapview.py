# -*- coding: utf-8 -*-
"""snapview.py —— 心超工作站「快照模式」（设计报告 7.3 / 图 5、图 6）：sequence.SeqWindow 嵌入 + 第三栏「双平面测量」卡片。

为什么单独一个模式：现场 / 关键帧模式只有一个 A4C 切面、没有毫米尺度，只能给比值（EF、RV/LV …）；
快照模式拿同一病人的 A4C + A2C 两段（带像素间距），用指南推荐的双平面 Simpson 出绝对容积（mL）、左房容积、
左室质量、壁厚。分割在 FPGA 上（2 号自动分割、4 号序列传播、3 号点击纠错、17 / 18 号左房 / 心外膜），
Simpson 与左房 / 心肌测量在板上算（simpson_ef 任务），与离线评测逐位一致。

卡片：选病例（CAMUS patient0027 / 0213 / 0231，四腔 + 二腔）→「一键双平面测量」= 四腔逐帧上板跑完 → 记入 →
二腔跑完 → 记入 → 板上 Simpson + 左房 / 心肌；结果用大字号分块显示，2×2 圆盘图直接放在卡片里（点开看大图）。
手动路径照旧：播放 / 单步、暂停时点击纠错、「记入当前视图」、「算 EF」（纠错后的轮廓也会被记入）。
仅用于工程教学与算法演示，不作为临床诊断依据；画面来自 CAMUS（CC BY-NC-SA 4.0）。
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import ttk

import sequence
import uistyle
from uistyle import FONT, FONT_HEAD, FONT_SMALL

HERE = os.path.dirname(os.path.abspath(__file__))
PLD_DIR = os.path.normpath(os.path.join(HERE, "..", ".."))
CASE_DIRS = (os.path.join(PLD_DIR, "echo", "samples"), os.path.join(HERE, "demo"))   # 工程目录 / 板上 DP 的 demo/
CASES = (("patient0027", "图像质量 Good"), ("patient0213", "四腔 Medium / 二腔 Good"), ("patient0231", "图像质量 Poor"))
CASE_SEX = {"patient0027": "M", "patient0213": "M", "patient0231": "M"}   # CAMUS Info_4CH.cfg 的 Sex；身高体重 CAMUS 没有
CW = 392                                          # 卡片内容宽
FIG = 228                                         # 卡片里圆盘图的边长（点开看 512 大图）；卡片不能比左边两栏高
FONT_EF = ("Microsoft YaHei UI", 28, "bold")
FONT_NUM = ("Microsoft YaHei UI", 14, "bold")
BAND_COL = ("#c2410c", "#b45309", "#047857")
VIEWS = (("4CH", "四腔 A4C"), ("2CH", "二腔 A2C"))
N112, UP = 112, 4                                 # 关键帧 / 现场模式的掩膜是 112；单平面 Simpson 前在板上放大 4 倍
LEGEND2 = "上 四腔　下 二腔\n左 ED　右 ES\n\n绿 轮廓\n红 长轴\n蓝 二尖瓣平面\n黄 20 个圆盘\n\n点图看大图"
LEGEND1 = "四腔 A4C\n左 ED　右 ES\n\n绿 轮廓\n红 长轴\n蓝 二尖瓣平面\n黄 20 个圆盘\n\n点图看大图"


def case_dir(patient, view):
    for d in CASE_DIRS:
        p = os.path.join(d, "seq_%s_%s" % (patient, view))
        if os.path.isdir(p):
            return p
    return None


def available_cases():
    return [(p, note) for p, note in CASES if case_dir(p, "4CH") and case_dir(p, "2CH")]


class SnapView(sequence.SeqWindow):
    """嵌入用（parent 必给）。app = heartapp.HeartApp。"""

    def __init__(self, app, parent):
        self.cases = available_cases()
        self.mq = queue.Queue()
        self.auto = None                              # 一键测量的进度：None / ("4CH" | "2CH", 阶段)
        self.mono = None                              # 关键帧 / 现场模式送来的一拍（accept_mono）
        self.cardiac = None
        self.photos_m = {}
        sequence.SeqWindow.__init__(self, app, parent)
        self.top.after(60, self._mpoll)

    # ---------------------------------------------------------------- 界面
    def _build(self):
        sequence.SeqWindow._build(self)
        self.ef_panel.grid_forget()                   # 原来的「射血分数（F4）」一行换成右边的测量卡片
        for w in self.mid.grid_slaves(row=1, column=0):     # 进度 / 帧信息卡片贴着上面两栏（右边卡片更高时不留空）
            w.grid_configure(sticky="new")
        hp, hb = uistyle.panel(self.mid, "提示方向", "测量值可能与哪类问题有关；辅助复核，不作诊断（判据：ESC 2021、ASE 2015）")
        hp.grid(row=2, column=0, columnspan=2, sticky="new", pady=(12, 0))   # 左边进度卡片下面原来空着
        self.var_hint = tk.StringVar(value="测量后在这里列出：EF 分档、左室是否扩大、左房是否扩大、左室是否肥厚，以及它们的组合提示")
        uistyle.text_box(hb, self.var_hint, height=6, font=FONT_SMALL, fg="#9a3412").pack(fill="x")
        card, b = uistyle.panel(self.mid, "双平面测量", "Simpson + 左房 / 心肌，板上算")
        card.grid(row=0, column=2, rowspan=3, sticky="ns", padx=(12, 0))
        self.mid.grid_rowconfigure(2, weight=1)       # 卡片比左边两行高出来的部分给空的第 3 行，前两行不被撑开
        row = tk.Frame(b, bg=uistyle.C_CARD)
        row.pack(fill="x")
        uistyle.label(row, "病例", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 6))
        labels = ["%s（%s）" % c for c in self.cases] or ["（没有找到 CAMUS 示例病例）"]
        self.var_case = tk.StringVar(value=labels[0])
        ttk.Combobox(row, textvariable=self.var_case, values=labels, width=30, state="readonly", font=FONT) \
            .pack(side="left", fill="x", expand=True)
        prow = tk.Frame(b, bg=uistyle.C_CARD)
        prow.pack(fill="x", pady=(8, 0))
        if not self.var_height.get():                 # 性别按 CAMUS；身高体重是示例值（CAMUS 没有），填了才算 LAVI / LVMI
            self.var_sex.set(CASE_SEX.get(self.cases[0][0], "—") if self.cases else "—")
            self.var_height.set("170")
            self.var_weight.set("70")
        uistyle.label(prow, "性别", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 4))
        ttk.Combobox(prow, textvariable=self.var_sex, values=("—", "F", "M"), width=3, state="readonly",
                     font=FONT).pack(side="left")
        for name, var, unit in (("身高", self.var_height, "cm"), ("体重", self.var_weight, "kg")):
            uistyle.label(prow, name, fg=uistyle.C_MUTED).pack(side="left", padx=(10, 4))
            ttk.Entry(prow, textvariable=var, width=4, font=FONT).pack(side="left")
            uistyle.label(prow, unit, fg=uistyle.C_MUTED).pack(side="left", padx=(2, 0))
        uistyle.label(prow, "示例", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(side="left", padx=(8, 0))
        self.btn_auto = uistyle.primary_button(b, "▶ 一键双平面测量", self.measure_case)
        self.btn_auto.pack(fill="x", pady=(8, 0))
        mrow = tk.Frame(b, bg=uistyle.C_CARD)
        mrow.pack(fill="x", pady=(6, 0))
        uistyle.label(mrow, "手动：", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(side="left")
        ttk.Button(mrow, text="记入当前视图", command=self._store_current).pack(side="left")
        ttk.Button(mrow, text="算 EF", command=self.compute).pack(side="left", padx=(6, 0))
        ttk.Button(mrow, text="清空", command=self._clear_meas).pack(side="left", padx=(6, 0))
        self.var_views = {}
        vbox = tk.Frame(b, bg=uistyle.C_CARD)
        vbox.pack(fill="x", pady=(8, 0))
        for v, name in VIEWS:
            r = tk.Frame(vbox, bg=uistyle.C_CARD)
            r.pack(fill="x")
            uistyle.label(r, name, font=FONT_HEAD, width=9, anchor="w").pack(side="left")
            var = tk.StringVar(value="未记入")
            uistyle.label(r, var=var, fg=uistyle.C_MUTED, anchor="w").pack(side="left")
            self.var_views[v] = var
        tk.Frame(b, bg=uistyle.C_BORDER, height=1).pack(fill="x", pady=8)
        top = tk.Frame(b, bg=uistyle.C_CARD)
        top.pack(fill="x")
        efc = tk.Frame(top, bg=uistyle.C_CARD)
        efc.pack(side="left")
        self.var_eft = tk.StringVar(value="EF（双平面 Simpson）")
        uistyle.label(efc, var=self.var_eft, font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
        self.var_mef = tk.StringVar(value="—")
        self.lab_mef = uistyle.label(efc, var=self.var_mef, font=FONT_EF)
        self.lab_mef.pack(anchor="w")
        vc = tk.Frame(top, bg=uistyle.C_CARD)
        vc.pack(side="left", padx=(18, 0), anchor="n")
        self.var_edv, self.var_esv, self.var_ref = tk.StringVar(value="EDV —"), tk.StringVar(value="ESV —"), \
            tk.StringVar(value="")
        for var in (self.var_edv, self.var_esv):
            uistyle.label(vc, var=var, font=FONT_NUM).pack(anchor="w")
        uistyle.label(vc, var=self.var_ref, font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
        grid = tk.Frame(b, bg=uistyle.C_CARD)
        grid.pack(fill="x", pady=(8, 0))
        self.var_m, self.var_mt = {}, {}
        for i, (k, title) in enumerate((("la", "左房容积 / LAVI"), ("lvm", "左室质量 / LVMI"), ("wall", "壁厚 / RWT"),
                                        ("where", "计算"))):
            c = tk.Frame(grid, bg=uistyle.C_CARD)
            c.grid(row=i // 2, column=i % 2, sticky="nw", padx=(0, 16), pady=(0, 6))
            tvar = tk.StringVar(value=title)
            self.var_mt[k] = (tvar, title)
            uistyle.label(c, var=tvar, font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
            var = tk.StringVar(value="—")
            lab = uistyle.label(c, var=var, font=FONT_HEAD, anchor="w", justify="left")
            lab.pack(anchor="w")
            self.var_m[k] = (var, lab)
        frow = tk.Frame(b, bg=uistyle.C_CARD)
        frow.pack(fill="x", pady=(2, 0))
        self.lab_fig = tk.Label(frow, bg="#111", bd=0, cursor="hand2", width=1)
        self.lab_fig.pack(side="left")
        self.lab_fig.bind("<Button-1>", lambda e: self._big_figure())
        self.var_legend = tk.StringVar(value=LEGEND2)
        uistyle.label(frow, var=self.var_legend, font=FONT_SMALL, fg=uistyle.C_MUTED, justify="left",
                      anchor="nw").pack(side="left", padx=(10, 0), anchor="n")
        self.var_note = tk.StringVar(value="选病例，点「一键双平面测量」")
        uistyle.label(b, var=self.var_note, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x", pady=(4, 0))
        self._blank_fig()

    def _blank_fig(self):
        from PIL import Image, ImageTk
        self.fig_full, self.fig_size = None, (512, 512)
        self.photos_m["fig"] = ImageTk.PhotoImage(Image.new("RGB", (FIG, FIG), (24, 24, 24)))
        self.lab_fig.configure(image=self.photos_m["fig"], width=FIG, height=FIG)

    # ---------------------------------------------------------------- 一键测量
    def _patient_id(self):
        i = next((k for k, c in enumerate(self.cases) if self.var_case.get().startswith(c[0])), None)
        return None if i is None else self.cases[i][0]

    def measure_case(self):
        p = self._patient_id()
        if p is None:
            self.var_note.set("没有找到 CAMUS 示例病例（echo/samples 或 demo/ 里的 seq_<病人>_4CH / _2CH）")
            return
        if self.busy or self.app.busy or self.auto is not None:
            self.var_note.set("板子正忙，稍等")
            return
        self.ef_slots, self.ef_result, self.mono = {}, None, None
        if p in CASE_SEX:
            self.var_sex.set(CASE_SEX[p])
        self._show_slots()
        self._clear_result()
        self.btn_auto.state(["disabled"])
        self._auto_view(p, "4CH")

    def _auto_view(self, p, view):
        if not self.load(case_dir(p, view)):
            self._auto_done("载入 %s %s 失败" % (p, view))
            return
        self.auto = (p, view)
        self.var_note.set("%s：%s 逐帧上板（第 1 帧自动分割，之后每帧由上一帧结果传播）…" % (p, dict(VIEWS)[view]))
        self.app.status(self.var_note.get())
        self._toggle_play()
        self.top.after(100, self._auto_wait)

    def _auto_wait(self):
        if self.auto is None:
            return
        p, view = self.auto
        tr = self.tracker
        done = tr is not None and len(tr.steps) >= len(self.frames)
        if not done or self.stream_stop is not None or self.busy:
            if not self.playing and self.stream_stop is None and not self.busy and not done:
                self._auto_done("%s 没放完（通信失败？看左下状态）" % view)
                return
            self.top.after(100, self._auto_wait)
            return
        if not self._ef_store(view):
            self._auto_done("%s 记入失败：%s" % (view, self.var_status.get()))
            return
        self._show_slots()
        if view == "4CH":
            self._auto_view(p, "2CH")
        else:
            self.auto = None
            self.compute()

    def _auto_done(self, msg):
        self.auto = None
        self.btn_auto.state(["!disabled"])
        self.var_note.set(msg)

    def _store_current(self):
        if self._ef_store():
            self._show_slots()

    def _show_slots(self):
        for v, _ in VIEWS:
            sl = self.ef_slots.get(v)
            self.var_views[v].set("未记入" if sl is None else "%s  ED 第 %d 帧 / ES 第 %d 帧 ✓" % (
                sl["patient"], sl["ed_frame"] + 1, sl["es_frame"] + 1))

    def _clear_meas(self):
        self.mono = None
        self._ef_clear()
        self._show_slots()
        self._clear_result()

    # ---------------------------------------------------------------- 板上 Simpson + 左房 / 心肌（后台线程）
    def compute(self):
        a2c, a4c = self.ef_slots.get("2CH"), self.ef_slots.get("4CH")
        if a2c is None or a4c is None:
            self.var_note.set("四腔、二腔都记入后才能算")
            self.btn_auto.state(["!disabled"])
            return
        if self.busy or self.app.busy:
            self.var_note.set("板子正忙，稍等")
            return
        self.busy = self.app.busy = True
        self.btn_auto.state(["disabled"])
        self.var_note.set("板上计算双平面 Simpson、左房与心肌 …")
        pt = self._patient()
        try:
            hp = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except ValueError:
            hp = self.app.link.host, self.app.link.port
        threading.Thread(target=self._compute_worker, args=(a2c, a4c, pt, hp), daemon=True).start()

    def _compute_worker(self, a2c, a4c, pt, hp):
        import time
        link = self.app.link
        try:
            link.host, link.port = hp
            t0 = time.perf_counter()
            res = sequence.compute_ef_board(a2c, a4c, link)
            try:
                res["cardiac"] = sequence.cardiac_board(a2c, a4c, link, pt)["cardiac"]
            except Exception as e:
                res["cardiac_err"] = str(e)
            res["wall_total_s"] = time.perf_counter() - t0
            link.close()
            fig = sequence.render_ef(a2c, a4c, res)
            self.mq.put(("ok", res, fig, a2c, a4c, pt))
        except Exception as e:
            link.close()
            self.mq.put(("err", str(e)))

    def _mpoll(self):
        try:
            while True:
                item = self.mq.get_nowait()
                self.busy = self.app.busy = False
                self.btn_auto.state(["!disabled"])
                if item[0] == "err":
                    self.var_note.set("算不出来：%s（轮廓上找不到二尖瓣平面时会这样，可先点击纠正再记入）" % item[1])
                elif item[0] == "mono":
                    self._show_mono(*item[1:])
                else:
                    self._show_result(*item[1:])
        except queue.Empty:
            pass
        if self.top.winfo_exists():
            self.top.after(60, self._mpoll)

    def _clear_result(self):
        self.var_mef.set("—")
        self.lab_mef.configure(fg=uistyle.C_TEXT)
        self.var_edv.set("EDV —")
        self.var_esv.set("ESV —")
        self.var_ref.set("")
        for var, lab in self.var_m.values():
            var.set("—")
            lab.configure(fg=uistyle.C_TEXT)
        for tvar, title in self.var_mt.values():
            tvar.set(title)
        self.var_eft.set("EF（双平面 Simpson）")
        self.var_legend.set(LEGEND2)
        if hasattr(self, "var_hint"):
            self.var_hint.set("测量后在这里列出：EF 分档、左室是否扩大、左房是否扩大、左室是否肥厚，以及它们的组合提示")
        self._blank_fig()

    def _show_result(self, res, fig, a2c, a4c, pt):
        import cardiac_measure as cm
        from PIL import Image, ImageTk
        self.ef_result = res
        ef = res["ef"]
        band = 0 if ef <= 40 else (1 if ef < 50 else 2)
        self.var_mef.set("%d%%" % ef)
        self.lab_mef.configure(fg=BAND_COL[band])
        self.var_edv.set("EDV %d mL" % res["edv"])
        self.var_esv.set("ESV %d mL" % res["esv"])
        ref = a4c.get("ef_ref") if a4c.get("ef_ref") is not None else a2c.get("ef_ref")
        self.var_ref.set(("CAMUS 参考 EF %.0f%%" % ref) if ref is not None else "")
        c = res.get("cardiac")
        h, w, sex = pt if pt else (None, None, None)
        bsa = cm.bsa_mosteller(h, w) if pt else None
        la, lvm, wall = self.var_m["la"], self.var_m["lvm"], self.var_m["wall"]
        if c is None:
            for var, _ in (la, lvm, wall):
                var.set("不可用")
        else:
            if c.get("la_truncated"):
                la[0].set("被扇区截断，不报")
            elif c.get("la_ml") is not None:
                if bsa:
                    lavi = c["la_ml"] / bsa
                    la[0].set("%.0f mL / %.0f mL/m²\n%s" % (c["la_ml"], lavi, cm.lavi_band(lavi)))
                    la[1].configure(fg="#c2410c" if lavi > 34 else uistyle.C_GREEN)
                else:
                    la[0].set("%.0f mL\n（填身高体重算 LAVI）" % c["la_ml"])
            if c.get("lv_mass_g") is not None:
                lvmi = c.get("lvmi") if c.get("lvmi") is not None else (c["lv_mass_g"] / bsa if bsa else None)
                k = (c.get("lvmi_class") or {}).get("code")
                lvm[0].set("%.0f g%s%s" % (c["lv_mass_g"], (" / %.0f g/m²" % lvmi) if lvmi else "",
                                           ("\n" + cm.LVMI_CLASS.get(k, "")) if k else ""))
                lvm[1].configure(fg="#c2410c" if k == "increased" else uistyle.C_TEXT)
            if c.get("wall_mm") is not None:
                wall[0].set("%.1f mm%s" % (c["wall_mm"], (" / RWT %.2f" % c["rwt"]) if c.get("rwt") is not None else ""))
        self.var_m["where"][0].set("板上 Simpson %.0f ms\n合计 %.1f s" % (res.get("board_ms", 0), res["wall_total_s"]))
        im = Image.frombytes("RGB", (512, 512), fig).resize((FIG, FIG), Image.BILINEAR)
        self.fig_full, self.fig_size = fig, (512, 512)
        self.photos_m["fig"] = ImageTk.PhotoImage(im)
        self.lab_fig.configure(image=self.photos_m["fig"], width=FIG, height=FIG)
        import clinical_hints as ch
        lvmi_v = None
        if c is not None and c.get("lv_mass_g") is not None:
            lvmi_v = c.get("lvmi") if c.get("lvmi") is not None else (c["lv_mass_g"] / bsa if bsa else None)
        self.hints = ch.measure_hints(ef, res["edv"], sex, bsa, (c or {}).get("la_ml"), bool((c or {}).get("la_truncated")),
                                      lvmi_v)
        self.var_hint.set(ch.text(self.hints))
        self.var_note.set("提示方向见左下；左室质量 / RWT 为心尖切面二维掩膜口径，仅作参考")
        self.var_status.set("双平面 Simpson：EDV %d mL｜ESV %d mL｜EF %d%%%s" % (
            res["edv"], res["esv"], ef, ("（CAMUS 参考 %.0f%%）" % ref) if ref is not None else ""))
        self.app.status("%s 双平面测量完成：%s；Simpson 在板上 %.0f ms" % (
            a4c["patient"], self.var_status.get().split("：", 1)[1], res.get("board_ms", 0)))

    # ---------------------------------------------------------------- 从关键帧 / 现场模式送来的一拍：单平面 Simpson
    def data_note(self):
        m = getattr(self, "mono", None)
        return m["data_note"] if m else "画面来自 CAMUS（CC BY-NC-SA 4.0）"

    def accept_mono(self, info):
        """info（heartview._send_snapshot）：一拍的 ED / ES 112 帧 + 板上左室掩膜 + 屏幕标尺换算的像素间距。
        只有 A4C 一个切面：把它同时当两个切面交给板上的 simpson_ef，双平面公式在两个切面相同时就是单平面圆盘法
        V = π/4 Σ d² h（ASE 2015 允许只有一个切面时用）。左房 / 心肌程序是 CAMUS 训练的，这里不跑。"""
        if self.busy or self.app.busy or self.auto is not None:
            self.var_note.set("板子正忙，稍等再送")
            return
        self.mono = info
        self.ef_slots, self.ef_result = {}, None
        self._clear_result()
        self.var_views["4CH"].set("%s  ED 第 %d 帧 / ES 第 %d 帧 ✓" % (
            info["patient"].split()[0][:12], info["ed_frame"] + 1, info["es_frame"] + 1))
        self.var_views["2CH"].set("无：单平面，来自%s%s" % (info["source"], "（含纠错）" if info["corrected"] else ""))
        self.busy = self.app.busy = True
        self.btn_auto.state(["disabled"])
        self.var_note.set("板上单平面 Simpson（A4C）…")
        self.app.status("从%s送来 %s 的一拍（ED 第 %d 帧 / ES 第 %d 帧），按屏幕标尺 %.2f mm/像素做单平面 Simpson" % (
            info["source"], info["patient"], info["ed_frame"] + 1, info["es_frame"] + 1, info["spacing"][0]))
        try:
            hp = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except ValueError:
            hp = self.app.link.host, self.app.link.port
        threading.Thread(target=self._mono_worker, args=(info, hp), daemon=True).start()

    def _mono_worker(self, info, hp):
        import time
        import numpy as np
        import jobs
        link = self.app.link
        try:
            link.host, link.port = hp
            t0 = time.perf_counter()
            link.connect()
            ed, es = np.asarray(info["ed_mask"], bool), np.asarray(info["es_mask"], bool)
            # 112 太粗：轮廓 / 二尖瓣平面按原图分辨率设计，直接在 112 上算 EF 偏低约 10 个点（ES000108：24 % 对 33 %）；
            # 让板上先按 Pillow NEAREST 放大到 448（与 CAMUS 掩膜回原图尺寸同一个做法），像素间距相应除以 4
            sp = (info["spacing"][0] / UP, info["spacing"][1] / UP)
            r = jobs.simpson_ef(link, [ed, es, ed, es], sp, sp, out_sizes=[(N112 * UP, N112 * UP)] * 4, clean=True)
            link.close()
            res = r["result"]
            if not res.get("ok"):
                raise RuntimeError(res.get("error"))
            pts = np.frombuffer(r["blobs"]["contours"], np.float32).reshape(-1, 2).astype(np.float64)
            geom, o = [], 0
            for m in res["masks"]:
                a = lambda v: np.array(v, np.float64)
                geom.append(dict(base=(a(m["base"][0]), a(m["base"][1])), mid=a(m["mid"]), apex=a(m["apex"]),
                                 disks=[(a(d[0]), a(d[1])) for d in m["disks"]], contour=pts[o:o + m["n_contour"]]))
                o += m["n_contour"]
            res["wall_total_s"] = time.perf_counter() - t0
            self.mq.put(("mono", res, self._mono_fig(info, geom[2], geom[3]), info))
        except Exception as e:
            link.close()
            self.mq.put(("err", str(e)))

    @staticmethod
    def _mono_fig(info, g_ed, g_es):
        """1×2（左 ED、右 ES）：112 帧放大到 256，画轮廓、长轴、二尖瓣平面、20 个圆盘 -> 512×256 RGB bytes。"""
        from PIL import Image, ImageDraw
        canvas = Image.new("RGB", (512, 256), (0, 0, 0))
        k = 256.0 / (N112 * UP)                       # 几何在板上放大后的 448 网格里
        for col, (img, g, ph, fr) in enumerate(((info["ed_img"], g_ed, "ED", info["ed_frame"]),
                                                (info["es_img"], g_es, "ES", info["es_frame"]))):
            tile = Image.fromarray(img).convert("RGB").resize((256, 256), Image.BILINEAR)
            d = ImageDraw.Draw(tile)
            pt = lambda rc: (float(rc[1]) * k, float(rc[0]) * k)
            c = g["contour"]
            if len(c):
                d.line([pt(rc) for rc in c] + [pt(c[0])], fill=(0, 230, 0), width=1)
            for a, b in g["disks"]:
                d.line([pt(a), pt(b)], fill=(255, 200, 0), width=1)
            d.line([pt(g["mid"]), pt(g["apex"])], fill=(255, 60, 60), width=2)
            d.line([pt(g["base"][0]), pt(g["base"][1])], fill=(80, 160, 255), width=2)
            d.text((6, 4), "A4C %s  frame %d" % (ph, fr + 1), fill=(255, 255, 255))
            canvas.paste(tile, (col * 256, 0))
        return canvas.tobytes()

    def _show_mono(self, res, fig, info):
        from PIL import Image, ImageTk
        self.ef_result = res
        ef = res["ef"]
        band = 0 if ef <= 40 else (1 if ef < 50 else 2)
        self.var_mef.set("%d%%" % ef)
        self.lab_mef.configure(fg=BAND_COL[band])
        self.var_edv.set("EDV %d mL" % res["edv"])
        self.var_esv.set("ESV %d mL" % res["esv"])
        self.var_ref.set("%s：同一拍 %.0f%%（未校准）\n整段 %.0f%%（校准后）" % (
            info["source"][:3], info["ef_beat_raw"], info["ef_src"]) if info.get("ef_src") is not None else "")
        self.var_eft.set("EF（单平面 Simpson）")
        self.var_legend.set(LEGEND1)
        self.var_mt["wall"][0].set("屏幕标尺（定标）")
        self.var_m["la"][0].set("单平面不算\n（左房程序是 CAMUS 训练的）")
        self.var_m["lvm"][0].set("单平面不算")
        self.var_m["wall"][0].set("%.2f mm/像素\n原始画面 %.3f" % (info["spacing"][0], info["mm_px"] or 0))
        big = Image.new("RGB", (512, 512), (0, 0, 0))   # 左边大图：送来的这一拍（上 ED / ES 圆盘图）
        big.paste(Image.frombytes("RGB", (512, 256), fig), (0, 128))
        self.photos = [ImageTk.PhotoImage(big)]
        self.cv.itemconfigure(self.cv_img, image=self.photos[0])
        self.var_frame.set("从%s送来：%s %s，ED 第 %d 帧 / ES 第 %d 帧%s" % (
            info["source"], info["patient"], info.get("which", "中位那一拍"), info["ed_frame"] + 1, info["es_frame"] + 1,
            "（含人工纠错）" if info["corrected"] else ""))
        self.var_key.set("")
        self.curve.delete("all")
        self.curve.create_text(self.CURVE_W // 2, self.CURVE_H // 2, fill="#888", font=FONT,
                               text="单平面测量：这一拍来自%s\n容积曲线和逐拍结果在%s里看" % (info["source"], info["source"]))
        self.var_m["where"][0].set("板上 Simpson %.0f ms\n合计 %.1f s" % (res.get("ms", {}).get("total", 0)
                                                                     if isinstance(res.get("ms"), dict)
                                                                     else res.get("ms", 0), res["wall_total_s"]))
        self.fig_full = fig
        self.fig_size = (512, 256)
        im = Image.frombytes("RGB", (512, 256), fig).resize((FIG, FIG // 2), Image.BILINEAR)
        self.photos_m["fig"] = ImageTk.PhotoImage(im)
        self.lab_fig.configure(image=self.photos_m["fig"], width=FIG, height=FIG // 2)
        import clinical_hints as ch
        self.hints = ch.measure_hints(ef, res["edv"], mono=True)
        self.var_hint.set(ch.text(self.hints))
        self.var_note.set("提示方向见左下；Simpson 在二尖瓣平面截断，EF 与%s的整掩膜比值法口径不同" % info["source"])
        self.var_status.set("单平面 Simpson（A4C，来自%s）：EDV %d mL｜ESV %d mL｜EF %d%%" % (
            info["source"], res["edv"], res["esv"], ef))
        self.app.status("%s 单平面测量完成：EDV %d mL｜ESV %d mL｜EF %d%%（%s EF %.0f%%）" % (
            info["patient"], res["edv"], res["esv"], ef, info["source"], info.get("ef_src") or 0))

    def _big_figure(self):
        if getattr(self, "fig_full", None) is None:
            return
        from PIL import Image, ImageTk
        old = getattr(self, "ef_win", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        win = uistyle.window(tk.Toplevel(self.top), "单平面 Simpson 圆盘图（A4C）" if getattr(self, "mono", None)
                             else "双平面 Simpson 圆盘图")
        self.ef_photo = ImageTk.PhotoImage(Image.frombytes("RGB", self.fig_size, self.fig_full))
        tk.Label(win, image=self.ef_photo, bd=0).pack(padx=12, pady=12)
        self.ef_win = win

    def key(self, name):
        sequence.SeqWindow.key(self, name)
