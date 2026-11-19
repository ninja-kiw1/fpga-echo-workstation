# -*- coding: utf-8 -*-
"""make_board_ws.py —— 从 PC 版 workstation.py 生成板端版。

为什么要有这个脚本（2026-11-19）：
    板端那份一直是「PC 单文件版 + 板端适配」的分叉，每次 PC 侧加了功能，两边就再分叉一次。
    现在把「板端适配」写成这份可重放的脚本：**PC 版是唯一来源**，板端版 = 本脚本的输出。
    脚本里每一步都带断言（锚点必须恰好出现 N 次），PC 版改了形状会直接报错，
    不会静默漏掉某一层适配。

板端适配共四件事：
    ① 术语改写：板端→引擎、板上/板子→设备、服务端→引擎、回传→输出、发送→处理、
       上传→送入、上位机→本机、板卡地址→系统名（11-17 定的口径：界面不出现"板子/地址"）。
    ② 不暴露地址 / 端口：顶栏与眼底页的地址、端口输入框删掉（StringVar 留着，走 127.0.0.1）；
       命令行不再接受 <host> <port>。
    ③ 右心「触边判定」关闭：HC01 末 8 字节 border_px 固定 0（PC 版 11-19 已同样关闭）。
    ④ 板端拖动提速层（11-18 / 11-19 两轮，GTK 兼容层的 _canvas / _gtkbase 那两处不在本文件里）：
       整帧照片缓存 + 合成底图缓存 + 拖动合并事件 + 角标画进图里 + 拖动中只动容积曲线光标。

用法：
    python make_board_ws.py <PC 版目录> <输出目录>
        PC 版目录：含 workstation.py / echo_ws.py / proto.py（即 2026pld/07_ui）
        输出目录：板端 app/ 要放的三份文件
"""
from __future__ import print_function

import io
import os
import sys


# ---------------------------------------------------------------- 1. 术语改写
TERMS = [
    ("板卡地址", "系统名"),
    ("板端回传", "引擎输出"),
    ("上位机", "本机"),
    ("板端", "引擎"),
    ("板上", "设备"),
    ("板子", "设备"),
    ("服务端", "引擎"),
    ("回传", "输出"),
    ("发送", "处理"),
    ("上传", "送入"),
]

# 术语改写之后仍要单独收拾的句子（"处理"会被改到协议帧的收/发语境里，读着不通）
FIXUPS = [
    # 保序：下面是逐句润色，锚点必须在术语改写之后仍然存在
    ("处理系统调用从 512 次降到 1 次", "收发系统调用从 512 次降到 1 次"),
    ("处理时要把 KF01 的 a / b 换成这两个", "送入时要把 KF01 的 a / b 换成这两个"),
    ("往返", "来回"),
    ("PC 上", "本机"),
    ("TCP 链", "数据通"),
]

HEADER_OLD = (
    "    本文件由 18 个源模块机械合并而成（见文件末尾的\"来源\"清单），行为与原工程一致：\n"
    "        python workstation.py                 # 心超工作站（默认）\n"
    "        python workstation.py <host> <port>   # 指板卡地址\n"
    "        python workstation.py --fundus        # 直接开眼底那套（原 app.py）\n"
)
HEADER_NEW = (
    "    本文件由 18 个源模块机械合并而成（见文件末尾的\"来源\"清单），行为与原工程一致：\n"
    "        python workstation.py                 # 心超工作站（默认）\n"
    "        python workstation.py --fundus        # 直接开眼底那套（原 app.py）\n"
    "\n"
    "    单机版（DP 工作站）：推理服务跑在本机，界面不暴露任何地址 / 端口，\n"
    "    也不再显示任何传输侧计时指标。\n"
)

# ---------------------------------------------------------------- 2. 地址输入框
TOPBAR_OLD = """            self._label(conn, "板卡", fg=C_MUTED).pack(side="left", padx=(0, 6))
            self.var_host = tk.StringVar(value=self.link.host)
            ttk.Entry(conn, textvariable=self.var_host, width=14, font=FONT_MONO).pack(side="left")
            self._label(conn, ":", fg=C_MUTED).pack(side="left", padx=2)
            self.var_port = tk.StringVar(value=str(self.link.port))
            ttk.Entry(conn, textvariable=self.var_port, width=6, font=FONT_MONO).pack(side="left")
"""
TOPBAR_NEW = """            # 单机版不暴露地址 / 端口（11-17）：StringVar 留着给内部用，输入框不摆出来
            self.var_host = tk.StringVar(value=self.link.host)
            self.var_port = tk.StringVar(value=str(self.link.port))
"""

