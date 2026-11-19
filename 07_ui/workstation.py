# -*- coding: utf-8 -*-
"""工作站单文件版 —— 心超工作站 + 眼底平台扩展（自包含，不 import 任何本地模块）。

    本文件由 18 个源模块机械合并而成（见文件末尾的"来源"清单），行为与原工程一致：
        python workstation.py                 # 心超工作站（默认）
        python workstation.py <host> <port>   # 指板卡地址
        python workstation.py --fundus        # 直接开眼底那套（原 app.py）

    为什么放这个目录：资产是按 __file__ 相对定位的（demo/、reports/、samples/、
    live_sources.json，以及 ../../ 下的 fpga-echo-workstation）。所以
      * **必须**放在 tcp_image_ui/ 里跑；
      * 想放到别处，就把环境变量 PLD_WS_DIR 指到 fpga-echo-workstation 目录。

    合并时做的三件事：① 丢掉每个模块的 `__main__` 测试入口；② 丢掉模块之间的 import；
    ③ 把 17 处同名不同义的常量/函数改名（APP_TITLE→APP_TITLE_FUNDUS、
    FONT_EF→FONT_EF_SNAP、SUBTITLE→SUBTITLE_PLATFORM、_self_check→_self_check_interactive、
    app.main→main_fundus）。
"""


import array
import base64
import binascii
import csv
import gzip
import json
import math
import os
import queue
import re
import socket
import struct
import sys
import threading
import time
import webbrowser
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageTk


# pythonw（「启动上位机.bat」默认就用它）没有控制台：sys.stdout / sys.stderr 是 None，
# 任何 print / write 都抛 AttributeError —— 在线程里就等于"静默死掉、界面卡死在忙状态"。
# 这里给个黑洞：这类崩溃降级成"没有输出"；配合把 _fix_worker 整段包进 try，双保险。
class _NullIO(object):
    def write(self, *a):
        pass

    def flush(self):
        pass

    def isatty(self):
        return False


if sys.stderr is None:
    sys.stderr = _NullIO()
if sys.stdout is None:
    sys.stdout = _NullIO()


# WS_OPT_V1 惰性模块代理：把 import 推迟到第一次真正用到。
# scipy.ndimage 光导入就要 ~330 ms（-X importtime 实测），而它只在算 EF 几何时用；
# 启动路径上完全用不着，没必要让用户等。
class _LazyModule(object):
    """只做属性转发，第一次取属性时才真的 import。"""

    __slots__ = ("_name", "_mod")

    def __init__(self, name):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_mod", None)

    def _target(self):
        mod = object.__getattribute__(self, "_mod")
        if mod is None:
            import importlib
            mod = importlib.import_module(object.__getattribute__(self, "_name"))
            object.__setattr__(self, "_mod", mod)
        return mod

    def __getattr__(self, key):
        return getattr(self._target(), key)

    def __repr__(self):
        return "<lazy module " + repr(object.__getattribute__(self, "_name")) + ">"


ndimage = _LazyModule("scipy.ndimage")


def find_contours(*a, **kw):
    """skimage.measure.find_contours 的惰性包装（同上，只有量直径时才用）。"""
    from skimage.measure import find_contours as _f
    return _f(*a, **kw)
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ── 资产根目录：本文件所在目录（demo/、reports/）+ 上两级的 fpga-echo-workstation ──
HERE = os.path.dirname(os.path.abspath(__file__))
PLD_DIR = os.environ.get("PLD_WS_DIR") or HERE      # 打包版：资产就在本目录下


def _log_exc(where):
    """把异常追加到 ws.log —— Tk 回调里的异常会被 Tk 自己吞掉，只能自己留证据。"""
    import traceback
    try:
        msg = "[%s] %s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), where, traceback.format_exc())
    except Exception:
        msg = "%s: traceback failed\n" % where
    try:
        with open(os.path.join(PLD_DIR, "ws.log"), "a", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass
    try:
        sys.stderr.write(msg)
    except Exception:
        pass


def _log_line(msg):
    """往 ws.log 追加一行（卡死时至少能看出跑到哪一步）。"""
    try:
        with open(os.path.join(PLD_DIR, "ws.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# ── 合并后，原来 import 的那些本地模块就是本文件自己 ──
_SELF = sys.modules[__name__]
_efs = _SELF
appmod = _SELF
bigwin = _SELF
ch = _SELF
cm = _SELF
confidence = _SELF
ef = _SELF
efs = _SELF
heartapp = _SELF
heartview = _SELF
imageio = _SELF
imagelink = _SELF
interactive = _SELF
jobs = _SELF
platformwin = _SELF
proto = _SELF
sequence = _SELF
snapview = _SELF
te = _SELF
uistyle = _SELF



# ══════════════════════════════════════════════════════════════════════════
# 来源：clinical_hints.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""clinical_hints.py —— 把测量值翻成「提示方向」：只说"可能与哪类问题有关、建议复核什么"，不作诊断。

心超工作站的三处共用：关键帧 / 现场模式结果卡片（heartview）、快照模式测量卡片（snapview）、证据报告（echo_ws.write_report）。
每条提示 = (级别, 文字)：2 = 建议复核（橙），1 = 注意，0 = 未见需要提示的异常（只在一条都没触发时给）。
判据（出处写在文字里）：
  ESC 2021 心衰指南：LVEF ≤40 / 41–49 / ≥50；HFpEF 要 EF 保留 + 结构 / 功能异常（左房扩大、左室肥厚等）。
  ASE 2015（Lang 等）：LAVI > 34 mL/m² 左房扩大；LVMI 上限 女 95 / 男 115 g/m²；双平面 EDV 上限 女 106 / 男 150 mL，
    EDV 指数上限 女 61 / 男 74 mL/m²（Table 1）。RWT 只显示数值（口径不同，项目 10-18 决定不套 0.42）。
  本系统门限（只在训练 / 验证 / 开发集上定）：80 % 区间跨界复核、节段运动减低、RR 不规则。
  右心：文献多用 A4C 的 RV/LV 直径比 > 1.0；我们是面积比，只作提示。
仅用于工程教学与算法演示，不作为临床诊断依据。
"""

EDV_UPPER = {"F": (106.0, 61.0), "M": (150.0, 74.0)}      # ASE 2015 Table 1，双平面：EDV mL、EDV 指数 mL/m² 上限
LAVI_LIMIT = 34.0
LVMI_LIMIT = {"F": 95.0, "M": 115.0}
LAD = (0, 1, 2, 3)                                          # HMC-QU 6 节段：间隔 3 段 + 心尖侧壁 -> 多对应前降支
LCX = (4, 5)                                                # 中段 / 基底段侧壁 -> 多对应回旋支


def ef_hint(ef):
    if ef is None:
        return None
    if ef <= 40:
        return (2, "LVEF %.0f%% ≤ 40%%：收缩功能降低（HFrEF 范围，ESC 2021）→ 常见于心梗后、扩张型心肌病，建议复核并结合病因"
                % ef)
    if ef < 50:
        return (2, "LVEF %.0f%% 在 41–49%%：收缩功能轻度降低（HFmrEF 范围，ESC 2021）→ 建议复核、结合症状" % ef)
    return None


def echo_hints(ef, review=False, hr=None, irregular=False, wall_motion=None, right_heart=None, light=None, names=None):
    """关键帧 / 现场模式（只有 A4C、没有毫米尺度）：EF、复核、心率 / 节律、节段运动、右心比值、图像质量。"""
    out = []
    h = ef_hint(ef)
    if h:
        out.append(h)
    if review:
        out.append((2, "EF 的 80% 区间跨过 40% / 50% 分界：分型待定，建议人工复测（本系统，验证集校准）"))
    if wall_motion and any(wall_motion.get("seg") or []):
        seg = wall_motion["seg"]
        low = [names[k] if names else str(k) for k, f in enumerate(seg) if f]
        terr = []
        if any(seg[k] for k in LAD):
            terr.append("间隔 / 心尖段多对应前降支")
        if any(seg[k] for k in LCX):
            terr.append("侧壁中段 / 基底段多对应回旋支")
        out.append((2, "节段运动减低（%s）→ 提示缺血性心脏病 / 心梗可能（%s），建议复核" % ("、".join(low), "；".join(terr))))
    rv = (right_heart or {}).get("rv_lv")
    if rv is not None and rv > 1.0:
        out.append((2, "RV/LV 面积比 %.2f > 1：右室相对扩大 → 急性肺栓塞、肺动脉高压、右室心梗等需鉴别（面积比，仅提示）" % rv))
    if irregular:
        out.append((1, "RR 间期不规则（由容积曲线推出，无心电图）→ 可能房颤；EF 宜多拍平均"))
    # 心率不出提示：它由容积曲线和视频文件头的帧率推出，没有心电图；HMC-QU 官方 25 fps 而 AVI 头写 30 fps，
    # 按文件头算会偏高约 20 %（ES000108：129 对超声机屏上的 96），据此说"偏快"会误导（hr 参数保留，接采集卡用时间戳后再开）
    _ = hr
    if light == 2:
        out.append((1, "图像质量红灯：以上结果可靠性低，建议重新采图"))
    if not out:
        out.append((0, "未见需要提示的异常（LVEF ≥ 50%%，区间未跨界%s）；EF 保留不排除 HFpEF，需结合左房 / 左室质量（快照模式）"
                    % ("，节段 / 右心未达提示门限" if wall_motion or right_heart else "")))
    return out


def measure_hints(ef, edv=None, sex=None, bsa=None, la_ml=None, la_truncated=False, lvmi=None, mono=False):
    """快照模式（有毫米尺度）：EF、左室大小、左房、左室质量，以及 HFpEF 的结构证据组合。mono = 单平面（毫升未验证，不判大小）。"""
    out = []
    h = None if mono else ef_hint(ef)                   # 单平面 EF 是几何法原值、没校准：不据此分档，免得和关键帧模式打架
    if h:
        out.append(h)
    big = la_big = lvh = False
    if edv is not None and not mono and sex in EDV_UPPER:
        lim, limi = EDV_UPPER[sex]
        if bsa:
            big = edv / bsa > limi
            if big:
                out.append((2, "EDV 指数 %.0f mL/m² > %.0f（ASE 2015）：左室扩大 → 扩张型心肌病、瓣膜反流等容量负荷，建议复核"
                            % (edv / bsa, limi)))
        else:
            big = edv > lim
            if big:
                out.append((2, "EDV %.0f mL > %.0f（ASE 2015）：左室扩大 → 扩张型心肌病、容量负荷，建议复核" % (edv, lim)))
    if la_ml is not None and not la_truncated and bsa:
        lavi = la_ml / bsa
        la_big = lavi > LAVI_LIMIT
        if la_big:
            out.append((2, "LAVI %.0f mL/m² > 34（ASE 2015）：左房扩大 → 左室充盈压升高 / 舒张功能不全、房颤风险、二尖瓣病变" % lavi))
    if lvmi is not None and sex in LVMI_LIMIT and lvmi > LVMI_LIMIT[sex]:
        lvh = True
        out.append((2, "LVMI %.0f g/m² > %.0f（ASE 2015）：左室肥厚 → 高血压、主动脉瓣狭窄、肥厚型心肌病等（二维掩膜口径，仅提示）"
                    % (lvmi, LVMI_LIMIT[sex])))
    if ef is not None and ef >= 50 and (la_big or lvh) and not mono:
        out.append((2, "EF 保留但有结构异常（%s）→ 符合 ESC 2021 诊断 HFpEF 时要看的结构证据，需结合症状、利钠肽"
                    % "、".join(n for n, f in (("左房扩大", la_big), ("左室肥厚", lvh)) if f)))
    if ef is not None and ef <= 40 and big:
        out.append((2, "EF 降低 + 左室扩大：典型的射血分数降低型心衰表现"))
    if mono:
        out.append((1, "单平面（只有 A4C）：EF 是几何法原值、没校准，毫升值依赖屏幕标尺、没有真值核对 → "
                       "不据此分档、也不判断左室大小，以关键帧 / 现场模式的结论为准"))
    if not out:
        out.append((0, "未见需要提示的异常（EF 保留，左室大小 / 左房 / 左室质量未超 ASE 2015 上限）"))
    return out


def text(hints):
    """给界面文本框 / 报告：每条一行，前面用符号区分级别。"""
    mark = {2: "▲ ", 1: "△ ", 0: "○ "}
    return "\n".join(mark[k] + s for k, s in hints)


# ══════════════════════════════════════════════════════════════════════════
# 来源：jobs.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""jobs.py —— 病例任务（控制头 v7 "HM"，板端 src/job.c）：上传一次，整个病例在板上跑完。

和紧凑帧（v6c）的区别：紧凑帧是"PC 发一帧、板子算一帧"，编码、后处理、流程控制都在 PC；
病例任务是"PC 发一个序列（CT 文件、两帧图……），板上做完编码 → PL 推理 → 后处理 → 决策"，只回数值和掩膜。

请求：24 字节头（u16 0x4D48、u8 任务、u8 版本 1、u32 flags、u32 参数字节、u32 数据字节、u32 参数+数据的 CRC-32、u32 0）
      + 参数 + 数据。
回复：一串消息，每条 16 字节头（u16 0x524A、u8 类型、u8 0、u32 长度、u32 CRC-32、u32 序号）+ 负载：
      1 进度（JSON）、2 数据块（u8 名字长度、名字、u8 编码 0 原样 / 1 zlib、u32 原长、数据）、3 结果（JSON）、4 结束（u32 状态）。
板端在任务运行期间至少每 0.5 s 发一条进度，所以 PC 的套接字超时（15 s）不会误触发。

纯 Python（struct / zlib / json），不依赖 numpy；numpy 视图由调用方自己做。
"""
import json
import os
import struct
import time
import zlib

JOB_MAGIC = 0x4D48            # "HM"
MSG_MAGIC = 0x524A            # "JR"
JOB_VERSION = 1
JOB_CAPS, JOB_CT_SEG, JOB_REG_SEARCH, JOB_ECHO_EF, JOB_SIMPSON_EF, JOB_RENDER3D, JOB_OVERLAY = 0, 1, 2, 3, 4, 5, 6
MSG_PROGRESS, MSG_BLOB, MSG_RESULT, MSG_END = 1, 2, 3, 4

# JOB_CT_SEG flags（src/job_ct.c）。放大默认在 PL 的 pp_layer 上做（v5.14 起；之前默认 PS）；CT_PS_UPSAMPLE 改用 PS 工作线程
CT_LOGITS, CT_MASK_COARSE, CT_MASK_FINAL, CT_PS_UPSAMPLE, CT_INPUTS, CT_PL_UPSAMPLE = 1, 2, 4, 8, 16, 32
# v5.16：板上三维表面（surf3d.c，ct_volume.Surface 的 C 移植），回 "render3d" 图块 + 结果 "surface"；
# CT_SLICES 回窗宽窗位后的放射科视图各层 "slices_u8"（板上界面显示用）
CT_RENDER3D, CT_SLICES = 64, 128
# JOB_REG_SEARCH flags（src/job_reg.c）
REG_COSTS = 1
# JOB_ECHO_EF flags（src/job_echo.c）：LIVE = 每个分析帧回一个 "frame" 块 + 滚动 EF；PACE = 按视频自身帧率送帧（模拟探头）
# 视频只在 PC 上解码（OpenCV），上传灰度帧；板上解码（libjpeg）和 OpenCV 差几个灰度级，10-13 决定不做
# 最大块 + 填洞默认在 PL 的 pp_layer 上做（v5.14 起）；ECHO_PS_CLEAN 改用 PS 工作线程（v5.12/5.13 板端 bit2 的意思相反）
ECHO_LOGITS, ECHO_MASKS, ECHO_PS_CLEAN, ECHO_LIVE, ECHO_PACE = 1, 2, 4, 16, 32
# 部分 E 心肌应变（src/job_strain.c，服务端 v5.19 起）：清理默认在 PL 的 pp_layer；STRAIN_PS_CLEAN 改用 PS；
# STRAIN_C_KERNEL 追踪用纯 C 的 SAD 核（只为对照计时，结果相同）；STRAIN_LOGITS 多回两张 128 logit（板上核对用）
JOB_STRAIN = 7
STRAIN_PS_CLEAN, STRAIN_C_KERNEL, STRAIN_LOGITS = 1, 2, 4


class JobError(Exception):
    pass


def pack_request(kind, flags, params, data):
    body = bytes(params) + bytes(data)
    head = struct.pack("<HBBIIIII", JOB_MAGIC, kind, JOB_VERSION, flags, len(params), len(data),
                       zlib.crc32(body) & 0xFFFFFFFF, 0)
    return head, body


def _blob(payload):
    n = payload[0]
    name = payload[1:1 + n].decode("utf-8", "replace")
    enc = payload[1 + n]
    raw_len = struct.unpack_from("<I", payload, 2 + n)[0]
    data = payload[6 + n:]
    if enc == 1:
        data = zlib.decompress(data)
    if len(data) != raw_len:
        raise JobError("blob %s: %d bytes, expected %d" % (name, len(data), raw_len))
    return name, data


def run(link, kind, flags=0, params=b"", data=b"", on_progress=None, chunk=1 << 20, on_blob=None):
    """发一个任务并收完全部回复 -> dict(result, blobs, progress, status, upload_s, wall_s)。
    link 是 imagelink.ImageLink（用它的 _send_all / _recv_exact，连接断了会自动重连）。
    on_progress(dict) 在收到每条进度时调用（上传期间也会以 stage="upload" 调用）。
    on_blob(name, bytes) 返回 True 时这个块交给调用方处理、不存进 blobs（边跑边回的逐帧结果用它）。"""
    head, body = pack_request(kind, flags, params, data)
    t0 = time.perf_counter()
    link._send_all(head)
    for i in range(0, len(body), chunk):                 # 分块发：sendall 的超时是整次调用的总时长
        link._send_all(body[i:i + chunk])
        if on_progress is not None and len(body) > chunk:
            on_progress(dict(stage="upload", done=min(i + chunk, len(body)), total=len(body)))
    t_up = time.perf_counter() - t0
    out = dict(result=None, blobs={}, progress=[], status=None, upload_s=t_up)
    seq = 0
    while True:
        h = link._recv_exact(16)
        magic, typ, _, n, crc, s = struct.unpack("<HBBIII", h)
        if magic != MSG_MAGIC:
            raise JobError("bad reply magic 0x%04X (board server too old for case jobs?)" % magic)
        if s != seq:
            raise JobError("message %d out of order (expected %d)" % (s, seq))
        seq += 1
        p = link._recv_exact(n) if n else b""
        if zlib.crc32(p) & 0xFFFFFFFF != crc:
            raise JobError("message %d CRC mismatch" % s)
        if typ == MSG_PROGRESS:
            pr = json.loads(p.decode("utf-8"))
            out["progress"].append(pr)
            if on_progress is not None:
                on_progress(pr)
        elif typ == MSG_BLOB:
            name, d = _blob(p)
            if on_blob is None or not on_blob(name, d):
                out["blobs"][name] = d
        elif typ == MSG_RESULT:
            out["result"] = json.loads(p.decode("utf-8"))
        elif typ == MSG_END:
            out["status"] = struct.unpack("<I", p)[0]
            break
        else:
            raise JobError("unknown message type %d" % typ)
    out["wall_s"] = time.perf_counter() - t0
    if out["result"] is None:
        raise JobError("job ended without a result")
    return out


def caps(link):
    """板端支持哪些任务；老板端（v5.10 及以前）不认 "HM" 头，会直接断开 -> LinkError。"""
    return run(link, JOB_CAPS)["result"]


def ct_params(coarse=14, fine=15, level=40, width=400, target_mm=5):
    return struct.pack("<BBhHBB", coarse, 255 if fine is None else fine, level, width, target_mm, 0)


def ct_seg(link, path, coarse=14, fine=15, flags=CT_MASK_FINAL, on_progress=None, data=None, views=0, size=0):
    """整卷 CT 上传给板子，板上做完两阶段分割（fine=None 只做粗分割）。data 可直接给文件内容。
    flags 带 CT_RENDER3D 时（v5.16+）板上还渲染三维表面：views 个角度（0 = 每 30° 一张共 12 张）、size 边长（0 = 320）。"""
    if data is None:
        with open(path, "rb") as f:
            data = f.read()
    params = ct_params(coarse, fine) + (struct.pack("<BBH", views, 0, size) if flags & CT_RENDER3D else b"")
    return run(link, JOB_CT_SEG, flags, params, data, on_progress)


def unpack_render3d(r):
    """ct_seg / render3d 的结果 -> [每个角度一张 (size, size, 3) uint8]（老板端没有就是 []）。"""
    import numpy as np
    s = (r.get("result") or {}).get("surface")
    b = r["blobs"].get("render3d")
    if not s or b is None or "size" not in s:
        return []
    n = s["size"]
    a = np.frombuffer(b, np.uint8).reshape(-1, n, n, 3)
    return [a[i] for i in range(len(a))]


def render3d(link, mask, spacing, angles=(), size=320, step=2):
    """板上三维表面渲染（v5.16 JOB_RENDER3D）：mask [z,512,512] bool + 间距 (dx,dy,dz) mm -> 每个角度一张图。"""
    import numpy as np
    m = np.ascontiguousarray(mask, bool)
    params = struct.pack("<HHBBH3d", m.shape[0], size, len(angles), step, 0, *[float(v) for v in spacing])
    params += struct.pack("<%dd" % len(angles), *[float(a) for a in angles])
    r = run(link, JOB_RENDER3D, 0, params, np.packbits(m.ravel(), bitorder="little").tobytes())
    return unpack_render3d(r), r["result"]


# JOB_OVERLAY（v5.16）：PL（vis_layer）合成叠加图，没有 vis_layer 的板子在 PS 上按同一规格算（"engine" 说明在哪算的）
OVL_BASE_NONE, OVL_BASE_GREY, OVL_BASE_RGB, OVL_BASE_HEAT = 0, 1, 2, 3
OVL_HAS_LOGIT, OVL_HAS_BITS, OVL_FILL_FROM_BITS, OVL_FILL, OVL_UNSURE, OVL_CONTOUR = 8, 16, 32, 64, 128, 256


def overlay(link, ow, oh, flags, base=None, logits=None, bits=None, lut=None, thr=0, band=0, fill=(255, 0, 0), fill_a=16,
            unsure=(255, 200, 0), unsure_a=16, contour=(0, 255, 0), force_ps=False):
    """-> ((oh, ow, 3) uint8, result dict)。alpha 以 1/16 计（16 = 直接涂色，与 ai_view_overlay_rgb 相同）；
    logits [lh, lw] int8（最近邻放大）；bits [mh, mw] bool；lut [256, 3]（热力图，下标 logit + 128）。"""
    import numpy as np
    lh, lw = np.shape(logits) if logits is not None else (0, 0)
    mh, mw = np.shape(bits) if bits is not None else (0, 0)
    rgb = lambda c: c[0] | c[1] << 8 | c[2] << 16
    params = struct.pack("<16i", ow, oh, flags, lw, lh, mw, mh, thr, band, fill_a, unsure_a, rgb(fill), rgb(unsure),
                         rgb(contour), 1 if force_ps else 0, 0)
    data = b""
    if flags & 3 in (OVL_BASE_GREY, OVL_BASE_RGB):
        data += np.ascontiguousarray(base, np.uint8).tobytes()
    if flags & OVL_HAS_LOGIT:
        data += np.ascontiguousarray(logits, np.int8).tobytes()
    if flags & OVL_HAS_BITS:
        b = np.zeros((mh, (mw + 127) // 128 * 128), bool)
        b[:, :mw] = bits
        data += np.packbits(b, axis=1, bitorder="little").tobytes()
    if flags & 3 == OVL_BASE_HEAT:
        data += np.ascontiguousarray(lut, np.uint8).tobytes()
    r = run(link, JOB_OVERLAY, 0, params, data)
    return np.frombuffer(r["blobs"]["overlay"], np.uint8).reshape(oh, ow, 3), r["result"]


def unpack_mask(bits, shape):
    """位压缩掩膜（第 k 个体素 = 字节 k>>3 的第 k&7 位）-> numpy bool 数组。"""
    import numpy as np
    n = 1
    for s in shape:
        n *= s
    return np.unpackbits(np.frombuffer(bits, np.uint8), bitorder="little")[:n].reshape(shape).astype(bool)


def reg_params(mode=0, radius=7, model=5, steps=(4, 2, 1), engine=0, subpel=0, size=128):
    st = list(steps)[:4] + [0] * (4 - min(4, len(steps)))
    p = struct.pack("<BBBB4B", mode, radius, model, engine, *st)
    if subpel or size != 128:
        p += struct.pack("<BB", subpel, 1 if size == 256 else 0)
    return p


def _costs(tab):
    return {(struct.unpack_from("<b", tab, i)[0], struct.unpack_from("<b", tab, i + 1)[0]):
            struct.unpack_from("<d", tab, i + 2)[0] for i in range(0, len(tab), 10)}


def reg_search(link, a128, b128, mode=0, radius=7, model=5, steps=(4, 2, 1), costs=True, engine=0, subpel=0, size=128):
    """F5 配准搜索整个在板上做：A、B 两帧 128x128 灰度进，(dx, dy) 出。
    mode 0 = 三步搜索（steps），1 = 全搜索（±radius）。costs=True 时带回每个算过的候选的代价。
    engine=1（v5.16+，r2 固件）：平移、插值、代价和搜索全在 PL 的 vis_layer 上，整数结果与 engine=0 逐位相同；
    板子没有 vis_layer 时板端自动走老路，结果里 "engine" 写明实际走的哪条（老板端没有这个字段 = U-Net 老路）。
    subpel（1 = 半像素，2 = 1/4 像素）和 size=256 只有 PL 引擎支持；结果另有 "qx"/"qy"（1/4 像素）和 "costs_q"。"""
    r = run(link, JOB_REG_SEARCH, REG_COSTS if costs else 0, reg_params(mode, radius, model, steps, engine, subpel, size),
            bytes(a128) + bytes(b128))
    res = r["result"]
    res.setdefault("engine", "unet")
    if costs and "costs" in r["blobs"]:
        res["costs"] = _costs(r["blobs"]["costs"])
    if costs and "costs_q" in r["blobs"]:
        res["costs_q"] = _costs(r["blobs"]["costs_q"])
    res["wall_s"] = r["wall_s"]
    return res


def echo_params(conf, scale, model=16, realtime=False, nframes=0, fps=0.0, board_fps=25.0):
    """echo_ef 的 128 字节参数。conf = echo_ws.load_config()（逐拍分析参数、两套校准、质控门限来自训练集；
    最后 24 字节是临床测量的门限（复核边界宽度、RR 变异门限、多拍平均的拍数），在验证集上定。v5.14 及以前的板端只读前 104 字节）。"""
    import echo_ws
    c = conf["cfg"]
    thr = conf.get("qc_thresholds", (0.35, 0.6))
    cc = echo_ws.clinical_conf(conf, "realtime" if realtime else "full")
    return struct.pack("<6BHd14d", model, 0, 1 if realtime else 0, int(c["smooth"]),
                       1 if c["agg"] == "mean" else 0, 0, nframes, float(fps), c["dist_s"], c["prom"], c["win"],
                       conf["full"]["a"], conf["full"]["b"], conf["realtime"]["a"], conf["realtime"]["b"],
                       float(scale), float(thr[0]), float(thr[1]), float(board_fps),
                       float(cc["review_margin"]), float(cc["rr_cv_irregular"]), float(cc["min_beats_avg"]))


KF_KIND = {"full": 0, "hold": 1, "fixed": 2, "adaptive": 3}


def load_kf_config(mode="keyframe"):
    """echo_kf_config.json（E:/PLD/quality/make_kf_config.py 生成）里的一套：关键帧调度 + 跟踪参数 + 这套调度的 EF 校准
    + 质量灯（②）+ 保形区间（③）的全部数字。mode = "keyframe"（推荐档）或 "full"（每帧推理，同样出 ②③）。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "echo_kf_config.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)[mode]


def echo_kf_params(base, kf):
    """服务端 v5.21 起 echo_ef mode 2（src/job_echo.c job_echo_kf）：128 字节基本参数（mode 字节改成 2）+ 560 字节扩展
    "KF01"（布局见 job_echo.c）。kf = load_kf_config() 的一套。"""
    b = bytearray(base)
    b[2] = 2
    p, t = kf["policy"], kf["track"]
    win = p.get("win")
    ext = struct.pack("<I11i4d", 0x3130464B, KF_KIND[p["kind"]], int(p.get("n", 0)), int(p.get("n_max", 0)),
                      int(p.get("n_min", 1)), int(win) if win is not None and win >= 0 else -1, int(t["R"]), int(t["k"]),
                      int(t["reg"]), int(t.get("pad", 24)), 1, 0,
                      float(p["res"]) if p.get("res") else 0.0, float(t["spacing"]), float(kf["a"]), float(kf["b"]))
    m = kf["model"]
    vals = list(m["w"]) + list(m["mu"]) + list(m["sd"]) + [m["t_green"], m["t_red"]] + list(m["b_sigma"]) + \
        list(m["zmu"]) + list(m["zsd"]) + [m["q80"], m["q90"]]
    assert len(vals) == 60
    ext += struct.pack("<60d", *[float(v) for v in vals])
    assert len(ext) == 560
    return bytes(b) + ext


def load_heart_config():
    """echo_heart_config.json（E:/PLD/wallmotion/make_heart_config.py 生成）：节段室壁运动（HMC-QU DEV 拟合）+ 右心程序号 /
    合理性门限。没有文件时回 None。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "echo_heart_config.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


HEART_WM, HEART_RH = 1, 2

# 右心「触边判定」**已按用户要求整体关闭**（2026-11-19）：HC01 末 8 字节 border_px 固定写 0 = 不查，
# 右室掩膜贴到扇形边界也照样报比值、画轮廓。板上/服务端仍认这个字段，客户端不再给非零值，
# 界面上的档位下拉也一并删掉了（要恢复得同时改 heart_block 与 _build_middle）。
BORDER_PX_OFF = 0.0


def heart_block(hc, flags=HEART_WM | HEART_RH):
    """服务端 v5.24 起 mode 2 / 3 可选的 176 字节 "HC01"（布局见 src/cardiac_ext.h）：接在 KF01（mode 2）或 LV01（mode 3）后面。
    末 8 字节 border_px 固定写 0（2026-11-19 起「触边判定」整体关闭）：右室掩膜贴到扇形边界也照常报比值、画轮廓。"""
    w = hc["wall_motion"]
    r = hc["right_heart"]
    b = struct.pack("<IBBBB", 0x31304348, int(flags), int(r["rv_model"]), int(r["ra_model"]), 0)
    b += struct.pack("<6d6dd4ddddd", *[float(v) for v in w["mu"]], *[float(v) for v in w["sd"]], float(w["t_seg"]),
                     *[float(v) for v in w["w"]], float(w["t_rec"]), float(r["min_frac"]), float(r["max_overlap"]),
                     BORDER_PX_OFF)
    assert len(b) == 176
    return b


def unpack_rh(d, pairs):
    """"rh_masks" 块 -> [(RV 舒张末, RV 收缩末, RA 收缩末) bool [112,112] 掩膜] * pairs。"""
    import numpy as np
    m = np.unpackbits(np.frombuffer(d, np.uint8), bitorder="little").reshape(-1, 112, 112).astype(bool)
    return [tuple(m[3 * k:3 * k + 3]) for k in range(pairs)]


def unpack_frame(d):
    """"frame" 块 -> dict(t, vol, ncomp, area, border, unc, mask=112*112/8 字节的位掩膜)。"""
    t, vol, ncomp, area, border, unc = struct.unpack_from("<Idiiid", d, 0)
    return dict(t=t, vol=vol, ncomp=ncomp, area=area, border=bool(border), unc=unc, mask=d[32:32 + 112 * 112 // 8])


def echo_ef(link, conf, scale, frames, fps, model=16, realtime=False, flags=ECHO_MASKS, on_progress=None,
            on_frame=None, keyframe=None, heart=None):
    """整段心超在板上分析：frames = PC 解码的 [T,112,112] uint8（numpy 或 bytes），fps = 帧率。
    on_frame(dict) 收到每个分析帧的结果（要 flags 带 ECHO_LIVE）。回 jobs.run 的结果。
    keyframe = load_kf_config() 的一套时走 mode 2（v5.21：关键帧调度 + 质量灯 + 保形 EF 区间，结果多 "keyframe" /
    "key" / "quality" / "interval"；每帧都分析，realtime 不起作用；mode 2 不发 "frame" 块）。
    heart = load_heart_config()（只配 keyframe 用，板端 v5.24+）：结果多 "wall_motion" / "right_heart"，blobs 多 "rh_masks"。"""
    data = bytes(frames) if not hasattr(frames, "tobytes") else frames.tobytes()
    params = echo_params(conf, scale, model, realtime, len(data) // (112 * 112), fps)
    if keyframe is not None:
        params = echo_kf_params(params, keyframe)
        if heart is not None:
            params += heart_block(heart)

    def blob(name, d):
        if name == "frame" and on_frame is not None:
            on_frame(unpack_frame(d))
            return True
        return False
    return run(link, JOB_ECHO_EF, flags, params, data, on_progress, on_blob=blob)


LIVE_FRAMES112, LIVE_TRACE, LIVE_FRAME, LIVE_NATIVE = 1, 2, 4, 8   # mode 3 输出：结束时回 112 帧、每帧时间戳；
# v5.23 起每个 live 块带本帧；v5.28 起再带裁剪方块的原分辨率帧（8）+ 一块整屏 "still"（定标用）
LIVE_STAMPS = ("captured", "ready", "start", "work0", "work1", "end", "sent_prev")


def echo_live_params(base, kf, name, source="file", crop=None, max_frames=4096, detect_frames=50, thr=8, every=1,
                     out_flags=LIVE_FRAMES112 | LIVE_TRACE, v4l2=(640, 480, 60.0)):
    """服务端 v5.22 起 echo_ef mode 3（src/job_echo.c job_echo_live）：mode 2 的 688 字节 + 112 字节 "LV01"。
    source "file"：name = 板上 /root/echo_src 里的 .y8；"v4l2"：name = "video0" 等，v4l2 = 想要的 (W, H, fps)。
    crop = (x, y, 边长) 固定裁切，None = 前 detect_frames 帧自动找运动方块（门限 thr）。every = 每几帧回一个 "live" 块。"""
    b = bytearray(echo_kf_params(base, kf))
    b[2] = 3
    cx, cy, cs = crop if crop else (0, 0, 0)
    nm = name.encode("ascii")
    assert len(nm) < 64
    lv = struct.pack("<IBBHHHHH3iHHdII64s", 0x3130564C, {"file": 0, "v4l2": 1}[source], 1 if crop else 0,
                     int(max_frames), int(detect_frames), int(thr), int(every), 0, int(cx), int(cy), int(cs),
                     int(v4l2[0]), int(v4l2[1]), float(v4l2[2]), int(out_flags), 0, nm)
    assert len(lv) == 112
    return bytes(b) + lv


ROLL = dict(win=50, every=10, view_every=25, view_n=3, view_t=0.27)   # v5.25：灯每 10 帧（50 fps 下 0.2 s）更新；每 5 帧时板上 DP 界面同时在跑，P99 到 83 ms


def roll_block(win=50, every=5, view_every=25, view_n=3, rv_model=0, view_t=0.27, chain=0, f1_every=0, f1_model=0,
               camus=None):
    """服务端 v5.25 起 mode 3 可选的 32 字节 "RQ01"（布局见 src/job_echo.c）：接在 LV01（有 HC01 时接在 HC01）后面。
    滚动质量灯：每 every 帧算最近 win 帧；切面：每 view_every 帧跑一次右室程序（rv_model 0 = HC01 的），
    最近 view_n 次 右室 / 左室 面积比的中位数 >= view_t 判四腔（门限在 CAMUS TRAIN 上定，E:/PLD/quality/scan_sim）。
    v5.26：chain=1 走 CAMUS 口径（caps "live_roll_camus"；只用于 CAMUS 这类画面的源，见 ROLL_CAMUS）：每 f1_every 帧在 PL
    上跑一次 F1 左室程序（f1_model），后面接 248 字节 "CQ01" = camus 模型（w[10] / mu[9] / sd[9] / t_green / t_red）；
    camus["hold_thr"] > 0（灰度级）= 判定后保持：灯定了就停 F1 / 右室程序，1 s 平均画面变化超过门限（探头动了）再重判。"""
    b = struct.pack("<IHHHHidIHH", 0x31305152, int(win), int(every), int(view_every), int(view_n), int(rv_model),
                    float(view_t), int(chain), int(f1_every), int(f1_model))
    assert len(b) == 32
    if chain in (1, 2):           # 2 = 1 + F1 关键帧引擎（11-02）
        m = camus
        assert len(m["w"]) == 10 and len(m["mu"]) == 9 and len(m["sd"]) == 9
        c = struct.pack("<II10d9d9ddd", 0x31305143, int(round(1000 * float(m.get("hold_thr", 0.0)))), *map(float, m["w"]), *map(float, m["mu"]), *map(float, m["sd"]),
                        float(m["t_green"]), float(m["t_red"]))
        assert len(c) == 248
        b += c
    return b


def load_roll_camus(path=None):
    """v5.26 CAMUS 口径的滚动质量灯 + 切面（E:/PLD/quality/live_camus/fit_dll.py 用板上同一份 C 在 CAMUS TRAIN 上定的
    标准化，权重 / 门限 = quality_camus.json portable；VAL 区分 Poor AUC 见文件 "val"）。返回 echo_live(roll=...) 用的 dict。
    只给 CAMUS 这类画面的源（live/src/index.json 里 "quality": "camus"）：F1 分不好 EchoNet 画面（Dice 0.47）。
    11-02："engine": "f1" -> chain 2，现场引擎的关键帧也用 F1，EF 用 ef_a / ef_b 校准（CAMUS TRAIN，quality/live_camus/f1_engine.py），
    发送时要把 KF01 的 a / b 换成这两个（heartview 里做）。"""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "camus_live_model.json")
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    return dict(win=m["win"], every=m["every"], view_every=m["view_every"], view_n=m["view_n"], rv_model=m["rv_model"],
                view_t=m["view_t"], chain=2 if m.get("engine") == "f1" else 1, f1_every=m["f1_every"],
                f1_model=m["f1_model"], camus=m)   # chain 2：关键帧也用 F1（caps "live_f1_engine"）


def unpack_live(d):
    """"live" 块 -> dict(t, key, vol, stamps={名字: 任务开始后的 µs}, mask=112*112/8 字节的位掩膜,
    frame=112*112 字节的本帧或 None（要 LIVE_FRAME，板端 v5.23+）,
    roll_light=滚动质量灯 0/1/2 或 None（还没出 / 没开 RQ01）, view=2 四腔 / 1 非四腔 / 0 还没判断, roll_score,
    hold=True：v5.26 CAMUS 口径已判定、探头未动，暂停判断）。"""
    t, key, rl, vw, hold, vol = struct.unpack_from("<IBBBBd", d, 0)
    st = struct.unpack_from("<7I", d, 16)
    score = struct.unpack_from("<f", d, 44)[0]
    m0 = 48 + 112 * 112 // 8
    fr = d[m0:m0 + 112 * 112] if len(d) >= m0 + 112 * 112 else None
    n0 = m0 + (112 * 112 if fr is not None else 0)     # v5.28 bit3：尾部 u32 side, u32 n, n*n 字节原生裁剪帧
    nat = None
    if len(d) >= n0 + 8:
        side, n = struct.unpack_from("<II", d, n0)
        if 0 < n <= 2048 and len(d) >= n0 + 8 + n * n:
            nat = (int(side), int(n), d[n0 + 8:n0 + 8 + n * n])
    return dict(t=t, key=bool(key), vol=vol, stamps=dict(zip(LIVE_STAMPS, st)), mask=d[48:m0],
                frame=fr, native=nat,
                roll_light=rl - 1 if rl else None, view=vw, roll_score=score, hold=bool(hold))


def unpack_still(d):
    """v5.28 的 "still" 块：一块整屏（定标要看裁剪方块外面的深度刻度）。
    24 字节头 = W, H, 裁切方块 x, y, 边长（都按源像素），后面 W*H 字节灰度。"""
    w, h, x, y, side = struct.unpack_from("<IIIII", d, 0)
    return dict(W=int(w), H=int(h), crop=dict(x=int(x), y=int(y), side=int(side)), luma=d[24:24 + w * h])


def echo_live(link, conf, scale, kf, name, source="file", crop=None, model=16, on_live=None, on_progress=None, heart=None,
              roll=None, **kw):
    """现场模式（服务端 v5.22 echo_ef mode 3）：帧来自板上的信号源（.y8 文件按自身帧率回放 / 采集卡），PC 不上传帧。
    板上：前 detect_frames 帧找运动方块 -> 只留方块、整数盒滤波缩到 112 -> mode 2 同一套引擎逐帧跑。
    on_live(dict) 收每个 "live" 块（unpack_live）；进度里 stage="live" 带滚动 EF（已校准）/ 心率 / 延迟。
    结果 = mode 2 的全部字段 + "stream"（源、裁切、帧数、延迟）；blobs 可含 "frames112"、"trace"（每帧 7 个 u32 时间戳）。
    heart = load_heart_config()（板端 v5.24+）：同 echo_ef，多 "wall_motion" / "right_heart" / "rh_masks"。
    roll = dict（如 ROLL，板端 v5.25+ caps "live_roll"）：每个 live 块带滚动质量灯 / 切面，结果多 "rolling"。"""
    params = echo_live_params(echo_params(conf, scale, model, False, 0, 50.0), kf, name, source, crop, **kw)
    if heart is not None:
        params += heart_block(heart)
    if roll is not None:
        params += roll_block(**roll)

    def blob(n, d):
        if n == "live" and on_live is not None:
            on_live(unpack_live(d))
            return True
        if n == "still" and on_live is not None:      # v5.28：整屏一块（定标用），t = -1 表示不是某一帧
            on_live(dict(t=-1, still=unpack_still(d)))
            return True
        return False
    return run(link, JOB_ECHO_EF, ECHO_MASKS, params, b"", on_progress, on_blob=blob)


def simpson_ef(link, masks, spacing_2ch, spacing_4ch, out_sizes=None, clean=False, on_progress=None, patient=None):
    """F4 双平面 Simpson 在板上算（板端 src/job_simpson.c，与 ef_simpson.volumes 逐位一致）。
    masks = 4 个 bool 掩膜，顺序 2CH ED, 2CH ES, 4CH ED, 4CH ES；spacing = (高, 宽) mm；
    out_sizes = 4 个 (w, h)：板上先按 Pillow NEAREST 缩放到这个尺寸（界面里 512 掩膜回原图尺寸），None = 不缩放；
    clean = 板上先做最大连通块 + 填洞（预测掩膜要，专家掩膜不要）。
    v5.16（P1 1.6）：masks / out_sizes 可以是 8 个，后 4 个依次 2CH ED 心外膜、4CH ED 心外膜、2CH ES 左房、4CH ES 左房，
    结果多 "cardiac"（左房截断 / 容积 / 校准、心外膜 / 心内膜容积、左室质量、壁厚；与 cardiac_measure.measure 逐位一致）。
    部分 A（下一版服务端）：cardiac 再多 lvidd_mm / rwt；patient = (身高 cm, 体重 kg, 性别 'F' / 'M' / None) 时
    再多 bsa_m2 / lvmi / lvmi_class（LVMI 增高与否，cardiac_measure.lvmi_class；RWT 只给数值）。旧服务端忽略多出的参数。"""
    params, data = simpson_request(masks, spacing_2ch, spacing_4ch, out_sizes, clean, patient)
    return run(link, JOB_SIMPSON_EF, 0, params, data, on_progress)


def simpson_request(masks, spacing_2ch, spacing_4ch, out_sizes=None, clean=False, patient=None):
    import numpy as np
    data = bytearray()
    for k, m in enumerate(masks):
        m = np.asarray(m, bool)
        h, w = m.shape
        ow, oh = out_sizes[k] if out_sizes else (w, h)
        data += struct.pack("<4HB3x", w, h, ow, oh, 1 if clean else 0)
        data += np.packbits(m.ravel(), bitorder="little").tobytes()
    params = struct.pack("<4d", spacing_2ch[0], spacing_2ch[1], spacing_4ch[0], spacing_4ch[1])
    if patient is not None and patient[0] and patient[1]:      # f64 身高 cm, f64 体重 kg, u8 性别 (0 / 1 F / 2 M), 7x
        params += struct.pack("<2dB7x", float(patient[0]), float(patient[1]), {"F": 1, "M": 2}.get(patient[2], 0))
    return params, bytes(data)


def strain_params(sx, sy, lv_model=2, epi_model=18):
    """f64 sx, f64 sy（512 网格上每像素 mm：原图宽 × 像素间距 x / 512，高同理），u32 左室模型、u32 心外膜模型、8 字节 0"""
    return struct.pack("<2d4I", float(sx), float(sy), lv_model, epi_model, 0, 0)


def strain(link, frames512, sx, sy, flags=0, on_progress=None, lv_model=2, epi_model=18):
    """部分 E 心肌应变（探索性辅助测量，src/job_strain.c，服务端 v5.19 起）。
    frames512：T 帧 512x512 u8（上位机 512 帧的 R 通道），第 0 帧 = ED、最后一帧 = ES（CAMUS 半周期序列）。
    板上：ED 帧 [::4, ::4] 在 PL 上跑模型 2（F1 左室）和程序 18（心外膜）→ PL 清理 → PS 提心肌中线、逐帧块匹配追踪
    → GLS-like 曲线（"gls_pct"）、ES 值（原始 / TRAIN 偏移后）。blobs："points"（i32 T x n x 2，(y, x) Q8）、
    "line"（i32 中线像素 (y, x)）、"myo"（512 x 512 位图）。老服务端回 "unknown job"。"""
    data = b"".join(bytes(f) for f in frames512)
    return run(link, JOB_STRAIN, flags, strain_params(sx, sy, lv_model, epi_model), data, on_progress)


# ══════════════════════════════════════════════════════════════════════════
# 来源：proto.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""proto.py —— 图像传输协议（PC 端 GUI 用的自包含实现）。

本文件是**自包含**的：不 import tcp_image_rx 的任何东西，方便这个工具单独拷走使用。
里面每个常量都必须与板端 `tcp_image_rx/src/image_proto.h` 保持一致；
为了能立刻发现漂移，模块在导入时用标准校验向量自检 CRC16 与各字段算术。

协议（与 image_proto.h 一致）
----------------------------
 图像    : 512 x 512，RGB888（每像素 R,G,B 三字节），整幅 786 432 字节
 传输    : 切成 512 个"行包"，每包正好一行像素

 行包布局（全部小端）::

     偏移  长度  字段      说明
     ----  ----  --------  ------------------------------------------
      0     2    row       行号 0..511
      2     2    length    负载长度，固定 1536
      4     2    crc16     CRC-16/CCITT-FALSE，对后面 1536 字节负载计算
      6    1536  payload   该行 512 个像素，序 R,G,B

 单包 1542 字节，整幅 512 * 1542 = 789 504 字节。
"""

import binascii
import struct

# --------------------------------------------------------------------------
# 图像参数（对应 image_proto.h）
# --------------------------------------------------------------------------
IMG_WIDTH = 512
IMG_HEIGHT = 512
IMG_BYTES_PER_PIX = 3                      # RGB888
IMG_ROW_BYTES = IMG_WIDTH * IMG_BYTES_PER_PIX      # 1536
IMG_TOTAL_BYTES = IMG_ROW_BYTES * IMG_HEIGHT       # 786432

PKT_HDR_BYTES = 6
PKT_PAYLOAD_BYTES = IMG_ROW_BYTES
PKT_TOTAL_BYTES = PKT_HDR_BYTES + PKT_PAYLOAD_BYTES   # 1542
PKT_COUNT = IMG_HEIGHT                                # 512
STREAM_TOTAL_BYTES = PKT_TOTAL_BYTES * PKT_COUNT      # 789504

# --------------------------------------------------------------------------
# 控制头（v2 新增）：整幅图像流最前面 4 字节，用来告诉板子用哪种处理模式。
#
#     偏移  长度  字段    说明
#     ----  ----  ------  ------------------------------------
#      0     2    magic   固定 0x4D43（内存小端存放即 ASCII "CM"）
#      2     2    mode    处理模式 0..4，见下表
#
#   mode: 0 灰度归一化 / 1 阈值分割 / 2 边缘增强 / 3 伪彩色映射 / 4 组合 / 5 原图直通
#         （5 = 直通：板子不经 PL，直接把收到的原图回显回来）
#
# 板子回给 PC 的仍是**纯图像流**（512 个行包，不带控制头）—— PC 自己知道发的是哪个模式。
# --------------------------------------------------------------------------
CTRL_MAGIC = 0x4D43
CTRL_BYTES = 4
WIRE_TOTAL_BYTES = CTRL_BYTES + STREAM_TOTAL_BYTES    # 789508，v2 帧总长

# --------------------------------------------------------------------------
# 控制头 v3（2026-10-01 新增 flags / aiparam，8 字节）
#
#     偏移  长度  字段      说明
#     ----  ----  --------  ------------------------------------------
#      0     2    magic     固定 0x4D44（内存小端即 ASCII "DM"）
#      2     2    mode      预处理模式 0..5（语义同 v2）
#      4     2    flags     回传内容位图，见 FLAG_*
#      6     2    aiparam   AI 参数，见 AIPARAM_*
#
#  板子**先读 2 字节 magic 再决定读几字节**，所以发 v2 头的旧客户端照旧能用。
#  下行仍是图像流，但按 flags 可能：
#    * 先来一个 64 字节统计块（FLAG_STATS）
#    * 再来 0..3 个图像流，顺序固定 = 预处理图 -> 叠加图 -> 掩膜图
# --------------------------------------------------------------------------
CTRL_MAGIC_V3 = 0x4D44
CTRL_BYTES_V3 = 8
WIRE_TOTAL_BYTES_V3 = CTRL_BYTES_V3 + STREAM_TOTAL_BYTES

# --------------------------------------------------------------------------
# 控制头 v4（2026-10-02 新增逐帧阈值，12 字节）
#
#     偏移  长度  字段      说明
#     ----  ----  --------  ------------------------------------------
#      0     2    magic     固定 0x4D45（内存小端即 ASCII "EM"）
#      2     2    mode      预处理模式 0..5
#      4     2    flags     回传内容位图（同 v3）
#      6     2    aiparam   AI 参数（同 v3）
#      8     2    thr       阈值分割(mode 1)的门限 0..255
#     10     2    reserved  保留(写 0)
#
#  v2/v3 里阈值只是**板端命令行**选项，上位机够不着；"阈值分割"要按图调，
#  所以 v4 把它放进控制头。板子仍然是"先读 2 字节 magic 再决定读几字节"，
#  三种头都认。
#  注：伪彩映射(mode 3)在 PL 里是直接 jet[gray]，没有阈值参数，不在协议内。
# --------------------------------------------------------------------------
CTRL_MAGIC_V4 = 0x4D45
CTRL_BYTES_V4 = 12
WIRE_TOTAL_BYTES_V4 = CTRL_BYTES_V4 + STREAM_TOTAL_BYTES
CTRL_THR_DEFAULT = 128          # 不带 thr 的老头 / 板端 --thr 的默认值
CTRL_THR_MIN = 0
CTRL_THR_MAX = 255
_CTRL_FMT_V4 = "<HHHHHH"        # magic, mode, flags, aiparam, thr, reserved

# --------------------------------------------------------------------------
# 控制头 v5（2026-10-07，ROI 局部推理，20 字节）= v4 的 12 字节 + roi_x, roi_y, roi_w, roi_h
#   magic 0x4D46（内存小端即 ASCII "FM"）；roi_w 或 roi_h 为 0 = 不用 ROI。
#   有 ROI 时板端只在 roi_crop() 给出的裁剪窗口上跑整网，ROI 内结果贴回 512x512
#   （ROI 外 logit = -128），并且与整图推理逐值一致
#   （证明见 ../tcp_image_rx/ai/net/roi_exactness.py）。
#   只有统计块 version >= AI_STAT_VERSION_ROI 的板端认 v5 头。
# --------------------------------------------------------------------------
CTRL_MAGIC_V5 = 0x4D46
CTRL_BYTES_V5 = 20

# 控制头 v6c（紧凑帧，"GM"，16 字节，实时化）：头后面直接是 w*w*kind 字节负载（模型真正要的像素），
# 不走 PL 预处理；板端每个连接保存一份跟踪状态。只在统计块 version >= 7 的板端上用。
#   magic u16 | kind u8 (1 灰度 / 3 RGB) | track u8 (0 无 / 1 跟踪一步 / 2 清状态 / 3 结果设为状态)
#   | flags u16 | model0 u8 | model1 u8 | thr i8 | 保留 u8 | w u16 | h u16 | 负载 CRC16
# 回传：统计块（b3）-> 分块和块（b7）-> w*w 个 int8 logit + CRC16（b8）-> 位掩膜 w*w/8 + CRC16（b9）
CTRL_MAGIC_V6C = 0x4D47
CTRL_BYTES_V6C = 16
_CTRL_FMT_V6C = "<HBBHBBbBHHH"
V6C_KIND_GRAY = 1
V6C_KIND_RGB = 3
V6C_TRACK_NONE, V6C_TRACK_STEP, V6C_TRACK_RESET, V6C_TRACK_SET = 0, 1, 2, 3
V6C_MAX_EXTRA = 3            # 多程序流水线：程序 0 之外最多 3 个
WIRE_TOTAL_BYTES_V5 = CTRL_BYTES_V5 + STREAM_TOTAL_BYTES
_CTRL_FMT_V5 = "<HHHHHHHHHH"    # v4 的 6 个字段 + roi_x, roi_y, roi_w, roi_h

ROI_MARGIN = 24        # 板端 UNET_ROI_MARGIN：裁剪边界的影响最深 22 像素，留 24
ROI_ALIGN = 4          # 裁剪起点对齐（两级 2x2 池化）
ROI_SIZE_ALIGN = 8     # 裁剪尺寸对齐
ROI_OUTSIDE_LOGIT = -128

FLAG_ECHO_PROC = 0x0001     # b0 回传预处理图（默认，等价于旧行为）
FLAG_ECHO_MASK = 0x0002     # b1 回传 AI 血管掩膜图
FLAG_ECHO_OVER = 0x0004     # b2 回传掩膜叠加图（红色叠在预处理图上）
FLAG_STATS = 0x0008         # b3 图像流之前先来一个 64B 统计块
FLAG_NO_IMAGE = 0x0010      # b4 只做识别，不回传任何图像
FLAG_ECHO_LOGIT = 0x0020    # b5 回传 AI 原始 logit 图（排在掩膜之后，R=G=B=logit+128）
FLAG_MORPH = 0x0040         # b6 血管形态指标：统计块后多一个 64B 指标块（须带 b3），图像最后多一幅形态骨架视图
FLAG_C_LOGIT = 0x0100       # b8（紧凑帧）回 w*w 个 int8 logit + 2 字节 CRC16
FLAG_C_MASK = 0x0200        # b9（紧凑帧）回按位打包的掩膜（行优先，每字节低位在前）+ 2 字节 CRC16
FLAG_C_PP = 0x0400          # b10（紧凑帧）PL 后处理：多 12 字节请求，回 32 字节块 + 位掩膜（见 PP_*）
FLAG_REG = 0x0080           # b7 分块求和（F5 配准）：统计块 / 形态块之后多一个 96B 块，logit 图 4x4 分块的有符号和（须带 b3）
FLAG_DEFAULT = FLAG_ECHO_PROC

LOGIT_IMG_OFFSET = 128      # logit 图的偏移码：像素值 = logit + 128
AI_LOGIT_SCALE = 0.1017041844645823   # 输出量化 scale：概率 = sigmoid(logit * scale)

AIPARAM_SRC_RAW = 0         # AI 输入 = 收到的原始 RGB
AIPARAM_SRC_PROC = 1        # AI 输入 = PL 预处理后的结果
AIPARAM_THR_DEFAULT = -3    # logit >= thr 判为血管

# aiparam bits[4:2] = 工作分辨率档位: 分辨率 = 512 >> shift
#   0 = 512(全分辨率, 最慢最准) / 1 = 256 / 2 = 128 / 3 = 64 / 4 = 32
#   0 也是"不自选, 用板端 --ai-res 的默认值"
AIPARAM_RES_SHIFT = 2
AIPARAM_RES_MASK = 0x001C
AI_RES_CHOICES = (512, 256, 128, 64, 32)

# aiparam bits[7:5] = 模型（板端 src/unet_models.h 的模型库）：0 = 板端默认（--model，缺省眼底），
# k = 第 k-1 号模型。老板端不认这三位，一律按眼底跑。
AIPARAM_MODEL_SHIFT = 5
AIPARAM_MODEL_MASK = 0x00E0

# 模型库，顺序与板端 unet_models.c 一致（gen_unet_models.py 生成）。
#   thr   默认判定门限（logit >= thr）；res 默认分辨率；scale 输出量化 scale（概率 = sigmoid(logit*scale)）
#   roi   是否支持 ROI 局部推理（只有全分辨率训练的模型才有意义）；morph 形态指标是否有意义
#   inter 配套的点击纠错模型下标；hidden 不出现在界面下拉框
MODELS = (
    dict(index=0, name="fundus", label="眼底血管", target="血管", thr=-3, res=512,
         scale=0.1017041844645823, roi=True, morph=True),
    dict(index=1, name="skin", label="皮肤镜病灶", target="病灶", thr=1, res=128,
         scale=0.4723758547324834, roi=False, morph=False),
    dict(index=2, name="echo_lv", label="心超左心室", target="左心室", thr=1, res=128,
         scale=0.24494746350866603, roi=False, morph=False, inter=3, seq=4),
    # 点击纠错（F2）用的配套模型：输入是 interactive.compose() 拼的“图像 + 先验 + 点击”，
    # 不能直接拿原图跑，所以不出现在下拉框里（hidden），由「点击纠错」自动选用
    dict(index=3, name="echo_inter", label="心超点击纠错", target="左心室", thr=1, res=128,
         scale=0.4401192552461399, roi=False, morph=False, hidden=True),
    # 序列传播（F3）：输入是 sequence.py 拼的“本帧 + 上一步结果 + B=128”（enc="none"，与无点击的 compose 相同），
    # 由「序列跟踪」窗口自动选用
    dict(index=4, name="echo_seq", label="心超序列传播", target="左心室", thr=1, res=128,
         scale=0.5600521658349225, roi=False, morph=False, hidden=True, enc="none"),
    # 配准代价（F5）：手工构造的参数，logit ≈ |R - G| / 2；16 个候选平移拼成 4x4 一帧，PS 分块求和（FLAG_REG）。
    # 由「连续帧配准」自动选用；门限 8 = |a - b| >= 16 算“变化像素”
    dict(index=5, name="reg_absdiff", label="配准代价 |a-b|", target="变化像素", thr=8, res=512,
         scale=1.0 / 127, roi=False, morph=False, hidden=True),
    # ② 编译出来的算子程序（E:/PLD/opcomp）：输出 = logit * scale + mid（数值在 out_range 内，1.0 = 255 灰度）。
    # 7 号以上只能经紧凑帧调用（aiparam 的模型字段只有 3 位）
    dict(index=6, name="op_median3", label="三帧中值", target="去斑", thr=0, res=128, scale=0.003937007874015748, mid=0.5,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 1.0]),
    dict(index=7, name="op_mean3", label="三帧均值", target="平均", thr=0, res=128, scale=0.003937007874015748, mid=0.5,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 1.0]),
    dict(index=8, name="op_sobel", label="Sobel 梯度", target="边缘", thr=0, res=128, scale=0.007874015748031496, mid=1.0,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 2.0]),
    dict(index=9, name="op_gauss11", label="11×11 高斯", target="模糊", thr=0, res=128, scale=0.003937007874015748, mid=0.5,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 1.0]),
    dict(index=10, name="op_unsharp", label="反锐化增强", target="增强", thr=0, res=128, scale=0.003937007874015748, mid=0.5,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 1.0]),
    dict(index=11, name="op_motion", label="三帧运动能量", target="运动", thr=0, res=128, scale=0.007874015748031496, mid=1.0,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 2.0]),
    dict(index=12, name="op_change", label="变化检测", target="变化", thr=0, res=128, scale=0.003937007874015748, mid=0.5,
         roi=False, morph=False, hidden=True, op=True, out_range=[0.0, 1.0]),
    dict(index=13, name="op_vessel4", label="线检测（血管增强）", target="血管响应", thr=0, res=128, scale=0.007874015748031496, mid=0.0,
         roi=False, morph=False, hidden=True, op=True, out_range=[-1.0, 1.0]),
    # ⑤ CT 脾脏（E:/PLD/ct）：输入是 ct_volume.payloads 拼的“上方约 5 mm / 本层 / 下方约 5 mm”三层（W400/L40，4x4 块平均到 128），
    # 只能经紧凑帧调用；由「CT 整卷分割」窗口自动选用
    dict(index=14, name="ct_spleen", label="CT 脾脏", target="脾脏", thr=1, res=128, scale=0.6008696668730007,
         roi=False, morph=False, hidden=True, enc="z3"),
    # ⑤ 第二阶段 ROI 精分割：按 14 号的结果定 256x256 框，框内 2x2 块平均到 128；R = 本层，G = 粗结果掩膜，B = 上下层平均
    # （ct_volume.roi_payloads）。「CT 整卷分割」窗口在粗分割后自动接着跑
    dict(index=15, name="ct_spleen_roi", label="CT 脾脏 ROI 精分割", target="脾脏", thr=1, res=128, scale=4.15080825925812,
         roi=False, morph=False, hidden=True, enc="roi_prior"),
    # ③ EchoNet-Dynamic 左心室：112x112 灰度补边到 128，R / G / B = 前约 40 ms / 本帧 / 后约 40 ms（echo_ws.payloads），
    # 输出裁回 112；由「实时心超工作站」窗口自动选用
    dict(index=16, name="echonet_lv", label="心超左心室（EchoNet）", target="左心室", thr=1, res=128,
         scale=0.13784920699953093, roi=False, morph=False, hidden=True, enc="t3"),
)


def model_by_index(index):
    """模型表里的一项；index 为 None 或越界时返回眼底（老板端只有眼底）。"""
    if index is None or not 0 <= index < len(MODELS):
        return MODELS[0]
    return MODELS[index]


def res_to_shift(res):
    """512 -> 0, 256 -> 1, ... 32 -> 4。不合法返回 None。"""
    if res not in AI_RES_CHOICES:
        return None
    return {512: 0, 256: 1, 128: 2, 64: 3, 32: 4}[res]


def shift_to_res(shift):
    return 512 >> shift

AI_STAT_BYTES = 64
AI_STAT_MAGIC = 0x31414953   # 小端字节 'S','I','A','1'
AI_STAT_VERSION = 8          # 板端 ai_view.h 同名常量
AI_STAT_VERSION_LOGIT = 2    # 统计块 version >= 2 的板端才会回传 logit 图
AI_STAT_VERSION_ROI = 3      # 统计块 version >= 3 的板端才认 v5 头（ROI）；
                             # ROI 帧的 reserved = (裁剪高 << 16) | 裁剪宽，total = ROI 面积
AI_STAT_VERSION_MORPH = 4    # 统计块 version >= 4 的板端才认 b6（形态指标）
AI_STAT_VERSION_MODEL = 5    # 统计块 version >= 5 的板端有模型库；flags bits[23:16] = 模型下标 + 1
AI_STAT_VERSION_REG = 6      # 统计块 version >= 6 的板端认 b7；真的跟着分块求和块时，回显的 flags 里 b7 才是 1
AI_STAT_VERSION_COMPACT = 7  # 统计块 version >= 7 的板端认紧凑帧（v6c）、板端跟踪状态、b8 / b9
AI_STAT_VERSION_PP = 8       # 统计块 version >= 8 的板端认 b10（PL 后处理）；紧凑帧 reserved 的 bit2 = 这块板能做

# PL 后处理（b10，板端 image_proto.h FLAG_C_PP / ai_view.h PP_BLOCK_*，固件 xu2dp_e2b_pp 的 pp_layer）
# 请求：程序列表之后、负载之前 12 字节 = u8 op | u8 k | i8 thr | i8 tn | u16 x0, y0, cw, ch（cw = 0 表示整帧）
# 回传排在最后：32 字节块 u32 magic 'PPR1' / status / count / comps / largest / pl_us / mask_bytes / rows<<16|row_bytes，
# mask_bytes > 0 时再跟位掩膜（每行 row_bytes 字节，行内低位在前）+ CRC16
PP_OP_MASK, PP_OP_CLEAN, PP_OP_CONN8 = 1, 2, 4
PP_BLOCK_BYTES = 32
PP_MAGIC = 0x31525050         # 小端字节 'P','P','R','1'
PP_STATUS_UNAVAILABLE = 0x80000000

# 形态指标块（板端 src/vessel_morph.h 的 vm_fill_block，u32 小端 x 16）
# 分块求和块（板端 ai_view.h 的 REG_*，F5 配准）：u32 magic / version / grid / out_w / out_h / run_us / 2 保留，
# 然后 i32 sum[16]（4x4 分块，行优先）。配 reg_absdiff 模型时 sum[k] = 第 k 个候选平移的 SAD（logit 单位 ≈ |a-b|/2）
REG_BLOCK_BYTES = 96
REG_MAGIC = 0x31474552       # 小端字节 'R','E','G','1'
REG_GRID = 4

MORPH_BYTES = 64
MORPH_MAGIC = 0x3150524D     # 小端字节 'M','R','P','1'
MORPH_FIELDS = ("magic", "version", "grid", "vessel_px", "fov_px", "length_x10",
                "caliber_x100", "branches", "endpoints", "segments", "tortuosity_x1000",
                "fractal_x1000", "density_x1000", "quad01", "quad23", "run_us")

# 统计块字段顺序（u32 小端）
AI_STAT_FIELDS = ("magic", "version", "flags", "ai_res", "vessels", "total",
                  "permille", "logit_min", "logit_max", "ai_us", "components",
                  "proc_min", "proc_max", "mode", "frame_count", "reserved")
_AI_STAT_SIGNED = ("logit_min", "logit_max")

_CTRL_FMT_V3 = "<HHHH"       # magic, mode, flags, aiparam —— 小端

MODE_NAMES = {
    0: "灰度归一化",
    1: "阈值分割",
    2: "边缘增强",
    3: "伪彩色映射",
    4: "组合(归一化+伪彩色)",
    5: "原图直通",
    6: "边缘叠加(白边)",
}

# 模式 6 在 PL 里**没有对应实现**：板子让 PL 跑模式 2 拿到边缘图，
# 再由 PS 把"边缘 >= 阈值"的像素在原图上画成白色。
# 所以模式 6 用的阈值就是那个"边缘阈值"（和模式 1 共用控制头里的 thr 字段）。
CTRL_MODE_MAX = 6
MODE_EDGE_OVERLAY = 6
MODE_EDGE_PL = 2              # 边缘叠加内部实际发给 PL 的模式

_ROW_CRC16 = "<HHH"          # row, length, crc16 —— 小端
_CTRL_FMT = "<HH"            # magic, mode —— 小端

# --------------------------------------------------------------------------
# CRC-16/CCITT-FALSE（与板端 crc16.c 一致）
# --------------------------------------------------------------------------
CRC16_INIT = 0xFFFF
CRC16_CHECK = 0x29B1         # CRC16("123456789") 的标准校验值


def crc16_ccitt_false_ref(data, crc=CRC16_INIT):
    """纯 Python 逐位参考实现。**慢**（786432 字节要 621 ms），只用于自检对照。"""
    for byte in data:
        crc ^= (byte << 8)
        crc &= 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# --------------------------------------------------------------------------
# 热路径一律用标准库的 C 实现
#
# binascii.crc_hqx 用的是 CRC-CCITT 多项式 0x1021、MSB-first、不反转、不异或输出，
# 和我们需要的 CRC-16/CCITT-FALSE 是**同一个算法**：只要初值传 0xFFFF，结果逐位相同。
# 实测 786432 字节：纯 Python 621.7 ms → crc_hqx 1.2 ms，**快约 500 倍**。
# 导入时会用参考实现交叉验证，防止哪天搞错。
# --------------------------------------------------------------------------
def crc16_ccitt_false(data, crc=CRC16_INIT):
    """CRC-16/CCITT-FALSE，热路径实现（C）。

    包一层是为了补默认初值 0xFFFF —— binascii.crc_hqx 两个参数都是必填。
    """
    return binascii.crc_hqx(data, crc)


FAST_CRC = True


# --------------------------------------------------------------------------
# 组包 / 解包
# --------------------------------------------------------------------------
def build_packet(row, payload):
    """把一个行包拼成 bytes（payload 必须正好 1536 字节）。"""
    if len(payload) != PKT_PAYLOAD_BYTES:
        raise ValueError("payload must be %d bytes, got %d"
                         % (PKT_PAYLOAD_BYTES, len(payload)))
    crc = crc16_ccitt_false(payload)
    return struct.pack(_ROW_CRC16, row, len(payload), crc) + payload


def iter_packets(image):
    """按行号 0..511 依次产出 packets（image 必须正好 786 432 字节）。"""
    if len(image) != IMG_TOTAL_BYTES:
        raise ValueError("image must be %d bytes, got %d"
                         % (IMG_TOTAL_BYTES, len(image)))
    for row in range(PKT_COUNT):
        off = row * IMG_ROW_BYTES
        yield build_packet(row, image[off:off + IMG_ROW_BYTES])


def parse_header(hdr):
    """解析 6 字节包头，返回 (row, length, crc16)。"""
    return struct.unpack(_ROW_CRC16, hdr)


def _roi_axis(r, n, full):
    lo = max(0, r - ROI_MARGIN)
    hi = min(full, r + n + ROI_MARGIN)
    s = lo // ROI_ALIGN * ROI_ALIGN
    size = -(-(hi - s) // ROI_SIZE_ALIGN) * ROI_SIZE_ALIGN
    if s + size > full:
        s = full - size              # 512 是 8 的倍数，回挪后起点仍对齐到 4
    return s, size


def roi_crop(x, y, w, h):
    """ROI 框 -> 板端实际计算的裁剪窗口 (cx, cy, cw, ch)。与板端 ai_view_roi_crop() 同一算法。"""
    cx, cw = _roi_axis(x, w, IMG_WIDTH)
    cy, ch = _roi_axis(y, h, IMG_HEIGHT)
    return cx, cy, cw, ch


def pack_stream(image, mode=0, flags=FLAG_DEFAULT, aiparam=None,
                thr=CTRL_THR_DEFAULT, version=4, roi=None):
    """把整幅图打成**完整的一帧字节流**：控制头 + 512 个行包。

    roi=(x, y, w, h) 时发 20 字节 v5 头（ROI 局部推理），返回 WIRE_TOTAL_BYTES_V5；
    version=4（默认）发 12 字节 v4 头（多了逐帧阈值 thr），返回 WIRE_TOTAL_BYTES_V4；
    version=3 发 8 字节 v3 头（没有阈值），返回 WIRE_TOTAL_BYTES_V3；
    version=2 发 4 字节老头（只为验证板端兼容性），返回 WIRE_TOTAL_BYTES。
    aiparam=None 时用默认（原始 RGB + 阈值 -3 + 512 分辨率）。

    CRC 走 C 实现，组包用 pack_into，系统调用从 512 次降到 1 次。
    """
    if len(image) != IMG_TOTAL_BYTES:
        raise ValueError("image must be %d bytes, got %d"
                         % (IMG_TOTAL_BYTES, len(image)))
    if not (0 <= mode <= CTRL_MODE_MAX):
        raise ValueError("mode must be 0..%d, got %r" % (CTRL_MODE_MAX, mode))
    if aiparam is None:
        aiparam = make_aiparam()
    if not (CTRL_THR_MIN <= thr <= CTRL_THR_MAX):
        raise ValueError("thr must be %d..%d, got %r"
                         % (CTRL_THR_MIN, CTRL_THR_MAX, thr))

    if roi is not None:
        x, y, w, h = (int(v) for v in roi)
        if not (w > 0 and h > 0 and x >= 0 and y >= 0
                and x + w <= IMG_WIDTH and y + h <= IMG_HEIGHT):
            raise ValueError("roi must lie inside the %dx%d image, got %r"
                             % (IMG_WIDTH, IMG_HEIGHT, roi))
        version = 5
    if version == 5:
        hdr_bytes, wire = CTRL_BYTES_V5, WIRE_TOTAL_BYTES_V5
    elif version == 4:
        hdr_bytes, wire = CTRL_BYTES_V4, WIRE_TOTAL_BYTES_V4
    elif version == 3:
        hdr_bytes, wire = CTRL_BYTES_V3, WIRE_TOTAL_BYTES_V3
    elif version == 2:
        hdr_bytes, wire = CTRL_BYTES, WIRE_TOTAL_BYTES
    else:
        raise ValueError("version must be 2, 3, 4 or 5 (5 needs roi)")

    out = bytearray(wire)
    if version == 5:
        struct.pack_into(_CTRL_FMT_V5, out, 0, CTRL_MAGIC_V5, mode,
                         flags & 0xFFFF, aiparam & 0xFFFF, thr & 0xFFFF, 0, x, y, w, h)
    elif version == 4:
        struct.pack_into(_CTRL_FMT_V4, out, 0, CTRL_MAGIC_V4, mode,
                         flags & 0xFFFF, aiparam & 0xFFFF, thr & 0xFFFF, 0)
    elif version == 3:
        struct.pack_into(_CTRL_FMT_V3, out, 0, CTRL_MAGIC_V3, mode,
                         flags & 0xFFFF, aiparam & 0xFFFF)
    else:
        struct.pack_into(_CTRL_FMT, out, 0, CTRL_MAGIC, mode)

    mvb = memoryview(out)
    pos = hdr_bytes
    for row in range(PKT_COUNT):
        off = row * IMG_ROW_BYTES
        payload = image[off:off + IMG_ROW_BYTES]
        struct.pack_into(_ROW_CRC16, out, pos, row, PKT_PAYLOAD_BYTES,
                         crc16_ccitt_false(payload))
        mvb[pos + PKT_HDR_BYTES:pos + PKT_TOTAL_BYTES] = payload
        pos += PKT_TOTAL_BYTES
    return bytes(out)


def pack_v6c(payload, w, kind=V6C_KIND_GRAY, flags=None, model0=2, model1=4, thr=1, track=V6C_TRACK_NONE,
             extra=(), pp=None):
    """紧凑帧：16 字节头 + 额外程序描述（每个 4 字节）+ [PL 后处理请求 12 字节] + 负载（w*w*kind 字节）。
    flags 缺省 = 统计块 + int8 logit。
    extra = [(model, src, dst, thr)]：多程序流水线，src 0 = 本帧 / 1 = 上一个程序的输出（偏移码 logit+128），
    dst 0 = 复制到 R/G/B，1/2/3 = 只替换 R/G/B。最多 3 个；b8 依次回所有程序的 logit。
    pp = dict(op, k=1, thr=1, tn=1, win=None)：程序 0 的 logit 交给 PL 的 pp_layer（自动加 FLAG_C_PP）；
    只发给统计块 version >= 8 的板端（老板端不认这 12 字节，会把协议搞乱）。"""
    if flags is None:
        flags = FLAG_STATS | FLAG_C_LOGIT
    if pp is not None:
        flags |= FLAG_C_PP
    if len(payload) != w * w * kind:
        raise ValueError("v6c payload must be %d bytes, got %d" % (w * w * kind, len(payload)))
    if w not in (32, 64, 128, 256, 512):
        raise ValueError("v6c size must be 512>>shift, got %d" % w)
    if len(extra) > V6C_MAX_EXTRA:
        raise ValueError("at most %d extra programs" % V6C_MAX_EXTRA)
    progs = b"".join(struct.pack("<BBBb", m, src, dst, int(t)) for m, src, dst, t in extra)
    if flags & FLAG_C_PP:
        progs += pack_pp(**(pp or {}))
    return struct.pack(_CTRL_FMT_V6C, CTRL_MAGIC_V6C, kind, track, flags & 0xFFFF, model0, model1,
                       int(thr), len(extra), w, w, crc16_ccitt_false(payload)) + progs + bytes(payload)


def pack_pp(op=PP_OP_MASK, k=1, thr=1, tn=1, win=None):
    """PL 后处理请求（12 字节）。win = (x0, y0, cw, ch)，logit 坐标；None = 整帧。"""
    x0, y0, cw, ch = win if win is not None else (0, 0, 0, 0)
    if k not in (1, 2, 4):
        raise ValueError("pp k must be 1, 2 or 4")
    return struct.pack("<BBbbHHHH", op & 7, k, int(thr), int(tn), x0, y0, cw, ch)


def unpack_pp(blk):
    """32 字节 PL 后处理块 -> dict（status 0 = 成功；bit31 = 这块板做不了）。"""
    v = struct.unpack("<8I", blk)
    if v[0] != PP_MAGIC:
        raise ValueError("bad pp block magic 0x%08X" % v[0])
    return dict(status=v[1], count=v[2], comps=v[3], largest=v[4], pl_us=v[5], mask_bytes=v[6],
                rows=v[7] >> 16, row_bytes=v[7] & 0xFFFF)


def pp_mask_array(pp):
    """pp 块里的位掩膜 -> 二维 bool 数组 [rows, row_bytes*8]（numpy；调用方再按 cw*k 截列）。"""
    import numpy as np
    bits = np.unpackbits(np.frombuffer(pp["mask"], np.uint8), bitorder="little")
    return bits.reshape(pp["rows"], pp["row_bytes"] * 8).astype(bool)


def stats_pp_available(ai_stats):
    """板端能做 PL 后处理（统计块 version >= 8、紧凑帧、reserved bit2）。"""
    st = ai_stats or {}
    r = st.get("reserved", 0)
    return st.get("version", 0) >= AI_STAT_VERSION_PP and bool(r & 0x80000000) and bool(r & 4)


def stats_compact_info(ai_stats):
    """紧凑帧的统计块 reserved：(是紧凑帧, 先验取自板端状态, 本帧后板端存有状态)。"""
    r = (ai_stats or {}).get("reserved", 0)
    return bool(r & 0x80000000), bool(r & 1), bool(r & 2)


def make_aiparam(src=AIPARAM_SRC_RAW, thr=AIPARAM_THR_DEFAULT, res=512, model=None):
    """打包 aiparam：bit0 输入源，bits[4:2] 分辨率档位，bits[7:5] 模型，bits[15:8] AI 阈值（int8）。

    res=512（默认）时档位是 0，也就是"不自选、用板端的默认"（选了模型时是该模型的默认档位）。
    model=None 时模型字段为 0（板端默认模型），否则是模型下标（0 = 眼底）。
    """
    sh = res_to_shift(res)
    if sh is None:
        raise ValueError("res must be one of %s" % (AI_RES_CHOICES,))
    mf = 0 if model is None else model + 1
    if not 0 <= mf <= 7:
        raise ValueError("model index must be 0..6, got %r" % (model,))
    return ((src & 0x3) | ((sh << AIPARAM_RES_SHIFT) & AIPARAM_RES_MASK)
            | ((mf << AIPARAM_MODEL_SHIFT) & AIPARAM_MODEL_MASK) | ((thr & 0xFF) << 8))


def expected_images(flags, ai_stats=None):
    """按 flags 推出发回图像流的顺序（与板端一致：预处理图 -> 叠加图 -> 掩膜图 -> logit 图）。

    ai_stats 是本帧先收到的统计块（没要统计时为 None）。有它就能避开两种"空等到超时"：
    板端这帧没跑出 AI（ai_us == 0）时不会发叠加 / 掩膜 / logit；
    老板端（version < AI_STAT_VERSION_LOGIT）不认 b5，不会发 logit 图。
    """
    if flags & FLAG_NO_IMAGE:
        return []
    ai_ok = ai_stats is None or ai_stats.get("ai_us", 0) != 0
    logit_ok = ai_stats is None or ai_stats.get("version", 0) >= AI_STAT_VERSION_LOGIT
    morph_ok = ai_stats is not None and ai_stats.get("version", 0) >= AI_STAT_VERSION_MORPH
    order = []
    if flags & FLAG_ECHO_PROC:
        order.append("proc")
    if flags & FLAG_ECHO_OVER and ai_ok:
        order.append("overlay")
    if flags & FLAG_ECHO_MASK and ai_ok:
        order.append("mask")
    if flags & FLAG_ECHO_LOGIT and ai_ok and logit_ok:
        order.append("logit")
    if flags & FLAG_MORPH and ai_ok and morph_ok:
        order.append("morph")
    return order


def expects_reg_block(ai_stats):
    """统计块之后是否跟着 96 字节分块求和块：板端认 b7（version >= 6）且回显的 flags 里 b7 仍是 1。"""
    return (ai_stats is not None and ai_stats.get("version", 0) >= AI_STAT_VERSION_REG
            and bool(ai_stats.get("flags", 0) & FLAG_REG))


def unpack_reg(blk):
    """解析 96 字节分块求和块 -> dict(grid, out_w, out_h, run_us, sums=[16 个有符号和])。"""
    if len(blk) != REG_BLOCK_BYTES:
        raise ValueError("reg block must be %d bytes, got %d" % (REG_BLOCK_BYTES, len(blk)))
    head = struct.unpack_from("<8I", blk, 0)
    if head[0] != REG_MAGIC:
        raise ValueError("bad reg magic 0x%08X" % head[0])
    return dict(version=head[1], grid=head[2], out_w=head[3], out_h=head[4], run_us=head[5],
                sums=list(struct.unpack_from("<16i", blk, 32)))


def expects_morph_block(flags, ai_stats):
    """统计块之后是否还跟着 64 字节形态指标块（板端：要了 b6 + b3、认 b6、这帧 AI 跑出来了）。"""
    return (bool(flags & FLAG_MORPH) and ai_stats is not None
            and ai_stats.get("version", 0) >= AI_STAT_VERSION_MORPH and ai_stats.get("ai_us", 0) != 0)


def unpack_morph(blk):
    """解析 64 字节形态指标块；长度 / 管径是分析网格的像素，派生字段换成浮点。"""
    if len(blk) != MORPH_BYTES:
        raise ValueError("morph block must be %d bytes, got %d" % (MORPH_BYTES, len(blk)))
    d = dict(zip(MORPH_FIELDS, struct.unpack("<16I", blk)))
    if d["magic"] != MORPH_MAGIC:
        raise ValueError("bad morph magic 0x%08X" % d["magic"])
    d["w"], d["h"] = d["grid"] & 0xFFFF, d["grid"] >> 16
    d["length"] = d["length_x10"] / 10.0
    d["caliber"] = d["caliber_x100"] / 100.0
    d["tortuosity"] = d["tortuosity_x1000"] / 1000.0
    d["fractal"] = d["fractal_x1000"] / 1000.0
    d["density_pct"] = d["density_x1000"] / 10.0
    d["quad_pct"] = [(d["quad01"] & 0xFFFF) / 10.0, (d["quad01"] >> 16) / 10.0,
                     (d["quad23"] & 0xFFFF) / 10.0, (d["quad23"] >> 16) / 10.0]
    return d


def unpack_stats(blk):
    """解析 64 字节统计块，返回 dict（含派生字段 vessels_pct）。"""
    if len(blk) != AI_STAT_BYTES:
        raise ValueError("stats block must be %d bytes, got %d"
                         % (AI_STAT_BYTES, len(blk)))
    vals = struct.unpack("<16I", blk)
    d = dict(zip(AI_STAT_FIELDS, vals))
    for k in _AI_STAT_SIGNED:
        if d[k] >= 0x80000000:
            d[k] -= 0x100000000
    if d["magic"] != AI_STAT_MAGIC:
        raise ValueError("bad stats magic 0x%08X" % d["magic"])
    d["vessels_pct"] = (100.0 * d["vessels"] / d["total"]) if d["total"] else 0.0
    # ROI 帧（version >= 3）：reserved = (裁剪高 << 16) | 裁剪宽；整图帧为 0
    d["crop_w"] = d["reserved"] & 0xFFFF if d["version"] >= AI_STAT_VERSION_ROI else 0
    d["crop_h"] = d["reserved"] >> 16 if d["version"] >= AI_STAT_VERSION_ROI else 0
    # 模型库（version >= 5）：flags bits[23:16] = 模型下标 + 1；老板端为 None（只有眼底）
    mf = (d["flags"] >> 16) & 0xFF if d["version"] >= AI_STAT_VERSION_MODEL else 0
    d["model"] = mf - 1 if mf else None
    return d


def unpack_stream(stream):
    """解析一整帧字节流，返回 (image_bytes, stats)。与 pack_stream 对称。

    stats = {'rows_ok', 'crc_errors', 'seq_errors'}
    行乱序也能正确落位（按包头里的 row 放）。
    """
    if len(stream) != STREAM_TOTAL_BYTES:
        raise ValueError("stream must be %d bytes, got %d"
                         % (STREAM_TOTAL_BYTES, len(stream)))
    image = bytearray(IMG_TOTAL_BYTES)
    seen = bytearray(PKT_COUNT)
    crc_errors = 0
    for k in range(PKT_COUNT):
        pos = k * PKT_TOTAL_BYTES
        row, length, crc = struct.unpack_from(_ROW_CRC16, stream, pos)
        if row >= PKT_COUNT or length != PKT_PAYLOAD_BYTES:
            raise ValueError("bad header at packet %d: row=%d len=%d"
                             % (k, row, length))
        off = pos + PKT_HDR_BYTES
        payload = stream[off:off + PKT_PAYLOAD_BYTES]
        if crc16_ccitt_false(payload) != crc:
            crc_errors += 1
            continue
        d = row * IMG_ROW_BYTES
        image[d:d + IMG_ROW_BYTES] = payload
        seen[row] = 1
    stats = {"rows_ok": sum(seen), "crc_errors": crc_errors, "seq_errors": 0}
    return bytes(image), stats


def verify_row(hdr, payload):
    """校验包头与负载；返回 (row, ok, reason)。"""
    row, length, crc = parse_header(hdr)
    if row >= PKT_COUNT:
        return row, False, "row %d out of range" % row
    if length != PKT_PAYLOAD_BYTES:
        return row, False, "length %d != %d" % (length, PKT_PAYLOAD_BYTES)
    if len(payload) != PKT_PAYLOAD_BYTES:
        return row, False, "payload short (%d)" % len(payload)
    got = crc16_ccitt_false(payload)
    if got != crc:
        return row, False, "crc 0x%04X != 0x%04X" % (got, crc)
    return row, True, ""


# --------------------------------------------------------------------------
# 导入自检：常量算术 + CRC 校验向量
# --------------------------------------------------------------------------
def _self_check():
    errs = []
    v6 = pack_v6c(bytes(range(256)) * 64, 128, track=V6C_TRACK_STEP)
    if len(v6) != CTRL_BYTES_V6C + 16384 or struct.unpack_from("<H", v6, 0)[0] != CTRL_MAGIC_V6C or \
            struct.unpack_from("<H", v6, 14)[0] != crc16_ccitt_false(bytes(range(256)) * 64) or v6[3] != 1:
        errs.append("pack_v6c layout")
    v7 = pack_v6c(bytes(64 * 64), 64, pp=dict(op=PP_OP_MASK | PP_OP_CLEAN, win=(8, 8, 48, 48)))
    if (struct.unpack_from("<H", v7, 4)[0] & FLAG_C_PP) == 0 or len(v7) != CTRL_BYTES_V6C + 12 + 4096 or \
            v7[16:28] != struct.pack("<BBbbHHHH", 3, 1, 1, 1, 8, 8, 48, 48):
        errs.append("pack_v6c pp request")
    pb = unpack_pp(struct.pack("<8I", PP_MAGIC, 0, 5, 2, 3, 7, 32, (16 << 16) | 2))
    if (pb["rows"], pb["row_bytes"], pb["mask_bytes"]) != (16, 2, 32):
        errs.append("unpack_pp")
    blk = struct.pack("<8I", REG_MAGIC, 1, 4, 512, 512, 9, 0, 0) + struct.pack("<16i", *range(-8, 8))
    if unpack_reg(blk)["sums"] != list(range(-8, 8)) or unpack_reg(blk)["out_w"] != 512:
        errs.append("unpack_reg round trip")
    if not expects_reg_block(dict(version=6, flags=FLAG_REG | FLAG_STATS)) or \
            expects_reg_block(dict(version=5, flags=FLAG_REG)) or expects_reg_block(dict(version=6, flags=FLAG_STATS)):
        errs.append("expects_reg_block")
    if IMG_ROW_BYTES != 1536:
        errs.append("IMG_ROW_BYTES=%d" % IMG_ROW_BYTES)
    if IMG_TOTAL_BYTES != 786432:
        errs.append("IMG_TOTAL_BYTES=%d" % IMG_TOTAL_BYTES)
    if PKT_TOTAL_BYTES != 1542:
        errs.append("PKT_TOTAL_BYTES=%d" % PKT_TOTAL_BYTES)
    if STREAM_TOTAL_BYTES != 789504:
        errs.append("STREAM_TOTAL_BYTES=%d" % STREAM_TOTAL_BYTES)
    if WIRE_TOTAL_BYTES != STREAM_TOTAL_BYTES + 4:
        errs.append("WIRE_TOTAL_BYTES=%d" % WIRE_TOTAL_BYTES)
    if struct.unpack(_CTRL_FMT, struct.pack(_CTRL_FMT, CTRL_MAGIC, 3)) != (CTRL_MAGIC, 3):
        errs.append("control header packing round-trip failed")

    if CTRL_BYTES_V3 != 8 or WIRE_TOTAL_BYTES_V3 != STREAM_TOTAL_BYTES + 8:
        errs.append("v3 wire size wrong")
    if struct.unpack(_CTRL_FMT_V3,
                     struct.pack(_CTRL_FMT_V3, CTRL_MAGIC_V3, 3, FLAG_ECHO_MASK, 0x00FD)) \
            != (CTRL_MAGIC_V3, 3, FLAG_ECHO_MASK, 0x00FD):
        errs.append("v3 control header round-trip failed")

    if CTRL_BYTES_V4 != 12 or WIRE_TOTAL_BYTES_V4 != STREAM_TOTAL_BYTES + 12:
        errs.append("v4 wire size wrong")
    if struct.unpack(_CTRL_FMT_V4,
                     struct.pack(_CTRL_FMT_V4, CTRL_MAGIC_V4, 1, FLAG_ECHO_PROC,
                                 0x0000, 137, 0)) != (CTRL_MAGIC_V4, 1, FLAG_ECHO_PROC,
                                                      0x0000, 137, 0):
        errs.append("v4 control header round-trip failed")
    if CTRL_MAGIC == CTRL_MAGIC_V3 or CTRL_MAGIC_V3 == CTRL_MAGIC_V4:
        errs.append("control header magics must be distinct")
    if make_aiparam() != ((AIPARAM_THR_DEFAULT & 0xFF) << 8):
        errs.append("make_aiparam default wrong: 0x%04X" % make_aiparam())
    if make_aiparam(AIPARAM_SRC_PROC, 12) != ((12 & 0xFF) << 8) | 1:
        errs.append("make_aiparam(SRC_PROC,12) wrong")
    if make_aiparam(AIPARAM_SRC_RAW, -3, 128) != (((-3) & 0xFF) << 8) | (2 << AIPARAM_RES_SHIFT):
        errs.append("make_aiparam(res=128) wrong: 0x%04X" % make_aiparam(0, -3, 128))
    if [shift_to_res(res_to_shift(r)) for r in AI_RES_CHOICES] != list(AI_RES_CHOICES):
        errs.append("res<->shift round-trip failed")
    if make_aiparam(0, -3, 512, None) != make_aiparam(0, -3, 512):
        errs.append("model=None must leave the model field 0")
    if (make_aiparam(0, 1, 128, 2) >> AIPARAM_MODEL_SHIFT) & 0x7 != 3 \
            or make_aiparam(0, 1, 128, 2) & ~AIPARAM_MODEL_MASK != make_aiparam(0, 1, 128):
        errs.append("make_aiparam(model=2) wrong: 0x%04X" % make_aiparam(0, 1, 128, 2))
    if [m["index"] for m in MODELS] != list(range(len(MODELS))) or MODELS[0]["name"] != "fundus":
        errs.append("MODELS must be indexed 0.. with fundus first")
    if MODELS[0]["scale"] != AI_LOGIT_SCALE or MODELS[0]["thr"] != AIPARAM_THR_DEFAULT:
        errs.append("fundus model entry drifted from AI_LOGIT_SCALE / AIPARAM_THR_DEFAULT")
    _sb = struct.pack("<16I", AI_STAT_MAGIC, 5, 0x0003002B, 128, 1, 2, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0)
    if unpack_stats(_sb)["model"] != 2:
        errs.append("stats v5 model decode wrong")
    if unpack_stats(_sb[:4] + struct.pack("<I", 4) + _sb[8:])["model"] is not None:
        errs.append("stats v4 must not report a model")

    if sorted(MODE_NAMES) != list(range(CTRL_MODE_MAX + 1)):
        errs.append("MODE_NAMES must cover exactly 0..%d, got %s"
                    % (CTRL_MODE_MAX, sorted(MODE_NAMES)))
    if MODE_EDGE_OVERLAY != CTRL_MODE_MAX or MODE_EDGE_PL != 2:
        errs.append("edge-overlay mode constants drifted")
    try:
        _m6 = pack_stream(bytes(IMG_TOTAL_BYTES), mode=MODE_EDGE_OVERLAY, thr=64)
        if len(_m6) != WIRE_TOTAL_BYTES_V4:
            errs.append("pack_stream(mode=6) wrong length")
    except Exception as exc:                        # noqa: BLE001 - self check
        errs.append("pack_stream(mode=6) failed: %s" % exc)
    if expected_images(FLAG_DEFAULT) != ["proc"]:
        errs.append("expected_images(default) = %r" % expected_images(FLAG_DEFAULT))
    if expected_images(FLAG_NO_IMAGE) != []:
        errs.append("expected_images(FLAG_NO_IMAGE) should be empty")
    if expected_images(FLAG_ECHO_PROC | FLAG_ECHO_OVER | FLAG_ECHO_MASK) != \
            ["proc", "overlay", "mask"]:
        errs.append("expected_images order wrong")
    _all = FLAG_ECHO_PROC | FLAG_ECHO_OVER | FLAG_ECHO_MASK | FLAG_ECHO_LOGIT | FLAG_STATS
    if expected_images(_all, {"version": 2, "ai_us": 1}) != ["proc", "overlay", "mask", "logit"]:
        errs.append("expected_images with logit order wrong")
    if expected_images(_all, {"version": 1, "ai_us": 1}) != ["proc", "overlay", "mask"]:
        errs.append("old board (stats v1) must not be expected to send logit")
    if expected_images(_all | FLAG_MORPH, {"version": 4, "ai_us": 1})[-1] != "morph" or \
            "morph" in expected_images(_all | FLAG_MORPH, {"version": 3, "ai_us": 1}):
        errs.append("morph view must come last, and only from a v4 board")
    try:
        _m = unpack_morph(struct.pack("<16I", MORPH_MAGIC, 1, 512 | (512 << 16), 100, 200, 123,
                                      456, 7, 8, 9, 1100, 1450, 105, 100 | (200 << 16),
                                      300 | (400 << 16), 999))
        if (_m["w"], _m["h"], _m["length"], _m["quad_pct"]) != (512, 512, 12.3, [10.0, 20.0, 30.0, 40.0]):
            errs.append("morph block decode wrong: %r" % (_m,))
    except Exception as exc:                     # noqa: BLE001 - self check
        errs.append("morph block parse failed: %s" % exc)
    if expected_images(_all, {"version": 2, "ai_us": 0}) != ["proc"]:
        errs.append("no AI images must be expected when the board ran no AI")
    if CTRL_BYTES_V5 != 20 or struct.calcsize(_CTRL_FMT_V5) != CTRL_BYTES_V5:
        errs.append("v5 header size wrong")
    try:
        _w5 = pack_stream(bytes(IMG_TOTAL_BYTES), roi=(10, 20, 30, 40))
        if len(_w5) != WIRE_TOTAL_BYTES_V5 or \
                struct.unpack_from(_CTRL_FMT_V5, _w5, 0)[0] != CTRL_MAGIC_V5 or \
                struct.unpack_from(_CTRL_FMT_V5, _w5, 0)[6:] != (10, 20, 30, 40):
            errs.append("v5 header packing wrong")
    except Exception as exc:                        # noqa: BLE001 - self check
        errs.append("pack_stream(roi) failed: %s" % exc)
    for _r in ((200, 180, 128, 128), (0, 0, 1, 1), (500, 500, 12, 12), (0, 0, 512, 512)):
        _cx, _cy, _cw, _ch = roi_crop(*_r)
        if (_cx % ROI_ALIGN or _cy % ROI_ALIGN or _cw % ROI_SIZE_ALIGN or _ch % ROI_SIZE_ALIGN
                or _cx + _cw > IMG_WIDTH or _cy + _ch > IMG_HEIGHT
                or _cx > max(0, _r[0] - ROI_MARGIN) or _cy > max(0, _r[1] - ROI_MARGIN)
                or _cx + _cw < min(IMG_WIDTH, _r[0] + _r[2] + ROI_MARGIN)
                or _cy + _ch < min(IMG_HEIGHT, _r[1] + _r[3] + ROI_MARGIN)):
            errs.append("roi_crop%r = %r breaks the alignment / margin rules"
                        % (_r, (_cx, _cy, _cw, _ch)))
    if (LOGIT_IMG_OFFSET - 3) & 0xFF != 125:
        errs.append("logit offset coding drifted")

    try:
        _blk = struct.pack("<16I", AI_STAT_MAGIC, AI_STAT_VERSION, FLAG_STATS, 32,
                           100, 1024, 97, (0x100000000 - 3) & 0xFFFFFFFF, 14,
                           2900, 5, 0, 255, 0, 1, 0)
        _d = unpack_stats(_blk)
        if (_d["vessels"] != 100 or _d["logit_min"] != -3 or _d["logit_max"] != 14
                or _d["reserved"] != 0):
            errs.append("stats block round-trip mismatch: %r" % (_d,))
    except Exception as exc:                     # noqa: BLE001 - self check
        errs.append("stats block parse failed: %s" % exc)

    # 标准校验向量
    if crc16_ccitt_false(b"123456789") != CRC16_CHECK:
        errs.append("CRC16 check vector failed")

    # 快速实现必须与逐位参考实现逐位一致(几组不同长度/内容)
    probes = [b"", b"123456789", bytes(range(256)),
              bytes(((i * 7 + 3) & 0xFF) for i in range(1536))]
    for p in probes:
        if crc16_ccitt_false(p) != crc16_ccitt_false_ref(p):
            errs.append("fast CRC != reference CRC on %d-byte probe" % len(p))
    return errs


SELF_CHECK_ERRORS = _self_check()


# ══════════════════════════════════════════════════════════════════════════
# 来源：ef_simpson.py
# ══════════════════════════════════════════════════════════════════════════

"""ef_simpson.py —— F4：双平面 Simpson 圆盘法算左心室容积和射血分数（上位机与离线评测共用这一份）。

F4: LV volumes and ejection fraction by Simpson's biplane method of disks.

Re-implementation of the official CAMUS notebook (database "jupyter/script_camus_ef.ipynb",
Leclerc et al., IEEE TMI 2019, doi 10.1109/TMI.2019.2900516; CC BY-NC-SA 4.0), kept
step for step so that expert masks reproduce the reference EF given in Info_*.cfg:
  * resample to isotropic pixels (NEAREST on the coarser axis);
  * first contour of skimage.measure.find_contours(mask, 0.5);
  * mitral (AV) plane = longest chord between contour points i > j whose direction is
    within 45 deg of horizontal and with every contour point between them within 8 px of it;
  * apex = contour point farthest from the chord's middle contour point;
  * 20 disks along midpoint -> apex, diameter = edge-to-edge distance along the normal,
    each side marched from 8 px in, in 0.5 px steps;
  * volume = sum(d_a2c * d_a4c) * step * pi / 4 with the largest of the four step sizes,
    rounded to whole millilitres; EF = round(100 * (EDV - ESV) / EDV).
The notebook itself warns that implementation details change the numbers; only the
reproduction rate on expert masks (check_ef_gt.py) says this port is faithful.
Predicted masks go through clean() first (largest component, holes filled) -- the one
addition to the notebook, on the PC.

Needs numpy + scipy + scikit-image + Pillow (the srtp-vision environment on this PC);
the UI disables its EF panel when they are missing. echo/ef.py re-exports this module.
"""
import numpy as np
from PIL import Image
# WS_OPT_V1 原本这里再 import 一次 scipy.ndimage / skimage：统一走文件头的惰性代理。


def clean(mask):
    """Largest 4-connected component with its holes filled. The notebook takes the first
    contour only, so a stray blob or a hole in a predicted mask would be measured instead
    of the cavity (validation: >1 component in 21 % of F1 frames). Expert masks: no-op."""
    lab, n = ndimage.label(mask)
    if n > 1:
        sizes = ndimage.sum(mask, lab, range(1, n + 1))
        mask = lab == (int(np.argmax(sizes)) + 1)
    return ndimage.binary_fill_holes(mask)


def to_isotropic(mask, spacing):
    """mask (H, W) bool, spacing (height, width) mm -> (mask', isotropic spacing)."""
    scaling = np.array(spacing) / min(spacing)
    h, w = (np.array(mask.shape) * scaling).round().astype(int)
    m = np.array(Image.fromarray(mask.astype(np.uint8)).resize((w, h), Image.NEAREST))
    return m, min(spacing)


def _distance_to_edge(seg, point, normal):
    d = 8.0                                         # start a bit in, as the notebook
    while True:
        y, x = np.round(point + d * normal).astype(int)
        if not (0 <= y < seg.shape[0] and 0 <= x < seg.shape[1]) or seg[y, x] == 0:
            return d
        d += 0.5


def _line_to_points(p0, p1, pts):
    v = p1 - p0
    w = p0 - pts
    return np.abs(v[0] * w[:, 1] - v[1] * w[:, 0]) / np.linalg.norm(v)


def diameters(mask, spacing):
    """-> (20 disk diameters in mm, disk step in mm, geometry dict for display)."""
    seg, iso = to_isotropic(mask, spacing)
    contour = find_contours(seg, 0.5)[0]
    best, bi, bj = 0.0, None, None
    for i in range(2, len(contour)):
        diff = contour[:i] - contour[i]
        ang = np.abs(np.degrees(np.arctan2(diff[:, 0], diff[:, 1])))
        for j in np.nonzero(ang <= 45)[0]:
            if np.all(_line_to_points(contour[i], contour[j], contour[j + 1:i]) <= 8):
                dist = np.linalg.norm(contour[i] - contour[j])
                if best < dist:
                    best, bi, bj = dist, i, j
    mid = int(bj + round((bi - bj) / 2))
    lengths = np.linalg.norm(contour - contour[mid], axis=1)
    apex = int(np.argmax(lengths))                  # first maximum, as the notebook's strict '<' loop
    axis_len = lengths[apex]
    direction = contour[apex] - contour[mid]
    normal = np.array([-direction[1], direction[0]])
    normal = normal / np.linalg.norm(normal)
    d, ends = [], []
    for f in np.linspace(0, 1, 20, endpoint=False):
        p = contour[mid] + direction * f
        d1, d2 = _distance_to_edge(seg, p, normal), _distance_to_edge(seg, p, -normal)
        d.append((d1 + d2) * iso)
        ends.append((p + d1 * normal, p - d2 * normal))
    # geometry in ORIGINAL pixel coordinates (row, col), for drawing
    back = iso / np.array(spacing, dtype=float)
    geom = dict(base=(contour[bj] * back, contour[bi] * back), mid=contour[mid] * back,
                apex=contour[apex] * back, disks=[(a * back, b * back) for a, b in ends], contour=contour * back)
    return np.array(d), axis_len * iso / 20, geom


def volumes(a2c_ed, a2c_es, a2c_spacing, a4c_ed, a4c_es, a4c_spacing):
    """Binary LV masks at the original resolution, spacing (height, width) mm -> (EDV, ESV) ml."""
    d2e, s2e, _ = diameters(a2c_ed, a2c_spacing)
    d2s, s2s, _ = diameters(a2c_es, a2c_spacing)
    d4e, s4e, _ = diameters(a4c_ed, a4c_spacing)
    d4s, s4s, _ = diameters(a4c_es, a4c_spacing)
    step = max(s2e, s2s, s4e, s4s)
    vol = lambda a, b: int(round(float(np.sum((a / 1000) * (b / 1000)) * (step / 1000) * np.pi / 4 * 1e6)))
    return vol(d2e, d4e), vol(d2s, d4s)


def ejection_fraction(edv, esv):
    return int(round(100 * (edv - esv) / edv)) if edv else None


# ══════════════════════════════════════════════════════════════════════════
# 来源：cardiac_measure.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""cardiac_measure.py —— P1 1.6：左心房容积 / 指数、左室质量 / 指数、壁厚（辅助测量与复核，不作诊断）。

全部建立在 F4 的双平面 Simpson 移植（ef_simpson.diameters，CAMUS 官方 notebook 的逐步移植）上：
  左房容积  LA 掩膜（标签 3）在 LV 收缩末（= LA 最大）帧，两切面 20 圆盘：sum(d2·d4)·step·π/4（mL，不取整）
  左室质量  1.05 g/mL ×（心外膜容积 − 心内膜容积），两者都在 ED 帧、各自用双平面 Simpson
  壁厚      ED 帧每个切面：心内膜轮廓点（去掉二尖瓣平面那一段）到心外膜边界的距离，取中位数（mm），两切面平均
  体表面积  Mosteller：sqrt(身高 cm × 体重 kg / 3600)（ASE 2015 原文没指定公式，见 docs/临床测量 第 3 节）

阈值（2026-10-15 核对原文：Lang RM et al., Recommendations for Cardiac Chamber Quantification by Echocardiography in
Adults, EHJ-CVI 2015;16:233–271 / JASE 2015;28:1–39）：
  LAVI（Table 4，双平面圆盘法，男女相同）  正常 16–34，轻度 35–41，中度 42–48，重度 > 48 mL/m²
  LV 质量指数（Table 6，2D 法）           正常 女 44–88、男 50–102 g/m²；**原文 2D 法是短轴乳头肌水平的面积-长度 / 截头椭球法，
                                          本系统是两个心尖切面上心外膜 − 心内膜的双平面 Simpson，方法不同，阈值只作参考、界面注明**
  壁厚（Table 6，线性法，胸骨旁长轴）       正常 女 0.6–0.9、男 0.6–1.0 cm；本系统的定义不同（心尖切面、全轮廓中位数），只显示数值

几何分型（部分 A，2026-10-16；阈值核对 ASE 官方托管的指南日文全译本 asecho.org/wp-content/uploads/2018/01/ChamberQuant_Japanese.pdf
第 13–14 页：正文、表 6、RWT × LVMI 分型图——图的坐标轴保留英文原文；JASE / OUP 原站有机器人校验打不开）：
  RWT = (2 × 后壁厚) / 左室舒张末内径；> 0.42 向心，≤ 0.42 非向心（正文与图坐标轴；图注写 ≧ / ≦，等号只是测度为零的边界）
  LVMI 上限（分型图，原文注明"左室质量按线性法"）女 ≤ 95、男 ≤ 115 g/m²（表 6 线性法 女 43–95、男 49–115；2D 法上限 88 / 102）
  指南四型：LVMI 不高 + RWT ≤ 0.42 正常构型；不高 + > 0.42 向心性重构；高 + > 0.42 向心性肥厚；高 + ≤ 0.42 离心性肥厚
  **本系统不套 RWT 0.42（用户 10-16 定）**：CAMUS 二维掩膜的心肌偏宽，专家掩膜上 RWT 中位 0.58、VAL 49 / 50 人 > 0.42
  （训练集换「与内径同一水平的壁厚」也一样，382 / 400），四型实际只剩 LVMI 一维。所以 RWT 只显示数值，
  分类只按 LVMI：> 95（女）/ 115（男）g/m² 为「增高」，否则「不增高」（lvmi_class）；不叫几何分型、不给四型名称。
  **口径**：指南的 RWT / LVMI 按胸骨旁长轴 M 型 / 二维线性测量定义；本系统全部来自两个心尖切面的二维掩膜：
    壁厚    = 上面的「全轮廓中位数」（代替后壁厚）
    内径    = ED 心内膜第 3–6 个圆盘（沿长轴 15%–30%，二尖瓣叶尖附近）直径的平均，两切面平均（代替 PLAX 的 LVIDd）
    质量    = 双平面 Simpson 心外膜 − 心内膜（代替线性法立方公式）
  所以 LVMI 分类只作辅助测量与复核的参考，界面 / 报告都注明口径不同，不作诊断。需要性别、身高、体重（BSA = Mosteller）。
"""
import math

import numpy as np
# WS_OPT_V1 原本这里再 import 一次 scipy.ndimage：统一走文件头的惰性代理。


DENSITY = 1.05                       # g/mL，ASE 2015 §4
LAVI_BANDS = ((34, '正常'), (41, '轻度增大'), (48, '中度增大'), (float('inf'), '重度增大'))     # Table 4 上限（含）
LVMI_2D = {'F': (44, 88), 'M': (50, 102)}                                                    # Table 6，2D 法
WALL_LINEAR_CM = {'F': (0.6, 0.9), 'M': (0.6, 1.0)}                                          # Table 6，线性法
SOURCE = 'Lang RM et al. EHJ-CVI 2015;16:233-271 (ASE/EACVI), Table 4 / Table 6'
# 部分 A：LVMI > LVMI_LIMIT[性别] 为增高（ASE 2015 分型图的线性法上限）；RWT 只显示数值，不套指南的 0.42
RWT_GUIDELINE = 0.42                 # 只用于文档 / 评测对照，不参与分类
LVMI_LIMIT = {'F': 95, 'M': 115}
LVIDD_DISKS = (3, 4, 5, 6)           # ED 心内膜 20 圆盘里取这几个（f = 0.15–0.30）平均 = 一个切面的舒张末内径
LVMI_CLASS = {'not_increased': '左室质量指数不增高', 'increased': '左室质量指数增高'}
LVMI_CLASS_NOTE = '按 ASE 2015 LVMI 上限（线性法）；本系统质量由心尖切面二维掩膜测得，口径不同，仅作辅助测量与复核；RWT 只显示数值'
# 左房截断（10-15 与用户商定）：CAMUS 切面以左室为中心，左房常被扇区下缘截掉（VAL 41/100 个 ES 帧）。
# 任一切面的 LA 掩膜碰到原图最下 LA_EDGE_ROWS 行 -> 报「左房被截断，不可测」，不给容积 / LAVI。
LA_EDGE_ROWS = 2
# 线性校准 la = a * raw + b，只在训练集（两切面都未截断的病人）上拟合：echo/fit_la_calib.py -> results/p1_16/la_calib.json
LA_CAL = dict(a=0.9533886925323705, b=9.024183337520322, n_fit=192, fit='train', json_sha256_16='89f79b072179dabc')


def bsa_mosteller(height_cm, weight_kg):
    return math.sqrt(height_cm * weight_kg / 3600.0)


def biplane_volume(m2, sp2, m4, sp4):
    """ef_simpson.volumes 的单时相版本（同一个求和，不取整）：两切面掩膜（原图分辨率）-> mL。"""
    d2, s2, _ = ef.diameters(m2, sp2)
    d4, s4, _ = ef.diameters(m4, sp4)
    step = max(s2, s4)
    return float(np.sum(np.asarray(d2) * np.asarray(d4)) * step * math.pi / 4.0 / 1000.0)


def wall_thickness_mm(endo, epi, spacing):
    """一个切面（ED）：心内膜轮廓点到心外膜外的距离的中位数（mm）；二尖瓣平面上的点不算。"""
    endo_i, sp = ef.to_isotropic(endo, spacing)
    epi_i, _ = ef.to_isotropic(epi, spacing)
    if not endo_i.any() or not epi_i.any():
        return None
    _, _, geo = ef.diameters(endo, spacing)
    dist = ndimage.distance_transform_edt(epi_i) * sp           # inside the epicardium: distance to its outside
    edge = endo_i & ~ndimage.binary_erosion(endo_i)
    ys, xs = np.nonzero(edge)
    keep = np.ones(len(ys), bool)
    back = sp / np.array(spacing, dtype=float)                  # original -> isotropic pixel coordinates
    mv = [np.asarray(p, float) / back for p in geo['base']]
    if mv:                                                      # drop points within 3 mm of the mitral chord
        (y0, x0), (y1, x1) = mv
        v = np.array([y1 - y0, x1 - x0], float)
        L = np.hypot(*v) or 1.0
        t = np.clip(((ys - y0) * v[0] + (xs - x0) * v[1]) / (L * L), 0, 1)
        dd = np.hypot(ys - (y0 + t * v[0]), xs - (x0 + t * v[1])) * sp
        keep = dd > 3.0
    vals = dist[ys[keep], xs[keep]]
    return float(np.median(vals)) if len(vals) else None


def lvidd_view_mm(endo, spacing):
    """一个切面 ED 心内膜的舒张末内径（mm）：LVIDD_DISKS 几个圆盘直径从左到右相加再除（板端 C 同一顺序）。"""
    d, _, _ = ef.diameters(endo, spacing)
    s = float(d[LVIDD_DISKS[0]])
    for k in LVIDD_DISKS[1:]:
        s = s + float(d[k])
    return s / len(LVIDD_DISKS)


def lvmi_class(lvmi, sex):
    """LVMI 增高 / 不增高（ASE 2015 线性法上限）-> dict；缺 LVMI 或性别返回 None。"""
    if lvmi is None or sex not in LVMI_LIMIT:
        return None
    code = 'increased' if lvmi > LVMI_LIMIT[sex] else 'not_increased'
    return dict(code=code, name=LVMI_CLASS[code], lvmi_limit=LVMI_LIMIT[sex], note=LVMI_CLASS_NOTE)


def la_truncated(mask):
    m = np.asarray(mask, bool)
    return bool(m[-LA_EDGE_ROWS:].any())


def la_calibrated(raw_ml):
    return raw_ml if LA_CAL is None else LA_CAL['a'] * raw_ml + LA_CAL['b']


def lavi_band(lavi):
    for hi, name in LAVI_BANDS:
        if lavi <= hi:
            return name
    return LAVI_BANDS[-1][1]


def measure(views, height_cm=None, weight_kg=None, sex=None, calibrate=True):
    """views = {'2CH': dict(spacing, ed_endo, ed_epi, es_la), '4CH': ...}（原图分辨率的布尔掩膜）-> dict。
    calibrate=False：专家掩膜（评测参照）不做 LA 校准。"""
    a, b = views['2CH'], views['4CH']
    out = {}
    out['la_truncated'] = la_truncated(a['es_la']) or la_truncated(b['es_la'])
    if out['la_truncated']:
        out['la_raw_ml'] = out['la_ml'] = None
        out['la_note'] = '左房被截断，不可测'
    else:
        out['la_raw_ml'] = biplane_volume(a['es_la'], a['spacing'], b['es_la'], b['spacing'])
        out['la_ml'] = la_calibrated(out['la_raw_ml']) if calibrate else out['la_raw_ml']
    v_epi = biplane_volume(a['ed_epi'], a['spacing'], b['ed_epi'], b['spacing'])
    v_endo = biplane_volume(a['ed_endo'], a['spacing'], b['ed_endo'], b['spacing'])
    out.update(epi_ml=v_epi, endo_ml=v_endo, lv_mass_g=DENSITY * (v_epi - v_endo))
    w = [wall_thickness_mm(v['ed_endo'], v['ed_epi'], v['spacing']) for v in (a, b)]
    out['wall_mm'] = float(np.mean([x for x in w if x is not None])) if any(x is not None for x in w) else None
    out['wall_mm_views'] = w
    li = [lvidd_view_mm(v['ed_endo'], v['spacing']) for v in (a, b)]
    out['lvidd_mm_views'] = li
    out['lvidd_mm'] = (li[0] + li[1]) / 2.0
    out['rwt'] = 2.0 * out['wall_mm'] / out['lvidd_mm'] if out['wall_mm'] is not None and out['lvidd_mm'] > 0 else None
    if height_cm and weight_kg:
        bsa = bsa_mosteller(height_cm, weight_kg)
        out['bsa_m2'] = bsa
        if out['la_ml'] is not None:
            out['lavi'] = out['la_ml'] / bsa
            out['lavi_band'] = lavi_band(out['lavi'])
        out['lvmi'] = out['lv_mass_g'] / bsa
        if sex in LVMI_2D:
            lo, hi = LVMI_2D[sex]
            out['lvmi_ref'] = dict(range=[lo, hi], above=out['lvmi'] > hi, note='ASE 2D 法（短轴面积-长度）的参考范围；本系统方法不同，仅作参考')
        out['lvmi_class'] = lvmi_class(out['lvmi'], sex)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 来源：confidence.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""confidence.py —— 用板端回传的 logit 图，在上位机本地生成 AI 的各种视图。

板端（flags b5 = FLAG_ECHO_LOGIT）把 U-Net 输出的 int8 logit 编成一幅 512x512 RGB 图：
每像素 R=G=B=logit+128，放大方式与掩膜图完全相同（见 tcp_image_rx/src/ai_view.c）。
有了它，上位机可以：

* 换门限后**不重跑 FPGA**，本地重出掩膜 / 叠加图 —— 与板端 ai_view.c 的输出逐字节一致；
* 画置信度热力图：颜色 = 血管概率 sigmoid(logit * scale)；
* 标出“不确定区域”：logit 落在门限 ±band 以内、模型拿不准的像素。

全部用 bytes.translate、步长切片和大整数位运算完成，不依赖 numpy；
512x512 重算一次只要几毫秒到几十毫秒，拖滑条能跟手。
"""

import math


N_PIX = proto.IMG_WIDTH * proto.IMG_HEIGHT
OFFSET = proto.LOGIT_IMG_OFFSET

VESSEL_RGB = (255, 0, 0)       # 与板端叠加图同色
UNSURE_RGB = (255, 200, 0)     # 不确定区域：黄

# 热力图色带（近似 inferno：黑 -> 紫 -> 红 -> 橙 -> 浅黄），按概率 0..1 线性插值
_HEAT_ANCHORS = ((0.00, (0, 0, 4)), (0.13, (31, 12, 72)), (0.25, (85, 15, 109)),
                 (0.38, (136, 34, 106)), (0.50, (186, 54, 85)), (0.63, (227, 89, 51)),
                 (0.75, (249, 140, 10)), (0.88, (249, 201, 50)), (1.00, (252, 255, 164)))


# 输出量化 scale 因模型而异（proto.MODELS）；界面在每帧到达时按该帧的模型设置。
SCALE = proto.AI_LOGIT_SCALE


def set_scale(scale):
    """切换概率换算用的输出 scale（换模型时调用）。"""
    global SCALE
    SCALE = float(scale)


def probability(logit):
    """int8 logit -> 目标（血管 / 病灶 / 左心室）概率。"""
    return 1.0 / (1.0 + math.exp(-logit * SCALE))


def logit_plane(logit_img):
    """logit 图（786432 字节 RGB）-> 每像素一个偏移码字节（262144 字节）。"""
    if len(logit_img) != proto.IMG_TOTAL_BYTES:
        raise ValueError("logit image must be %d bytes, got %d"
                         % (proto.IMG_TOTAL_BYTES, len(logit_img)))
    return bytes(logit_img[0::3])


def _table(pred):
    """256 项查表：偏移码 v 满足 pred(logit) 时为 0xFF，否则 0x00。"""
    return bytes(255 if pred(v - OFFSET) else 0 for v in range(256))


def _select(sel, a, b):
    """逐字节选择：sel 为 0xFF 处取 a，为 0x00 处取 b（三者等长）。整块一次位运算完成。"""
    n = len(sel)
    m = int.from_bytes(sel, "little")
    full = (1 << (8 * n)) - 1
    r = (int.from_bytes(a, "little") & m) | (int.from_bytes(b, "little") & (full ^ m))
    return r.to_bytes(n, "little")


def _fill(value):
    return bytes((value,)) * N_PIX


def _interleave(r, g, b):
    out = bytearray(3 * len(r))
    out[0::3] = r
    out[1::3] = g
    out[2::3] = b
    return bytes(out)


def _vessel_sel(plane, thr):
    return plane.translate(_table(lambda q: q >= thr))


def _unsure_sel(plane, thr, band):
    return plane.translate(_table(lambda q: abs(q - thr) <= band))


def mask_rgb(plane, thr):
    """与板端 ai_view_mask_rgb 相同：logit >= thr 为白，其余为黑。"""
    s = _vessel_sel(plane, thr)
    return _interleave(s, s, s)


def overlay_rgb(base, plane, thr):
    """与板端 ai_view_overlay_rgb 相同：血管像素涂红，其余取底图（板端的底图是预处理图）。"""
    s = _vessel_sel(plane, thr)
    return _interleave(_select(s, _fill(VESSEL_RGB[0]), base[0::3]),
                       _select(s, _fill(VESSEL_RGB[1]), base[1::3]),
                       _select(s, _fill(VESSEL_RGB[2]), base[2::3]))


def uncertain_rgb(base, plane, thr, band):
    """底图上：确定的血管涂红，门限 ±band 以内（模型拿不准）的像素涂黄。"""
    v = _vessel_sel(plane, thr)
    u = _unsure_sel(plane, thr, band)
    chans = []
    for c in range(3):
        ch = _select(v, _fill(VESSEL_RGB[c]), base[c::3])
        chans.append(_select(u, _fill(UNSURE_RGB[c]), ch))
    return _interleave(*chans)


def _heat_color(p):
    for (p0, c0), (p1, c1) in zip(_HEAT_ANCHORS, _HEAT_ANCHORS[1:]):
        if p <= p1:
            t = (p - p0) / (p1 - p0)
            return tuple(int(round(a + (b - a) * t)) for a, b in zip(c0, c1))
    return _HEAT_ANCHORS[-1][1]


_HEAT_LUT = [_heat_color(probability(v - OFFSET)) for v in range(256)]
_HEAT_TABLES = [bytes(c[k] for c in _HEAT_LUT) for k in range(3)]


def heat_rgb(plane):
    """置信度热力图：每像素按血管概率上色（黑紫 = 0，红 ≈ 0.5，浅黄 = 1）。"""
    return _interleave(*(plane.translate(t) for t in _HEAT_TABLES))


def roi_compare(plane_a, plane_b, roi):
    """两个 logit 平面在 ROI 框内逐字节比对，返回 (比对像素数, 不同的像素数)。
    用来在实板上核对“ROI 局部推理 == 整图推理”。"""
    x, y, w, h = roi
    bad = 0
    for yy in range(y, y + h):
        a = plane_a[yy * proto.IMG_WIDTH + x:yy * proto.IMG_WIDTH + x + w]
        b = plane_b[yy * proto.IMG_WIDTH + x:yy * proto.IMG_WIDTH + x + w]
        if a != b:
            bad += sum(1 for p, q in zip(a, b) if p != q)
    return w * h, bad


def stats(plane, thr, band, ai_res=512, roi=None):
    """本地统计。板端按网络分辨率计数，logit 图是最近邻放大的，所以按放大倍数折回去。
    roi=(x, y, w, h) 时（ROI 局部推理帧）只统计框内，与板端统计块口径相同。"""
    if roi is not None:
        x, y, w, h = roi
        plane = b"".join(plane[r * proto.IMG_WIDTH + x:r * proto.IMG_WIDTH + x + w]
                         for r in range(y, y + h))
        ai_res = proto.IMG_WIDTH                 # ROI 帧总是全分辨率
    f = max(1, proto.IMG_WIDTH // ai_res)
    rep = f * f
    vessels = _vessel_sel(plane, thr).count(255) // rep
    unsure = _unsure_sel(plane, thr, band).count(255) // rep
    total = len(plane) // rep
    return {"vessels": vessels, "unsure": unsure, "total": total,
            "vessels_pct": 100.0 * vessels / total, "unsure_pct": 100.0 * unsure / total}


# ══════════════════════════════════════════════════════════════════════════
# 来源：imageio.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""imageio.py —— 图像的来源与显示转换。

提供：
  * 生成测试图样（gradient / bars / checker）
  * 读 **samples/** 目录里随工具分发的**默认测试图**（STARE 眼底图 im0001~im0005）；
    往目录里再丢图片、重启工具就会出现在下拉框里，不用改代码
  * **原生读** Netpbm 全家桶（P1..P7，不需要 Pillow）与裸 RGB888；
    其它格式（PNG/JPG/BMP/TIF/WebP…）走 Pillow
  * **原生写** 裸 RGB888 / PPM / PGM / PBM / PAM（不需要 Pillow）；
    其它格式走 Pillow
  * 转成 PPM，喂给 tkinter 的 PhotoImage 显示

统一的内部表示就是**裸 RGB888 的 bytes**，长度必须 786432。
取图的统一入口是 :func:`load_pattern`（名字，内置图样或 samples 都走它）
和 :func:`load_image`（文件路径）。

支持的图片格式一览（"原生" = 只靠标准库就能读写）::

    格式         读            写      说明
    ----------  -----------  ------  --------------------------------------
    裸 RGB888    ✅ 原生       ✅ 原生  .rgb/.raw/.bin，必须正好 786432 字节
    PPM  P1..P6  ✅ 原生       ✅ P6    ASCII/二进制都认，maxval 1..65535
    PGM  P2/P5   ✅ 原生       ✅ P5    灰度，导出用与 PL 相同的 (77R+150G+29B)>>8
    PBM  P1/P4   ✅ 原生       ✅ P4    1=黑；读时按位打包解析
    PNM  P1..P6  ✅ 原生       ✅ P6    扩展名 .pnm 一律按 P6 写
    PAM  P7      ✅ 原生       ✅ P7    depth 1/2/3/4，多出来的 alpha 丢掉
    PNG/JPG      ✅ Pillow     ✅ Pillow
    BMP/TIF/GIF  ✅ Pillow     ✅ Pillow
    WebP         ✅ Pillow     ✅ Pillow

非 512x512 的一律拉伸到 512x512（优先 Pillow LANCZOS；没装 Pillow 时对
Netpbm 回退最近邻）。**已经 512x512 的不重采样**，保证逐字节可比。
"""

import base64
import os


# 标准 8 色条，取 75% 幅度
_BAR_COLORS_75 = [
    (191, 191, 191), (191, 191, 0), (0, 191, 191), (0, 191, 0),
    (191, 0, 191), (191, 0, 0), (0, 0, 191), (0, 0, 0),
]


def make_gradient():
    """水平渐变(R 随 x) + 垂直渐变(G 随 y) + 固定蓝。"""
    # WS_OPT_V1 原来逐像素写 bytearray（512x512 = 26 万次 Python 循环，约 35 ms/张）。
    # 同样的算术交给 numpy，输出逐字节相同（已用原实现交叉验证），耗时 <1 ms。
    x = np.arange(IMG_WIDTH, dtype=np.uint32)
    y = np.arange(IMG_HEIGHT, dtype=np.uint32)
    buf = np.empty((IMG_HEIGHT, IMG_WIDTH, IMG_BYTES_PER_PIX), np.uint8)
    buf[..., 0] = (x * 255 // (IMG_WIDTH - 1)).astype(np.uint8)
    buf[..., 1] = ((y * 255) // (IMG_HEIGHT - 1)).astype(np.uint8)[:, None]
    buf[..., 2] = 128
    return buf.tobytes()


def make_colorbars():
    """8 条竖向彩条，每条 64 像素。"""
    # WS_OPT_V1 整行只取决于 x：算一行 512 像素再按行铺开，省掉 26 万次循环。
    n = len(_BAR_COLORS_75)
    bar_w = IMG_WIDTH // n
    px = np.array(_BAR_COLORS_75, np.uint8)
    idx = np.minimum(np.arange(IMG_WIDTH) // bar_w, n - 1)
    row = px[idx]
    return np.broadcast_to(row[None, :, :],
                           (IMG_HEIGHT, IMG_WIDTH, IMG_BYTES_PER_PIX)).tobytes()


def make_checker():
    """32x32 像素黑白棋盘。"""
    # WS_OPT_V1 只跟 (x//cell + y//cell) 的奇偶有关，一次算完整幅。
    cell = 32
    xc = np.arange(IMG_WIDTH) // cell
    yc = np.arange(IMG_HEIGHT) // cell
    v = (((xc[None, :] + yc[:, None]) & 1) * 255).astype(np.uint8)
    return np.repeat(v[..., None], IMG_BYTES_PER_PIX, -1).tobytes()


PATTERNS = {
    "gradient": make_gradient,
    "bars": make_colorbars,
    "checker": make_checker,
}


# --------------------------------------------------------------------------
# samples/ —— 随工具一起分发的**默认测试图**
#
# 出厂的"图样"下拉框 = 三个内置图样(gradient/bars/checker) + samples/ 里所有图片。
# 想再加图：把文件丢进 samples/，重启工具即可，**不用改代码**。
#
# 目前带的是 STARE 眼底图 `im0001`~`im0005`（P6 700x605 maxval=255，
# 1.27 MB/张，**保留原始分辨率未缩放**）。选它们时按本工具的统一约定
# 拉伸到 512x512（Pillow LANCZOS）再发 —— 和"载入图片…"完全同一条路，
# 所以 CLI 上 `imagelink.py <ip> <port> im0003` 也能直接用。
# --------------------------------------------------------------------------
SAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")

# 扫描时认得的扩展名；同名不同扩展名（im0001.ppm 与 im0001.png）时按此顺序取第一个
SAMPLE_EXTS = (".ppm", ".pnm", ".pgm", ".pbm", ".pam",
               ".png", ".bmp", ".tif", ".tiff", ".jpg", ".jpeg", ".gif", ".webp",
               ".rgb", ".raw", ".bin")


def list_samples():
    """扫描 samples/ 目录，返回 ``{显示名: 绝对路径}``（按显示名排序）。

    显示名 = 文件名去掉扩展名（``im0001.ppm`` → ``im0001``）。

    目录不存在、或里面没有认得出的图片，一律返回**空 dict**而不是报错 ——
    用户把图删光了，工具靠三个内置图样照样能跑。
    """
    found = {}
    try:
        names = os.listdir(SAMPLE_DIR)
    except OSError:
        return found
    for fn in sorted(names):
        stem, ext = os.path.splitext(fn)
        ext = ext.lower()
        if not stem or ext not in SAMPLE_EXTS:
            continue
        path = os.path.join(SAMPLE_DIR, fn)
        if not os.path.isfile(path):
            continue
        prev = found.get(stem)
        if prev is None or SAMPLE_EXTS.index(ext) < SAMPLE_EXTS.index(
                os.path.splitext(prev)[1].lower()):
            found[stem] = path
    return found


# 导入时扫一次；运行中往 samples/ 里加图，重启工具就能看到。
SAMPLES = list_samples()


def figure_names():
    """下拉框里该列的全部名字（三个内置图样 + samples/ 里的图），已排序。

    重名时**内置图样优先** —— ``PATTERNS`` 里的名字是文档承诺过的，
    不能被 samples/ 里一个同名文件顶掉；这种 sample 只在 :func:`list_samples`
    里可见，且会被 :func:`load_pattern` 忽略。
    """
    return sorted(set(PATTERNS) | set(SAMPLES))


def load_pattern(name):
    """按名字取一张图，返回 ``(裸 RGB888 字节, 中文说明)``。

    名字先当 samples/ 里的图找，再回落到三个内置图样；两处都没有抛 ``KeyError``。
    出图规则与 :func:`load_image` 完全一致（非 512x512 拉伸，正好 512x512
    的不重采样），所以选出来的就是**真正会被发出去的那 786432 字节**。
    """
    path = SAMPLES.get(name)
    if path is not None and name not in PATTERNS:
        return load_image(path)
    try:
        fn = PATTERNS[name]
    except KeyError:
        raise KeyError("既不认识图样 %r，samples/ 里也没有它（可选：%s）"
                       % (name, figure_names())) from None
    return fn(), "内置图样 '%s'，%d 字节 RGB888" % (name, IMG_TOTAL_BYTES)


# --------------------------------------------------------------------------
# 通用：把任意尺寸的 RGB 弄成 512x512
# --------------------------------------------------------------------------
def _resize_nearest(rgb, w, h, tw, th):
    """纯 Python 最近邻缩放（没有 Pillow 时的兜底）。

    rgb 是 w*h*3 的裸 RGB888。返回 tw*th*3。
    """
    out = bytearray(tw * th * 3)
    for ty in range(th):
        sy = (ty * h) // th
        srow = sy * w * 3
        trow = ty * tw * 3
        for tx in range(tw):
            sx = (tx * w) // tw
            s = srow + sx * 3
            t = trow + tx * 3
            out[t] = rgb[s]
            out[t + 1] = rgb[s + 1]
            out[t + 2] = rgb[s + 2]
    return bytes(out)


def _to_target(rgb, w, h, srcnote, size=(IMG_WIDTH, IMG_HEIGHT)):
    """已到目标尺寸就原样返回（逐字节不变）；size=None 时不做缩放（原生尺寸）。"""
    if size is None:
        return rgb, "%s（原生尺寸，未缩放）" % srcnote
    if (w, h) == size:
        return rgb, ("%s → 尺寸已是 %dx%d，未重采样（逐字节不变）"
                     % (srcnote, size[0], size[1]))
    try:
        from PIL import Image
    except ImportError:
        return (_resize_nearest(rgb, w, h, size[0], size[1]),
                "%s → 已拉伸为 %dx%d（最近邻；本机没装 Pillow）"
                % (srcnote, size[0], size[1]))
    im = Image.frombytes("RGB", (w, h), rgb).resize(size, Image.LANCZOS)
    return im.tobytes(), "%s → 已拉伸为 %dx%d（Pillow LANCZOS）" % (srcnote, size[0], size[1])


# --------------------------------------------------------------------------
# Netpbm 家族（P1..P7）—— 原生解码/编码，**不依赖 Pillow**
#
#   P1 PBM  ASCII 位图       P4 PBM  二进制位图
#   P2 PGM  ASCII 灰度       P5 PGM  二进制灰度
#   P3 PPM  ASCII 彩色       P6 PPM  二进制彩色
#   P7 PAM  带表头的任意通道（灰度/灰度+alpha/RGB/RGBA）
#
# 全部支持: 头里任意位置 # 注释、maxval 1..65535（>255 每样本 2 字节大端）、
# 以及 PBM 的 1=黑/0=白 语义。FPGA/图像处理圈子里这几种是最常见的交换格式。
# --------------------------------------------------------------------------
NETPBM_EXTS = (".ppm", ".pgm", ".pbm", ".pnm", ".pam")
_PBM_ASCII, _PGM_ASCII, _PPM_ASCII = b"P1", b"P2", b"P3"
_PBM_RAW, _PGM_RAW, _PPM_RAW, _PAM_RAW = b"P4", b"P5", b"P6", b"P7"


def _is_netpbm(data):
    return data[:1] == b"P" and data[1:2] in (b"1", b"2", b"3", b"4", b"5", b"6", b"7")


class _PnmReader(object):
    """解析 netpbm 头 + 光栅。"""

    def __init__(self, data, path="<data>"):
        self.d = data
        self.path = path
        self.i = 0

    def _skip_ws_and_comments(self):
        d, n = self.d, len(self.d)
        while self.i < n:
            c = d[self.i]
            if c == 0x23:                       # '#'
                while self.i < n and d[self.i] not in (0x0A, 0x0D):
                    self.i += 1
            elif c in (0x20, 0x09, 0x0A, 0x0D, 0x0B, 0x0C):
                self.i += 1
            else:
                return
        raise ValueError("%s: unexpected end of header" % self.path)

    def token(self):
        self._skip_ws_and_comments()
        start = self.i
        d, n = self.d, len(self.d)
        while self.i < n and d[self.i] not in (0x20, 0x09, 0x0A, 0x0D, 0x0B, 0x0C):
            self.i += 1
        if self.i == start:
            raise ValueError("%s: empty token" % self.path)
        return d[start:self.i]

    def int_token(self):
        return int(self.token())

    def end_header(self):
        """光栅起点：**只吃掉一个空白字符**（后面可能就是 0x0A/0x20 这样的像素值）。

        典型的 netpbm 陷阱：这里若"跳过所有空白"，以 0x0A 或 0x20 开头的像素
        就会被吞掉，整幅图随之错位。
        """
        d = self.d
        if self.i < len(d) and d[self.i] in (0x20, 0x09, 0x0A, 0x0D, 0x0B, 0x0C):
            if d[self.i] == 0x0D and self.i + 1 < len(d) and d[self.i + 1] == 0x0A:
                self.i += 2                     # CRLF 算一个分隔
            else:
                self.i += 1
        return self.i

    def ascii_samples(self, count, maxval):
        out = []
        for _ in range(count):
            v = self.int_token()
            if v < 0 or v > maxval:
                raise ValueError("%s: sample %d out of range 0..%d"
                                 % (self.path, v, maxval))
            out.append(v)
        return out

    def bin_samples(self, count, maxval):
        step = 1 if maxval < 256 else 2
        need = count * step
        chunk = self.d[self.i:self.i + need]
        if len(chunk) != need:
            raise ValueError("%s: raster truncated (need %d bytes, got %d)"
                             % (self.path, need, len(chunk)))
        self.i += need
        if step == 1:
            return list(chunk)
        return [(chunk[k] << 8) | chunk[k + 1] for k in range(0, need, 2)]


def load_netpbm(path, size=(IMG_WIDTH, IMG_HEIGHT)):
    """读 netpbm（P1..P7）→ (裸 RGB888, w, h, 说明)。不依赖任何第三方库。

    size=None 时按原生尺寸返回(不缩放)——自检/工具用; 默认拉伸到 512x512。
    """
    with open(path, "rb") as f:
        data = f.read()
    if not _is_netpbm(data):
        raise ValueError("%s: 不是 netpbm 文件（应以 P1..P7 开头）" % path)

    r = _PnmReader(data, path)
    magic = data[:2]
    r.i = 2

    if magic == _PAM_RAW:
        return _load_pam(r, size)

    w = r.int_token()
    h = r.int_token()
    if w <= 0 or h <= 0:
        raise ValueError("%s: bad size %dx%d" % (path, w, h))

    if magic == _PBM_ASCII:
        vals = r.ascii_samples(w * h, 1)
        return _finish_pbm(vals, w, h, path, size)
    if magic == _PBM_RAW:
        r.end_header()
        row_bytes = (w + 7) // 8
        need = row_bytes * h
        chunk = data[r.i:r.i + need]
        if len(chunk) != need:
            raise ValueError("%s: raster truncated (%d, need %d)" % (path, len(chunk), need))
        bits = []
        for y in range(h):
            base = y * row_bytes
            for x in range(w):
                byte = chunk[base + (x >> 3)]
                bits.append((byte >> (7 - (x & 7))) & 1)
        return _finish_pbm(bits, w, h, path, size)

    maxval = r.int_token()
    if maxval < 1 or maxval > 65535:
        raise ValueError("%s: bad maxval %d" % (path, maxval))

    if magic == _PGM_ASCII:
        g = r.ascii_samples(w * h, maxval)
        rgb = _gray_to_rgb(g, maxval)
    elif magic == _PPM_ASCII:
        s = r.ascii_samples(w * h * 3, maxval)
        rgb = _scale_rgb(s, maxval)
    elif magic == _PGM_RAW:
        r.end_header()
        g = r.bin_samples(w * h, maxval)
        rgb = _gray_to_rgb(g, maxval)
    elif magic == _PPM_RAW:
        r.end_header()
        s = r.bin_samples(w * h * 3, maxval)
        rgb = _scale_rgb(s, maxval)
    else:
        raise ValueError("%s: unsupported netpbm magic %r" % (path, magic))

    note = "Netpbm %s %dx%d maxval=%d" % (magic.decode(), w, h, maxval)
    return _to_target(rgb, w, h, note, size)


def _load_pam(r, size):
    """P7 (PAM): 行式表头 + 通用通道数。"""
    depth = None
    width = None
    height = None
    maxval = None
    tupletype = ""
    while True:
        tok = r.token()
        if tok == b"ENDHDR":
            break
        if tok == b"WIDTH":
            width = r.int_token()
        elif tok == b"HEIGHT":
            height = r.int_token()
        elif tok == b"DEPTH":
            depth = r.int_token()
        elif tok == b"MAXVAL":
            maxval = r.int_token()
        elif tok == b"TUPLTYPE":
            tupletype = r.token().decode(errors="replace")
        else:
            raise ValueError("%s: unexpected PAM header token %r" % (r.path, tok))
    if None in (depth, width, height, maxval):
        raise ValueError("%s: PAM header missing WIDTH/HEIGHT/DEPTH/MAXVAL" % r.path)
    if maxval < 1 or maxval > 65535:
        raise ValueError("%s: PAM bad maxval %d" % (r.path, maxval))
    if depth not in (1, 2, 3, 4):
        raise ValueError("%s: PAM DEPTH=%d unsupported" % (r.path, depth))
    r.i = r.end_header()
    s = r.bin_samples(width * height * depth, maxval)

    rgb = bytearray(width * height * 3)
    for p in range(width * height):
        base = p * depth
        if depth == 1:
            v = _scale8(s[base], maxval)
            rgb[p * 3] = rgb[p * 3 + 1] = rgb[p * 3 + 2] = v
        elif depth == 2:                        # 灰度 + alpha：丢掉 alpha
            v = _scale8(s[base], maxval)
            rgb[p * 3] = rgb[p * 3 + 1] = rgb[p * 3 + 2] = v
        else:                                   # RGB / RGBA：丢掉 alpha
            rgb[p * 3] = _scale8(s[base], maxval)
            rgb[p * 3 + 1] = _scale8(s[base + 1], maxval)
            rgb[p * 3 + 2] = _scale8(s[base + 2], maxval)
    note = "Netpbm P7 (PAM) %dx%d depth=%d maxval=%d%s" % (
        width, height, depth, maxval, (" %s" % tupletype) if tupletype else "")
    return _to_target(bytes(rgb), width, height, note, size)


def _scale8(v, maxval):
    """按 maxval 线性缩放到 0..255。"""
    if maxval == 255:
        return v
    return (v * 255 + maxval // 2) // maxval


def _scale_rgb(samples, maxval):
    if maxval == 255:
        return bytes(samples)
    out = bytearray(len(samples))
    for k, v in enumerate(samples):
        out[k] = _scale8(v, maxval)
    return bytes(out)


def _gray_to_rgb(gray, maxval):
    out = bytearray(len(gray) * 3)
    for k, v in enumerate(gray):
        v8 = _scale8(v, maxval)
        out[k * 3] = out[k * 3 + 1] = out[k * 3 + 2] = v8
    return bytes(out)


def _finish_pbm(bits, w, h, path, size):
    """PBM 每像素 1 bit：**1 = 黑(0), 0 = 白(255)**。"""
    out = bytearray(w * h * 3)
    for k, b in enumerate(bits):
        v = 0 if b else 255
        out[k * 3] = out[k * 3 + 1] = out[k * 3 + 2] = v
    return _to_target(bytes(out), w, h, "Netpbm PBM %dx%d (1=黑)" % (w, h), size)


# --------------------------------------------------------------------------
# 从文件读
# --------------------------------------------------------------------------
def load_raw(path):
    """读裸 RGB888 文件，长度必须正好 786432。"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) != IMG_TOTAL_BYTES:
        raise ValueError("%s: %d bytes, expected %d"
                         % (path, len(data), IMG_TOTAL_BYTES))
    return data


def load_ppm(path):
    """读 P6 二进制 PPM（只支持 maxval<=255）。保留给老调用方；新代码用 load_netpbm。"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"P6":
        raise ValueError("%s: not a P6 PPM" % path)
    idx, n, fields = 2, len(data), []
    while len(fields) < 3:
        while idx < n and data[idx:idx + 1].isspace():
            idx += 1
        if data[idx:idx + 1] == b"#":
            while idx < n and data[idx:idx + 1] not in (b"\n", b"\r"):
                idx += 1
            continue
        start = idx
        while idx < n and not data[idx:idx + 1].isspace():
            idx += 1
        fields.append(int(data[start:idx]))
    idx += 1
    w, h, maxv = fields
    if maxv > 255:
        raise ValueError("%s: maxval=%d not supported" % (path, maxv))
    if (w, h) != (IMG_WIDTH, IMG_HEIGHT):
        raise ValueError("%s: %dx%d, expected %dx%d"
                         % (path, w, h, IMG_WIDTH, IMG_HEIGHT))
    payload = data[idx:idx + IMG_TOTAL_BYTES]
    if len(payload) != IMG_TOTAL_BYTES:
        raise ValueError("%s: pixel data truncated (%d)" % (path, len(payload)))
    return payload


def load_and_stretch(path, size=(IMG_WIDTH, IMG_HEIGHT)):
    """用 Pillow 读**任意格式**图片 → 转 RGB → 拉伸到 512x512 → 裸 RGB888 字节。

    已经是目标尺寸的**不做重采样**，保证"载入一张 512x512 的图再发出去"逐字节不变
    —— 这点对验证很重要(回图要和原图 bit-exact 比对)。

    @return (raw_bytes, note)  note 是给界面显示的中文说明。
    """
    try:
        from PIL import Image
    except ImportError as e:                      # pragma: no cover
        raise RuntimeError(
            "读这个格式需要 Pillow，但这台机器上没装上。\n"
            "装法：python -m pip install --user pillow\n"
            "（PPM/PGM/PBM/PNM/PAM 和裸 RGB888 不需要 Pillow，可以直接用）\n"
            "原始错误：%s" % e)

    with Image.open(path) as im:
        src_mode = im.mode
        w, h = im.size
        rgb = im if im.mode == "RGB" else im.convert("RGB")
        data = rgb.tobytes()

    return _to_target(data, w, h, "%dx%d %s" % (w, h, src_mode), size)


def load_image(path):
    """读入任意图片并**统一成 512x512 RGB888**，返回 (raw_bytes, note)。

    分发规则（按顺序）：

      * 文件头是 ``P1..P7``（Netpbm 家族）→ **原生解码**，不依赖 Pillow；
        支持 PBM/PGM/PPM/PAM 的 ASCII 与二进制两种写法、maxval 1..65535；
      * `.rgb` / `.raw` / `.bin` → 裸 RGB888，长度必须正好 786432，**原样透传**；
      * 其它（PNG / JPG / BMP / GIF / TIF / WebP …）→ 交给 Pillow 解码。

    非 512x512 的一律拉伸（优先 Pillow LANCZOS；没装 Pillow 时对 Netpbm
    回退到最近邻），**目标尺寸的不重采样**以保证逐字节可比。

    `note` 是一句中文说明，界面上直接显示。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".rgb", ".raw", ".bin"):
        data = load_raw(path)
        return data, "裸 RGB888，%d 字节，原样透传" % len(data)

    # Netpbm: 认文件头(比扩展名可靠), 但也接受扩展名提示
    with open(path, "rb") as f:
        head = f.read(2)
    if _is_netpbm(head):
        return load_netpbm(path)

    return load_and_stretch(path)


def save_raw(path, image):
    with open(path, "wb") as f:
        f.write(image)


def _luma_gray(image):
    """RGB888 → 8 位灰度，权重与 PL 侧 img_proc_axis 的 (77R+150G+29B)>>8 一致。"""
    out = bytearray(len(image) // 3)
    for k in range(0, len(image), 3):
        out[k // 3] = (77 * image[k] + 150 * image[k + 1] + 29 * image[k + 2]) >> 8
    return bytes(out)


def luma_histogram(image, exclude_extremes=False):
    """256 桶灰度直方图，灰度定义与 PL 完全一致((77R+150G+29B)>>8)。

    这样算出来的阈值直接就是"板子会用的那个灰度"上的阈值 —— 阈值分割之前
    板子算的正是这个灰度，所以预设值选得准。

    ``exclude_extremes=True`` 时**不计灰度为 0 或 255 的像素**：
    纯黑/纯白往往只是背景（图像的黑边、过曝白块、拼图留白），
    把它们算进去会把 Otsu 拽向"背景 vs 内容"而不是"内容内部的明暗分界"。
    """
    hist = [0] * 256
    n = len(image) - 2
    if exclude_extremes:
        for k in range(0, n, 3):
            v = (77 * image[k] + 150 * image[k + 1] + 29 * image[k + 2]) >> 8
            if v != 0 and v != 255:
                hist[v] += 1
        return hist
    for k in range(0, n, 3):
        hist[(77 * image[k] + 150 * image[k + 1] + 29 * image[k + 2]) >> 8] += 1
    return hist


def otsu_threshold(hist):
    """Otsu 大津法：取让"前景/背景两类间方差"最大的那个门限，返回 0..255。

    纯 Python 遍历 256 个候选，与像素数无关，很快。直方图全空或全在一个桶上时
    返回 CTRL_THR_DEFAULT。
    """
    total = sum(hist)
    if total <= 0:
        return CTRL_THR_DEFAULT
    sum_all = 0
    for v in range(256):
        sum_all += v * hist[v]

    w_b = 0
    sum_b = 0
    best_t = CTRL_THR_DEFAULT
    best_var = -1.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = float(sum_b) / w_b
        m_f = float(sum_all - sum_b) / w_f
        var = float(w_b) * float(w_f) * (m_b - m_f) * (m_b - m_f)
        if var > best_var:
            best_var = var
            best_t = t
    return best_t


def _smooth_hist(hist, r=1):
    """滑动窗口取平均的直方图（用于找谷，避免被单桶噪声带偏）。

    半径只取 1：窗口再大就会把"只有一个桶宽的尖峰"抹平，谷的判定跟着失准
    （踩过：r=3 时合成图里 60% 的近黑峰被摊到 7 个桶上，谷反而不够深）。
    """
    out = [0.0] * 256
    for v in range(256):
        a = v - r if v - r > 0 else 0
        b = v + r + 1 if v + r + 1 < 256 else 256
        out[v] = float(sum(hist[a:b])) / float(b - a)
    return out


def _border_dark_fraction(image, gray_max, width=2):
    """图像外圈（默认 2 像素宽）里，灰度 < gray_max 的像素占比。

    用来分辨"背景"和"暗物体"：**背景总贴着图像边缘**（黑边、圆窗外的黑角），
    而画面中间的深色物体不是。只在边框上采样，代价可以忽略。
    """
    w = IMG_WIDTH
    h = IMG_HEIGHT
    dark = 0
    total = 0
    for y in range(h):
        edge_row = (y < width) or (y >= h - width)
        for x in range(w):
            if not edge_row and width <= x < w - width:
                continue                       # 只走外圈
            k = (y * w + x) * 3
            g = (77 * image[k] + 150 * image[k + 1] + 29 * image[k + 2]) >> 8
            total += 1
            if g < gray_max:
                dark += 1
    return float(dark) / float(total) if total else 0.0


def auto_gray_floor(image, hist=None, t1=None):
    """自动定"灰度最小值"：暗半区里**第一个符合条件的最深谷** = 暗背景的右沿。

    为什么要这么绕：暗端常常不止一个结构。以眼底图为例，从暗到亮是
    "一圈细黑边(0-3) → 谷(~8) → 深色背景环(16-31) → 深谷(~35) → 内容(48+)"。
    **取第一个谷会切在 8**，只削掉 3.7% 的细边、背景环原封不动；
    得让"左侧暗区至少占 5%"这条把 8 筛掉，才轮到 35（正好是背景环的右沿，削 26%）。

    候选谷要同时满足四条才采用，否则返回 0（=不排除任何像素）：
    ① 是局部极小；② 左侧暗区 ≥ 全图 5%；③ 谷深 ≤ 两侧峰较小者的 30%；
    ④ **暗区要贴在图像边框上**（≥30% 的边框像素是暗的）—— 背景总在边上，
    画面中间的深色物体不该被当背景排掉。平坦直方图（渐变图）在③被挡掉，
    细边（<5%）在②被挡掉，居中的暗物体在④被挡掉。

    @param image 裸 RGB888（④ 需要在像素上看边框）
    @param hist  可选，全图直方图
    @param t1    可选，全图一级 Otsu 阈值（只用来做"暗端有没有东西可切"的粗筛）
    @return 灰度下限 0..127；0 表示不排除
    """
    if hist is None:
        hist = luma_histogram(image)
    if t1 is None:
        t1 = otsu_threshold(hist)
    if t1 <= 1:
        return 0                        # 一级 Otsu 就是 0：暗端没有可切的东西
    total = sum(hist)
    if total <= 0:
        return 0

    sm = _smooth_hist(hist, 1)
    for v in range(1, 128):             # 背景只在暗半区
        if sm[v] > sm[v - 1] or sm[v] > sm[v + 1]:
            continue                    # 只考虑局部极小
        if sum(hist[:v]) * 20 < total:  # ① 左边的"暗背景"至少 5%
            continue
        peak_l = max(sm[:v + 1])
        peak_r = max(sm[v:])
        if peak_l <= 0.0 or peak_r <= 0.0:
            continue
        if sm[v] * 10.0 > 3.0 * min(peak_l, peak_r):     # ② 谷要够深（≤30%）
            continue
        if _border_dark_fraction(image, v) < 0.30:       # ③ 暗区得贴着边框
            continue
        return v                        # 第一个满足条件的谷
    return 0


def sobel_magnitude(image):
    """Sobel 幅度图（|gx|+|gy| 截到 255），返回 512×512 的 bytes。

    与 PL 的 mode 2 同构（同样的 3×3 权重、同样的边界零填充、同样的"约化梯度"），
    用于在 PC 上给"边缘阈值"挑一个推荐值。**不强求与 PL 逐值一致** ——
    板子上真正的边缘图以 PL 的输出为准。
    """
    gray = _luma_gray(image)
    w = IMG_WIDTH
    h = IMG_HEIGHT
    out = bytearray(w * h)
    for y in range(1, h - 1):
        r0 = (y - 1) * w
        r1 = y * w
        r2 = (y + 1) * w
        for x in range(1, w - 1):
            tl = gray[r0 + x - 1]
            tc = gray[r0 + x]
            tr = gray[r0 + x + 1]
            ml = gray[r1 + x - 1]
            mr = gray[r1 + x + 1]
            bl = gray[r2 + x - 1]
            bc = gray[r2 + x]
            br = gray[r2 + x + 1]
            gx = (tl + 2 * ml + bl) - (tr + 2 * mr + br)
            gy = (tl + 2 * tc + tr) - (bl + 2 * bc + br)
            mag = abs(gx) + abs(gy)
            out[r1 + x] = 255 if mag > 255 else mag
    return bytes(out)


def edge_threshold(image, pct=92, floor=24):
    """给"边缘叠加"(模式 6)挑一个推荐阈值：**Sobel 幅度的 pct 分位**。

    边缘幅度的直方图极度偏向 0（大多数像素是平坦区），Otsu 会被压得很低、
    画得满屏都是边。所以这里改用分位数："让大约 (100-pct)% 的像素被判成边缘"，
    视觉上干净可控。再取个下限 floor，避免平坦图也画出一堆噪点。

    @return (thr, note)
    """
    mag = sobel_magnitude(image)
    hist = [0] * 256
    for v in mag:
        hist[v] += 1
    total = sum(hist)
    if total <= 0:
        return CTRL_THR_DEFAULT, "空图"
    target = total * (100 - pct) // 100
    acc = 0
    thr = floor
    for v in range(255, -1, -1):
        acc += hist[v]
        if acc >= target:
            thr = max(v, floor)
            break
    note = ("边缘阈值(自动) = %d：Sobel 幅度前 %d%% 判为边缘、画白，其余保持原图"
            % (thr, 100 - pct))
    return thr, note


def auto_threshold(image, exclude_extremes=True, gray_min=None):
    """根据图片本身给一个合适的阈值分割门限（Otsu），返回 (thr, hist, note)。

    **算之前先自动定一个"灰度最小值"**（``gray_min``，None = 自动）：
    低于它的像素当作背景，**不计入**直方图（见 :func:`auto_gray_floor`）。
    另外灰度为 0/255 的纯黑/纯白像素也一律不计（图像黑边、过曝白块、拼图留白）。
    这些都只影响**挑阈值所用的统计** —— 板子仍然对**全部**像素应用这个阈值，
    所以提示里的占比是按全图算的，和板子回图对得上。
    排除得太狠（剩下不到 1/4）时会**自动退回全量**。

    再做**两级 Otsu**（很便宜，就是在同一张直方图上跑两次）：

    * 一级 = 背景/物体（最稳妥的默认值）。
    * 二级 = 把一级以下的所有桶清零后再跑一次 = "物体内部"
      （眼底图上正好是 **血管 vs 盘内背景**）。

    note 里两个值都写出来，界面上直接显示；用户也可以拖滑块自己试。
    注意 PL 的判据是 ``gray >= thr`` 为白，所以"黑"是 ``gray < thr``
    —— 统计时必须用 ``hist[:thr]``，别把 ``== thr`` 那个桶算进黑里。
    """
    hist_all = luma_histogram(image)
    total_all = sum(hist_all)

    floor = gray_min
    if floor is None:
        floor = auto_gray_floor(image, hist_all, otsu_threshold(hist_all))
    floor = max(0, min(int(floor), 255))

    hist = list(hist_all)
    # 低于灰度下限的一律当背景；下限为 0 时也仍然排掉纯黑(0)（用户 2026-10-02 的要求）
    lo_excl = max(floor, 1) if exclude_extremes else floor
    for v in range(0, min(lo_excl, 256)):
        hist[v] = 0
    if exclude_extremes:
        hist[255] = 0                                  # 过曝白块同理

    # 排除得太狠就退回全量（本来就是黑白的图，比如棋盘/二值图）
    excluded = total_all - sum(hist)
    if sum(hist) * 4 < total_all:
        hist = hist_all
        floor = 0
        excluded = 0

    thr = otsu_threshold(hist)
    # 占比按**全图**算：板子是把阈值应用到所有像素上的（含被排除的那些）
    black_all = sum(hist_all[:thr])
    note = "Otsu 一级=%d（全图黑占 %.0f%%）" % (
        thr, 100.0 * black_all / total_all if total_all else 0.0)
    if floor > 0 and total_all > 0:
        note += "｜排除< %d 的背景 %.0f%%" % (floor, 100.0 * excluded / total_all)

    # 二级：把一级以下清零后在同一张直方图上再跑一次 Otsu = "物体内部"的分割
    tail = list(hist)
    for v in range(0, min(thr + 1, 256)):
        tail[v] = 0
    tail_total = sum(tail)
    busy_bins = sum(1 for h in tail if h)
    # 物体内部要"够大"且"够分散"才值得报 —— 否则会给出误导性的值
    # (比如只有两个灰阶的合成图, 尾部只剩一个桶, Otsu 会吐出个 128)
    if tail_total > 0 and tail_total * 20 >= sum(hist) and busy_bins >= 8:
        thr2 = otsu_threshold(tail)
        if thr2 > thr:
            note += "｜二级=%d" % thr2
    return thr, hist, note


def to_pgm_bytes(image):
    """P5 二进制 PGM（灰度）。"""
    return b"P5\n%d %d\n255\n" % (IMG_WIDTH, IMG_HEIGHT) + _luma_gray(image)


def to_pbm_bytes(image, thr=128):
    """P4 二进制 PBM（1 = 黑）。按灰度阈值二值化。"""
    gray = _luma_gray(image)
    row_bytes = (IMG_WIDTH + 7) // 8
    out = bytearray(row_bytes * IMG_HEIGHT)
    for y in range(IMG_HEIGHT):
        base = y * row_bytes
        row = y * IMG_WIDTH
        for x in range(IMG_WIDTH):
            if gray[row + x] < thr:
                out[base + (x >> 3)] |= 0x80 >> (x & 7)
    return b"P4\n%d %d\n" % (IMG_WIDTH, IMG_HEIGHT) + bytes(out)


def to_pam_bytes(image):
    """P7 PAM（RGB, depth=3）。"""
    hdr = (b"P7\nWIDTH %d\nHEIGHT %d\nDEPTH 3\nMAXVAL 255\n"
           b"TUPLTYPE RGB\nENDHDR\n" % (IMG_WIDTH, IMG_HEIGHT))
    return hdr + image


def save_image(path, image):
    """把裸 RGB888 存成文件, 格式由扩展名决定。

      * `.rgb` / `.raw` / `.bin` -> 裸 RGB888, 原样写;
      * `.ppm` / `.pnm`           -> P6 二进制 PPM;
      * `.pgm`                    -> P5 二进制 PGM（灰度, 与 PL 同一套权重）;
      * `.pbm`                    -> P4 二进制 PBM（阈值二值）;
      * `.pam`                    -> P7 PAM（RGB）;
      * 其它(.png/.jpg/.bmp/.tif/.webp) -> 交给 Pillow 编码(需要装 Pillow)。

    @param image  裸 RGB888 bytes, 长度必须 786432。
    @return 写入的路径(与入参相同)。
    """
    if len(image) != IMG_TOTAL_BYTES:
        raise ValueError("image must be %d bytes, got %d"
                         % (IMG_TOTAL_BYTES, len(image)))
    ext = os.path.splitext(path)[1].lower()
    if ext in (".rgb", ".raw", ".bin"):
        save_raw(path, image)
    elif ext in (".ppm", ".pnm"):
        with open(path, "wb") as f:
            f.write(to_ppm_bytes(image))
    elif ext == ".pgm":
        with open(path, "wb") as f:
            f.write(to_pgm_bytes(image))
    elif ext == ".pbm":
        with open(path, "wb") as f:
            f.write(to_pbm_bytes(image))
    elif ext == ".pam":
        with open(path, "wb") as f:
            f.write(to_pam_bytes(image))
    else:
        try:
            from PIL import Image
        except ImportError as e:                    # pragma: no cover
            raise RuntimeError(
                "保存 %s 需要 Pillow, 但没装上。\n"
                "可以改用 .rgb / .ppm / .pgm / .pbm / .pam 扩展名存（这些不需要 Pillow）。\n"
                "原始错误：%s" % (ext, e))
        Image.frombytes("RGB", (IMG_WIDTH, IMG_HEIGHT), image).save(path)
    return path


# --------------------------------------------------------------------------
# 显示用：转 PPM
# --------------------------------------------------------------------------
def to_ppm_bytes(image):
    """把裸 RGB888 打包成 P6 PPM（tkinter 的 PhotoImage 认得）。"""
    return b"P6\n%d %d\n255\n" % (IMG_WIDTH, IMG_HEIGHT) + image


def make_photo(tk_module, image, keep=None):
    """给 tkinter 用的 PhotoImage。

    优先用 base64 内联数据（不落盘）；如果这个 Tk 构建不支持，再退回临时文件。
    `keep` 传一个 list 时，成功创建的 PhotoImage 会 append 进去，
    防止被 GC 掉（tkinter 的经典坑）。
    """
    ppm = to_ppm_bytes(image)
    photo = None
    # WS_OPT_V1 快路径：Pillow 直接把裸 RGB888 变 PhotoImage，512x512 实测 23 ms -> 1.1 ms。
    # 走 Tk 的 -data base64 那条老路时 Tk 要自己解 base64 再逐像素建图，慢一个数量级。
    if len(image) == IMG_TOTAL_BYTES:
        try:
            from PIL import Image as _Im, ImageTk as _ImTk
            photo = _ImTk.PhotoImage(_Im.frombytes("RGB", (IMG_WIDTH, IMG_HEIGHT), image))
        except Exception:
            photo = None
    if photo is None:
        try:
            photo = tk_module.PhotoImage(data=base64.b64encode(ppm))
        except Exception:
            photo = None

    if photo is None:
        tmp = os.path.join(os.environ.get("TEMP", "."), "_tcp_image_ui_%d.ppm"
                           % (abs(hash(image)) % 100000))
        with open(tmp, "wb") as f:
            f.write(ppm)
        photo = tk_module.PhotoImage(file=tmp)

    if keep is not None:
        keep.append(photo)
    return photo


# ══════════════════════════════════════════════════════════════════════════
# 来源：interactive.py
# ══════════════════════════════════════════════════════════════════════════

"""interactive.py —— 点击纠错（F2）：把“图像 + 先验掩膜 + 点击”编码进一帧 RGB 发给板子。

板子上跑的还是同一个 30 节点网络，只是用了另一套参数（模型库里的 echo_inter）。三个通道：

    R  原图（灰度图本来就是 R=G=B，取 R）
    G  先验掩膜：0 / 255。来自上一次的结果（第一步是自动分割的结果）
    B  有符号点击图：round(128 + 127·pos − 128·neg)，夹到 0..255
       pos / neg = 各正 / 负点的锥形 max(0, 1 − d / 40) 取最大，d 是 512 画布上的像素距离

板子在 128 档按 x[::4, ::4] 取样，锥形就变成训练时用的 128 尺度、半径 10 的锥形
（echo/train_inter.py 的 click_channel / cone，数学上完全相同）。

纯 Python、无 numpy，与 confidence.py 一致。
"""

import math


RADIUS = 40.0                     # 512 画布上的点击锥形半径（= 128 档的 10 像素）
W, H = proto.IMG_WIDTH, proto.IMG_HEIGHT


def click_plane(clicks):
    """clicks: [(x, y, positive)]（512 画布坐标）-> 512x512 字节，每像素一个 B 通道值。"""
    pos = {}
    neg = {}
    r = int(math.ceil(RADIUS))
    for x, y, positive in clicks:
        target = pos if positive else neg
        for py in range(max(0, int(y) - r), min(H, int(y) + r + 1)):
            dy = py - y
            row = py * W
            for px in range(max(0, int(x) - r), min(W, int(x) + r + 1)):
                c = 1.0 - math.sqrt(dy * dy + (px - x) ** 2) / RADIUS
                if c > 0 and c > target.get(row + px, 0.0):
                    target[row + px] = c
    out = bytearray(b"\x80" * (W * H))
    for k in set(pos) | set(neg):
        v = int(round(128 + 127 * pos.get(k, 0.0) - 128 * neg.get(k, 0.0)))
        out[k] = 0 if v < 0 else (255 if v > 255 else v)
    return bytes(out)


def prior_from_plane(plane, thr):
    """logit 偏移码平面（confidence.logit_plane）-> 先验掩膜（每像素 0 / 1）。"""
    lut = bytes(1 if v - proto.LOGIT_IMG_OFFSET >= thr else 0 for v in range(256))
    return plane.translate(lut)


def compose(image, prior, clicks):
    """原图 RGB（786432 字节）+ 先验（262144 字节 0/1）+ 点击 -> 要发给板子的 RGB。"""
    if len(image) != proto.IMG_TOTAL_BYTES or len(prior) != W * H:
        raise ValueError("compose: bad sizes")
    out = bytearray(proto.IMG_TOTAL_BYTES)
    out[0::3] = image[0::3]
    out[1::3] = prior.translate(bytes([0, 255] + [255] * 254))
    out[2::3] = click_plane(clicks)
    return bytes(out)


def compose_seq(image, prior, prev_image):
    """序列传播（F3，echo_seq）：R = 本帧，G = 上一步的结果 0/255，B = 上一步那一帧（取 R）。"""
    if len(image) != proto.IMG_TOTAL_BYTES or len(prev_image) != proto.IMG_TOTAL_BYTES or len(prior) != W * H:
        raise ValueError("compose_seq: bad sizes")
    out = bytearray(proto.IMG_TOTAL_BYTES)
    out[0::3] = image[0::3]
    out[1::3] = prior.translate(bytes([0, 255] + [255] * 254))
    out[2::3] = prev_image[0::3]
    return bytes(out)


class Session(object):
    """一张图上的一轮纠错：先验、点击、每一步的回传（用于撤销）。"""

    def __init__(self, prior, images, ai_stats):
        self.history = [(prior, [], images, ai_stats)]

    @property
    def prior(self):
        return self.history[-1][0]

    @property
    def clicks(self):
        return list(self.history[-1][1])

    def push(self, prior, clicks, images, ai_stats):
        self.history.append((prior, list(clicks), images, ai_stats))

    def undo(self):
        """回到上一步；返回该步的 (images, ai_stats)。已经在起点时返回 None。"""
        if len(self.history) <= 1:
            return None
        self.history.pop()
        return self.history[-1][2], self.history[-1][3]

    def reset(self):
        del self.history[1:]
        return self.history[0][2], self.history[0][3]


def _self_check_interactive():
    errs = []
    blank = click_plane([])
    if blank != b"\x80" * (W * H):
        errs.append("no clicks must give 128 everywhere")
    p = click_plane([(100, 200, True)])
    if p[200 * W + 100] != 255 or p[200 * W + 100 + 40] != 128 or p[200 * W + 100 + 20] != 192:
        errs.append("positive cone wrong: %d %d %d" % (p[200 * W + 100], p[200 * W + 140], p[200 * W + 120]))
    n = click_plane([(100, 200, False)])
    if n[200 * W + 100] != 0 or n[200 * W + 120] != 64:
        errs.append("negative cone wrong")
    img = bytes(range(256)) * (proto.IMG_TOTAL_BYTES // 256)
    prior = bytes([1]) * 10 + bytes(W * H - 10)
    c = compose(img, prior, [])
    if c[0::3] != img[0::3] or c[1] != 255 or c[31] != 0 or c[2] != 128:
        errs.append("compose channel layout wrong")
    prev = bytes(reversed(img))
    s = compose_seq(img, prior, prev)
    if s[0::3] != img[0::3] or s[2::3] != prev[0::3] or s[1] != 255 or s[31] != 0:
        errs.append("compose_seq channel layout wrong")
    return errs


SELF_CHECK_ERRORS_INTERACTIVE = _self_check_interactive()


# ══════════════════════════════════════════════════════════════════════════
# 来源：uistyle.py
# ══════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════
# 来源：sequence.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""sequence.py —— F3 序列跟踪：心超序列逐帧上板，上一帧的结果当先验，轮廓随动、面积曲线、ED / ES。

每一帧仍是板子上同一个 30 节点网络，只按模型库换参数：
  第 0 步（或没有先验时）  自动分割模型（echo_lv，2 号），帧原样发；
  之后每一步              传播模型（模型表里 echo_lv 的 "seq"，即 echo_seq）：
                          R = 本帧，G = 上一步的结果（0 / 255），B = 上一步那一帧（运动线索）
                          （没有 "seq" 时退用点击纠错模型、B = 128；离线评测表明它跟不上收缩，见 echo/README F3）
  暂停时在当前帧上点击      echo_inter：G = 当前结果，B = 点击锥形（与 F2 完全相同）；
                          之后的帧从纠正后的结果继续传播
面积按板端掩膜换算回原图像素（与离线评测 seg_metrics 的口径相同：512 网格按 Pillow NEAREST
映射回原尺寸），有像素间距时换成 cm²。面积最大的帧标 ED、最小的标 ES（候选），
FAC = (A_ED − A_ES) / A_ED。只用于工程教学与算法演示，不作临床结论。

载入：装帧图片的文件夹（按文件名排序；可放一个 sequence.json 写 spacing_mm / fps / note）、
CAMUS 的 .nii / .nii.gz（uint8，读头里的像素间距）、或多帧 GIF / TIFF。都需要 Pillow。

射血分数（F4）：二腔 / 四腔各放完一段后「记入当前视图」（取面积曲线上标的 ED / ES 候选帧的掩膜），
「算 EF」用 ef_simpson.py（CAMUS 官方笔记本的双平面 Simpson 圆盘法，与离线评测同一份代码）；需要 numpy / scipy /
scikit-image，没有时这一栏变灰，其余照常。

实时通路（板端 v5.4+，统计块 version >= 7）：只发 128x128 灰度（紧凑帧），上一帧的结果由板端保存并拼进
G 通道，后台线程最多 2 帧在途，界面限频刷新；老板端自动退回整幅帧通路。两条通路的结果逐字节相同。

核心（load_sequence / Tracker / contour_rgb / AreaMapper / ef_slot / compute_ef）不依赖 Tk，便于脚本验收；
窗口类 SeqWindow 由 app.py 的「序列跟踪…」打开，和主界面共用同一条板子连接。
"""

import array
import gzip
import json
import os
import queue
import re
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


W, H = proto.IMG_WIDTH, proto.IMG_HEIGHT
FRAME_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".pgm", ".ppm", ".pnm", ".webp")
CONTOUR_RGB = (0, 230, 0)
NIFTI_TYPES = {4: ("h", 2), 512: ("H", 2), 16: ("f", 4), 64: ("d", 8)}   # datatype -> (array 类型码, 字节数)


# ---------------------------------------------------------------- 读序列
def _gray_to_512(gray, w, h):
    """灰度帧 -> 与 imageio.load_image 相同的 512x512 RGB（Pillow：转 RGB 再 LANCZOS）。"""
    from PIL import Image
    rgb = Image.frombytes("L", (w, h), gray).convert("RGB").tobytes()
    return imageio._to_target(rgb, w, h, "")[0]


def read_nifti_frames(path):
    """CAMUS 用的 NIfTI-1（uint8 / int16 / uint16 / float32 / float64，x 最快）-> ([每帧灰度 bytes], 宽, 高, (dx, dy) mm)。纯标准库。"""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    for e in "<>":
        if struct.unpack(e + "i", raw[:4])[0] == 348:
            break
    else:
        raise ValueError("不是 NIfTI-1 文件：%s" % path)
    dim = struct.unpack(e + "8h", raw[40:56])
    datatype = struct.unpack(e + "h", raw[70:72])[0]
    pixdim = struct.unpack(e + "8f", raw[76:108])
    off = int(struct.unpack(e + "f", raw[108:112])[0])
    nx, ny = dim[1], dim[2]
    nt = dim[3] if dim[0] >= 3 else 1
    n = nx * ny
    if datatype == 2:                                    # uint8：原样
        frames = [raw[off + t * n: off + (t + 1) * n] for t in range(nt)]
    elif datatype in NIFTI_TYPES:                        # CAMUS 的半周期序列是 float32
        code, size = NIFTI_TYPES[datatype]
        vals = array.array(code)
        vals.frombytes(raw[off: off + n * nt * size])
        if (e == ">") != (sys.byteorder == "big"):
            vals.byteswap()
        # 与离线缓存的 astype(np.uint8) 相同（像素值本来就是 0..255 的整数；越界的夹到 0..255）
        u8 = bytes(0 if v < 0 else (255 if v > 255 else int(v)) for v in vals)
        frames = [u8[t * n: (t + 1) * n] for t in range(nt)]
    else:
        raise ValueError("不支持的 NIfTI 数据类型 datatype=%d" % datatype)
    return frames, nx, ny, (float(pixdim[1]), float(pixdim[2]))


def load_sequence(path):
    """-> (frames: [512x512 RGB bytes], info dict)。info: name, n, width, height, spacing_mm, fps, note。"""
    from PIL import Image, ImageSequence
    info = dict(name=os.path.basename(path.rstrip("/\\")), spacing_mm=None, fps=None, note="")
    frames = []
    low = path.lower()
    if os.path.isdir(path):
        names = sorted(n for n in os.listdir(path) if n.lower().endswith(FRAME_EXTS)
                       and not os.path.splitext(n)[0].lower().endswith("_gt"))
        if not names:
            raise ValueError("文件夹里没有帧图片：%s" % path)
        for n in names:
            p = os.path.join(path, n)
            with Image.open(p) as im:
                size = im.size
            frames.append(imageio.load_image(p)[0])
            if "width" in info and (info["width"], info["height"]) != size:
                raise ValueError("各帧尺寸不一致：%s" % n)
            info["width"], info["height"] = size
        side = os.path.join(path, "sequence.json")
        if os.path.exists(side):
            with open(side, "r", encoding="utf-8") as f:
                meta = json.load(f)
            info["spacing_mm"] = meta.get("spacing_mm")
            info["fps"] = meta.get("fps")
            info["note"] = meta.get("note", "")
            for k in ("view", "patient", "ef_ref"):
                info[k] = meta.get(k)
    elif low.endswith(".nii") or low.endswith(".nii.gz"):
        grays, w, h, spacing = read_nifti_frames(path)
        frames = [_gray_to_512(g, w, h) for g in grays]
        info.update(width=w, height=h, spacing_mm=list(spacing))
        m = re.match(r"(patient\d+)_(2CH|4CH)_", info["name"])     # CAMUS 的命名：patientXXXX_4CH_half_sequence
        if m:
            info.update(patient=m.group(1), view=m.group(2))
            cfg = os.path.join(os.path.dirname(path), "Info_%s.cfg" % m.group(2))
            if os.path.exists(cfg):
                with open(cfg, "r", encoding="utf-8", errors="replace") as f:
                    kv = dict(line.split(": ", 1) for line in f.read().splitlines() if ": " in line)
                if "EF" in kv:
                    info["ef_ref"] = float(kv["EF"])
    else:
        with Image.open(path) as im:
            for fr in ImageSequence.Iterator(im):
                rgb = fr.convert("RGB")
                info["width"], info["height"] = rgb.size
                frames.append(imageio._to_target(rgb.tobytes(), rgb.size[0], rgb.size[1], "")[0])
    if not frames:
        raise ValueError("没有读到帧：%s" % path)
    info["n"] = len(frames)
    return frames, info


def down128_frame(frame512):
    """512x512 RGB -> 128x128 灰度（R 通道 [::4, ::4]），即板端 128 档取样。"""
    r = frame512[0::3]
    return b"".join(r[(4 * y) * W:(4 * y) * W + W:4] for y in range(128))


# ---------------------------------------------------------------- 面积、轮廓
class AreaMapper(object):
    """512 网格掩膜 -> 原图像素面积，与离线 seg_metrics 相同（Pillow NEAREST 的行列映射）。"""

    def __init__(self, width, height, spacing_mm=None):
        from PIL import Image
        self.rows = self._counts(Image, height)
        self.cols = self._counts(Image, width)
        self.mm2 = None if not spacing_mm else float(spacing_mm[0]) * float(spacing_mm[1])

    @staticmethod
    def _counts(Image, n):
        idx = Image.frombytes("I", (512, 1), struct.pack("<512i", *range(512)))
        m = struct.unpack("<%di" % n, idx.resize((n, 1), Image.NEAREST).tobytes())
        c = [0] * 512
        for v in m:
            c[v] += 1
        return c

    def area128(self, mask128):
        """128 网格掩膜的原图面积：每格权重 = 它覆盖的 4 行原图行数 x 4 列原图列数（与 area_px(up4(m)) 相同）。"""
        if not hasattr(self, "r4"):
            self.r4 = [sum(self.rows[4 * i:4 * i + 4]) for i in range(128)]
            self.c4 = [sum(self.cols[4 * i:4 * i + 4]) for i in range(128)]
        c4 = self.c4
        total = 0
        for y in range(128):
            line = mask128[y * 128:(y + 1) * 128]
            if 1 in line:
                total += self.r4[y] * sum(c for c, v in zip(c4, line) if v)
        return total

    def area_px(self, mask):
        rows, cols = self.rows, self.cols
        total = 0
        for y in range(H):
            r = rows[y]
            if r:
                line = mask[y * W:(y + 1) * W]
                if 1 in line:
                    total += r * sum(c for c, v in zip(cols, line) if v)
        return total

    def label(self, px):
        if self.mm2:
            return "%.2f cm²" % (px * self.mm2 / 100.0)
        return "%d 像素" % px


def contour_rgb(frame, mask, color=CONTOUR_RGB):
    """在 512x512 RGB 帧上画掩膜轮廓（128 网格的边界格，每格 4x4）。"""
    m = [mask[(4 * y) * W:(4 * y + 1) * W:4] for y in range(128)]
    out = bytearray(frame)
    block = bytes(color) * 4
    for y in range(128):
        row = m[y]
        up = m[y - 1] if y > 0 else None
        dn = m[y + 1] if y < 127 else None
        for x in range(128):
            if not row[x]:
                continue
            edge = (x == 0 or x == 127 or not row[x - 1] or not row[x + 1]
                    or up is None or dn is None or not up[x] or not dn[x])
            if edge:
                for dy in range(4):
                    p = 3 * ((4 * y + dy) * W + 4 * x)
                    out[p:p + 12] = block
    return bytes(out)


# ---------------------------------------------------------------- 128 网格（紧凑帧）
_UP4 = {0: b"\x00" * 4, 1: b"\x01" * 4}


def up4(mask128):
    """128x128 掩膜（每像素 0/1）-> 512x512，与板端 logit 图“每个结果重复 4x4”完全相同。"""
    rows = []
    for y in range(128):
        r = b"".join([_UP4[v] for v in mask128[y * 128:(y + 1) * 128]])
        rows.append(r * 4)
    return b"".join(rows)


def unpack_bits(bits, n=128 * 128):
    """板端 b9 位掩膜（行优先、每字节低位在前）-> 每像素 0/1 的 bytes。"""
    out = bytearray(n)
    for i, b in enumerate(bits):
        if b:
            o = 8 * i
            for k in range(8):
                if b >> k & 1:
                    out[o + k] = 1
    return bytes(out)


def mask_from_logit(logit, thr):
    lut = bytes(1 if (v if v < 128 else v - 256) >= thr else 0 for v in range(256))
    return bytes(logit).translate(lut)


def click_plane128(clicks):
    """点击锥形在 512 画布上算、再按板端 128 档取样（[::4, ::4]），与整幅帧通路逐字节相同。"""
    p = interactive.click_plane(clicks)
    return b"".join(p[(4 * y) * W:(4 * y) * W + W:4] for y in range(128))


# ---------------------------------------------------------------- 纠错后的梯度恢复（2026-11-18 尝试）
# 为什么要有它：echo_inter 是整幅重画，点一下会把离点击几十像素的地方整片切掉，切出来的是**开口的
# 凹口**（背景能从图像边界走到，不算洞），ws.clean 的填洞管不到。这里按图像证据把"模型多挖掉"的部分
# 恢复回来，只恢复同时满足三条的像素：
#   ① 先验掩膜里有、模型这次删了；
#   ② 在点击锥之外（点击附近听用户的，不跟它较劲）；
#   ③ 从保留下来的腔体走过去，"过边代价"不超过预算 —— 也就是这一路上没有真正的边界挡着。
# 判据用测地线而不是硬阈值：硬阈值只要漏一个像素就整片灌进去（离线合成用例里弱带/强带一律恢复 90%+），
# 代价累计才有区分度。代价 = max(0, 梯度/噪声底 - 1)：散斑级梯度（低于噪声底）免费，真边界才收费。
# 11-18 上机实测（板子 + demo_a4c.y8 + 真 echo_inter，10 个用例）：预算 0.5~2.0 之间只差 18%，
# 真正起作用的是**点击保护半径**（1.5 锥→补回被删的 51%，2.0→44%，3.0→28%），所以三档做成保护半径。
# 纯 numpy，板上 Python 3.6 / numpy 1.13 也能跑。
def _envf(name, default):
    """环境变量 -> float；空值 / 写错都退回默认（别让一个笔误把界面弄得起不来）。"""
    try:
        v = os.environ.get(name)
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


GRAD_ON = os.environ.get("WS_GRAD_RECOVER", "1") != "0"
GRAD_BUDGET = _envf("WS_GRAD_BUDGET", 1.0)                         # 过边预算
GRAD_CLICK_K = _envf("WS_GRAD_CLICK_K", 2.0)                       # 默认档：点击锥外扩倍数（锥半径 10 格）
GRAD_PRESETS = (("保守", 3.0), ("标准", 2.0), ("激进", 1.5))        # 三档只差保护半径（11-18 上机实测：预算几乎不参与筛选）

# 纠错影响（2026-11-18 上机实测定的）：后续几帧把**同一次点击**重放给 3 号 echo_inter，之后才交给 4 号 echo_seq。
# 实测（真帧 20 帧、板子真模型）：0 帧 → 影响≈0；3 帧 → 平均 Δ面积 19 px、存活 86%；
# 5 帧 → 36 px、89%、与原曲线相关 0.847；10 帧 → 55 px、90%、0.861；整段重放 → 末帧还 +172 px（永久抬高）。
# 所以默认 5 帧：影响明显、又会自然衰减回原曲线。
FIX_REPLAY_DEFAULT = 5             # 后续几帧把这次点击重放给 3 号 echo_inter（0 = 只改本帧）；界面不再给选项
GRAD_MAX_FRAC = _envf("WS_GRAD_MAX_FRAC", 0.6)                     # 一次最多恢复先验面积的这么多
GRAD_ITERS = 112                                                   # 测地线松弛的最多迭代次数
CLICK_R = RADIUS / 4.0                                             # 点击锥在 112 网格上的半径（10 格）


def _grad_map(gray):
    """112x112 uint8 -> float32 梯度幅值（3x3 均值压散斑 + 中心差分 |dx|+|dy|）。"""
    g = np.asarray(gray, np.float32)
    p = np.pad(g, 1, mode="constant")                            # 板上 np.pad 没有 mode 默认值
    s = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] + p[1:-1, 1:-1]) / 5.0
    gx = np.zeros_like(s)
    gy = np.zeros_like(s)
    gy[1:-1, :] = s[2:, :] - s[:-2, :]
    gx[:, 1:-1] = s[:, 2:] - s[:, :-2]
    return np.abs(gx) + np.abs(gy)


def _noise_floor(g, region):
    """噪声底 = 区域内梯度的中位数 + 2.5 倍 MAD（散斑就能到这个量级，不该算边界）。"""
    v = g[region]
    if v.size < 3:
        return 3.0
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med))) * 1.4826
    return max(med + 2.5 * mad, 3.0)


def _geodesic(cost, seed, iters=GRAD_ITERS):
    """从 seed 出发的"过边代价"（4 邻域松弛，cost = 进入该像素要付的钱；inf = 不可通行）。"""
    d = np.where(seed, 0.0, np.inf)
    for _ in range(iters):
        nd = d.copy()
        np.minimum(nd[1:], d[:-1] + cost[1:], out=nd[1:])
        np.minimum(nd[:-1], d[1:] + cost[:-1], out=nd[:-1])
        np.minimum(nd[:, 1:], d[:, :-1] + cost[:, 1:], out=nd[:, 1:])
        np.minimum(nd[:, :-1], d[:, 1:] + cost[:, :-1], out=nd[:, :-1])
        if np.array_equal(nd, d):
            break
        d = nd
    return d


def _outside(clicks, k, shape):
    """"不在点击锥内"的布尔图（锥半径 = CLICK_R × k；clicks 是 448 视图坐标）。"""
    ok = np.ones(shape, bool)
    r = CLICK_R * k
    for x, y, pos in (clicks or []):
        cx, cy = x / 4.0, y / 4.0
        y0, y1 = max(0, int(cy - r)), min(shape[0], int(cy + r) + 2)
        x0, x1 = max(0, int(cx - r)), min(shape[1], int(cx + r) + 2)
        if y0 >= y1 or x0 >= x1:
            continue
        yy, xx = np.ogrid[y0:y1, x0:x1]
        ok[y0:y1, x0:x1] &= ((yy - cy) ** 2 + (xx - cx) ** 2) > r * r
    return ok


def grad_recover(frame, prior, mask, clicks=None, budget=None, click_k=None, max_frac=None):
    """按图像证据恢复模型"多挖掉"的部分 -> (新掩膜, 恢复的像素数, {档名: 像素数})。

    frame  112x112 灰度（self.frames[t]）；prior 这一帧的先验掩膜（点之前的板端结果）；
    mask   模型这次给的、已经 clean 过的掩膜；clicks [(x, y, positive)]，x/y 是 448 视图坐标。
    """
    budget = GRAD_BUDGET if budget is None else budget
    click_k = GRAD_CLICK_K if click_k is None else click_k
    max_frac = GRAD_MAX_FRAC if max_frac is None else max_frac
    prior = np.asarray(prior, bool)
    mask = np.asarray(mask, bool)
    removed = prior & ~mask
    n_prior = int(prior.sum())
    if frame is None or n_prior < 3 or not removed.any():
        return mask, 0, {}
    if np.shape(frame) != np.shape(prior):                       # 尺寸对不上就不做（宁可不动，别卡住）
        return mask, 0, {}
    if clicks and all(c[2] for c in clicks):                     # 只有左键补漏：删掉的一定是误删，放宽
        max_frac = max(max_frac, 0.9)
    if removed.sum() > max_frac * n_prior:                       # 整块去多（用户真想删）：不碰
        return mask, 0, {}
    seed = prior & mask
    if not seed.any():                                           # 模型把腔删光了：没地方可以长
        return mask, 0, {}
    g = _grad_map(frame)
    g0 = _noise_floor(g, prior)
    cost = np.where(prior, np.maximum(0.0, g / g0 - 1.0), np.inf)   # 只允许在先验范围内走
    d = _geodesic(cost, seed)
    reach = removed & (d <= budget)                              # 这一路上没有真边界挡着
    counts = dict((name, int((reach & _outside(clicks, kk, mask.shape)).sum())) for name, kk in GRAD_PRESETS)
    rest = reach & _outside(clicks, click_k, mask.shape)          # 点击锥里听用户的
    n = int(rest.sum())
    if n == 0:
        return mask, 0, counts
    new = ndimage.binary_fill_holes(mask | rest)                 # 恢复区可能带细缝，顺手补上
    return new, n, counts


def fast_contour_photo(frame, mask128, color=CONTOUR_RGB):
    """Pillow 快速版轮廓（128 网格上取边界、最近邻放大 4 倍）；没有 Pillow / ImageTk 时返回 None。"""
    try:
        from PIL import Image, ImageChops, ImageFilter, ImageTk
    except Exception:
        return None
    im = Image.frombytes("RGB", (W, H), frame)
    m = Image.frombytes("L", (128, 128), bytes(mask128).translate(bytes([0, 255] + [255] * 254)))
    edge = ImageChops.subtract(m, m.filter(ImageFilter.MinFilter(3)))
    im.paste(color, (0, 0), edge.resize((W, H), Image.NEAREST))
    return ImageTk.PhotoImage(im)


# ---------------------------------------------------------------- 跟踪状态（无 Tk）
class Step(object):
    __slots__ = ("frame", "kind", "mask", "mask128", "area", "ai_us", "rt_ms", "verified", "session")

    def __init__(self, frame, kind):
        self.frame, self.kind = frame, kind
        self.mask = self.mask128 = self.area = self.ai_us = self.rt_ms = self.verified = None
        self.session = None


class Tracker(object):
    """播放顺序 + 每一步的结果。只决定“下一帧发什么”，网络由调用方做。"""

    KIND_LABEL = {"auto": "自动分割", "prop": "上一帧传播", "fix": "点击纠正"}

    def __init__(self, frames, auto_model, seq_model, inter_model, mapper, pingpong=False):
        self.frames = frames
        self.auto, self.seq, self.inter = auto_model, seq_model, inter_model
        self.mapper = mapper
        self.pingpong = pingpong
        self.steps = []

    def frame_at(self, k):
        """第 k 步播放哪一帧：正向一遍；往返时 0..n-1, n-2..0, 1..n-1 …"""
        n = len(self.frames)
        if not self.pingpong or n < 2:
            return k if k < n else None
        period = 2 * (n - 1)
        r = k % period
        return r if r < n else period - r

    def next_request(self):
        """-> (step, image_bytes, model) 或 None（放完了）。"""
        k = len(self.steps)
        t = self.frame_at(k)
        if t is None:
            return None
        if not self.steps:
            step = Step(t, "auto")
            return step, self.frames[t], self.auto
        step = Step(t, "prop")
        prev = self.steps[-1]
        if self.seq.get("enc") == "prev":
            image = interactive.compose_seq(self.frames[t], prev.mask, self.frames[prev.frame])
        else:
            image = interactive.compose(self.frames[t], prev.mask, [])
        return step, image, self.seq

    def click_request(self, clicks):
        """当前（最后一步）帧上的点击纠正：先验 = 当前结果，点击累加（F2 的协议）。"""
        cur = self.steps[-1]
        if cur.session is None:
            cur.session = interactive.Session(cur.mask, (cur.kind, cur.area, cur.ai_us, cur.rt_ms, cur.verified,
                                                         cur.mask128), None)
        return interactive.compose(self.frames[cur.frame], cur.session.prior, clicks), self.inter

    def accept(self, step, logit_plane, thr, board_mask_rgb, ai_us, rt_ms):
        mask = interactive.prior_from_plane(logit_plane, thr)
        step.mask = mask
        step.area = self.mapper.area_px(mask)
        step.ai_us, step.rt_ms = ai_us, rt_ms
        step.verified = None if board_mask_rgb is None else (confidence.mask_rgb(logit_plane, thr) == board_mask_rgb)
        self.steps.append(step)
        return step

    def accept128(self, step, logit, bits, thr, ai_us, rt_ms):
        """紧凑帧的结果：int8 logit（w*w）+ 位掩膜；两者在本地核对一致才算 verified。"""
        m = mask_from_logit(logit, thr)
        step.mask128 = m
        step.mask = up4(m)
        step.area = self.mapper.area128(m)
        step.ai_us, step.rt_ms = ai_us, rt_ms
        step.verified = None if bits is None else (unpack_bits(bits) == m)
        self.steps.append(step)
        return step

    def click_request128(self, clicks, frames128):
        """紧凑帧的点击纠正：R = 本帧 128，G = 当前结果，B = 点击锥形（与整幅帧通路取样后相同）。"""
        cur = self.steps[-1]
        if cur.session is None:
            cur.session = interactive.Session(cur.mask, (cur.kind, cur.area, cur.ai_us, cur.rt_ms, cur.verified,
                                                         cur.mask128), None)
        prior128 = b"".join(cur.session.prior[(4 * y) * W:(4 * y) * W + W:4] for y in range(128))
        out = bytearray(3 * 128 * 128)
        out[0::3] = frames128[cur.frame]
        out[1::3] = prior128.translate(bytes([0, 255] + [255] * 254))
        out[2::3] = click_plane128(clicks)
        return bytes(out), self.inter

    def accept_click128(self, clicks, logit, bits, thr, ai_us, rt_ms):
        cur = self.steps[-1]
        m = mask_from_logit(logit, thr)
        cur.kind, cur.mask128, cur.mask = "fix", m, up4(m)
        cur.area = self.mapper.area128(m)
        cur.ai_us, cur.rt_ms = ai_us, rt_ms
        cur.verified = None if bits is None else (unpack_bits(bits) == m)
        cur.session.push(cur.mask, clicks, (cur.kind, cur.area, cur.ai_us, cur.rt_ms, cur.verified, cur.mask128), None)
        return cur

    def accept_click(self, clicks, logit_plane, thr, board_mask_rgb, ai_us, rt_ms):
        cur = self.steps[-1]
        mask = interactive.prior_from_plane(logit_plane, thr)
        cur.kind, cur.mask, cur.area = "fix", mask, self.mapper.area_px(mask)
        cur.ai_us, cur.rt_ms = ai_us, rt_ms
        cur.verified = None if board_mask_rgb is None else (confidence.mask_rgb(logit_plane, thr) == board_mask_rgb)
        cur.session.push(mask, clicks, (cur.kind, cur.area, cur.ai_us, cur.rt_ms, cur.verified, cur.mask128), None)
        return cur

    def undo_click(self):
        cur = self.steps[-1] if self.steps else None
        if cur is None or cur.session is None or cur.session.undo() is None:
            return False
        self._restore(cur)
        return True

    def reset_clicks(self):
        cur = self.steps[-1] if self.steps else None
        if cur is None or cur.session is None:
            return False
        cur.session.reset()
        self._restore(cur)
        return True

    @staticmethod
    def _restore(cur):
        prior, _, saved, _ = cur.session.history[-1]
        cur.mask = prior
        cur.kind, cur.area, cur.ai_us, cur.rt_ms, cur.verified, cur.mask128 = saved

    def clicks(self):
        cur = self.steps[-1] if self.steps else None
        return [] if cur is None or cur.session is None else cur.session.clicks

    def latest_per_frame(self):
        """每一帧最近一次的面积（往返时取最后一遍）。"""
        area = {}
        for s in self.steps:
            area[s.frame] = s.area
        return area

    def keyframes(self):
        """-> (ed_frame, ed_area, es_frame, es_area, fac) 或 None。面积最大 = ED 候选，最小 = ES 候选。"""
        area = self.latest_per_frame()
        if len(area) < 2:
            return None
        ed = max(area, key=lambda t: (area[t], -t))
        es = min(area, key=lambda t: (area[t], t))
        fac = (area[ed] - area[es]) / float(area[ed]) if area[ed] else 0.0
        return ed, area[ed], es, area[es], fac


# ---------------------------------------------------------------- 射血分数（F4）
def view_of(info):
    """sequence.json 的 view 或文件 / 文件夹名里的 2CH / 4CH（A2C / A4C）；认不出返回 None。"""
    text = ("%s %s" % (info.get("view") or "", info.get("name") or "")).upper()
    for key, canon in (("4CH", "4CH"), ("A4C", "4CH"), ("2CH", "2CH"), ("A2C", "2CH")):
        if key in text:
            return canon
    return None


def spacing_hw(info):
    """sequence.json / NIfTI 给的是 (x, y) 毫米；Simpson 要 (行, 列) 即 (y, x)。"""
    sp = info.get("spacing_mm")
    return None if not sp else (float(sp[1]), float(sp[0]))


def ef_slot(tracker, frames, info, view):
    """一段放完的序列 -> EF 用的一个视图：面积曲线最大 / 最小帧（即曲线上标的 ED / ES 候选）的掩膜。"""
    kf = tracker.keyframes()
    latest = {}
    for s in tracker.steps:
        latest[s.frame] = s
    if kf is None or len(latest) < len(frames):
        raise ValueError("这一段还没放完（%d/%d 帧有结果）" % (len(latest), len(frames)))
    sp = spacing_hw(info)
    if sp is None:
        raise ValueError("没有像素间距，算不了容积（文件夹里放 sequence.json 写 spacing_mm，或直接用 NIfTI）")
    ed, _, es, _, _ = kf
    return dict(view=view, patient=info.get("patient") or info.get("name"), w=info["width"], h=info["height"],
                spacing=sp, ed_frame=ed, es_frame=es, ed_mask=latest[ed].mask, es_mask=latest[es].mask,
                ed_img=frames[ed], es_img=frames[es], ef_ref=info.get("ef_ref"))


def _mask_original(mask512, w, h):
    import numpy as np
    from PIL import Image
    m = Image.frombytes("L", (W, H), mask512.translate(bytes([0, 255] + [255] * 254)))
    return np.array(m.resize((w, h), Image.NEAREST)) > 0


def compute_ef(a2c, a4c):
    """两个视图 -> dict(edv, esv, ef, geom, masks)。数值走 ef_simpson.volumes（与离线评测同一份代码）；
    掩膜先回到原图尺寸（Pillow NEAREST，与评测相同）再 clean（最大连通域 + 填洞）。"""
    efs = sys.modules[__name__]
    m = {}
    for slot in (a2c, a4c):
        for ph in ("ed", "es"):
            m[(slot["view"], ph)] = efs.clean(_mask_original(slot[ph + "_mask"], slot["w"], slot["h"]))
    edv, esv = efs.volumes(m[("2CH", "ed")], m[("2CH", "es")], a2c["spacing"],
                           m[("4CH", "ed")], m[("4CH", "es")], a4c["spacing"])
    geom = {k: efs.diameters(v, (a2c if k[0] == "2CH" else a4c)["spacing"])[2] for k, v in m.items()}
    return dict(edv=edv, esv=esv, ef=efs.ejection_fraction(edv, esv), geom=geom, masks=m)


def compute_ef_board(a2c, a4c, link):
    """同 compute_ef，但整个 Simpson 在板上算（病例任务 simpson_ef，板端 v5.13+，src/job_simpson.c）：
    上传 4 个 512 掩膜 + 原图尺寸 + 像素间距，板上回原图尺寸（Pillow NEAREST）、清理、轮廓、二尖瓣平面、圆盘、容积。
    与 compute_ef 逐位相同（verify_board_simpson.py）；几何（画图用）也由板上给出。板端太旧时抛异常，调用方退回 PC。"""
    import numpy as np
    jobs = sys.modules[__name__]
    masks, outs, keys = [], [], []
    for slot in (a2c, a4c):
        for ph in ("ed", "es"):
            masks.append(np.frombuffer(slot[ph + "_mask"], np.uint8).reshape(H, W) > 0)
            outs.append((slot["w"], slot["h"]))
            keys.append((slot["view"], ph))
    link.connect()                                    # 板端 10 s 空闲超时：任务前总是重连
    r = jobs.simpson_ef(link, masks, a2c["spacing"], a4c["spacing"], out_sizes=outs, clean=True)
    res = r["result"]
    if not res.get("ok"):
        raise RuntimeError(res.get("error"))
    pts = np.frombuffer(r["blobs"]["contours"], np.float32).reshape(-1, 2).astype(np.float64)
    geom, o = {}, 0
    for k, m in zip(keys, res["masks"]):
        a = lambda v: np.array(v, np.float64)
        geom[k] = dict(base=(a(m["base"][0]), a(m["base"][1])), mid=a(m["mid"]), apex=a(m["apex"]),
                       disks=[(a(d[0]), a(d[1])) for d in m["disks"]], contour=pts[o:o + m["n_contour"]])
        o += m["n_contour"]
    return dict(edv=res["edv"], esv=res["esv"], ef=res["ef"], geom=geom, masks=None, where="board",
                board_ms=res["ms"], wall_s=r["wall_s"])


CARDIAC_PROGRAMS = {"la": 17, "epi": 18}       # P1 1.6 程序包 camus_la / camus_epi（programs_p1/*.upk）


def cardiac_board(a2c, a4c, link, patient=None):
    """P1 1.6：板上跑程序包 camus_epi（ED 帧）/ camus_la（ES 帧）各两切面（128 档，整数阈值 >= 1），
    再把 4 个心内膜 + 4 个新掩膜一起交给 simpson_ef（板端 v5.16 的 "cardiac"）。
    patient = (身高 cm, 体重 kg, 'F' / 'M' / None)：部分 A 起板上再算 BSA / LVMI / LVMI 增高与否（旧服务端忽略）。
    返回 job 结果 dict（含 "cardiac"）；程序包没装 / 板端太旧时抛异常。辅助测量与复核，不作诊断。"""
    import numpy as np
    jobs = sys.modules[__name__]
    masks, outs = [], []
    for slot in (a2c, a4c):
        for ph in ("ed", "es"):
            masks.append(np.frombuffer(slot[ph + "_mask"], np.uint8).reshape(H, W) > 0)
            outs.append((slot["w"], slot["h"]))
    extra = {}
    link.connect()
    for slot in (a2c, a4c):
        for ph, prog in (("ed", "epi"), ("es", "la")):
            st, logit, _, _ = link.v6c_round_trip(down128_frame(slot[ph + "_img"]), 128,
                                                  model0=CARDIAC_PROGRAMS[prog], thr=1, flags=proto.FLAG_STATS | proto.FLAG_C_LOGIT)
            if logit is None:
                raise RuntimeError("程序 %d 没有回 logit（程序包没装？）" % CARDIAC_PROGRAMS[prog])
            q = np.frombuffer(logit, np.int8).reshape(128, 128) >= 1
            extra[(slot["view"], prog)] = (np.repeat(np.repeat(q, 4, 0), 4, 1), (slot["w"], slot["h"]))
    for key in (("2CH", "epi"), ("4CH", "epi"), ("2CH", "la"), ("4CH", "la")):
        masks.append(extra[key][0])
        outs.append(extra[key][1])
    link.connect()
    r = jobs.simpson_ef(link, masks, a2c["spacing"], a4c["spacing"], out_sizes=outs, clean=True, patient=patient)
    res = r["result"]
    if not res.get("ok"):
        raise RuntimeError(res.get("error"))
    if "cardiac" not in res:
        raise RuntimeError("板端不支持 cardiac（需要 v5.16）")
    res["masks_cardiac"] = extra
    return res


def cardiac_text(c, height_cm=None, weight_kg=None, sex=None):
    """板上 "cardiac" -> 一行文字（LAVI / 指数需要身高体重；阈值出处 cardiac_measure.SOURCE）。"""
    cm = sys.modules[__name__]
    parts = []
    if c.get("la_truncated"):
        parts.append("左房：被截断，不可测")
    elif c.get("la_ml") is not None:
        t = "左房容积 %.0f mL（训练集校准）" % c["la_ml"]
        if height_cm and weight_kg:
            bsa = cm.bsa_mosteller(height_cm, weight_kg)
            lavi = c["la_ml"] / bsa
            t += "｜LAVI %.1f mL/m²（落在 ASE 2015 Table 4 的「%s」范围）" % (lavi, cm.lavi_band(lavi))
        parts.append(t)
    else:
        parts.append("左房：算不出来")
    if c.get("lv_mass_g") is not None:
        t = "左室质量 %.0f g" % c["lv_mass_g"]
        if height_cm and weight_kg:
            lvmi = c["lvmi"] if c.get("lvmi") is not None else c["lv_mass_g"] / cm.bsa_mosteller(height_cm, weight_kg)
            t += "｜指数 %.1f g/m²" % lvmi
            if sex in cm.LVMI_2D:
                lo, hi = cm.LVMI_2D[sex]
                t += "（ASE 2D 法参考 %d–%d；方法不同，仅作参考）" % (lo, hi)
        parts.append(t)
    if c.get("wall_mm") is not None:
        parts.append("壁厚（心尖切面中位数）%.1f mm" % c["wall_mm"])
    if c.get("rwt") is not None:                       # 部分 A：RWT 只显示数值（不套指南 0.42，口径不同）
        parts.append("RWT %.2f（2×壁厚 / 内径 %.0f mm，二维掩膜口径，只显示数值）" % (c["rwt"], c["lvidd_mm"]))
    k = c.get("lvmi_class")
    if k:                                              # 部分 A：板上按 LVMI 分增高 / 不增高
        parts.append("%s（ASE 2015 线性法上限 %d g/m²；本系统为心尖切面二维掩膜，口径不同）"
                     % (cm.LVMI_CLASS.get(k["code"], k["code"]), k["lvmi_limit"]))
    elif "lvmi_class" in c:
        parts.append("LVMI 分类需要性别")
    return "｜".join(parts) + "｜辅助测量与复核，不作诊断"


def render_ef(a2c, a4c, res):
    """2x2 小图（上 A4C、下 A2C；左 ED、右 ES）：清理后的轮廓、长轴、二尖瓣平面、20 个圆盘 -> 512x512 RGB。"""
    from PIL import Image, ImageDraw
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    for row, slot in enumerate((a4c, a2c)):
        for col, ph in enumerate(("ed", "es")):
            tile = Image.frombytes("RGB", (W, H), slot[ph + "_img"]).resize((256, 256), Image.BILINEAR)
            d = ImageDraw.Draw(tile)
            sx, sy = 256.0 / slot["w"], 256.0 / slot["h"]
            pt = lambda rc: (float(rc[1]) * sx, float(rc[0]) * sy)
            g = res["geom"][(slot["view"], ph)]
            c = g["contour"]
            d.line([pt(rc) for rc in c[::2]] + [pt(c[0])], fill=(0, 230, 0), width=1)
            for a, b in g["disks"]:
                d.line([pt(a), pt(b)], fill=(255, 200, 0), width=1)
            d.line([pt(g["mid"]), pt(g["apex"])], fill=(255, 60, 60), width=2)
            d.line([pt(g["base"][0]), pt(g["base"][1])], fill=(80, 160, 255), width=2)
            d.text((6, 4), "%s %s  frame %d" % ("A4C" if slot["view"] == "4CH" else "A2C", ph.upper(),
                                               slot[ph + "_frame"] + 1), fill=(255, 255, 255))
            canvas.paste(tile, (col * 256, row * 256))
    d = ImageDraw.Draw(canvas)
    d.text((6, 496), "EDV %d ml  ESV %d ml  EF %d %%  (biplane Simpson, demo only)" % (res["edv"], res["esv"], res["ef"]),
           fill=(255, 255, 0))
    return canvas.tobytes()


try:
    _efs = sys.modules[__name__]    # noqa: F401  (numpy + scipy + scikit-image + Pillow)
    EF_ERROR = None
except Exception as _e:            # 界面其余部分照常可用
    EF_ERROR = "射血分数需要 numpy + scipy + scikit-image（%s）" % _e


# ---------------------------------------------------------------- 窗口
class SeqWindow(object):
    """「序列跟踪」窗口。app 提供 link / 主机端口 / busy 互斥（板端一次只服务一个连接）。"""

    CURVE_W, CURVE_H = 520, 512

    def __init__(self, app, parent=None):
        self.app = app
        self.embedded = parent is not None          # 嵌在心超工作站主窗口（heartapp.py 的快照模式）里
        if self.embedded:
            self.top = self.frame = tk.Frame(parent, bg=uistyle.C_BG)
        else:
            self.top = tk.Toplevel(app.root)
            self.top.title("序列跟踪（F3）— 心超左心室")
        self.queue = queue.Queue()
        self.frames, self.info, self.tracker = None, None, None
        self.view = None                 # 正在看第几步（None = 最新一步）
        self.playing = False
        self.busy = False
        self.inflight = None
        self.photos = []
        self.t_last = None
        self.ef_slots = {}               # "2CH" / "4CH" -> ef_slot(...)
        self.ef_result = None
        self.ef_photo = None
        self.compact = None              # None = 还没探测；True = 板端认紧凑帧（v5.4+）
        self.frames128 = None
        self.stream_stop = None          # threading.Event：实时流在跑
        self.t_render = 0.0
        self.t_curve = 0.0
        self.rt_count = 0
        self.rt_t0 = None
        self.fps = None
        self._build()
        if not self.embedded:
            self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.after(50, self._poll)

    def key(self, name):
        """嵌入时主窗口转来的快捷键（独立窗口自己绑定）。"""
        if name == "space":
            self._toggle_play()
        elif name == "undo":
            self._undo()

    # ---- 界面 ----
    # 顶栏：载入 / 播放控制；中间：当前帧 | 面积曲线（卡片），下面是进度条和帧信息；
    # 再下面是射血分数；底部状态栏（左：状态，右：使用声明 + 数据来源）。
    def _build(self):
        t = uistyle.window(self.top, None)
        if self.embedded:
            tools = uistyle.topbar(t, "CAMUS 序列", "左室分割 · 点击纠错 · 序列传播 · 双平面 Simpson")
        else:
            tools = uistyle.topbar(t, "序列跟踪", "心超左心室 · F3")
        ttk.Button(tools, text="载入文件夹…", command=self._load_dir).pack(side="left")
        ttk.Button(tools, text="载入 NIfTI/GIF…", command=self._load_file).pack(side="left", padx=(6, 0))
        uistyle.vsep(tools)
        self.var_pingpong = tk.BooleanVar(value=False)
        ttk.Checkbutton(tools, text="往返循环", style="Card.TCheckbutton", variable=self.var_pingpong,
                        command=self._on_pingpong).pack(side="left")
        uistyle.label(tools, "间隔", fg=uistyle.C_MUTED).pack(side="left", padx=(12, 4))
        self.var_interval = tk.StringVar(value="0")
        ttk.Entry(tools, textvariable=self.var_interval, width=5, font=FONT_MONO).pack(side="left")
        uistyle.label(tools, "ms", fg=uistyle.C_MUTED).pack(side="left", padx=(3, 12))
        ttk.Button(tools, text="从头", width=6, command=self._restart).pack(side="left")
        ttk.Button(tools, text="单步", width=6, command=self._step_once).pack(side="left", padx=(6, 0))
        self.btn_play = uistyle.primary_button(tools, "▶ 播放", self._toggle_play, width=8)
        self.btn_play.pack(side="left", padx=(12, 0), pady=8)

        mid = tk.Frame(t, bg=uistyle.C_BG)
        self.mid = mid                               # snapview.SnapView 往这里加第三栏
        mid.grid(row=1, column=0, sticky="n" if self.embedded else "nw", padx=12, pady=(12, 0))
        left, lb = uistyle.panel(mid, "当前帧", "绿轮廓 = 板端；暂停时左键补漏、右键去多")
        left.grid(row=0, column=0, padx=(0, 12), sticky="n")
        ttk.Button(left.head, text="重置点击", command=self._reset).pack(side="right")
        ttk.Button(left.head, text="撤销", width=6, command=self._undo).pack(side="right", padx=(0, 6))
        self.cv = tk.Canvas(lb, width=W, height=H, bg=uistyle.C_EMPTY, bd=0, highlightthickness=0,
                            cursor="crosshair")
        self.cv.pack()
        self.cv_img = self.cv.create_image(0, 0, anchor="nw")
        self.cv.bind("<ButtonPress-1>", lambda e: self._click(e, True))
        self.cv.bind("<Button-3>", lambda e: self._click(e, False))
        if not self.embedded:
            self.top.bind("<Control-z>", lambda e: self._undo())
            self.top.bind("<space>", lambda e: self._toggle_play())
        right, rb = uistyle.panel(mid, "左心室面积曲线", "▼ED 面积最大　▲ES 面积最小")
        right.grid(row=0, column=1, sticky="n")
        self.curve = tk.Canvas(rb, width=self.CURVE_W, height=self.CURVE_H, bg="white",
                               bd=0, highlightthickness=0)
        self.curve.pack()
        self.curve.bind("<ButtonPress-1>", self._curve_click)

        # 进度条 + 帧信息：同一张卡片，宽度跟上面两栏对齐
        info = uistyle.card(mid)
        info.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.var_scrub = tk.IntVar(value=0)
        self.scale = uistyle.scale(info, from_=0, to=0, variable=self.var_scrub,
                                   command=lambda v: self._scrub(int(v)))
        self.scale.pack(fill="x", padx=14, pady=(12, 6))
        self.var_frame = tk.StringVar(value="先载入一段序列（示例：echo/samples/seq_*）")
        self.var_key = tk.StringVar(value="")
        self.var_status = tk.StringVar(value="")
        uistyle.label(info, var=self.var_frame, font=FONT_MONO, anchor="w").pack(fill="x", padx=14)
        uistyle.label(info, var=self.var_key, font=FONT_MONO, fg=uistyle.C_RED, anchor="w") \
            .pack(fill="x", padx=14)
        uistyle.label(info, var=self.var_status, fg=uistyle.C_MUTED, wrap=True) \
            .pack(fill="x", padx=14, pady=(0, 10))

        efr, eb = uistyle.panel(mid, "射血分数（F4）", "双平面 Simpson + 左房 / 心肌测量；分割在 FPGA，Simpson 在板上算（老板端退回 PC）")
        efr.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 12))
        self.ef_panel = efr                          # snapview.SnapView 用测量卡片替换它
        erow = tk.Frame(eb, bg=uistyle.C_CARD)
        erow.pack(fill="x")
        self.btn_ef_store = ttk.Button(erow, text="记入当前视图", command=self._ef_store)
        self.btn_ef_store.pack(side="left")
        self.btn_ef_calc = ttk.Button(erow, text="算 EF", command=self._ef_compute)
        self.btn_ef_calc.pack(side="left", padx=(6, 0))
        ttk.Button(erow, text="清空", command=self._ef_clear).pack(side="left", padx=(6, 0))
        uistyle.vsep(erow)
        # 部分 A：性别 / 身高 / 体重（LAVI、LVMI、几何分型要用；不填就只显示原始数值）
        self.var_sex, self.var_height, self.var_weight = tk.StringVar(value="—"), tk.StringVar(), tk.StringVar()
        uistyle.label(erow, "性别", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 4))
        ttk.Combobox(erow, textvariable=self.var_sex, values=("—", "F", "M"), width=3, state="readonly",
                     font=FONT).pack(side="left")
        for name, var, unit in (("身高", self.var_height, "cm"), ("体重", self.var_weight, "kg")):
            uistyle.label(erow, name, fg=uistyle.C_MUTED).pack(side="left", padx=(12, 4))
            ttk.Entry(erow, textvariable=var, width=5, font=FONT).pack(side="left")
            uistyle.label(erow, text=unit, fg=uistyle.C_MUTED).pack(side="left", padx=(2, 0))
        uistyle.label(erow, "（可不填：填了才算 LAVI / LVMI / 几何分型）", font=uistyle.FONT_SMALL,
                      fg=uistyle.C_MUTED).pack(side="left", padx=(10, 0))
        self.var_ef = tk.StringVar(value=EF_ERROR or "二腔心、四腔心各放完一段后分别「记入当前视图」，再「算 EF」")
        uistyle.label(eb, var=self.var_ef, font=FONT_MONO, fg=uistyle.C_ACCENT, wrap=True) \
            .pack(fill="x", pady=(8, 0))
        if EF_ERROR:
            self.btn_ef_store.state(["disabled"])
            self.btn_ef_calc.state(["disabled"])
        if not self.embedded:                          # 嵌入时主窗口自己有状态栏和声明
            uistyle.statusbar(t, 2, None,
                              uistyle.DISCLAIMER + "｜心超序列来自 CAMUS（CC BY-NC-SA 4.0）")
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(1, weight=1)

    # ---- 载入 ----
    def _load_dir(self):
        p = filedialog.askdirectory(parent=self.top, title="选择装帧图片的文件夹")
        if p:
            self.load(p)

    def _load_file(self):
        p = filedialog.askopenfilename(parent=self.top, title="选择序列文件",
                                       filetypes=[("NIfTI / 多帧图", "*.nii *.gz *.gif *.tif *.tiff"),
                                                  ("所有文件", "*.*")])
        if p:
            self.load(p)

    def load(self, path):
        try:
            frames, info = load_sequence(path)
        except Exception as e:                       # 给出原因，不崩
            messagebox.showerror("载入失败", str(e), parent=self.top)
            return False
        self.playing = False
        self.btn_play.configure(text="▶ 播放")
        self.frames, self.info = frames, info
        self.frames128 = [down128_frame(f) for f in frames]
        self._new_tracker()
        sp = info.get("spacing_mm")
        self.var_status.set("%s：%d 帧，原图 %dx%d%s%s" % (
            info["name"], info["n"], info["width"], info["height"],
            "，像素间距 %.3f×%.3f mm" % (sp[0], sp[1]) if sp else "，没有像素间距（面积用像素）",
            "｜" + info["note"] if info.get("note") else ""))
        self._show_step(None)
        return True

    def _new_tracker(self):
        auto = proto.MODELS[2]
        seq = proto.MODELS[auto.get("seq", auto["inter"])]
        mapper = AreaMapper(self.info["width"], self.info["height"], self.info.get("spacing_mm"))
        self.tracker = Tracker(self.frames, auto, seq, proto.MODELS[auto["inter"]], mapper,
                               pingpong=self.var_pingpong.get())
        self.view = None

    # ---- 播放控制 ----
    def _toggle_play(self):
        if self.tracker is None:
            return
        if self.stream_stop is not None:            # 实时流在跑：请它停（在途帧收完再交还连接）
            self.stream_stop.set()
            self.playing = False
            self.btn_play.configure(text="▶ 播放")
            return
        self.playing = not self.playing
        self.btn_play.configure(text="⏸ 暂停" if self.playing else "▶ 播放")
        if self.playing:
            self.view = None
            if self.compact is not False:
                self._start_stream()
            else:
                self._send_next()

    def _step_once(self):
        if self.tracker is not None and not self.playing and self.stream_stop is None:
            self.view = None
            if self.compact is not False:
                self._start_stream(single=True)
            else:
                self._send_next()

    def _restart(self):
        if self.tracker is None or self.busy:
            return
        self.playing = False
        self.btn_play.configure(text="▶ 播放")
        self._new_tracker()
        self._show_step(None)

    def _on_pingpong(self):
        if self.tracker is not None:
            if len(self.tracker.steps) >= len(self.frames) and not self.var_pingpong.get():
                self.var_status.set("已经放过一遍，关掉往返后从头开始才是单遍")
            self.tracker.pingpong = self.var_pingpong.get()

    # ---- 网络 ----
    def _send(self, image, model, what):
        if self.app.busy or self.busy:
            return False
        self.busy = True
        self.app.busy = True                   # 主界面这时不能发（同一条连接）
        self.inflight = what
        # WS_OPT_V2 少回图：掩膜本来就是本地 prior_from_plane 算的，板端那幅 789504 B 只用来
        # 打「与板端逐字节一致」的标记 —— 上板核对过等价之后没必要每帧都传。
        flags = proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS
        aiparam = proto.make_aiparam(proto.AIPARAM_SRC_RAW, model["thr"], model["res"], model["index"])
        try:
            host = self.app.var_host.get().strip()
            port = int(self.app.var_port.get().strip())
        except ValueError:
            host, port = self.app.link.host, self.app.link.port
        threading.Thread(target=self._worker, args=(host, port, image, flags, aiparam, model),
                         daemon=True).start()
        return True

    def _worker(self, host, port, image, flags, aiparam, model):
        try:
            self.app.link.host, self.app.link.port = host, port
            images, tim, _, st = self.app.link.round_trip(image, 0, flags=flags, aiparam=aiparam)
            self.queue.put(("ok", images, tim, st, model))
        except Exception as e:
            self.queue.put(("err", str(e)))

    # ---- 实时流（紧凑帧 + 板端跟踪状态 + 2 帧在途）----
    def _start_stream(self, single=False):
        if self.app.busy or self.busy:
            self.top.after(100, lambda: self._start_stream(single))
            return
        k0 = len(self.tracker.steps)
        if self.tracker.frame_at(k0) is None:
            self.playing = False
            self.btn_play.configure(text="▶ 播放")
            self.var_status.set("放完了（%d 步）。勾「往返循环」可以继续来回传播，看会不会漂" % len(self.tracker.steps))
            return
        self.busy = self.app.busy = True
        self.stream_stop = threading.Event()
        try:
            host, port = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except ValueError:
            host, port = self.app.link.host, self.app.link.port
        last = self.tracker.steps[-1] if self.tracker.steps else None
        resume = None if last is None else (last.frame, last.mask128 or
                                            b"".join(last.mask[(4 * y) * W:(4 * y) * W + W:4] for y in range(128)))
        self.rt_count, self.rt_t0 = 0, time.perf_counter()
        threading.Thread(target=self._stream_worker, args=(host, port, k0, resume, self.stream_stop, single),
                         daemon=True).start()

    def _stream_worker(self, host, port, k0, resume, stop, single):
        """后台线程：按播放顺序连续发紧凑帧，最多 2 帧在途。
        第一帧：没有历史 -> track=reset（F1）；有历史 -> 把上一步结果显式拼成传播输入（track=set），
        这样就算连接重连过、板端状态丢了也不会算错；之后 track=step，先验由板端自己保存。"""
        link = self.app.link
        tr = self.tracker
        auto, seq = tr.auto, tr.seq
        flags = proto.FLAG_STATS | proto.FLAG_C_LOGIT | proto.FLAG_C_MASK
        try:
            link.host, link.port = host, port
            if self.compact is None:                 # 探测：老板端会因为不认识 "GM" 头而断开连接
                try:
                    st, _, _, _ = link.v6c_round_trip(self.frames128[0], 128, model0=auto["index"], thr=auto["thr"],
                                                      flags=proto.FLAG_STATS)
                    self.compact = bool(st and st.get("version", 0) >= proto.AI_STAT_VERSION_COMPACT)
                except Exception:
                    link.close()
                    self.compact = False
                if not self.compact:
                    self.queue.put(("rt_fallback",))
                    return
            inflight = []
            k = k0
            while True:
                while len(inflight) < 2 and not stop.is_set() and not (single and k > k0):
                    t = tr.frame_at(k)
                    if t is None:
                        break
                    if k == 0:
                        kw = dict(model0=auto["index"], model1=seq["index"], thr=seq["thr"], track=proto.V6C_TRACK_RESET)
                        payload, kind = self.frames128[t], "auto"
                    elif k == k0 and resume is not None:
                        rgb = bytearray(3 * 128 * 128)
                        rgb[0::3] = self.frames128[t]
                        rgb[1::3] = resume[1].translate(bytes([0, 255] + [255] * 254))
                        rgb[2::3] = b"\x80" * (128 * 128)
                        kw = dict(kind=proto.V6C_KIND_RGB, model0=seq["index"], thr=seq["thr"], track=proto.V6C_TRACK_SET)
                        payload, kind = bytes(rgb), "prop"
                    else:
                        kw = dict(model0=auto["index"], model1=seq["index"], thr=seq["thr"], track=proto.V6C_TRACK_STEP)
                        payload, kind = self.frames128[t], "prop"
                    link.send_v6c(payload, 128, flags=flags, **kw)
                    inflight.append((k, t, kind, time.perf_counter()))
                    k += 1
                if not inflight:
                    break
                kk, t, kind, ts = inflight.pop(0)
                st, logit, bits = link.recv_v6c(flags, 128)
                self.queue.put(("rt", kk, t, kind, st, logit, bits, (time.perf_counter() - ts) * 1000.0))
        except Exception as e:
            link.close()
            self.queue.put(("err", str(e)))
            return
        finally:
            self.queue.put(("rt_end",))

    def _on_rt(self, k, t, kind, st, logit, bits):
        tr = self.tracker
        want = tr.auto if kind == "auto" else tr.seq
        if st is None or st.get("model") != want["index"]:
            self.var_status.set("板端没按 %d 号模型回结果（统计块 %s）" % (want["index"], None if st is None else st.get("model")))
            if self.stream_stop is not None:
                self.stream_stop.set()
            return
        step = Step(t, kind)
        tr.accept128(step, logit, bits, want["thr"], st.get("ai_us"), self._rt_ms)
        self.rt_count += 1
        now = time.perf_counter()
        el = now - (self.rt_t0 or now)
        self.fps = self.rt_count / el if el > 0.2 else None
        if now - self.t_render >= 0.035 or self.stream_stop is None:
            self.t_render = now
            self._show_step(None, curve=(now - self.t_curve >= 0.12))
            if now - self.t_curve >= 0.12:
                self.t_curve = now

    def _send_next(self):
        if self.busy:
            return
        req = self.tracker.next_request()
        if req is None:
            self.playing = False
            self.btn_play.configure(text="▶ 播放")
            self.var_status.set("放完了（%d 步）。勾「往返循环」可以继续来回传播，看会不会漂" % len(self.tracker.steps))
            return
        step, image, model = req
        if not self._send(image, model, ("step", step)):
            self.top.after(100, self._send_next)

    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "rt":
                    self._rt_ms = item[7]
                    self._on_rt(*item[1:7])
                    continue
                if item[0] == "rt_end":
                    self.stream_stop = None
                    self.busy = self.app.busy = False
                    self.playing = False
                    self.btn_play.configure(text="▶ 播放")
                    if self.tracker is not None and self.tracker.steps:
                        self._show_step(None)
                        if self.tracker.frame_at(len(self.tracker.steps)) is None:
                            self.var_status.set("放完了（%d 步，实时通路 %.1f 帧/秒）。勾「往返循环」可以继续来回传播" % (
                                len(self.tracker.steps), self.fps or 0))
                    continue
                if item[0] == "rt_fallback":
                    self.var_status.set("板端程序不认紧凑帧（要 v5.4 以上），改用整幅帧通路")
                    continue
                if item[0] == "click128":
                    self.busy = self.app.busy = False
                    _, clicks, st, logit, bits, ms, model = item
                    if st is None or st.get("model") != model["index"]:
                        self.var_status.set("板端没按 %d 号模型回结果" % model["index"])
                        continue
                    self.tracker.accept_click128(clicks, logit, bits, model["thr"], st.get("ai_us"), ms)
                    self._show_step(None)
                    continue
                self.busy = False
                self.app.busy = False
                if item[0] == "err":
                    self.playing = False
                    self.btn_play.configure(text="▶ 播放")
                    self.var_status.set("通信失败：%s" % item[1])
                    continue
                self._on_result(*item[1:])
        except queue.Empty:
            pass
        if self.top.winfo_exists():
            # WS_OPT_V1 同 App._poll：在途时收紧轮询，空闲时保持 30 ms。
            self.top.after(4 if self.busy else 30, self._poll)

    def _on_result(self, images, tim, st, model):
        what = self.inflight
        self.inflight = None
        if st is None or st.get("model") != model["index"] or "logit" not in images:
            self.playing = False
            self.var_status.set("板端没按 %d 号模型回结果（统计块 %s）：板端程序要 v5.1 以上" % (
                model["index"], None if st is None else st.get("model")))
            return
        plane = confidence.logit_plane(images["logit"])
        args = (plane, model["thr"], images.get("mask"), st.get("ai_us"), tim.get("total_ms"))
        if what[0] == "step":
            self.tracker.accept(what[1], *args)
        else:
            self.tracker.accept_click(what[1], *args)
        now = time.perf_counter()
        self.fps = None if self.t_last is None else 1.0 / max(now - self.t_last, 1e-6)
        self.t_last = now
        self._show_step(None)
        if self.playing:
            try:
                gap = max(0, int(self.var_interval.get()))
            except ValueError:
                gap = 0
            self.top.after(gap, self._send_next)

    # ---- 点击纠正 ----
    def _click(self, e, positive):
        tr = self.tracker
        if tr is None or not tr.steps:
            return
        if self.playing or self.busy:
            self.var_status.set("先暂停（空格），再在当前帧上点击纠正")
            return
        if self.view is not None and self.view != len(tr.steps) - 1:
            self.var_status.set("只能纠正最新一帧（拖到最右再点）；纠正后从这一帧继续往后传播")
            return
        x, y = min(max(e.x, 0), W - 1), min(max(e.y, 0), H - 1)
        clicks = tr.clicks() + [(x, y, positive)]
        if self.compact:
            rgb, model = tr.click_request128(clicks, self.frames128)
            self._send_click128(rgb, model, clicks)
            return
        image, model = tr.click_request(clicks)
        self._send(image, model, ("click", clicks))

    def _send_click128(self, rgb, model, clicks):
        """紧凑帧点击纠正：模型 3，结果写回板端状态（track=set），继续播放就从这里往后传播。"""
        if self.app.busy or self.busy:
            return
        self.busy = self.app.busy = True
        flags = proto.FLAG_STATS | proto.FLAG_C_LOGIT | proto.FLAG_C_MASK

        def work():
            try:
                st, logit, bits, ms = self.app.link.v6c_round_trip(rgb, 128, kind=proto.V6C_KIND_RGB,
                                                                   model0=model["index"], thr=model["thr"],
                                                                   flags=flags, track=proto.V6C_TRACK_SET)
                self.queue.put(("click128", clicks, st, logit, bits, ms, model))
            except Exception as e:
                self.app.link.close()
                self.queue.put(("err", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _undo(self):
        if self.tracker is not None and not self.busy and self.tracker.undo_click():
            self._show_step(None)

    def _reset(self):
        if self.tracker is not None and not self.busy and self.tracker.reset_clicks():
            self._show_step(None)

    # ---- 显示 ----
    def _scrub(self, k):
        if self.tracker is None or not self.tracker.steps or self.playing:
            return
        self.view = None if k >= len(self.tracker.steps) - 1 else k
        self._show_step(self.view)

    def _curve_click(self, e):
        tr = self.tracker
        if tr is None or not tr.steps or self.playing:
            return
        k = int(round((e.x - 50) / max(1.0, (self.CURVE_W - 70) / max(1, self._curve_len() - 1))))
        k = min(max(k, 0), len(tr.steps) - 1)
        self.var_scrub.set(k)
        self._scrub(k)

    def _curve_len(self):
        tr = self.tracker
        n = len(self.frames)
        return max(n, len(tr.steps)) if tr is not None else n

    def _show_step(self, k, curve=True):
        tr = self.tracker
        if tr is None:
            return
        if not tr.steps:
            self.photos = [imageio.make_photo(tk, self.frames[0])]
            self.cv.itemconfigure(self.cv_img, image=self.photos[0])
            self.cv.delete("clicks")
            self.var_frame.set("第 1/%d 帧：还没上板。按「播放」或「单步」" % len(self.frames))
            self.var_key.set("")
            self._draw_curve()
            return
        idx = len(tr.steps) - 1 if k is None else k
        s = tr.steps[idx]
        photo = fast_contour_photo(self.frames[s.frame], s.mask128) if s.mask128 is not None else None
        self.photos = [photo or imageio.make_photo(tk, contour_rgb(self.frames[s.frame], s.mask))]
        self.cv.itemconfigure(self.cv_img, image=self.photos[0])
        self.cv.delete("clicks")
        if k is None:
            for x, y, positive in tr.clicks():
                c = "#00e000" if positive else "#ff3030"
                self.cv.create_oval(x - 6, y - 6, x + 6, y + 6, outline=c, width=2, tags="clicks")
        self.scale.configure(to=max(0, len(tr.steps) - 1))
        if k is None:
            self.var_scrub.set(len(tr.steps) - 1)
        lab = tr.KIND_LABEL[s.kind]
        model_no = {"auto": tr.auto, "prop": tr.seq, "fix": tr.inter}[s.kind]["index"]
        fps = getattr(self, "fps", None)
        self.var_frame.set("第 %d 步 = 第 %d/%d 帧｜%s（%d 号模型）｜面积 %s｜板端整网 %.1f ms｜往返 %.0f ms%s%s｜%s" % (
            idx + 1, s.frame + 1, len(self.frames), lab, model_no, tr.mapper.label(s.area),
            (s.ai_us or 0) / 1000.0, s.rt_ms or 0, "｜%.1f 帧/秒" % fps if (fps and self.playing) else "",
            "｜紧凑帧 + 板端跟踪" if s.mask128 is not None else "",
            ("与板端逐字节一致 ✓" if s.verified else ("与板端不一致 ✗" if s.verified is False
                                                   else "掩膜本地生成（板端只回 logit）"))))
        kf = tr.keyframes()
        if kf:
            ed, a_ed, es, a_es, fac = kf
            self.var_key.set("ED 候选：第 %d 帧 %s｜ES 候选：第 %d 帧 %s｜面积变化率 FAC %.1f%%（仅供演示）" % (
                ed + 1, tr.mapper.label(a_ed), es + 1, tr.mapper.label(a_es), 100 * fac))
        if curve:
            self._draw_curve(idx)

    def _draw_curve(self, cur=None):
        c = self.curve
        c.delete("all")
        tr = self.tracker
        x0, x1, y0, y1 = 50, self.CURVE_W - 20, 20, self.CURVE_H - 40
        c.create_line(x0, y1, x1, y1, fill="#888")
        c.create_line(x0, y0, x0, y1, fill="#888")
        n = self._curve_len()
        c.create_text((x0 + x1) / 2, y1 + 24, text="步（帧序号随播放顺序）", font=FONT, fill="#666")
        if tr is None or not tr.steps:
            return
        areas = [s.area for s in tr.steps]
        hi = max(areas) * 1.1 or 1
        lo = min(areas) * 0.8
        sx = (x1 - x0) / max(1, n - 1)
        fy = lambda a: y1 - (a - lo) / max(hi - lo, 1e-9) * (y1 - y0)
        for frac in (0.0, 0.5, 1.0):
            a = lo + frac * (hi - lo)
            c.create_text(x0 - 4, fy(a), text=tr.mapper.label(a).split(" ")[0], anchor="e",
                          font=("Consolas", 8), fill="#666")
        pts = []
        for k, a in enumerate(areas):
            pts += [x0 + k * sx, fy(a)]
        if len(pts) >= 4:
            c.create_line(*pts, fill="#06b", width=2)
        for k, s in enumerate(tr.steps):
            col = {"auto": "#a33", "prop": "#06b", "fix": "#e80"}[s.kind]
            c.create_oval(x0 + k * sx - 3, fy(s.area) - 3, x0 + k * sx + 3, fy(s.area) + 3, fill=col, outline="")
        kf = tr.keyframes()
        if kf:
            ed, a_ed, es, a_es, _ = kf
            last = {s.frame: k for k, s in enumerate(tr.steps)}
            ke, ks = last[ed], last[es]
            c.create_text(x0 + ke * sx, fy(a_ed) - 12, text="▼ED", fill="#c00", font=FONT)
            c.create_text(x0 + ks * sx, fy(a_es) + 14, text="▲ES", fill="#00c", font=FONT)
        if cur is not None:
            c.create_line(x0 + cur * sx, y0, x0 + cur * sx, y1, fill="#bbb", dash=(3, 3))
        c.create_text(x1, y0, anchor="ne", font=FONT, fill="#666",
                      text="● 红 自动分割  ● 蓝 传播  ● 橙 点击纠正")

    # ---- 连续帧配准（F5）/ 心肌应变：窗口模块未随本包提供，入口已移除 ----
    # 板端的 job_reg / job_strain 仍在（见本文件 jobs 段），只是没有对应界面。

    # ---- 射血分数（F4）----
    def _ef_text(self):
        names = {"4CH": "四腔心", "2CH": "二腔心"}
        parts = []
        for v in ("4CH", "2CH"):
            sl = self.ef_slots.get(v)
            parts.append("%s：%s" % (names[v], "未记入" if sl is None else "%s ED 第 %d 帧 / ES 第 %d 帧" % (
                sl["patient"], sl["ed_frame"] + 1, sl["es_frame"] + 1)))
        return "｜".join(parts)

    def _ef_store(self, view=None):
        if self.tracker is None or self.busy or self.playing:
            self.var_status.set("先把这一段放完（暂停状态）再记入")
            return False
        view = view or view_of(self.info)
        if view is None:
            ans = messagebox.askyesnocancel("哪个视图", "这一段是四腔心（A4C）吗？\n选「否」= 二腔心（A2C）",
                                            parent=self.top)
            if ans is None:
                return False
            view = "4CH" if ans else "2CH"
        try:
            self.ef_slots[view] = ef_slot(self.tracker, self.frames, self.info, view)
        except ValueError as e:
            self.var_status.set(str(e))
            return False
        self.ef_result = None
        self.var_ef.set(self._ef_text())
        return True

    def _ef_clear(self):
        self.ef_slots, self.ef_result = {}, None
        self.var_ef.set(EF_ERROR or "已清空")

    def _patient(self):
        """性别 / 身高 / 体重输入框 -> (cm, kg, 'F' / 'M' / None)；身高体重没填或不是正数 -> None。"""
        try:
            h, w = float(self.var_height.get()), float(self.var_weight.get())
        except ValueError:
            return None
        sex = self.var_sex.get()
        return (h, w, sex if sex in ("F", "M") else None) if h > 0 and w > 0 else None

    def _ef_compute(self, confirm=True):
        a2c, a4c = self.ef_slots.get("2CH"), self.ef_slots.get("4CH")
        if a2c is None or a4c is None:
            self.var_ef.set(self._ef_text() + "｜两个视图都记入后才能算")
            return None
        if self.busy or self.app.busy:                 # 板子连接是共用的，别的窗口在用时不能插一个任务进去
            self.var_ef.set("板子正忙（有帧在途或别的窗口在算），稍等再「算 EF」")
            return None
        if confirm and a2c["patient"] != a4c["patient"]:
            if not messagebox.askokcancel("不是同一位病人？", "二腔心是 %s，四腔心是 %s，仍然合起来算吗？"
                                          % (a2c["patient"], a4c["patient"]), parent=self.top):
                return None
        self.var_ef.set("计算中…")
        self.top.update_idletasks()
        res, where = None, ""
        try:                                           # 先在板上算（v5.13+）；板端太旧 / 连不上就退回 PC
            link = self.app.link
            link.host, link.port = self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
            res = compute_ef_board(a2c, a4c, link)
            where = "｜Simpson 在板上算（%.0f ms）" % res["board_ms"]
        except RuntimeError as e:                      # 板上算了但算不出来（例如找不到二尖瓣平面）
            self.var_ef.set("算不出来：%s（轮廓上找不到二尖瓣平面时会这样，可先点击纠正再记入）" % e)
            return None
        except Exception as e:
            where = "｜Simpson 在 PC 上算（板端不支持：%s）" % e
            try:
                self.app.link.close()
            except Exception:
                pass
        if res is None:
            try:
                res = compute_ef(a2c, a4c)
            except Exception as e:
                self.var_ef.set("算不出来：%r（轮廓上找不到二尖瓣平面时会这样，可先点击纠正再记入）" % e)
                return None
        self.ef_result = res
        ref = a4c.get("ef_ref") if a4c.get("ef_ref") is not None else a2c.get("ef_ref")
        card = ""
        if res.get("where") == "board":                # P1 1.6：左房 / 左室质量 / 壁厚（程序包 + 板端 v5.16）
            try:
                pt = self._patient()
                c = cardiac_board(a2c, a4c, self.app.link, pt)["cardiac"]
                res["cardiac"] = c
                card = "\n" + cardiac_text(c, *(pt or (None, None, None)))
            except Exception as e:
                card = "\n左房 / 心肌测量不可用（%s）" % e
        self.var_ef.set("EDV %d ml｜ESV %d ml｜EF %d%%%s%s｜仅供演示，不作临床结论%s" % (
            res["edv"], res["esv"], res["ef"], "（CAMUS 参考 %.0f%%）" % ref if ref is not None else "", where, card))
        old = getattr(self, "ef_win", None)             # 再算一次：换掉上一次的结果窗口，不叠一摞
        if old is not None and old.winfo_exists():
            old.destroy()
        win = uistyle.window(tk.Toplevel(self.top), "射血分数（双平面 Simpson）— %s" % a4c["patient"])
        self.ef_photo = imageio.make_photo(tk, render_ef(a2c, a4c, res))
        tk.Label(win, image=self.ef_photo, bd=0).pack(padx=12, pady=(12, 0))
        uistyle.label(win, "上：四腔心　下：二腔心　左：ED　右：ES｜绿 = 轮廓（最大连通域、已填洞）　红 = 长轴　"
                           "蓝 = 二尖瓣平面　黄 = 20 个圆盘", font=uistyle.FONT_SMALL, fg=uistyle.C_MUTED,
                      bg=uistyle.C_BG).pack(padx=12, pady=8)
        self.ef_win = win
        return res

    def close(self):
        self.playing = False
        if self.stream_stop is not None:
            self.stream_stop.set()
        uistyle.close_when_idle(self.top, lambda: self.busy)    # 在途帧回来后再关，避免主界面卡在 busy


# ══════════════════════════════════════════════════════════════════════════
# 来源：heartview.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""heartview.py —— 心超工作站的「关键帧模式」「现场模式」视图（设计报告 7.3–7.5 节），嵌在 heartapp.py 主窗口里。

一个视图 = 本模式的工具条 + 三栏：
  左  视频（左室红、右室紫、右房橙轮廓；右上角 6 节段示意）+ 播放 / 逐帧 + 点击纠错（撤销 / 重置）；
  中  左心室容积曲线（▲ED ▼ES，点曲线跳到该帧）、事件时间线（只画心拍，11-19 起关键帧 / 纠错两栏已删）、全心卡片；
  右  结果：EF、心率、80 % 区间条（40 / 50 分界）、LVEF 分档、复核提示、质量灯与原因、逐拍列表、临床测量；
  下  计算过程：调度、PL 推理帧数、每关键帧 PL 耗时、跟踪耗时、板上用时、延迟（现场）+ 进度。
关键帧模式：PC 解码整段 A4C 视频（录屏素材自动找扇区、裁成方块，echo_ws.read_avi），整段交给板子（jobs.echo_ef mode 2）。
现场模式：采集卡（板端 /dev/video0）逐帧"采"帧（jobs.echo_live，mode 3），每帧回掩膜和容积，滚动 EF；结束后与关键帧模式一样出全部结果。
两种模式都可选「关键帧推荐档」或「每帧推理（对照）」（echo_kf_config.json 的 keyframe / full）。

点击纠错（7.5 节）：分析结束后暂停在某一帧，左键补漏、右键去多 -> 板上 echo_inter（3 号，128 档整网约 22 ms）按
"本帧 + 当前掩膜 + 全部点击"重算这一帧；随后后面的每一帧（不分关键帧 / 跟踪帧）都由板上 echo_seq（4 号）从纠正后的掩膜
逐帧往后重算，一直到视频末尾，或到下一个人工纠过的帧为止（那一帧保留医生的纠正）。规则只有一条：纠一帧，后面跟着改。
10-30 板上实测（ES000108，33 帧）：不加点击从第 2 / 11 帧一路传到末尾，与每帧独立 PL 分割的 Dice 约 0.90、越往后不漂，约 25 ms / 帧。
容积按单平面圆盘法重算，逐拍 EF 与临床测量在上位机按纠正后的容积重算（板上原始结果保留，用于对照、撤销、重置）；
80 % 区间与质量灯沿用板上原始结果（标"纠错前"）。
仅用于工程教学与算法演示，不作为临床诊断依据；EchoNet-Dynamic 数据不得外传。
"""

import csv
import json
import math
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, ttk


VIEW = 448                                   # 112 x 4（112 视频 / 现场模式仍是这个尺寸；录屏按原分辨率，见 _apply_view）
NATIVE_MAX_BYTES = 384 << 20                 # 预览用的原生帧内存上限；超了整段按整数步长抽稀（dec）
# 现场模式：板上每帧回的原生裁剪帧（v5.28）存这里。20 s × 60 fps × 480² = 276 MB（板端实测可用 1.5 GB），
# 预算 320 MB 就够整段 1:1 存下来；真超了才整幅按步长抽稀（WS_LIVE_NATIVE_MB 可调）。
NATIVE_LIVE_BYTES = int(float(os.environ.get("WS_LIVE_NATIVE_MB") or 320) * (1 << 20))
FLOOR_SIDE = VIEW                            # 预览短边保底：112 的视频放大到 448，原生更大的按 1:1
CW, CH, TH = 560, 200, 38                    # 容积曲线 / 时间线画布（时间线只剩心拍一行：11-19 由 92 收到 38）
PLOT_L, PLOT_B = 44, CH - 22
RW = 404                                     # 右栏宽
FONT_EF = ("Microsoft YaHei UI", 30, "bold")
FONT_HR = ("Microsoft YaHei UI", 15, "bold")
FONT_TAG = ("Microsoft YaHei UI", 14, "bold")
ECHONET_DIR = os.path.join(PLD_DIR, "EchoNet-Dynamic")
# 「打开 A4C 心超视频」的默认目录（2026-11-16 改到本机视频库；环境变量 PLD_CARDIAC_VIDEO_DIR 可覆盖）
CARDIAC_VIDEO_DIR = os.environ.get("PLD_CARDIAC_VIDEO_DIR") or r"C:\Users\moxie\Videos\Videos"
LIVE_DIR = os.path.join(PLD_DIR, "live", "src")
SCHEDS = (("keyframe", "关键帧推荐档（PL 约 18% 帧，其余板上跟踪）"), ("full", "每帧推理（对照：每帧都上 PL）"))
CROP_FIXED, CROP_AUTO = "固定（测试源的已知方块）", "自动找画面（前 1 s 里动的方块）"
CROPS = (CROP_FIXED, CROP_AUTO)              # 采集卡没有真值方块，界面不给这个选项（一律自动）；CROP_FIXED 只给自检脚本
DEMO_SRC = "0X100009310A3BD7FC_z4.y8"        # 设计报告 5.3 节的 50 fps、174 帧测试源：只给自检 / 回归脚本（界面不再列出）
LAT_H = 100                                  # 现场模式逐帧延迟图的高度
CAPTURE = ("采集卡 video0（YUYV 640×480，最长 20 s）", dict(kind="v4l2", name="video0", fps=60.0, frames=1200,
                                                         video=None, truth=None))
LIVE_SRC = CAPTURE                           # 现场模式唯一的信号源（11-18 起不再用内置视频）
BAND_COL = ("#c2410c", "#b45309", "#047857")       # LVEF ≤40 / 41–49 / ≥50 的字色
BAND_BG = ("#fde2d6", "#fdf0c8", "#d8f3e6")
LIGHT_COL = {0: "#16a34a", 1: "#eab308", 2: "#dc2626"}
LIGHT_TXT = {0: "绿灯", 1: "黄灯", 2: "红灯"}
# 现场模式画面刷新间隔：PC 0.035 s；板端 DP（GTK 兼容层）把 112 帧放大到 448 叠轮廓一次要几十 ms，
# 按 0.035 s 刷主线程会被占满、画面越积越落后（10-31 实测），改 0.12 s（约 8 帧/秒，数据仍每帧都收）
# 11-19：合成提速后单帧降到 ~20 ms、消息循环也按 25 ms 分片了，间隔收到 0.08 s（约 12 帧/秒）——实测主循环仍有富余。
RENDER_GAP = 0.08 if getattr(tk, "_IS_GTK_SHIM", False) else 0.035
# 消息循环每趟最多占主循环多久（11-19）：现场模式引擎会把整段帧一口气推过来，掏空队列会卡死几秒。
POLL_BUDGET = 0.025
# 现场模式下曲线 / 延迟图 / 预算这几个"重画一次几十毫秒"的东西多久刷一次。
LIVE_REDRAW_GAP = 0.40 if getattr(tk, "_IS_GTK_SHIM", False) else 0.25
VIEW_TXT = {0: "切面判断中", 1: "切面：未见右室，可能不是心尖四腔", 2: "切面：心尖四腔（看得到右室）"}
WIN_LENS = ("全段", "放好后", "2 s", "3 s", "4 s", "5 s")   # 分析片段长度（「放好后」= 最后一次判定锁定到结束，v5.26 CAMUS 源）
PAD, N = 8, 112
# 心率显示口径（11-19）：最近 HR_RECENT 拍的瞬时心率平均，现场滚动与最终结果共用（见 echo_ws.hr_recent）。
# 板上回的 hr 是「60 / 中位 RR」，拍数少时中位数在两个值之间跳，显示上就是心率来回闪。
# echo_ws 是在 EchoView.__init__ 里才 import 的（模块级没有这个名字），这里写死同一个数，
# 04_verify/verify_hr_recent.py 会核对两者一致。
HR_RECENT = 5                                       # = echo_ws.HR_RECENT
HR_LIVE_SPAN = 10.0                                 # 现场：只在最近 10 s 的曲线上找拍（60 bpm 约 10 拍、30 bpm 也够 5 个间期）
HR_PLAUSIBLE = (20.0, 250.0)                        # 这个范围外的结果不采信（找拍失败时宁可用板上的数）


def live_sources():
    """现场模式的信号源：**只有板上的采集卡**（/dev/video0，USB 输入）。

    11-18 起现场模式不用内置视频了：以前这里还会列出 E:/PLD/live/src/index.json 里的 .y8 测试源
    （先传到板上 /root/echo_src 回放），现在界面只留采集卡，不给切换。测试源挪到 test_sources()，
    只给自检 / 回归脚本注入（verify_* / _verify_*），界面不显示。
    """
    return [LIVE_SRC]


def test_sources():
    """.y8 测试源（自检 / 回归脚本用，界面不列出）：E:/PLD/live/src/index.json；板上 DP 工作站没有工程目录，
    读上位机目录里的拷贝 live_sources.json。返回 [(标签, dict)]，形状与 live_sources() 一样，可以直接接在后面：
        lv.sources = live_sources() + test_sources()
    """
    out = []
    p = os.path.join(LIVE_DIR, "index.json")
    if not os.path.exists(p):                        # 板上 DP 工作站：没有工程目录，用上位机目录里的拷贝
        p = os.path.join(HERE, "live_sources.json")
    try:
        with open(p, encoding="utf-8") as f:
            idx = json.load(f)
        for fn, m in sorted(idx.items()):
            out.append(("%s（%d 帧 %.0f fps，%d×%d）" % (fn, m["frames"], m["fps"], m["W"], m["H"]),
                        dict(kind="file", name=fn, fps=m["fps"], frames=m["frames"], video=m["video"],
                             truth=(m["x"], m["y"], m["side"]), zoom=m.get("zoom", 1.0),
                             quality=m.get("quality"),        # "camus"：v5.26 用 CAMUS 口径的滚动质量灯 + 切面
                             segments=m.get("segments"))))    # 模拟扫查各段（病人 / 切面 / 专家质量等级），自检用
    except (OSError, ValueError, KeyError):
        pass
    return out


def live_err(src, e):
    """现场模式失败文案。11-18 起现场只有采集卡，「图上没画面」基本都是 /dev/video0 打不开，顺手把排查方向写出来。"""
    msg = str(e)
    if src.get("kind") == "v4l2" and ("video" in msg or "v4l2" in msg.lower() or "cannot open" in msg):
        msg += "（板上采集卡 /dev/video0 打不开：查 USB 连接，以及 /root/radar_usb_watch.skip 在不在）"
    return "现场模式失败：%s" % msg


HMCQU_A4C = os.path.join(PLD_DIR, "HMC-QU Dataset-Kaggle v1.0 (1)", "HMC-QU Dataset-Kaggle v1.0", "HMC-QU", "A4C")


def read_video(ws, path):
    """与 echo_ws.read_avi 逐帧相同的读法（112 的 EchoNet 取三通道平均；录屏取灰度后 screen_to_112），
    另回录屏的原始首帧（BGR）、扇区方块和**原始分辨率的整段灰度帧**（预览按输入分辨率画，11-18）；
    112 的视频没有更高分辨率的画面，回 None。"""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(path)
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 50.0
    out, raw, first = [], [], None
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if fr.shape[:2] == (N, N):
            f = fr.astype(np.uint16)
            out.append(((f[..., 0] + f[..., 1] + f[..., 2] + 1) // 3).astype(np.uint8))
        else:
            if first is None:
                first = fr.copy()
            raw.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
    cap.release()
    if not out and not raw:
        raise ValueError("读不出视频帧")
    if raw:
        grey = np.stack(raw)
        v, crop = ws.screen_to_112(grey)
        # 预览要用的整段原生帧：占太多内存就整幅按步长抽稀（抽稀因子记在 dec 里，坐标换算要除它）
        dec = 1
        while grey.nbytes // (dec * dec) > NATIVE_MAX_BYTES:
            dec += 1
        if dec > 1:
            grey = np.ascontiguousarray(grey[:, ::dec, ::dec])
        return v, fps, dict(frame=first, crop=crop, path=path, frames=grey, dec=dec)
    return np.stack(out), fps, None


def raw_video_path(name):
    """现场模式信号源对应的原始录屏（定标要看原始画面上的深度刻度）：工程里的 HMC-QU 目录或上位机 demo/。"""
    for d in (HMCQU_A4C, os.path.join(HERE, "demo")):
        p = os.path.join(d, name or "")
        if name and os.path.exists(p):
            return p
    return None


def depth_ticks(frame_bgr):
    """读超声机屏幕上的深度刻度（1 cm 一个，扇区左/右边上一排小短线）-> ((x0,y0), (x1,y1), 厘米, 说明) 或 None。

    两种画法都要认：黄色刻度（原逻辑）和**黑底上的灰/白刻度**（ES000108 这类白刻度机）。步骤：
    刻度像素连通块 -> 按"小、细"的形状挑候选 -> 穷举两点连线取共线最多的一条（垂距 < 2.5 px）
    -> 相邻间距中位数 = 1 cm -> 首尾两点之间共几厘米。每 5 cm 画出长刻度的机型再核对一次
    （长刻度必须正好隔 5 个间距）。纯 numpy / scipy，几十毫秒。

    实测（本机 8 段录屏，每段取 0/15/30 三帧）：ES0001 31.96、ES0002 21.88、ES000108 27.73、
    ES0003 25.96、0X100 那组（右侧 0–14 刻度）30.00 像素每厘米；同一段不同帧完全一致（Δ=0）。
    ES000108 与手点 27.88 差 0.5%。ES0003 首帧读不到（第 0 帧刻度被遮挡），换 15 帧就有 -> 调用方要试几帧。
    """
    np = __import__("numpy")
    from scipy import ndimage
    f = np.asarray(frame_bgr).astype(np.int16)
    if f.ndim != 3 or f.shape[2] < 3:
        return None
    b, g, r = f[..., 0], f[..., 1], f[..., 2]
    mx, mn = f.max(2), f.min(2)
    yellow = (r - b > 25) & (g - b > 25) & (r > 90)                 # 黄色刻度
    grey = (mx > 55) & (mx < 215) & (mx - mn < 45)                  # 灰 / 白刻度（还要求周围是黑底）
    dark = ndimage.uniform_filter(mx.astype(np.float32), 11) < 48   # 扇区里的组织邻域不够黑，进不来
    m = yellow | (grey & dark)
    lab, n = ndimage.label(m, structure=np.ones((3, 3), int))
    if n < 8:
        return None
    area = np.bincount(lab.ravel(), minlength=n + 1)[1:]
    cy, cx = np.array(ndimage.center_of_mass(m, lab, np.arange(1, n + 1))).T
    boxes = ndimage.find_objects(lab)
    bw = np.array([s[1].stop - s[1].start for s in boxes])
    bh = np.array([s[0].stop - s[0].start for s in boxes])
    long_, short = np.maximum(bw, bh), np.minimum(bw, bh)
    keep = (area >= 3) & (area <= 45) & (long_ >= 4) & (long_ <= 16) & (short <= 5)
    cand = np.nonzero(keep)[0]
    if len(cand) < 8:
        return None
    if len(cand) > 200:                                             # 太多就均匀抽稀（刻度等距，抽稀不影响找线）
        cand = cand[np.linspace(0, len(cand) - 1, 200).astype(int)]
    best = None
    for ii in range(len(cand)):
        for jj in range(ii + 1, len(cand)):
            i, j = cand[ii], cand[jj]
            dx, dy = float(cx[j] - cx[i]), float(cy[j] - cy[i])
            ln = (dx * dx + dy * dy) ** 0.5
            if ln < 8:
                continue
            ux, uy = dx / ln, dy / ln
            inl = cand[np.abs((cx[cand] - cx[i]) * uy - (cy[cand] - cy[i]) * ux) < 2.5]
            if best is None or len(inl) > len(best[0]):
                best = (inl, ux, uy, cx[i], cy[i])
    if best is None:
        return None
    inl, ux, uy, x0, y0 = best
    if len(inl) < 8:                                                # 少于 8 个刻度不敢定标
        return None
    t = (cx[inl] - x0) * ux + (cy[inl] - y0) * uy
    o = np.argsort(t)
    inl, t = inl[o], t[o]
    d = np.diff(t)
    d = d[d > 5]                                                    # 挨太近的当同一个刻度
    if len(d) < 4:
        return None
    p0 = np.median(d)
    k = np.round(d / p0)
    ok = (k >= 1) & (np.abs(d / np.maximum(k, 1) - p0) < 0.15 * p0)
    if ok.sum() < 4:                                                # 间距要够规整，散斑凑不出来
        return None
    period = float(d[ok].sum() / k[ok].sum())                       # 1 cm 多少像素
    if not 10 <= period <= 80:
        return None
    major = [q for q in range(len(inl)) if area[inl[q]] >= 8 and bw[inl[q]] >= 6]
    if 2 <= len(major) <= 0.8 * len(inl):                           # 只有"少数长刻度"的机型才拿它核对单位
        a, c = major[0], major[1]
        if abs((t[c] - t[a]) / period - 5.0) > 0.25:
            return None
    span_cm = float(t[-1] - t[0]) / period                          # 首尾两个刻度之间几厘米
    if not 3 <= span_cm <= 30:
        return None
    return ((float(cx[inl[0]]), float(cy[inl[0]])), (float(cx[inl[-1]]), float(cy[inl[-1]])),
            span_cm, dict(period=period, ticks=len(inl), span_px=float(t[-1] - t[0]), major=len(major)))


def model_by_name(name):
    return next((m for m in proto.MODELS if m["name"] == name), None)


def chip(parent, title, width=None):
    """计算过程条里的一个小指标：灰色小标题 + 粗体数值，返回数值的 StringVar。"""
    f = tk.Frame(parent, bg=uistyle.C_CARD)
    f.pack(side="left", padx=(0, 22))
    uistyle.label(f, title, font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
    v = tk.StringVar(value="—")
    kw = dict(width=width, anchor="w") if width else {}
    uistyle.label(f, var=v, font=FONT_HEAD, **kw).pack(anchor="w")
    return v


class _Stopped(Exception):
    pass


class CalibDialog(object):
    """手动定标（11-19，三个模式共用）：把一张**源分辨率**的画面摆出来，点两个深度刻度，再填这两点之间几厘米。

    用在自动定标读不到刻度的时候（刻度被遮挡、画面太暗、外接采集卡的画面跟录屏不一样……）。缩放用最近邻、
    刻度边缘不糊，点得准；距离按**源像素**算（显示坐标除回缩放）。回调 on_done(mm_per_px, dist_px, cm)。
    """

    MAXW, MAXH = 1000, 660

    def __init__(self, parent, title, img, on_done, default_cm=5.0, note=None):
        from PIL import Image, ImageTk
        self.on_done, self.img, self.pts = on_done, img, []
        self.w, self.h = img.size
        self.k = max(0.25, min(3.0, min(self.MAXW / float(self.w), self.MAXH / float(self.h))))
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.configure(bg=uistyle.C_BG)
        self.top.resizable(False, False)
        vw, vh = int(round(self.w * self.k)), int(round(self.h * self.k))
        self.vw, self.vh = vw, vh
        # 说明文字的折行宽度要跟着画布走：不给 wraplength 时 wrap=True 不折行，长句会伸出窗口外
        uistyle.label(self.top, note or "在画面上点两个**深度刻度**（超声机屏幕边上 1 cm 一格的小短线）——"
                                        "点第三个点会重新从第一个点开始；点完在下面填这两点之间几厘米。",
                      font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True,
                      wraplength=vw).pack(fill="x", padx=10, pady=(8, 4))
        self.cv = tk.Canvas(self.top, width=vw, height=vh, bg="#000", highlightthickness=0, cursor="crosshair")
        self.cv.pack(padx=10)
        self.photo = ImageTk.PhotoImage(img.resize((vw, vh), Image.NEAREST) if (vw, vh) != (self.w, self.h) else img)
        self.cv.create_image(0, 0, anchor="nw", image=self.photo)
        self.cv.bind("<Button-1>", self._click)
        row = tk.Frame(self.top, bg=uistyle.C_BG)
        row.pack(fill="x", padx=10, pady=(6, 10))
        self.var_info = tk.StringVar(value="已点 0 / 2")
        uistyle.label(row, var=self.var_info, font=FONT_SMALL, fg=uistyle.C_MUTED, width=34).pack(side="left")
        uistyle.label(row, "两点之间", font=FONT).pack(side="left", padx=(6, 4))
        self.var_cm = tk.StringVar(value="%g" % float(default_cm))
        ttk.Entry(row, textvariable=self.var_cm, width=6, font=FONT).pack(side="left")
        uistyle.label(row, "cm", font=FONT).pack(side="left", padx=(2, 8))
        self.btn_ok = uistyle.primary_button(row, "确定", self._ok)
        self.btn_ok.state(["disabled"])
        self.btn_ok.pack(side="right")
        ttk.Button(row, text="取消", command=self.top.destroy).pack(side="right", padx=(0, 6))
        ttk.Button(row, text="重来", command=self._reset).pack(side="right", padx=(0, 6))
        try:
            self.top.transient(parent.winfo_toplevel())
            self.top.grab_set()
        except Exception:
            pass

    def _dist(self):
        (x0, y0), (x1, y1) = self.pts
        return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5

    def _click(self, e):
        if len(self.pts) >= 2:
            self.pts = []
        self.pts.append((e.x / self.k, e.y / self.k))
        self.cv.delete("pt")
        for x, y in self.pts:                            # 显示坐标 = 源坐标 × k
            X, Y = x * self.k, y * self.k
            self.cv.create_line(X - 9, Y, X + 9, Y, fill="#22d3ee", width=2, tags="pt")
            self.cv.create_line(X, Y - 9, X, Y + 9, fill="#22d3ee", width=2, tags="pt")
        if len(self.pts) == 2:
            (x0, y0), (x1, y1) = self.pts
            self.cv.create_line(x0 * self.k, y0 * self.k, x1 * self.k, y1 * self.k, fill="#22d3ee", width=2,
                                tags="pt")
            self.var_info.set("两点相距 %.1f 像素（源画面）" % self._dist())
        else:
            self.var_info.set("已点 1 / 2：再点第二个刻度")
        self.btn_ok.state(["!disabled"] if len(self.pts) == 2 else ["disabled"])

    def _reset(self):
        self.pts = []
        self.cv.delete("pt")
        self.var_info.set("已点 0 / 2")
        self.btn_ok.state(["disabled"])

    def _ok(self):
        try:
            cm = float(self.var_cm.get())
        except ValueError:
            self.var_info.set("「几厘米」要填数字（默认 5）")
            return
        if cm <= 0 or len(self.pts) != 2:
            self.var_info.set("先点两个刻度，再填一个大于 0 的厘米数")
            return
        d = self._dist()
        if d < 3.0:
            self.var_info.set("两点只差 %.1f 像素，太近了：重新点" % d)
            return
        self.top.destroy()
        self.on_done(cm * 10.0 / d, d, cm)


class EchoView(object):
    """live=False：关键帧模式（整段视频上传）；live=True：现场模式（板上采集卡 /dev/video0）。app 提供 root / link / var_host /
    var_port / busy（板端一次只服务一个连接）/ status(msg)。"""

    def __init__(self, app, parent, live=False):
        import numpy
        import echo_ws
        self.app, self.live = app, live
        self.np, self.ws = numpy, echo_ws
        self.model = model_by_name("echonet_lv")
        self.m_inter, self.m_seq = model_by_name("echo_inter"), model_by_name("echo_seq")
        self.conf = echo_ws.load_config()
        if self.model:
            echo_ws.SCALE[0] = self.model["scale"]
        echo_ws.THRESH = tuple(self.conf.get("qc_thresholds", echo_ws.THRESH))
        self.ref = self._load_filelist()
        self.hmc = self._load_hmcqu()
        self.queue = queue.Queue()
        self.busy = False
        self.stop = None
        self.playing = False
        self.open_report = True                      # 生成报告后用浏览器打开（自检脚本关掉）
        self.frames = self.fps = self.name = None
        self.src = None
        self.src_kind = "采集卡"                      # 现场模式文案用（11-18 起现场模式只有采集卡）
        self.rawvid = None                           # 关键帧模式：录屏原始首帧 + 扇区方块（read_video）
        # ---- 预览尺寸 / 像素标尺（11-18）---------------------------------------------
        self.native, self.crop, self.ndec = None, None, 1   # 原生分辨率的整段灰度帧（read_video）；没有就是 None
        self.native_have = None                      # 现场模式（v5.28）：哪些帧收到了原生裁剪帧
        self.live_native = False                     # 现场模式的原生帧就是裁剪方块本身（crop 按存帧坐标）
        self.live_still = None                       # 现场模式：板上回的那块整屏（自动 / 手动定标都看它）
        self._stills = []                            # 现场模式：三块整屏（第 0/30/60 帧），自动定标各读一次取中位
        self._calib_th = None
        self.vw = self.vh = VIEW                     # 预览画布实际尺寸（按输入分辨率定，超屏才缩）
        self.kd = 1.0                                # 源像素 -> 显示像素
        self.s = 1.0                                 # 448 视图坐标 -> 显示像素（点击纠错仍按 448 存）
        self.ruler = None                            # 两点测距的显示坐标 (x0, y0, x1, y1)；None = 没量
        self._ruler_from = None                      # 正在拖的那一端
        self.photos = {}
        self._calib_pending = {}                     # 原始录屏文件名 -> 开始时间：自动定标在途（送快照时要等它）
        self.calib_auto = None                       # 自动定标读到的刻度信息（period / ticks / span_px / major）
        self.prev_run = None                         # 现场模式上一次跑完的逐帧延迟（灰线对照）
        self._reset_state()
        self.frame = tk.Frame(parent, bg=uistyle.C_BG)
        self._build()
        self.frame.after(30, self._poll)

    def _reset_state(self):
        self.masks, self.vols, self.keyv = {}, [], []
        self.res = self.raw = None                   # 板上结果（原样）/ jobs.run 的整个返回
        self.result = None                           # 显示用的逐拍结果（纠错后为上位机重算）
        self.kf_cfg = self.hc_cfg = None
        self.heart, self.rh_frames = None, {}
        self.clinical = None
        self.ef_board = self.ef_now = None
        self.orig = None                             # 板上原始掩膜 / 容积（重置用）
        self.clicks, self.undo, self.fixed = {}, [], {}   # 帧 -> 点击；撤销栈；帧 -> "fix" / "prop"
        self.fix_ms = []
        self.stats = None
        self.live_info = None
        self.roll = (None, 0)                        # 现场：板上滚动质量灯 / 切面（服务端 v5.25 RQ01）的当前值
        self.roll_tag = ""                           # 灯的口径（v5.26：CAMUS 源 = CAMUS 专家等级标定，其余 = EchoNet 口径）
        self.roll_hold = self.roll_moved = False     # v5.26：已判定保持中 / 保持后探头动过（重新判断）
        self.lat, self.proc = [], []                 # 现场模式逐帧：采到 → 掩膜 + 容积 / 处理线程用时（ms，板上时间戳）
        self.cur = 0
        self.t_render = self.t_curve = 0.0
        self.render_cost = 0.05                      # 现场模式上一帧画图耗时（秒）：刷新间隔按它自适应，见 _on_lframe
        self.win = None                              # 分析片段 (a, b)：EF / 逐拍 / 临床测量只用第 a .. b-1 帧；None = 全段
        self._hr_key, self._hr_val = None, None      # 现场滚动心率：上次算过的容积段 (a, n) 与结果（见 _hr_recent）

    @staticmethod
    def _load_filelist():
        p = os.path.join(ECHONET_DIR, "FileList.csv")
        ref = {}
        if os.path.exists(p):
            with open(p, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    n = r["FileName"] if r["FileName"].endswith(".avi") else r["FileName"] + ".avi"
                    ref[n] = (float(r["EF"]), r["Split"])
        return ref

    @staticmethod
    def _load_hmcqu():
        d = os.path.join(PLD_DIR, "wallmotion", "results")
        try:
            with open(os.path.join(d, "labels_a4c.json"), encoding="utf-8") as f:
                rows = json.load(f)["rows"]
            with open(os.path.join(d, "split.json"), encoding="utf-8") as f:
                split = json.load(f)["split"]
        except (OSError, ValueError, KeyError):
            return {}
        return {r["name"] + ".avi": (r["seg"], split.get(r["name"], "?")) for r in rows}

    # ================================================================ 界面
    def _build(self):
        t = self.frame
        self._build_toolbar(t)
        body = tk.Frame(t, bg=uistyle.C_BG)
        body.grid(row=1, column=0, sticky="n", padx=12, pady=(10, 0))
        self._build_video(body)
        self._build_middle(body)
        self._build_results(body)
        self._build_process(body)
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(1, weight=1)
        self._redraw()                               # 还没数据时曲线 / 时间线里先写上会显示什么

    def _build_toolbar(self, t):
        bar = tk.Frame(t, bg=uistyle.C_CARD, highlightthickness=1, highlightbackground=uistyle.C_BORDER)
        bar.grid(row=0, column=0, sticky="ew")
        row = tk.Frame(bar, bg=uistyle.C_CARD)
        row.pack(fill="x", padx=14, pady=8)
        if self.live:
            # 现场模式只认采集卡（11-18）：信号源固定，不给下拉、也不给「画面方块」（采集卡没有真值方块，一律自动找）。
            # 自检 / 回归脚本要做 .y8 回归时，往 self.sources 里接 test_sources() 再 set var_src 即可。
            uistyle.label(row, "信号源", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 4))
            self.sources = live_sources()
            self.var_src = tk.StringVar(value=self.sources[0][0])
            uistyle.label(row, var=self.var_src, font=FONT, anchor="w").pack(side="left")
            self.var_crop = tk.StringVar(value=CROP_AUTO)
        else:
            ttk.Button(row, text="打开视频…", command=self._open).pack(side="left")
            self.var_name = tk.StringVar(value="（未打开视频：A4C 心尖四腔 .avi，录屏素材自动找扇区）")
            uistyle.label(row, var=self.var_name, fg=uistyle.C_MUTED, width=44, anchor="w").pack(side="left", padx=(8, 0))
        uistyle.vsep(row)
        uistyle.label(row, "运行方式", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 4))
        self.var_sched = tk.StringVar(value=SCHEDS[0][1])
        ttk.Combobox(row, textvariable=self.var_sched, width=32 if self.live else 36, font=FONT, state="readonly",
                     values=[s[1] for s in SCHEDS]).pack(side="left")
        self.btn_run = uistyle.primary_button(row, "▶ 开始采集" if self.live else "▶ 开始分析", self.run)
        self.btn_run.pack(side="right")
        self.btn_stop = ttk.Button(row, text="■ 停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="right", padx=(0, 8))
        self.btn_rep = ttk.Button(row, text="导出证据报告", command=self._report, state="disabled")
        self.btn_rep.pack(side="right", padx=(0, 8))

    def _build_video(self, body):
        col = tk.Frame(body, bg=uistyle.C_BG)
        col.grid(row=0, column=0, sticky="n", padx=(0, 12))
        f, b = uistyle.panel(col, "视频", "红 左室　紫 右室　橙 右房")
        f.pack(fill="x")
        # 标尺：11-18 起载入即自动定标；11-19 起**三个模式都能手动定标**（自动读不到时人工点两个刻度）。
        self.var_scale = tk.StringVar(value="标尺：等待载入录屏…")
        # 固定宽度（11-19）：这一排标签的值是随分析进度变的（"—" → "62%" → "LVEF 分档：正常（≥52%）…"），
        # 而窗口是按内容定尺寸的 —— 文字一变长，整栏和窗口边框就左右跳。给固定字符宽后请求宽度不再随文字变，
        # 宽度按各自**最长真实文案**量出来（见 16_输出文字边框伸缩_20261119 说明里的量法），正常值不会截断。
        uistyle.label(f.head, var=self.var_scale, font=FONT_SMALL, fg=uistyle.C_MUTED, width=24).pack(side="right")
        ttk.Button(f.head, text="自动", width=4, command=self._calib_auto).pack(side="right", padx=(6, 0))
        ttk.Button(f.head, text="标尺…", width=6, command=self._calib_dialog).pack(side="right", padx=(6, 0))
        self.cv_v = tk.Canvas(b, width=VIEW, height=VIEW, bg="#000", highlightthickness=0, cursor="crosshair")
        self.cv_v.pack()
        self.cv_img = self.cv_v.create_image(0, 0, anchor="nw")
        self.cv_v.create_text(VIEW // 2, VIEW // 2, fill="#9aa3ad", font=FONT, tags="hint", justify="center",
                              text=("点右上角「▶ 开始采集」\n\n采集卡按真实帧率逐帧送入，\n轮廓、容积曲线、延迟图边采边更新"
                                    if self.live else "「打开视频…」选一段 A4C 视频\n再点「▶ 开始分析」\n\n"
                                    "超声机录屏会自动找扇区、裁成方块"))
        self.cv_v.bind("<ButtonPress-1>", self._press)
        self.cv_v.bind("<Button-3>", lambda e: self._click(e, False))
        self.cv_v.bind("<Shift-ButtonPress-1>", self._ruler_press)
        self.cv_v.bind("<B1-Motion>", self._ruler_drag)
        self.cv_v.bind("<Shift-B1-Motion>", self._ruler_drag)
        self.cv_v.bind("<ButtonRelease-1>", self._ruler_release)
        self.cv_v.bind("<Motion>", self._hover)
        self.cv_v.bind("<Leave>", lambda e: self.var_xy.set(self._xy_idle()))
        # ---- 像素标尺（11-18）：鼠标悬停读坐标；Shift+拖拽（或点「量距」）量两点；毫米用**自动定标**读到的 mm/像素 ----
        rb = tk.Frame(b, bg=uistyle.C_CARD)
        rb.pack(fill="x", pady=(6, 0))
        uistyle.label(rb, text="像素标尺", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(side="left")
        ttk.Button(rb, text="清除", width=6, command=self._ruler_clear).pack(side="right")
        self.btn_rule = ttk.Button(rb, text="量距", width=6, command=self._ruler_toggle_arm)
        self.btn_rule.pack(side="right", padx=(0, 6))
        uistyle.label(rb, text="Shift+拖拽", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(side="right", padx=(0, 10))
        self.var_xy = tk.StringVar(value=self._xy_idle())
        uistyle.label(b, var=self.var_xy, font=FONT_MONO, anchor="w", width=56).pack(fill="x")
        nav = tk.Frame(b, bg=uistyle.C_CARD)
        nav.pack(fill="x", pady=(8, 0))
        ttk.Button(nav, text="◀", width=3, command=lambda: self.step(-1)).pack(side="left")
        self.btn_play = ttk.Button(nav, text="▶ 播放", width=8, command=self.toggle_play)
        self.btn_play.pack(side="left", padx=4)
        ttk.Button(nav, text="▶|", width=3, command=lambda: self.step(1)).pack(side="left")
        self.var_ft = tk.StringVar(value="（未载入）")
        # 固定宽度：播放时这行每帧变长变短，会挤动各列、让右侧文本框折行来回变（11-03 界面上下闪）
        uistyle.label(nav, var=self.var_ft, font=FONT_MONO, anchor="w", width=50).pack(side="left", padx=(10, 0))
        self._scale_busy = False                     # _goto 自己设进度条时为真：板上兼容层的 set() 会回调 command，不拦就同一帧画两遍
        self.scale = ttk.Scale(b, from_=0, to=0, orient="horizontal", length=VIEW,
                               command=lambda v: None if self._scale_busy else self._goto(int(float(v)), from_scale=True))
        self.scale.pack(pady=(6, 0))
        fx, bx = uistyle.panel(col, "点击纠错", "暂停后左键补漏、右键去多；FPGA 重算本帧，后面各帧跟着改")
        fx.pack(fill="x", pady=(10, 0))
        ttk.Button(fx.head, text="重置", width=6, command=self.reset_fix).pack(side="right")
        ttk.Button(fx.head, text="撤销", width=6, command=self.undo_fix).pack(side="right", padx=(0, 6))
        # 「纠错保护」「纠错影响」两个下拉已按用户要求删掉（11-18）：这两档在真机上只差个位数像素，
        # 摆着反而让人以为没生效。行为改成固定值：保护 = WS_GRAD_CLICK_K（默认 2.0 锥），
        # 影响 = 后续 5 帧重放（FIX_REPLAY_DEFAULT）。要改仍可用环境变量 WS_GRAD_RECOVER / WS_GRAD_CLICK_K。
        self.var_fix = tk.StringVar(value="分析结束后可用（Ctrl+Z 撤销）")
        uistyle.label(bx, var=self.var_fix, fg=uistyle.C_MUTED, wrap=True).pack(fill="x")

    def _heart_cfg(self):
        """load_heart_config() 原样返回（「触边判定」已整体关闭，border_px 由 heart_block 固定写 0）。"""
        return jobs.load_heart_config()

    def _build_middle(self, body):
        col = tk.Frame(body, bg=uistyle.C_BG)
        col.grid(row=0, column=1, sticky="n", padx=(0, 12))
        f1, b1 = uistyle.panel(col, "左心室容积曲线", "▲ 舒张末 ED　▼ 收缩末 ES　点曲线跳到该帧")
        f1.pack(fill="x")
        self.cv = tk.Canvas(b1, width=CW, height=CH, bg="#fff", highlightthickness=0)
        self.cv.pack()
        self.cv.bind("<Button-1>", lambda e: self._curve_click(e.x))
        self.cv.bind("<B1-Motion>", lambda e: self._curve_click(e.x))
        # 分析片段（11-02）：滚动条的滑块 = 参与 EF / 逐拍 / 临床测量的那段；CAMUS 源默认「放好后」（最后一次判定锁定起）
        wr = tk.Frame(b1, bg=uistyle.C_CARD)
        wr.pack(fill="x", pady=(4, 0))
        uistyle.label(wr, "分析片段", font=FONT).pack(side="left")
        self.var_winlen = tk.StringVar(value=WIN_LENS[0])
        cb = ttk.Combobox(wr, textvariable=self.var_winlen, width=8, font=FONT, state="readonly", values=WIN_LENS)
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda e: self._win_len_changed())
        self.var_win = tk.StringVar(value="全段")
        uistyle.label(wr, var=self.var_win, font=FONT, width=20).pack(side="right", padx=(6, 6))
        self.sb_win = ttk.Scrollbar(wr, orient="horizontal", command=self._win_cmd)
        self.sb_win.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.sb_win.set(0.0, 1.0)
        f2, b2 = uistyle.panel(col, "事件时间线", "每拍一段（颜色交替），段内标该拍 EF；方便核对 ED / ES")
        f2.pack(fill="x", pady=(10, 0))
        self.tl = tk.Canvas(b2, width=CW, height=TH, bg="#fafafa", highlightthickness=0)
        self.tl.pack()
        self.tl.bind("<Button-1>", lambda e: self._curve_click(e.x))
        f3, b3 = uistyle.panel(col, "全心", "节段室壁运动 + 右室 / 右房（板上，ED / ES 关键帧加跑）；仅复核提示")
        f3.pack(fill="x", pady=(10, 0))
        row = tk.Frame(b3, bg=uistyle.C_CARD)
        row.pack(fill="x")
        self.lab_glyph = tk.Label(row, bg=uistyle.C_CARD, bd=0)
        self.lab_glyph.pack(side="left", anchor="n", padx=(0, 10))
        right = tk.Frame(row, bg=uistyle.C_CARD)
        right.pack(side="left", fill="both", expand=True)
        self.var_wm = tk.StringVar(value="节段室壁运动：—")
        self.lab_wm = uistyle.label(right, var=self.var_wm, font=FONT_HEAD, wrap=True)
        self.lab_wm.pack(fill="x")
        hr = tk.Frame(right, bg=uistyle.C_CARD)
        hr.pack(fill="x", pady=(6, 0))
        self.var_rv = tk.StringVar(value="—")
        self.var_ra = tk.StringVar(value="—")
        for title, var in (("RV/LV 舒张末面积比", self.var_rv), ("RA/LV 面积比", self.var_ra)):
            c = tk.Frame(hr, bg=uistyle.C_CARD)
            c.pack(side="left", padx=(0, 26))
            uistyle.label(c, title, font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
            uistyle.label(c, var=var, font=FONT_HR, width=5,
                          fg=uistyle.C_PURPLE if var is self.var_rv else "#c2410c").pack(anchor="w")
        # 「触边判定」档位下拉已按用户要求删掉（11-19）：固定不查（border_px = 0），
        # 右室掩膜贴到扇形边界也照常出数、画轮廓。恢复要同时改 heart_block 与这里。
        self.var_heart = tk.StringVar(value="关键帧 / 现场模式跑完后显示（板端 v5.24）")
        uistyle.text_box(b3, self.var_heart, height=4, font=FONT_SMALL, fg="#245").pack(fill="x", pady=(6, 0))
        self._draw_glyph(None)

    def _build_results(self, body):
        col = tk.Frame(body, bg=uistyle.C_BG, width=RW)
        col.grid(row=0, column=2, sticky="n")
        f, b = uistyle.panel(col, "结果", "不作诊断")
        f.pack(fill="x")
        ttk.Button(f.head, text="送到快照测量 →", command=self._send_snapshot).pack(side="right")
        top = tk.Frame(b, bg=uistyle.C_CARD)
        top.pack(fill="x")
        efc = tk.Frame(top, bg=uistyle.C_CARD)
        efc.pack(side="left")
        uistyle.label(efc, "射血分数 EF", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
        self.var_ef = tk.StringVar(value="—")
        self.lab_ef = uistyle.label(efc, var=self.var_ef, font=FONT_EF, fg=uistyle.C_TEXT, width=5)
        self.lab_ef.pack(anchor="w")
        hrc = tk.Frame(top, bg=uistyle.C_CARD)
        hrc.pack(side="left", padx=(26, 0), anchor="n")
        uistyle.label(hrc, "心率（近 %d 拍）" % HR_RECENT, font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
        self.var_hr = tk.StringVar(value="—")
        uistyle.label(hrc, var=self.var_hr, font=FONT_HR, width=8).pack(anchor="w", pady=(6, 0))
        qc = tk.Frame(top, bg=uistyle.C_CARD)
        qc.pack(side="left", padx=(26, 0), anchor="n")
        uistyle.label(qc, "图像质量", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
        qrow = tk.Frame(qc, bg=uistyle.C_CARD)
        qrow.pack(anchor="w", pady=(6, 0))
        self.cv_light = tk.Canvas(qrow, width=22, height=22, bg=uistyle.C_CARD, highlightthickness=0)
        self.cv_light.pack(side="left")
        self.var_light = tk.StringVar(value="—")
        uistyle.label(qrow, var=self.var_light, font=FONT_HR, width=4).pack(side="left", padx=(4, 0))
        self.var_efnote = tk.StringVar(value="")
        uistyle.label(b, var=self.var_efnote, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x")
        self.cv_iv = tk.Canvas(b, width=RW - 24, height=62, bg=uistyle.C_CARD, highlightthickness=0)
        self.cv_iv.pack(pady=(6, 0))
        self.var_band = tk.StringVar(value="LVEF 分档：—")
        self.lab_band = uistyle.label(b, var=self.var_band, font=FONT_HEAD, anchor="w", width=36)
        self.lab_band.pack(fill="x", pady=(4, 0))
        self.var_review = tk.StringVar(value="")
        self.lab_review = tk.Label(b, textvariable=self.var_review, font=FONT_HEAD, fg="white", bg=uistyle.C_CARD,
                                   anchor="w", justify="left", padx=8, pady=4, wraplength=RW - 40)
        self.lab_review.pack(fill="x", pady=(4, 0))
        self.var_qwhy = tk.StringVar(value="")
        uistyle.label(b, var=self.var_qwhy, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x", pady=(4, 0))
        uistyle.label(b, "提示方向（辅助复核，不作诊断；判据见报告）", font=FONT_SMALL, fg=uistyle.C_MUTED) \
            .pack(anchor="w", pady=(6, 0))
        self.var_hint = tk.StringVar(value="")       # clinical_hints：测量值 -> 可能与哪类问题有关
        uistyle.text_box(b, self.var_hint, height=4, font=FONT_SMALL, fg="#9a3412").pack(fill="x")
        uistyle.label(b, "逐拍", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w", pady=(4, 0))
        self.var_beats = tk.StringVar(value="")
        uistyle.text_box(b, self.var_beats, height=2, font=FONT_MONO).pack(fill="x")
        uistyle.label(b, "临床测量", font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w", pady=(4, 0))
        self.var_clin = tk.StringVar(value="")
        uistyle.text_box(b, self.var_clin, height=3, font=FONT_SMALL, fg="#245").pack(fill="x")
        self.var_ref = tk.StringVar(value="")
        uistyle.label(b, var=self.var_ref, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x", pady=(4, 0))
        self._draw_interval()
        self._draw_light(None)

    def _build_process(self, body):
        c = uistyle.card(body)
        c.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 10))
        row = tk.Frame(c, bg=uistyle.C_CARD)
        row.pack(fill="x", padx=14, pady=(8, 0))
        head = tk.Frame(row, bg=uistyle.C_CARD)
        head.pack(side="left", padx=(0, 18), anchor="n")
        uistyle.label(head, "计算过程", font=FONT_HEAD).pack(anchor="w")
        uistyle.label(head, "分割阈值 logit ≥ %d · 128 档" % (self.model["thr"] if self.model else 1),
                      font=FONT_SMALL, fg=uistyle.C_MUTED).pack(anchor="w")
        self.var_k_sched = chip(row, "运行方式", 8)
        self.var_k_pl = chip(row, "PL 推理帧", 17)
        self.var_k_plms = chip(row, "每关键帧 PL", 8)
        self.var_k_trk, self.var_k_total, self.var_k_proc, self.var_k_lat, self.var_k_fix = (tk.StringVar(value="—")
                                                                                            for _ in range(5))
        if self.live:                                    # 设计报告 5.3 节的口径：每帧处理占帧间隔、采到 → 掩膜 + 容积
            self.var_k_proc = chip(row, "每帧处理 / 帧间隔（预算占用）", 22)
            self.var_k_lat = chip(row, "延迟 P50 / P99（采到 → 掩膜 + 容积）", 18)
            self.var_k_fps = chip(row, "帧率（信号源 · 处理帧数）", 18)
        else:
            self.var_k_trk = chip(row, "板上跟踪", 10)
            self.var_k_total = chip(row, "板上用时 / 视频", 14)
            self.var_k_fps = chip(row, "帧率（视频 / 板上处理）", 14)
            self.var_k_fix = chip(row, "纠错", 6)
        self.pbar = ttk.Progressbar(c, mode="determinate")
        if self.live:                                    # 逐帧延迟图代替进度条（x 本身就是进度）
            self.cv_lat = tk.Canvas(c, height=LAT_H, bg="#fafafa", highlightthickness=0)
            self.cv_lat.pack(fill="x", padx=14, pady=(8, 10))
            self.cv_lat.bind("<Configure>", lambda e: self._draw_latency())
        else:
            self.pbar.pack(fill="x", padx=14, pady=(8, 10))
        # 详细文字走主窗口状态栏（省一行高度，1080p 屏也放得下）
        self.var_txt = tk.StringVar(value=("接上采集卡，点「开始采集」：板上逐帧采集、分割、跟踪，每帧回掩膜和容积"
                                           if self.live else "「打开视频…」选一段 A4C 视频，再「开始分析」（整段交给板子，PC 只显示）"))

    def _sched(self):
        return next((k for k, v in SCHEDS if v == self.var_sched.get()), "keyframe")

    def _hostport(self):
        try:
            return self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        except ValueError:
            return self.app.link.host, self.app.link.port

    def _say(self, msg):
        self.var_txt.set(msg)
        if self.app.views.get(self.app.mode) is self or not self.app.views:   # 别的模式在前台时不抢状态栏
            self.app.status(msg.splitlines()[0] if msg else "")

    # ================================================================ 载入（关键帧模式）
    def _open(self):
        if self.busy:
            return
        # 默认目录优先级：本机视频库 -> 原来的 EchoNet-Dynamic\Videos -> 上位机目录
        vd = next((d for d in (CARDIAC_VIDEO_DIR, os.path.join(ECHONET_DIR, "Videos"), HERE)
                   if os.path.isdir(d)), HERE)
        path = filedialog.askopenfilename(parent=self.frame, initialdir=vd,
                                          title="打开 A4C 心超视频", filetypes=[("AVI", "*.avi"), ("全部", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        self._say("载入 %s …" % os.path.basename(path))

        def work():
            try:
                v, fps, raw = read_video(self.ws, path)
                self.queue.put(("loaded", path, v, fps, raw))
            except Exception as e:
                self.queue.put(("err", "载入失败：%s" % e))
        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, path, v, fps, raw=None):
        self._pause()
        self.frames, self.fps, self.name = v, fps, os.path.basename(path)
        self.rawvid = raw                            # 录屏的原始首帧 + 扇区方块（自动定标就靠它上面的深度刻度）；EchoNet 112 视频为 None
        self.native = (raw or {}).get("frames")      # 预览用的原生分辨帧（11-18）：112 的视频 / 现场模式为 None
        self.native_have, self.live_native = None, False   # 现场模式（v5.28）那两个字段在这里复位
        self.crop = (raw or {}).get("crop")
        self.ndec = int((raw or {}).get("dec") or 1)
        self.ruler, self._ruler_from = None, None
        self._apply_view()
        self._auto_calibrate((raw or {}).get("frame"), path, os.path.basename(path))   # 载入即自动定标（后台）
        self._show_scale()
        self._reset_state()
        self._clear_results()
        self.var_name.set("%s　%d 帧 · %.0f fps · %.2f s" % (self.name, len(v), fps, len(v) / fps))
        self.scale.configure(to=len(v) - 1)
        self._ref_line()
        self._say("%s：%d 帧，%.0f 帧/秒（%.2f s）。点「开始分析」" % (self.name, len(v), fps, len(v) / fps))
        self._goto(0)
        self._redraw()

    def _ref_line(self):
        name = self.name if not self.live else (self.src or {}).get("video")
        r = self.ref.get(name or "")
        h = self.hmc.get(name or "")
        if r:
            self.var_ref.set("数据集标注 EF（EchoNet FileList，仅作对照）：%.1f%%，划分 %s" % r)
        elif h:
            mi = [self.ws.WM_NAMES[k] for k, f in enumerate(h[0]) if f]
            self.var_ref.set("HMC-QU 标注（仅作对照，%s）：%s" % ({"dev": "开发集", "test": "测试集"}.get(h[1], h[1]),
                                                            ("心梗节段 " + "、".join(mi)) if mi else "无心梗节段"))
        else:
            self.var_ref.set("")

    def _report_quality(self):
        """报告里的质量灯：CAMUS 源（v5.26 chain 1）用扫查最后 1 s 的 CAMUS 口径灯（整段的灯是程序 16 链 = EchoNet 口径）。"""
        ro = (self.res or {}).get("rolling") or {}
        if ro.get("chain", 0) in (1, 2) and ro.get("updates"):
            return dict(light=ro["updates"][-1][1], reasons=[])
        return (self.res or {}).get("quality")

    def data_note(self):
        """本段素材的出处（状态栏 / 报告页脚）。"""
        if self.live and (self.src or {}).get("quality") == "camus":
            return "画面来自 CAMUS 验证集（Leclerc et al. 2019，CC BY-NC-SA 4.0，非商业），模拟扫查"
        name = (self.src or {}).get("video") if self.live else self.name
        name = name or self.name or ""
        if name in self.ref:
            return "画面来自 EchoNet-Dynamic（Stanford AIMI，研究使用协议，不得外传）"
        if name in self.hmc or name.upper().startswith("ES0"):
            return "画面来自 HMC-QU（CC BY-NC-SA 3.0 IGO）"
        return "画面来自公开数据集"

    # ================================================================ 板上分析
    def run(self):
        if self.busy or self.app.busy:
            self._say("板子正忙，稍等")
            return
        if self.model is None:
            self._say("proto.MODELS 里没有 echonet_lv（上位机太旧）")
            return
        if self.live:
            self._run_live()
            return
        if self.frames is None:
            self._say("先「打开视频…」")
            return
        self._pause()
        self._reset_state()
        self._clear_results()
        sched = self._sched()
        self.pbar.configure(maximum=len(self.frames), value=0)
        self._set_busy(True, stoppable=False)
        self._say("关键帧模式：上传 %s（%d 帧）到板子，%s …" % (self.name, len(self.frames), self.var_sched.get()))
        threading.Thread(target=self._job_worker, args=(self._hostport(), sched), daemon=True).start()

    def _job_worker(self, hp, sched):
        link = self.app.link
        t0 = time.perf_counter()
        try:
            link.host, link.port = hp
            link.connect()                           # 板端 10 s 空闲超时：任务前总是重连
            caps = jobs.caps(link)
            if 2 not in caps.get("echo_modes", []):
                self.queue.put(("err", "板端不支持关键帧模式（需要服务端 v5.21+，现在是 %s）" % caps.get("server", "?")))
                return
            link.connect()
            kf = jobs.load_kf_config(sched)
            hc = self._heart_cfg() if "heart" in caps else None
            self.queue.put(("cfg", kf, hc, caps.get("server")))
            r = jobs.echo_ef(link, self.conf, self.model["scale"], self.frames, self.fps, model=self.model["index"],
                             flags=jobs.ECHO_MASKS, on_progress=lambda p: self.queue.put(("prog", p)),
                             keyframe=kf, heart=hc)
            link.close()                             # 连接与平台扩展共用：用完就放，下一个用的人重连（板端 10 s 空闲会断）
            self.queue.put(("job", r, time.perf_counter() - t0))
        except Exception as e:
            link.close()
            self.queue.put(("err", "板上分析失败：%s" % e))

    def _run_live(self):
        label, src = next((s for s in self.sources if s[0] == self.var_src.get()), self.sources[0])
        crop = src["truth"] if self.var_crop.get() == CROP_FIXED and src["truth"] else None
        if src.get("quality") == "camus" and src.get("truth"):
            crop = src["truth"]                          # CAMUS 口径要固定方块（板上 128 帧 = 原 CAMUS 帧，逐位）
        np = self.np
        self._pause()
        if self.lat and self.res is not None:            # 上一次完整跑完的延迟留作灰线对照（推荐档 vs 每帧推理）
            lat = self.res["stream"]["latency_ms"]
            self.prev_run = dict(lat=list(self.lat), fps=self.fps, p50=lat["p50"], p99=lat["p99"], src=self.name,
                                 label="%s · %s" % ("推荐档" if self.res["keyframe"]["key_frac"] < 1 else "每帧推理",
                                                    self.name))
        self._reset_state()
        self._clear_results()
        self.src = src
        self.src_kind = "采集卡" if src["kind"] == "v4l2" else "测试源"    # 后者只可能来自自检脚本注入的 .y8
        self.name, self.fps = src["name"], src["fps"]
        self.frames = np.zeros((src["frames"], N, N), np.uint8)
        # v5.28：板上每帧回一块原生裁剪帧（预览按它 1:1 画）+ 开流时一块整屏（定标）；没收到就还是 112 放大
        self.native, self.native_have, self.crop, self.ndec = None, None, None, 1
        self.live_native, self.live_still = False, None
        self._stills = []
        self.ruler, self._ruler_from = None, None
        self._apply_view()
        # 现场模式：信号源对应的原始录屏里就有深度刻度 -> 开始采集时后台自动定标（11-18）。
        # 采集卡没有原始录屏（video=None），这里是空操作；自检脚本注入的 .y8 源才有得读。
        self._auto_calibrate(None, raw_video_path(src.get("video")), src.get("video"))
        self._show_scale()
        self.scale.configure(to=src["frames"] - 1)
        self._ref_line()
        self.pbar.configure(maximum=src["frames"], value=0)
        self.stop = threading.Event()
        self._set_busy(True, stoppable=True)
        self._say("现场模式：%s %s，%s …" % (self.src_kind, src["name"],
                                            "固定方块 %s" % (crop,) if crop else "先找画面（约 1 s）"))
        self._goto(0)
        self._redraw()
        threading.Thread(target=self._live_worker, args=(self._hostport(), self._sched(), src, crop, self.stop),
                         daemon=True).start()

    def _live_worker(self, hp, sched, src, crop, stop):
        link = self.app.link
        t0 = time.perf_counter()
        try:
            link.host, link.port = hp
            link.connect()
            caps = jobs.caps(link)
            if 3 not in caps.get("echo_modes", []):
                self.queue.put(("err", "板端不支持现场模式（需要服务端 v5.22+，现在是 %s）" % caps.get("server", "?")))
                return
            link.connect()
            kf = jobs.load_kf_config(sched)
            hc = self._heart_cfg() if "heart" in caps else None
            # v5.25：扫查时滚动灯 + 切面；板端 DP（GTK 兼容层）上不开：DP 界面自己占 CPU，开了现场推荐档 P99 在 71–83 ms 浮动
            roll = jobs.ROLL if caps.get("live_roll") and hc is not None and not getattr(tk, "_IS_GTK_SHIM", False) else None
            tag = "EchoNet 口径，仅作提示" if roll else ""
            heart = hc
            if src.get("quality") == "camus":            # v5.26：CAMUS 源用 CAMUS 口径（这个源就是为了看灯，DP 上也开）
                # 不请求全心：半个心动周期往返播放，节段室壁运动 / 右心比值不成立（右室程序号单独写在 RQ01 里，切面不受影响）
                heart = None
                if caps.get("live_roll_camus"):
                    roll, tag = jobs.load_roll_camus(), "CAMUS 专家等级标定"
                    if roll["chain"] == 2 and caps.get("live_f1_engine"):   # 关键帧也用 F1：EF 按 CAMUS TRAIN 校准
                        kf = dict(kf, a=roll["camus"]["ef_a"], b=roll["camus"]["ef_b"])
                    else:
                        roll["chain"] = 1                    # 老服务端：灯照样，曲线仍是程序 16
                else:
                    roll, tag = None, "CAMUS 口径的灯需要服务端 v5.26（现在是 %s）" % caps.get("server", "?")
            self.queue.put(("rolltag", tag))
            self.queue.put(("cfg", kf, hc, caps.get("server")))

            def on_live(d):
                if stop.is_set():
                    raise _Stopped()
                self.queue.put(("lframe", d))
            r = jobs.echo_live(link, self.conf, self.model["scale"], kf, src["name"], source=src["kind"], crop=crop,
                               model=self.model["index"], max_frames=src["frames"], every=1,
                               out_flags=jobs.LIVE_FRAMES112 | jobs.LIVE_FRAME |
                               # 原生帧预览：每帧多回一块裁剪方块（480² 灰度，约 6.9 MB/s @30fps）。
                               # 板端 v5.28+ 才认；临时想回到 112 放大就设 WS_LIVE_NATIVE=0。
                               (jobs.LIVE_NATIVE if (caps.get("live_native") and
                                                     os.environ.get("WS_LIVE_NATIVE", "1") != "0") else 0),
                               v4l2=(640, 480, src["fps"]),
                               on_live=on_live, on_progress=lambda p: self.queue.put(("prog", p)), heart=heart,
                               roll=roll)
            link.close()
            self.queue.put(("job", r, time.perf_counter() - t0))
        except _Stopped:
            link.close()
            self.queue.put(("err", "已停止：连接已断开，板上任务随之结束（现场模式中途停止没有最终结果）"))
        except Exception as e:
            link.close()
            self.queue.put(("err", live_err(src, e)))

    def _stop(self):
        if self.stop is not None:
            self.stop.set()

    def _set_busy(self, busy, stoppable=False):
        self.busy = self.app.busy = busy
        self.btn_run.state(["disabled"] if busy else ["!disabled"])
        self.btn_stop.state(["!disabled"] if busy and stoppable else ["disabled"])
        if busy:
            self.btn_rep.state(["disabled"])

    def _hr_recent(self, beats=None):
        """显示用的心率（次/分）：最近 HR_RECENT 拍的瞬时心率平均（echo_ws.hr_recent）。

        beats=None 是现场滚动中（板上每 0.25 s 回一次进度）：上位机自己从容积曲线尾部找拍，
        取最近几拍；beats 给定时（跑完、纠错、片段重算）直接用那一份逐拍结果。
        回 None = 还凑不出一个间期，调用方退回板上回的那个 hr（60 / 中位 RR）。
        """
        ws, fps = self.ws, self.fps
        if not fps:
            return None
        if beats is not None:
            hr = ws.hr_recent(beats, fps, HR_RECENT)
        else:
            n = len(self.vols)
            if n < 3:
                return None
            a = max(0, n - int(round(HR_LIVE_SPAN * fps)))    # 只看尾巴：算得快，最近几拍不受影响
            if (a, n) == self._hr_key:                        # 同一条曲线不重复算（每帧都来一次 live 块）
                return self._hr_val
            self._hr_key = (a, n)
            self._hr_val = None
            try:
                r = ws.analyze(self.vols[a:n], fps, 1, self.conf["cfg"])
                hr = ws.hr_recent(r["beats"], fps, HR_RECENT)
            except Exception:                                 # 曲线太短 / 找拍失败：不打断现场流程
                _log_exc("_hr_recent")
                return None
            if hr is None or not (HR_PLAUSIBLE[0] <= hr <= HR_PLAUSIBLE[1]):
                return None
            self._hr_val = hr
        if hr is None or not (HR_PLAUSIBLE[0] <= hr <= HR_PLAUSIBLE[1]):
            return None
        return hr

    def _show_hr(self, hr, board=None):
        """心率那一格：优先用最近几拍平均；算不出来才退回板上的值。"""
        if hr is None:
            hr = board
        self.var_hr.set(("%.0f 次/分" % hr) if hr else "—")

    def _on_prog(self, p):
        if p.get("stage") == "live":                       # 现场：板上滚动 EF（已按这套调度校准）
            if p.get("beats", 0) > 0 and p.get("ef", -1) >= 0:
                self.var_ef.set("%.0f%%" % p["ef"])
                self._show_hr(self._hr_recent(), p.get("hr"))
                self.var_efnote.set("滚动 EF（板上每 0.25 s 更新）")
            if "key_frac" in p:
                self.var_k_pl.set("%.0f%%" % (100 * p["key_frac"]))
                self.var_k_lat.set("当前 %.0f ms" % p["lag_ms"])
                self.var_txt.set("现场：已分析 %d 帧（已采到 %d），关键帧 %.0f%%，当前延迟 %.0f ms"
                                 % (p["done"], p["captured"], 100 * p["key_frac"], p["lag_ms"]))
            return
        if p.get("stage") == "frames" and p.get("total"):
            self.pbar.configure(maximum=p["total"], value=p.get("done", 0))

    def _on_lframe(self, d):
        np = self.np
        if "still" in d:                                 # v5.28：整屏一块（定标用），不是某一帧
            self._on_live_still(d["still"])
            return
        t = d["t"]
        if t >= len(self.frames):
            return
        if d.get("native") is not None:                  # v5.28：原生裁剪帧（预览用）
            self._keep_native(t, d["native"])
        if d["frame"] is not None:
            self.frames[t] = np.frombuffer(d["frame"], np.uint8).reshape(N, N)
        self.masks[t] = np.unpackbits(np.frombuffer(d["mask"], np.uint8), bitorder="little")[:N * N].reshape(N, N) > 0
        self.vols.append(d["vol"])
        self.keyv.append(bool(d["key"]))
        s = d.get("stamps") or {}
        if "end" in s:
            self.lat.append((s["end"] - s["captured"]) / 1000.0)
            self.proc.append((s["end"] - s["start"]) / 1000.0)
        self.pbar.configure(value=len(self.vols))
        roll = (d.get("roll_light"), d.get("view", 0))
        hold = d.get("hold", False)
        if (roll != self.roll or hold != self.roll_hold) and (roll[0] is not None or roll[1] or self.roll_hold):
            self.roll_moved = self.roll_moved or (self.roll_hold and not hold)
            self.roll, self.roll_hold = roll, hold
            self._show_roll()
        now = time.perf_counter()
        # 现场：画面只画最新一帧，按 RENDER_GAP 限速（数据每帧都收）。原生分辨率预览在板端画一帧要几十毫秒
        # （11-19 实测合成 480² 约 40 ms），按固定间隔刷会把界面线程占满、按钮与定标消息都排不进来 ——
        # 实际间隔取 max(RENDER_GAP, 1.4 × 上一帧画图耗时)，给主循环留三成空。
        if now - self.t_render > max(RENDER_GAP, 1.4 * self.render_cost):
            self.t_render = now
            _t0 = time.perf_counter()
            self._goto(t)
            self.render_cost = 0.7 * self.render_cost + 0.3 * (time.perf_counter() - _t0)
        if now - self.t_curve > LIVE_REDRAW_GAP:          # 11-19：板端 0.25→0.40 s（这三样一次共约 70 ms）
            self.t_curve = now
            self._redraw()
            self._show_budget()
            self._draw_latency()

    def _keep_native(self, t, nat):
        """现场模式（v5.28）：把板上回的原生裁剪帧存进 self.native（与 112 帧同下标，预览按它 1:1 画）。
        第一次收到时按内存预算决定要不要整幅抽稀（dec），顺手把预览尺寸切到原生分辨率。"""
        np = self.np
        side, n, raw = nat
        if self.native is None:
            if n < 16 or len(raw) < n * n:
                return
            frames = int((self.src or {}).get("frames") or 0) or len(self.frames) or 1
            dec = 1
            while dec < n and frames * (n // dec) * (n // dec) > NATIVE_LIVE_BYTES:
                dec += 1
            w = n // dec
            try:
                self.native = np.zeros((frames, w, w), np.uint8)
                self.native_have = bytearray(frames)
            except MemoryError:
                self.native, self.native_have = None, None
                self._say("内存不够存原生帧，预览回到 112 放大")
                return
            self.live_native = True
            self.nat_dec, self.nat_n = dec, n
            self.crop = dict(x=0, y=0, side=w)           # 存帧就是裁剪方块本身：掩膜铺满整幅
            self.ndec = float(dec) * float(side) / float(n)   # 存帧 1 像素 = 多少个源像素（毫米换算用）
            self._apply_view()
        if t >= len(self.native) or self.native_have[t]:
            return
        a = np.frombuffer(raw, np.uint8).reshape(n, n)
        d = self.nat_dec
        if d > 1:
            a = a[::d, ::d]
        w = self.native.shape[1]
        self.native[t][:a.shape[0], :a.shape[1]] = a[:w, :w]
        self.native_have[t] = 1

    def _on_live_still(self, st):
        """现场模式（v5.28）：板上开流后回 3 块**整屏**（第 0 / 30 / 60 帧）。深度刻度画在裁剪方块外面，
        只有整屏上才读得到；三块各读一次，取彼此相差 <3% 的那组的中位数 —— 和关键帧模式试第 0 / 15 / 30 帧
        同一个道理（单帧偶尔会读到别的东西，实测两次 0.347 / 0.519 mm/px）。"""
        self.live_still = st
        self._stills.append(st)
        key = self._raw_key() or ""
        if self.app.calib.get(key) is not None or key in self._calib_pending:
            return
        th = self._calib_th
        if th is None or not th.is_alive():
            self._calib_th = threading.Thread(target=self._calib_from_stills, daemon=True)
            self._calib_th.start()
            self._calib_pending[key] = time.time()

    def _calib_from_stills(self):
        """后台：三块整屏各读一次深度刻度 -> 一致的取中位数，交回界面线程写 app.calib。"""
        key = self._raw_key() or ""
        t0 = time.time()
        while len(self._stills) < 3 and time.time() - t0 < 8.0:
            time.sleep(0.2)
        vals = []
        for st in list(self._stills)[:3]:
            try:
                g = self.np.frombuffer(st["luma"], self.np.uint8)[:st["H"] * st["W"]].reshape(st["H"], st["W"])
                r = depth_ticks(self.np.repeat(g[..., None], 3, 2))
            except Exception:
                _log_exc("_calib_from_stills")
                r = None
            if r is None:
                continue
            (x0, y0), (x1, y1), cm, info = r
            d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            if d >= 5:
                vals.append((cm * 10.0 / d, r))
        best = None
        if len(vals) >= 2:
            vals.sort(key=lambda v: v[0])
            if vals[-1][0] > 1.10 * vals[0][0]:         # 三块整屏读数差一成以上：宁可不定标，让用户手动
                _log_line("[SCALE] %s：%d 块整屏读数不一致（%s mm/px），不定标"
                          % (key, len(vals), "、".join("%.3f" % v[0] for v in vals)))
            else:
                best = vals
        elif len(vals) == 1:
            best = vals
        r = None
        if best:
            best.sort(key=lambda v: v[0])
            r = best[len(best) // 2][1]
        self.queue.put(("scale", key, r, key))

    def _show_roll(self):
        """现场扫查时（服务端 v5.25）：最近 1 s 的质量灯（每 0.2 s 更新）+ 切面（右室程序每 0.5 s 一次，最近 3 次中位数）。"""
        light, view = self.roll
        if light is not None:
            self._draw_light(dict(light=light))
            self.var_light.set(LIGHT_TXT[light])
        if self.roll_hold:                           # v5.26 CAMUS：灯定了、探头没动 -> 板上停了 F1 / 右室程序
            state = "已判定，探头未动，暂停判断"
        elif self.roll_moved and not view:
            state = "探头移动了，重新判断中"
        else:
            state = "扫查中"
        self.var_qwhy.set("%s（最近 1 s%s）：%s%s" % (
            state, "，" + self.roll_tag if self.roll_tag else "",
            {0: "画面可用", 1: "注意，可微调探头", 2: "画面差，建议调整探头"}.get(light, "质量灯攒满 1 s 后出"),
            ("；" + VIEW_TXT[view]) if view else ""))      # view = 0：没开切面判断（jobs.ROLL view_every=0）或还没出

    def _show_budget(self, captured=None):
        """每帧处理（处理线程从取帧到出掩膜 + 容积）的均值占帧间隔多少；设计报告 5.3 节：推荐档 26 %、每帧推理 121 %。"""
        if not self.proc or not self.fps:
            return
        gap = 1000.0 / self.fps
        m = sum(self.proc) / len(self.proc)
        self.var_k_proc.set("%.1f / %.0f ms（%.0f%%）" % (m, gap, 100 * m / gap))
        self.var_k_fps.set("%.0f fps · %d%s 帧" % (self.fps, len(self.proc), (" / %d" % captured) if captured else ""))

    def _on_job(self, r, wall):
        res = r["result"]
        self._set_busy(False)
        if not res.get("ok"):
            self._say("板上分析失败：%s" % res.get("error"))
            return
        np = self.np
        n = res["n_frames"]
        if res.get("mode") == 3:                            # 现场：换成板上裁好的 112 帧
            if "frames112" in r["blobs"]:
                self.frames = np.frombuffer(r["blobs"]["frames112"], np.uint8)[:n * N * N].reshape(n, N, N).copy()
            else:
                self.frames = self.frames[:n]
            self.fps = res["fps"]
            self.scale.configure(to=n - 1)
            if self.native is None:                       # 旧服务端 / 一块原生帧都没收到：回到 112 放大
                self.crop, self.ndec, self.live_native = None, 1, False
            self.ruler, self._ruler_from = None, None
            self._apply_view()
        self.res, self.raw, self.wall = res, r, wall
        self.vols = list(res["vols"])
        self.keyv = [c == "1" for c in res["key"]]
        if "masks" in r["blobs"]:
            mm = jobs.unpack_mask(r["blobs"]["masks"], (n, N, N))
            self.masks = {t: mm[t] for t in range(n)}
        self.orig = dict(masks={t: m.copy() for t, m in self.masks.items()}, vols=list(self.vols))
        self.ef_board = res["ef"]
        self.result = dict(ef=res["ef_raw"] if res["ef_raw"] is not None else float("nan"), fallback=res["fallback"],
                           hr=res["hr"], cycle_s=res["cycle_s"], beats=[dict(ed=b["ed"], es=b["es"], v_ed=b["v_ed"],
                                                                          v_es=b["v_es"], ef=b["ef"]) for b in res["beats"]])
        if "ef_sd" in res:
            self.result["ef_sd"] = res["ef_sd"]
        q = res["qc"]
        self.qc_level, self.qc_reasons = q["level"], self.ws.qc_reasons(q["why"], q, self.result)
        self.clinical, self.clinical_where = res.get("clinical"), "板上计算"
        self.ef_now = self.ef_board
        self._take_heart(r)
        k = res["keyframe"]
        self.stats = dict(n=n, wall=wall, ai_ms=k["pl_ms_per_key"])
        self._show_process()
        self.win = None
        self.var_winlen.set(WIN_LENS[1] if self._settled_from() is not None else WIN_LENS[0])
        self._win_len_changed(redraw=False)          # CAMUS 源默认「放好后」；其余全段（= 板上原始结果）
        self.btn_rep.state(["!disabled"] if n >= 3 else ["disabled"])
        self.var_fix.set("暂停在任意一帧，左键补漏、右键去多：FPGA 重算这一帧，后面各帧跟着重算到视频末尾")
        self._show_scale()
        if self.result["beats"] and not getattr(tk, "_IS_GTK_SHIM", False) and not self.playing:
            self.toggle_play()                       # 讲解时画面一直在动（点画面 / 曲线就停）；板上兼容层逐帧重绘太费 CPU，不自动播
        if self.result["beats"]:
            self._goto(self.result["beats"][len(self.result["beats"]) // 2]["es"])
        self._redraw()

    def _show_process(self):
        res, k = self.res, self.res["keyframe"]
        n = res["n_frames"]
        dur = n / self.fps
        self.var_k_sched.set("推荐档" if k["key_frac"] < 1 else "每帧推理")
        self.var_k_pl.set("%d / %d（%.1f%%）" % (k["n_key"], n, 100 * k["key_frac"]))
        self.var_k_plms.set("%.1f ms" % k["pl_ms_per_key"])
        self.var_k_trk.set("%.2f ms/帧" % k["track_ms_per_frame"] if k["key_frac"] < 1 else "—")
        self.var_k_total.set("%.2f s / %.2f s" % (res["ms"]["total"] / 1000, dur))
        self.pbar.configure(maximum=n, value=n)
        txt = ("%s：%d 帧，PL 推理 %d 帧（间隔触发 %d、残差触发 %d），其余帧在 PS 上块匹配跟踪；板上 %.2f s，视频本身 %.2f s"
               % ("现场模式（%s）" % self.src_kind if self.live else "关键帧模式（板上整病例）", n, k["n_key"],
                  k["reasons"]["interval"], k["reasons"]["residual"], res["ms"]["total"] / 1000, dur))
        if self.live and res.get("stream"):
            st = res["stream"]
            c, lat = st["crop"], st["latency_ms"]
            how = "自动找到" if c["auto"] else "固定"
            self.var_k_lat.set("%.0f / %.0f ms%s" % (lat["p50"], lat["p99"], "（含找画面 1 s）" if c["auto"] else ""))
            self.live_info = "现场（%s %s，%d×%d %.0f fps；画面方块%s (%d, %d) 边长 %d；%s）" % (
                self.src_kind, st["name"], st["W"], st["H"], st["src_fps"], how, c["x"], c["y"], c["side"],
                "关键帧推荐档" if k["key_frac"] < 1 else "每帧推理")
            txt += "\n%s %s（%d×%d），画面方块%s (%d, %d) 边长 %d；延迟（采到 → 掩膜 + 容积）P50 %.0f ms、P99 %.0f ms、最大 %.0f ms" % (
                self.src_kind, st["name"], st["W"], st["H"], how, c["x"], c["y"], c["side"], lat["p50"], lat["p99"], lat["max"])
            src_n = (self.src or {}).get("frames")
            self._show_budget(captured=src_n if src_n and src_n != n else None)
            if src_n == n:
                self.var_k_fps.set("%.0f fps · %d 帧不丢" % (self.fps, n))
            self._draw_latency()
        else:
            self.var_k_fps.set("%.0f / %.0f fps" % (self.fps, n / max(res["ms"]["total"] / 1000.0, 1e-6)))
        self._say(txt)

    def _take_heart(self, r):
        res = r["result"]
        self.heart, self.rh_frames = None, {}
        if "wall_motion" not in res and "right_heart" not in res:
            self.var_heart.set("CAMUS 模拟扫查源不做全心分析（半个心动周期往返播放，节段运动 / 右心比值不成立）"
                               if (res.get("rolling") or {}).get("chain", 0) in (1, 2) else "这次没有全心结果（要板端 v5.24）")
            self._draw_glyph(None)
            return
        self.heart = dict(wall_motion=res.get("wall_motion"), right_heart=res.get("right_heart"))
        rh = res.get("right_heart") or {}
        if "rh_masks" in r["blobs"] and rh.get("pairs"):
            for p, (rv_ed, rv_es, ra_es) in zip(rh["pairs"], jobs.unpack_rh(r["blobs"]["rh_masks"], len(rh["pairs"]))):
                self.rh_frames.setdefault(p["ed"], []).append((rv_ed, self.ws.RV_COLOR) if p["rv_ok_ed"] else None)
                self.rh_frames.setdefault(p["es"], []).extend(
                    [(rv_es, self.ws.RV_COLOR) if p["rv_ok_es"] else None, (ra_es, self.ws.RA_COLOR) if p["ra_ok"] else None])
        wm = self.heart["wall_motion"]
        if wm is None:
            self.var_wm.set("节段室壁运动：没有可用的心动周期")
            self.lab_wm.configure(fg=uistyle.C_MUTED)
        else:
            low = [self.ws.WM_NAMES[k] for k, f in enumerate(wm["seg"]) if f]
            self.var_wm.set(("提示运动减低：%s（建议复核）" % "、".join(low)) if low else "6 个节段均未达减低提示门限")
            self.lab_wm.configure(fg="#c2410c" if low or wm.get("flag") else uistyle.C_GREEN)
        self.var_rv.set("%.2f" % rh["rv_lv"] if rh.get("rv_lv") is not None else "—")
        self.var_ra.set("%.2f" % rh["ra_lv"] if rh.get("ra_lv") is not None else "—")
        self.var_heart.set("\n".join(self.ws.heart_text(res, self.hc_cfg)))
        self._draw_glyph(wm)

    # ================================================================ 结果卡片
    def _clear_results(self):
        self.var_ef.set("—")
        self.lab_ef.configure(fg=uistyle.C_TEXT)
        self.var_hr.set("—")
        self.var_efnote.set("")
        self.var_band.set("LVEF 分档：—")
        self.lab_band.configure(fg=uistyle.C_TEXT)
        self.var_review.set("")
        self.lab_review.configure(bg=uistyle.C_CARD)
        self.var_qwhy.set("")
        self.var_light.set("—")
        self.var_beats.set("")
        self.var_clin.set("")
        self.var_hint.set("")
        self.hints = None
        self.var_wm.set("节段室壁运动：—")
        self.lab_wm.configure(fg=uistyle.C_TEXT)
        self.var_rv.set("—")
        self.var_ra.set("—")
        self.var_heart.set("关键帧 / 现场模式跑完后显示（板端 v5.24）")
        for v in (self.var_k_sched, self.var_k_pl, self.var_k_plms, self.var_k_trk, self.var_k_total, self.var_k_lat,
                  self.var_k_proc, self.var_k_fps, self.var_k_fix):
            v.set("—")
        if self.live:
            self._draw_latency()
        self.var_fix.set("分析结束后可用（Ctrl+Z 撤销）")
        self.btn_rep.state(["disabled"])
        self._draw_interval()
        self._draw_light(None)
        self._draw_glyph(None)

    def _cal(self, e):
        """逐拍原始 EF -> 校准 EF（这套调度自己的 a / b，echo_kf_config.json，训练子集上拟合）。"""
        if self.kf_cfg:
            return self.kf_cfg["a"] * e + self.kf_cfg["b"]
        return self.ws.calibrated(e, self.conf, "full")

    def _show_results(self):
        ws, res, r = self.ws, self.res, self.result
        ef = self.ef_now
        band = ws.lvef_band(ef) if ef is not None else None
        fixed = bool(self.fixed)
        self.var_ef.set("%.0f%%" % ef if ef is not None else "—")
        self.lab_ef.configure(fg=BAND_COL[band] if band is not None else uistyle.C_TEXT)
        self.var_hr.set(("%.0f 次/分" % r["hr"]) if r.get("hr") else "—")
        if fixed:
            self.var_efnote.set("已人工纠错 %d 处：EF 由上位机按纠正后的容积重算（板上原始 %.0f%%）"
                                % (len(self.undo), self.ef_board))
        else:
            self.var_efnote.set("逐拍中位数，按本调度校准；%d 拍" % len(r["beats"]))
        if self.win is not None:
            a, b = self.win
            self.var_efnote.set("分析片段 %.1f–%.1f s：上位机按同一规则重算，%d 拍（全段板上 %.0f%%）%s" % (
                a / self.fps, b / self.fps, len(r["beats"]), self.ef_board if self.ef_board is not None else float("nan"),
                "；含人工纠错" if fixed else ""))
        iv = res.get("interval") if self.win is None and not self._no_beat_ef() else None
        # 80 % 区间是板上按全段、用 EchoNet 上校准的保形模型算的：片段不适用，CAMUS 源也不适用
        self._draw_interval(iv, ef)
        if band is not None:
            self.var_band.set("LVEF 分档：%s（ESC 2021）" % ws.LVEF_BANDS[band])
            self.lab_band.configure(fg=BAND_COL[band])
        else:
            self.var_band.set("LVEF 分档：未能测出")
        if iv:
            lo, hi = max(0.0, iv["lo80"]), min(100.0, iv["hi80"])
            cross = [b for b in (40, 50) if lo < b < hi]
            if iv["review"]:
                self.var_review.set("分型待定，建议复核：80%% 区间 %.0f–%.0f%% 跨过 %s 分界%s" % (
                    lo, hi, "、".join("%d%%" % b for b in cross), "（纠错前）" if fixed else ""))
                self.lab_review.configure(bg="#dc2626")
            else:
                self.var_review.set("80%% 区间 %.0f–%.0f%% 整体落在同一分档，无需按区间复核%s" % (
                    lo, hi, "（纠错前）" if fixed else ""))
                self.lab_review.configure(bg="#059669")
        else:
            self.var_review.set("")
            self.lab_review.configure(bg=uistyle.C_CARD)
        q = res.get("quality")
        ro = res.get("rolling")
        if ro and ro.get("chain", 0) in (1, 2) and ro.get("updates"):   # v5.26 CAMUS 源：整段的灯（程序 16 链）是 EchoNet 口径，不显示
            ls = [u[1] for u in ro["updates"]]
            vs = [v[3] for v in ro.get("views", [])]
            self._draw_light(dict(light=ls[-1]))
            self.var_light.set(LIGHT_TXT[ls[-1]])
            note = ("EF：F1（CAMUS 训练）分割 + 板上跟踪，CAMUS 训练集校准（验证集误差约 6 个百分点，单切面）；"
                    "片段只有半个心动周期、往返播放，不做全心分析" if ro.get("chain") == 2 else
                    "CAMUS 片段只有半个心动周期、往返播放：这个源只演示质量灯和切面，EF 没有临床意义，不做全心分析")
            self.var_qwhy.set("质量灯（CAMUS 专家等级标定，最后 1 s）：%s。扫查过程（每 %.1f s 一次）：绿 %d / 黄 %d / 红 %d 次%s\n%s" % (
                {0: "画面可用", 1: "注意，可微调探头", 2: "画面差，建议调整探头"}[ls[-1]], ro["every"] / (self.fps or 50.0),
                ls.count(0), ls.count(1), ls.count(2),
                ("；切面 %d 次判断里 %d 次是心尖四腔" % (len(vs), vs.count(2))) if vs else "", note))
            ho = ro.get("hold")
            if ho:                                       # 判定后保持：停了多少帧的 F1 / 右室程序，探头动了几次
                ev = ho.get("events", [])
                self.var_qwhy.set(self.var_qwhy.get() + "\n判定后保持：%d / %d 帧没再跑 F1（%.0f%%），判定 %d 次、探头移动后重判 %d 次" % (
                    ho["held_frames"], res["n_frames"], 100.0 * ho["held_frames"] / max(res["n_frames"], 1),
                    sum(1 for e_ in ev if e_[1] == 1), sum(1 for e_ in ev if e_[1] == 0)))
            q = None
        else:
            self._draw_light(q)
        if q:
            why = [ws.QUALITY_REASON[k] for k in q.get("reasons", []) if k in ws.QUALITY_REASON] if q["light"] > 0 else []
            self.var_light.set(LIGHT_TXT[q["light"]])
            self.var_qwhy.set("质量灯原因：%s（零额外模型，由分割输出和画面算出；只作提示，不参与复核判断）"
                              % ("；".join(why) if why else "无") + ("（纠错前）" if fixed else ""))
            if ro and ro.get("updates"):                 # 现场 v5.25：扫查过程中的滚动灯 / 切面
                ls = [u[1] for u in ro["updates"]]
                vs = [v[3] for v in ro.get("views", [])]
                self.var_qwhy.set(self.var_qwhy.get() + "\n扫查过程（每 0.2 s 一次，最近 1 s）：绿 %d / 黄 %d / 红 %d 次%s" % (
                    ls.count(0), ls.count(1), ls.count(2),
                    ("；切面 %d 次判断里 %d 次是心尖四腔" % (len(vs), vs.count(2))) if vs else ""))
        elif not (ro and ro.get("chain", 0) in (1, 2)):
            self.var_light.set("—")
            self.var_qwhy.set("")
        lines = []
        for j, b in enumerate(r["beats"]):
            lines.append("第 %d 拍  ED %3d → ES %3d   EF %.0f%%" % (j + 1, b["ed"], b["es"], self._cal(b["ef"])))
        if self.qc_reasons:
            lines.append("质控：" + "；".join(self.qc_reasons))
        self.var_beats.set("\n".join(lines) if lines else "没有找到完整心动周期")
        if self.clinical is not None and ef is not None:
            self.var_clin.set("（%s）\n" % self.clinical_where + "\n".join(ws.clinical_text(self.clinical, ef)))
        else:
            self.var_clin.set("")
        ch = sys.modules[__name__]
        heart = self.heart or {}
        self.hints = ch.echo_hints(ef, bool(iv and iv["review"]), r.get("hr"), bool((self.clinical or {}).get("irregular")),
                                   heart.get("wall_motion"), heart.get("right_heart"), (q or {}).get("light"), ws.WM_NAMES)
        if (res.get("rolling") or {}).get("chain", 0) in (1, 2):   # v5.26 CAMUS 源：EF 无临床意义，不给提示方向
            self.hints = []
        # 区间跨界那条上面红条已经写了，框里不重复（报告里照常列）
        self.var_hint.set(ch.text([h for h in self.hints if "区间跨过" not in h[1]]))

    def _draw_interval(self, iv=None, ef=None):
        """80 % / 90 % 区间条：横轴 EF 10–90 %，底色按 LVEF 三档，竖线 = 40 / 50 分界，黑三角 = EF。"""
        c = self.cv_iv
        c.delete("all")
        w = int(c.cget("width"))
        x0, x1, y0, y1 = 8, w - 8, 14, 38
        X = lambda v: x0 + (x1 - x0) * (min(max(v, 10.0), 90.0) - 10.0) / 80.0
        for (a, b_), bg in zip(((10, 40), (40, 50), (50, 90)), BAND_BG):
            c.create_rectangle(X(a), y0, X(b_), y1, fill=bg, outline="")
        for v in (40, 50):
            c.create_line(X(v), y0 - 4, X(v), y1 + 4, fill="#555", dash=(3, 2))
        for v in (10, 20, 30, 40, 50, 60, 70, 80, 90):
            c.create_text(X(v), y1 + 12, text="%d" % v, fill="#777", font=("Consolas", 8))
        c.create_text(x0, 5, text="EF 区间（%）", anchor="w", fill="#777", font=("Microsoft YaHei UI", 8))
        if not iv or ef is None:
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                          text="分析后显示 80% 区间" if ef is None else
                          "80% 区间模型在 EchoNet 上校准，CAMUS 源不给" if self._no_beat_ef() else
                          "80% 区间按全段估计，所选片段不给" if self.win is not None else "分析后显示 80% 区间", fill="#999",
                          font=("Microsoft YaHei UI", 9))
            return
        ym = (y0 + y1) / 2
        c.create_line(X(iv["lo90"]), ym, X(iv["hi90"]), ym, fill="#1e3a8a", width=2)
        c.create_rectangle(X(iv["lo80"]), ym - 6, X(iv["hi80"]), ym + 6, fill="#2563eb", outline="#1e3a8a")
        c.create_polygon(X(ef), y0 - 1, X(ef) - 6, y0 - 9, X(ef) + 6, y0 - 9, fill="#111")
        c.create_text(x1, 5, text="■ 80%%  ─ 90%%%s" % ("" if not self.fixed else "（纠错前）"), anchor="e",
                      fill="#555", font=("Microsoft YaHei UI", 8))

    def _draw_latency(self):
        """现场模式逐帧延迟（采到 → 掩膜 + 容积，板上时间戳）：蓝 = 本次，灰 = 上一次（例如每帧推理）；
        虚线 = 帧间隔与 80 ms 最坏目标；0–100 ms 占下面 55 % 高度，100 ms 以上压缩（每帧推理会涨到几百 ms）。"""
        if not self.live:
            return
        c = self.cv_lat
        c.delete("all")
        w, h = max(c.winfo_width(), 200), LAT_H
        x0, x1, yb, yt = 46, w - 8, h - 14, 16
        # 灰线只和同一个信号源比（推荐档 vs 每帧推理）；换了源就不画，免得拿 50 fps 的曲线去比 30 fps 的
        prev = self.prev_run if self.prev_run and self.prev_run.get("src") == (self.src or {}).get("name") else None
        runs = [r["lat"] for r in ([prev] if prev else [])] + [self.lat]
        top = max([100.0] + [max(r) for r in runs if r])
        split = yb - 0.55 * (yb - yt)

        def Y(v):
            if v <= 100 or top <= 100:
                return yb - (yb - split) * min(v, 100.0) / 100.0
            return split - (split - yt) * (v - 100.0) / (top - 100.0)
        n = max([len(self.frames) if self.frames is not None else 0] + [len(r) for r in runs] + [2])
        X = lambda t: x0 + (x1 - x0) * t / max(n - 1, 1)
        c.create_rectangle(x0, yt, x1, yb, outline="#ddd")
        gap = 1000.0 / self.fps if self.fps else 20.0
        for v, txt, col in ((gap, "帧间隔 %.0f" % gap, "#059669"), (80.0, "80 ms 目标", "#dc2626")):
            c.create_line(x0, Y(v), x1, Y(v), fill=col, dash=(4, 3))
            c.create_text(x1 - 2, Y(v) - 7, text=txt, anchor="e", fill=col, font=("Microsoft YaHei UI", 7))
        for v in ([0, 50, 100] + ([int(top)] if top > 100 else [])):
            c.create_text(x0 - 4, Y(v), text="%d" % v, anchor="e", fill="#777", font=("Consolas", 8))
        if prev and len(prev["lat"]) >= 2:
            pts = []
            for t, v in enumerate(prev["lat"]):
                pts += [X(t), Y(v)]
            c.create_line(*pts, fill="#a8b0bc", width=2)
        if len(self.lat) >= 2:
            pts = []
            for t, v in enumerate(self.lat):
                pts += [X(t), Y(v)]
            c.create_line(*pts, fill="#1e5ac8", width=2)
        for t, k in enumerate(self.keyv[:len(self.lat)]):
            if k:
                c.create_line(X(t), yb, X(t), yb + 5, fill="#2864c8", width=2)
        s = "延迟（ms，采到 → 掩膜 + 容积，板上时间戳）　蓝：本次"
        if self.res is not None and self.res.get("stream"):
            lat = self.res["stream"]["latency_ms"]
            s += " P50 %.0f / P99 %.0f" % (lat["p50"], lat["p99"])
        if prev:
            s += "　灰：上次 %s P50 %.0f / P99 %.0f" % (prev["label"], prev["p50"], prev["p99"])
        c.create_text(x0, 7, text=s, anchor="w", fill="#444", font=("Microsoft YaHei UI", 8))
        c.create_text(4, yb + 6, text="关键帧", anchor="w", fill="#2864c8", font=("Microsoft YaHei UI", 7))

    def _draw_light(self, q):
        c = self.cv_light
        c.delete("all")
        col = LIGHT_COL.get(q["light"]) if q else "#d1d5db"
        c.create_oval(2, 2, 20, 20, fill=col, outline="#666")

    def _draw_glyph(self, wm):
        from PIL import Image, ImageTk
        im = Image.new("RGB", (76, 96), (255, 255, 255))
        if wm:
            self.ws.draw_wm_glyph(im, wm["seg"], 6, 6)
        else:
            self.ws.draw_wm_glyph(im, [0] * 6, 6, 6)
            im = Image.blend(im, Image.new("RGB", im.size, (255, 255, 255)), 0.65)
        self.photos["glyph"] = ImageTk.PhotoImage(im)
        self.lab_glyph.configure(image=self.photos["glyph"])

    # ================================================================ 预览尺寸 + 像素标尺（11-18）
    def _src_size(self):
        """预览要画的源画面尺寸 (w, h)：有原生帧就是原视频分辨率，否则是 112 网格（再放大到 448）。"""
        if self.native is not None:
            return int(self.native.shape[2]), int(self.native.shape[1])
        return N, N

    def _apply_view(self):
        """按输入分辨率定预览尺寸（11-18）：1:1 像素；短边不到 448 的放大到 448（112 的视频与改造前一样）；
        超出屏幕可用区的等比缩到装得下。同时更新 kd（源像素 -> 显示像素）与 s（448 视图 -> 显示像素）。"""
        w, h = self._src_size()
        try:
            top = self.frame.winfo_toplevel()
            maxh = max(320, top.winfo_screenheight() - 360)      # 留给标题 / 标尺读数 / 导航 / 进度条 / 纠错卡片
            # 视频是三栏里的第一栏：右栏 RW + 中间容积曲线 CW + 边距都要让出来；
            # 再小也不低于「448 视图 + 40」，这样 112 的视频在小屏上也不会比改造前更小（宁可窗口超一点）
            maxw = max(FLOOR_SIDE + 40, top.winfo_screenwidth() - RW - CW - 120)
        except Exception:
            maxh = maxw = 10 ** 6
        k = max(1.0, FLOOR_SIDE / float(min(w, h)))
        k = min(k, maxh / float(h), maxw / float(w))
        self.vw, self.vh = max(1, int(round(w * k))), max(1, int(round(h * k)))
        self.kd = self.vw / float(w)
        self.s = self.vw / float(VIEW)
        try:
            self.cv_v.configure(width=self.vw, height=self.vh)
            self.scale.configure(length=self.vw)                 # 板端 GTK 兼容层可能不认 length：不认就算了
            self.cv_v.coords("hint", self.vw // 2, self.vh // 2)
        except Exception:
            pass
        self.var_xy.set(self._xy_idle())

    def _compose(self, t, mask, rh_items):
        """这一帧画成显示尺寸的 RGB：有原生帧就按**输入分辨率**画整幅（掩膜贴回扇区方块），
        没有（112 的 EchoNet 视频）就跟改造前一样把 112 放大。合成逻辑在 echo_ws.compose_preview。

        现场模式（11-19）：板上每帧回一块**裁剪方块的原生帧**（v5.28），存帧坐标就是方块本身（crop 按存帧算、
        dec 传 1）；某一帧没收到原生帧（板端没赶上 / 旧服务端）就那一帧退回 112 放大，不整段退。"""
        if self.native is not None:
            t = min(int(t), len(self.native) - 1)            # 原生帧数比 112 帧少时（异常源）不越界
        ok = self.native is not None and (self.native_have is None or
                                          (t < len(self.native_have) and self.native_have[t]))
        if not ok:
            return self.ws.compose_preview(None, t, None, 1, mask, rh_items, self.frames[t], self.vw, self.vh)
        dec = 1 if self.live_native else self.ndec
        return self.ws.compose_preview(self.native, t, self.crop, dec, mask,
                                       rh_items, self.frames[t], self.vw, self.vh)

    # ---------------------------------------------------------------- 像素标尺
    def _mm_per_px(self):
        """每个**源像素**多少毫米：原生帧 = 自动定标给的原视频 mm/像素（抽稀过要乘 dec）；
        112 网格 = _spacing() 的 112 尺度。没定标回 None（只报像素）。"""
        if self.native is not None:
            mm = self.app.calib.get(self._raw_key() or "")
            return None if mm is None else mm * float(self.ndec or 1)
        return self._spacing()

    def _src_unit(self):
        return "原视频像素" if self.native is not None else "112 网格"

    def _src_per_disp(self):
        """显示 1 像素 = 多少个"报出来的像素"（原生抽稀过就是 dec，112 网格是 1）。"""
        return float(self.ndec or 1) if self.native is not None else 1.0

    def _mm_of(self, px):
        """报出来的像素数（原视频像素 / 112 格）-> 毫米。原生帧的 mm/像素是按抽稀后的存帧给的，要除 dec。"""
        mm = self._mm_per_px()
        if mm is None:
            return None
        return px / self._src_per_disp() * mm

    def _len_text(self, d_disp):
        """显示像素长度 -> “N 像素 · x.x mm（y.yy cm）”。"""
        px = d_disp / (self.kd or 1.0) * self._src_per_disp()
        s = "%.0f 像素" % px
        v = self._mm_of(px)
        if v is None:
            s += " · 无标尺"
        else:
            s += " · %.1f mm" % v
            if v >= 10.0:
                s += "（%.2f cm）" % (v / 10.0)
        return s

    def _xy_idle(self):
        if self.ruler:
            x0, y0, x1, y1 = self.ruler
            return "测距 %s（%s）" % (self._len_text(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5), self._src_unit())
        return "悬停读坐标 · Shift+拖拽量两点（%s%s）" % (
            self._src_unit(), "" if self._mm_per_px() is not None else "，没读到深度刻度只报像素")

    def _hover(self, e):
        """鼠标移动：报源像素坐标（+ 毫米），有测距时同时报距离。"""
        x, y = min(max(e.x, 0), self.vw - 1), min(max(e.y, 0), self.vh - 1)
        f = self._src_per_disp()
        sx, sy = x / (self.kd or 1.0) * f, y / (self.kd or 1.0) * f
        txt = "x=%d y=%d %s" % (round(sx), round(sy), self._src_unit())
        mx, my = self._mm_of(sx), self._mm_of(sy)
        if mx is not None:
            txt += " · (%.1f, %.1f) mm" % (mx, my)
        if self.ruler:
            x0, y0, x1, y1 = self.ruler
            txt += "　测距 %s" % self._len_text(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)
        self.var_xy.set(txt)

    def _ruler_mode(self, e):
        """这一下左键是量距还是纠错：按住 Shift，或点了「量距」按钮。"""
        return bool(int(getattr(e, "state", 0)) & 0x0001) or bool(getattr(self, "ruler_armed", False))

    def _press(self, e):
        if self._ruler_mode(e):
            return self._ruler_press(e)
        return self._click(e, True)

    def _ruler_toggle_arm(self):
        self.ruler_armed = not bool(getattr(self, "ruler_armed", False))
        try:
            self.btn_rule.configure(text="量距中" if self.ruler_armed else "量距")
        except Exception:
            pass
        self.var_xy.set("量距：在画面上拖一条线" if self.ruler_armed else self._xy_idle())

    def _ruler_press(self, e):
        x, y = min(max(e.x, 0), self.vw - 1), min(max(e.y, 0), self.vh - 1)
        self._ruler_from = (x, y)
        self.ruler = (x, y, x, y)
        self._draw_ruler()
        return "break"

    def _ruler_drag(self, e):
        if self._ruler_from is None:
            return
        x, y = min(max(e.x, 0), self.vw - 1), min(max(e.y, 0), self.vh - 1)
        self.ruler = (self._ruler_from[0], self._ruler_from[1], x, y)
        self._draw_ruler()
        self.var_xy.set("测距 %s（%s）" % (
            self._len_text(((x - self._ruler_from[0]) ** 2 + (y - self._ruler_from[1]) ** 2) ** 0.5), self._src_unit()))
        return "break"

    def _ruler_release(self, e):
        if self._ruler_from is None:
            return
        self._ruler_drag(e)
        self._ruler_from = None
        if self.ruler and abs(self.ruler[2] - self.ruler[0]) < 2 and abs(self.ruler[3] - self.ruler[1]) < 2:
            self.ruler = None                               # 只是点了一下：不当测距
        self._draw_ruler()
        self.var_xy.set(self._xy_idle())
        return "break"

    def _ruler_clear(self):
        self.ruler, self._ruler_from = None, None
        try:
            self.cv_v.delete("ruler")
        except Exception:
            pass
        self.var_xy.set(self._xy_idle())

    def _draw_ruler(self):
        """把测距线画在画布上（Tk 图元，不改 PIL 图；每帧 _goto 之后重画一次）。"""
        c = self.cv_v
        try:
            c.delete("ruler")
            if not self.ruler:
                return
            x0, y0, x1, y1 = self.ruler
            c.create_line(x0, y0, x1, y1, fill="#22d3ee", width=2, tags="ruler")
            for x, y in ((x0, y0), (x1, y1)):
                c.create_line(x - 7, y, x + 7, y, fill="#22d3ee", width=2, tags="ruler")
                c.create_line(x, y - 7, x, y + 7, fill="#22d3ee", width=2, tags="ruler")
            d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            c.create_text((x0 + x1) / 2.0 + 10, (y0 + y1) / 2.0 - 10, text=self._len_text(d), anchor="w",
                          fill="#a5f3fc", font=FONT_SMALL, tags="ruler")
        except Exception:
            _log_exc("EchoView._draw_ruler")

    # ================================================================ 视频 / 曲线 / 时间线
    def _goto(self, t, from_scale=False):
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

    def _phase(self, t):
        if self.result:
            for b in self.result["beats"]:
                if b["ed"] == t:
                    return "ED"
                if b["es"] == t:
                    return "ES"
        return ""

    def _span(self):
        """曲线 / 时间线显示的帧范围 [a, b)：选了分析片段就自动放大到片段（11-03），否则全段。"""
        n = len(self.frames) if self.frames is not None else 0
        return self.win if self.win is not None else (0, n)

    def _xy(self):
        a, b = self._span()
        x = lambda t: PLOT_L + (CW - PLOT_L - 10) * (t - a) / max(b - 1 - a, 1)
        vs = self.vols[a:b] or self.vols or [0.0, 1.0]
        lo, hi = min(vs), max(vs)
        top, bot = 22, PLOT_B - 24
        y = lambda v: bot - (bot - top) * (v - lo) / max(hi - lo, 1e-9)
        return x, y

    def _redraw(self):
        self._draw_curve()
        self._draw_timeline()

    def _draw_curve(self):
        c = self.cv
        c.delete("all")
        c.create_text(4, 14, text="容积", anchor="w", fill="#777", font=("Microsoft YaHei UI", 8))
        if self.frames is None or (self.live and not self.vols):
            c.create_text(CW / 2, CH / 2, text="分析时逐帧画出左心室容积（单平面圆盘法，像素单位）\n"
                                               "▲ 舒张末 ED　▼ 收缩末 ES，每拍下面标 EF", fill="#999",
                          font=("Microsoft YaHei UI", 9), justify="center")
            if self.frames is None:
                return
        x, y = self._xy()
        a, b = self._span()
        c.create_rectangle(PLOT_L, 8, CW - 10, PLOT_B, outline="#ddd")
        step = 0.5 if (b - a) / self.fps < 5 else 1.0     # 放大到片段时半秒一个刻度
        s = math.ceil(a / self.fps / step - 1e-9) * step
        while s * self.fps <= b - 1 + 1e-9:
            xs = x(s * self.fps)
            c.create_line(xs, PLOT_B, xs, PLOT_B + 4, fill="#999")
            c.create_text(xs, CH - 9, text="%g s" % s, fill="#777", font=("Consolas", 8))
            s += step
        if self.win is not None:                         # 放大中：标出是哪一段
            c.create_text(CW - 12, 14, anchor="e", fill="#1e5ac8", font=("Microsoft YaHei UI", 8),
                          text="已放大到分析片段 %.1f–%.1f s（全段 %.1f s）" % (a / self.fps, b / self.fps,
                                                                       len(self.frames) / self.fps))
        if self.orig and self.fixed:                     # 纠错前的曲线（灰）
            pts = []
            for t in range(a, min(b, len(self.orig["vols"]))):
                pts += [x(t), y(self.orig["vols"][t])]
            if len(pts) >= 4:
                c.create_line(*pts, fill="#c0c7d2", width=1, dash=(4, 2))
        if len(self.vols) >= 2:
            pts = []
            for t in range(a, min(b, len(self.vols))):
                pts += [x(t), y(self.vols[t])]
            if len(pts) >= 4:
                c.create_line(*pts, fill="#1e5ac8", width=2)
        for t in self.fixed:
            if a <= t < min(b, len(self.vols)):
                c.create_oval(x(t) - 3, y(self.vols[t]) - 3, x(t) + 3, y(self.vols[t]) + 3, fill="#f59e0b", outline="")
        if self.result:
            for b in self.result["beats"]:
                xe, ye, xs_, ys = x(b["ed"]), y(b["v_ed"]), x(b["es"]), y(b["v_es"])
                c.create_polygon(xe, ye - 10, xe - 6, ye - 2, xe + 6, ye - 2, fill="#d83232")
                c.create_polygon(xs_, ys + 10, xs_ - 6, ys + 2, xs_ + 6, ys + 2, fill="#2878dc")
                if not self._no_beat_ef():
                    c.create_text(xs_, ys + 18, text="%.0f%%" % self._cal(b["ef"]), fill="#333", font=("Consolas", 9))
        self._draw_cursor()

    def _draw_cursor(self):
        if self.frames is None:
            return
        x, _ = self._xy()
        a, b = self._span()
        for cv, y1 in ((self.cv, PLOT_B), (self.tl, TH - 3)):
            cv.delete("cursor")
            if a <= self.cur < b:                        # 放大到片段时，片段外的帧不画光标
                cv.create_line(x(self.cur), 6, x(self.cur), y1, fill="#e0a000", width=2, tags="cursor")

    def _draw_timeline(self):
        """事件时间线（11-19 起只画心拍）。

        原来三栏：心拍条（每拍一段 + 该拍 EF）、关键帧 / 跟踪帧（蓝 / 灰竖线）、人工纠错 / 跟着重算（橙 / 浅橙竖线）。
        后两栏是给"关键帧调度"和"点击纠错"做核对用的，日常看片子时是噪声，按用户要求删掉；画布高度也从 92 收到 38。
        这两类帧号本身没丢：帧标题里照旧写着"关键帧（PL 分割）/ 跟踪帧（PS 块匹配）"和"人工纠错 / 纠错后跟着重算"，
        容积曲线上也照旧画着纠错帧的橙点。
        """
        c = self.tl
        c.delete("all")
        c.create_text(4, 17, text="心拍", anchor="w", fill="#555", font=("Microsoft YaHei UI", 8))
        if self.frames is None:
            return
        x, _ = self._xy()
        if self.result:
            for j, b in enumerate(self.result["beats"]):
                c.create_rectangle(x(b["ed"]), 7, x(b["es"]), 27, fill="#f4c7c7" if j % 2 == 0 else "#c7d8f4", outline="")
                if not self._no_beat_ef():
                    c.create_text((x(b["ed"]) + x(b["es"])) / 2, 17, text="%.0f%%" % self._cal(b["ef"]), font=("Consolas", 8))
        self._draw_cursor()

    def _curve_click(self, px):
        if self.frames is None:
            return
        self._pause()
        a, b = self._span()
        f = min(1.0, max(0.0, (px - PLOT_L) / max(CW - PLOT_L - 10, 1)))
        self._goto(a + int(round(f * (b - 1 - a))))

    # ---- 播放 ----
    def toggle_play(self):
        if self.frames is None or (self.busy and self.live):
            return
        self.playing = not self.playing
        self.btn_play.configure(text="❚❚ 暂停" if self.playing else "▶ 播放")
        if self.playing:
            self._tick()

    def _pause(self):
        if self.playing:
            self.playing = False
            self.btn_play.configure(text="▶ 播放")

    def _tick(self):
        if not self.playing or self.frames is None:
            return
        a, b = self._span()
        t = self.cur + 1
        self._goto(t if a <= t < b else a)               # 选了分析片段：只在片段里循环
        self.frame.after(max(15, int(1000 / (self.fps or 30))), self._tick)

    def step(self, d):
        if self.frames is None:
            return
        self._pause()
        self._goto(self.cur + d)

    def key(self, name):
        """主窗口转来的快捷键。"""
        if name == "space":
            self.toggle_play()
        elif name == "left":
            self.step(-1)
        elif name == "right":
            self.step(1)
        elif name == "undo":
            self.undo_fix()

    # ================================================================ 点击纠错（7.5 节）
    def _click(self, e, positive):
        if self.res is None or self.frames is None:
            self.var_fix.set("先分析一段视频，再在帧上点击纠错")
            return
        if self.busy:
            self.var_fix.set("板子正忙，稍等")
            return
        if self.playing:
            self._pause()
            self.var_fix.set("已暂停；在这一帧上左键补漏、右键去多")
            return
        if self.m_inter is None or self.m_seq is None:
            self.var_fix.set("模型表里没有 echo_inter / echo_seq（上位机太旧）")
            return
        if self.app.busy:
            self.var_fix.set("板子正忙，稍等")
            return
        t = self.cur
        s = self.s or 1.0                                  # 画布是显示像素，点击坐标仍按 448 视图存
        x, y = min(max(e.x / s, 0), VIEW - 1), min(max(e.y / s, 0), VIEW - 1)
        clicks = self.clicks.get(t, []) + [(x, y, positive)]
        n = len(self.frames)
        end = t + 1                                        # 往后一直传到视频末尾；碰到医生纠过的帧就停（保留那次纠正）
        while end < n and self.fixed.get(end) != "fix":
            end += 1
        prior = self.masks.get(t)
        if prior is None:
            prior = self.np.zeros((N, N), bool)
        self._set_busy(True)
        try:
            self.var_fix.set("FPGA 重算第 %d 帧 …" % (t + 1))
            grad_k = GRAD_CLICK_K if GRAD_ON else None           # 界面不再给档位（11-18 用户要求删）：走默认 / 环境变量
            _log_line("[FIX] 开始 t=%d end=%d clicks=%d 保护=%s 影响=%d"
                      % (t, end, len(clicks), "%.1f 锥" % grad_k if grad_k is not None else "关", FIX_REPLAY_DEFAULT))
            threading.Thread(target=self._fix_worker,
                             args=(self._hostport(), t, end, prior, clicks, grad_k, FIX_REPLAY_DEFAULT),
                             daemon=True).start()
            self._watchdog(20.0, "点击纠错（第 %d 帧）" % (t + 1))
        except Exception:
            _log_exc("EchoView._click")                    # 起线程失败也不能把「忙」留在界面上
            self._set_busy(False)
            self.var_fix.set("纠错启动失败（详见 ws.log）")

    def _watchdog(self, secs, what):
        """慢任务看门狗：超过 secs 秒还 busy，就把所有线程的调用栈写进 ws.log。

        卡死时这是唯一的证据：能看出主线程是卡在某个函数里（栈非空）、还是轮询循环已经死了（栈在 mainloop）。
        """
        def run():
            t0 = time.time()
            while time.time() - t0 < 600:
                time.sleep(1.0)
                if not self.busy:
                    return
                if time.time() - t0 >= secs:
                    try:
                        import faulthandler
                        with open(os.path.join(PLD_DIR, "ws.log"), "a", encoding="utf-8") as f:
                            f.write("\n[%s] 看门狗：%s 超过 %.0f s 还没结束，线程栈如下\n"
                                    % (time.strftime("%Y-%m-%d %H:%M:%S"), what, secs))
                            f.flush()
                            faulthandler.dump_traceback(file=f, all_threads=True)
                            f.write("\n")
                    except Exception:
                        _log_exc("_watchdog")
                    return
        try:
            threading.Thread(target=run, daemon=True).start()
        except Exception:
            pass

    def _payload(self, t, prior, clicks=None):
        """128 档 RGB：R = 本帧（补边到 128），G = 先验掩膜 0 / 255，B = 点击锥形（128 = 无点击）。"""
        np = self.np
        rgb = np.zeros((128, 128, 3), np.uint8)
        rgb[PAD:PAD + N, PAD:PAD + N, 0] = self.frames[t]
        rgb[PAD:PAD + N, PAD:PAD + N, 1] = np.where(prior, 255, 0)
        if clicks:                                         # 448 视图坐标 -> 128 档 -> 512 画布（click_plane128 的口径）
            pl = sequence.click_plane128([((x / 4.0 + PAD) * 4, (y / 4.0 + PAD) * 4, p) for x, y, p in clicks])
            rgb[..., 2] = np.frombuffer(pl, np.uint8).reshape(128, 128)
        else:
            rgb[..., 2] = 128
        return rgb.tobytes()

    @staticmethod
    def _fix_round_trip(link, payload, model, flags, notify):
        """一次 v6c 往返；连接断了就重连，把**同一帧**重发（最多 3 次）。

        为什么要重发：板端 tcp_image_server 的 SO_RCVTIMEO 是 10 s —— 客户端只要静默
        超过 10 s（界面上重绘 / 采集抢 CPU 时会这样），服务端就断开连接，原来那一版
        直接抛 "peer closed after 0/64 bytes"，已经算好的几十帧全丢、整段白算。
        每一帧的输入都自带先验（G 通道），重发是安全的。
        """
        for attempt in (1, 2, 3):
            try:
                st, logit, _m, _ms = link.v6c_round_trip(
                    payload, 128, kind=proto.V6C_KIND_RGB, model0=model["index"],
                    thr=model["thr"], flags=flags)
                if st is not None and logit is not None:
                    return st, logit
                err = "板端没按 %d 号模型回结果" % model["index"]
            except Exception as exc:
                err = str(exc) or exc.__class__.__name__
            link.close()
            if attempt == 3:
                raise RuntimeError(err)
            notify(err)
            time.sleep(0.2 * attempt)
            link.connect()

    def _fix_worker(self, hp, t, end, prior, clicks, grad_k=None, replay=0):
        np, ws = self.np, self.ws
        self._gr_n = 0                                       # 梯度恢复补回来的像素数（给状态栏看）
        self._gr_all = {}                                    # 三档预算各自能补多少（挑档用）
        # 整段都包在 try 里：线程里任何一处抛异常都必须回到界面，否则 busy 永远挂着 = 界面卡死。
        # 2026-11-18 就踩过：下面那句裸的 sys.stderr.write 在 pythonw（启动 .bat 默认用它、没有控制台、
        # stderr 是 None）下直接 AttributeError，线程静默死掉，界面永远停在"FPGA 重算第 N 帧"。
        link = None
        try:
            # 独占一条连接（2026-11-18）：原来借 app.link —— 主窗口「处理」/ 快照 / 序列 /
            # 现场采集共用同一个 socket，纠错线程一 connect() 就把别人正在用的连接关掉，
            # 两条线程还各读各的 → 服务端收到的报文是乱的、10 s 收不到东西就断开。
            link = imagelink.ImageLink(hp[0], hp[1], timeout=25)
            _log_line("[FIX] 线程起 t=%d end=%d clicks=%d" % (t, end, len(clicks)))
            flags = proto.FLAG_STATS | proto.FLAG_C_LOGIT
            out = {}
            link.host, link.port = hp
            link.connect()
            mi, mq = self.m_inter, self.m_seq
            t0 = time.perf_counter()
            for k in range(t, end):
                use_click = (k - t) <= replay                    # 纠错影响：后续几帧把同一次点击重放给 3 号
                model = mi if use_click else mq
                if k % 10 == 0:
                    _log_line("[FIX] k=%d dt=%.1f s" % (k, time.perf_counter() - t0))
                pl = self._payload(k, prior if k == t else out[k - 1], clicks if use_click else None)
                st, logit = self._fix_round_trip(
                    link, pl, model, flags,
                    lambda msg: self.queue.put(("fix_prog", "第 %d 帧 %s，重连重发…" % (k + 1, msg))))
                if st is None or st.get("model") != model["index"] or logit is None:
                    raise RuntimeError("板端没按 %d 号模型回结果" % model["index"])
                _, raw = ws.mask_from_logit(logit)
                m, _ = ws.clean(raw) if raw.any() else (raw, 0)
                if k == t and GRAD_ON and grad_k is not None:   # 点击帧：按梯度把模型"多挖掉"的补回来
                    try:
                        tg = time.perf_counter()
                        m, self._gr_n, self._gr_all = grad_recover(self.frames[k], prior, m, clicks, click_k=grad_k)
                        _log_line("[FIX] grad_recover k=%s %.1f ms -> +%d px"
                                  % (grad_k, (time.perf_counter() - tg) * 1000.0, self._gr_n))
                    except Exception as exc:                 # 恢复是加菜，坏了也不能把整次纠错带塌
                        _log_exc("_fix_worker/grad_recover")
                out[k] = m
                if k == t:
                    ai_ms = st.get("ai_us", 0) / 1000.0
                elif (k - t) % 10 == 0:
                    self.queue.put(("fix_prog", "第 %d 帧已重算（FPGA %.1f ms），后面各帧跟着重算 %d / %d …"
                                    % (t + 1, ai_ms, k - t, end - t - 1)))
            link.close()
            _log_line("[FIX] 完成 %d 帧 用时 %.0f ms -> 交给界面" % (len(out), (time.perf_counter() - t0) * 1000.0))
            self.queue.put(("fixed", t, clicks, out, ai_ms, (time.perf_counter() - t0) * 1000.0))
        except Exception as e:
            try:
                if link is not None:
                    link.close()
            except Exception:
                pass
            _log_exc("_fix_worker")
            self.queue.put(("fix_err", "纠错失败：%s" % e))

    def _on_fixed(self, t, clicks, out, ai_ms, wall_ms):
        _log_line("[UI] _on_fixed 进 t=%d 帧数=%d" % (t, len(out)))
        self._set_busy(False)
        self.btn_rep.state(["!disabled"])
        before = dict(t=t, clicks=list(self.clicks.get(t, [])), masks={k: self.masks.get(k) for k in out},
                      vols={k: self.vols[k] for k in out}, fixed={k: self.fixed.get(k) for k in out})
        self.undo.append(before)
        self.clicks[t] = clicks
        for k, m in out.items():
            self.masks[k] = m
            self.vols[k] = self.ws.volume_pca(m)
            self.fixed[k] = "fix" if k == t else "prop"
        self.fix_ms.append(ai_ms)
        self._reanalyze()
        last = max(out)
        if len(out) == 1:
            how = "这是最后一帧，后面没有帧" if t == len(self.frames) - 1 else "下一帧是人工纠过的帧，保留那次纠正"
        elif last == len(self.frames) - 1:
            how = "后面 %d 帧跟着重算到视频末尾" % (len(out) - 1)
        else:
            how = "后面 %d 帧跟着重算（第 %d 帧是人工纠过的，保留）" % (len(out) - 1, last + 2)
        extra = []
        if getattr(self, "_gr_n", 0):
            extra.append("梯度恢复 %d 像素" % self._gr_n)
        note = ("，" + "，".join(extra)) if extra else ""
        self.var_fix.set("第 %d 帧：%d 个点击，FPGA 整网 %.1f ms%s；%s，共 %.0f ms。可撤销 / 重置"
                         % (t + 1, len(clicks), ai_ms, note, how, wall_ms))
        self._goto(t)
        self._redraw()
        _log_line("[UI] _on_fixed 出（界面已按要求刷新）")

    # ---- 分析片段（11-02）：只拿一段（如「放好后」）算 EF / 逐拍 / 临床测量，滚动条拖动选段 ----
    def _settled_from(self):
        """现场 v5.26 CAMUS 源：最后一次判定锁定（= 探头放好、灯定下来）的帧；没有则 None。"""
        ho = ((self.res or {}).get("rolling") or {}).get("hold") or {}
        locks = [e[0] for e in ho.get("events", []) if e[1] == 1]
        n = len(self.vols)
        return locks[-1] if locks and n - locks[-1] >= int(self.fps or 50) else None   # 至少 1 s 才算一段

    def _win_len_changed(self, redraw=True):
        if self.res is None or not self.vols:
            return
        n, sel = len(self.vols), self.var_winlen.get()
        if sel == WIN_LENS[0]:
            self.win = None
        elif sel == WIN_LENS[1]:
            a = self._settled_from()
            self.win = (a, n) if a is not None else None
            if a is None:
                self.var_winlen.set(WIN_LENS[0])
        else:
            L = min(n, int(round(float(sel.split()[0]) * self.fps)))
            a = self.win[0] if self.win else n - L     # 换长度时保留起点，第一次放在最后
            a = max(0, min(a, n - L))
            self.win = (a, a + L)
        self._apply_win(redraw)

    def _win_cmd(self, *args):
        """滚动条拖动 / 点两侧：平移分析片段（长度不变）。"""
        if self.res is None or not self.vols or self.busy:
            return
        n = len(self.vols)
        a, b = self.win if self.win else (0, n)
        L = b - a
        if args[0] == "moveto":
            a = int(round(float(args[1]) * n))
        elif args[0] == "scroll":
            a += int(args[1]) * (L if args[2] == "pages" else max(1, int(self.fps / 4)))
        a = max(0, min(a, n - L))
        if L >= n:
            self.win = None
        else:
            self.win = (a, a + L)
            if self.var_winlen.get() == WIN_LENS[1] and a != self._settled_from():
                self.var_winlen.set("%.1f s" % (L / self.fps))   # 拖离「放好后」的起点：长度保留，显示成秒数
        self._apply_win(True)

    def _apply_win(self, redraw):
        n = len(self.vols)
        a, b = self.win if self.win else (0, n)
        self.sb_win.set(a / max(n, 1), b / max(n, 1))
        self.var_win.set("全段 %.1f s" % (n / self.fps) if self.win is None else
                         "%.1f–%.1f s（%d 帧）" % (a / self.fps, b / self.fps, b - a))
        self._reanalyze()
        if self.frames is not None and not a <= self.cur < b:
            self._goto(a)                                # 光标挪进片段（放大后的曲线只画片段）
        if redraw:
            self._redraw()

    def _no_beat_ef(self):
        """v5.26 CAMUS 源：曲线 / 时间线上不标逐拍 EF（半个心动周期往返播放，逐拍 EF 没有意义）。"""
        return ((self.res or {}).get("rolling") or {}).get("chain", 0) in (1, 2)

    def _reanalyze(self):
        """纠错后 / 选了分析片段：逐拍分析与临床测量在上位机按当前容积重算（规则与板上相同，echo_ws.analyze /
        clinical；片段内的帧号换回原视频帧号）；没有纠错也没选片段时回到板上原始结果。"""
        ws, res = self.ws, self.res
        if not self.fixed and self.win is None:
            self.result = dict(ef=res["ef_raw"] if res["ef_raw"] is not None else float("nan"), fallback=res["fallback"],
                               hr=res["hr"], cycle_s=res["cycle_s"], beats=[dict(ed=b["ed"], es=b["es"], v_ed=b["v_ed"],
                                                                              v_es=b["v_es"], ef=b["ef"]) for b in res["beats"]])
            self.ef_now = self.ef_board
            self.clinical, self.clinical_where = res.get("clinical"), "板上计算"
        else:
            a, b = self.win if self.win else (0, len(self.vols))
            r = ws.analyze(self.vols[a:b], self.fps, 1, self.conf["cfg"])
            for bt in r["beats"]:
                bt["ed"] += a
                bt["es"] += a
            self.result = r
            self.ef_now = self._cal(r["ef"]) if r["ef"] == r["ef"] else None
            self.clinical = ws.clinical(r["beats"], self.fps, self.ef_now, self._cal, ws.clinical_conf(self.conf, "full")) \
                if self.ef_now is not None else None
            self.clinical_where = "上位机按同一规则重算：%s" % "，".join(
                ([("所选片段 %.1f–%.1f s" % (a / self.fps, b / self.fps))] if self.win else []) + (["纠错后"] if self.fixed else []))
        # 心率显示统一成「最近几拍瞬时心率平均」（11-19）：板上/重算给的 hr 是 60 / 中位 RR，
        # 拍数少时会在两个数之间跳。原始值留在 hr_board 里对照，报告与提示都用平滑后的值。
        self.result["hr_board"] = self.result.get("hr")
        if self.result["beats"]:
            self.result["hr_n"] = min(HR_RECENT, len(self.result["beats"]) - 1)
        hr = self._hr_recent(self.result["beats"])
        if hr is not None:
            self.result["hr"] = hr
        self.var_k_fix.set(("%d 处" % len(self.undo)) if self.undo else "—")
        self._show_results()

    def undo_fix(self):
        if self.busy or not self.undo:
            return
        b = self.undo.pop()
        for k, m in b["masks"].items():
            if m is None:
                self.masks.pop(k, None)
            else:
                self.masks[k] = m
            self.vols[k] = b["vols"][k]
            if b["fixed"][k] is None:
                self.fixed.pop(k, None)
            else:
                self.fixed[k] = b["fixed"][k]
        if b["clicks"]:
            self.clicks[b["t"]] = b["clicks"]
        else:
            self.clicks.pop(b["t"], None)
        self._reanalyze()
        self.var_fix.set("已撤销第 %d 帧的最后一次点击（还剩 %d 步可撤销）" % (b["t"] + 1, len(self.undo)))
        self._goto(b["t"])
        self._redraw()

    def reset_fix(self):
        if self.busy or self.orig is None or not self.undo:
            return
        self.masks = {t: m.copy() for t, m in self.orig["masks"].items()}
        self.vols = list(self.orig["vols"])
        self.clicks, self.undo, self.fixed = {}, [], {}
        self._reanalyze()
        self.var_fix.set("已重置：回到板上原始结果")
        self._goto(self.cur)
        self._redraw()

    # ================================================================ 自动定标（11-18 取代手动入口）+ 送到快照测量（三个模式打通）
    def _raw_key(self):
        """定标结果按原始录屏的文件名记在主窗口里（关键帧 / 现场两个视图共用：同一段录屏只定一次）。
        现场模式没有录屏文件（采集卡）时按信号源记，例：capture:video0 —— 同一台机器只定一次。"""
        if self.live:
            src = self.src or {}
            return src.get("video") or "%s:%s" % ("capture" if src.get("kind") == "v4l2" else "file",
                                                  src.get("name") or "?")
        return os.path.basename(self.rawvid["path"]) if self.rawvid else None

    def _spacing(self):
        """112 尺度上每像素多少毫米：原始画面 mm/像素 × 扇区方块边长 / 112（自检注入的 .y8 若放大过再除以放大倍数）。"""
        mm = self.app.calib.get(self._raw_key() or "")
        if mm is None:
            return None
        if self.live:
            st = (self.res or {}).get("stream") or {}
            # 采集还没结束时 res 为空：方块边长先用板上开流时回的那块整屏（v5.28）里的裁切信息
            side = ((st.get("crop") or {}).get("side")
                    or ((self.live_still or {}).get("crop") or {}).get("side")
                    or ((self.src or {}).get("truth") or (0, 0, 0))[2])
            return mm / float((self.src or {}).get("zoom") or 1.0) * side / N if side else None
        return mm * self.rawvid["crop"]["side"] / N

    def _show_scale(self):
        """工具栏那行：自动定标的结果，或者手动定标（11-19 起「标尺…」可以人工点两个刻度）。"""
        try:
            s = self._spacing()
        except Exception:
            s = None
        key = self._raw_key() or ""
        if s:
            self.var_scale.set("标尺 %.2f mm/像素（%s）" % (s, "手动" if self.app.calib_manual.get(key) else "自动"))
        elif key and key in self._calib_pending:
            self.var_scale.set("自动定标中 …")
        elif key:
            self.var_scale.set("未读到深度刻度：只报像素（可「标尺…」）")
        else:
            self.var_scale.set("本视频没有深度标尺：只报像素")

    # ---------------------------------------------------------------- 手动定标（11-19，三个模式都有）
    def _calib_image(self):
        """手动定标要用的那张**源分辨率**画面（PIL.Image）：关键帧 = 录屏原始首帧；现场 = 板上回的整屏 still。
        112 的 EchoNet 视频没有更高分辨率的画面，也没有深度刻度 -> None。"""
        from PIL import Image
        if self.live:
            st = self.live_still
            if st is None:
                return None
            g = self.np.frombuffer(st["luma"], self.np.uint8)[:st["H"] * st["W"]].reshape(st["H"], st["W"])
            return Image.fromarray(self.np.repeat(g[..., None], 3, 2))
        if self.rawvid is not None and self.rawvid.get("frame") is not None:
            return Image.fromarray(self.rawvid["frame"][..., ::-1].copy())     # read_video 的 BGR 原始首帧
        return None

    def _calib_set(self, mm_px, dist, cm):
        """手动定标的结果写进 app.calib（和自动定标同一个键），并让开着同一段素材的视图都刷新那行字。"""
        key = self._raw_key() or ""
        self.app.calib[key] = float(mm_px)
        self.app.calib_manual[key] = (float(cm), float(dist))
        self._calib_pending.pop(key, None)
        self.calib_auto = None
        for v in self.app.views.values():
            if hasattr(v, "_show_scale"):
                v._show_scale()
        self._show_scale()
        self._say("手动定标：两点相距 %.0f 像素 = %g cm → %.3f mm/像素" % (dist, cm, mm_px))

    def _calib_dialog(self):
        """「标尺…」：点两个深度刻度 + 填两点之间几厘米（默认 5）。"""
        img = self._calib_image()
        if img is None:
            self._say("手动定标要在有深度刻度的画面上做：%s"
                      % ("等开流后板上回整屏（先「开始采集」）" if self.live else "先「打开视频…」选一段录屏"))
            return
        CalibDialog(self.frame, "手动定标 — %s" % (self.name or ""), img, self._calib_set,
                    note="点两个**深度刻度**（超声机屏幕边上 1 cm 一格的小短线；通常是长刻度，间隔 5 cm 也可以，"
                         "下面把厘米数改掉就行）。点第三个点 = 重新开始。")

    def _calib_auto(self):
        """「自动」：清掉这一段的定标（含手动的），重新按深度刻度自动读一遍。"""
        key = self._raw_key() or ""
        self.app.calib.pop(key, None)
        self.app.calib_manual.pop(key, None)
        if self.live:
            if self.live_still is None:
                self._show_scale()
                self._say("自动定标用板上开流后回的那块整屏：先「开始采集」")
                return
            self._on_live_still(self.live_still)
        else:
            raw = self.rawvid or {}
            if raw.get("frame") is None and not raw.get("path"):
                self._show_scale()
                self._say("这段视频没有原始录屏（112 的 EchoNet 视频），屏上没有深度刻度")
                return
            self._auto_calibrate(raw.get("frame"), raw.get("path"), key)
        self._show_scale()

    def _auto_calibrate(self, frame=None, path=None, where=None):
        """自动定标：读超声机屏幕上的黄色深度刻度 -> app.calib[原始录屏文件名]。

        frame 给 BGR 首帧（关键帧模式已有），或只给 path（现场模式自己读第一帧）。整件事在后台线程里做：
        depth_ticks 要穷举刻度两两连线，几百毫秒到几秒，不能卡住界面。同一段录屏只定一次（现场 / 关键帧共用）。
        """
        key = where or ""
        if not key or (frame is None and not path):    # 没有原始录屏（EchoNet 112）就没什么可读的
            return
        if self.app.calib.get(key) is not None:
            return
        if key in self._calib_pending:
            return
        self._calib_pending[key] = time.time()

        def work():
            r = None
            try:
                if frame is not None:
                    r = depth_ticks(np.asarray(frame))
                if r is None and path:                      # 首帧读不到就再试第 15 / 30 帧（ES0003 就是这样）
                    import cv2
                    cap = cv2.VideoCapture(path)
                    for want in (15, 30):
                        cap.set(cv2.CAP_PROP_POS_FRAMES, want)
                        ok, fr = cap.read()
                        if ok:
                            r = depth_ticks(np.asarray(fr))
                            if r is not None:
                                break
                    cap.release()
            except Exception:
                _log_exc("_auto_calibrate")
                r = None
            self.queue.put(("scale", key, r, where))

        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception:
            _log_exc("_auto_calibrate/thread")
            self._calib_pending.pop(key, None)

    def _on_scale(self, key, r, where):
        """自动定标的结果回到界面线程：写进 app.calib，再让各视图刷新那行字。"""
        self._calib_pending.pop(key, None)
        if self.app.calib_manual.get(key):           # 用户已经手动定过标：自动结果不许覆盖
            self._show_scale()
            return
        if r is None:
            _log_line("[SCALE] %s：没读到深度刻度，只报像素" % where)
            self._show_scale()
            return
        (x0, y0), (x1, y1), cm, info = r
        d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if d < 5:
            _log_line("[SCALE] %s：两点只差 %.1f 像素，丢弃" % (where, d))
            self._show_scale()
            return
        mm_px = cm * 10.0 / d
        self.app.calib[key] = mm_px
        self.app.calib_manual.pop(key, None)          # 自动读到了就以自动为准（手动标记撤掉）
        self.calib_auto = info
        s = self._spacing()
        _log_line("[SCALE] %s：自动 %d 个刻度、共 %.0f cm，1 cm = %.1f px -> %.3f mm/px%s"
                  % (where, info["ticks"], cm, info["period"], mm_px,
                     "，112 尺度 %.2f mm/px" % s if s else ""))
        for v in self.app.views.values():             # 另一个视图开着同一段录屏时也用上
            if hasattr(v, "_show_scale"):
                v._show_scale()
        self._say("自动定标：%d 个深度刻度、1 cm = %.1f 像素，%.3f mm/像素" % (info["ticks"], info["period"], mm_px))

    def _send_snapshot(self):
        """把中位那一拍的 ED / ES 帧和板上的左室掩膜（含人工纠错）交给快照模式，按标尺做单平面 Simpson（mL）。"""
        if self.res is None or not self.result or not self.result["beats"] or self.busy:
            self._say("先分析出至少一个完整心动周期，再送到快照测量")
            return
        s = self._spacing()
        if s is None:
            key = self._raw_key() or ""
            t0 = self._calib_pending.get(key)
            if t0 is not None and time.time() - t0 < 20.0:   # 自动定标还在跑：等它落地再接着送（最多等 20 s）
                self._say("正在自动定标，稍等 …")
                self.frame.after(400, self._send_snapshot)
            else:
                self._say("这段视频没读到深度刻度（EchoNet 112 视频屏上没有标尺），给不出毫米尺度，不能送去测量")
            return
        beats = self.result["beats"]
        here = [b for b in beats if b["ed"] <= self.cur <= b["es"]] if not self.playing else []
        b = here[0] if here else sorted(beats, key=lambda b: b["ef"])[len(beats) // 2]   # 停在哪一拍就送哪一拍
        ed, es = b["ed"], b["es"]
        if self.masks.get(ed) is None or self.masks.get(es) is None:
            self._say("这一拍的掩膜不全，送不过去")
            return
        info = dict(patient=os.path.splitext(self.name)[0].strip(), ed_frame=ed, es_frame=es,
                    ed_img=self.frames[ed].copy(), es_img=self.frames[es].copy(),
                    ed_mask=self.masks[ed].copy(), es_mask=self.masks[es].copy(), spacing=(s, s),
                    mm_px=self.app.calib.get(self._raw_key()), ef_src=self.ef_now, ef_beat_raw=b["ef"],
                    source="现场模式" if self.live else "关键帧模式", data_note=self.data_note(),
                    corrected=bool(self.fixed), which="当前这一拍" if here else "中位那一拍")
        self._pause()
        self.app.switch("snapshot")
        self.app.views["snapshot"].accept_mono(info)

    # ================================================================ 证据报告
    def _report(self):
        if not self.result or self.res is None:
            return
        ws = self.ws
        r = self.result
        keys = []
        if r["beats"]:
            b = sorted(r["beats"], key=lambda b: b["ef"])[len(r["beats"]) // 2]
            for t, lab in ((b["ed"], "舒张末 ED（第 %d 帧）"), (b["es"], "收缩末 ES（第 %d 帧）")):
                m = self.masks.get(t)
                if m is not None:
                    keys.append((ws._png(ws.contour_rgb(self.frames[t], m, 3)), lab % (t + 1)))
        k = self.res["keyframe"]
        mode = self.live_info if self.live and self.live_info else \
            ("关键帧（PL 只分割关键帧，其余帧板上块匹配跟踪；板端 v5.21+）" if k["key_frac"] < 1 else "每帧推理（每帧都上 PL）")
        info = dict(result=r, fps=self.fps, name=self.name, nframes=len(self.frames), ef=self.ef_now, cal_beat=self._cal,
                    keyframes=keys, qc_level=self.qc_level, qc_reasons=self.qc_reasons,
                    clinical=self.clinical, clinical_where=self.clinical_where,
                    ref_ef=self.ref.get(self.name if not self.live else (self.src or {}).get("video") or "", (None,))[0],
                    curve=ws.curve_png([t / self.fps for t in range(len(self.vols))], self.vols, r["beats"], self.fps,
                                       self._cal),
                    model="%d 号 %s" % (self.model["index"], self.model["name"]), mode=mode,
                    n_analyzed=len(self.vols), ai_ms=self.stats["ai_ms"], wall=self.stats["wall"],
                    keyframe=k, quality=self._report_quality(),
                    interval=self.res.get("interval") if self.win is None and not self._no_beat_ef() else None,
                    data_note=self.data_note(), hints=getattr(self, "hints", None))
        if self.fixed:
            info["corrections"] = dict(n=len(self.undo), frames=sorted(t + 1 for t, v in self.fixed.items() if v == "fix"),
                                       prop=sorted(t + 1 for t, v in self.fixed.items() if v == "prop"),
                                       ef_board=self.ef_board, ef_now=self.ef_now, pl_ms=self.fix_ms,
                                       models="%d 号 %s / %d 号 %s" % (self.m_inter["index"], self.m_inter["name"],
                                                                     self.m_seq["index"], self.m_seq["name"]))
        if self.heart:
            figs = []
            rh = self.heart.get("right_heart") or {}
            for p in rh.get("pairs", [])[:1]:
                for t, lab in ((p["ed"], "舒张末（第 %d 帧）：左室红、右室紫"), (p["es"], "收缩末（第 %d 帧）：左室红、右室紫、右房橙")):
                    m = self.masks.get(t)
                    if m is not None:
                        rgb = ws.contour_rgb(self.frames[t], m, 3)
                        for item in self.rh_frames.get(t, []):
                            if item is not None:
                                ws.add_contour(rgb, item[0], 3, item[1])
                        figs.append((ws._png(rgb), lab % (t + 1)))
            info.update(heart=self.heart, heart_cfg=self.hc_cfg, heart_figs=figs)
        d = os.path.join(HERE, "reports")
        os.makedirs(d, exist_ok=True)
        path = ws.write_report(os.path.join(d, "echo_%s_%s.html" % (os.path.splitext(self.name)[0],
                                                                    time.strftime("%Y%m%d_%H%M%S"))), info)
        self.last_report = path
        self._say("证据报告已写到 %s" % path)
        if self.open_report:
            try:
                webbrowser.open("file:///" + path.replace("\\", "/"))
            except Exception:
                pass

    # ================================================================ 消息循环
    def _poll(self):
        # 11-19：**一次最多占主循环 POLL_BUDGET 秒**，剩下的消息下一轮再取。
        # 原来这个 while 一定要把队列掏空；现场模式引擎一口气把整段（1200 帧）推过来时，
        # 一趟就处理 1000 多条、实测**一次卡死 7.4 秒**（画面、按钮、停止键全无响应）。
        # 现在每趟只干 25 ms，剩下的 4 ms 后再来 —— 总吞吐几乎不变，但界面一直是活的。
        deadline = time.time() + POLL_BUDGET
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "loaded":
                    self._on_loaded(*item[1:])
                elif kind == "cfg":
                    self.kf_cfg, self.hc_cfg = item[1], item[2]
                    self.app.board_seen(item[3])
                elif kind == "rolltag":
                    self.roll_tag = item[1]
                    if self.roll_tag:
                        self.var_qwhy.set("扫查中：质量灯（%s）攒满 1 s 后出" % self.roll_tag)
                elif kind == "prog":
                    self._on_prog(item[1])
                elif kind == "lframe":
                    self._on_lframe(item[1])
                elif kind == "job":
                    self._on_job(*item[1:])
                elif kind == "fixed":
                    self._on_fixed(*item[1:])
                elif kind == "fix_prog":
                    self.var_fix.set(item[1])
                elif kind == "fix_err":
                    self._set_busy(False)
                    self.btn_rep.state(["!disabled"])
                    self.var_fix.set(item[1])
                elif kind == "scale":
                    self._on_scale(item[1], item[2], item[3])
                elif kind == "err":
                    self._set_busy(False)
                    msg = item[1]
                    if any(k in msg for k in ("peer closed", "timed out", "refused", "WinError")):
                        heartapp = sys.modules[__name__]                      # 代理 / 网线问题给一句提示
                        msg += heartapp.route_hint(self._hostport()[0])
                    self._say(msg)
                if time.time() >= deadline:              # 超预算：剩下的下一轮（4 ms 后）接着处理
                    break
        except queue.Empty:
            pass
        except Exception:
            # 以前这里只接 queue.Empty：任何一条消息处理出错都会让这个 after 循环**彻底断掉**
            # （Tk 只把异常打到 stderr），表现就是"界面卡死、按钮一直灰着"。现在记日志 + 清忙 + 继续转。
            _log_exc("EchoView._poll")
            try:
                self._set_busy(False)
                self.btn_rep.state(["!disabled"])
                self.var_fix.set("界面处理出错，已恢复可用（详见 ws.log）")
            except Exception:
                pass
        finally:
            if self.frame.winfo_exists():
                # WS_OPT_V1 现场模式每帧一个 live 块，30 ms 的轮询粒度会白等；
                # 有任务在途时 4 ms，空闲仍用 30 ms（不占 CPU）。
                self.frame.after(4 if self.busy else 30, self._poll)

    def close(self):
        self._stop()
        self._pause()


# ══════════════════════════════════════════════════════════════════════════
# 来源：imagelink.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""imagelink.py —— 与板子之间的 TCP 链路：发一帧、收一帧、量往返耗时。

计时口径（界面里显示的是"往返总耗时"）::

    t0 ──开始发送──► t1 ──发完──► 等板子处理并回传 ──► t2 ──收完整幅回图──►
    |<------ send_ms ------>|<------- reply_ms ------->|
    |<------------------ total_ms --------------------->|

命令行自测（不开界面就能对着板子验证收发与计时）::

    python imagelink.py 192.168.1.254 5060 bars
    python imagelink.py 192.168.1.254 5060 checker --repeat 5
    python imagelink.py 192.168.1.254 5060 bars --mode 3 --save out.png
"""

import os
import socket
import sys
import time



class LinkError(Exception):
    """链路层错误（连接不上、对端关闭、超时、协议不符）。"""


class ImageLink(object):
    def __init__(self, host, port, timeout=15.0):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self._sock = None
        self.pp_available = False     # 最近一帧紧凑回复说板端能做 PL 后处理（统计块 v8 + reserved bit2）

    # ---------------- 连接管理 ----------------
    @property
    def connected(self):
        return self._sock is not None

    def connect(self):
        self.close()
        self.pp_available = False     # 新连接可能是别的板端版本：先看到一帧 v8 回复再发 b10
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise LinkError("connect %s:%d failed: %s" % (self.host, self.port, e))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # 把收发缓冲开大, 避免 786 KB 的帧被 TCP 窗口反压
        for opt, val in ((socket.SO_RCVBUF, 1 << 20), (socket.SO_SNDBUF, 1 << 20)):
            try:
                s.setsockopt(socket.SOL_SOCKET, opt, val)
            except OSError:
                pass
        s.settimeout(self.timeout)
        self._sock = s
        return s

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _ensure(self):
        if self._sock is None:
            self.connect()
        return self._sock

    # ---------------- 收发 ----------------
    def _send_all(self, data):
        s = self._ensure()
        try:
            s.sendall(data)
        except OSError as e:
            raise LinkError("send failed: %s" % e)

    def _recv_exact(self, n):
        s = self._ensure()
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = s.recv(n - len(buf))
            except socket.timeout:
                raise LinkError("timeout waiting for %d bytes (got %d)" % (n, len(buf)))
            except OSError as e:
                raise LinkError("recv failed: %s" % e)
            if not chunk:
                raise LinkError("peer closed after %d/%d bytes" % (len(buf), n))
            buf += chunk
        return bytes(buf)

    # ---------------- 紧凑帧（v6c，实时化）----------------
    def send_v6c(self, payload, w, **kw):
        """只发不收；与 recv_v6c 配对使用。连续发几帧再收 = 流水线（板端顺序处理，TCP 缓冲让收和算重叠）。"""
        self._send_all(proto.pack_v6c(payload, w, **kw))

    def recv_v6c(self, flags, w, nextra=0):
        """收一帧紧凑回复 -> (ai_stats 或 None, int8 logit bytes 或 None, 位掩膜 bytes 或 None)。
        nextra > 0（多程序流水线）时 logit 是列表：[程序 0, 额外程序 1, ...]。"""
        st = logit = mask = None
        n = w * w
        if flags & proto.FLAG_STATS:
            st = proto.unpack_stats(self._recv_exact(proto.AI_STAT_BYTES))
            if proto.expects_reg_block(st):
                st["reg"] = proto.unpack_reg(self._recv_exact(proto.REG_BLOCK_BYTES))
        if flags & proto.FLAG_C_LOGIT:
            outs = []
            for _ in range(1 + nextra):
                data = self._recv_exact(n + 2)
                if proto.crc16_ccitt_false(data[:n]) != data[n] | (data[n + 1] << 8):
                    raise LinkError("v6c logit CRC mismatch")
                outs.append(data[:n])
            logit = outs if nextra else outs[0]
        if flags & proto.FLAG_C_MASK:
            data = self._recv_exact(n // 8 + 2)
            if proto.crc16_ccitt_false(data[:n // 8]) != data[n // 8] | (data[n // 8 + 1] << 8):
                raise LinkError("v6c mask CRC mismatch")
            mask = data[:n // 8]
        if flags & proto.FLAG_C_PP:              # PL 后处理块排在最后，挂在统计块的 "pp" 上
            pp = proto.unpack_pp(self._recv_exact(proto.PP_BLOCK_BYTES))
            pp["mask"] = None
            if pp["mask_bytes"]:
                data = self._recv_exact(pp["mask_bytes"] + 2)
                nb = pp["mask_bytes"]
                if proto.crc16_ccitt_false(data[:nb]) != data[nb] | (data[nb + 1] << 8):
                    raise LinkError("v6c pp mask CRC mismatch")
                pp["mask"] = data[:nb]
            st = st if st is not None else {}
            st["pp"] = pp
        if st is not None and "version" in st:
            self.pp_available = proto.stats_pp_available(st)
        return st, logit, mask

    def v6c_round_trip(self, payload, w, **kw):
        """发一帧紧凑帧并等回复 -> (ai_stats, logit, mask, total_ms)。"""
        flags = kw.get("flags")
        if flags is None:
            flags = kw["flags"] = proto.FLAG_STATS | proto.FLAG_C_LOGIT
        t0 = time.perf_counter()
        try:
            self.send_v6c(payload, w, **kw)
            st, logit, mask = self.recv_v6c(flags, w, len(kw.get("extra", ())))
        except LinkError:
            self.close()
            raise
        return st, logit, mask, (time.perf_counter() - t0) * 1000.0

    def send_frame(self, image, mode=0, flags=proto.FLAG_DEFAULT, aiparam=None,
                   thr=proto.CTRL_THR_DEFAULT, version=4, roi=None):
        """按协议把一整幅图发出去。

        整帧先在 Python 里拼好（CRC 走 C 实现）再**一次性 sendall** ——
        发送系统调用从 512 次降到 1 次，发一帧从 ~610 ms 降到几毫秒。

        `mode`   处理模式 0..5（v2/v3/v4 头都带）。
        `flags`  回传内容位图，只在 v3/v4 头里（见 proto.FLAG_*）。
        `aiparam` AI 参数（输入源 + 分辨率 + 阈值），只在 v3/v4 头里；None = 默认。
        `thr`    阈值分割(mode 1)的门限 0..255，**只在 v4/v5 头里**。
        `roi`    (x, y, w, h)：ROI 局部推理，发 v5 头（板端统计块 version >= 3 才认）。
        """
        self._send_all(proto.pack_stream(image, mode, flags=flags, aiparam=aiparam,
                                         thr=thr, version=version, roi=roi))

    def recv_stream(self):
        """一次读满一整帧字节流（789504 B）。

        用 `recv_into` 填一个预分配的大缓冲，而不是每包读两次 ——
        接收系统调用从 ~1024 次降到个位数。
        """
        s = self._ensure()
        need = proto.STREAM_TOTAL_BYTES
        buf = bytearray(need)
        mv = memoryview(buf)
        got = 0
        try:
            while got < need:
                n = s.recv_into(mv[got:], need - got)
                if n == 0:
                    raise LinkError("peer closed after %d/%d bytes" % (got, need))
                got += n
        except socket.timeout:
            raise LinkError("timeout: got %d/%d bytes" % (got, need))
        except OSError as e:
            raise LinkError("recv failed: %s" % e)
        return bytes(buf)

    def recv_frame(self):
        """收一整幅回图，返回 (image_bytes, stats)。

        stats = {'rows_ok', 'crc_errors', 'seq_errors'}
        """
        return proto.unpack_stream(self.recv_stream())

    def recv_ai_stats(self):
        """收 64 字节 AI 统计块（flags 带 FLAG_STATS 时，在图像流之前）。"""
        return proto.unpack_stats(self._recv_exact(proto.AI_STAT_BYTES))

    def round_trip(self, image, mode=0, flags=proto.FLAG_DEFAULT, aiparam=None,
                   thr=proto.CTRL_THR_DEFAULT, version=4, roi=None):
        """发一帧、按 flags 收回相应内容。

        返回 ``(images, timings, rowstats, ai_stats)``：

        * ``images``   —— ``{'proc'|'overlay'|'mask'|'logit': bytes}``，按 flags 可能是空的；
          要 logit 时请同时带 FLAG_STATS，否则遇到老板端会一直等到超时；
        * ``timings``  —— ``{'send_ms', 'reply_ms', 'total_ms'}``；
        * ``rowstats`` —— 每个图像流自己的 ``{'rows_ok','crc_errors','seq_errors'}``；
        * ``ai_stats`` —— flags 带 FLAG_STATS 时的统计块 dict，否则 None。

        ``thr`` 是阈值分割(mode 1)的门限，只在 v4/v5 控制头里带。
        ``roi`` = (x, y, w, h) 时走 ROI 局部推理（v5 头）。
        连接**保持复用**：板端一个连接上连收多帧。出错才断开，下次自动重连。
        """
        if not self.connected:
            self.connect()
        try:
            t0 = time.perf_counter()
            self.send_frame(image, mode, flags=flags, aiparam=aiparam,
                            thr=thr, version=version, roi=roi)
            t1 = time.perf_counter()

            ai_stats = None
            if flags & proto.FLAG_STATS:
                ai_stats = self.recv_ai_stats()
                if proto.expects_morph_block(flags, ai_stats):   # 形态指标块紧跟统计块
                    ai_stats["morph"] = proto.unpack_morph(self._recv_exact(proto.MORPH_BYTES))
                if proto.expects_reg_block(ai_stats):            # 分块求和块（F5）在形态块之后
                    ai_stats["reg"] = proto.unpack_reg(self._recv_exact(proto.REG_BLOCK_BYTES))

            images, rowstats = {}, {}
            for name in proto.expected_images(flags, ai_stats):
                img, st = self.recv_frame()
                images[name] = img
                rowstats[name] = st
            t2 = time.perf_counter()
        except Exception:
            self.close()
            raise

        timings = {
            "send_ms": (t1 - t0) * 1000.0,
            "reply_ms": (t2 - t1) * 1000.0,
            "total_ms": (t2 - t0) * 1000.0,
        }
        return images, timings, rowstats, ai_stats

# --------------------------------------------------------------------------
# 命令行自测
# --------------------------------------------------------------------------
_FLAG_ALIASES = {
    "proc": proto.FLAG_ECHO_PROC,
    "mask": proto.FLAG_ECHO_MASK,
    "overlay": proto.FLAG_ECHO_OVER,
    "over": proto.FLAG_ECHO_OVER,
    "stats": proto.FLAG_STATS,
    "logit": proto.FLAG_ECHO_LOGIT,
    "morph": proto.FLAG_MORPH,
    "reg": proto.FLAG_REG,
    "noimage": proto.FLAG_NO_IMAGE,
    "none": proto.FLAG_NO_IMAGE,
}


def _parse_flags(text):
    """接受 'proc,mask,stats' / 'none' / '0x0007' / '7' 三种写法。"""
    text = text.strip()
    if text.lower().startswith("0x") or text.isdigit():
        return int(text, 0) & 0xFFFF
    flags = 0
    for part in text.replace("+", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part not in _FLAG_ALIASES:
            raise ValueError("unknown flag %r; use one of %s"
                             % (part, sorted(set(_FLAG_ALIASES))))
        flags |= _FLAG_ALIASES[part]
    return flags


def _cli(argv):
    host = argv[0] if len(argv) > 0 else "192.168.1.254"
    port = int(argv[1]) if len(argv) > 1 else 5060
    pattern = argv[2] if len(argv) > 2 else "bars"
    repeat = 1
    mode = 0
    flags = proto.FLAG_DEFAULT
    ai_src = proto.AIPARAM_SRC_RAW
    ai_thr = proto.AIPARAM_THR_DEFAULT
    ai_res = 512
    thr_spec = "auto"          # "auto" 或 0..255
    version = 4
    saves = {}
    if "--repeat" in argv:
        repeat = int(argv[argv.index("--repeat") + 1])
    if "--mode" in argv:
        mode = int(argv[argv.index("--mode") + 1])
    if "--flags" in argv:
        flags = _parse_flags(argv[argv.index("--flags") + 1])
    if flags & (proto.FLAG_ECHO_LOGIT | proto.FLAG_MORPH):
        flags |= proto.FLAG_STATS          # 靠统计块的 version 判断板端认不认 logit / 形态
    if "--ai-src" in argv:
        ai_src = (proto.AIPARAM_SRC_PROC if argv[argv.index("--ai-src") + 1] == "proc"
                  else proto.AIPARAM_SRC_RAW)
    model = None                           # --model fundus|skin|echo_lv|<下标>：模型库（板端 v5）
    if "--model" in argv:
        spec = argv[argv.index("--model") + 1]
        hit = [m for m in proto.MODELS if m["name"] == spec or str(m["index"]) == spec]
        if not hit:
            print("--model must be one of %s" % ", ".join(m["name"] for m in proto.MODELS))
            return 2
        model = hit[0]["index"]
        ai_thr = hit[0]["thr"]             # 该模型的默认门限；--ai-thr 可再覆盖
    if "--ai-thr" in argv:
        ai_thr = int(argv[argv.index("--ai-thr") + 1])
    if "--ai-res" in argv:
        ai_res = int(argv[argv.index("--ai-res") + 1])
        if proto.res_to_shift(ai_res) is None:
            print("--ai-res must be one of %s" % (proto.AI_RES_CHOICES,))
            return 2
    if "--thr" in argv:
        thr_spec = argv[argv.index("--thr") + 1].strip()
    roi = None
    if "--roi" in argv:                    # --roi x,y,w,h：ROI 局部推理（v5 头）
        roi = tuple(int(v) for v in argv[argv.index("--roi") + 1].split(","))
        flags |= proto.FLAG_STATS          # 统计块里有板端实际计算的裁剪尺寸
    if "--v2" in argv:
        version = 2
    if "--v3" in argv:
        version = 3
    if "--save" in argv:
        saves[""] = argv[argv.index("--save") + 1]
    for name in ("proc", "mask", "overlay"):
        key = "--save-" + name
        if key in argv:
            saves[name] = argv[argv.index(key) + 1]

    if not (0 <= mode <= proto.CTRL_MODE_MAX):
        print("mode must be 0..%d: %s"
              % (proto.CTRL_MODE_MAX,
                 " / ".join("%d %s" % (k, proto.MODE_NAMES[k])
                            for k in sorted(proto.MODE_NAMES))))
        return 2

    aiparam = proto.make_aiparam(ai_src, ai_thr, ai_res, model)
    print("proto self-check: %s" % ("OK" if not proto.SELF_CHECK_ERRORS
                                    else "FAILED %s" % proto.SELF_CHECK_ERRORS))
    print("模式: %d (%s)   flags=0x%04X %s   aiparam=0x%04X (src=%s thr=%d res=%s model=%s)   头=v%d"
          % (mode, proto.MODE_NAMES.get(mode, "?"), flags,
             proto.expected_images(flags), aiparam,
             "proc" if ai_src == proto.AIPARAM_SRC_PROC else "raw", ai_thr,
             "default" if ai_res == 512 and model is not None else ai_res,
             "board default" if model is None else proto.MODELS[model]["name"], version))

    # 第三个参数既可以是内置图样名 / samples/ 里的默认测试图名(im0001~im0005),
    # 也可以是一个图片文件路径
    # (PNG/JPG/BMP/TIF/WebP 走 Pillow; PPM/PGM/PBM/PNM/PAM 与裸 RGB888 原生读)
    if os.path.isfile(pattern):
        try:
            image, note = imageio.load_image(pattern)
        except Exception as e:
            print("读图失败: %s" % e)
            return 2
        print("图像: %s —— %s" % (pattern, note))
    elif pattern in imageio.PATTERNS or pattern in imageio.SAMPLES:
        try:
            image, note = imageio.load_pattern(pattern)
        except Exception as e:
            print("读图失败: %s" % e)
            return 2
        print("图像: %s —— %s" % (pattern, note))
    else:
        print("既不是文件也不是已知图样: %r (可选 %s)"
              % (pattern, imageio.figure_names()))
        return 2
    print("上线总长 %d 字节"
          % {4: proto.WIRE_TOTAL_BYTES_V4, 3: proto.WIRE_TOTAL_BYTES_V3,
             2: proto.WIRE_TOTAL_BYTES}[version])

    # ---- 阈值分割的门限: auto = 按这张图算 Otsu ----
    if thr_spec.lower() == "auto":
        thr, _hist, thr_note = imageio.auto_threshold(image)
        print("阈值分割: auto -> %s" % thr_note)
    else:
        try:
            thr = int(thr_spec, 0)
        except ValueError:
            print("--thr 只能是 auto 或 0..255，收到 %r" % thr_spec)
            return 2
        if not (0 <= thr <= 255):
            print("--thr 越界: %d (0..255)" % thr)
            return 2
        print("阈值分割: %d (手动指定)" % thr)

    link = ImageLink(host, port)
    total = []
    last = None
    for i in range(repeat):
        try:
            images, tim, rowstats, ai_stats = link.round_trip(
                image, mode, flags=flags, aiparam=aiparam, thr=thr, version=version, roi=roi)
        except LinkError as e:
            print("[%d] LINK ERROR: %s" % (i + 1, e))
            return 1
        total.append(tim["total_ms"])
        last = images
        parts = " ".join("%s(rows_ok=%d)" % (n, rowstats[n]["rows_ok"])
                         for n in images)
        print("[%d] total=%7.1f ms  (send %6.1f + reply %6.1f)  %s"
              % (i + 1, tim["total_ms"], tim["send_ms"], tim["reply_ms"],
                 parts if parts else "(no image)"))
        if ai_stats is not None:
            print("      AI: %ux%u thr=%d vessels=%u/%u (%.1f%%) comps=%u "
                  "logit[%d..%d] ai_us=%u gray[%u..%u] mode=%u frame=%u"
                  % (ai_stats["ai_res"], ai_stats["ai_res"], ai_thr,
                     ai_stats["vessels"], ai_stats["total"], ai_stats["vessels_pct"],
                     ai_stats["components"], ai_stats["logit_min"],
                     ai_stats["logit_max"], ai_stats["ai_us"],
                     ai_stats["proc_min"], ai_stats["proc_max"],
                     ai_stats["mode"], ai_stats["frame_count"]))

    link.close()
    if total:
        print("summary: n=%d  min=%.1f  max=%.1f  avg=%.1f ms"
              % (len(total), min(total), max(total), sum(total) / len(total)))
    if last:
        if "" in saves:
            first = last[sorted(last)[0]]
            imageio.save_image(saves[""], first)
            print("save: %s -> %s" % (sorted(last)[0], saves[""]))
        for name, path in saves.items():
            if name and name in last:
                imageio.save_image(path, last[name])
                print("save: %s -> %s" % (name, path))
            elif name:
                print("save: no %r image in the reply (flags=0x%04X)" % (name, flags))
    print("CLI_DONE")
    return 0


# ══════════════════════════════════════════════════════════════════════════
# 来源：tile_engine.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""tile_engine.py —— 大图分块推理引擎（部分 C）：任意大小的图、任意框、任意放大倍数，
用固定的 512 PL U-Net 引擎分块算，拼起来与"整幅图一次推理"逐位相同。

这一层不碰界面（tkinter）也不碰网络细节，上位机窗口、板端网页 / DP 前端、验收脚本都调它（C3）。

概念
  原图    任意尺寸的 RGB（HRF 3504x2336、STARE 700x605……）。
  层      原图按比例 s 缩放（Pillow LANCZOS，与 imageio 拉伸 512 帧同一种滤波），右 / 下补黑到 8 的倍数。
          放大倍数 m：s = m * W1X / 原图宽——1 倍 = 模型尺度 = 原图宽缩到 W1X = 600 像素（眼底视野约占图宽 93%，
          STARE / HRF / DRIVE 都是，所以 ≈ 视野宽 560；由 STARE 尺度扫描定，见 E:/PLD/hrf/README.md）。
          层图预先在 PC 上做好放到板上 /data。
  框      层坐标里的 (x, y, w, h)。
  块      512 以内、起点 4 对齐、尺寸 8 的倍数的裁剪窗口；不贴图像边的那几条边各留 MARGIN = 24 像素真实邻域
          （ai/net/roi_exactness.py：裁剪边界的影响最深正好 22 像素）。每个块只取它的"自有区"（各块的自有区
          不重叠、正好铺满框），所以拼接结果 = 整图推理的结果。
  只要一块就够时（框 + 边距 <= 512），块就是老 ROI 的裁剪窗口（proto.roi_crop / 板端 ai_view_roi_crop
  同一规则）：512 帧上 1 倍框选 = 原来的 ROI 推理，逐位相同。

板端同一规划写在 tcp_image_rx/src/job_tile.c（tile_plan_axis），两份由 E:/PLD/hrf/c2_plan_compare.py 对照。
"""
import math

MARGIN = 24        # 每边真实邻域（理论需要 22）
TILE = 512         # PL 卷积核一行最多 512
ALIGN = 4          # 两级 2x2 池化：块起点 4 对齐
SIZE_ALIGN = 8     # 块尺寸 8 的倍数（最深层宽高仍是偶数）
STEP = TILE - 2 * MARGIN   # 多块时相邻块起点间距 464（8 的倍数，起点从 8 对齐开始就一直 8 对齐）
FOV_THR = 20       # 视野：(77R+150G+29B)>>8 > 20（= 板端 VM_FOV_THR）
OUTSIDE_LOGIT = -128

W1X = 600          # 1 倍 = 原图宽缩到 600 像素（STARE 尺度扫描的结果，见 hrf/README.md；改了要同步那里）


def ceil_to(v, a):
    return -(-v // a) * a


# ---------------------------------------------------------------------------------------------
# 规划
# ---------------------------------------------------------------------------------------------
def plan_axis(a, n, full, margin=MARGIN, tile=TILE):
    """一个方向：框 [a, a+n) 在 [0, full) 里（full 是 8 的倍数）-> [(块起点, 块长, 自有区起点, 自有区终点)]。
    一块够用时 = 老 ROI 规则（外扩 margin、起点向下对齐 4、长度向上取 8、越界回挪）；
    否则从 floor8(a - margin) 起每隔 STEP 放一块 512，最后一块只取需要的长度（越界回挪），自有区首尾相接。"""
    if not (0 <= a and n >= 1 and a + n <= full and full % SIZE_ALIGN == 0):
        raise ValueError("bad axis: a=%d n=%d full=%d" % (a, n, full))
    lo = max(0, a - margin)
    hi = min(full, a + n + margin)
    s = lo // ALIGN * ALIGN
    size = ceil_to(hi - s, SIZE_ALIGN)
    if size <= tile:
        if s + size > full:
            s = full - size
        return [(s, size, a, a + n)]
    out = []
    t = lo // SIZE_ALIGN * SIZE_ALIGN
    own0 = a
    while True:
        size = min(tile, ceil_to(hi - t, SIZE_ALIGN))
        if t + size > full:
            t = full - size
        core_end = t + size - (margin if t + size < full else 0)
        own1 = min(a + n, core_end)
        out.append((t, size, own0, own1))
        if own1 >= a + n:
            return out
        own0 = own1
        t += STEP


class Tile(object):
    __slots__ = ("index", "tx", "ty", "tw", "th", "ox", "oy", "ow", "oh", "skip", "score")

    def __init__(self, index, tx, ty, tw, th, ox, oy, ow, oh):
        self.index, self.tx, self.ty, self.tw, self.th = index, tx, ty, tw, th
        self.ox, self.oy, self.ow, self.oh = ox, oy, ow, oh
        self.skip = False
        self.score = 0.0

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self):
        return "Tile(%d: %d,%d %dx%d own %d,%d %dx%d%s)" % (self.index, self.tx, self.ty, self.tw, self.th,
                                                          self.ox, self.oy, self.ow, self.oh,
                                                          " skip" if self.skip else "")


def plan(box, level_w, level_h, margin=MARGIN, tile=TILE):
    """框 (x, y, w, h) -> [Tile]，行优先（板端同一顺序编号）。"""
    x, y, w, h = [int(v) for v in box]
    xs = plan_axis(x, w, level_w, margin, tile)
    ys = plan_axis(y, h, level_h, margin, tile)
    out = []
    for (ty, th, oy0, oy1) in ys:
        for (tx, tw, ox0, ox1) in xs:
            out.append(Tile(len(out), tx, ty, tw, th, ox0, oy0, ox1 - ox0, oy1 - oy0))
    return out


def fov_mask(rgb):
    """视野 = 亮度 (77R+150G+29B)>>8 > 20（板端 vm_fov_from_rgb 同一公式）。rgb: (h, w, 3) uint8。"""
    import numpy as np
    r, g, b = (rgb[..., i].astype(np.int32) for i in range(3))
    return ((77 * r + 150 * g + 29 * b) >> 8) > FOV_THR


def mark_fov_skip(tiles, level_rgb=None, fov=None):
    """自有区里一个视野像素都没有的块标记 skip（板端同一判据：按亮度现算）。返回跳过的块数。"""
    if fov is None:
        fov = fov_mask(level_rgb)
    n = 0
    for t in tiles:
        t.skip = not bool(fov[t.oy:t.oy + t.oh, t.ox:t.ox + t.ow].any())
        n += t.skip
    return n


def costs(tiles):
    """计算量账：算的像素、自有像素、halo 开销、有效像素比（跳过的块不算）。"""
    comp = sum(t.tw * t.th for t in tiles if not t.skip)
    own = sum(t.ow * t.oh for t in tiles if not t.skip)
    own_all = sum(t.ow * t.oh for t in tiles)
    return dict(tiles=len(tiles), run=sum(1 for t in tiles if not t.skip),
                skipped=sum(1 for t in tiles if t.skip), computed_px=comp, own_px=own, box_px=own_all,
                halo_overhead=(comp / own - 1.0) if own else 0.0, useful_ratio=(own / comp) if comp else 0.0)


# ---------------------------------------------------------------------------------------------
# 拼接
# ---------------------------------------------------------------------------------------------
def tile_rgb(level_rgb, t):
    return level_rgb[t.ty:t.ty + t.th, t.tx:t.tx + t.tw]


def own_of(t, tile_logits):
    """块的整幅输出 (th, tw) -> 自有区 (oh, ow)。"""
    return tile_logits[t.oy - t.ty:t.oy - t.ty + t.oh, t.ox - t.tx:t.ox - t.tx + t.ow]


def stitch(box, tiles, owns):
    """owns[i] = 第 i 块自有区的 logit（或 None = 跳过）-> 框内 (h, w) int8，跳过处 = -128。"""
    import numpy as np
    x, y, w, h = box
    out = np.full((h, w), OUTSIDE_LOGIT, np.int8)
    for t in tiles:
        o = owns[t.index]
        if o is None:
            continue
        out[t.oy - y:t.oy - y + t.oh, t.ox - x:t.ox - x + t.ow] = o
    return out


def run_tiles(level_rgb, box, runner, skip_fov=True, order=None, on_tile=None):
    """在 PC 上按同一规划跑（runner(rgb_tile) -> int8 logits，可以是 NumPy 逐位参考或别的）。
    返回 (框内 logit, tiles)。on_tile(tile, own_logits) 每算完一块调一次（逐块填热图）。"""
    H, W = level_rgb.shape[:2]
    tiles = plan(box, W, H)
    if skip_fov:
        mark_fov_skip(tiles, level_rgb)
    owns = [None] * len(tiles)
    for i in (order if order is not None else range(len(tiles))):
        t = tiles[i]
        if t.skip:
            continue
        owns[i] = own_of(t, runner(tile_rgb(level_rgb, t)))
        if on_tile is not None:
            on_tile(t, owns[i])
    return stitch(box, tiles, owns), tiles


# ---------------------------------------------------------------------------------------------
# 层（金字塔）
# ---------------------------------------------------------------------------------------------
def fov_width(rgb_or_fov):
    """原图视野的像素宽（列方向上有视野像素的跨度）。"""
    import numpy as np
    f = rgb_or_fov if rgb_or_fov.dtype == bool else fov_mask(rgb_or_fov)
    cols = np.nonzero(f.any(axis=0))[0]
    return int(cols[-1] - cols[0] + 1) if cols.size else f.shape[1]


def scale_for_mag(mag, width0, w1x=W1X):
    """放大 mag 倍时的缩放比例（width0 = 原图宽）。"""
    return float(mag) * w1x / float(width0)


def mag_of_scale(s, width0, w1x=W1X):
    return s * float(width0) / w1x


def mag_cap(width0, cap, w1x=W1X):
    """这张图能"重新推理"的最大倍数：模型上限 cap（HRF 训练集的 Dice 曲线）和原图分辨率（层不能比原图大——
    再大就是把原图插值放大后送进网络，没有新的真实像素）取小。"""
    return min(float(cap), float(width0) / w1x)


def effective_mag(mag, width0, cap, w1x=W1X):
    """选的倍数 -> (推理倍数, 显示放大倍数)。超过 mag_cap：按上限推理，其余只插值放大显示。"""
    c = mag_cap(width0, cap, w1x)
    return (float(mag), 1.0) if mag <= c + 1e-9 else (c, float(mag) / c)


def level_size(w0, h0, s):
    """缩放后的尺寸（四舍五入）和补到 8 的倍数后的层尺寸。"""
    lw, lh = max(8, int(round(w0 * s))), max(8, int(round(h0 * s)))
    return (lw, lh), (ceil_to(lw, SIZE_ALIGN), ceil_to(lh, SIZE_ALIGN))


def make_level(rgb, s):
    """原图 (h0, w0, 3) uint8 按 s 缩放（LANCZOS）+ 右 / 下补黑到 8 的倍数 -> (层图, (有效宽, 有效高))。"""
    import numpy as np
    from PIL import Image
    h0, w0 = rgb.shape[:2]
    (lw, lh), (W, H) = level_size(w0, h0, s)
    im = Image.fromarray(np.ascontiguousarray(rgb, np.uint8))
    if (lw, lh) != (w0, h0):
        im = im.resize((lw, lh), Image.LANCZOS)
    out = np.zeros((H, W, 3), np.uint8)
    out[:lh, :lw] = np.asarray(im)
    return out, (lw, lh)


def box_to_level(box0, s, level_w, level_h):
    """原图坐标的框 -> 层坐标（向外取整，夹在层内）。"""
    x0, y0, w0, h0 = box0
    x = max(0, int(math.floor(x0 * s)))
    y = max(0, int(math.floor(y0 * s)))
    x1 = min(level_w, int(math.ceil((x0 + w0) * s)))
    y1 = min(level_h, int(math.ceil((y0 + h0) * s)))
    return x, y, max(1, x1 - x), max(1, y1 - y)


def box_to_orig(box, s):
    x, y, w, h = box
    return x / s, y / s, w / s, h / s


# ---------------------------------------------------------------------------------------------
# 按内容建议放大区域（总览的 logit 上算）
# ---------------------------------------------------------------------------------------------
def tile_scores(overview_logits, fov, thr, band, grid):
    """总览 logit 切成 grid x grid 像素的格子 -> 每格 (不确定像素比, 血管像素比, 视野像素比)。
    不确定 = |logit - thr| <= band（与 flags b5 置信度同一定义）。只数视野内。"""
    import numpy as np
    H, W = overview_logits.shape
    q = overview_logits.astype(np.int16)
    unsure = (np.abs(q - thr) <= band) & fov
    vessel = (q >= thr) & fov
    gy, gx = -(-H // grid), -(-W // grid)
    out = np.zeros((gy, gx, 3))
    for j in range(gy):
        for i in range(gx):
            sl = (slice(j * grid, (j + 1) * grid), slice(i * grid, (i + 1) * grid))
            n = fov[sl].sum()
            if n:
                out[j, i] = (unsure[sl].sum() / n, vessel[sl].sum() / n, n / fov[sl].size)
    return out


def suggest(overview_logits, fov, thr, band, grid=96, k=5, min_fov=0.5):
    """建议精查的区域：完整的、视野占比 >= min_fov 的格子按不确定像素比从高到低取 k 个（总览层坐标的框）。"""
    sc = tile_scores(overview_logits, fov, thr, band, grid)
    H, W = overview_logits.shape
    cand = [(sc[j, i, 0], (i * grid, j * grid, grid, grid))
            for j in range(sc.shape[0]) for i in range(sc.shape[1])
            if sc[j, i, 2] >= min_fov and (i + 1) * grid <= W and (j + 1) * grid <= H]
    cand.sort(key=lambda c: -c[0])
    H, W = overview_logits.shape
    return [(u, (x, y, min(grid, W - x), min(grid, H - y))) for u, (x, y, _, _) in cand[:k]]


def order_by_uncertainty(tiles, level_w, level_h, overview_logits, overview_fov, thr, band):
    """块的计算顺序：把每块自有区映射回总览层，按不确定像素比从高到低（跳过的块排最后）。返回块下标列表。"""
    import numpy as np
    oh, ow = overview_logits.shape
    fy, fx = oh / float(level_h), ow / float(level_w)
    q = overview_logits.astype(np.int16)
    unsure = (np.abs(q - thr) <= band) & overview_fov
    for t in tiles:
        y0, y1 = int(t.oy * fy), max(int(t.oy * fy) + 1, int(math.ceil((t.oy + t.oh) * fy)))
        x0, x1 = int(t.ox * fx), max(int(t.ox * fx) + 1, int(math.ceil((t.ox + t.ow) * fx)))
        n = overview_fov[y0:y1, x0:x1].sum()
        t.score = float(unsure[y0:y1, x0:x1].sum()) / n if n else -1.0
    return sorted(range(len(tiles)), key=lambda i: (tiles[i].skip, -tiles[i].score, i))


# ---------------------------------------------------------------------------------------------
# 板端病例任务 JOB_TILE（服务端 v5.20 起，src/job_tile.c）
# ---------------------------------------------------------------------------------------------
JOB_TILE = 8
TILE_LOGITS, TILE_MASK, TILE_OVERLAY, TILE_NO_SKIP, TILE_PS_POST = 1, 2, 4, 8, 16
TILE_HDR = 72
LEVEL_MAGIC = 0x4C444C50          # "PLDL"
LEVEL_HDR = 32
UNSURE_RGB = (255, 200, 0)        # 不确定带的颜色（= jobs.overlay 的默认值）


def level_file_bytes(level_rgb):
    """板上 /data 里的层文件：32 字节头（"PLDL"、版本 1、宽、高、3）+ RGB 行。"""
    import struct
    import numpy as np
    H, W = level_rgb.shape[:2]
    return struct.pack("<5I12x", LEVEL_MAGIC, 1, W, H, 3) + np.ascontiguousarray(level_rgb, np.uint8).tobytes()


def union_rect(tiles):
    x0 = min(t.tx for t in tiles)
    y0 = min(t.ty for t in tiles)
    x1 = max(t.tx + t.tw for t in tiles)
    y1 = max(t.ty + t.th for t in tiles)
    return x0, y0, x1 - x0, y1 - y0


def tile_request(level_w, level_h, box, model=0, thr=-3, band=0, region=None, path=None, order=None):
    """JOB_TILE 的参数。region = 上传的层区域 (x, y, w, h)（path 为 None 时）；path = 板上层文件（/data/...）。"""
    import struct
    order = list(order or [])
    pb = path.encode("utf-8") if path else b""
    rx, ry, rw, rh = region if region is not None else (0, 0, 0, 0)
    p = struct.pack("<II ii II iiii iiii II", 1 if path else 0, model, thr, band, level_w, level_h,
                    *[int(v) for v in box], rx, ry, rw, rh, len(order), len(pb))
    return p + struct.pack("<%dH" % len(order), *order) + pb


def mask_row_bytes(ow16):
    return (ow16 + 127) // 128 * 16


def parse_tile_blob(d, flags):
    """"tile" 块 -> dict（头字段 + logits (oh, ow) int8 / mask (oh, ow) bool / overlay (oh, ow, 3) uint8）。"""
    import struct
    import numpy as np
    keys = ("index", "pos", "tx", "ty", "tw", "th", "ox", "oy", "ow", "oh", "state", "run_us", "crop_us",
            "post_us", "ovl_us", "vessels", "comps", "reserved")
    v = dict(zip(keys, struct.unpack_from("<2I8i8I", d, 0)))
    v["skip"] = bool(v["state"] & 1)
    ow, oh, off = v["ow"], v["oh"], TILE_HDR
    if not v["skip"]:
        if flags & TILE_LOGITS:
            v["logits"] = np.frombuffer(d, np.int8, ow * oh, off).reshape(oh, ow)
            off += ow * oh
        if flags & TILE_MASK:
            mrow = mask_row_bytes(ceil_to(ow, 16))
            bits = np.frombuffer(d, np.uint8, oh * mrow, off).reshape(oh, mrow)
            v["mask"] = np.unpackbits(bits, axis=1, bitorder="little")[:, :ow].astype(bool)
            off += oh * mrow
        if flags & TILE_OVERLAY:
            v["overlay"] = np.frombuffer(d, np.uint8, ow * oh * 3, off).reshape(oh, ow, 3)
            off += ow * oh * 3
    if off != len(d):
        raise ValueError("tile blob: %d bytes, expected %d" % (len(d), off))
    return v


def run_board(link, level_w, level_h, box, flags=TILE_LOGITS, level_rgb=None, path=None, order=None, model=0,
              thr=-3, band=0, on_tile=None, on_progress=None):
    """板上分块推理一个框。像素来源：path（板上 /data 的层文件，板子只裁剪）或 level_rgb（上传需要的区域）。
    返回 dict(result, tiles=[解析后的块], logits / mask / overlay = 拼好的框（按 flags；跳过的块 = -128 / False / 黑），
    upload_s, wall_s)。
    on_tile(块 dict) 每收到一块调一次（逐块填热图）。"""
    import numpy as np
    jobs = sys.modules[__name__]
    tiles = plan(box, level_w, level_h)
    region = None
    data = b""
    if path is None:
        region = union_rect(tiles)
        rx, ry, rw, rh = region
        data = np.ascontiguousarray(level_rgb[ry:ry + rh, rx:rx + rw], np.uint8).tobytes()
    params = tile_request(level_w, level_h, box, model, thr, band, region, path, order)
    got = []
    link.close()          # 板端会断开空闲的连接（看图、框选可能很久）：每个任务新连一次，几毫秒

    def blob(name, d):
        if name != "tile":
            return False
        t = parse_tile_blob(d, flags)
        got.append(t)
        if on_tile is not None:
            on_tile(t)
        return True
    r = jobs.run(link, JOB_TILE, flags, params, data, on_progress, on_blob=blob)
    res = r["result"]
    out = dict(result=res, tiles=sorted(got, key=lambda t: t["index"]), upload_s=r["upload_s"], wall_s=r["wall_s"],
               plan=tiles)
    if not res.get("ok"):
        return out
    x, y, w, h = box
    for key, flag, fill, shape in (("logits", TILE_LOGITS, OUTSIDE_LOGIT, (h, w)), ("mask", TILE_MASK, False, (h, w)),
                                   ("overlay", TILE_OVERLAY, 0, (h, w, 3))):
        if not flags & flag:
            continue
        a = np.full(shape, fill, dict(logits=np.int8, mask=bool, overlay=np.uint8)[key])
        for t in got:
            if key in t:
                a[t["oy"] - y:t["oy"] - y + t["oh"], t["ox"] - x:t["ox"] - x + t["ow"]] = t[key]
        out[key] = a
    return out


def overlay_ref(base_rgb, logits, thr, band=0):
    """板上叠加（vis_layer 或 PS，alpha 16）的参照：血管（logit >= thr）涂红，|logit - thr| <= band 涂不确定色。
    = ai_view_overlay_rgb（band = 0）/ visref.overlay。"""
    import numpy as np
    out = np.array(base_rgb, np.uint8, copy=True)
    q = logits.astype(np.int16)
    out[q >= thr] = (255, 0, 0)
    if band > 0:
        out[np.abs(q - thr) <= band] = UNSURE_RGB
    return out


# ══════════════════════════════════════════════════════════════════════════
# 来源：bigwin.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""bigwin.py —— 部分 C「大图精查」窗口：任意大小的眼底图，总览 + 框选放大，板上分块推理（病例任务 JOB_TILE，服务端 v5.20 起）。

用法（界面）
  1. 打开就先算"总览"：整幅图按 1 倍（模型尺度 = 图宽缩到 600，tile_engine.W1X）分 1–4 块在 PL 上推理，左边显示叠加，
     半透明格子 = 每 64 像素格的不确定像素比（|logit − 门限| <= 带宽，与 b5 置信度同一定义），编号虚线框 = 建议精查的区域。
  2. 在总览上拖一个任意大小的框（或点建议框），选放大倍数，点「重新推理」：框映射到该倍数的层（原图按比例缩放），
     自动切 512 块（每边 24 像素真实邻域），按不确定度从高到低逐块在 PL 上算、逐块填进右边；视野外的块跳过。
     拼接结果与"该倍数整幅图一次推理"逐位相同（E:/PLD/hrf/c1_exactness.py）。
  3. 「显示放大」只是插值放大右边的图（不重新推理）；超过放大上限的倍数只给显示放大。
层图来源：板上 /data 有这张图的预制层（tile_pyramid.json）就让板子只裁剪；没有就从 PC 上传需要的区域。
措辞：测量辅助（血管密度、形态指标），不作诊断。
"""
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk


CONFIG = os.path.join(HERE, "tile_config.json")
OV_W = 600            # 总览画布宽 = 1 倍层宽
ZOOM_W, ZOOM_H = 640, 520
NOTE = ("测量辅助：血管分割与形态指标仅供工程演示与复核，不作诊断。分块拼接 = 该倍数整幅一次推理（逐位相同）；"
        "「显示放大」只是插值，不产生新信息。")


def load_config():
    cfg = dict(mags=[1, 1.5, 2, 3, 4], cap=4, thr=-3, band=8, pyramid=None, cap_note="")
    try:
        cfg.update(json.load(open(CONFIG, encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def rgb_key(rgb):
    import hashlib
    return hashlib.sha256(np.ascontiguousarray(rgb, np.uint8).tobytes()).hexdigest()


def load_pyramid(cfg):
    p = cfg.get("pyramid")
    if not p:
        return {}
    if not os.path.isabs(p):
        p = os.path.join(HERE, p)
    try:
        return json.load(open(p, encoding="utf-8")).get("cases", {})
    except (OSError, ValueError):
        return {}


def heat_lut():
    """logit -> 颜色（蓝 = 很确定不是、红 = 很确定是、黄绿 = 门限附近），只用于显示。"""
    lut = np.zeros((256, 3), np.uint8)
    for i in range(256):
        t = i / 255.0
        lut[i] = (int(255 * min(1, max(0, 1.5 - abs(4 * t - 3)))), int(255 * min(1, max(0, 1.5 - abs(4 * t - 2)))),
                  int(255 * min(1, max(0, 1.5 - abs(4 * t - 1)))))
    return lut


LUT = heat_lut()


class BigWindow(object):
    def __init__(self, app, rgb0, name, box0=None, mag=None):
        self.app = app
        self.cfg = load_config()
        self.rgb0 = np.ascontiguousarray(rgb0, np.uint8)
        self.name = name
        self.H0, self.W0 = self.rgb0.shape[:2]
        self.key = rgb_key(self.rgb0)
        self.cap = te.mag_cap(self.W0, self.cfg["cap"])          # 这张图能重新推理的最大倍数
        self.m_ov = min(1.0, self.cap)                           # 总览倍数（原图比 1 倍还小时就用原图）
        self.case = load_pyramid(self.cfg).get(self.key)
        self.levels = {}
        self.overview = None              # dict(logits, overlay, fov, out)
        self.zoom = None                  # 当前放大视图的结果
        self.box1 = None                  # 总览（1 倍层）坐标里的框
        self.q = queue.Queue()
        self.photo_l = self.photo_r = None
        self.drag0 = None
        self.running = False              # 有板上任务在途（关窗时要等它回来，见 uistyle.close_when_idle）
        self.top = tk.Toplevel(app.root)
        self.top.title("大图精查 — %s（%d×%d）" % (name, self.W0, self.H0))
        self._build()
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.pending = (box0, mag)
        self.top.after(100, self.run_overview)
        self.top.after(50, self._poll)

    # ------------------------------------------------------------------ 界面
    def _build(self):
        t = uistyle.window(self.top, None)
        tools = uistyle.topbar(t, "大图精查", "总览 + 框选放大 · 512 分块在 PL 上推理，拼接与整幅推理逐位相同")
        uistyle.label(tools, "放大倍数", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 6))
        self.mag_labels = self._mag_labels()
        dm = float(self.cfg.get("default_mag", 2))
        self.var_mag = tk.StringVar(value=next((l for l in self.mag_labels if float(l.split("×")[0]) == dm),
                                               self.mag_labels[0]))
        cb = ttk.Combobox(tools, textvariable=self.var_mag, values=self.mag_labels, width=22, state="readonly",
                          font=FONT)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._box_status())
        self.btn_run = uistyle.primary_button(tools, "重新推理 ▶", self.run_zoom, width=10)
        self.btn_run.pack(side="left", padx=(10, 0), pady=8)
        uistyle.vsep(tools)
        uistyle.label(tools, "显示", fg=uistyle.C_MUTED).pack(side="left", padx=(0, 6))
        self.var_view = tk.StringVar(value="叠加（板上 vis_layer）")
        self.mask_txt = mask_text(getattr(self.app, "program", None))   # 皮肤镜 = 皮肤镜掩膜
        cb = ttk.Combobox(tools, textvariable=self.var_view, width=18, state="readonly", font=FONT,
                          values=["叠加（板上 vis_layer）", self.mask_txt, "置信度热图", "不确定区域", "原图"])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.render())
        uistyle.label(tools, "显示放大（仅插值）", fg=uistyle.C_MUTED).pack(side="left", padx=(12, 6))
        self.var_disp = tk.StringVar(value="适应窗口")
        cb = ttk.Combobox(tools, textvariable=self.var_disp, width=8, state="readonly", font=FONT,
                          values=["适应窗口", "1×", "2×", "4×", "8×"])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.render_zoom())

        mid = tk.Frame(t, bg=uistyle.C_BG)
        mid.grid(row=1, column=0, sticky="nsew", padx=12, pady=(12, 0))
        t.grid_rowconfigure(1, weight=1)
        t.grid_columnconfigure(0, weight=1)
        mid.grid_rowconfigure(0, weight=1)
        mid.grid_columnconfigure(1, weight=1)                # 窗口拉大时多出来的地方给放大视图
        lp, lb = uistyle.panel(mid, "总览（1 倍 = 模型尺度）", "拖框选区域　点建议框　双击 / 回车 = 重新推理")
        lp.grid(row=0, column=0, sticky="n", padx=(0, 12))
        self.ov_h = int(round(self.H0 * OV_W / float(self.W0)))
        self.cv_l = tk.Canvas(lb, width=OV_W, height=self.ov_h, bg=uistyle.C_EMPTY, bd=0, highlightthickness=0,
                              cursor="crosshair")
        self.cv_l.pack()
        self.cv_l_img = self.cv_l.create_image(0, 0, anchor="nw")
        self.cv_l.bind("<ButtonPress-1>", self._press)
        self.cv_l.bind("<B1-Motion>", self._drag)
        self.cv_l.bind("<ButtonRelease-1>", self._release)
        self.cv_l.bind("<Double-Button-1>", lambda e: self.run_zoom())
        self.top.bind("<Return>", lambda e: self.run_zoom())
        row = tk.Frame(lb, bg=uistyle.C_CARD)
        row.pack(fill="x", pady=(6, 0))
        self.var_heat = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="不确定度格子", variable=self.var_heat, style="Card.TCheckbutton",
                        command=self.render_overview).pack(side="left")
        self.var_sugg = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="建议框", variable=self.var_sugg, style="Card.TCheckbutton",
                        command=self.render_overview).pack(side="left", padx=(12, 0))
        self.var_ovnote = tk.StringVar(value="")
        uistyle.label(lb, var=self.var_ovnote, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x", pady=(4, 0))

        self.var_rtitle = tk.StringVar(value="放大视图")
        rp, rb = uistyle.panel(mid, self.var_rtitle, "逐块填入：按不确定度从高到低；左键拖动平移")
        rp.grid(row=0, column=1, sticky="nsew")
        fr = tk.Frame(rb, bg=uistyle.C_CARD)
        fr.pack(fill="both", expand=True)
        fr.grid_rowconfigure(0, weight=1)
        fr.grid_columnconfigure(0, weight=1)
        self.cv_r = tk.Canvas(fr, width=ZOOM_W, height=ZOOM_H, bg=uistyle.C_EMPTY, bd=0, highlightthickness=0,
                              cursor="fleur")
        self.sx = ttk.Scrollbar(fr, orient="horizontal", command=self.cv_r.xview)
        self.sy = ttk.Scrollbar(fr, orient="vertical", command=self.cv_r.yview)
        self.cv_r.configure(xscrollcommand=self.sx.set, yscrollcommand=self.sy.set)
        self.cv_r.grid(row=0, column=0, sticky="nsew")
        self.sy.grid(row=0, column=1, sticky="ns")
        self.sx.grid(row=1, column=0, sticky="ew")
        self.cv_r_img = self.cv_r.create_image(0, 0, anchor="nw")
        self.cv_r.bind("<ButtonPress-1>", lambda e: self.cv_r.scan_mark(e.x, e.y))
        self.cv_r.bind("<B1-Motion>", lambda e: self.cv_r.scan_dragto(e.x, e.y, gain=1))
        self.cv_r.bind("<MouseWheel>", lambda e: self.cv_r.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.cv_r.bind("<Shift-MouseWheel>", lambda e: self.cv_r.xview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.cv_r.bind("<Configure>", lambda e: self._on_zoom_resize())
        self.resize_job = None
        self.var_zoomnote = tk.StringVar(value="在左边拖框（或点虚线建议框），选倍数，点「重新推理」")
        uistyle.label(rb, var=self.var_zoomnote, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x", pady=(4, 0))

        info = uistyle.card(mid)
        info.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 12))
        self.var_m1 = tk.StringVar(value="")
        self.var_m2 = tk.StringVar(value="")
        self.var_m3 = tk.StringVar(value="")
        for v in (self.var_m1, self.var_m2, self.var_m3):
            uistyle.label(info, var=v, font=FONT_MONO, anchor="w", justify="left", wraplength=1240) \
                .pack(fill="x", padx=14, pady=(6, 0))
        uistyle.label(info, text=NOTE, fg=uistyle.C_WARN, wrap=True, font=FONT_SMALL).pack(fill="x", padx=14, pady=(4, 10))
        self.var_status = tk.StringVar(value="")
        uistyle.statusbar(t, 2, self.var_status, uistyle.DISCLAIMER + "｜HRF 图像 CC BY 4.0（Budai 2013）")

    def _mag_labels(self):
        """超过本图上限的倍数只能显示放大；括号里写上限，免得下拉框放不下被截断。"""
        why = "上限" if self.cap >= float(self.cfg["cap"]) - 1e-9 else "原图"
        return ["%g× 重新推理" % m if m <= self.cap + 1e-9 else
                "%g× 仅显示放大（%s %s×）" % (m, why, uistyle.fmt_mag(self.cap)) for m in self.cfg["mags"]]

    def close(self):
        uistyle.close_when_idle(self.top, lambda: self.running)

    def _mag(self):
        """当前选择 -> (推理倍数, 显示放大倍数)。超过本图上限：按上限推理，再插值放大。"""
        return te.effective_mag(float(self.var_mag.get().split("×")[0]), self.W0, self.cfg["cap"])

    # ------------------------------------------------------------------ 层
    def level(self, mag):
        """mag 倍的层：(层图, 有效 (宽, 高), 缩放比例, 板上路径或 None)。"""
        if mag not in self.levels:
            s = te.scale_for_mag(mag, self.W0)
            path = None
            if self.case:
                lv = self.case["levels"].get("%.4g" % mag)
                if lv and abs(lv["scale"] - s) < 1e-12:
                    path = lv["path"]
            lvl, valid = te.make_level(self.rgb0, s)
            self.levels[mag] = (lvl, valid, s, path)
        return self.levels[mag]

    # ------------------------------------------------------------------ 板上计算
    def _start(self, what, mag, box, order=None):
        if self.app.busy:
            self.var_status.set("板子正忙，稍等")
            return False
        self.app.busy = self.running = True
        self.btn_run.state(["disabled"])
        host, port =self.app.var_host.get().strip(), int(self.app.var_port.get().strip())
        threading.Thread(target=self._worker, args=(what, mag, box, order, host, port), daemon=True).start()
        return True

    def _worker(self, what, mag, box, order, host, port):
        lvl, valid, s, path = self.level(mag)
        H, W = lvl.shape[:2]
        try:
            link = self.app.link
            link.host, link.port = host, port
            out = te.run_board(link, W, H, box, te.TILE_LOGITS | te.TILE_OVERLAY, level_rgb=None if path else lvl,
                               path=path, order=order, thr=self.cfg["thr"], band=0,
                               on_tile=lambda t: self.q.put(("tile", what, t)))
            self.q.put(("done", what, (mag, box, out)))
        except Exception as e:                       # noqa: BLE001 - 界面上报错即可
            try:
                self.app.link.close()
            except Exception:                        # noqa: BLE001
                pass
            self.q.put(("err", what, str(e)))

    def _poll(self):
        try:
            while True:
                kind, what, v = self.q.get_nowait()
                if kind == "tile":
                    self._on_tile(what, v)
                elif kind == "done":
                    self._on_done(what, v)
                else:
                    self.app.busy = self.running = False
                    self.btn_run.state(["!disabled"])
                    msg = v if "unknown job" not in v else "板端不支持分块任务（需要服务端 v5.20）"
                    self.var_status.set("出错：%s" % msg)
        except queue.Empty:
            pass
        if self.top.winfo_exists():
            self.top.after(40, self._poll)

    def run_overview(self):
        lvl, valid, s, path = self.level(self.m_ov)
        H, W = lvl.shape[:2]
        self.var_status.set("总览：%d×%d 层、%d 块，在板上推理…" % (W, H, len(te.plan((0, 0, W, H), W, H))))
        self.ov_canvas = np.array(lvl[:valid[1], :valid[0]])
        self.render_overview()
        self._start("overview", self.m_ov, (0, 0, W, H))

    def run_zoom(self, box1=None):
        box1 = box1 or self.box1
        if box1 is None:
            self.var_status.set("先在总览上拖一个框（或点一个建议框）")
            return
        if self.overview is None:
            self.var_status.set("总览还没算完")
            return
        mag, self.disp_extra = self._mag()
        s1 = self.level(self.m_ov)[2]
        lvl, valid, s, path = self.level(mag)
        H, W = lvl.shape[:2]
        box = te.box_to_level(te.box_to_orig(box1, s1), s, W, H)
        tiles = te.plan(box, W, H)
        te.mark_fov_skip(tiles, lvl)
        ov = self.overview
        order = te.order_by_uncertainty(tiles, W, H, ov["logits"], ov["fov"], self.cfg["thr"], self.cfg["band"])
        x, y, w, h = box
        self.zoom = dict(mag=mag, box=box, box1=box1, tiles=tiles, logits=np.full((h, w), te.OUTSIDE_LOGIT, np.int8),
                         overlay=np.array(lvl[y:y + h, x:x + w]), base=np.array(lvl[y:y + h, x:x + w]), done=0,
                         t0=time.perf_counter(), out=None, path=path)
        c = te.costs(tiles)
        self.var_rtitle.set("放大视图（%s× 重新推理%s）" % (
            uistyle.fmt_mag(mag), "，再插值 %s×" % uistyle.fmt_mag(self.disp_extra) if self.disp_extra > 1 else ""))
        self.var_zoomnote.set("%s× 层 %d×%d，框 %d×%d → %d 块（视野外跳过 %d），halo 开销 %.0f%%，有效像素 %.0f%%；%s"
                              % (uistyle.fmt_mag(mag), W, H, w, h, c["tiles"], c["skipped"], 100 * c["halo_overhead"],
                                 100 * c["useful_ratio"], "板上 /data 层文件（只裁剪）" if path else "上传该区域"))
        self.render_zoom()
        self.render_overview()
        self._start("zoom", mag, box, order)

    def _on_tile(self, what, t):
        if what == "overview":
            return
        z = self.zoom
        if z is None:
            return
        x, y = z["box"][:2]
        sl = (slice(t["oy"] - y, t["oy"] - y + t["oh"]), slice(t["ox"] - x, t["ox"] - x + t["ow"]))
        if not t["skip"]:
            z["logits"][sl] = t["logits"]
            z["overlay"][sl] = t["overlay"]
        z["done"] += 1
        z.setdefault("got", []).append(t)
        self.var_status.set("放大：第 %d / %d 块（块 %d %s，U-Net %.1f ms）" % (
            z["done"], len(z["tiles"]), t["index"], "视野外跳过" if t["skip"] else "%d×%d" % (t["tw"], t["th"]),
            t["run_us"] / 1000.0))
        self.render_zoom()
        self.render_overview()

    def _on_done(self, what, v):
        self.app.busy = self.running = False
        self.btn_run.state(["!disabled"])
        mag, box, out = v
        res = out["result"]
        if not res.get("ok"):
            self.var_status.set("板上失败：%s" % res.get("error"))
            return
        if what == "overview":
            lvl, valid, s, path = self.level(self.m_ov)
            ovl = np.array(lvl)                         # 视野外跳过的块显示原图
            for t in out["tiles"]:
                if not t["skip"]:
                    ovl[t["oy"]:t["oy"] + t["oh"], t["ox"]:t["ox"] + t["ow"]] = t["overlay"]
            self.overview = dict(logits=out["logits"], overlay=ovl, fov=te.fov_mask(lvl), out=out, valid=valid)
            self.sugg = te.suggest(out["logits"][:valid[1], :valid[0]], te.fov_mask(lvl)[:valid[1], :valid[0]],
                                   self.cfg["thr"], self.cfg["band"], grid=96, k=5)
            self.var_ovnote.set("总览 %d×%d：%d 块，板上 %.0f ms（U-Net %.0f ms），往返 %.2f s；%s" % (
                res["level"][0], res["level"][1], res["tiles"], res["ms"], res["unet_ms"], out["wall_s"],
                "层来自板上 /data" if res["source"] == "file" else "上传"))
            self.var_status.set("总览完成。拖框选区域，或点虚线建议框")
            self.render_overview()
            box0, mag0 = self.pending
            self.pending = (None, None)
            if box0 is not None:
                s1 = self.level(self.m_ov)[2]
                lw, lh = self.level(self.m_ov)[0].shape[1], self.level(self.m_ov)[0].shape[0]
                self.box1 = te.box_to_level(box0, s1, lw, lh)
                if mag0 is not None:
                    for lab in self.mag_labels:
                        if abs(float(lab.split("×")[0]) - mag0) < 1e-9:
                            self.var_mag.set(lab)
                self.top.after(10, self.run_zoom)
            return
        z = self.zoom
        z["out"] = out
        self.render_zoom()
        self.render_overview()
        self._metrics()

    # ------------------------------------------------------------------ 指标
    def _metrics(self):
        z = self.zoom
        out, res = z["out"], z["out"]["result"]
        run = [t for t in out["tiles"] if not t["skip"]]
        tm = [t["run_us"] / 1000.0 for t in run]
        lvl, valid, s, path = self.level(z["mag"])
        x, y, w, h = z["box"]
        fov = te.fov_mask(lvl[y:y + h, x:x + w])
        m = z["logits"] >= self.cfg["thr"]
        dens = 100.0 * (m & fov).sum() / max(1, fov.sum())
        skip_save = (res["skipped"] / float(res["tiles"])) if res["tiles"] else 0.0
        self.var_m1.set("计算：%d 块（跑 %d、视野外跳过 %d = 省 %.0f%%）｜算了 %d 像素、框内 %d｜halo 开销 %.1f%%｜有效像素 %.1f%%"
                        % (res["tiles"], res["run"], res["skipped"], 100 * skip_save, res["computed_px"],
                           res["box_px"], 100 * res["halo_overhead"], 100 * res["useful_ratio"]))
        self.var_m2.set("耗时：每块 U-Net 平均 %.1f ms、最长 %.1f ms｜视图 板上 %.0f ms（U-Net %.0f、PL 叠加 %.1f、裁剪 %.1f）｜"
                        "往返 %.2f s（上传 %.2f s）｜%s"
                        % (np.mean(tm) if tm else 0, max(tm) if tm else 0, res["ms"], res["unet_ms"], res["ovl_ms"],
                           res["crop_ms"], out["wall_s"], out["upload_s"],
                           "层来自板上 /data（板子只裁剪）" if res["source"] == "file" else "层区域从 PC 上传"))
        txt = "框内（%s×，原图 %.0f×%.0f 像素）：视野内血管密度 %.1f%%" % (uistyle.fmt_mag(z["mag"]), w / s, h / s, dens)
        try:
            import sys
            ref_dir = os.path.join(HERE, "..", "tcp_image_rx", "ai", "net")
            if ref_dir not in sys.path:
                sys.path.insert(0, ref_dir)
            import vessel_morph_ref
            if w * h <= 1600 * 1600:
                r = vessel_morph_ref.analyze(m, fov, res512=int(round(512 * z["mag"])))
                f = 1.0 / s                                  # 层像素 -> 原图像素
                txt += ("｜骨架总长 %.0f px（原图）分支 %d 端点 %d 段 %d 迂曲度 %.3f 分形维数 %.3f"
                        % (r["length_x10"] / 10.0 * f, r["branches"], r["endpoints"], r["segments"],
                           r["tortuosity_x1000"] / 1000.0, r["fractal_x1000"] / 1000.0))
        except Exception as e:                         # noqa: BLE001 - 指标只是附加信息
            txt += "｜形态指标没算出：%s" % e
        self.var_m3.set(txt)
        self.var_status.set("放大完成：%d 块、板上 %.0f ms；拼接结果 = 该倍数整幅推理（逐位相同）" % (res["run"], res["ms"]))

    # ------------------------------------------------------------------ 显示
    def _view_rgb(self, base, logits, overlay):
        v = self.var_view.get()
        thr, band = self.cfg["thr"], self.cfg["band"]
        if v.startswith("叠加"):
            return overlay
        if v == self.mask_txt:
            return np.repeat(np.where(logits >= thr, 255, 0).astype(np.uint8)[..., None], 3, axis=2)
        if v == "置信度热图":
            return LUT[(logits.astype(np.int16) + 128)]
        if v == "不确定区域":
            return te.overlay_ref(base, logits, thr, band)
        return base

    def render_overview(self):
        if not hasattr(self, "ov_canvas"):
            return
        if self.overview is not None:
            ov = self.overview
            vw, vh = ov["valid"]
            lvl = self.level(self.m_ov)[0]
            rgb = self._view_rgb(lvl[:vh, :vw], ov["logits"][:vh, :vw], ov["overlay"][:vh, :vw])
        else:
            rgb = self.ov_canvas
        # 底图保持 RGB：ImageDraw 的 "RGBA" 模式只在 RGB 底图上做半透明混合；底图若是 RGBA，填充会直接覆盖像素，
        # 转回 RGB 后格子变成实心色、盖住整张总览（10-18 修）
        im = Image.fromarray(np.ascontiguousarray(rgb)).convert("RGB")
        d = ImageDraw.Draw(im, "RGBA")
        if self.overview is not None and self.var_heat.get():
            vw, vh = self.overview["valid"]
            sc = te.tile_scores(self.overview["logits"][:vh, :vw], self.overview["fov"][:vh, :vw], self.cfg["thr"],
                                self.cfg["band"], 64)
            mx = max(1e-6, sc[..., 0].max())
            for j in range(sc.shape[0]):
                for i in range(sc.shape[1]):
                    if sc[j, i, 2] > 0.2:
                        a = int(110 * sc[j, i, 0] / mx)
                        d.rectangle([i * 64, j * 64, i * 64 + 63, j * 64 + 63], fill=(255, 160, 0, a))
        if self.overview is not None and self.var_sugg.get():
            for k, (u, (x, y, w, h)) in enumerate(getattr(self, "sugg", [])):
                d.rectangle([x, y, x + w - 1, y + h - 1], outline=(0, 229, 255, 255), width=1)
                d.text((x + 3, y + 2), "%d" % (k + 1), fill=(0, 229, 255, 255))
        z = self.zoom
        if z is not None:                                   # 放大的块映射回总览：算完的实线、没算的虚
            s1 = self.level(self.m_ov)[2]
            f = s1 / te.scale_for_mag(z["mag"], self.W0)
            done = {t["index"]: t for t in z.get("got", [])}
            for t in z["tiles"]:
                r = [t.ox * f, t.oy * f, (t.ox + t.ow) * f - 1, (t.oy + t.oh) * f - 1]
                if t.index in done:
                    d.rectangle(r, outline=(120, 120, 120, 200) if done[t.index]["skip"] else (0, 200, 80, 255))
                else:
                    d.rectangle(r, outline=(255, 255, 255, 90))
        if self.box1 is not None:
            x, y, w, h = self.box1
            d.rectangle([x, y, x + w - 1, y + h - 1], outline=(255, 212, 0, 255), width=2)
        self.photo_l = ImageTk.PhotoImage(im)
        self.cv_l.itemconfigure(self.cv_l_img, image=self.photo_l)

    def render_zoom(self):
        z = self.zoom
        if z is None:
            return
        rgb = self._view_rgb(z["base"], z["logits"], z["overlay"])
        h, w = rgb.shape[:2]
        cw, ch = self._zoom_area()
        disp = self.var_disp.get()
        extra = getattr(self, "disp_extra", 1.0)
        if disp == "适应窗口":                              # 适应窗口 = 整个框正好放进视图（已含插值倍数）
            f = min(cw / float(w), ch / float(h))
        else:
            f = float(disp.rstrip("×")) * extra
        im = Image.fromarray(np.ascontiguousarray(rgb))
        size = (max(1, int(round(w * f))), max(1, int(round(h * f))))
        if size != (w, h):
            im = im.resize(size, Image.NEAREST if f >= 1 else Image.BILINEAR)
        self.photo_r = ImageTk.PhotoImage(im)
        self.cv_r.itemconfigure(self.cv_r_img, image=self.photo_r)
        ox, oy = max(0, (cw - size[0]) // 2), max(0, (ch - size[1]) // 2)     # 比视图小就居中
        self.cv_r.coords(self.cv_r_img, ox, oy)
        self.cv_r.configure(scrollregion=(0, 0, max(cw, size[0]), max(ch, size[1])))
        for sb, need in ((self.sx, size[0] > cw), (self.sy, size[1] > ch)):     # 放得下就不显示滚动条
            if need:
                sb.grid()
            else:
                sb.grid_remove()
        note = self.var_zoomnote.get().split("｜显示")[0]
        self.var_zoomnote.set("%s｜显示 %.2f 倍（%s）" % (
            note, f, "仅插值放大，不重新推理" if f > 1.0 or extra > 1.0 else "缩小显示"))

    def _zoom_area(self):
        """放大视图的实际像素尺寸（窗口还没画出来时用默认值）。"""
        w, h = self.cv_r.winfo_width(), self.cv_r.winfo_height()
        return (w, h) if w > 20 and h > 20 else (ZOOM_W, ZOOM_H)

    def _on_zoom_resize(self):
        if self.resize_job is not None:
            self.top.after_cancel(self.resize_job)
        self.resize_job = self.top.after(80, self._resized)

    def _resized(self):
        self.resize_job = None
        self.render_zoom()

    # ------------------------------------------------------------------ 框选
    def _xy(self, e):
        return max(0, min(OV_W - 1, e.x)), max(0, min(self.ov_h - 1, e.y))

    def _press(self, e):
        self.drag0 = self._xy(e)
        self.box_before = self.box1                    # 只是点了一下（没点中建议框）时恢复原来的框

    def _drag(self, e):
        if self.drag0 is None:
            return
        (x0, y0), (x1, y1) = self.drag0, self._xy(e)
        self.box1 = (min(x0, x1), min(y0, y1), abs(x1 - x0) + 1, abs(y1 - y0) + 1)
        self.render_overview()

    def _release(self, e):
        if self.drag0 is None:
            return
        (x0, y0), (x1, y1) = self.drag0, self._xy(e)
        self.drag0 = None
        if abs(x1 - x0) < 8 and abs(y1 - y0) < 8:          # 点击：选中所在的建议框
            for u, (x, y, w, h) in getattr(self, "sugg", []):
                if x <= x1 < x + w and y <= y1 < y + h:
                    self.box1 = (x, y, w, h)
                    break
            else:
                self.box1 = self.box_before
                self.render_overview()
                return
        else:
            self.box1 = (min(x0, x1), min(y0, y1), abs(x1 - x0) + 1, abs(y1 - y0) + 1)
        self.render_overview()
        self._box_status()

    def _box_status(self):
        """已选框在当前倍数下要算多少：选框后、换倍数后都更新一次。"""
        if self.box1 is None or self.running:
            return
        mag, extra = self._mag()
        lvl, valid, s, path = self.level(mag)
        box = te.box_to_level(te.box_to_orig(self.box1, self.level(self.m_ov)[2]), s, lvl.shape[1], lvl.shape[0])
        c = te.costs(te.plan(box, lvl.shape[1], lvl.shape[0]))
        self.var_status.set("已选框：%s× 下 %d×%d 像素、%d 块%s；点「重新推理」（或双击 / 回车）" % (
            uistyle.fmt_mag(mag), box[2], box[3], c["tiles"],
            "，再插值放大 %s× 显示" % uistyle.fmt_mag(extra) if extra > 1 else ""))

    def render(self):
        self.render_overview()
        self.render_zoom()

    # ------------------------------------------------------------------ 主窗口调用
    def zoom_to(self, box0, mag=None):
        """主窗口框选放大 > 1 倍：box0 = 原图坐标的框。总览还没算完就等它算完再放大。"""
        if self.overview is None:
            self.pending = (box0, mag)
            return
        lvl = self.level(self.m_ov)[0]
        self.box1 = te.box_to_level(box0, self.level(self.m_ov)[2], lvl.shape[1], lvl.shape[0])
        if mag is not None:
            for lab in self.mag_labels:
                if abs(float(lab.split("×")[0]) - mag) < 1e-9:
                    self.var_mag.set(lab)
        self.run_zoom()


# ══════════════════════════════════════════════════════════════════════════
# 来源：app.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""app.py —— 基于 FPGA 的眼底血管实时分析系统：上位机界面。

    顶栏：板卡地址 + 链路状态 + 发送 / 连续发送
    左栏：AI 推理 · 回传内容 · 点击纠错 · 专项工作站
    中间：发给板子的原图 | FPGA 处理后回传的图
    下方：往返总耗时 / 帧率 / 板端推理 / 目标占比 四张指标卡 + 详情页签（链路 / AI / 形态）
    菜单栏与界面控件是同一套变量，两边改哪边都行。

用法：run.bat 双击，或
    python app.py [host] [port]
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk



DEFAULT_HOST = "192.168.1.254"
DEFAULT_PORT = 5060
HISTORY_MAX = 10
FPS_WINDOW = 10            # 连续模式下按最近这么多帧的完成时刻算实测帧率

APP_TITLE_FUNDUS = "基于 FPGA 的眼底血管实时分析系统 — 上位机"
DISCLAIMER = "仅用于工程教学与算法演示，不作为临床诊断依据"
DATA_NOTE = "内置示例图来自 STARE 公开数据集"

# 掩膜在界面上的叫法：皮肤镜程序下叫「皮肤镜掩膜」，眼底血管叫「血管掩膜」
MASK_TEXT_SKIN = "皮肤镜掩膜"
MASK_TEXT_DEFAULT = "血管掩膜"


def mask_text(program=None):
    """按当前程序取掩膜的叫法（看程序 dict 的 group）。"""
    return MASK_TEXT_SKIN if (program or {}).get("group") == "skin" else MASK_TEXT_DEFAULT


def show_choices(program=None):
    """右图可选的显示内容：(值, 菜单里的名字)。值与板端回传图的名字一致。

    板端 PL 预处理已移除，界面不再提供「预处理图」；auto 取图仍可能落到 proc
    （回传里带了就用），它只用于说明文字，见 SHOW_LABELS。
    """
    return (("auto", "自动"), ("overlay", "掩膜叠加"), ("mask", mask_text(program)),
            ("heat", "置信度热力图"), ("uncertain", "不确定区域"), ("morph", "形态骨架"))


# 只说给状态栏 / 回图说明用，不进菜单（proc 已不在可选显示里）
SHOW_LABELS = {"auto": "自动", "proc": "预处理图", "overlay": "掩膜叠加",
               "mask": MASK_TEXT_DEFAULT, "heat": "置信度热力图",
               "uncertain": "不确定区域", "morph": "形态骨架"}
AI_VIEWS = ("overlay", "mask", "heat", "uncertain")   # 有 logit 时可在本地重算的视图
UNSURE_BAND_DEFAULT = 8    # 不确定带：|logit - 门限| <= 8（约 ±0.8 个 logit 单位）
ROI_MIN_SIZE = 8           # 拖出的框小于 8 像素当作误点
ZOOM_1X = "1×（ROI，帧内推理）"   # 框选放大 1 倍 = 原来的 ROI 局部推理（v5 控制头，逐位相同）


def zoom_choices():
    """框选放大的选项：1 倍 = 老 ROI；更大的倍数 = 在原图上按该倍数重新推理（大图精查窗口，病例任务 JOB_TILE）。
    倍数和上限来自 tile_config.json（HRF 训练集上量的 Dice-倍数曲线，见 E:/PLD/hrf/README.md）。"""
    bigwin = sys.modules[__name__]
    cfg = bigwin.load_config()
    return [ZOOM_1X] + ["%g× 重新推理（大图精查）" % m for m in cfg["mags"] if 1 < m <= float(cfg["cap"])]


# WS_OPT_V1 按需生成的视图映射：只有真正被取用的键才去算。
class _LazyViews(dict):
    """dict 的按需版本：later(key, fn) 登记一个造价高的视图，真正被读时才生成。

    只覆盖 dict 的读接口（[] / in / get / items / values），写接口照常。
    """

    def __init__(self, *a, **kw):
        dict.__init__(self, *a, **kw)
        self._later = {}

    def later(self, key, fn):
        self._later[key] = fn
        return self

    def _make(self, key):
        fn = self._later.pop(key, None)
        if fn is None:
            return None
        value = fn()
        dict.__setitem__(self, key, value)
        return value

    def __missing__(self, key):
        value = self._make(key)
        if value is None:
            raise KeyError(key)
        return value

    def __contains__(self, key):
        if dict.__contains__(self, key):
            return True
        if key in self._later:
            self._make(key)
            return True
        return False

    def get(self, key, default=None):
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        if key in self._later:
            return self._make(key)
        return default

    def items(self):
        for k in list(self._later):
            self._make(k)
        return dict.items(self)

    def values(self):
        for k in list(self._later):
            self._make(k)
        return dict.values(self)


class App(object):
    # busy：嵌在平台扩展窗口里时与心超工作站共用一个（板端一次只服务一个连接）
    busy = property(lambda self: self.shared.busy if self.shared is not None else self._busy,
                    lambda self, v: setattr(self.shared, "busy", v) if self.shared is not None
                    else setattr(self, "_busy", v))

    def __init__(self, root, host, port, selftest_path=None, preset_flags=None,
                 preset_ai_src=None, preset_ai_thr=None, preset_ai_res=None,
                 preset_thr=None, preset_mode=None, parent=None, shared=None, program=None):
        """parent / shared / program：嵌进 platformwin.PlatformWindow 的一页（parent = 页框，shared = 心超工作站
        heartapp.HeartApp：共用板子连接、地址、busy 和状态栏；program = 这一页的程序 dict，见 platformwin.PROGRAMS）。
        不给时与原来的独立主界面完全相同。"""
        self.root = root
        self.parent, self.shared, self.program = parent, shared, program
        self.queue = queue.Queue()
        self.selftest_path = selftest_path
        # 掩膜叫法随程序变（皮肤镜 = 皮肤镜掩膜，眼底 = 血管掩膜）；显示菜单也按它建
        self.mask_txt = mask_text(program)
        self.show_choices = show_choices(program)
        self.show_keys = {label: value for value, label in self.show_choices}

        self.link = shared.link if shared is not None else imagelink.ImageLink(host, port)

        self.image = None          # 当前要发送的原图(bytes)
        self.inflight_image = None                       # WS_OPT_V2：在途那一帧真正发出去的字节
        self.fast_noimg = False                          # WS_OPT_V2：本帧是否走了「少回图」
        self.inter = None          # 点击纠错的一轮会话（interactive.Session）；None = 没在纠错
        self.inflight_inter = None  # 在途那一帧是点击纠错帧时，它带的点击列表
        self.photo_left = None     # 必须持有引用, 否则会被 GC 掉
        self.photo_right = None
        self.last_reply = None     # 最近一次收到的回图(bytes), 供"保存回图"用
        self.reply_images = {}     # 最近一次收到的所有回传图 {名字: bytes}
        self.view_images = {}      # 右图可显示的图：回传图 + 由 logit 本地生成的视图
        self.local_views = set()   # view_images 里哪些是本地重算的(门限与板端不同)
        self.logit_plane = None    # 本帧 logit（偏移码，每像素 1 字节）；没回传时为 None
        self.logit_base = None     # 本地叠加用的底图（板端叠加的底图是预处理图）
        self.logit_base_note = ""
        self.heat_cache = None     # 热力图与门限无关，每帧算一次
        self.frame_ai_thr = proto.AIPARAM_THR_DEFAULT   # 本帧板端用的 AI 门限
        self.inflight_ai_thr = proto.AIPARAM_THR_DEFAULT
        self.frame_ai_res = 512
        self.verify_note = ""      # 本地复算 vs 板端回传图的一致性
        self._verify_t = 0.0       # WS_OPT_V1 上次逐字节自检的时刻（限速用）
        self._verify_last = ""     # WS_OPT_V1 上次逐字节自检的结论（限速期间沿用）
        self.conf_job = None
        self.inflight_key = None
        self.roi = None           # 界面上选的 ROI (x, y, w, h)；None = 整图推理
        self.roi_drag_start = None
        self.inflight_roi = None   # 在途那一帧发出去的 ROI
        self.frame_roi = None      # 当前显示的这一帧是哪个 ROI 的结果（整图帧为 None）
        self.full_ref = None       # 同一张图上一帧整图推理的 logit，用来核对 ROI 结果
        self.roi_verify_note = ""
        self.board_stat_version = None   # 最近一次统计块的 version（判断板端认不认 ROI）
        self.orig_rgb = None       # 载入的原图（任意尺寸，numpy）；框选放大 > 1 倍时在它上面重新推理
        self.orig_name = None
        self.reply_stats = {}      # 每幅回图的 {rows_ok, crc_errors, seq_errors}
        self.ai_stats = None       # 最近一次的 AI 统计块(dict)或 None
        self.showing = None        # 当前右图显示的是哪一种
        self.last_thr = proto.CTRL_THR_DEFAULT   # 最近一帧用的阈值分割门限
        self.hist = []             # 最近若干次的 total_ms
        self.done_times = []       # 连续模式下最近若干帧的完成时刻(perf_counter), 算实测帧率
        if shared is None:         # 共用时不动心超工作站的 busy（它可能正有任务在途）
            self.busy = False
        self.repeat = False
        self.repeat_job = None

        self._build_ui()
        if preset_flags is not None:
            self.var_f_proc.set(bool(preset_flags & proto.FLAG_ECHO_PROC))
            self.var_f_mask.set(bool(preset_flags & proto.FLAG_ECHO_MASK))
            self.var_f_over.set(bool(preset_flags & proto.FLAG_ECHO_OVER))
            self.var_f_stats.set(bool(preset_flags & proto.FLAG_STATS))
            self.var_f_logit.set(bool(preset_flags & proto.FLAG_ECHO_LOGIT))
            self.var_f_morph.set(bool(preset_flags & proto.FLAG_MORPH))
        if preset_ai_thr is not None:
            self.var_ai_thr.set(str(preset_ai_thr))
        if preset_ai_res is not None:
            self.var_ai_res.set(str(preset_ai_res))
        self._load_pattern("bars")
        if preset_mode is not None and 0 <= preset_mode < len(self.mode_labels):
            self.var_mode.set(self.mode_labels[preset_mode])    # 界面无入口，只留命令行调试
        if preset_thr is not None:
            self.var_thr.set(str(preset_thr))
        self.root.after(50, self._poll)

    # ------------------------------------------------------------------ UI
    # 布局：顶栏（板卡地址 / 链路状态 / 发送）
    #       左侧参数栏（AI 推理 · 回传内容 · 点击纠错 · 专项工作站）
    #       右侧两幅图 + 四张指标卡 + 详情页签
    #       底部状态栏（常驻使用声明）
    # 只用带前缀的 ttk 样式（Card.* / Detail.*），不改默认样式，免得影响各个子窗口。
    # 配色 / 字体 / 卡片等通用部件都在 uistyle.py，子窗口共用
    def _setup_style(self):
        uistyle.setup_style(self.root)

    _card = staticmethod(uistyle.card)
    _label = staticmethod(uistyle.label)

    def _section(self, parent, title, first=False):
        """左侧参数栏的一节：分隔线 + 小标题 + 两列网格（标签 | 控件）。"""
        if not first:
            tk.Frame(parent, bg=C_BORDER, height=1).pack(fill="x", padx=14, pady=(10, 0))
        self._label(parent, title, font=FONT_HEAD, fg=C_ACCENT) \
            .pack(anchor="w", padx=14, pady=(10 if first else 6, 2))
        body = tk.Frame(parent, bg=C_CARD)
        body.pack(fill="x", padx=14)
        body.grid_columnconfigure(1, weight=1)
        return body

    def _field(self, body, row, label, widget=None, label_var=None):
        self._label(body, label, var=label_var, fg=C_MUTED) \
            .grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
        if widget is not None:
            widget.grid(row=row, column=1, sticky="ew", pady=2)

    def _slider(self, parent, var, lo, hi, extra=None):
        """数值框 + 滑条（+ 可选的按钮），和同一个 StringVar 双向同步，写回的总是整数。"""
        f = tk.Frame(parent, bg=C_CARD)
        ent = ttk.Entry(f, textvariable=var, width=5, font=FONT_MONO)
        ent.pack(side="left")

        def from_scale(v):
            s = str(int(round(float(v))))
            if var.get() != s:
                var.set(s)

        sc = ttk.Scale(f, from_=lo, to=hi, orient="horizontal", length=120, command=from_scale)
        sc.pack(side="left", fill="x", expand=True, padx=(8, 0))

        def from_var(*_):
            try:
                v = max(lo, min(hi, float(var.get())))
            except ValueError:
                return                   # 正在输入（比如只打了个负号）
            if abs(sc.get() - v) >= 0.5:
                sc.set(v)

        var.trace_add("write", from_var)
        from_var()
        if extra is not None:
            text, cmd = extra
            btn = ttk.Button(f, text=text, width=len(text) + 1, command=cmd)
            btn.pack(side="left", padx=(8, 0))
            return f, ent, sc, btn
        return f, ent, sc

    def _build_ui(self):
        embedded = self.parent is not None
        r = self.parent if embedded else self.root
        if not embedded:
            r.title(APP_TITLE_FUNDUS)
        r.configure(bg=C_BG)
        uistyle.setup_style(r)

        self._build_topbar(r)
        side = self._card(r)
        side.grid(row=1, column=0, sticky="nsw", padx=(12, 0), pady=12)
        self._build_sidebar(side)
        main = tk.Frame(r, bg=C_BG)
        main.grid(row=1, column=1, sticky="nsew" if not embedded else "n", padx=12, pady=12)
        self._build_images(main)
        self._build_metrics(main)

        # ---- 状态栏：左边是状态，右边常驻使用声明（赛题基础要求⑥）；嵌入时用平台窗口的状态栏 ----
        self.var_status = tk.StringVar(value="就绪")
        if embedded:
            self.var_status.trace_add("write", lambda *a: self.shared.status(self.var_status.get()))
        else:
            uistyle.statusbar(r, 2, self.var_status, "%s｜%s" % (DISCLAIMER, DATA_NOTE), columnspan=2)

        r.grid_rowconfigure(1, weight=1)
        r.grid_columnconfigure(1, weight=1)
        self._update_conf_note()
        self._sync_send_btn()
        if not embedded:
            self._build_menu()
        else:                                          # 嵌入时没有菜单栏：常用快捷键绑在平台窗口上
            for seq, cmd in (("<Control-o>", self._load_file), ("<F5>", self._on_send), ("<Escape>", self._roi_clear)):
                self.parent.winfo_toplevel().bind(seq, lambda e, c=cmd: c() if self.parent.winfo_ismapped() else None)

    def _build_topbar(self, r):
        bar = tk.Frame(r, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        if self.shared is not None:                   # 嵌入：标题 = 本页程序，地址在心超工作站标题栏
            self.var_prog_title = tk.StringVar(value=(self.program or {}).get("title", "单幅图像程序"))
            self.var_prog_note = tk.StringVar(value=(self.program or {}).get("note", ""))
            self._label(bar, var=self.var_prog_title, font=FONT_TITLE).pack(side="left", padx=(16, 0), pady=10)
            self._label(bar, var=self.var_prog_note, font=FONT_SMALL, fg=C_MUTED) \
                .pack(side="left", padx=(12, 0), pady=(6, 0))
        else:
            self._label(bar, APP_TITLE_FUNDUS.split(" — ")[0], font=FONT_TITLE) \
                .pack(side="left", padx=(16, 0), pady=10)

        # 右侧从右往左：发送按钮 / 连续发送 / 板卡地址 + 链路灯
        self.btn_send = tk.Button(bar, text="发送 ▶", font=FONT_HEAD, fg="white", bg=C_ACCENT,
                                  activebackground=C_ACCENT_D, activeforeground="white",
                                  disabledforeground="#c7d2fe", relief="flat", bd=0,
                                  padx=20, pady=5, cursor="hand2", command=self._on_send_button)
        self.btn_send.pack(side="right", padx=(12, 16), pady=8)

        rep = tk.Frame(bar, bg=C_CARD)
        rep.pack(side="right", padx=(20, 0))
        self.var_repeat = tk.BooleanVar(value=False)
        ttk.Checkbutton(rep, text="连续发送，间隔", style="Card.TCheckbutton",
                        variable=self.var_repeat, command=self._on_repeat_toggle).pack(side="left")
        self.var_interval = tk.StringVar(value="200")
        ttk.Entry(rep, textvariable=self.var_interval, width=5, font=FONT_MONO) \
            .pack(side="left", padx=(4, 3))
        self._label(rep, "ms", fg=C_MUTED).pack(side="left")

        conn = tk.Frame(bar, bg=C_CARD)
        conn.pack(side="right")
        if self.shared is not None:
            self.var_host, self.var_port = self.shared.var_host, self.shared.var_port
        else:
            self._label(conn, "板卡", fg=C_MUTED).pack(side="left", padx=(0, 6))
            self.var_host = tk.StringVar(value=self.link.host)
            ttk.Entry(conn, textvariable=self.var_host, width=14, font=FONT_MONO).pack(side="left")
            self._label(conn, ":", fg=C_MUTED).pack(side="left", padx=2)
            self.var_port = tk.StringVar(value=str(self.link.port))
            ttk.Entry(conn, textvariable=self.var_port, width=6, font=FONT_MONO).pack(side="left")
        self.lbl_link = self._label(conn, "●", fg=C_IDLE)
        self.lbl_link.pack(side="left", padx=(12, 3))
        self.var_link = tk.StringVar(value="未连接")
        self._label(conn, var=self.var_link, fg=C_MUTED, width=8, anchor="w").pack(side="left")

    def _build_sidebar(self, side):
        # ---- AI 推理：模型库同一比特流换一套参数（板端统计块 version >= 5 才认）----
        body = self._section(side, "AI 推理", first=True)
        self.var_ai_model = tk.StringVar(value=proto.MODELS[0]["label"])
        models = [m for m in proto.MODELS if not m.get("hidden")]
        if self.shared is not None:                   # 平台扩展里只放心脏以外的单图程序（心超在主界面）
            models = [m for m in models if not m["name"].startswith("echo")]
        self._field(body, 0, "模型", ttk.Combobox(
            body, textvariable=self.var_ai_model, width=16, font=FONT, state="readonly",
            values=[m["label"] for m in models]))
        self.var_ai_model.trace_add("write", lambda *a: self._on_model_change())
        self.var_ai_res = tk.StringVar(value="512")
        self._field(body, 1, "分辨率", ttk.Combobox(
            body, textvariable=self.var_ai_res, width=16, font=FONT, state="readonly",
            values=[str(v) for v in proto.AI_RES_CHOICES]))
        # AI 输入固定「原始 RGB」：板端 PL 预处理已移除，界面不再给「预处理结果」这个选项
        # （AIPARAM 里的 src 字段照旧发，见 _build_ctrl）
        self.var_ai_src = tk.StringVar(value="原始 RGB")
        # 判定门限 + 不确定带：有 logit 时拖动只在本地重算，不重跑 FPGA
        self.var_ai_thr = tk.StringVar(value=str(proto.AIPARAM_THR_DEFAULT))
        self._field(body, 3, "判定门限", self._slider(body, self.var_ai_thr, -64, 64)[0])
        self.var_band = tk.StringVar(value=str(UNSURE_BAND_DEFAULT))
        self._field(body, 4, "不确定带 ±", ttk.Spinbox(
            body, from_=1, to=64, textvariable=self.var_band, width=5, font=FONT_MONO))
        body.grid_slaves(row=4, column=1)[0].grid_configure(sticky="w")
        self.var_conf_note = tk.StringVar(value="")
        self._label(body, var=self.var_conf_note, font=FONT_SMALL, fg=C_MUTED, wrap=True) \
            .grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self.var_ai_thr.trace_add("write", lambda *a: self._schedule_conf())
        self.var_band.trace_add("write", lambda *a: self._schedule_conf())
        # 框选放大（部分 C）：1 倍 = 原来的 ROI；> 1 倍 = 原图上按该倍数分块重新推理（大图精查窗口）
        self.zoom_values = zoom_choices()
        self.var_zoom = tk.StringVar(value=ZOOM_1X)
        self._field(body, 6, "框选放大", ttk.Combobox(
            body, textvariable=self.var_zoom, width=16, font=FONT, state="readonly", values=self.zoom_values))
        self.var_zoom.trace_add("write", lambda *a: self._draw_roi())

        # ---- 回传内容（flags）----
        # 「预处理图」不再作为可选项：板端 PL 预处理已移除，这一路回图在协议里保留（proc 流照旧请求），
        # 只是界面不再暴露勾选框，见 _build_ctrl 里的 var_f_proc。
        body = self._section(side, "回传内容")
        self.var_f_proc = tk.BooleanVar(value=True)
        self.var_f_mask = tk.BooleanVar(value=False)
        self.var_f_over = tk.BooleanVar(value=False)
        self.var_f_stats = tk.BooleanVar(value=False)
        self.var_f_logit = tk.BooleanVar(value=False)
        self.var_f_morph = tk.BooleanVar(value=False)
        for i, (text, var) in enumerate((
                (self.mask_txt, self.var_f_mask),
                ("掩膜叠加", self.var_f_over), ("数值统计", self.var_f_stats),
                ("置信度 logit", self.var_f_logit), ("形态指标", self.var_f_morph))):
            cb = ttk.Checkbutton(body, text=text, variable=var, style="Card.TCheckbutton")
            cb.grid(row=i // 2, column=i % 2, sticky="w", pady=2, padx=(0, 12))
            if var is self.var_f_mask:                 # 皮肤镜程序下文案要跟着换
                self.cb_mask = cb

        # ---- 预处理：板端 PL 预处理已移除，界面不再提供模式 / 门限 ----
        # 控制头里的 mode / thr 字段照旧发（协议不改）：固定「5 原图直通」+ 默认门限，
        # 只留两个隐藏变量给 _build_ctrl 和命令行 --mode / --thr 调试用。
        self.mode_labels = ["%d %s" % (k, proto.MODE_NAMES[k]) for k in sorted(proto.MODE_NAMES)]
        self.var_mode = tk.StringVar(value="%d %s" % (5, proto.MODE_NAMES[5]))
        self.var_thr = tk.StringVar(value=str(proto.CTRL_THR_DEFAULT))

        # ---- 点击纠错（F2）：有配套交互模型的任务（心超）才可用；平台扩展里没有心超，不放 ----
        self.var_inter = tk.BooleanVar(value=False)
        if self.shared is None:
            body = self._section(side, "点击纠错")
            ttk.Checkbutton(body, text="启用（左键是 / 右键否）", variable=self.var_inter,
                            style="Card.TCheckbutton", command=self._on_inter_toggle) \
                .grid(row=0, column=0, sticky="w")
            btns = tk.Frame(body, bg=C_CARD)
            btns.grid(row=0, column=1, sticky="e")
            ttk.Button(btns, text="撤销", width=6, command=self._inter_undo).pack(side="left")
            ttk.Button(btns, text="重置", width=6, command=self._inter_reset) \
                .pack(side="left", padx=(6, 0))

        # ---- 专项工作站：各自开子窗口，和主界面共用板子连接 ----
        if self.shared is not None:                   # 平台扩展：心超 / 序列在主界面，平台窗口只余单幅图页
            tools = [("大图精查", self._open_bigwin)] + [
                (label, (lambda f=fn: f(self))) for label, fn in getattr(self.shared, "extra_tools", [])]
        else:
            tools = [("序列跟踪", self._open_sequence), ("大图精查", self._open_bigwin)]
        body = self._section(side, "专项工作站" if self.shared is None else "更多")
        body.grid_columnconfigure(0, weight=1, uniform="tools")
        body.grid_columnconfigure(1, weight=1, uniform="tools")
        for i, (text, cmd) in enumerate(tools):
            ttk.Button(body, text=text, command=cmd).grid(
                row=i // 2, column=i % 2, sticky="ew", pady=3,
                padx=(0, 4) if i % 2 == 0 else (4, 0))
        tk.Frame(side, bg=C_CARD, height=12).pack()

    def _build_images(self, main):
        imgs = tk.Frame(main, bg=C_BG)
        imgs.grid(row=0, column=0, sticky="n")

        # ---- 左：发给板子的原图（画布：要在图上拖框选 ROI / 点击纠错）----
        lc = self._card(imgs)
        lc.grid(row=0, column=0, padx=(0, 12))
        head = tk.Frame(lc, bg=C_CARD)
        head.pack(fill="x", padx=12, pady=(10, 8))
        self._label(head, "原图", font=FONT_HEAD).pack(side="left")
        ttk.Button(head, text="载入图片…", command=self._load_file).pack(side="right")
        # 下拉框 = 三个内置图样 + samples/ 里的默认测试图（im0001~im0005 等）。
        # 往 samples/ 里丢新图后**重启工具**即可看到（列表在启动时扫一次）。
        self.var_pattern = tk.StringVar(value="bars")
        cb = ttk.Combobox(head, textvariable=self.var_pattern, width=16, font=FONT,
                          state="readonly", values=imageio.figure_names())
        cb.pack(side="right", padx=(0, 6))
        cb.bind("<<ComboboxSelected>>", lambda e: self._load_pattern(self.var_pattern.get()))
        # 边框和高亮都设 0，画布坐标 = 图像像素坐标
        self.cv_left = tk.Canvas(lc, width=512, height=512, bg=C_EMPTY,
                                 bd=0, highlightthickness=0, cursor="crosshair")
        self.cv_left.pack(padx=12)
        self.cv_img = self.cv_left.create_image(0, 0, anchor="nw")
        self.cv_left.bind("<ButtonPress-1>", self._roi_press)
        self.cv_left.bind("<B1-Motion>", self._roi_drag)
        self.cv_left.bind("<ButtonRelease-1>", self._roi_release)
        self.cv_left.bind("<Button-3>", self._right_click)
        self.root.bind("<Control-z>", lambda e: self._inter_undo())
        self.var_roi_note = tk.StringVar(value="")
        self._label(lc, var=self.var_roi_note, font=FONT_SMALL, fg=C_MUTED, anchor="w") \
            .pack(fill="x", padx=12, pady=(6, 10))

        # ---- 右：板子回传（按「回传内容」生成）----
        rc = self._card(imgs)
        rc.grid(row=0, column=1)
        head = tk.Frame(rc, bg=C_CARD)
        head.pack(fill="x", padx=12, pady=(10, 8))
        self._label(head, "板子回传", font=FONT_HEAD).pack(side="left")
        self.btn_save = ttk.Button(head, text="保存…", command=self._save_reply)
        self.btn_save.pack(side="right")
        # var_show 存的是回传图的名字（auto/mask/…），下拉框显示中文名（掩膜这档随程序变）
        self.var_show = tk.StringVar(value="auto")
        self.var_show_label = tk.StringVar(value=SHOW_LABELS["auto"])
        self.show_cb = ttk.Combobox(head, textvariable=self.var_show_label, width=12, font=FONT,
                                    state="readonly",
                                    values=[label for _, label in self.show_choices])
        self.show_cb.pack(side="right", padx=(0, 6))
        self.show_cb.bind("<<ComboboxSelected>>", lambda e: (
            self.var_show.set(self.show_keys[self.var_show_label.get()]), self._render_right()))
        self.var_show.trace_add("write", lambda *a: self.var_show_label.set(
            self._show_label(self.var_show.get())))
        self._label(head, "显示", fg=C_MUTED).pack(side="right", padx=(0, 6))
        self.cv_right = tk.Canvas(rc, width=512, height=512, bg=C_EMPTY,
                                  bd=0, highlightthickness=0)
        self.cv_right.pack(padx=12)
        self.cv_right_img = self.cv_right.create_image(0, 0, anchor="nw")
        self.cv_right_hint = self.cv_right.create_text(
            256, 256, text="发送一帧后，这里显示板子回传的结果", fill=C_MUTED, font=FONT)
        self.var_reply_note = tk.StringVar(value="")
        self._label(rc, var=self.var_reply_note, font=FONT_SMALL, fg=C_MUTED, anchor="w") \
            .pack(fill="x", padx=12, pady=(6, 10))

    def _build_metrics(self, main):
        # ---- 四张指标卡：往返总耗时 / 帧率 / 板端推理 / 目标占比，各带一行口径说明 ----
        row = tk.Frame(main, bg=C_BG)
        row.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.var_total = tk.StringVar(value="—")
        self.var_fps = tk.StringVar(value="—")
        self.var_fps_note = tk.StringVar(value="")
        self.var_infer = tk.StringVar(value="—")
        self.var_infer_note = tk.StringVar(value="勾选「数值统计」后显示")
        self.var_target = tk.StringVar(value="—")
        self.var_target_note = tk.StringVar(value="勾选「数值统计」后显示")
        self.var_total_note = tk.StringVar(value="上位机发图 → 收齐回图")
        for col, (title, var, note, color) in enumerate((
                ("往返总耗时", self.var_total, self.var_total_note, C_GREEN),
                ("帧率", self.var_fps, self.var_fps_note, C_ACCENT),
                ("板端推理（整网）", self.var_infer, self.var_infer_note, C_RED),
                ("目标占比", self.var_target, self.var_target_note, C_PURPLE))):
            row.grid_columnconfigure(col, weight=1, uniform="metric")
            c = self._card(row)
            c.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 12, 0))
            self._label(c, title, font=FONT_SMALL, fg=C_MUTED).pack(anchor="w", padx=14, pady=(6, 0))
            self._label(c, var=var, font=FONT_BIG, fg=color).pack(anchor="w", padx=14)
            self._label(c, var=note, font=FONT_SMALL, fg=C_MUTED, wrap=True) \
                .pack(fill="x", padx=14, pady=(0, 6))

        # ---- 详情：链路计时 / AI 统计 / 血管形态，分页签放，默认只看链路 ----
        nb = ttk.Notebook(main, style="Detail.TNotebook")
        nb.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self.var_detail = tk.StringVar(value="")
        self.var_stats = tk.StringVar(value="统计：还没有数据")
        self.var_hist = tk.StringVar(value="")
        self.var_ai = tk.StringVar(value="AI：还没有数据")
        self.var_morph = tk.StringVar(value="形态：勾「形态指标」后由板端计算")
        labels = []
        for title, lines in (("链路计时", (self.var_detail, self.var_stats, self.var_hist)),
                             ("AI 统计", (self.var_ai,)),
                             ("血管形态", (self.var_morph,))):
            page = tk.Frame(nb, bg=C_CARD, pady=4)
            for var in lines:
                lb = self._label(page, var=var, font=FONT_MONO, justify="left", anchor="w",
                                 wraplength=600)
                lb.pack(fill="x", padx=12, pady=2)
                labels.append(lb)
            nb.add(page, text=title)
        # 没选中的页签不会收到 <Configure>，所以按整个页签栏的宽度统一折行
        nb.bind("<Configure>", lambda e: [lb.configure(wraplength=max(200, e.width - 40))
                                          for lb in labels])

    def _build_menu(self):
        """菜单栏。所有项都绑在界面控件用的同一批变量上，所以两边始终同步。"""
        r = self.root
        menubar = tk.Menu(r)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="载入图片…", accelerator="Ctrl+O", command=self._load_file)
        m_file.add_command(label="保存当前回图…", accelerator="Ctrl+S",
                           command=self._save_reply)
        m_file.add_separator()
        m_file.add_command(label="退出", command=r.destroy)
        menubar.add_cascade(label="文件", menu=m_file)

        m_run = tk.Menu(menubar, tearoff=0)
        m_run.add_command(label="发送一帧", accelerator="F5", command=self._on_send)
        m_run.add_checkbutton(label="连续发送", variable=self.var_repeat,
                              command=self._on_repeat_toggle)
        menubar.add_cascade(label="运行", menu=m_run)

        # 「处理」菜单随 PL 预处理一起移除：模式固定直通，门限固定默认值（协议字段保留）

        m_ai = tk.Menu(menubar, tearoff=0)
        self.m_ai = m_ai
        # 掩膜这一项单独加：皮肤镜程序下要改文案，_refresh_mask_text() 按 idx_ai_mask 定位
        self.idx_ai_mask = 0
        m_ai.add_checkbutton(label="回传" + self.mask_txt, variable=self.var_f_mask)
        for label, var in (("回传掩膜叠加", self.var_f_over), ("回传数值统计", self.var_f_stats),
                           ("回传置信度（logit）", self.var_f_logit),
                           ("回传形态指标", self.var_f_morph)):
            m_ai.add_checkbutton(label=label, variable=var)
        m_ai.add_separator()
        m_res = tk.Menu(m_ai, tearoff=0)
        for res in proto.AI_RES_CHOICES:
            m_res.add_radiobutton(label="%d×%d" % (res, res), variable=self.var_ai_res,
                                  value=str(res))
        m_ai.add_cascade(label="AI 分辨率", menu=m_res)
        m_model = tk.Menu(m_ai, tearoff=0)
        for m in proto.MODELS:
            if not m.get("hidden"):
                m_model.add_radiobutton(label=m["label"], variable=self.var_ai_model, value=m["label"])
        m_ai.add_cascade(label="AI 模型", menu=m_model)
        m_ai.add_separator()
        m_ai.add_command(label="清除 ROI（恢复整图推理）", accelerator="Esc",
                         command=self._roi_clear)
        m_zoom = tk.Menu(m_ai, tearoff=0)
        for label in self.zoom_values:
            m_zoom.add_radiobutton(label=label, variable=self.var_zoom, value=label)
        m_ai.add_cascade(label="框选放大", menu=m_zoom)
        menubar.add_cascade(label="AI", menu=m_ai)

        m_view = tk.Menu(menubar, tearoff=0)
        self.m_view = m_view
        self.idx_view_mask = None
        for i, (value, label) in enumerate(self.show_choices):
            m_view.add_radiobutton(label="右图：" + label, variable=self.var_show, value=value,
                                   command=self._render_right)
            if value == "mask":
                self.idx_view_mask = i
        menubar.add_cascade(label="视图", menu=m_view)

        m_tools = tk.Menu(menubar, tearoff=0)
        m_tools.add_command(label="序列跟踪（心超，F3）…", command=self._open_sequence)
        m_tools.add_command(label="大图精查（眼底大图分块推理，部分 C）…", command=self._open_bigwin)
        menubar.add_cascade(label="工具", menu=m_tools)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="关于与使用声明", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=m_help)

        r.configure(menu=menubar)
        # 只绑主窗口（不用 bind_all）：子窗口里按 Esc / F5 / Ctrl+O 不该清主界面的 ROI、给主界面发帧
        r.bind("<Control-o>", lambda e: self._load_file())
        r.bind("<Control-s>", lambda e: self._save_reply())
        r.bind("<F5>", lambda e: self._on_send())
        r.bind("<Escape>", lambda e: self._roi_clear())

    def _show_about(self):
        messagebox.showinfo(
            "关于",
            "%s\n\n"
            "链路：上位机经以太网发图 → FZ3B（Zynq UltraScale+ XAZU3EG）\n"
            "PL 完成 U-Net 血管分割推理 → 结果回传上位机显示。\n\n"
            "%s。\n\n%s。" % (APP_TITLE_FUNDUS, DATA_NOTE, DISCLAIMER))

    # -------------------------------------------------------------- 行为
    def _status(self, msg):
        self.var_status.set(msg)

    def _current_mode(self):
        """控制头里的模式编号（形如 "5 原图直通"）。界面已无入口，固定直通；--mode 仍可覆盖。"""
        try:
            return int(self.var_mode.get().split()[0])
        except (ValueError, IndexError):
            return 0

    def _show_label(self, name):
        """回图名字 -> 界面上的叫法。掩膜这一档随程序变（皮肤镜 = 皮肤镜掩膜）。"""
        return self.mask_txt if name == "mask" else SHOW_LABELS.get(name, name)

    def _refresh_mask_text(self):
        """换程序后把掩膜相关的文案一起换掉（皮肤镜 = 皮肤镜掩膜，眼底 = 血管掩膜）。

        涉及四处：左侧「回传内容」勾选框、「AI」菜单、「视图」菜单、「板子回传」卡头的显示下拉。
        嵌入模式下没有菜单栏，用 getattr 兜住。
        """
        txt = mask_text(self.program)
        if txt == self.mask_txt:
            return
        self.mask_txt = txt
        self.show_choices = show_choices(self.program)
        self.show_keys = {label: value for value, label in self.show_choices}
        cb = getattr(self, "cb_mask", None)
        if cb is not None:
            cb.configure(text=txt)
        if getattr(self, "m_ai", None) is not None:
            self.m_ai.entryconfigure(self.idx_ai_mask, label="回传" + txt)
        if getattr(self, "m_view", None) is not None and self.idx_view_mask is not None:
            self.m_view.entryconfigure(self.idx_view_mask, label="右图：" + txt)
        if getattr(self, "show_cb", None) is not None:
            self.show_cb.configure(values=[label for _, label in self.show_choices])
        self.var_show_label.set(self._show_label(self.var_show.get()))

    def _load_pattern(self, name):
        """下拉框里选一项：三个内置图样，或 samples/ 里的默认测试图。

        两处都走 `imageio.load_pattern`，所以拿到的就是真正会被发出去的
        786432 字节（samples 里的图在这里被拉伸到 512x512）。
        """
        try:
            self.image, note = imageio.load_pattern(name)
        except KeyError:
            return                          # 下拉框里不会有这种名字，忽略
        except Exception as e:                          # noqa: BLE001
            messagebox.showerror("读图失败", "读 '%s' 失败：%s" % (name, e))
            return
        self._after_load(imageio.SAMPLES.get(name) if name not in imageio.PATTERNS else None, name)
        self._status("已载入 '%s'（%d 字节）—— %s" % (name, len(self.image), note))

    def _load_path(self, path):
        """读入一个图片文件并显示到左侧（任意格式/尺寸，会自动拉伸为 512x512）。"""
        try:
            data, note = imageio.load_image(path)
        except Exception as e:
            messagebox.showerror("读图失败", str(e))
            return
        self.image = data
        self._after_load(path, os.path.basename(path))
        self._status("已载入 %s —— %s" % (os.path.basename(path), note))

    def _after_load(self, path, name):
        """换图之后的共同收尾：下拉框显示当前图名（载入文件时显示文件名），丢掉上一张图的点击纠错和回图。"""
        self.var_pattern.set(name)
        self.inter = None
        self._draw_clicks()
        self._set_orig(path, name)
        self._show_left()
        self._clear_right()

    def _load_file(self):
        path = filedialog.askopenfilename(
            title="选择图片（任意格式/尺寸，会自动拉伸为 512x512 RGB888）",
            filetypes=[("常见图片 (PNG/JPG/BMP/TIF/WebP)", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp"),
                       ("Netpbm (PPM/PGM/PBM/PNM/PAM)", "*.ppm *.pgm *.pbm *.pnm *.pam"),
                       ("裸 RGB888 (Xilinx .rgb/.raw/.bin)", "*.rgb *.raw *.bin"),
                       ("所有文件", "*.*")])
        if path:
            self._load_path(path)

    def _save_reply(self):
        """把右边那幅图存成文件（格式由扩展名决定）。"""
        if not self.view_images:
            messagebox.showinfo("保存回图", "还没有收到回图，先发送一帧。")
            return
        name = self.showing or self._pick_show("auto")
        reply = self.view_images.get(name)
        if reply is None:
            messagebox.showinfo("保存回图", "本帧没有回传图像。")
            return
        # 勾着"数据统计"时, 顺便把统计一起导出(同名 .json)
        extra = {"stats_images": sorted(self.reply_images), "showing": name}
        if self.ai_stats:
            extra["ai"] = self.ai_stats
        path = filedialog.asksaveasfilename(
            title="保存回图（当前显示的是 %s）" % name,
            initialfile="reply_%s.png" % name,
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png"),
                       ("PPM (P6 二进制, 无损)", "*.ppm"),
                       ("PGM (P5 灰度)", "*.pgm"),
                       ("PBM (P4 二值)", "*.pbm"),
                       ("PAM (P7, 带表头)", "*.pam"),
                       ("裸 RGB888", "*.rgb"),
                       ("JPEG 图片", "*.jpg"),
                       ("其它 (由 Pillow 决定)", "*.*")])
        if not path:
            return
        try:
            imageio.save_image(path, reply)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        self._status("回图(%s)已保存到 %s" % (name, path))
        if self.ai_stats:
            try:
                import json
                with open(os.path.splitext(path)[0] + "_stats.json", "w",
                          encoding="utf-8") as fh:
                    json.dump(extra, fh, ensure_ascii=False, indent=1)
            except OSError:
                pass

    def _show_left(self):
        if self.image is None:
            return
        self.photo_left = imageio.make_photo(tk, self.image)
        self.cv_left.itemconfigure(self.cv_img, image=self.photo_left)
        self._draw_roi()

    # ---- ROI：在左图上拖框；有框时 AI 只在框附近的裁剪窗口上算 ----
    @staticmethod
    def _clamp_xy(e):
        return (max(0, min(proto.IMG_WIDTH, e.x)), max(0, min(proto.IMG_HEIGHT, e.y)))

    def _ui_model(self):
        """当前选中的模型（proto.MODELS 的一项）。"""
        label = self.var_ai_model.get()
        return next((m for m in proto.MODELS if m["label"] == label), proto.MODELS[0])

    def _on_model_change(self):
        """换模型：门限 / 分辨率换成该模型的默认值；不支持的 ROI / 形态指标关掉。"""
        m = self._ui_model()
        self.var_ai_thr.set(str(m["thr"]))
        self.var_ai_res.set(str(m["res"]))
        notes = []
        if not m["morph"] and self.var_f_morph.get():
            self.var_f_morph.set(False)
            notes.append("形态指标只对眼底血管有意义，已关闭")
        if not m["roi"] and self.roi is not None:
            self.roi = None
            self._draw_roi()
            notes.append("该模型在 %d×%d 上训练，不支持 ROI，已清除" % (m["res"], m["res"]))
        if not m.get("inter") and self.var_inter.get():
            self.var_inter.set(False)
            self.inter = None
            self._draw_clicks()
            notes.append("该任务没有点击纠错模型，已关闭点击纠错")
        self._status("AI 模型：%s（门限 %d，分辨率 %d）%s"
                     % (m["label"], m["thr"], m["res"], "；" + "；".join(notes) if notes else ""))

    def _roi_press(self, e):
        if self._inter_ready():
            self._inter_click(e, True)
            return
        if not self._ui_model()["roi"]:
            self._status("当前模型（%s）不支持 ROI 局部推理" % self._ui_model()["label"])
            return
        self.roi_drag_start = self._clamp_xy(e)

    def _roi_drag(self, e):
        if self.roi_drag_start is None or self._inter_ready():
            return
        (x0, y0), (x1, y1) = self.roi_drag_start, self._clamp_xy(e)
        self._draw_roi((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))

    def _roi_release(self, e):
        if self.roi_drag_start is None or self._inter_ready():
            return
        (x0, y0), (x1, y1) = self.roi_drag_start, self._clamp_xy(e)
        self.roi_drag_start = None
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w >= ROI_MIN_SIZE and h >= ROI_MIN_SIZE:      # 太小的当作误点，保持原来的框
            self.roi = (min(x0, x1), min(y0, y1), w, h)
            if self._zoom_mag() > 1:
                self._status("已选框：「发送」在原图上按 %g 倍分块重新推理（右键或 Esc 清除）" % self._zoom_mag())
            else:
                self._status("已选 ROI：下一帧 AI 只算框附近（右键或 Esc 清除）")
        self._draw_roi()

    def _roi_clear(self):
        self.roi = None
        self.roi_drag_start = None
        self._draw_roi()
        self._status("已清除 ROI：下一帧恢复整图推理")

    def _draw_roi(self, box=None):
        """左图上画 ROI 框（黄）和板端实际计算的裁剪窗口（青色虚线）。"""
        self.cv_left.delete("roi")
        box = box or self.roi
        if box is None:
            self.var_roi_note.set("左键拖框选 ROI：AI 只算框附近，结果与整图一致")
            return
        x, y, w, h = box
        if w < 1 or h < 1:
            return
        mag = self._zoom_mag()
        if mag > 1 and self.orig_rgb is not None:            # 框选放大：画品红框，说明会在原图上重新推理
            te = sys.modules[__name__]
            self.cv_left.create_rectangle(x, y, x + w - 1, y + h - 1, outline="#ff3cf0", width=2, tags="roi")
            bigwin = sys.modules[__name__]
            h0, w0 = self.orig_rgb.shape[:2]
            m_inf, extra = te.effective_mag(mag, w0, bigwin.load_config()["cap"])
            s = te.scale_for_mag(m_inf, w0)
            (lw, lh), (W, H) = te.level_size(w0, h0, s)
            lb = te.box_to_level(self._roi_to_orig(box), s, W, H)
            n = len(te.plan(lb, W, H))
            self.var_roi_note.set("框 %d×%d → 原图 %.0f×%.0f → %.3g 倍层上 %d×%d、%d 块%s（「发送」= 在大图精查窗口重新推理）"
                                  % (w, h, w * w0 / 512.0, h * h0 / 512.0, m_inf, lb[2], lb[3], n,
                                     "，再插值放大 %.2f 倍显示（原图分辨率不够）" % extra if extra > 1 else ""))
            return
        cx, cy, cw, ch = proto.roi_crop(x, y, w, h)
        self.cv_left.create_rectangle(cx, cy, cx + cw - 1, cy + ch - 1, outline="#00e5ff",
                                      dash=(4, 3), tags="roi")
        self.cv_left.create_rectangle(x, y, x + w - 1, y + h - 1, outline="#ffd400",
                                      width=2, tags="roi")
        self.var_roi_note.set("ROI %d×%d @(%d,%d) → 板端算 %d×%d（占整图 %.1f%%）"
                              % (w, h, x, y, cw, ch,
                                 100.0 * cw * ch / (proto.IMG_WIDTH * proto.IMG_HEIGHT)))

    # ---- 框选放大（部分 C）：> 1 倍时框映射回原图，在大图精查窗口里按该倍数分块重新推理 ----
    def _set_orig(self, path, name):
        """记下原图（任意尺寸）。读不了（裸 RGB、内置图样）就用发出去的 512 帧本身。"""
        import numpy as np
        self.orig_name = name
        self.orig_rgb = None
        if path:
            try:
                from PIL import Image
                self.orig_rgb = np.asarray(Image.open(path).convert("RGB"))
            except Exception:                              # noqa: BLE001 - 退回 512 帧
                self.orig_rgb = None
        if self.orig_rgb is None and self.image is not None:
            self.orig_rgb = np.frombuffer(self.image, np.uint8).reshape(proto.IMG_HEIGHT, proto.IMG_WIDTH, 3)

    def _zoom_mag(self):
        """框选放大的倍数（1 = 老 ROI）。"""
        v = self.var_zoom.get() if hasattr(self, "var_zoom") else ZOOM_1X
        return 1.0 if v == ZOOM_1X else float(v.split("×")[0])

    def _roi_to_orig(self, box):
        """512 帧上的框 -> 原图坐标（帧是原图直接拉伸到 512x512 的）。"""
        h0, w0 = self.orig_rgb.shape[:2]
        fx, fy = w0 / float(proto.IMG_WIDTH), h0 / float(proto.IMG_HEIGHT)
        x, y, w, h = box
        return (x * fx, y * fy, w * fx, h * fy)

    def _open_bigwin(self, box=None, mag=None):
        """大图精查窗口：box = 原图坐标的框（None = 只看总览），mag = 放大倍数。"""
        self._stop_repeat()
        if self.orig_rgb is None:
            self._status("先载入一张图")
            return None
        bigwin = sys.modules[__name__]
        win = getattr(self, "big_win", None)
        if uistyle.alive(win) and win.key == bigwin.rgb_key(self.orig_rgb):
            uistyle.bring_up(win.top)
            if box is not None:
                win.zoom_to(box, mag)
            return win
        if uistyle.alive(win):
            win.close()                                  # 换了图：旧窗口关掉（板上任务在途时等它回来再销毁）
        self.big_win = bigwin.BigWindow(self, self.orig_rgb, self.orig_name or "图像", box0=box, mag=mag)
        return self.big_win

    def _clear_right(self):
        self.photo_right = None
        self.cv_right.itemconfigure(self.cv_right_img, image="")
        self.cv_right.itemconfigure(self.cv_right_hint, state="normal")
        self.var_reply_note.set("")
        # 换了图就丢掉上一帧的 logit，免得新图配旧的热力图 / 叠加
        self.view_images = {}
        self.local_views = set()
        self.logit_plane = None
        self.heat_cache = None
        self.verify_note = ""
        self.frame_roi = None
        self._update_conf_note()

    def _on_repeat_toggle(self):
        self.repeat = self.var_repeat.get()
        self.done_times = []            # 实测帧率从这次打开连续模式重新算
        if not self.repeat and self.repeat_job is not None:
            self.root.after_cancel(self.repeat_job)    # 关掉时把排好的下一帧也撤掉
        self.repeat_job = None
        self._sync_send_btn()
        if self.repeat and not self.busy:
            self._on_send()

    def _on_send_button(self):
        """顶栏大按钮：平时是「发送」，连续发送时变成「停止」。"""
        if self.repeat:
            self.var_repeat.set(False)
            self._on_repeat_toggle()
        else:
            self._on_send()

    def _sync_send_btn(self):
        if self.repeat:
            self.btn_send.configure(text="停止 ■", state="normal", bg=C_STOP,
                                    activebackground=C_STOP)
        else:
            self.btn_send.configure(text="发送 ▶", state="disabled" if self.busy else "normal",
                                    bg=C_ACCENT, activebackground=C_ACCENT_D)

    def _set_busy(self, busy):
        self.busy = busy
        self._sync_send_btn()

    def _set_link(self, ok, text):
        self.lbl_link.configure(fg=C_GREEN if ok else C_STOP)
        self.var_link.set(text)

    # ---- 专项工作站子窗口：和主界面共用板子连接，所以先停掉连续发送；已开着就拿到前面 ----
    def _stop_repeat(self):
        if self.repeat:
            self.var_repeat.set(False)
            self._on_repeat_toggle()

    def _open_tool(self, attr, make):
        self._stop_repeat()
        win = getattr(self, attr, None)
        if uistyle.alive(win):
            uistyle.bring_up(win.top)
            return win
        try:
            win = make()
        except ImportError:                    # 缺 numpy / scipy / OpenCV：子窗口自己已经弹框说明
            return None
        setattr(self, attr, win)
        return win

    def _open_sequence(self):                  # 序列跟踪（F3）
        return self._open_tool("seq_win", lambda: sequence.SeqWindow(self))

    # ---- 点击纠错（F2）：左键 = 这里是目标，右键 = 这里不是；每次点击 FPGA 重算一次 ----
    def _inter_ready(self):
        return bool(self.var_inter.get() and self._ui_model().get("inter") and self.inter is not None)

    def _on_inter_toggle(self):
        m = self._ui_model()
        if self.var_inter.get() and not m.get("inter"):
            self.var_inter.set(False)
            self._status("当前任务（%s）没有点击纠错模型" % m["label"])
            return
        if self.var_inter.get():
            self.var_f_logit.set(True)
            self._status("点击纠错：先发送一帧拿到自动分割，然后在左图上左键点漏掉的、右键点多出的；Ctrl+Z 撤销")
        else:
            self.inter = None
            self._draw_clicks()

    def _right_click(self, e):
        if self._inter_ready():
            self._inter_click(e, False)
        else:
            self._roi_clear()

    def _inter_click(self, e, positive):
        if self.busy:
            self._status("上一帧还在算，稍等再点")
            return
        x, y = self._clamp_xy(e)
        clicks = self.inter.clicks + [(x, y, positive)]
        self._draw_clicks(clicks)
        self._on_send(inter=(interactive.compose(self.image, self.inter.prior, clicks), clicks))

    def _update_inter(self, images, ai_stats):
        """收到一帧后：纠错帧接到会话上；普通帧（自动分割）作为新一轮纠错的起点。"""
        if self.inflight_inter is not None and self.inter is not None:
            prior = interactive.prior_from_plane(self.logit_plane, self.frame_ai_thr)
            self.inter.push(prior, self.inflight_inter, images, ai_stats)
            self.var_show.set("overlay")
        elif self.var_inter.get() and self._ui_model().get("inter") and self.logit_plane is not None:
            prior = interactive.prior_from_plane(self.logit_plane, self.frame_ai_thr)
            self.inter = interactive.Session(prior, images, ai_stats)
        self.inflight_inter = None
        self._draw_clicks()

    def _inter_restore(self, state):
        if state is None:
            self._status("已经是自动分割的结果，没有可撤销的点击")
            return
        images, ai_stats = state
        self.reply_images, self.ai_stats = images, ai_stats
        self._load_logits(images)
        self._rebuild_views()
        self._render_right()
        self._draw_clicks()
        self._status("纠错：当前 %d 次点击（本地恢复，未重跑 FPGA）" % len(self.inter.clicks))

    def _inter_undo(self):
        if self.inter is not None and not self.busy:
            self._inter_restore(self.inter.undo())

    def _inter_reset(self):
        if self.inter is not None and not self.busy:
            self._inter_restore(self.inter.reset())

    def _draw_clicks(self, clicks=None):
        self.cv_left.delete("clicks")
        if clicks is None:
            clicks = self.inter.clicks if self.inter is not None else []
        for x, y, positive in clicks:
            color = "#00e000" if positive else "#ff3030"
            self.cv_left.create_oval(x - 6, y - 6, x + 6, y + 6, outline=color, width=2, tags="clicks")
            self.cv_left.create_line(x - 4, y, x + 4, y, fill=color, width=2, tags="clicks")
            if positive:
                self.cv_left.create_line(x, y - 4, x, y + 4, fill=color, width=2, tags="clicks")

    def _fast_flags(self, flags, roi, inter=None):
        """WS_OPT_V2「少回图」：预处理图 / 掩膜 / 叠加图 改由上位机本地生成，回传只留 logit 一幅。

        依据（2026-11-19 上板实测，6 张真实 512 图逐字节 PASS，脚本 04_verify/verify_noimg.py）：
          * 板端回的 proc **就是发出去的那张原图**（mode 5 原图直通，md5 相同）；
          * mask / overlay 用本文件的 mask_rgb / overlay_rgb 算，与板端回传**逐字节相同**
            （`_load_logits` 里那条「与板端逐字节一致 ✓」自检一直在跑这件事）。
        回传 3.15 MB -> 0.79 MB，一次往返 525 -> 395 ms。条件不满足就原样返回：
        ROI 帧（底图与裁剪相关）、老板端（不认 b5 就没有 logit 图）、
        没勾掩膜/叠加（本来就省不到）、点击纠错帧（历史栈里要存原样）。
        环境变量 WS_NO_FASTIMG=1 可整体关掉。
        """
        self.fast_noimg = False
        if os.environ.get("WS_NO_FASTIMG"):
            return flags
        if roi is not None or inter is not None or self.inter is not None:
            return flags
        ver = self.board_stat_version
        if ver is not None and ver < proto.AI_STAT_VERSION_LOGIT:
            return flags
        if not flags & (proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_OVER):
            return flags
        self.fast_noimg = True
        return ((flags & ~(proto.FLAG_ECHO_PROC | proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_OVER))
                | proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS)

    def _on_send(self, inter=None):
        """发一帧。inter = (要发的合成图, 点击列表) 时发点击纠错帧（交互模型、只回掩膜 + logit）。"""
        if self.busy or self.image is None:
            return
        if inter is None and self.roi is not None and self._zoom_mag() > 1:
            self._open_bigwin(self._roi_to_orig(self.roi), self._zoom_mag())    # 框选放大 > 1 倍：分块重新推理
            return
        try:
            host = self.var_host.get().strip()
            port = int(self.var_port.get().strip())
        except ValueError:
            messagebox.showerror("参数错误", "端口必须是数字")
            return

        self._set_busy(True)
        self._status("正在发送并等待处理后的回图…")
        self.var_total.set("…")
        self.var_detail.set("")

        # 控制头里的模式编号：界面无入口，固定「5 原图直通」（PL 预处理已移除，协议字段保留）
        mode = self._current_mode()

        flags = 0
        if self.var_f_proc.get():
            flags |= proto.FLAG_ECHO_PROC
        if self.var_f_mask.get():
            flags |= proto.FLAG_ECHO_MASK
        if self.var_f_over.get():
            flags |= proto.FLAG_ECHO_OVER
        if self.var_f_stats.get():
            flags |= proto.FLAG_STATS
        if self.var_f_logit.get():
            # 要 logit 必须同时要统计块：上位机靠它的 version 判断板端认不认 b5
            flags |= proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS
            self.var_f_stats.set(True)
        if self.var_f_morph.get():
            # 形态指标块跟在统计块后面，板端也只在带了统计块时才发
            flags |= proto.FLAG_MORPH | proto.FLAG_STATS
            self.var_f_stats.set(True)

        roi = self.roi
        if roi is not None and self.board_stat_version is not None \
                and self.board_stat_version < proto.AI_STAT_VERSION_ROI:
            roi = None                     # 老板端不认 v5 头，发了会被当成坏帧
            self._status("板端程序不支持 ROI（统计块 version=%d），本帧按整图推理"
                         % self.board_stat_version)
        if roi is not None:
            # ROI 帧必须让板子跑 AI，并带统计块（里面有板端实际计算的裁剪尺寸）
            if not flags & (proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_OVER | proto.FLAG_ECHO_LOGIT):
                flags |= proto.FLAG_ECHO_OVER
                self.var_f_over.set(True)
            flags |= proto.FLAG_STATS
            self.var_f_stats.set(True)
        image = self.image
        self.inflight_inter = None
        if inter is not None:
            image, self.inflight_inter = inter
            flags = proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS
            roi = None
        elif self.var_inter.get() and self._ui_model().get("inter"):
            flags |= proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS     # 自动结果要当纠错的起点
        # WS_OPT_V2 少回图：本地生成用的底图 = 本帧真正发出去的那份字节
        self.inflight_image = image
        flags = self._fast_flags(flags, roi, inter)
        self.inflight_roi = roi
        if flags == 0:
            self._status("「回传内容」至少要勾一项（否则板子不会回任何东西）")
            self._set_busy(False)
            return

        ai_thr = self._ui_ai_thr()
        self.inflight_ai_thr = ai_thr      # 本帧板端用的门限(同一时刻只有一帧在途)
        # 板端 PL 预处理已移除，AI 输入恒为原始 RGB（aiparam 的 src 字段照旧发）
        ai_src = proto.AIPARAM_SRC_RAW
        try:
            ai_res = int(self.var_ai_res.get())
        except ValueError:
            ai_res = 512
        model = self._ui_model()
        if inter is not None:
            model = proto.MODELS[model["inter"]]
            ai_thr, ai_res = model["thr"], model["res"]
            self.inflight_ai_thr = ai_thr
        self.inflight_model = model["index"]
        aiparam = proto.make_aiparam(ai_src, ai_thr, ai_res, model["index"])

        # 阈值分割的门限(只在 v4 控制头里带; 其它模式板子会忽略)
        try:
            thr = int(self.var_thr.get())
        except ValueError:
            thr = proto.CTRL_THR_DEFAULT
        thr = max(proto.CTRL_THR_MIN, min(proto.CTRL_THR_MAX, thr))
        self.var_thr.set(str(thr))
        self.last_thr = thr

        self.inflight_key = (hash(image), ai_src, mode if ai_src == proto.AIPARAM_SRC_PROC
                             else None)    # 哪张图、什么输入：判断能不能拿整图结果核对 ROI
        threading.Thread(target=self._worker,
                         args=(host, port, image, mode, flags, aiparam, thr, roi),
                         daemon=True).start()

    # ---- 后台线程：只做网络, 结果丢进 queue ----
    def _worker(self, host, port, image, mode, flags, aiparam, thr, roi):
        try:
            # round_trip 内部自己建连(板端每连接可连收多帧), 这里只更新目标
            self.link.host = host
            self.link.port = port
            images, timings, rowstats, ai_stats = self.link.round_trip(
                image, mode, flags=flags, aiparam=aiparam, thr=thr, roi=roi)
            self.queue.put(("ok", images, timings, rowstats, ai_stats, mode, flags))
        except Exception as e:
            if roi is not None and self.board_stat_version is None:
                e = "%s（发的是 ROI 帧；如果板端还是旧程序，它不认 v5 头。先清除 ROI 发一帧整图）" % e
            self.queue.put(("err", str(e)))

    # ---- GUI 线程：轮询 queue ----
    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "ok":
                    self._on_result(item[1], item[2], item[3], item[4], item[5], item[6])
                else:
                    self._on_error(item[1])
        except queue.Empty:
            pass
        # WS_OPT_V1 空转时 50 ms 足够省电；有任务在途时 50 ms 的轮询粒度就是白等
        # （平均多等 25 ms）。按 busy 自适应，实测整帧墙钟少 ~20 ms。
        self.root.after(5 if self.busy else 50, self._poll)

    def _on_error(self, msg):
        self.var_total.set("—")
        self.var_fps.set("—")
        self.var_fps_note.set("")
        self.done_times = []
        self.var_detail.set("")
        self._status("出错：%s" % msg)
        self.link.close()
        self._set_link(False, "连接失败")
        if self.repeat:
            self.repeat = False
            self.var_repeat.set(False)
        self._set_busy(False)

    def _pick_show(self, wanted):
        """决定右图显示哪一幅: auto = 按 叠加/掩膜/热力图 的优先级取第一幅有的
        （AI 结果优先；板端若还带 proc 流，排在掩膜之后兜底）。
        """
        if wanted != "auto" and wanted in self.view_images:
            return wanted
        for name in ("overlay", "mask", "proc", "heat"):
            if name in self.view_images:
                return name
        return None

    def _render_right(self):
        """按「显示」的选择把最近一帧的图画到右边；返回实际显示的图名，没有图时返回 None。"""
        name = self._pick_show(self.var_show.get())
        if name is None:
            self.var_reply_note.set("没有可显示的回传图")
            return None
        reply = self.view_images[name]
        self.last_reply = reply
        self.showing = name
        if self.frame_roi is not None:          # ROI 帧：框外没有算，把框描出来
            reply = self._with_box(reply, self.frame_roi)
        self.photo_right = imageio.make_photo(tk, reply)
        self.cv_right.itemconfigure(self.cv_right_img, image=self.photo_right)
        self.cv_right.itemconfigure(self.cv_right_hint, state="hidden")
        if name in self.local_views:
            src = ("本地生成（板端只回 logit 一幅）" if self.fast_noimg
                   else "本地按门限 %d 重算，未重跑 FPGA" % self._ui_ai_thr())
        elif name in ("heat", "uncertain"):
            src = "由板端 logit 本地生成"
        else:
            src = "板端回传"
        self.var_reply_note.set("%s · %s" % (self._show_label(name), src))
        return name

    # ---- 置信度：板端回传 logit 后，换门限只在本地重算 ----
    def _ui_ai_thr(self):
        """界面上的 AI 判定门限，夹到 int8 范围。"""
        try:
            return max(-127, min(127, int(float(self.var_ai_thr.get().strip()))))
        except ValueError:
            return proto.AIPARAM_THR_DEFAULT

    def _ui_band(self):
        try:
            return max(0, min(127, int(self.var_band.get().strip())))
        except ValueError:
            return UNSURE_BAND_DEFAULT

    def _load_logits(self, images):
        """从本帧回传里取出 logit 平面和本地叠加用的底图；没有 logit 就清空。"""
        self.heat_cache = None
        self.verify_note = ""
        if "logit" not in images:
            self.logit_plane = None
            return
        self.logit_plane = confidence.logit_plane(images["logit"])
        self.frame_ai_thr = self.inflight_ai_thr
        # 概率换算按这一帧实际用的模型（v5 板端在统计块里报；老板端只有眼底）
        st_model = (self.ai_stats or {}).get("model")
        self.frame_model = proto.model_by_index(st_model)
        confidence.set_scale(self.frame_model["scale"])
        self.frame_ai_res = (self.ai_stats or {}).get("ai_res") or 512
        # 板端叠加的底图是预处理结果（直通模式时就是原图）；没回传预处理图时只能退用原图
        if "proc" in images:
            self.logit_base, self.logit_base_note = images["proc"], ""
        else:
            self.logit_base = self.image
            self.logit_base_note = "底图用原图（未回传预处理图）"
        # WS_OPT_V1 自检：板端门限下本地复算的掩膜 / 叠加图应与板端回传的逐字节一致。
        # 复算一次约 3.8 ms，而结论只跟 (门限, 模型, 分辨率) 有关，没必要每帧都算：
        # 1 秒内只复算一次，其余帧沿用上次结论（界面上的提示不会闪）。
        if time.perf_counter() - self._verify_t >= 1.0:
            self._verify_t = time.perf_counter()
            checks = []
            if "mask" in images:
                checks.append(confidence.mask_rgb(self.logit_plane, self.frame_ai_thr)
                              == images["mask"])
            if "overlay" in images and "proc" in images:
                checks.append(confidence.overlay_rgb(images["proc"], self.logit_plane,
                                                     self.frame_ai_thr) == images["overlay"])
            if checks:
                self._verify_last = ("与板端逐字节一致 ✓" if all(checks)
                                     else "与板端回传不一致 ✗")
        self.verify_note = self._verify_last

    def _show_morph(self, flags, ai_stats):
        """板端算的血管形态指标。长度 / 管径换算成 512 尺度的像素（低分辨率档按网格放大）。"""
        m = (ai_stats or {}).get("morph")
        if m is None:
            if flags & proto.FLAG_MORPH and ai_stats is not None \
                    and ai_stats["version"] < proto.AI_STAT_VERSION_MORPH:
                self.var_morph.set("形态：板端程序不支持（统计块 version=%d）" % ai_stats["version"])
            elif not flags & proto.FLAG_MORPH:
                self.var_morph.set("形态：勾「形态指标」后由板端计算")
            else:
                self.var_morph.set("形态：本帧板端没有算出")
            return
        f = 1.0 if self.frame_roi is not None else proto.IMG_WIDTH / max(1, m["w"])
        where = ("ROI %d×%d" % (m["w"], m["h"]) if self.frame_roi is not None
                 else "%d×%d" % (m["w"], m["h"]))
        self.var_morph.set(
            "形态(%s, 门限 %d)：总长 %.0f px  管径 %.2f px  分支 %d  端点 %d  段 %d  迂曲度 %.3f  "
            "分形维数 %.3f  FOV 密度 %.1f%%(%s)  PS %.1f ms"
            % (where, self.frame_ai_thr, m["length"] * f, m["caliber"] * f, m["branches"],
               m["endpoints"], m["segments"], m["tortuosity"], m["fractal"], m["density_pct"],
               "/".join("%.1f" % q for q in m["quad_pct"]), m["run_us"] / 1000.0))

    def _check_roi_against_full(self):
        """实板核对“ROI 局部推理 == 整图推理”：同一张图、同样输入的上一帧整图 logit 留作参照，
        ROI 帧回来后在框内逐字节比。没回传 logit（没勾「置信度」）时没法比，只给提示。"""
        self.roi_verify_note = ""
        if self.logit_plane is None:
            if self.frame_roi is not None:
                self.roi_verify_note = "勾「置信度」并先发一帧整图，可核对 ROI 与整图逐值一致"
            return
        if self.frame_roi is None:
            if self.frame_ai_res == proto.IMG_WIDTH:       # 只有全分辨率整图能当参照
                self.full_ref = (self.inflight_key, self.logit_plane)
            return
        if self.full_ref is None or self.full_ref[0] != self.inflight_key:
            self.roi_verify_note = "先对同一张图发一帧整图（不框 ROI），才能核对 ROI 与整图一致"
            return
        n, bad = confidence.roi_compare(self.logit_plane, self.full_ref[1], self.frame_roi)
        self.roi_verify_note = ("ROI 与整图推理逐值一致 ✓（%d 像素）" % n if bad == 0
                                else "ROI 与整图推理有 %d/%d 像素不同 ✗" % (bad, n))

    @staticmethod
    def _with_box(rgb, box, color=(255, 212, 0)):
        """在 RGB 图上描一个 2 像素宽的框（只用于显示）。"""
        x, y, w, h = box
        out = bytearray(rgb)
        px = bytes(color)
        W = proto.IMG_WIDTH
        for yy in (y, y + 1, y + h - 2, y + h - 1):
            if 0 <= yy < proto.IMG_HEIGHT:
                out[(yy * W + x) * 3:(yy * W + x + w) * 3] = px * w
        for yy in range(y, y + h):
            for xx in (x, x + 1, x + w - 2, x + w - 1):
                if 0 <= xx < W:
                    out[(yy * W + xx) * 3:(yy * W + xx) * 3 + 3] = px
        return bytes(out)

    def _rebuild_views(self):
        """回传图 + 由 logit 本地生成的视图。门限与板端相同时，掩膜 / 叠加用板端原图。"""
        views = {k: v for k, v in self.reply_images.items() if k != "logit"}
        self.local_views = set()
        plane = self.logit_plane
        if plane is not None:
            thr, band = self._ui_ai_thr(), self._ui_band()
            same = (thr == self.frame_ai_thr)
            if not same or "mask" not in views:
                views["mask"] = confidence.mask_rgb(plane, thr)
                self.local_views.add("mask")
            if not same or "overlay" not in views:
                views["overlay"] = confidence.overlay_rgb(self.logit_base, plane, thr)
                self.local_views.add("overlay")
            # WS_OPT_V1 热力图 / 不确定带一帧要 6 ms 上下，而界面上一次只显示其中一幅：
            # 改成点开才生成（_LazyViews），省掉每帧白算。
            views = _LazyViews(views)
            if self.heat_cache is None:
                views.later("heat", lambda p=plane: confidence.heat_rgb(p))
            else:
                views["heat"] = self.heat_cache
            views.later("uncertain",
                        lambda b=self.logit_base, p=plane, t=thr, d=band:
                        confidence.uncertain_rgb(b, p, t, d))
        self.view_images = views
        self._update_conf_note()

    def _update_conf_note(self):
        thr, band = self._ui_ai_thr(), self._ui_band()
        head = "门限 %d ≈ 概率 %.2f" % (thr, confidence.probability(thr))
        if self.logit_plane is None:
            self.var_conf_note.set(head + "\n勾「置信度」后，拖门限只在本地重算")
            return
        st = confidence.stats(self.logit_plane, thr, band, self.frame_ai_res, roi=self.frame_roi)
        target = getattr(self, "frame_model", proto.MODELS[0])["target"]
        parts = [head, "%s %.2f%%" % (target, st["vessels_pct"]), "不确定 %.2f%%" % st["unsure_pct"]]
        if thr != self.frame_ai_thr:
            parts.append("本地重算（板端本帧 %d）" % self.frame_ai_thr)
        elif self.verify_note:             # 一致性只对板端本帧的门限成立
            parts.append(self.verify_note)
        if self.logit_base_note:
            parts.append(self.logit_base_note)
        self.var_conf_note.set("  |  ".join(parts))

    def _schedule_conf(self):
        """门限 / 不确定带变化：合并连续的拖动事件，空闲时重算一次。"""
        if self.conf_job is not None:
            self.root.after_cancel(self.conf_job)
        self.conf_job = self.root.after(30, self._on_conf_change)

    def _on_conf_change(self):
        self.conf_job = None
        if self.logit_plane is None:
            self._update_conf_note()
            return
        self._rebuild_views()
        if self.showing in AI_VIEWS or self.var_show.get() in AI_VIEWS:
            self._render_right()

    def _update_fps(self, total_ms):
        """连续模式：按最近几帧的完成时刻实测帧率；单帧：按往返耗时折算。"""
        if self.repeat:
            self.done_times.append(time.perf_counter())
            del self.done_times[:-(FPS_WINDOW + 1)]
        else:
            self.done_times = []
        if len(self.done_times) >= 2:
            n = len(self.done_times) - 1
            span = max(self.done_times[-1] - self.done_times[0], 1e-6)
            self.var_fps.set("%.2f fps" % (n / span))
            self.var_fps_note.set("连续实测：近 %d 帧，含发送间隔 %s ms"
                                  % (n, self.var_interval.get().strip()))
        else:
            self.var_fps.set("%.2f fps" % (1000.0 / max(total_ms, 1e-3)))
            self.var_fps_note.set("按单帧往返折算（勾「连续」可实测）")

    def _on_result(self, images, tim, rowstats, ai_stats, mode, flags):
        if self.fast_noimg and "proc" not in images and self.inflight_image is not None:
            # WS_OPT_V2：这一帧没要预处理图 —— 补上发出去的原图（mode 5 直通，两者逐字节相同），
            # 下游（本地叠加、纠错历史栈、保存回图）看到的字典结构与老口径完全一样。
            images["proc"] = self.inflight_image
        self.reply_images = images
        self.reply_stats = rowstats
        self.ai_stats = ai_stats
        if ai_stats is not None:
            self.board_stat_version = ai_stats["version"]
        self.frame_ai_thr = self.inflight_ai_thr          # 本帧板端用的门限（形态指标也按它算）
        # 板端真的按 ROI 算了（统计块里有裁剪尺寸），这一帧才算 ROI 帧
        self.frame_roi = (self.inflight_roi
                          if ai_stats is not None and ai_stats.get("crop_w") else None)

        self._load_logits(images)
        self._update_inter(images, ai_stats)
        self._check_roi_against_full()
        self._rebuild_views()
        name = self._render_right()

        self.var_total.set("%.1f ms" % tim["total_ms"])
        self._update_fps(tim["total_ms"])
        if ai_stats is None:
            self.var_infer.set("—")
            self.var_infer_note.set("勾选「数值统计」后显示")
        elif ai_stats["ai_us"] == 0:
            self.var_infer.set("—")
            self.var_infer_note.set("本帧板端没有运行 AI")
        elif self.frame_roi is not None:
            self.var_infer.set("%.1f ms" % (ai_stats["ai_us"] / 1000.0))
            self.var_infer_note.set("ROI：只算 %d×%d（占整图 %.1f%%），含搬运与计算"
                                    % (ai_stats["crop_w"], ai_stats["crop_h"],
                                       100.0 * ai_stats["crop_w"] * ai_stats["crop_h"]
                                       / (proto.IMG_WIDTH * proto.IMG_HEIGHT)))
        else:
            self.var_infer.set("%.1f ms" % (ai_stats["ai_us"] / 1000.0))
            self.var_infer_note.set("%d×%d，含 PS 搬运与 PL 计算"
                                    % (ai_stats["ai_res"], ai_stats["ai_res"]))
        st = rowstats.get(name, {}) if name else {}
        self.var_detail.set(
            "模式 %d %s  |  flags=0x%04X %s  |  阈值 %d\n发送 %.1f ms  |  回传 %.1f ms  |  行 %s  |  CRC错 %s"
            % (mode, proto.MODE_NAMES.get(mode, "?"), flags,
               "+".join(proto.expected_images(flags, ai_stats)) or "无图", self.last_thr,
               tim["send_ms"], tim["reply_ms"],
               ("%d/%d" % (st["rows_ok"], proto.PKT_COUNT)) if st else "—",
               st.get("crc_errors", "—")))

        self.hist.append(tim["total_ms"])
        if len(self.hist) > 1000:
            del self.hist[:len(self.hist) - 1000]
        n = len(self.hist)
        self.var_stats.set(
            "统计(n=%d)：本次 %.1f ms   最小 %.1f   最大 %.1f   平均 %.1f"
            % (n, tim["total_ms"], min(self.hist), max(self.hist),
               sum(self.hist) / n))
        self.var_hist.set("最近 %d 次：%s"
                          % (min(n, HISTORY_MAX),
                             "  ".join("%.0f" % v for v in self.hist[-HISTORY_MAX:])))

        if ai_stats is not None:
            self.var_ai.set(
                "AI(%s，%s)：%s %d/%d = %.1f%%   连通域 %d   logit[%d..%d]   板端整网 %d µs   灰度[%d..%d]"
                % (proto.model_by_index(ai_stats.get("model"))["label"],
                   "ROI %d×%d" % self.frame_roi[2:] if self.frame_roi
                   else "%d×%d" % (ai_stats["ai_res"], ai_stats["ai_res"]),
                   proto.model_by_index(ai_stats.get("model"))["target"],
                   ai_stats["vessels"],
                   ai_stats["total"], ai_stats["vessels_pct"], ai_stats["components"],
                   ai_stats["logit_min"], ai_stats["logit_max"], ai_stats["ai_us"],
                   ai_stats["proc_min"], ai_stats["proc_max"]))
            self.var_target.set("%.2f%%" % ai_stats["vessels_pct"])
            self.var_target_note.set("%s · 连通域 %d"
                                     % (proto.model_by_index(ai_stats.get("model"))["target"],
                                        ai_stats["components"]))
        else:
            self.var_ai.set("AI：本帧没要统计（勾上「数值统计」就能看到）")
            self.var_target.set("—")
            self.var_target_note.set("勾选「数值统计」后显示")
        self._show_morph(flags, ai_stats)

        self._set_link(True, "在线")
        self._status("完成" + ("  ·  " + self.roi_verify_note if self.roi_verify_note else ""))
        self._set_busy(False)

        if self.repeat:
            try:
                iv = max(0, int(self.var_interval.get()))
            except ValueError:
                iv = 200
            self.repeat_job = self.root.after(iv, self._on_send)

        if self.selftest_path is not None:
            try:
                if self.last_reply is not None:
                    imageio.save_image(self.selftest_path, self.last_reply)
                    print("GUI_SELFTEST saved %s (showing=%s, %d bytes)"
                          % (self.selftest_path, self.showing, len(self.last_reply)))
                print("GUI_SELFTEST flags=0x%04X images=%s ai_vessels=%s ai_comps=%s"
                      % (flags, sorted(images),
                         (self.ai_stats or {}).get("vessels"),
                         (self.ai_stats or {}).get("components")))
            except Exception as e:                        # noqa: BLE001 - selftest
                print("GUI_SELFTEST save failed:", e)
            self.root.after(300, self.root.destroy)


def _enable_dpi_awareness():
    """Windows 上让 tkinter 在高 DPI 下不发虚。"""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main_fundus():
    argv = sys.argv[1:]
    auto = "--autosend" in argv           # 冒烟测试/演示: 起来后自动发一帧
    img_path = None                       # 可选: 启动时预载一张图片
    selftest_path = None                  # 可选: 收到第一帧后存图并退出(自检)
    preset_flags = None
    preset_ai_src = None
    preset_ai_thr = None
    preset_ai_res = None
    preset_thr = None
    preset_mode = None
    if "--image" in argv:
        i = argv.index("--image")
        if i + 1 < len(argv):
            img_path = argv[i + 1]
            del argv[i:i + 2]
    if "--selftest" in argv:
        i = argv.index("--selftest")
        if i + 1 < len(argv):
            selftest_path = argv[i + 1]
            del argv[i:i + 2]
        auto = True
    if "--flags" in argv:
        i = argv.index("--flags")
        preset_flags = imagelink._parse_flags(argv[i + 1])
        del argv[i:i + 2]
    if "--ai-src" in argv:
        i = argv.index("--ai-src")
        preset_ai_src = (proto.AIPARAM_SRC_PROC if argv[i + 1] == "proc"
                         else proto.AIPARAM_SRC_RAW)
        del argv[i:i + 2]
    if "--ai-thr" in argv:
        i = argv.index("--ai-thr")
        preset_ai_thr = int(argv[i + 1])
        del argv[i:i + 2]
    if "--ai-res" in argv:
        i = argv.index("--ai-res")
        preset_ai_res = int(argv[i + 1])
        del argv[i:i + 2]
    if "--thr" in argv:
        i = argv.index("--thr")
        preset_thr = int(argv[i + 1])
        del argv[i:i + 2]
    if "--mode" in argv:
        i = argv.index("--mode")
        preset_mode = int(argv[i + 1])
        del argv[i:i + 2]
    hold = 0
    if "--hold" in argv:
        i = argv.index("--hold")
        if i + 1 < len(argv):
            hold = int(argv[i + 1])
            del argv[i:i + 2]
    argv = [a for a in argv if not a.startswith("--")]

    host = argv[0] if len(argv) > 0 else DEFAULT_HOST
    port = int(argv[1]) if len(argv) > 1 else DEFAULT_PORT

    if proto.SELF_CHECK_ERRORS:
        print("proto self-check FAILED:", proto.SELF_CHECK_ERRORS)

    _enable_dpi_awareness()
    root = tk.Tk()
    app = App(root, host, port, selftest_path=selftest_path,
              preset_flags=preset_flags, preset_ai_src=preset_ai_src,
              preset_ai_thr=preset_ai_thr, preset_ai_res=preset_ai_res,
              preset_thr=preset_thr, preset_mode=preset_mode)
    if img_path:
        root.after(200, lambda: app._load_path(img_path))
    if auto:
        root.after(1500, app._on_send)
    if selftest_path is not None:
        # 看门狗: 自检模式下 25 秒必退, 免得链路不通时把窗口挂在那儿
        root.after(25000, lambda: (print("GUI_SELFTEST timeout"), root.destroy()))
    if hold > 0:
        root.after(hold * 1000, root.destroy)     # 演示/截图用: 停留 N 秒后自己退出
    root.mainloop()


# ══════════════════════════════════════════════════════════════════════════
# 来源：platformwin.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""platformwin.py —— 心超工作站的「平台扩展」窗口（设计报告第 9 节：权重即程序）。

同一比特流、同一颗 PL U-Net 引擎，换一套 INT8 权重（约 34 KB）就是另一个程序，不重新综合、不改服务端。
窗口与心超工作站同一套外观：标题栏 + 程序页签 + 内容 + 状态栏（常驻声明 + 本页数据出处）。

    眼底血管（512 档）/ 皮肤镜病灶（128 档）  app.App 嵌入（单幅图：AI、ROI、置信度、形态、大图精查）

CT 脾脏整卷 / PL 程序台两页的窗口模块（ctwin / progwin）未随本包提供，页签已移除。

与心超工作站在同一个进程里（不再另起 app.py 进程）：共用一条板子连接、地址和 busy 互斥（板端一次只服务一个连接），
板上 DP 工作站也能直接打开（板上另起的进程装不上 tkinter 兼容层）。关窗口只是藏起来，再开时结果都在。
仅用于工程教学与算法演示，不作为临床诊断依据。
"""

import tkinter as tk


TITLE = "平台扩展 · 权重即程序"
SUBTITLE_PLATFORM = "同一比特流、同一颗 PL U-Net 引擎；每套 INT8 权重约 34 KB，换程序不重新综合、不改服务端"
FONT_APP = ("Microsoft YaHei UI", 15, "bold")
FONT_TAB = ("Microsoft YaHei UI", 11, "bold")
# (键, 页签名, 页, 程序说明 dict)；页 = "image"（单幅图 App）
PROGRAMS = (
    ("fundus", "眼底血管", "image",
     dict(model="fundus", group="fundus", sample="im0002", title="眼底血管分割（512 档，程序 0）",
          note="STARE / DRIVE · 视野内 AUC 0.978（开发验证）· 512 档整网 292.8 ms，比同板 4 核 NEON 快 1.93 倍",
          desc="单幅眼底图：U-Net 血管分割；框选 ROI / 大图精查、置信度热力图、形态指标",
          data="示例图来自 STARE 公开数据集")),
    ("skin", "皮肤镜病灶", "image",
     dict(model="skin", group="skin", sample="ISIC_0011336", title="皮肤镜病灶分割（128 档，程序 1）",
          note="ISIC 2016 · 官方测试集 379 张 Jaccard 0.818 · 板上输出与离线逐值一致",
          desc="单幅皮肤镜图：同一颗引擎换一套权重分割病灶；同屏原图 / 掩膜 / 叠加 / 热力图",
          data="示例图 ISIC_0011336 / ISIC_0011092 来自 ISIC 2016 官方测试集（CC-0）")),
)


class PlatformWindow(object):
    """heart = heartapp.HeartApp（提供 root / link / var_host / var_port / busy / status / extra_tools）。"""

    def __init__(self, heart):
        self.heart = heart
        self.top = tk.Toplevel(heart.root)
        self.top.title("%s — 床旁超声心动图实时全心功能评估系统" % TITLE)
        self.top.configure(bg=C_BG)
        uistyle.setup_style(self.top)
        self.pages, self.app, self.cur = {}, None, None
        self.var_status = tk.StringVar(value="就绪")
        self.var_note = tk.StringVar(value=uistyle.DISCLAIMER)
        self._build()
        self.top.protocol("WM_DELETE_WINDOW", self.hide)
        self.switch("fundus")

    # 页里的 App / CTWindow / ProgWindow 按 app 接口调 status()，经这里进平台窗口自己的状态栏
    def status(self, msg):
        self.var_status.set(msg)

    def __getattr__(self, name):                    # link / var_host / var_port / busy / extra_tools 都转给心超工作站
        if name in ("link", "var_host", "var_port", "extra_tools", "root"):
            return getattr(self.heart, name)
        raise AttributeError(name)

    @property
    def busy(self):
        return self.heart.busy

    @busy.setter
    def busy(self, v):
        self.heart.busy = v

    def _build(self):
        t = self.top
        bar = tk.Frame(t, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        bar.grid(row=0, column=0, sticky="ew")
        logo = tk.Canvas(bar, width=30, height=30, bg=C_CARD, highlightthickness=0)
        logo.pack(side="left", padx=(16, 8), pady=10)
        for i in range(3):                           # 3x3 小方块 = 同一颗引擎上的多个程序
            for j in range(3):
                logo.create_rectangle(3 + 9 * i, 3 + 9 * j, 10 + 9 * i, 10 + 9 * j, outline="",
                                      fill=C_ACCENT if (i + j) % 2 == 0 else "#93c5fd")
        uistyle.label(bar, TITLE, font=FONT_APP).pack(side="left")
        uistyle.label(bar, SUBTITLE_PLATFORM, font=uistyle.FONT_SMALL, fg=C_MUTED).pack(side="left", padx=(12, 0), pady=(6, 0))
        tools = tk.Frame(bar, bg=C_CARD)
        tools.pack(side="right", padx=(0, 16))
        uistyle.label(tools, var=self.heart.var_link, fg=C_MUTED).pack(side="left", padx=(0, 10))
        tk.Button(tools, text="← 返回心超工作站", font=uistyle.FONT_HEAD, fg="white", bg=C_ACCENT,
                  activebackground=uistyle.C_ACCENT_D, activeforeground="white", relief="flat", bd=0,
                  padx=16, pady=5, cursor="hand2", command=self.hide).pack(side="left")

        mb = tk.Frame(t, bg=C_BG)
        mb.grid(row=1, column=0, sticky="ew", padx=12, pady=(10, 0))
        seg = tk.Frame(mb, bg=C_BORDER, padx=1, pady=1)
        seg.pack(side="left")
        self.tabs = {}
        for key, label, _, _ in PROGRAMS:
            b = tk.Button(seg, text=label, font=FONT_TAB, relief="flat", bd=0, padx=22, pady=6, cursor="hand2",
                          command=lambda k=key: self.switch(k))
            b.pack(side="left", padx=(0 if not self.tabs else 1, 0))
            self.tabs[key] = b
        self.var_desc = tk.StringVar(value="")
        uistyle.label(mb, var=self.var_desc, fg=C_MUTED, bg=C_BG, anchor="w").pack(side="left", padx=(14, 0))

        self.body = tk.Frame(t, bg=C_BG)
        self.body.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        sbar = tk.Frame(t, bg=uistyle.C_BAR)
        sbar.grid(row=3, column=0, sticky="ew")
        tk.Label(sbar, textvariable=self.var_note, font=uistyle.FONT_SMALL, fg=C_WARN, bg=uistyle.C_BAR, padx=12,
                 pady=4).pack(side="right")
        tk.Label(sbar, textvariable=self.var_status, font=uistyle.FONT_SMALL, anchor="w", fg=C_TEXT, width=1,
                 bg=uistyle.C_BAR, padx=12, pady=4).pack(side="left", fill="x", expand=True)
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(2, weight=1)

    def _page(self, kind):
        """页第一次用到时才建。本包只剩单幅图页（CT / PL 程序台的窗口模块未随包提供，页签已移除）。"""
        if kind in self.pages:
            return self.pages[kind]
        appmod = sys.modules[__name__]
        page = tk.Frame(self.body, bg=C_BG)
        self.app = appmod.App(self.top, self.heart.link.host, self.heart.link.port, parent=page, shared=self)
        self.pages[kind] = page
        return page

    def switch(self, key):
        _, label, kind, prog = next(p for p in PROGRAMS if p[0] == key)
        if not self.heart.busy:                      # 换页先放掉共用连接：单幅图页发送前不重连，别给它一个板端已断开的
            self.heart.link.close()
        try:
            page = self._page(kind)
        except ImportError as e:
            self.status("这一页需要 numpy + scipy（%s）" % e)
            return
        for k, p in self.pages.items():
            if p is not page:
                p.grid_forget()                      # 板上 tkinter 兼容层没有 grid_remove
        page.grid(row=0, column=0, sticky="nsew")
        if self.app is not None and self.app.program is not prog:
            self._set_program(prog)                  # 换页即换程序：同一比特流换一套权重
        for k, b in self.tabs.items():
            on = k == key
            b.configure(bg=C_ACCENT if on else C_CARD, fg="white" if on else C_TEXT,
                        activebackground=uistyle.C_ACCENT_D if on else uistyle.C_EMPTY,
                        activeforeground="white" if on else C_TEXT)
        self.cur = key
        self.var_desc.set(prog["desc"])
        self.var_note.set("%s｜%s" % (uistyle.DISCLAIMER, prog["data"]))

    def _set_program(self, prog):
        """单幅图页换程序：标题 / 说明、AI 模型（门限与分辨率随模型）、示例图。"""
        imageio = sys.modules[__name__]
        proto = sys.modules[__name__]
        a = self.app
        a.program = prog
        a._refresh_mask_text()                      # 皮肤镜 = 皮肤镜掩膜，眼底 = 血管掩膜
        a.var_prog_title.set(prog["title"])
        a.var_prog_note.set(prog["note"])
        m = next((m for m in proto.MODELS if m["name"] == prog["model"]), None)
        if m is not None:
            a.var_ai_model.set(m["label"])          # trace -> _on_model_change：门限 / 分辨率 / ROI / 形态
        pick = None
        if hasattr(imageio, "first_sample"):        # 交付包 / 板上版 imageio：samples/<组>/ 按模型分组
            pick = imageio.first_sample(prog.get("group") or "")
        elif prog.get("sample") in imageio.figure_names():
            pick = prog["sample"]
        if pick is not None:
            a._load_pattern(pick)
        a.var_f_over.set(True)
        a.var_f_stats.set(True)

    def show(self):
        uistyle.bring_up(self.top)

    def hide(self):
        if self.app is not None:
            self.app._stop_repeat()
        self.top.withdraw()
        uistyle.bring_up(self.heart.root)


# ══════════════════════════════════════════════════════════════════════════
# 来源：snapview.py
# ══════════════════════════════════════════════════════════════════════════

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


# 双平面测量要的是 CAMUS 的成对序列 <根目录>/seq_<病人>_4CH 与 seq_<病人>_2CH。
# 单文件版放在 07_ui 下，PLD_DIR 默认就等于 HERE，而样例在原工程里是 <工程根>/echo/samples，
# 所以不能只认一个写死的目录（2026-11-19：修「找不到病例」）。这里按优先级找一串候选根，
# 再扫描全部成对病例；界面里还能手选一个目录，记进 json 下次直接用。
ECHO_SAMPLES_JSON = os.path.join(HERE, "echo_samples_dir.json")      # 手选样例目录的落盘位置
CASE_NOTE = {"patient0027": "图像质量 Good", "patient0213": "四腔 Medium / 二腔 Good",
             "patient0231": "图像质量 Poor"}      # CAMUS 官方测试集里排最前的好 / 中 / 差三位
CASES = tuple((p, CASE_NOTE[p]) for p in ("patient0027", "patient0213", "patient0231"))   # 已知病例排前面
CASE_SEX = {"patient0027": "M", "patient0213": "M", "patient0231": "M"}   # CAMUS Info_4CH.cfg 的 Sex；身高体重 CAMUS 没有
CW = 392                                          # 卡片内容宽
FIG = 228                                         # 卡片里圆盘图的边长（点开看 512 大图）；卡片不能比左边两栏高
FONT_EF_SNAP = ("Microsoft YaHei UI", 28, "bold")
FONT_NUM = ("Microsoft YaHei UI", 14, "bold")
BAND_COL = ("#c2410c", "#b45309", "#047857")
VIEWS = (("4CH", "四腔 A4C"), ("2CH", "二腔 A2C"))
N112, UP = 112, 4                                 # 关键帧 / 现场模式的掩膜是 112；单平面 Simpson 前在板上放大 4 倍
LEGEND2 = "上 四腔　下 二腔\n左 ED　右 ES\n\n绿 轮廓\n红 长轴\n蓝 二尖瓣平面\n黄 20 个圆盘\n\n点图看大图"
LEGEND1 = "四腔 A4C\n左 ED　右 ES\n\n绿 轮廓\n红 长轴\n蓝 二尖瓣平面\n黄 20 个圆盘\n\n点图看大图"


def saved_samples_dir():
    """界面里手选过、且目录还在的那一个（文件没了或目录被删就当没选）。"""
    try:
        with open(ECHO_SAMPLES_JSON, "r", encoding="utf-8") as f:
            d = json.load(f).get("dir")
    except (OSError, ValueError, AttributeError):
        return None
    return d if d and os.path.isdir(d) else None


def save_samples_dir(d):
    try:
        with open(ECHO_SAMPLES_JSON, "w", encoding="utf-8") as f:
            json.dump({"dir": d}, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def _downloads_samples():
    """兜底：%USERPROFILE%\\Downloads 下每个文件夹里再找 samples / echo/samples。

    只用来找「解压出来的那包」，扫到就当成候选根之一；没有也正常（回到多根搜索）。
    """
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    base = os.path.join(home, "Downloads")
    hits = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return hits
    for nm in names:
        for rel in (("",), ("samples",), ("echo", "samples")):
            d = os.path.join(base, nm, *rel)
            try:
                if os.path.isdir(d) and any(n.startswith("seq_") for n in os.listdir(d)):
                    hits.append(d)
            except OSError:
                continue
    return hits


def echo_samples_roots():
    """候选根目录，按优先级排；同一个目录只留一次。"""
    out = []

    def add(p):
        if not p:
            return
        p = os.path.normpath(p)
        if p not in out:
            out.append(p)

    add(saved_samples_dir())                          # ① 界面里手选的（最优先）
    for env in (os.environ.get("PLD_ECHO_SAMPLES"), os.environ.get("PLD_WS_DIR")):
        if env:                                       # ② 环境变量：先当样例目录本身试，再当工程根试
            add(env)
            add(os.path.join(env, "echo", "samples"))
            add(os.path.join(env, "samples"))
    for base in (PLD_DIR, HERE, os.path.join(HERE, os.pardir), os.path.join(HERE, os.pardir, os.pardir)):
        add(os.path.join(base, "echo", "samples"))    # ③ 打包目录 / 上一级项目根（原工程布局）
        add(os.path.join(base, "samples"))
    add(os.path.join(HERE, "demo"))                   # ④ 板上 DP 工作站：样例拷贝放 demo/
    for d in _downloads_samples():                    # ⑤ 解压在 Downloads 里也能找到
        add(d)
    return out


def case_dir(patient, view, roots=None):
    for d in (echo_samples_roots() if roots is None else roots):
        p = os.path.join(d, "seq_%s_%s" % (patient, view))
        if os.path.isdir(p):
            return p
    return None


def available_cases(roots=None):
    """扫所有候选根里成对的 seq_<病人>_4CH / _2CH。

    已知三位（patient0027 / 0213 / 0231）排前面并带质量说明，其余患者的显示「自定义病例」。
    """
    roots = echo_samples_roots() if roots is None else roots
    names = []
    for d in roots:
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for nm in entries:
            m = re.match(r"^seq_(.+)_(?:4CH|2CH)$", nm)
            if m and m.group(1) not in names:
                names.append(m.group(1))
    ok = [p for p in names if case_dir(p, "4CH", roots) and case_dir(p, "2CH", roots)]
    out = [(p, CASE_NOTE[p]) for p, _ in CASES if p in ok]
    out += [(p, "自定义病例（四腔 + 二腔）") for p in ok if p not in CASE_NOTE]
    return out


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
        ttk.Button(row, text="目录…", width=6, command=self.pick_samples_dir) \
            .pack(side="right", padx=(6, 0))          # 样例不在默认位置时手选（记进 echo_samples_dir.json）
        ttk.Button(row, text="标尺…", width=6, command=self._calib_dialog) \
            .pack(side="right", padx=(6, 0))          # 手动定标（11-19）：覆盖 CAMUS 文件里的像素间距
        labels = self._case_labels()
        self.var_case = tk.StringVar(value=labels[0])
        self.cbo_case = ttk.Combobox(row, textvariable=self.var_case, values=labels, width=22, state="readonly",
                                     font=FONT)
        self.cbo_case.pack(side="left", fill="x", expand=True)
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
        uistyle.label(efc, var=self.var_eft, font=FONT_SMALL, fg=uistyle.C_MUTED, width=18).pack(anchor="w")
        self.var_mef = tk.StringVar(value="—")
        self.lab_mef = uistyle.label(efc, var=self.var_mef, font=FONT_EF_SNAP, width=5)
        self.lab_mef.pack(anchor="w")
        vc = tk.Frame(top, bg=uistyle.C_CARD)
        vc.pack(side="left", padx=(18, 0), anchor="n")
        self.var_edv, self.var_esv, self.var_ref = tk.StringVar(value="EDV —"), tk.StringVar(value="ESV —"), \
            tk.StringVar(value="")
        for var in (self.var_edv, self.var_esv):
            uistyle.label(vc, var=var, font=FONT_NUM, width=10).pack(anchor="w")
        uistyle.label(vc, var=self.var_ref, font=FONT_SMALL, fg=uistyle.C_MUTED, width=18).pack(anchor="w")
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
        uistyle.label(frow, var=self.var_legend, font=FONT_SMALL, fg=uistyle.C_MUTED, justify="left", width=14,
                      anchor="nw").pack(side="left", padx=(10, 0), anchor="n")
        self.var_note = tk.StringVar(value="选病例，点「一键双平面测量」" if self.cases else
                                     "没有找到病例：点右边的「目录…」，选 CAMUS 的 samples 文件夹")
        uistyle.label(b, var=self.var_note, font=FONT_SMALL, fg=uistyle.C_MUTED, wrap=True).pack(fill="x", pady=(4, 0))
        self._blank_fig()

    def _blank_fig(self):
        from PIL import Image, ImageTk
        self.fig_full, self.fig_size = None, (512, 512)
        self.photos_m["fig"] = ImageTk.PhotoImage(Image.new("RGB", (FIG, FIG), (24, 24, 24)))
        self.lab_fig.configure(image=self.photos_m["fig"], width=FIG, height=FIG)

    # ---------------------------------------------------------------- 病例目录
    def _case_labels(self):
        return ["%s（%s）" % c for c in self.cases] or ["（没有找到 CAMUS 示例病例）"]

    def _refresh_cases(self):
        """重新扫一遍候选根，刷新下拉框；返回找到的病例数。"""
        self.cases = available_cases()
        labels = self._case_labels()
        self.var_case.set(labels[0])
        self.cbo_case.configure(values=labels)
        if self.cases and self.var_sex.get() in ("", "—"):
            self.var_sex.set(CASE_SEX.get(self.cases[0][0], "—"))
        return len(self.cases)

    def pick_samples_dir(self):
        """样例不在默认位置时手选：可以选 samples 本身，也可以选它的上一层（echo/ 或解压出来的工程根）。"""
        from tkinter import filedialog, messagebox
        roots = echo_samples_roots()
        d = filedialog.askdirectory(parent=self.top, initialdir=roots[0] if roots else HERE,
                                    title="选择 CAMUS 样例目录（里面应有 seq_<病人>_4CH / _2CH）")
        if not d:
            return
        d = os.path.normpath(d)
        for cand in (d, os.path.join(d, "samples"), os.path.join(d, "echo", "samples")):
            try:
                hit = os.path.isdir(cand) and any(n.startswith("seq_") for n in os.listdir(cand))
            except OSError:
                hit = False
            if hit:
                d = cand
                break
        else:
            if not messagebox.askyesno("这个目录里没有 seq_* 病例",
                                       "选中的目录：\n%s\n\n里面没有 seq_<病人>_4CH / _2CH 文件夹。\n仍要记下它吗？" % d,
                                       parent=self.top):
                return
        saved = save_samples_dir(d)
        n = self._refresh_cases()
        self.var_note.set("样例目录：%s（找到 %d 个病例）%s"
                          % (d, n, "" if saved else "；配置没写进去，下次要重选"))

    def _calib_dialog(self):
        """手动定标（11-19）：快照模式也能人工指定像素间距（覆盖 CAMUS 文件里的 spacing_mm）。
        点两个已知间距的点（屏幕上的 1 cm 深度刻度、或图上的两把卡尺），填两点之间几厘米；
        之后的面积 / 容积 / Simpson 都用这个间距（只影响这一次载入，不动文件）。"""
        from PIL import Image
        if not self.frames or not self.info:
            self.var_note.set("先选病例载入一段序列，再手动定标")
            return
        w, h = int(self.info.get("width") or 0), int(self.info.get("height") or 0)
        if w <= 0 or h <= 0 or len(self.frames[0]) != w * h * 3:
            self.var_note.set("这段序列的画面尺寸对不上，手动定标先跳过")
            return
        img = Image.frombytes("RGB", (w, h), bytes(self.frames[0]))

        def done(mm_px, dist, cm):
            self.info["spacing_mm"] = [mm_px, mm_px]
            self.info["spacing_manual"] = True
            self._new_tracker()                       # AreaMapper 用的是间距，重建一下
            self.var_status.set("%s：手动定标 像素间距 %.3f×%.3f mm（两点 %.0f 像素 = %g cm）"
                                % (self.info.get("name", ""), mm_px, mm_px, dist, cm))
            self.var_note.set("已改用手动定标：面积 / 容积按 %.3f mm/像素 算（重新载入病例会回到文件里的间距）" % mm_px)
        CalibDialog(self.top, "手动定标 — %s" % self.info.get("name", ""), img, done,
                    note="点两个已知间距的点（超声机屏幕上的 1 cm 深度刻度，或图上的两把卡尺），再填两点之间几厘米。")

    # ---------------------------------------------------------------- 一键测量
    def _patient_id(self):
        s = self.var_case.get()
        for p, _ in self.cases:
            if s == p or s.startswith(p + "（"):
                return p
        return None

    def measure_case(self):
        p = self._patient_id()
        if p is None:
            self._refresh_cases()
            p = self._patient_id()
        if p is None:
            self.var_note.set("没有找到病例：点「目录…」选 CAMUS 的 samples 文件夹（里面要有 seq_<病人>_4CH / _2CH）")
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
        cm = sys.modules[__name__]
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
        ch = sys.modules[__name__]
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
        jobs = sys.modules[__name__]
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
        ch = sys.modules[__name__]
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


# ══════════════════════════════════════════════════════════════════════════
# 来源：heartapp.py
# ══════════════════════════════════════════════════════════════════════════

# -*- coding: utf-8 -*-
"""heartapp.py —— 床旁超声心动图实时全心功能评估系统：上位机主界面（「心超工作站」，设计报告第 7 节）。

    标题栏：系统名 + 板卡地址 / 连接状态（检测连接 = 问板端 caps）+ 平台扩展（打开原来的多程序上位机 app.py）
    模式条：快照模式（CAMUS）| 关键帧模式 | 现场模式（7.3 节三种工作模式），右侧一句话说明本模式的输入 → 输出
    内容区：快照 = sequence.SeqWindow 嵌入（单帧 / ED-ES 帧对、点击纠错、序列传播、双平面 Simpson、左房 / 左室质量 / 壁厚）；
            关键帧 / 现场 = heartview.EchoView（视频 + 容积曲线 + 时间线 + 结果 + 全心卡片 + 点击纠错 + 证据报告）
    状态栏：左边状态，右边常驻「仅用于工程教学与算法演示」+ 本段画面的数据出处
三个模式共用一条板子连接（板端一次只服务一个连接，busy 互斥）；切换模式不丢各自的结果。
快捷键：空格 播放 / 暂停，← → 逐帧，Ctrl+Z 撤销纠错。

用法：run_heart.bat 双击，或
    python heartapp.py [host] [port] [--mode snapshot|keyframe|live] [--open 视频.avi] [--demo]
--demo：验收演示，三个模式先备好素材、停在现场模式（prepare_demo）。
板上运行（DP 显示器）时 host 用 127.0.0.1。
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk


DEFAULT_HOST = "192.168.1.254"
DEFAULT_PORT = 5060
APP_TITLE = "床旁超声心动图实时全心功能评估系统"
SUBTITLE = "FZ3B · PL U-Net 分割 + PS 斑点跟踪 · 心尖四腔心（A4C）"
FONT_APP = ("Microsoft YaHei UI", 15, "bold")
FONT_TAB = ("Microsoft YaHei UI", 11, "bold")
MODES = (
    ("snapshot", "快照模式", "输入：单帧 / ED-ES 帧对（CAMUS）→ 左室分割、点击纠错、双平面 Simpson EF、左房容积、左室质量、壁厚",
     "画面来自 CAMUS（CC BY-NC-SA 4.0）"),
    ("keyframe", "关键帧模式", "输入：整段视频上传 → 逐拍 EF、分型、保形区间、质量灯、复核提示、全心卡片；可点击纠错、导出证据报告", None),
    ("live", "现场模式", "输入：采集卡（板端 /dev/video0）逐帧“采”帧 → 每帧回掩膜和容积，每 0.25 s 一次滚动 EF；结束后同关键帧模式", None),
)


def route_hint(host):
    """连不上时看本机走哪个地址去板子：TUN 代理（198.18.x）会假装端口开着、连上就断；没有同网段地址 = 网线没接 / IP 不对。"""
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
        return "——本机没有和板子同网段的地址（现在走 %s）：检查网线，有线网卡应为 192.168.1.100" % ip
    return ""


EXTRA_TOOLS = []          # 板端工作站（board/station_heart.py）在建窗口前往这里加 (名字, fn(单幅图 App))


class HeartApp(object):
    """主窗口。给各视图提供 root / link / var_host / var_port / busy / status() / board_seen()。"""

    def __init__(self, root, host, port, mode="keyframe"):
        self.root = root
        self.link = imagelink.ImageLink(host, port)
        self.busy = False
        self.queue = queue.Queue()
        self.views = {}
        self.mode = None
        self.platform = None                         # platformwin.PlatformWindow（第一次点「平台扩展…」时建）
        self.calib = {}                              # 自动定标结果：原始录屏文件名 -> 原始画面 mm/像素（heartview）
        self.calib_manual = {}                       # 手动定标（11-19）：同一个键 -> (两点几厘米, 两点像素)
        self.extra_tools = list(EXTRA_TOOLS)         # 平台扩展「更多」里的额外入口 (名字, fn(单幅图 App))，板端工作站挂
        root.title("%s — 上位机（心超工作站）" % APP_TITLE)
        root.configure(bg=C_BG)
        uistyle.setup_style(root)
        self._build_header()
        self._build_modebar()
        self.body = tk.Frame(root, bg=C_BG)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self.var_status = tk.StringVar(value="就绪")
        self.var_note = tk.StringVar(value=uistyle.DISCLAIMER)
        sbar = tk.Frame(root, bg=C_BAR)
        sbar.grid(row=3, column=0, sticky="ew")
        tk.Label(sbar, textvariable=self.var_note, font=uistyle.FONT_SMALL, fg=C_WARN, bg=C_BAR, padx=12, pady=4) \
            .pack(side="right")
        tk.Label(sbar, textvariable=self.var_status, font=uistyle.FONT_SMALL, anchor="w", fg=C_TEXT, width=1,
                 bg=C_BAR, padx=12, pady=4).pack(side="left", fill="x", expand=True)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)
        for seq, name in (("<space>", "space"), ("<Left>", "left"), ("<Right>", "right"), ("<Control-z>", "undo")):
            root.bind(seq, lambda e, n=name: self._key(e, n))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.switch(mode)
        root.after(50, self._poll)

    # ---------------------------------------------------------------- 界面
    def _build_header(self):
        bar = tk.Frame(self.root, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        bar.grid(row=0, column=0, sticky="ew")
        logo = tk.Canvas(bar, width=30, height=30, bg=C_CARD, highlightthickness=0)
        logo.pack(side="left", padx=(16, 8), pady=10)
        logo.create_oval(3, 3, 27, 27, fill="#dc2626", outline="")
        logo.create_line(6, 16, 11, 16, 14, 8, 17, 23, 20, 14, 24, 14, fill="white", width=2)
        uistyle.label(bar, APP_TITLE, font=FONT_APP).pack(side="left")
        uistyle.label(bar, SUBTITLE, font=uistyle.FONT_SMALL, fg=C_MUTED).pack(side="left", padx=(12, 0), pady=(6, 0))
        tools = tk.Frame(bar, bg=C_CARD)
        tools.pack(side="right", padx=(0, 16))
        uistyle.label(tools, "板卡", fg=C_MUTED).pack(side="left", padx=(0, 6))
        self.var_host = tk.StringVar(value=self.link.host)
        ttk.Entry(tools, textvariable=self.var_host, width=14, font=FONT_MONO).pack(side="left")
        uistyle.label(tools, ":", fg=C_MUTED).pack(side="left", padx=2)
        self.var_port = tk.StringVar(value=str(self.link.port))
        ttk.Entry(tools, textvariable=self.var_port, width=6, font=FONT_MONO).pack(side="left")
        self.lbl_dot = uistyle.label(tools, "●", fg=C_IDLE)
        self.lbl_dot.pack(side="left", padx=(12, 3))
        self.var_link = tk.StringVar(value="未检测")
        uistyle.label(tools, var=self.var_link, fg=C_MUTED, width=22, anchor="w").pack(side="left")
        ttk.Button(tools, text="检测连接", command=self.check_board).pack(side="left", padx=(6, 0))
        uistyle.vsep(tools)
        ttk.Button(tools, text="平台扩展…", command=self._open_platform).pack(side="left")

    def _build_modebar(self):
        bar = tk.Frame(self.root, bg=C_BG)
        bar.grid(row=1, column=0, sticky="ew", padx=12, pady=(10, 0))
        seg = tk.Frame(bar, bg=C_BORDER, padx=1, pady=1)
        seg.pack(side="left")
        self.tabs = {}
        for key, title, _, _ in MODES:
            b = tk.Button(seg, text=title, font=FONT_TAB, relief="flat", bd=0, padx=22, pady=6, cursor="hand2",
                          command=lambda k=key: self.switch(k))
            b.pack(side="left", padx=(0 if not self.tabs else 1, 0))
            self.tabs[key] = b
        self.var_desc = tk.StringVar(value="")
        uistyle.label(bar, var=self.var_desc, fg=C_MUTED, bg=C_BG, anchor="w").pack(side="left", padx=(14, 0))

    def switch(self, mode):
        """切换工作模式：第一次切到时才建视图；切走的视图只暂停播放，结果保留。"""
        if mode == self.mode:
            return
        if self.mode in self.views:
            v = self.views[self.mode]
            if hasattr(v, "_pause"):
                v._pause()
            if getattr(v, "stream_stop", None) is not None:     # 快照模式的实时流：收完在途帧就停
                v.stream_stop.set()
            v.frame.grid_forget()                    # 不用 grid_remove：板上 tkinter 兼容层没有它（下面总是带全参数重新 grid）
        self.mode = mode
        if mode not in self.views:
            try:
                self.views[mode] = self._make_view(mode)
            except ImportError as e:
                messagebox.showerror(APP_TITLE, "这个模式需要 numpy + scipy + OpenCV + Pillow（%s）" % e, parent=self.root)
                self.mode = None
                return
        self.views[mode].frame.grid(row=0, column=0, sticky="nsew")
        for k, b in self.tabs.items():
            on = k == mode
            b.configure(bg=C_ACCENT if on else C_CARD, fg="white" if on else C_TEXT,
                        activebackground=uistyle.C_ACCENT_D if on else uistyle.C_EMPTY,
                        activeforeground="white" if on else C_TEXT)
        m = next(x for x in MODES if x[0] == mode)
        self.var_desc.set(m[2])
        v = self.views[mode]
        txt = v.var_txt.get() if hasattr(v, "var_txt") else v.var_status.get()
        self.status(txt.splitlines()[0] if txt else m[1])

    def _make_view(self, mode):
        if mode == "snapshot":
            snapview = sys.modules[__name__]
            return snapview.SnapView(self, self.body)
        heartview = sys.modules[__name__]
        return heartview.EchoView(self, self.body, live=(mode == "live"))

    def _update_note(self):
        m = next(x for x in MODES if x[0] == self.mode)
        v = self.views.get(self.mode)
        note = (v.data_note() if v is not None and hasattr(v, "data_note") and (getattr(v, "name", None)
                                                                               or self.mode == "snapshot") else None) or m[3]
        self.var_note.set(uistyle.DISCLAIMER + ("｜" + note if note else ""))

    def status(self, msg):
        self.var_status.set(msg)
        self._update_note()

    # ---------------------------------------------------------------- 板子
    def _hostport(self):
        try:
            return self.var_host.get().strip(), int(self.var_port.get().strip())
        except ValueError:
            return self.link.host, self.link.port

    def check_board(self):
        if self.busy:
            self.status("板子正忙（有任务在途），稍后再检测")
            return
        self.busy = True
        self.lbl_dot.configure(fg=C_IDLE)
        self.var_link.set("检测中…")
        hp = self._hostport()

        def work():
            try:
                self.link.host, self.link.port = hp
                self.link.connect()
                c = jobs.caps(self.link)
                self.link.close()                    # 共用连接用完就放
                self.queue.put(("caps", c))
            except Exception as e:
                self.link.close()
                self.queue.put(("caps_err", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def board_seen(self, server):
        """各视图的任务连上板子时报一下服务端版本。"""
        if server:
            self.lbl_dot.configure(fg=C_GREEN)
            self.var_link.set("已连接 · 服务端 %s" % server)

    def _on_caps(self, c):
        self.busy = False
        modes = c.get("echo_modes", [])
        have = [name for name, ok in (("关键帧", 2 in modes), ("现场", 3 in modes), ("全心", "heart" in c)) if ok]
        self.board_seen(c.get("server", "?"))
        self.status("板端 %s：%s；%d 个内置模型，%d 核" % (
            c.get("server", "?"), ("支持 " + " / ".join(have)) if have else "不支持关键帧 / 现场 / 全心（需要 v5.21+ / v5.22+ / v5.24）",
            c.get("models", 0), c.get("cores", 0)))

    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "caps":
                    self._on_caps(item[1])
                elif item[0] == "caps_err":
                    self.busy = False
                    self.lbl_dot.configure(fg=uistyle.C_STOP)
                    self.var_link.set("连不上")
                    self.status("连不上板子 %s:%s（%s）%s" % (self.link.host, self.link.port, item[1],
                                                         route_hint(self.link.host)))
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(100, self._poll)

    def _open_platform(self):
        """平台扩展：同一比特流换权重的其它程序（眼底血管、皮肤镜），platformwin.PlatformWindow，
        同一进程、共用板子连接与 busy；关掉只是藏起来。"""
        for v in self.views.values():
            if hasattr(v, "_pause"):
                v._pause()
        if not self.busy:                            # 单幅图页的发送不先重连：别留一个板端已经断开的连接给它
            self.link.close()
        if self.platform is None:
            platformwin = sys.modules[__name__]
            self.platform = platformwin.PlatformWindow(self)
        else:
            self.platform.show()
        self.status("平台扩展：眼底血管 / 皮肤镜病灶（同一比特流，换权重即换程序）")

    def _key(self, e, name):
        if isinstance(e.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        v = self.views.get(self.mode)
        if v is not None and hasattr(v, "key"):
            v.key(name)

    def close(self):
        if self.platform is not None and self.platform.app is not None:
            self.platform.app._stop_repeat()
        for v in self.views.values():
            if hasattr(v, "_stop"):
                v._stop()
            if hasattr(v, "stream_stop") and v.stream_stop is not None:
                v.stream_stop.set()
        uistyle.close_when_idle(self.root, lambda: self.busy)




def _first(*paths):
    return next((p for p in paths if os.path.exists(p)), paths[0])


# 演示素材：走和「双平面测量」同一套候选目录找（工程目录原位置 / 07_ui/echo/samples / 手选目录 / 板上 demo/）
DEMO_SNAPSHOT = case_dir("patient0027", "4CH") or os.path.join(HERE, "demo", "seq_patient0027_4CH")   # 设计报告图 6（CAMUS）
DEMO_VIDEO = _first(os.path.join(PLD_DIR, "HMC-QU Dataset-Kaggle v1.0 (1)", "HMC-QU Dataset-Kaggle v1.0", "HMC-QU",
                                 "A4C", "ES000108 _4CH_1.avi"),
                    os.path.join(HERE, "demo", "ES000108 _4CH_1.avi"))               # 设计报告图 7 / 图 8（HMC-QU）


def prepare_demo(app):
    """--demo：三个模式先各备好素材（快照 = CAMUS patient0027 四腔，关键帧 = HMC-QU ES000108），最后停在现场模式。
    现场模式 11-18 起只认采集卡（板上 /dev/video0），素材不用预置，接上卡就能采。缺的素材跳过。"""
    app.switch("snapshot")
    if os.path.isdir(DEMO_SNAPSHOT):
        app.views["snapshot"].load(DEMO_SNAPSHOT)
    app.switch("keyframe")
    if os.path.exists(DEMO_VIDEO):
        app.views["keyframe"].load(DEMO_VIDEO)
    app.switch("live")
    app.status("演示准备好了：现场模式用采集卡（板上 /dev/video0）；先跑推荐档，再选「每帧推理」跑一遍对比延迟")


def _enable_dpi_awareness():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main():
    _log_line("启动：pid=%d 文件=%s 梯度恢复=%s 默认保护=%.1f 锥"
              % (os.getpid(), os.path.abspath(__file__), "开" if GRAD_ON else "关", GRAD_CLICK_K))
    argv = sys.argv[1:]
    mode, open_path = "keyframe", None
    demo = "--demo" in argv
    if "--mode" in argv:
        i = argv.index("--mode")
        mode = argv[i + 1]
        del argv[i:i + 2]
    if "--open" in argv:
        i = argv.index("--open")
        open_path = argv[i + 1]
        del argv[i:i + 2]
    argv = [a for a in argv if not a.startswith("--")]
    host = argv[0] if len(argv) > 0 else DEFAULT_HOST
    port = int(argv[1]) if len(argv) > 1 else DEFAULT_PORT
    _enable_dpi_awareness()
    root = tk.Tk()
    app = HeartApp(root, host, port, mode if mode in [m[0] for m in MODES] else "keyframe")
    if open_path and app.mode == "keyframe":
        app.views["keyframe"].load(open_path)
    if demo:
        prepare_demo(app)
    root.update_idletasks()
    try:
        big = root.winfo_reqheight() > root.winfo_screenheight() - 60 or root.winfo_reqwidth() > root.winfo_screenwidth()
        if demo or big:                              # 验收演示 / 屏幕放不下：最大化（内容居中）
            root.state("zoomed")
        else:                                        # 贴屏幕顶、水平居中，免得底部压到任务栏
            root.geometry("+%d+0" % max(0, (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2))
    except Exception:                                # 板上 tkinter 兼容层没有 winfo_req* / winfo_screen*：直接最大化
        try:
            root.state("zoomed")
        except Exception:
            pass
    root.after(300, app.check_board)
    root.mainloop()


# ══════════════════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════════════════
def _entry():
    # 注意：main() / main_fundus() 都是无参函数，各自去读 sys.argv；这里只负责摘掉 --fundus
    if "--fundus" in sys.argv[1:]:
        sys.argv = [a for a in sys.argv if a != "--fundus"]
        return main_fundus()
    return main()


if __name__ == "__main__":
    sys.exit(_entry() or 0)
