# -*- coding: utf-8 -*-
"""ppref.py -- NumPy model of the pp_layer HLS kernel (PL post-processing), same algorithm step by step.

pp_layer reads int8 logits (h x w) and, inside a window (x0, y0, cw, ch), produces:
  * mask m: k == 1: logit >= thr;  k in {2, 4}: bilinear x k of the logits > tn / 2
    (integer form of ct_volume.upsample_bilinear(q, k) > tn / 2, weights in units of 1 / (2k));
  * count = pixels of m, comps = connected components of m (4- or 8-connected), largest component area;
  * 4 x 4 tile sums of the raw logits (ai_view_fill_reg, whole frame);
  * optionally CLEAN = keep the largest component (ties: earliest in raster order) + fill holes
    (background 4-connected, not touching the border), i.e. echo_ws.clean;
  * the resulting mask as bits.

Connected components: one raster scan with provisional labels (local rule, no unions in the line buffer)
and a union-find table whose roots are always the smallest label, so a root is the component's first
pixel in raster order (= scipy.ndimage.label numbering). Later passes replay the same local rule and map
provisional labels through the flattened table. This file is the bit-level spec for the C++ kernel and is
itself checked against scipy / the board's C code by gen_vectors.py.
"""
import numpy as np


def mask_from_logits(q, k, thr, tn, win):
    """q [h, w] int8 -> mask [ch*k, cw*k] bool for window win = (x0, y0, cw, ch) (in logit pixels)."""
    x0, y0, cw, ch = win
    q = np.asarray(q, np.int64)
    if k == 1:
        return q[y0:y0 + ch, x0:x0 + cw] >= thr
    n_h, n_w = q.shape
    d = 2 * k

    def axis(n, start, length):
        o = np.arange(start * k, (start + length) * k)
        num = 2 * o + 1 - k                       # src * 2k
        num = np.maximum(num, 0)
        i0 = num // d
        f = num - i0 * d                          # weight of i1, units of 1/(2k)
        i1 = np.minimum(i0 + 1, n - 1)
        return i0, i1, d - f, f

    ry0, ry1, ay0, ay1 = axis(n_h, y0, ch)
    rx0, rx1, ax0, ax1 = axis(n_w, x0, cw)
    rows = q[ry0, :] * ay0[:, None] + q[ry1, :] * ay1[:, None]
    v = rows[:, rx0] * ax0[None, :] + rows[:, rx1] * ax1[None, :]
    return 2 * v > tn * d * d


class CCL(object):
    """Union-find with smallest-label roots; provisional labels from a local rule."""

    def __init__(self, conn8, maxl):
        self.conn8, self.maxl = conn8, maxl
        self.parent, self.area, self.border = [], [], []
        self.overflow = False

    def find(self, a):
        while self.parent[a] != a:
            a = self.parent[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.area[ra] += self.area[rb]
        self.border[ra] |= self.border[rb]

    def neighbours(self, prev, cur, x, w):
        """labels (0 = none, else label+1) in rule order: U, L, UL, UR (UL / UR only for 8-conn)."""
        nb = [prev[x], cur[x - 1] if x > 0 else 0]
        if self.conn8:
            nb += [prev[x - 1] if x > 0 else 0, prev[x + 1] if x < w - 1 else 0]
        return nb

    def scan(self, m, record_unions=True):
        """one raster pass over bool image m -> provisional label image (-1 = background)."""
        h, w = m.shape
        prev = [0] * w
        out = np.full((h, w), -1, np.int64)
        for y in range(h):
            cur = [0] * w
            for x in range(w):
                if not m[y, x]:
                    continue
                nb = [v for v in self.neighbours(prev, cur, x, w) if v]
                if nb:
                    lab = nb[0] - 1
                    if record_unions:
                        for v in nb[1:]:
                            self.union(lab, v - 1)
                else:
                    lab = len(self.parent) if record_unions else self._next
                    if record_unions:
                        if lab >= self.maxl:
                            self.overflow = True
                        self.parent.append(lab)
                        self.area.append(0)
                        self.border.append(False)
                    else:
                        self._next += 1
                if record_unions:
                    r = self.find(lab)
                    self.area[r] += 1
                    if y == 0 or x == 0 or y == h - 1 or x == w - 1:
                        self.border[r] = True
                cur[x] = lab + 1
                out[y, x] = lab
            prev = cur
        return out

    def replay(self, m):
        self._next = 0
        return self.scan(m, record_unions=False)

    def roots(self):
        r = list(range(len(self.parent)))
        for l in range(len(r)):                 # parent[l] <= l: one forward sweep flattens
            r[l] = r[self.parent[l]]
        return np.array(r, np.int64)


def run(q, k=1, thr=1, tn=1, win=None, conn8=False, clean=False, maxl=4096):
    q = np.asarray(q, np.int8)
    h, w = q.shape
    win = win or (0, 0, w, h)
    m = mask_from_logits(q, k, thr, tn, win)
    fg = CCL(conn8, maxl)
    fg.scan(m)
    roots = fg.roots()
    is_root = [l for l in range(len(roots)) if roots[l] == l]
    best, best_area = -1, 0
    for l in is_root:                           # ties: smallest label = earliest first pixel
        if fg.area[l] > best_area:
            best, best_area = l, fg.area[l]
    res = dict(count=int(m.sum()), comps=len(is_root), largest=best_area, labels=len(fg.parent),
               overflow=fg.overflow)
    t = np.zeros(16, np.int64)
    if h % 4 == 0 and w % 4 == 0:
        t = q.astype(np.int64).reshape(4, h // 4, 4, w // 4).sum(axis=(1, 3)).ravel()
    res['tiles'] = t
    if not clean:
        res['mask'] = m
        return res
    prov = fg.replay(m)
    kept = m if best < 0 else (prov >= 0) & (roots[np.maximum(prov, 0)] == best)
    bg = CCL(False, maxl)
    bg.scan(~kept)
    broots = bg.roots()
    bprov = bg.replay(~kept)
    border = np.array([bg.border[broots[l]] for l in range(len(broots))] or [True], bool)
    holes = (bprov >= 0) & ~border[np.maximum(bprov, 0)]
    res['mask'] = kept | holes
    res['bg_labels'] = len(bg.parent)
    res['overflow'] = res['overflow'] or bg.overflow
    return res