TOOLS_OLD = """        uistyle.label(tools, "板卡", fg=C_MUTED).pack(side="left", padx=(0, 6))
        self.var_host = tk.StringVar(value=self.link.host)
        ttk.Entry(tools, textvariable=self.var_host, width=14, font=FONT_MONO).pack(side="left")
        uistyle.label(tools, ":", fg=C_MUTED).pack(side="left", padx=2)
        self.var_port = tk.StringVar(value=str(self.link.port))
        ttk.Entry(tools, textvariable=self.var_port, width=6, font=FONT_MONO).pack(side="left")
"""
TOOLS_NEW = """        # 单机版不暴露地址 / 端口（11-17）：StringVar 留着给内部用，输入框不摆出来
        self.var_host = tk.StringVar(value=self.link.host)
        self.var_port = tk.StringVar(value=str(self.link.port))
"""

ROUTE_HINT_OLD = """def route_hint(host):
    \"\"\"连不上时看本机走哪个地址去设备：TUN 代理（198.18.x）会假装端口开着、连上就断；没有同网段地址 = 网线没接 / IP 不对。\"\"\"
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, 9))                         # UDP connect 不发包，只选路由
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        return ""
    if ip.startswith("198.18."):
        return "——流量走了代理 TUN（本机 %s）：关掉代理，或让 %s 所在网段直连" % (ip, host)
    if host != "127.0.0.1" and ip.split(".")[:3] != host.split(".")[:3]:
        return "——本机没有和设备同网段的地址（现在走 %s）：检查网线，有线网卡应为 192.168.1.100" % ip
    return ""
"""
ROUTE_HINT_NEW = """def route_hint(host):
    \"\"\"单机版：引擎在同机进程外跑，引擎未就绪只可能是本地服务没起来。
    （原版这里会诊断网段 / TUN 代理，单机版一律返回空串，调用点不动。）\"\"\"
    return ""
"""

CLI_HOST_OLD = 'def _cli(argv):\n    host = argv[0] if len(argv) > 0 else "192.168.1.254"'
CLI_HOST_NEW = 'def _cli(argv):\n    host = argv[0] if len(argv) > 0 else "127.0.0.1"'

DEFAULT_HOST_OLD = """DEFAULT_HOST = "192.168.1.254"
DEFAULT_PORT = 5060"""
DEFAULT_HOST_NEW = """DEFAULT_HOST = "127.0.0.1"                       # 单机运行：引擎服务在本机
DEFAULT_PORT = 5060"""

ARGC_OLD = """    argv = [a for a in argv if not a.startswith("--")]
    host = argv[0] if len(argv) > 0 else DEFAULT_HOST
    port = int(argv[1]) if len(argv) > 1 else DEFAULT_PORT"""
ARGC_NEW = """    argv = [a for a in argv if not a.startswith("--")]     # 单机版：位置参数不用（地址固定 127.0.0.1）
    host, port = DEFAULT_HOST, DEFAULT_PORT"""

# ---------------------------------------------------------------- 3. 帧缓存：初始化
INIT_OLD = """        self._hr_key, self._hr_val = None, None      # 现场滚动心率：上次算过的容积段 (a, n) 与结果（见 _hr_recent）
"""
INIT_NEW = """        self._hr_key, self._hr_val = None, None      # 现场滚动心率：上次算过的容积段 (a, n) 与结果（见 _hr_recent）
        self._vcache, self._vorder = {}, []           # 帧 -> 画好的 PhotoImage（拖时间轴用，见 _frame_photo）
        self._vprep, self._vprep_order = {}, []       # 帧 -> 合成好的底图（含掩膜 / 轮廓）
        self._vlock = threading.Lock()                # 预取线程与界面线程一起写 _vprep
        self._vgen = 0                                # 缓存代数：_vclear 时 +1，正在跑的预取线程看到就退出
        self._vsettle_seq = 0                         # 拖动停下后补画时间线光标的序号（见 _vsettle_later）
        self._render_pending = False                  # 拖动中已经排了一次空闲渲染
        self._scale_pending = None                    # 拖动中攒着没挪的进度条位置（见 _goto / _render_deferred）
        self._tag_font = None                         # ED / ES 角标的 PIL 字体（惰性）
"""

