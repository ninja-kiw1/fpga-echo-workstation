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

import confidence
import imageio
import interactive
import proto
import uistyle
from uistyle import FONT, FONT_MONO

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
    import ef_simpson as efs
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
    import jobs
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
    import jobs
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
    import cardiac_measure as cm
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
    import ef_simpson as _efs    # noqa: F401  (numpy + scipy + scikit-image + Pillow)
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
        ttk.Button(right.head, text="连续帧配准…", command=self._open_reg).pack(side="right")
        ttk.Button(right.head, text="心肌应变…", command=self._open_strain).pack(side="right", padx=(0, 6))
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
        flags = proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS
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
            self.top.after(30, self._poll)

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
            "与板端逐字节一致 ✓" if s.verified else ("与板端不一致 ✗" if s.verified is False else "")))
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

    # ---- 连续帧配准（F5）----
    def _open_reg(self):
        if self.frames is None:
            self.var_status.set("先载入一段序列，再打开配准")
            return None
        if self.playing:
            self._toggle_play()
        import regwin
        return self._child("reg_win", regwin.RegWindow)

    def _child(self, attr, cls):
        """配准 / 应变子窗口：同一段序列已经开着就拿到前面，不再叠一个新的（应变窗口一开就会在板上算）；
        换了序列就关掉旧的（它记着旧序列的帧数）再开新的。"""
        win = getattr(self, attr, None)
        if uistyle.alive(win):
            if getattr(win, "frames_ref", None) is self.frames:
                uistyle.bring_up(win.top)
                return win
            win.close()
        win = cls(self)
        win.frames_ref = self.frames
        setattr(self, attr, win)
        return win

    # ---- 部分 E 心肌应变（探索性；板上任务 strain，服务端 v5.19 起）----
    def _open_strain(self):
        if self.frames is None:
            self.var_status.set("先载入一段序列（CAMUS 半周期：第 0 帧 ED、最后一帧 ES），再算应变")
            return None
        if self.playing:
            self._toggle_play()
        import strainwin
        return self._child("strain_win", strainwin.StrainWindow)

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
