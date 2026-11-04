# -*- coding: utf-8 -*-
"""simpson_port.py —— F4 双平面 Simpson 的"无库"参考移植：板端 src/job_simpson.c 逐行照这份写。

ef_simpson.py 用 Pillow（NEAREST 缩放）、scipy（连通域、填洞）、scikit-image（find_contours）；板上没有这些库
（也不该依赖），所以这里只用纯 Python + numpy 的逐元素运算，把三个库在本用途下的行为逐位重写一遍：
  * Pillow Image.resize(NEAREST)：ImagingScaleAffine，源坐标 = int(a2 + a0 * 0.5 + k * a0) 逐次累加（不是乘法）；
  * scipy clean：4 连通最大块（同大取光栅序最先出现的）+ 4 连通背景填洞；
  * skimage find_contours(seg, 0.5)：二值图上的 marching squares（每个 2x2 方格按 case 出线段，'low' 连接）
    + _assemble_contours 的拼接规则（合并时保留编号小的，首尾相接；结果按编号排序取第一条）；
  * 弦搜索里的 |角度| <= 45° 用几何等价式 dc >= |dr| 判断（坐标都是半整数，等价性在 verify 里穷举验证过）。
其余（中点、心尖、法向、半像素步进、圆盘、取整）与 ef_simpson.py 同一套浮点运算，结果逐位相同。
命令行：python simpson_port.py  —— 在 CAMUS 训练 + 验证集 450 例专家掩膜上和 ef_simpson.volumes 逐位比对。
"""
import math

import numpy as np


# ---------------------------------------------------------------- Pillow NEAREST
def _nearest_index(n_in, n_out):
    a0 = n_in / n_out                     # (double)(box[2] - box[0]) / xsize
    xo = 0.0 + a0 * 0.5                   # a[2] + a[0] * 0.5
    idx = []
    for _ in range(n_out):
        xin = -1 if xo < 0.0 else int(xo)
        idx.append(min(max(xin, 0), n_in - 1))   # out-of-range never happens for plain resizes
        xo += a0
    return idx


def resize_nearest(m, w, h):
    """m [H, W] -> [h, w] like Image.fromarray(m).resize((w, h), Image.NEAREST)."""
    m = np.asarray(m)
    yi = _nearest_index(m.shape[0], h)
    xi = _nearest_index(m.shape[1], w)
    return m[np.ix_(yi, xi)]


# ---------------------------------------------------------------- scipy clean (largest 4-component + fill holes)
def clean(mask):
    m = np.asarray(mask, bool)
    h, w = m.shape
    lab = -np.ones((h, w), int)
    sizes = []
    for y0 in range(h):
        for x0 in range(w):
            if not m[y0, x0] or lab[y0, x0] >= 0:
                continue
            k = len(sizes)
            st = [(y0, x0)]
            lab[y0, x0] = k
            n = 0
            while st:
                y, x = st.pop()
                n += 1
                for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= yy < h and 0 <= xx < w and m[yy, xx] and lab[yy, xx] < 0:
                        lab[yy, xx] = k
                        st.append((yy, xx))
            sizes.append(n)
    if len(sizes) > 1:
        m = lab == int(np.argmax(sizes))
    out = m.copy()
    outside = np.zeros((h, w), bool)
    st = [(y, x) for y in range(h) for x in (0, w - 1)] + [(y, x) for x in range(w) for y in (0, h - 1)]
    st = [(y, x) for y, x in st if not out[y, x]]
    for y, x in st:
        outside[y, x] = True
    while st:
        y, x = st.pop()
        for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= yy < h and 0 <= xx < w and not out[yy, xx] and not outside[yy, xx]:
                outside[yy, xx] = True
                st.append((yy, xx))
    return out | ~outside