SCALE_OLD = """        self.scale = ttk.Scale(b, from_=0, to=0, orient="horizontal", length=VIEW,
                               command=lambda v: None if self._scale_busy else self._goto(int(float(v)), from_scale=True))"""
SCALE_NEW = """        self.scale = ttk.Scale(b, from_=0, to=0, orient="horizontal", length=VIEW,
                               command=lambda v: None if self._scale_busy
                               else self._goto(int(float(v)), from_scale=True, defer=True))"""

CURVE_OLD = """        self._goto(a + int(round(f * (b - 1 - a))))

    # ---- 播放 ----"""
CURVE_NEW = """        self._goto(a + int(round(f * (b - 1 - a))), defer=True)

    # ---- 播放 ----"""

# 掩膜 / 全心结果一变就作废
VCLEAR_TAKE_HEART_OLD = """        res = r["result"]
        self.heart, self.rh_frames = None, {}
"""
VCLEAR_TAKE_HEART_NEW = """        res = r["result"]
        self.heart, self.rh_frames = None, {}
        self._vclear()
"""

VCLEAR_MASKS_OLD = """            self.masks = {t: mm[t] for t in range(n)}
"""
VCLEAR_MASKS_NEW = """            self.masks = {t: mm[t] for t in range(n)}
            self._vclear()
"""

APPLY_VIEW_OLD = """        self.var_xy.set(self._xy_idle())

    def _compose(self, t, mask, rh_items):"""
APPLY_VIEW_NEW = """        self.var_xy.set(self._xy_idle())
        self._vclear()                               # 显示尺寸变了：合成好的底图 / 照片都作废

    def _compose(self, t, mask, rh_items):"""

ON_FIXED_OLD = """            self.fixed[k] = "fix" if k == t else "prop"
        self.fix_ms.append(ai_ms)"""
ON_FIXED_NEW = """            self.fixed[k] = "fix" if k == t else "prop"
        self._vclear()
        self.fix_ms.append(ai_ms)"""

ON_FIXED_TAIL_OLD = """        self._goto(t)
        self._redraw()
        _log_line("[UI] _on_fixed 出（界面已按要求刷新）")"""
ON_FIXED_TAIL_NEW = """        self._goto(t)
        self._redraw()
        self._vprefetch()                                # 后台先把底图算好，拖曲线就不用现场合成
        self._vwarm_photos()                             # 再把最近的一圈整帧照片补进缓存（第一次拖就命中）
        _log_line("[UI] _on_fixed 出（界面已按要求刷新）")"""

UNDO_OLD = """        self.var_fix.set("已撤销第 %d 帧的最后一次点击（还剩 %d 步可撤销）" % (b["t"] + 1, len(self.undo)))
        self._goto(b["t"])
        self._redraw()"""
UNDO_NEW = """        self.var_fix.set("已撤销第 %d 帧的最后一次点击（还剩 %d 步可撤销）" % (b["t"] + 1, len(self.undo)))
        self._goto(b["t"])
        self._redraw()
        self._vprefetch()
        self._vwarm_photos()"""

RESET_OLD = """        self.var_fix.set("已重置：回到设备原始结果")
        self._goto(self.cur)
        self._redraw()"""
RESET_NEW = """        self.var_fix.set("已重置：回到设备原始结果")
        self._goto(self.cur)
        self._redraw()
        self._vprefetch()
        self._vwarm_photos()"""

# 分析完成：缓存作废 + 预取
TAKE_ANALYZED_OLD = """        if self.result["beats"]:
            self._goto(self.result["beats"][len(self.result["beats"]) // 2]["es"])
        self._redraw()"""
TAKE_ANALYZED_NEW = """        if self.result["beats"]:
            self._goto(self.result["beats"][len(self.result["beats"]) // 2]["es"])
        self._redraw()
        self._vprefetch()                                # 后台先把底图算好，拖曲线就不用现场合成
        self._vwarm_photos()                             # 再把最近的一圈整帧照片补进缓存（第一次拖就命中）"""

# 时间线文字：板上画布只有 392 px，原句会溢出 30 px（11-18 实测）
# 11-19：事件时间线只留心拍一行，原来那两句"蓝 = 关键帧 / 灰 = 跟踪帧"的提示与图例在 PC 版里已经删除，
#        所以这里的两处文字改写一并撤掉（锚点不存在了，留着会直接 SystemExit）。

