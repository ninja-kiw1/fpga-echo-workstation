# -*- coding: utf-8 -*-
"""ct_volume.py —— ⑤ CT 整卷分割的上位机计算部分（numpy / scipy，不依赖 torch）。

和离线训练 / 评测（E:/PLD/ct/common.py）逐字节一致的部分：
  * 软组织窗 W400 / L40 -> uint8；
  * 4x4 块平均（四舍五入，整数运算）-> 128x128 负载；
  * z3 编码：R / G / B = 第 z-o / z / z+o 层（o = 最接近 5 mm 的层数，至少 1，到头就夹住）；
  * 板端回 128 网格的 int8 logit，上位机双线性放大到 512（半像素中心、边缘夹住，k = 4 时在 float32 里是精确值），> 0.5 为脾脏；
  * 可选：保留最大的三维 26 连通块。
另外：NIfTI 读取（按 sform / qform 转成放射科视图：前方朝上、病人右侧在图像左侧）、体积、三维表面渲染。
仅用于工程教学与算法演示，不作为临床诊断依据。
"""
import gzip
import math
import os
import struct

import numpy as np

WINDOW = (40.0, 400.0)                       # 窗位、窗宽（HU），与训练一致
_DTYPES = {2: np.uint8, 4: np.int16, 8: np.int32, 16: np.float32, 64: np.float64,
           256: np.int8, 512: np.uint16, 768: np.uint32}


def _quat_to_matrix(b, c, d, pix, qfac):
    a = math.sqrt(max(0.0, 1.0 - (b * b + c * c + d * d)))
    r = np.array([[a * a + b * b - c * c - d * d, 2 * (b * c - a * d), 2 * (b * d + a * c)],
                  [2 * (b * c + a * d), a * a + c * c - b * b - d * d, 2 * (c * d - a * b)],
                  [2 * (b * d - a * c), 2 * (c * d + a * b), a * a + d * d - c * c - b * b]])
    return r * np.array([pix[0], pix[1], pix[2] * (qfac or 1.0)])[None, :]


