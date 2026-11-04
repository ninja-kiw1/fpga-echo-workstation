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

import proto

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