# ---------------------------------------------------------------- 4. 帧缓存：_goto 拆成"合成本帧 / 贴到画布"
GOTO_OLD = '''    def _goto(self, t, from_scale=False):
        if self.frames is None:
            return
        from PIL import Image, ImageDraw, ImageTk
        np = self.np
        t = max(0, min(len(self.frames) - 1, int(t)))
        self.cur = t
        if not from_scale:
            self._scale_busy = True
            try:
                self.scale.set(t)
            finally:
                self._scale_busy = False
        m = self.masks.get(t)
        rh_items = [it for it in self.rh_frames.get(t, []) if it is not None]
        im = self._compose(t, m if m is not None else np.zeros((N, N), bool), rh_items)
        wm = (self.heart or {}).get("wall_motion")
        if wm:
            self.ws.draw_wm_glyph(im, wm["seg"], self.vw - 74, 6)
        tag = self._phase(t)
        d = ImageDraw.Draw(im)
        sx, r = self.s, min(max(4.0, 6.0 * self.s), 16.0)     # 点击标记：坐标按 448 视图存，画的时候按显示缩放
        for x, y, pos in self.clicks.get(t, []):
            col = (0, 230, 90) if pos else (255, 60, 60)
            X, Y = x * sx, y * sx
            d.ellipse((X - r, Y - r, X + r, Y + r), outline=col, width=2)
            d.line((X - r * 0.67, Y, X + r * 0.67, Y), fill=col, width=2)
            if pos:
                d.line((X, Y - r * 0.67, X, Y + r * 0.67), fill=col, width=2)
        self.photos["v"] = ImageTk.PhotoImage(im)
        self.cv_v.itemconfigure(self.cv_img, image=self.photos["v"])
        self.cv_v.delete("tag")
        self.cv_v.delete("hint")
        if tag:
            self.cv_v.create_text(12, 10, text=tag, anchor="nw", font=FONT_TAG, tags="tag",
                                  fill="#ff6060" if tag == "ED" else "#5aa0ff")
        self._draw_ruler()
        kind = ""
        if self.keyv and t < len(self.keyv):
            kind = "关键帧（PL 分割）" if self.keyv[t] else "跟踪帧（PS 块匹配）"
        if t in self.fixed:
            kind = "人工纠错（FPGA 重算）" if self.fixed[t] == "fix" else "纠错后跟着重算（FPGA）"
        self.var_ft.set("第 %d / %d 帧  %.2f s  %s %s" % (t + 1, len(self.frames), t / self.fps, tag, kind))
        self._draw_cursor()
'''

