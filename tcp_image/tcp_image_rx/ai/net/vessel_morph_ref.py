# -*- coding: utf-8 -*-
"""vessel_morph_ref.py —— 血管形态指标的独立参考实现（NumPy + SciPy），与板端 src/vessel_morph.c 对照。

刻意换一种写法：Zhang-Suen 用整幅数组移位一次判完一个子迭代，连通域用 scipy.ndimage.label，
链接 / 端点 / 盒计数全部向量化。只共享“定义”，不共享代码，所以两边一致才有意义。

定义（与 vessel_morph.h 相同）：
  1. 去掉 8 连通面积 < 20 px（按 512 尺度缩放）的小块；
  2. Zhang-Suen 细化；
  3. 交叉数 CN = ½ Σ|P_k − P_{k+1}|：CN == 1 端点，CN >= 3 分支像素，分支像素的 8 连通簇算一个分支点；
  4. 长度 = 正交链接数 + √2 × 斜向链接数（两点间已有正交路径时不计斜向链接）；
  5. 段 = 骨架去掉分支像素及其 8 邻域后的 8 连通块（只去分支像素的话，各臂仍斜向相连）；
     迂曲度 = Σ弧长 / Σ弦长（只算两端点、弧长 >= 10 px 的段）；
  6. 分形维数：盒边 2, 4, … <= min(w, h)/4，ln N 对 ln s 最小二乘斜率取负；
  7. FOV 内血管密度（亮度 (77R+150G+29B)>>8 > 20 为视野），以及四个象限。

analyze(mask, fov=None, res512=None) 返回与板端 64 字节块同口径的整数字段。
"""

import math

import numpy as np
from scipy import ndimage

FOV_THR = 20
MIN_BLOB_512 = 20
MIN_SEG_512 = 10
SQRT2 = 1.4142135623730951
EIGHT = np.ones((3, 3), dtype=int)
# 邻点顺序 N, NE, E, SE, S, SW, W, NW
OFFS = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]


def rnd(v):
    return 0 if v <= 0 else int(v + 0.5)


def shift(a, dx, dy):
    """out[y, x] = a[y + dy, x + dx]，越界补 0。"""
    h, w = a.shape
    out = np.zeros_like(a)
    ys, yd = (slice(dy, h), slice(0, h - dy)) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
    xs, xd = (slice(dx, w), slice(0, w - dx)) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
    out[yd, xd] = a[ys, xs]
    return out


def fov_from_rgb(rgb, w, h):
    """rgb: (rh, rw, 3) 的区域；按最近邻采样到 w x h（与板端相同的映射）。"""
    rh, rw = rgb.shape[:2]
    ys = (np.arange(h) * rh) // h
    xs = (np.arange(w) * rw) // w
    sub = rgb[ys][:, xs].astype(np.uint32)
    lum = (77 * sub[..., 0] + 150 * sub[..., 1] + 29 * sub[..., 2]) >> 8
    return lum > FOV_THR


def zhang_suen(b):
    b = b.copy()
    while True:
        removed = 0
        for step in (0, 1):
            p = [shift(b, dx, dy) for dx, dy in OFFS]
            bsum = sum(p)
            a = sum(((p[k] == 0) & (p[(k + 1) % 8] == 1)).astype(int) for k in range(8))
            if step == 0:
                c1 = (p[0] & p[2] & p[4]) == 0
                c2 = (p[2] & p[4] & p[6]) == 0
            else:
                c1 = (p[0] & p[2] & p[6]) == 0
                c2 = (p[0] & p[4] & p[6]) == 0
            kill = (b == 1) & (bsum >= 2) & (bsum <= 6) & (a == 1) & c1 & c2
            removed += int(kill.sum())
            b[kill] = 0
        if removed == 0:
            return b


def links(member):
    """member: bool 数组。返回 (正交链接数, 斜向链接数)。"""
    m = member
    e, s, wst = shift(m, 1, 0), shift(m, 0, 1), shift(m, -1, 0)
    se, sw = shift(m, 1, 1), shift(m, -1, 1)
    n_o = int((m & e).sum() + (m & s).sum())
    n_d = int((m & se & ~e & ~s).sum() + (m & sw & ~wst & ~s).sum())
    return n_o, n_d


