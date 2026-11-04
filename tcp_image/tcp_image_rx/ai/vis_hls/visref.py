# -*- coding: utf-8 -*-
"""visref.py -- bit-level NumPy model of the vis_layer HLS kernel (r2: registration engine + graphics overlay).

vis_layer is the fourth HLS operator. Like pp_layer it only sees the 256 KB tensor BRAM (one 128-bit m_axi
port); the PS writes a descriptor and the inputs there, starts the kernel, and reads the results back.
Two operations:

REG  -- registration of two grey frames (n = 128 or 256) entirely on the PL: geometric shift (integer, or
        quarter pixel with integer bilinear interpolation), cost per candidate, and the search itself
        (three-step or full), returning the best shift, the path and every evaluated (shift, SAD, overlap).
        Shifts are in quarter pixels (qx, qy); G(y, x) = B(y - qy / 4, x - qx / 4).
          source row  sy0 = y - ceil(qy / 4), fraction fy = (-qy) & 3 (weight of row sy0 + 1, units of 1/4)
          source col  sx0 = x - ceil(qx / 4), fx = (-qx) & 3
          G = ((4 - fy) * H0 + fy * H1 + 8) >> 4,  Hr = (4 - fx) * B[r, sx0] + fx * B[r, sx0 + 1]
          valid       sy0 >= 0, sy0 + (fy > 0) <= n - 1, same for x  (else G = A: difference 0)
        For qx, qy multiples of 4 this is exactly registration.shifted (B moved, A outside the overlap).
          SAD = sum over all n x n pixels of tab[|A - G|]   (tab: 256 entries, loaded by the PS)
          overlap O = (n - ceil(|qx| / 4)) * (n - ceil(|qy| / 4))  (= number of valid pixels)
        Candidate order: smaller SAD / O first (exact: S1 * O2 < S2 * O1), then |qx| + |qy|, qx, qy.
        This is registration.Registrar.best's (MAD, |dx| + |dy|, dx, dy) order: MAD = S / O as a double is
        a correctly rounded quotient, and two different fractions with O <= 65536 and S / O < 256 differ by
        more than 2^-32, far above the double spacing (2^-45 near 255), so the orders are identical.
        With tab = the measured reg_absdiff logit table (logit depends on |R - G| only, checked on all
        65 536 pairs) the SADs are the tile sums the 512 U-Net path gives today, bit for bit.
        Search (steps / radius in grid pixels):
          three-step  centre (0, 0); for each step s: the 9 candidates (cx + i s, cy + j s), j, i in -1..1,
                      best of them is the next centre (the centre's cost is known and not re-evaluated);
          full        every (dx, dy) in [-R, R]^2;
          subpel      then levels 1 / 2: the 8 neighbours at 2 (half) then 1 (quarter) quarter pixels.

OVL  -- graphics overlay of one band of output rows [ya, yb) (ow x oh output, RGB 3 bytes / pixel):
          value   nearest: V = q[y * lh // oh, x * lw // ow], D = 1  (= ai_view's (x * lw) / ow sampling)
                  bilinear (ow = k lw, oh = k lh, k in 2, 4): V = integer bilinear x k of the logits with
                  weights in units of 1 / (2k) (= pp_layer / ct_volume.upsample_bilinear), D = (2k)^2
          masks   logit mask  V >= thr D;  unsure  |V - thr D| <= band D;
                  bit mask    bits[y * mh // oh, x * mw // ow] (rows of ceil(mw / 128) words, v6c order)
          base    none (black) / grey (1 byte per pixel) / RGB / heat = lut[idx + 128] (table: 4 bytes / entry),
                  idx = V (nearest) or floor((V + D / 2) / D) (bilinear)
          colour  unsure (if on) > fill (if on and mask) > nothing; blend
                  out = (k C + (16 - k) base + 8) >> 4 per channel, alpha k in 16ths 0..16 (16 = paint C exactly)
          contour (if on) mask pixels with a 4-neighbour outside the mask or outside the frame -> colour Cc
        With k = 16, fill (255, 0, 0), nearest, logit mask: = ai_view_overlay_rgb (base RGB or none).
        Heat with nearest and lut = confidence._HEAT_LUT: = confidence.heat_rgb of the board's logit image.
        Unsure (255, 200, 0) over fill (255, 0, 0), k = 16: = confidence.uncertain_rgb.
"""
import numpy as np

# ------------------------------------------------------------------ descriptor (8 words = 32 x int32)
MAGIC = 0x564953 << 8           # 'VIS' in the top 24 bits of field 0, op in the low byte
OP_REG, OP_OVL = 1, 2
DESC_WORDS = 8
BRAM_WORDS = 16384