GOTO_NEW = '''    # ---- 拖动时间轴的逐帧缓存（板端 11-18 一轮 / 11-19 二轮；11-19 晚随 PC 版同步重新挂上）
    # 板上一帧要是从没看过，得把整幅从头合成一遍（面板实测 90 ms/格：合成 44 + 画布重合成 15
    # + 两条曲线重画 21），拖动只有 11 帧/秒，手指和画面完全脱节。四件事：
    #   ① 合成好的底图与整帧照片都按帧缓存（掩膜 / 点击 / 全心结果 / 缩放一变就整批作废）；
    #   ② 拖动时合并事件，只画最后停下的那一帧（中间那些直接跳过，不排队）；
    #   ③ ED / ES 角标画进图里，画布上只剩"一张图"，走兼容层 _canvas 的单图快路；
    #   ④ 拖动中只重画容积曲线那条光标，事件时间线那条等手停下来 160 ms 再补（那条有上百个图元）。
    # 11-19 二轮：合成从 29 ms 降到 ~20 ms 之后，缓存能多放几帧就基本全命中（99 帧的片子原来只放得下 64 帧底图、
    # 32 帧照片，扫一遍有一半是冷的）。按 675×448 算：底图 0.9 MB/帧、照片 ~2.1 MB/帧，总量仍控制在 ~200 MB。
    _V_CACHE_MAX = int(os.environ.get("WS_V_CACHE", "40"))     # 画好的整帧照片
    _V_PREP_MAX = int(os.environ.get("WS_V_PREP", "128"))      # 合成好的底图

    def _vclear(self):
        """帧图缓存作废：掩膜 / 点击 / 全心结果 / 显示尺寸变了才需要（都很便宜，直接整批丢掉）。"""
        self._vcache, self._vorder = {}, []
        self._vprep, self._vprep_order = {}, []
        self._vgen = getattr(self, "_vgen", 0) + 1             # 让正在跑的预取线程自己退出

    def _vprep_build(self, t):
        """第 t 帧合成好的底图（PIL Image，已含掩膜贴回 / 轮廓 / 原生帧缩放）。

        11-19 同步后「放大 + 贴掩膜 + 画轮廓」都收进了 echo_ws.compose_preview，
        所以这里缓存的就是合成结果；点击标记与 ED/ES 角标仍在 _frame_photo 里加。
        """
        m = self.masks.get(t)
        rh_items = [it for it in self.rh_frames.get(t, []) if it is not None]
        return self._compose(t, m if m is not None else self.np.zeros((N, N), bool), rh_items)

    def _vprep_get(self, t, gen=None):
        p = self._vprep.get(t)
        if p is not None:
            return p
        p = self._vprep_build(t)
        with self._vlock:
            if gen is not None and gen != self._vgen:        # 预取线程：算的过程中缓存被作废了，别塞回去
                return p
            while len(self._vprep_order) >= self._V_PREP_MAX:
                self._vprep.pop(self._vprep_order.pop(0), None)
            self._vprep[t] = p
            self._vprep_order.append(t)
        return p

    def _vprefetch(self, delay=600):
        """分析完 / 纠错后，后台把各帧的合成底图先算好（只碰 numpy / PIL，不碰 Tk）。

        体量：150 帧 × ~10 ms ≈ 1.5 s 的一个核，缓存放得下（WS_V_PREP 帧）之后就一直是命中。
        """
        if self.frames is None or self.live or self.busy:
            return
        gen, n, cur = self._vgen, len(self.frames), int(self.cur or 0)

        def run():
            order, step = [cur], 1
            while len(order) < n:                            # 从当前帧往两边扩：先看近处
                if cur - step >= 0:
                    order.append(cur - step)
                if cur + step < n:
                    order.append(cur + step)
                step += 1
            for t in order:
                if self._vgen != gen:                        # 缓存被作废（点击纠错 / 重新分析）
                    return
                # 11-19：用户在拖曲线时先让路。这个线程合成一帧要十几毫秒，和主线程抢 GIL 会让拖动一顿一顿的
                # （实测：分析完就拖，P90 心跳 90 ms；让它让路后主线程只跟自己的合成抢）。
                while self._render_pending or (time.time() - getattr(self, "_vlast_render", 0.0)) < 0.20:
                    time.sleep(0.05)
                    if self._vgen != gen:
                        return
                try:
                    self._vprep_get(t, gen=gen)
                except Exception:
                    return

        self.frame.after(int(delay), lambda: threading.Thread(target=run, daemon=True).start())

    def _vwarm_photos(self, chunk=1, delay=900):
        """空闲时把整帧照片也补进缓存（主线程、一次一两帧，不挡操作）。

        照片比底图更值钱：命中时换一帧只要 4 ms，底图命中但照片没命中还要 13 ms 建 PhotoImage。
        所以分析完先按"离当前帧近的先补"把缓存填满，用户第一次拖曲线就已经是命中。
        """
        if os.environ.get("WS_V_WARM", "1") == "0":
            return
        self._vwarm_seq = getattr(self, "_vwarm_seq", 0) + 1
        seq, gen = self._vwarm_seq, self._vgen

        def step():
            if seq != self._vwarm_seq or gen != self._vgen or self.frames is None or self.live:
                return
            if self.busy or self._render_pending:       # 正在出图 / 正在拖：让路，稍后再补
                self.frame.after(400, step)
                return
            n = len(self.frames)
            if len(self._vcache) >= min(n, self._V_CACHE_MAX):
                return
            cur, made, r = int(self.cur or 0), 0, 0
            while made < chunk and r <= n:
                for t in (cur - r, cur + r):
                    if 0 <= t < n and t not in self._vcache:
                        try:
                            ph = self._frame_photo(t)
                        except Exception:
                            _log_exc("EchoView._vwarm_photos")
                            return
                        self._vcache[t] = ph
                        self._vorder.append(t)
                        while len(self._vorder) > self._V_CACHE_MAX:
                            self._vcache.pop(self._vorder.pop(0), None)
                        made += 1
                        if made >= chunk:
                            break
                r += 1
            try:
                self.frame.after(25, step)
            except Exception:                            # 窗口已经关了
                return

        try:
            self.frame.after(int(delay), step)
        except Exception:
            pass

    def _frame_photo(self, t):
        """画第 t 帧：合成底图 + 全心标记 + 点击标记 + ED/ES 角标。

        角标画进图里（原来是一个 canvas 文字图元）：画布上只剩一张图，_canvas 才有单图快路。
        字号跟兼容层一致：tk 的 14 磅 → 96dpi 下 19 像素。
        """
        from PIL import ImageDraw, ImageTk
        im = self._vprep_get(t).copy()
        wm = (self.heart or {}).get("wall_motion")
        if wm:
            self.ws.draw_wm_glyph(im, wm["seg"], self.vw - 74, 6)
        d = ImageDraw.Draw(im)
        sx, r = self.s, min(max(4.0, 6.0 * self.s), 16.0)     # 点击标记：坐标按 448 视图存，画的时候按显示缩放
        for x, y, pos in self.clicks.get(t, []):
            col = (0, 230, 90) if pos else (255, 60, 60)
            X, Y = x * sx, y * sx
            d.ellipse((X - r, Y - r, X + r, Y + r), outline=col, width=2)
            d.line((X - r * 0.67, Y, X + r * 0.67, Y), fill=col, width=2)
            if pos:
                d.line((X, Y - r * 0.67, X, Y + r * 0.67), fill=col, width=2)
        tag = self._phase(t)
        if tag:
            if self._tag_font is None:
                self._tag_font = self.ws._font(19)
            d.text((12, 10), tag, font=self._tag_font,
                   fill=(255, 96, 96) if tag == "ED" else (90, 160, 255))
        return ImageTk.PhotoImage(im)

    def _render_frame(self, t, settle=True):
        """把第 t 帧贴到画布上（命中缓存就只换图）。

        settle=False：拖动中，只重画容积曲线那条光标（时间线那条等停下再补，见 _vsettle_later）。
        """
        self._vlast_render = time.time()                 # 后台预取线程看见这个就让路（见 _vprefetch）
        ph = self._vcache.get(t)
        if ph is None:
            ph = self._frame_photo(t)
            self._vcache[t] = ph
            self._vorder.append(t)
            while len(self._vorder) > self._V_CACHE_MAX:
                self._vcache.pop(self._vorder.pop(0), None)
        self.photos["v"] = ph
        self.cv_v.itemconfigure(self.cv_img, image=ph)
        self.cv_v.delete("hint")
        self._draw_ruler()
        self._draw_cursor(only=None if settle else "cv")

    def _vsettle_later(self, ms=160):
        """手停下来之后，把事件时间线那条光标补上（拖动期间只动容积曲线那条）。"""
        self._vsettle_seq += 1
        seq = self._vsettle_seq

        def run():
            if seq != self._vsettle_seq or self.frames is None:
                return
            try:
                self._draw_cursor()
            except Exception:
                _log_exc("EchoView._vsettle_later")
        try:
            self.frame.after(int(ms), run)
        except Exception:                                # 窗口已经关了：忽略
            pass

    def _render_deferred(self):
        self._render_pending = False
        if self.frames is None:
            return
        if getattr(self, "_scale_pending", None) is not None:
            self._set_scale(self._scale_pending)
            self._scale_pending = None
        self._render_frame(self.cur, settle=False)
        self._vsettle_later()

    def _set_scale(self, t):
        """挪播放进度条（兼容层的 set() 会回调 command，靠 _scale_busy 拦住重入）。"""
        self._scale_busy = True
        try:
            self.scale.set(t)
        finally:
            self._scale_busy = False

    def _goto(self, t, from_scale=False, defer=False):
        if self.frames is None:
            return
        t = max(0, min(len(self.frames) - 1, int(t)))
        self.cur = t
        if not from_scale:
            if defer:
                self._scale_pending = t          # 拖动中先不挪进度条：等真出图那一帧再一起挪（省一次 GTK 布局）
            else:
                self._set_scale(t)
        tag = self._phase(t)
        kind = ""
        if self.keyv and t < len(self.keyv):
            kind = "关键帧（PL 分割）" if self.keyv[t] else "跟踪帧（PS 块匹配）"
        if t in self.fixed:
            kind = "人工纠错（FPGA 重算）" if self.fixed[t] == "fix" else "纠错后跟着重算（FPGA）"
        self.var_ft.set("第 %d / %d 帧  %.2f s  %s %s" % (t + 1, len(self.frames), t / self.fps, tag, kind))
        if defer:
            # 拖动中：先把"停在哪一帧"记下来，空闲时只画最后那一帧
            if not self._render_pending:
                self._render_pending = True
                self.frame.after_idle(self._render_deferred)
            return
        self._render_frame(t)
'''