def analyze(mask, fov=None, res512=None):
    mask = np.asarray(mask) != 0
    h, w = mask.shape
    res512 = w if res512 is None else res512
    min_blob = max(1, (MIN_BLOB_512 * res512 * res512) // (512 * 512))
    min_seg = max(2, (MIN_SEG_512 * res512) // 512)
    out = {"w": w, "h": h}

    lab, n = ndimage.label(mask, structure=EIGHT)
    area = np.bincount(lab.ravel(), minlength=n + 1)
    keep = area >= min_blob
    keep[0] = False
    b = keep[lab].astype(np.uint8)
    out["vessel_px"] = int(b.sum())

    sk = zhang_suen(b)
    p = [shift(sk, dx, dy) for dx, dy in OFFS]
    cn = sum(np.abs(p[k].astype(int) - p[(k + 1) % 8].astype(int)) for k in range(8)) // 2
    on = sk == 1
    end = on & (cn == 1)
    br = on & (cn >= 3)
    out["endpoints"] = int(end.sum())
    _, out["branches"] = ndimage.label(br, structure=EIGHT)

    n_o, n_d = links(on)
    length = n_o + n_d * SQRT2
    out["length_x10"] = rnd(length * 10.0)
    out["caliber_x100"] = rnd(out["vessel_px"] / length * 100.0) if length > 0 else 0

    near_br = ndimage.binary_dilation(br, structure=EIGHT)
    seg_lab, nseg = ndimage.label(on & ~near_br, structure=EIGHT)
    out["segments"] = int(nseg)
    sum_arc = sum_chord = 0.0
    if nseg:
        same = sum((shift(seg_lab, dx, dy) == seg_lab).astype(int) for dx, dy in OFFS)
        is_end = (seg_lab > 0) & (same == 1)
        ends_per = np.bincount(seg_lab[is_end], minlength=nseg + 1)
        ys, xs = np.nonzero(is_end)
        order = np.argsort(seg_lab[is_end] * (h * w) + ys * w + xs, kind="stable")
        ends_lab, ys, xs = seg_lab[is_end][order], ys[order], xs[order]
        first = {}
        for L, y, x in zip(ends_lab.tolist(), ys.tolist(), xs.tolist()):
            first.setdefault(L, []).append((x, y))
        for L in range(1, nseg + 1):
            if ends_per[L] != 2:
                continue
            m = seg_lab == L
            o, d = links(m)
            arc = o + d * SQRT2
            if arc < min_seg:
                continue
            (x0, y0), (x1, y1) = first[L][:2]
            chord = math.sqrt(float((x0 - x1) ** 2 + (y0 - y1) ** 2))
            if chord > 0:
                sum_arc += arc
                sum_chord += chord
    out["tortuosity_x1000"] = rnd(sum_arc / sum_chord * 1000.0) if sum_chord > 0 else 0

    sx = sy = sxx = sxy = 0.0
    m_cnt, ok, s = 0, True, 2
    while s * 4 <= min(w, h):
        bh, bw = -(-h // s), -(-w // s)
        pad = np.zeros((bh * s, bw * s), dtype=np.uint8)
        pad[:h, :w] = b
        cnt = int(pad.reshape(bh, s, bw, s).max(axis=(1, 3)).sum())
        if cnt == 0:
            ok = False
            break
        ls, lc = math.log(s), math.log(cnt)
        sx += ls
        sy += lc
        sxx += ls * ls
        sxy += ls * lc
        m_cnt += 1
        s *= 2
    out["fractal_x1000"] = 0
    if ok and m_cnt >= 3:
        den = m_cnt * sxx - sx * sx
        if den > 0:
            out["fractal_x1000"] = rnd(-(m_cnt * sxy - sx * sy) / den * 1000.0)

    f = np.ones_like(b, dtype=bool) if fov is None else (np.asarray(fov) != 0)
    yy, xx = np.mgrid[0:h, 0:w]
    q = (yy >= h // 2) * 2 + (xx >= w // 2)
    quads = []
    for k in range(4):
        fk = f & (q == k)
        nf = int(fk.sum())
        quads.append(rnd(int((b.astype(bool) & fk).sum()) / nf * 1000.0) if nf else 0)
    out["quad_x1000"] = quads
    nf = int(f.sum())
    out["fov_px"] = nf
    out["density_x1000"] = rnd(int((b.astype(bool) & f).sum()) / nf * 1000.0) if nf else 0
    return out


if __name__ == "__main__":
    # 已知答案的小例子，直接跑这个文件时自检
    img = np.zeros((64, 64), np.uint8)
    img[30:33, 5:60] = 1                       # 3 像素宽、55 长的横线
    r = analyze(img)
    print("横线:", r)
    assert r["vessel_px"] == 165 and r["branches"] == 0 and r["endpoints"] == 2
    assert r["tortuosity_x1000"] == 1000