ST_OK, ST_BAD = 0, 4            # status (returned + out word 0)

# OVL flags (field 3)
BASE_NONE, BASE_GREY, BASE_RGB, BASE_HEAT = 0, 1, 2, 3
F_BILINEAR = 1 << 2
F_HAS_LOGIT = 1 << 3
F_HAS_BITS = 1 << 4
F_FILL_FROM_BITS = 1 << 5
F_FILL = 1 << 6
F_UNSURE = 1 << 7
F_CONTOUR = 1 << 8


def maxr(n):
    return 15 if n == 128 else 31


def ceil4(q):
    return -((-q) // 4)


# ------------------------------------------------------------------ REG
def warp(b, qx, qy):
    """G and the valid mask for shift (qx, qy) quarter pixels; b is n x n uint8."""
    b = np.asarray(b, np.int64)
    n = b.shape[0]
    cy, fy = ceil4(qy), (-qy) & 3
    cx, fx = ceil4(qx), (-qx) & 3
    y = np.arange(n)
    sy0 = y - cy
    sx0 = y - cx
    vy = (sy0 >= 0) & (sy0 + (fy > 0) <= n - 1)
    vx = (sx0 >= 0) & (sx0 + (fx > 0) <= n - 1)
    r0, r1 = np.clip(sy0, 0, n - 1), np.clip(sy0 + 1, 0, n - 1)
    c0, c1 = np.clip(sx0, 0, n - 1), np.clip(sx0 + 1, 0, n - 1)
    h0 = (4 - fx) * b[r0][:, c0] + fx * b[r0][:, c1]
    h1 = (4 - fx) * b[r1][:, c0] + fx * b[r1][:, c1]
    g = ((4 - fy) * h0 + fy * h1 + 8) >> 4
    return g, vy[:, None] & vx[None, :]


def overlap(n, qx, qy):
    return (n - ceil4(abs(qx))) * (n - ceil4(abs(qy)))


def sad(a, b, qx, qy, tab):
    a = np.asarray(a, np.int64)
    g, v = warp(b, qx, qy)
    d = np.where(v, np.abs(a - g), 0)
    return int(np.asarray(tab, np.int64)[d].sum())


def better(c1, c2):
    """c = (qx, qy, S, O): True if c1 comes before c2."""
    l, r = c1[2] * c2[3], c2[2] * c1[3]
    if l != r:
        return l < r
    return (abs(c1[0]) + abs(c1[1]), c1[0], c1[1]) < (abs(c2[0]) + abs(c2[1]), c2[0], c2[1])


def search(a, b, tab, mode, radius=7, steps=(4, 2, 1), subpel=0):
    """-> dict(best=(qx, qy), S, O, path=[(qx, qy)], records=[(qx, qy, S, O)] in evaluation order)."""
    a = np.asarray(a, np.uint8)
    b = np.asarray(b, np.uint8)
    n = a.shape[0]
    recs = []

    def ev(qx, qy):
        r = (qx, qy, sad(a, b, qx, qy, tab), overlap(n, qx, qy))
        recs.append(r)
        return r

    def pick(cands, known):
        best = known
        for c in cands:
            if known is not None and (c[0], c[1]) == (known[0], known[1]):
                continue
            r = ev(*c)
            if best is None or better(r, best):
                best = r
        return best

    def ring(c, s):
        return [(c[0] + i * s, c[1] + j * s) for j in (-1, 0, 1) for i in (-1, 0, 1)]

    path = [(0, 0)]
    if mode == 0:
        best = None
        for s in steps:
            centre = (0, 0) if best is None else best[:2]
            best = pick(ring(centre, 4 * s), best)
            path.append(best[:2])
    else:
        best = pick([(4 * dx, 4 * dy) for dy in range(-radius, radius + 1) for dx in range(-radius, radius + 1)], None)
        path.append(best[:2])
    for s in (2, 1)[:subpel]:
        best = pick(ring(best[:2], s), best)
        path.append(best[:2])
    return dict(best=best[:2], S=best[2], O=best[3], path=path, records=recs)


def reg_check(n, mode, radius, steps, subpel):
    """the descriptor checks the kernel does (False -> status BAD, nothing else touched)."""
    if n not in (128, 256) or subpel not in (0, 1, 2):
        return False
    m = maxr(n)
    if mode == 0:
        return 1 <= len(steps) <= 4 and all(1 <= s <= m for s in steps) and sum(steps) <= m
    if mode == 1:
        return 1 <= radius <= m
    return False


def max_records(mode, radius, steps, subpel):
    base = 9 + 8 * (len(steps) - 1) if mode == 0 else (2 * radius + 1) ** 2
    return base + 8 * subpel


# ------------------------------------------------------------------ OVL
def bilinear_axis(n, k):
    """per output index: i0, i1, w0, w1 (units of 1 / (2k)); = ppref.mask_from_logits.axis(n, 0, n)."""
    d = 2 * k
    o = np.arange(n * k)
    num = np.maximum(2 * o + 1 - k, 0)
    i0 = num // d
    f = num - i0 * d
    i1 = np.minimum(i0 + 1, n - 1)
    return i0, i1, d - f, f


def values(q, ow, oh, bilinear):
    """V [oh, ow] int64 and D."""
    q = np.asarray(q, np.int64)
    lh, lw = q.shape
    if not bilinear:
        return q[(np.arange(oh) * lh) // oh][:, (np.arange(ow) * lw) // ow], 1
    k = ow // lw
    ry0, ry1, ay0, ay1 = bilinear_axis(lh, k)
    rx0, rx1, ax0, ax1 = bilinear_axis(lw, k)
    rows = q[ry0, :] * ay0[:, None] + q[ry1, :] * ay1[:, None]
    return rows[:, rx0] * ax0[None, :] + rows[:, rx1] * ax1[None, :], (2 * k) ** 2


def bits_unpack(words, mw, mh):
    """packed rows (bytes, ceil(mw / 128) * 16 per row) -> bool [mh, mw]; bit j of a word = pixel j."""
    stride = (mw + 127) // 128 * 16
    a = np.frombuffer(bytes(words), np.uint8)[:mh * stride].reshape(mh, stride)
    return np.unpackbits(a, axis=1, bitorder='little')[:, :mw].astype(bool)


def bits_pack(m):
    m = np.asarray(m, bool)
    mh, mw = m.shape
    pad = (mw + 127) // 128 * 128
    p = np.zeros((mh, pad), bool)
    p[:, :mw] = m
    return np.packbits(p, axis=1, bitorder='little').tobytes()


def overlay(ow, oh, flags, base=None, q=None, bits=None, lut=None, thr=0, band=0,
            fill=(255, 0, 0), fill_a=16, unsure=(255, 200, 0), unsure_a=16, contour=(0, 255, 0)):
    """whole frame -> uint8 [oh, ow, 3]. base: [oh, ow] grey or [oh, ow, 3]; q: [lh, lw] int8 logits;
    bits: bool [mh, mw]; lut: [256, 3] (index = idx + 128)."""
    kind = flags & 3
    bil = bool(flags & F_BILINEAR)
    V = D = None
    if flags & F_HAS_LOGIT:
        V, D = values(q, ow, oh, bil)
    if kind == BASE_NONE:
        b = np.zeros((oh, ow, 3), np.int64)
    elif kind == BASE_GREY:
        b = np.repeat(np.asarray(base, np.int64)[:, :, None], 3, 2)
    elif kind == BASE_RGB:
        b = np.asarray(base, np.int64).copy()
    else:
        idx = V if D == 1 else (V + D // 2) // D
        b = np.asarray(lut, np.int64)[idx + 128]
    if flags & F_FILL_FROM_BITS:
        m = np.asarray(bits, bool)
        mh, mw = m.shape
        m = m[(np.arange(oh) * mh) // oh][:, (np.arange(ow) * mw) // ow]
    elif V is not None:
        m = V >= thr * D
    else:
        m = np.zeros((oh, ow), bool)
    sel_a = np.zeros((oh, ow), np.int64)
    sel_c = np.zeros((oh, ow, 3), np.int64)
    use = np.zeros((oh, ow), bool)
    if flags & F_FILL:
        sel_a[m], sel_c[m], use[m] = fill_a, fill, True
    if flags & F_UNSURE:
        u = np.abs(V - thr * D) <= band * D
        sel_a[u], sel_c[u], use[u] = unsure_a, unsure, True
    out = np.where(use[:, :, None], (sel_a[:, :, None] * sel_c + (16 - sel_a[:, :, None]) * b + 8) >> 4, b)
    if flags & F_CONTOUR:
        p = np.pad(m, 1, constant_values=False)
        inner = p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
        e = m & ~inner
        out[e] = contour
    return out.astype(np.uint8)


def ovl_rows_needed(ya, yb, oh, n_src, bilinear, k=1):
    """source rows (logits or bits) the band needs (output rows ya - 1 .. yb for the contour): [lo, hi]."""
    r0, r1 = max(ya - 1, 0), min(yb, oh - 1)
    if bilinear:
        i0, i1, _, _ = bilinear_axis(n_src, k)
        return int(i0[r0]), int(i1[r1])
    return (r0 * n_src) // oh, (r1 * n_src) // oh