CURSOR_OLD = '''    def _draw_cursor(self):
        if self.frames is None:
            return
        x, _ = self._xy()
        a, b = self._span()
        for cv, y1 in ((self.cv, PLOT_B), (self.tl, TH - 3)):
            cv.delete("cursor")'''
CURSOR_NEW = '''    def _draw_cursor(self, only=None):
        """画光标竖线。only="cv" = 只动容积曲线那条（拖动中不走时间线画布，省 7~8 ms/帧）。"""
        if self.frames is None:
            return
        x, _ = self._xy()
        a, b = self._span()
        for cv, y1, tag in ((self.cv, PLOT_B, "cv"), (self.tl, TH - 3, "tl")):
            if only == "cv" and tag == "tl":
                continue
            cv.delete("cursor")'''


# ---------------------------------------------------------------- 5. echo_ws.py：轮廓提速（板端 11-18 实测）
ECHO_EDGE_OLD = '''def _edge(mask, scale):
    """放大后的掩膜内边界（4 邻域里有背景的像素），各方向一样连续（10-25 前只比上 / 左邻居，右下边是虚线）。"""
    from PIL import Image
    m = np.array(Image.fromarray(mask.astype(np.uint8) * 255).resize((N * scale, N * scale), Image.NEAREST)) > 0
    p = np.pad(m, 1)
    inner = p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return m & ~inner
'''
ECHO_EDGE_NEW = '''def _edge(mask, scale):
    """放大后的掩膜内边界（4 邻域里有背景的像素），各方向一样连续（10-25 前只比上 / 左邻居，右下边是虚线）。

    板端 11-18 提速：原来是「把 112 掩膜 NEAREST 放大成 448 → 在 448x448 上做 4 次布尔与」，
    放大 1.8 ms + 布尔运算 5.2 ms，是拖动时间轴里第二贵的一步。
    改成在 112 网格上判「这一格的哪几条边算边界」，再按 scale×scale 块填回大图：
    放大后的边界像素集合**逐像素完全相同**（112 的一格放大成 s×s，块内部 4 邻域全在块内，
    必然是"内部"；块的四条外沿各自只取决于对应方向上邻居格的值），有对拍脚本验证。
    """
    m = np.asarray(mask, dtype=bool).reshape(N, N)
    if scale == 1:                       # 不放大时"块"退化成 1 像素，4 个邻居全在块外，走原式
        p = np.pad(m, 1)
        inner = p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
        return m & ~inner
    up = np.zeros_like(m)
    dn = np.zeros_like(m)
    lf = np.zeros_like(m)
    rt = np.zeros_like(m)
    up[1:, :] = m[:-1, :]
    dn[:-1, :] = m[1:, :]
    lf[:, 1:] = m[:, :-1]
    rt[:, :-1] = m[:, 1:]
    top = m & ~up
    bot = m & ~dn
    lef = m & ~lf
    rig = m & ~rt
    e = np.zeros((N, scale, N, scale), bool)
    e[:, 0, :, :] = top[:, :, None]
    e[:, scale - 1, :, :] = bot[:, :, None]
    e[:, :, :, 0] |= lef[:, None, :]          # |= ：角上的像素两个方向都算边界
    e[:, :, :, scale - 1] |= rig[:, None, :]
    return e.reshape(N * scale, N * scale)
'''
ECHO_CONTOUR_OLD = '''    rgb = np.repeat(big[..., None], 3, -1)
    rgb[_edge(mask, scale)] = color
    return rgb
'''
ECHO_CONTOUR_NEW = '''    # 板端 11-18：这里原来是 np.repeat(big[..., None], 3, -1)，板上（numpy 1.13）实测 21.9 ms ——
    # 是整个拖动路径里最贵的一步（448x448x3 的广播拷贝）。换成三次整块赋值 ~1 ms，出来的 RGB 逐字节相同。
    h, w = big.shape
    rgb = np.empty((h, w, 3), np.uint8)
    rgb[:, :, 0] = big
    rgb[:, :, 1] = big
    rgb[:, :, 2] = big
    rgb[_edge(mask, scale)] = color
    return rgb
'''