# ---------------------------------------------------------------- skimage find_contours(binary, 0.5)[0]
def first_contour(seg):
    """Points are kept doubled (integers) while assembling; returned as float (row, col)."""
    s = np.asarray(seg) > 0.5
    rows, cols = s.shape
    segs = []
    for r0 in range(rows - 1):
        for c0 in range(cols - 1):
            ul, ur, ll, lr = s[r0, c0], s[r0, c0 + 1], s[r0 + 1, c0], s[r0 + 1, c0 + 1]
            case = ul * 1 + ur * 2 + ll * 4 + lr * 8
            if case in (0, 15):
                continue
            R0, C0 = 2 * r0, 2 * c0
            top, bottom, left, right = (R0, C0 + 1), (R0 + 2, C0 + 1), (R0 + 1, C0), (R0 + 1, C0 + 2)
            if case == 1: segs.append((top, left))
            elif case == 2: segs.append((right, top))
            elif case == 3: segs.append((right, left))
            elif case == 4: segs.append((left, bottom))
            elif case == 5: segs.append((top, bottom))
            elif case == 6: segs += [(right, top), (left, bottom)]
            elif case == 7: segs.append((right, bottom))
            elif case == 8: segs.append((bottom, right))
            elif case == 9: segs += [(top, left), (bottom, right)]
            elif case == 10: segs.append((bottom, top))
            elif case == 11: segs.append((bottom, left))
            elif case == 12: segs.append((left, right))
            elif case == 13: segs.append((top, right))
            elif case == 14: segs.append((left, top))
    # _assemble_contours with linked lists: merged contour = head + tail, keeps the smaller number
    nxt, pts = [], []
    first, last, alive = [], [], []
    starts, ends = {}, {}

    def node(p):
        pts.append(p)
        nxt.append(-1)
        return len(pts) - 1
    for a, b in segs:
        tail = starts.pop(b, None)
        head = ends.pop(a, None)
        if tail is not None and head is not None:
            if tail == head:
                n = node(b)
                nxt[last[head]] = n
                last[head] = n
            else:
                keep, gone = (head, tail) if tail > head else (tail, head)
                hd, tl = head, tail                      # sequence = head + tail either way
                nxt[last[hd]] = first[tl]
                f, l = first[hd], last[tl]
                if keep == tail:
                    starts.pop(pts[first[hd]], None)
                alive[gone] = False
                first[keep], last[keep] = f, l
                starts[pts[f]] = keep
                ends[pts[l]] = keep
        elif tail is None and head is None:
            n1, n2 = node(a), node(b)
            nxt[n1] = n2
            k = len(first)
            first.append(n1)
            last.append(n2)
            alive.append(True)
            starts[a] = k
            ends[b] = k
        elif head is None:
            n = node(a)
            nxt[n] = first[tail]
            first[tail] = n
            starts[a] = tail
        else:
            n = node(b)
            nxt[last[head]] = n
            last[head] = n
            ends[b] = head
    k = alive.index(True)
    out, n = [], first[k]
    while n != -1:
        out.append(pts[n])
        if n == last[k]:
            break
        n = nxt[n]
    return np.array(out, float) / 2.0


# ---------------------------------------------------------------- ef_simpson geometry, same float operations
def to_isotropic(mask, spacing):
    scaling = np.array(spacing) / min(spacing)
    h, w = (np.array(mask.shape) * scaling).round().astype(int)
    return resize_nearest(np.asarray(mask, np.uint8), int(w), int(h)), min(spacing)


def _distance_to_edge(seg, point, normal):
    d = 8.0
    while True:
        y = int(np.round(point[0] + d * normal[0]))
        x = int(np.round(point[1] + d * normal[1]))
        if not (0 <= y < seg.shape[0] and 0 <= x < seg.shape[1]) or seg[y, x] == 0:
            return d
        d += 0.5


def diameters(mask, spacing):
    seg, iso = to_isotropic(mask, spacing)
    c = first_contour(seg)
    n = len(c)
    best, bi, bj = 0.0, None, None
    for i in range(2, n):
        ri, ci = c[i]
        for j in range(i):
            dr, dc = c[j][0] - ri, c[j][1] - ci
            if not dc >= abs(dr):                       # |degrees(arctan2(dr, dc))| <= 45
                continue
            v0, v1 = c[j][0] - ri, c[j][1] - ci           # v = p1 - p0 (p0 = contour[i], p1 = contour[j])
            nv = math.sqrt(v0 * v0 + v1 * v1)
            ok = True
            for k in range(j + 1, i):
                w0, w1 = ri - c[k][0], ci - c[k][1]
                if not (abs(v0 * w1 - v1 * w0) / nv <= 8) if nv > 0 else True:
                    ok = False
                    break
            if ok:
                dist = math.sqrt(dr * dr + dc * dc)
                if best < dist:
                    best, bi, bj = dist, i, j
    mid = int(bj + round((bi - bj) / 2))
    lengths = [math.sqrt((p[0] - c[mid][0]) ** 2 + (p[1] - c[mid][1]) ** 2) for p in c]
    apex = int(np.argmax(lengths))
    axis_len = lengths[apex]
    direction = c[apex] - c[mid]
    nn = math.sqrt(direction[1] * direction[1] + direction[0] * direction[0])
    normal = np.array([-direction[1] / nn, direction[0] / nn])
    d = []
    for k in range(20):
        f = k * (1.0 / 20)
        p = c[mid] + direction * f
        d1, d2 = _distance_to_edge(seg, p, normal), _distance_to_edge(seg, p, -normal)
        d.append((d1 + d2) * iso)
    return np.array(d), axis_len * iso / 20


def volumes(a2c_ed, a2c_es, a2c_spacing, a4c_ed, a4c_es, a4c_spacing):
    d2e, s2e = diameters(a2c_ed, a2c_spacing)
    d2s, s2s = diameters(a2c_es, a2c_spacing)
    d4e, s4e = diameters(a4c_ed, a4c_spacing)
    d4s, s4s = diameters(a4c_es, a4c_spacing)
    step = max(s2e, s2s, s4e, s4s)
    vol = lambda a, b: int(round(float(np.sum((a / 1000) * (b / 1000)) * (step / 1000) * np.pi / 4 * 1e6)))
    return vol(d2e, d4e), vol(d2s, d4s)