def read_nifti(path):
    """-> (array [k, j, i] as stored, voxel size (dx, dy, dz) mm, 3x3 direction*spacing matrix or None)."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    if struct.unpack_from("<i", raw, 0)[0] != 348:
        raise ValueError("不是小端 NIfTI-1 文件")
    dim = struct.unpack_from("<8h", raw, 40)
    datatype = struct.unpack_from("<h", raw, 70)[0]
    pixdim = struct.unpack_from("<8f", raw, 76)
    vox_offset = int(struct.unpack_from("<f", raw, 108)[0])
    slope, inter = struct.unpack_from("<2f", raw, 112)
    qform_code, sform_code = struct.unpack_from("<2h", raw, 252)
    if dim[0] < 3 or datatype not in _DTYPES:
        raise ValueError("只支持三维 NIfTI（dim=%d，datatype=%d）" % (dim[0], datatype))
    shape = tuple(int(d) for d in dim[1:4])
    a = np.frombuffer(raw, _DTYPES[datatype], int(np.prod(shape)), vox_offset).reshape(shape[::-1])
    if slope not in (0.0, 1.0) or inter != 0.0:
        a = a * np.float32(slope) + np.float32(inter)
    m = None
    if sform_code > 0:
        m = np.array(struct.unpack_from("<12f", raw, 280), np.float64).reshape(3, 4)[:, :3]
    elif qform_code > 0:
        b, c, d = struct.unpack_from("<3f", raw, 256)
        m = _quat_to_matrix(b, c, d, pixdim[1:4], pixdim[0])
    return a, tuple(float(p) for p in pixdim[1:4]), m


def to_radiological(a, m):
    """[k, j, i] -> [slice (下->上), row (前->后), col (病人右->左)]。只处理轴对齐的方向矩阵；
    没有方向信息时按 MSD 的约定（sform 对角为正）处理。"""
    if m is None:
        m = np.eye(3)
    world = [int(np.argmax(np.abs(m[:, ax]))) for ax in range(3)]          # array axis i/j/k -> world x/y/z
    sign = [float(np.sign(m[world[ax], ax])) or 1.0 for ax in range(3)]
    if sorted(world) != [0, 1, 2]:
        raise ValueError("方向矩阵不是轴对齐的")
    # numpy axes of a: 0 = k, 1 = j, 2 = i  ->  array axis index ax = 2 - numpy axis
    order = [2 - world.index(2), 2 - world.index(1), 2 - world.index(0)]     # numpy axes giving z, y, x
    v = np.transpose(a, order)
    sz, sy, sx = sign[world.index(2)], sign[world.index(1)], sign[world.index(0)]
    if sz < 0:
        v = v[::-1]
    if sy > 0:                    # +y = anterior (RAS): row 0 must be the most anterior
        v = v[:, ::-1]
    if sx > 0:                    # +x = patient's right: column 0 must be the patient's right
        v = v[:, :, ::-1]
    return np.ascontiguousarray(v), order


def load_ct(path):
    """CT 卷 -> dict(hu [z,512,512] float32, spacing (dx,dy,dz), label 或 None, name)。
    MSD 的 imagesTr/xxx.nii.gz 会自动找 labelsTr/xxx.nii.gz（只用来对照显示和算 Dice）。"""
    a, sp, m = read_nifti(path)
    hu, order = to_radiological(np.asarray(a, np.float32), m)
    spacing = [sp[0], sp[1], sp[2]]
    spacing = [spacing[2 - order[2]], spacing[2 - order[1]], spacing[2 - order[0]]]   # x, y, z after transpose
    if hu.shape[1:] != (512, 512):
        raise ValueError("这一版只支持 512x512 的层面（这个文件是 %dx%d）" % hu.shape[1:])
    label = None
    d, base = os.path.split(path)
    lp = os.path.join(os.path.dirname(d), "labelsTr", base)
    if os.path.basename(d) in ("imagesTr", "imagesTs") and os.path.exists(lp):
        la, _, lm = read_nifti(lp)
        label, _ = to_radiological(np.asarray(la) > 0, lm)
        if label.shape != hu.shape:
            label = None
    return dict(hu=hu, spacing=tuple(spacing), label=label, name=base.split(".nii")[0], path=path)


ROI_CROP, ROI_K, ROI_ZMARGIN = 256, 2, 2      # 第二阶段 ROI 精分割（与 E:/PLD/ct/roi.py 一致）


def roi_window(m0, dz_mm):
    """粗结果三维掩膜 -> 每卷一个 256x256 框 (y0, x0, z0, z1)；粗结果为空时返回 None。"""
    zs = np.nonzero(m0.reshape(len(m0), -1).any(1))[0]
    if len(zs) == 0:
        return None
    r, c = np.nonzero(m0.any(0))
    cy, cx = (int(r.min()) + int(r.max()) + 1) // 2, (int(c.min()) + int(c.max()) + 1) // 2
    y0 = min(max(cy - ROI_CROP // 2, 0), 512 - ROI_CROP)
    x0 = min(max(cx - ROI_CROP // 2, 0), 512 - ROI_CROP)
    o = neighbour_offset(dz_mm)
    return y0, x0, max(0, int(zs.min()) - ROI_ZMARGIN * o), min(len(m0) - 1, int(zs.max()) + ROI_ZMARGIN * o)


def roi_payloads(u8_512, m0, win, mode, dz_mm):
    """框内每层的 128x128x3 负载（bytes）。roi_z3：上 / 本 / 下层；roi_prior：本层 / 粗结果 / 上下层平均。"""
    y0, x0, z0, z1 = win
    nz = len(u8_512)
    o = neighbour_offset(dz_mm)
    z = np.arange(nz)
    p, n = np.clip(z - o, 0, nz - 1), np.clip(z + o, 0, nz - 1)
    zz = np.arange(z0, z1 + 1)
    crop = lambda a: a[..., y0:y0 + ROI_CROP, x0:x0 + ROI_CROP]
    if mode == "roi_z3":
        ch = [box(crop(u8_512[p[zz]]), ROI_K), box(crop(u8_512[zz]), ROI_K), box(crop(u8_512[n[zz]]), ROI_K)]
    elif mode == "roi_prior":
        prior = crop(m0[zz]).reshape(len(zz), ROI_CROP // 2, 2, ROI_CROP // 2, 2).sum(axis=(2, 4)) >= 2
        nb = (crop(u8_512[p[zz]]).astype(np.uint16) + crop(u8_512[n[zz]]) + 1) >> 1
        ch = [box(crop(u8_512[zz]), ROI_K), prior.astype(np.uint8) * 255, box(nb.astype(np.uint8), ROI_K)]
    else:
        raise ValueError(mode)
    rgb = np.stack(ch, axis=-1)
    return [np.ascontiguousarray(rgb[i]).tobytes() for i in range(len(zz))]


def roi_combine(logits128, win, nz):
    """精分割 int8 logit [z1-z0+1, 128, 128] -> 双线性放大 2 倍贴回框内 -> 最大三维连通块 -> [nz, 512, 512]。"""
    y0, x0, z0, z1 = win
    m = np.zeros((nz, 512, 512), bool)
    m[z0:z1 + 1, y0:y0 + ROI_CROP, x0:x0 + ROI_CROP] = upsample_bilinear(np.asarray(logits128, np.int8), ROI_K) > 0.5
    return largest_cc3d(m)


def window_u8(hu, level=WINDOW[0], width=WINDOW[1]):
    lo = level - width / 2
    return np.clip(np.rint((np.asarray(hu, np.float32) - lo) * (255.0 / width)), 0, 255).astype(np.uint8)


def box(u8, k=4):
    """[..., H, W] -> [..., H/k, W/k]：k x k 平均，四舍五入（整数运算）。"""
    h, w = np.shape(u8)[-2:]
    s = np.asarray(u8).reshape(*np.shape(u8)[:-2], h // k, k, w // k, k).astype(np.uint32).sum(axis=(-3, -1))
    return ((s + k * k // 2) // (k * k)).astype(np.uint8)


def neighbour_offset(dz_mm, target_mm=5.0):
    return max(1, int(round(target_mm / dz_mm)))


def payloads(u8_512, dz_mm, mode="z3"):
    """[z,512,512] uint8 -> 每层一个 128x128x3 的紧凑帧负载（bytes，RGB 交错）。"""
    small = box(u8_512, 4)
    nz = len(small)
    if mode == "rep":
        rgb = np.repeat(small[..., None], 3, axis=-1)
    else:
        o = neighbour_offset(dz_mm)
        z = np.arange(nz)
        rgb = np.stack([small[np.clip(z - o, 0, nz - 1)], small, small[np.clip(z + o, 0, nz - 1)]], axis=-1)
    return [np.ascontiguousarray(rgb[i]).tobytes() for i in range(nz)]


def upsample_bilinear(q, k=4):
    q = np.asarray(q, np.float32)
    n = q.shape[-1]
    src = np.maximum((np.arange(n * k, dtype=np.float64) + 0.5) / k - 0.5, 0.0)
    i0 = np.floor(src).astype(np.int64)
    i1 = np.minimum(i0 + 1, n - 1)
    w1 = (src - i0).astype(np.float32)
    w0 = np.float32(1) - w1
    rows = q[:, i0, :] * w0[None, :, None] + q[:, i1, :] * w1[None, :, None]
    return rows[:, :, i0] * w0[None, None, :] + rows[:, :, i1] * w1[None, None, :]


def pp_request(k):
    """PL 后处理请求（板端 v5.10+、固件带 pp_layer）：整帧 128 logit 双线性放大 k 倍后 > 0.5 的掩膜，
    与 upsample_bilinear(q, k) > 0.5 逐位相同（整数实现，E:/PLD/tcp_image/tcp_image_rx/ai/pp_hls）。"""
    import proto
    return dict(op=proto.PP_OP_MASK, k=k, tn=1)


def pp_mask(pp, n):
    """板端 PL 后处理块 -> [n, n] bool 掩膜；没做成时 None（退回 PC 上放大）。"""
    if not pp or pp.get("status") != 0 or pp.get("mask") is None:
        return None
    import proto
    return proto.pp_mask_array(pp)[:n, :n]


def postprocess_masks(m512, variant):
    """每层已在 PL 上放大好的 [z,512,512] 掩膜 -> 与 postprocess(logits, variant) 相同（bilinear 系列）。"""
    return largest_cc3d(m512) if variant.endswith("+lcc") else np.asarray(m512, bool)


def roi_combine_masks(m256, win, nz):
    """同 roi_combine，但框内掩膜已在 PL 上放大好（[z1-z0+1, 256, 256]）。"""
    y0, x0, z0, z1 = win
    m = np.zeros((nz, 512, 512), bool)
    m[z0:z1 + 1, y0:y0 + ROI_CROP, x0:x0 + ROI_CROP] = m256
    return largest_cc3d(m)


def largest_cc3d(mask):
    from scipy import ndimage
    lab, n = ndimage.label(mask, structure=np.ones((3, 3, 3), bool))
    if n <= 1:
        return mask.astype(bool)
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == int(np.argmax(sizes))


def postprocess(logits128, variant):
    """[z,128,128] int8 -> [z,512,512] bool。variant: nearest / bilinear，可带 +lcc。"""
    q = np.asarray(logits128, np.int8)
    if variant.startswith("bilinear"):
        m = np.empty((len(q), 512, 512), bool)
        for i in range(0, len(q), 32):
            m[i:i + 32] = upsample_bilinear(q[i:i + 32]) > 0.5
    else:
        m = np.repeat(np.repeat(q >= 1, 4, 1), 4, 2)
    return largest_cc3d(m) if variant.endswith("+lcc") else m


def dice3d(pred, gt):
    s = int(pred.sum()) + int(gt.sum())
    return 1.0 if s == 0 else 2.0 * int((pred & gt).sum()) / s


def volume_ml(mask, spacing):
    return float(mask.sum()) * spacing[0] * spacing[1] * spacing[2] / 1000.0


# ---------------------------------------------------------------- 脾脏测量（辅助测量与复核，不作诊断）
# 定义（板端 job_ct.c 的 spleen_measure 逐位相同：凸包、卡尺都是标量循环，浮点运算顺序一致）：
#   头尾长度 L = 含脾脏的层数跨度 (z_hi - z_lo + 1) × 层厚；
#   宽 W = 各轴位层面里最大的 Feret 直径（像素中心之间，按 dx / dy 换成 mm；取 W 最大的那一层，同值取靠下的层）；
#   厚 T = 同一层面上垂直于 W 方向的跨度（像素中心）；脾指数 SI = L × W × T（cm³）。
# 门限（出处见 SPLEEN_REFS，2026-10-13 核对过原文）：
#   按体重的脾大体积阈值 3.01 × 体重(kg) + 127 mL，体重 > 125 kg 时固定 503 mL（Perez et al., AJR 2023，CTisus 摘要）；
#   Radiopaedia（articles/6003，10-14 用户截图核对）：脾指数正常范围 120–480（最可靠的单项指标）；头尾长度 > 9.5–10.5 cm 提示脾大、
#   > 14.5 cm 提示巨脾；公式估算体积 30 + 0.58 × L × W × T（cm）在健康人中的范围 107.2–314.5 cm³（均值 214.6）。
#   任务清单里"约 314 mL 轻度脾大"是对这个范围上限的误读：314.5 是**公式估算体积**的健康人上限，不是分割体积的脾大阈值；
#   **这两个范围（脾指数 120–480、公式体积 107.2–314.5）都不套用**：它们来自手工测量（一层上的长径 + 垂直最大尺寸），
#   本系统的 W / T 是自动取最大值（弯月形的脾最长径斜跨弯曲部分），VAL 7 卷**标注掩膜**上公式估算体积平均是真实体积的 1.64 倍
#   （1.23–1.85，最小二乘系数 0.35 而不是 0.58），按原文范围会把多数正常脾判成"超出"。所以脾指数只显示数值，体积只和按体重的阈值比；
#   头尾长度定义与原文基本一致（原文是首末层间距，这里多一层厚），保留 > 10.5 / > 14.5 cm 的提示。原文"宽 > 10.6 cm"指最大前后径，定义不同，不套用；
#   随访体积下降 ≥ 35%（IWG-MRT 修订标准的脾脏缓解：MRI/CT 体积下降 ≥ 35%，疗效需维持 ≥ 12 周，Tefferi et al., Blood 2013）。
SPLEEN_SI_NORMAL = (120.0, 480.0)            # 脾指数正常范围（无单位，cm 相乘）
SPLEEN_EST_NORMAL = (107.2, 314.5)           # 公式估算体积的健康人范围（cm³）
SPLEEN_LEN_CM = (10.5, 14.5)                 # 头尾长度：> 9.5–10.5 cm 脾大（取上沿 10.5），> 14.5 cm 巨脾
SPLEEN_REFS = {
    "volume": "按体重的脾大体积阈值 3.01 × 体重(kg) + 127 mL（> 125 kg 固定 503 mL）；长度测量检出体积性脾大的效果欠佳。"
              "Perez AA et al., AJR 2023;221:1–9，摘要见 https://ctisus.com/learning/pearls/spleen/artificial-intelligence",
    "imaging": "脾指数正常 120–480；头尾长度 > 9.5–10.5 cm 脾大、> 14.5 cm 巨脾；公式估算体积 30 + 0.58 × L × W × T 的健康人范围 "
               "107.2–314.5 cm³：Radiopaedia, Splenomegaly, https://radiopaedia.org/articles/6003",
    "svr35": "IWG-MRT/ELN 修订疗效标准：脾脏缓解需 MRI/CT 证实体积下降 ≥ 35%，疗效需维持 ≥ 12 周。"
             "Tefferi A et al., Blood 2013;122:1395–1398，https://pmc.ncbi.nlm.nih.gov/articles/PMC4828070",
}


# 分割体积的误差带（预测 / 标注 − 1，%）：VAL 7 卷，E:/PLD/ct/results/eval/two_stage_roi_prior/val.json；(平均, 标准差, 卷数)。
# 测试集只用过一次（两阶段平均 −2.5%），这里不用它定任何东西。
SPLEEN_VOL_ERR = {"roi": (-1.63, 3.23, 7), "bilinear+lcc": (0.21, 4.84, 7)}


def _hull(pts):
    """单调链凸包（pts 为 (c, r) 整数对，已按 (c, r) 排序、无重复），去掉共线点，逆时针。"""
    if len(pts) <= 2:
        return list(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def slice_calipers(m2, dx, dy):
    """一层轴位掩膜 -> (W mm, T mm, 端点 (c0, r0, c1, r1))；空层 None。"""
    pts = []
    for r in range(m2.shape[0]):
        cs = np.flatnonzero(m2[r])
        if len(cs):
            pts.append((int(cs[0]), r))
            if cs[-1] != cs[0]:
                pts.append((int(cs[-1]), r))
    if not pts:
        return None
    pts.sort()
    h = _hull(pts)
    best, bi, bj = -1.0, 0, 0
    for i in range(len(h)):
        for j in range(i + 1, len(h)):
            ex = (h[j][0] - h[i][0]) * dx
            ey = (h[j][1] - h[i][1]) * dy
            d2 = ex * ex + ey * ey
            if d2 > best:
                best, bi, bj = d2, i, j
    w = math.sqrt(best) if best > 0 else 0.0
    if w > 0:
        nx = -((h[bj][1] - h[bi][1]) * dy) / w
        ny = ((h[bj][0] - h[bi][0]) * dx) / w
        pmin = pmax = h[0][0] * dx * nx + h[0][1] * dy * ny
        for p in h:
            q = p[0] * dx * nx + p[1] * dy * ny
            pmin = q if q < pmin else pmin
            pmax = q if q > pmax else pmax
        t = pmax - pmin
    else:
        t = 0.0
    return w, t, (h[bi][0], h[bi][1], h[bj][0], h[bj][1])


def spleen_measure(mask, spacing):
    """最终掩膜 [z, row(前->后), col(右->左)] + (dx, dy, dz) -> 测量 dict（mm / mL / cm³）；空掩膜 None。"""
    zs = np.flatnonzero(mask.reshape(len(mask), -1).any(1))
    if not len(zs):
        return None
    dx, dy, dz = float(spacing[0]), float(spacing[1]), float(spacing[2])
    z_lo, z_hi = int(zs[0]), int(zs[-1])
    length = (z_hi - z_lo + 1) * dz
    best = None
    for z in range(z_lo, z_hi + 1):
        c = slice_calipers(mask[z], dx, dy)
        if c is not None and (best is None or c[0] > best[1]):
            best = (z, c[0], c[1], c[2])
    z, w, t, ends = best
    return dict(volume_ml=volume_ml(mask, spacing), length_mm=length, width_mm=w, thickness_mm=t,
                splenic_index=length * w * t / 1000.0, z_range=[z_lo, z_hi], width_slice=z, width_ends=list(ends))


def spleen_weight_threshold(weight_kg):
    """Perez et al. AJR 2023：3.01 × 体重(kg) + 127 mL；体重 > 125 kg 时固定 503 mL。"""
    w = float(weight_kg)
    return 503.0 if w > 125.0 else 3.01 * w + 127.0


def spleen_assess(m, weight_kg=None, err_band=None):
    """测量 -> 参考阈值提示（文字列表）。err_band = (平均偏差 %, 标准差 %)：分割体积在验证集上的误差。"""
    v = m["volume_ml"]
    si = m["splenic_index"]
    est = 30.0 + 0.58 * si
    lcm = m["length_mm"] / 10.0
    out = dict(si=si, est_ml=est,
               length_flag=2 if lcm > SPLEEN_LEN_CM[1] else (1 if lcm > SPLEEN_LEN_CM[0] else 0),
               weight_limit=None, over_weight_limit=None, lines=[])
    s = "体积 %.0f mL" % v
    if err_band:
        b, sd = err_band
        lo, hi = v / (1 + (b + sd) / 100.0), v / (1 + (b - sd) / 100.0)
        s += "（按验证集分割误差 %+.1f%% ± %.1f%% 换算，真实体积约 %.0f–%.0f mL）" % (b, sd, min(lo, hi), max(lo, hi))
    out["lines"].append(s)
    out["lines"].append("头尾长度 %.1f cm，最大轴位宽 %.1f cm、厚 %.1f cm，脾指数 %.0f cm³（长 × 宽 × 厚）" % (
        m["length_mm"] / 10, m["width_mm"] / 10, m["thickness_mm"] / 10, m["splenic_index"]))
    out["lines"].append("脾指数 %.0f 只作记录，不和文献范围 120–480 比较：文献是手工测量，本系统自动取最大径，"
                        "验证集上偏大（按文献公式换算的体积平均是真实体积的 1.6 倍），体积判断以分割体积为准" % si)
    out["lines"].append("头尾长度 %.1f cm：%s（> 9.5–10.5 cm 提示脾大，> 14.5 cm 提示巨脾；本系统按含脾层数 × 层厚，比文献的首末层间距多一层；"
                        "单项线性测量，可靠性不如体积）" % (
        lcm, ("超过 14.5 cm", "超过 10.5 cm", "未超过 10.5 cm")[2 - out["length_flag"]]))
    if weight_kg:
        lim = spleen_weight_threshold(weight_kg)
        out["weight_limit"], out["over_weight_limit"] = lim, v > lim
        out["lines"].append("按体重 %.0f kg 的脾大阈值%s = %.0f mL：%s" % (
            weight_kg, "（> 125 kg 固定）" if weight_kg > 125 else " 3.01 × 体重 + 127", lim, "超过" if v > lim else "未超过"))
    else:
        out["lines"].append("未填体重：不做体重校正的比较")
    return out


def spleen_followup(v0, v1, weeks=None):
    """两次检查的体积 -> 变化 %、是否达到 SVR35（下降 ≥ 35%）。weeks = 两次检查间隔（周），可不填。"""
    pct = (v1 - v0) / v0 * 100.0
    reached = pct <= -35.0
    s = "体积 %.0f → %.0f mL，变化 %+.1f%%：%s SVR35（下降 ≥ 35%%）" % (v0, v1, pct, "达到" if reached else "未达到")
    if reached:
        s += "；IWG-MRT 标准还要求维持 ≥ 12 周" + ("（本次间隔 %.0f 周）" % weeks if weeks else "，需结合复查时间判断")
    return dict(change_pct=pct, svr35=reached, line=s)


class Surface(object):
    """三维表面渲染：正交投影 + z 缓冲 + Lambert 光照；法向取自平滑后的体数据（不从 z 缓冲求，避免体素台阶条纹）。
    mask [z, row(前->后), col(右->左)]。构造时做一次（裁剪、层间插值到近似立方体素、高斯平滑、取表面），
    render(angle) 只做投影：绕身体纵轴转 angle 度（0 = 从前方看，病人右侧在画面左侧），约几十毫秒。"""

    def __init__(self, mask, spacing, step=2):
        from scipy import ndimage
        self.empty = True
        m = np.asarray(mask, bool)[:, ::step, ::step]
        if not m.any():
            return
        zz, rr, cc = np.nonzero(m)
        m = m[max(zz.min() - 1, 0):zz.max() + 2, max(rr.min() - 3, 0):rr.max() + 4, max(cc.min() - 3, 0):cc.max() + 4]
        sx, sy = spacing[0] * step, spacing[1] * step
        zf = max(1.0, spacing[2] / sx)
        f = ndimage.zoom(m.astype(np.float32), (zf, 1, 1), order=1)
        sz = spacing[2] * len(m) / len(f)
        g = ndimage.gaussian_filter(f, 1.2)
        m = g > 0.5
        if not m.any():
            return
        surf = m & ~ndimage.binary_erosion(m)
        z, r, c = np.nonzero(surf)
        gz, gr, gc = (a[surf] for a in np.gradient(g))
        nX, nY, nZ = gc / sx, -gr / sy, -gz / sz           # -grad g in (X = -col, Y = row, Z = slice) coordinates
        nn = np.sqrt(nX ** 2 + nY ** 2 + nZ ** 2) + 1e-9
        X, Y, Z = -(c * sx), r * sy, z * sz                # 病人右侧在画面左侧；前 -> 后 = 离观察者越来越远
        X, Y, Z = X - X.mean(), Y - Y.mean(), Z - Z.mean()
        self.p = (X, Y, Z)
        self.n = (nX / nn, nY / nn, nZ / nn)
        self.ext = max(1.0, float(np.sqrt(X ** 2 + Y ** 2 + Z ** 2).max()))
        self.vox = max(sx, sy, sz)
        self.empty = False

    def render(self, angle_deg, size=320, color=(214, 92, 74)):
        img = np.full((size, size, 3), 24, np.uint8)
        if self.empty:
            return img
        (X, Y, Z), (nX, nY, nZ) = self.p, self.n
        t = math.radians(angle_deg)
        ct, st = math.cos(t), math.sin(t)
        u, d = X * ct - Y * st, X * st + Y * ct
        nu, nd = nX * ct - nY * st, nX * st + nY * ct
        L = np.array([-0.35, 0.45, -1.0])                  # light (u, Z, depth): upper left, from the viewer
        L = L / np.linalg.norm(L)
        diff = np.clip(nu * L[0] + nZ * L[1] + nd * L[2], 0, 1)
        spec = np.clip(-nd, 0, 1) ** 24
        scale = size * 0.46 / self.ext
        pu = np.round(u * scale + size / 2).astype(np.int64)
        pv = np.round(-Z * scale + size / 2).astype(np.int64)
        rad = max(1, int(math.ceil(self.vox * scale / 2)))
        pix, dep, idx = [], [], []
        ar = np.arange(len(u))
        for du in range(-rad, rad + 1):
            for dv in range(-rad, rad + 1):
                uu, vv = pu + du, pv + dv
                ok = (uu >= 0) & (uu < size) & (vv >= 0) & (vv < size)
                pix.append(vv[ok] * size + uu[ok])
                dep.append(d[ok])
                idx.append(ar[ok])
        pix, dep, idx = np.concatenate(pix), np.concatenate(dep), np.concatenate(idx)
        o = np.lexsort((dep, pix))                         # per pixel: nearest point first
        ps = pix[o]
        first = np.r_[True, ps[1:] != ps[:-1]]
        pix, pt = ps[first], idx[o][first]
        near, far = d.min(), d.max()
        lum = (0.16 + 0.84 * diff[pt]) * (1.0 - 0.3 * (d[pt] - near) / max(far - near, 1e-6))
        flat = img.reshape(-1, 3)
        for ch in range(3):
            flat[pix, ch] = np.clip(lum * color[ch] + spec[pt] * 90, 0, 255).astype(np.uint8)
        return img


def render_surface(mask, spacing, angle_deg, size=320, step=2):
    return Surface(mask, spacing, step).render(angle_deg, size)


def contour(mask):
    """二维掩膜的边缘（4 邻域）。"""
    m = np.asarray(mask, bool)
    e = np.zeros_like(m)
    e[1:, :] |= m[1:, :] != m[:-1, :]
    e[:, 1:] |= m[:, 1:] != m[:, :-1]
    return e & m