def apply_once(text, old, new, desc, count=1):
    n = text.count(old)
    if n != count:
        raise SystemExit("[FAIL] %s：锚点出现 %d 次（期望 %d）\n---\n%s\n---" % (desc, n, count, old[:400]))
    return text.replace(old, new)


def build_workstation(text):
    # 1) 头部：去掉命令行地址参数，补一段"单机版"说明
    text = apply_once(text, HEADER_OLD, HEADER_NEW, "模块头")

    # 2) 术语（长词先换，避免"板卡地址"被"板端"之类抢走）
    for a, b in TERMS:
        text = text.replace(a, b)

    # 3) 逐句润色
    for a, b in FIXUPS:
        text = text.replace(a, b)

    # 4) 地址 / 端口输入框
    text = apply_once(text, TOPBAR_OLD, TOPBAR_NEW, "顶栏地址框")
    text = apply_once(text, TOOLS_OLD, TOOLS_NEW, "眼底页地址框")
    text = apply_once(text, DEFAULT_HOST_OLD, DEFAULT_HOST_NEW, "DEFAULT_HOST", count=2)
    text = apply_once(text, ROUTE_HINT_OLD, ROUTE_HINT_NEW, "route_hint 单机版")
    text = apply_once(text, CLI_HOST_OLD, CLI_HOST_NEW, "imagelink CLI 默认地址")

    # 5) 帧缓存：初始化 / 作废点 / 预取
    text = apply_once(text, INIT_OLD, INIT_NEW, "缓存字段初始化")
    text = apply_once(text, SCALE_OLD, SCALE_NEW, "进度条 defer")
    text = apply_once(text, CURVE_OLD, CURVE_NEW, "曲线点击 defer")
    text = apply_once(text, VCLEAR_TAKE_HEART_OLD, VCLEAR_TAKE_HEART_NEW, "_take_heart 作废")
    text = apply_once(text, VCLEAR_MASKS_OLD, VCLEAR_MASKS_NEW, "掩膜落地作废")
    text = apply_once(text, APPLY_VIEW_OLD, APPLY_VIEW_NEW, "_apply_view 作废")
    text = apply_once(text, ON_FIXED_OLD, ON_FIXED_NEW, "_on_fixed 作废")
    text = apply_once(text, ON_FIXED_TAIL_OLD, ON_FIXED_TAIL_NEW, "_on_fixed 预取")
    text = apply_once(text, UNDO_OLD, UNDO_NEW, "undo_fix 预取")
    text = apply_once(text, RESET_OLD, RESET_NEW, "reset_fix 预取")
    text = apply_once(text, TAKE_ANALYZED_OLD, TAKE_ANALYZED_NEW, "分析完成预取")

    # 6) _goto 拆成"合成本帧 / 贴到画布"，并挂上缓存
    text = apply_once(text, GOTO_OLD, GOTO_NEW, "_goto 拆分")
    text = apply_once(text, CURSOR_OLD, CURSOR_NEW, "_draw_cursor only")
    return text


def build_echo_ws(text):
    for a, b in TERMS:
        text = text.replace(a, b)
    text = apply_once(text, ECHO_EDGE_OLD, ECHO_EDGE_NEW, "echo_ws._edge 提速")
    text = apply_once(text, ECHO_CONTOUR_OLD, ECHO_CONTOUR_NEW, "echo_ws.contour_rgb 提速")
    return text


def build_proto(text):
    for a, b in TERMS:
        text = text.replace(a, b)
    return text


BAD_WORDS = ("板端", "板子", "板上", "服务端", "板卡地址")


def report_words(text, name):
    bad = [w for w in BAD_WORDS if w in text]
    if not bad:
        return
    print("WARN %s：这些词还在（多半是术语改写后的新组合，检查一下）：%s" % (name, "、".join(bad)))
    for w in bad:
        for i, line in enumerate(text.splitlines(), 1):
            if w in line:
                print("  %-6s %5d  %s" % (w, i, line.strip()[:110]))


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    pc_dir, out_dir = argv[1], argv[2]
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    for name, fn in (("workstation.py", build_workstation),
                     ("echo_ws.py", build_echo_ws),
                     ("proto.py", build_proto)):
        src = os.path.join(pc_dir, name)
        dst = os.path.join(out_dir, name)
        with io.open(src, encoding="utf-8") as f:
            text = f.read()
        n0 = len(text)
        out = fn(text)
        with io.open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(out)
        print("OK %-16s %7d -> %7d 字节  (%s)" % (name, n0, len(out), dst))
        report_words(out, name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
