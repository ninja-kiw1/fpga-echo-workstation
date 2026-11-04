"""Part D reference pipeline (numpy only, integer arithmetic): what the PS does around the two PL programs.

  stage 1   payload1 = box4(img512)                      128 x 128 x 3 -> program fundus_disc -> q1 (int8 logits)
  locate    disc1 = largest 8-connected component of (q1 >= thr)          -> centre (512 px) + size
  window    S x S window of img512, S = side_for(size) = 128 k (smallest k in 1..4 with S >= RATIO * size),
            centred on the disc, clamped inside the image
  stage 2   payload2 = box_k(window)                                       -> fundus_disc -> qd, fundus_cup -> qc
  masks     bilinear x k of the logits into the window (integer weights), keep v >= thr - 1/2
            disc = fill_holes(largest_cc(disc_raw)); cup = fill_holes(largest_cc(cup_raw & disc))
  vCDR      cup rows spanned / disc rows spanned (window pixels = 512-grid pixels)

The host stretches every photograph to 512 x 512 (Pillow LANCZOS, as for the vessel model), so the
horizontal and vertical scales differ; a VERTICAL ratio is unaffected by that.
src/fundus_cdr.c implements the same functions; test/fundus_cdr/compare.py checks them bit for bit.
No scipy: every rule (connectivity, tie-break, hole filling) is spelled out here and in the C code.
"""
from collections import deque

import numpy as np

RATIO_NUM, RATIO_DEN = 2, 1     # window side >= RATIO * stage-1 disc size (integer ratio, C uses the same)
PAY = 128           # payload side of every program (512 >> 2)
MIN_DISC_PX = 4     # stage-1 component smaller than this (128-grid pixels) = "disc not found"


def box(u8, k):
    """[H, W(, C)] uint8 -> k x k mean, round half up (same as ct/common.box and job_ct.c box())."""
    a = np.asarray(u8)
    if k == 1:
        return a.astype(np.uint8)
    h, w = a.shape[:2]
    s = a.reshape(h // k, k, w // k, k, *a.shape[2:]).astype(np.uint32).sum(axis=(1, 3))
    return ((s + k * k // 2) // (k * k)).astype(np.uint8)


def largest_cc(mask):
    """Largest 8-connected component. Components are numbered in raster order of their first pixel;
    on equal size the lower number wins. Empty input -> empty output."""
    m = np.asarray(mask, bool)
    h, w = m.shape
    lab = np.zeros((h, w), np.int32)
    best, best_n, n = 0, 0, 0
    for y, x in zip(*np.nonzero(m)):
        if lab[y, x]:
            continue
        n += 1
        lab[y, x] = n
        q, size = deque([(y, x)]), 0
        while q:
            cy, cx = q.popleft()
            size += 1
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = cy + dy, cx + dx
                    if 0 <= yy < h and 0 <= xx < w and m[yy, xx] and not lab[yy, xx]:
                        lab[yy, xx] = n
                        q.append((yy, xx))
        if size > best_n:
            best, best_n = n, size
    return lab == best if best else np.zeros_like(m)


def fill_holes(mask):
    """Background not 4-connected to the image border through background becomes foreground."""
    m = np.asarray(mask, bool)
    h, w = m.shape
    outside = np.zeros((h, w), bool)
    q = deque()
    for y in range(h):
        for x in (0, w - 1):
            if not m[y, x] and not outside[y, x]:
                outside[y, x] = True
                q.append((y, x))
    for x in range(w):
        for y in (0, h - 1):
            if not m[y, x] and not outside[y, x]:
                outside[y, x] = True
                q.append((y, x))
    while q:
        cy, cx = q.popleft()
        for yy, xx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
            if 0 <= yy < h and 0 <= xx < w and not m[yy, xx] and not outside[yy, xx]:
                outside[yy, xx] = True
                q.append((yy, xx))
    return ~outside


def up_index(n, k):
    """Bilinear x k with half-pixel centres and edge clamping (= torch align_corners=False), integer form:
    output o takes (wa * q[a] + wb * q[b]) / (2k); returns a, b, wa, wb (wa + wb = 2k)."""
    o = np.arange(n * k)
    t = 2 * o + 1 - k                       # 2k * source coordinate
    t = np.maximum(t, 0)
    a = t // (2 * k)
    wb = t - 2 * k * a
    b = np.minimum(a + 1, n - 1)
    return a, b, 2 * k - wb, wb


def up_mask(q, k, thr):
    """int8 logits [n, n] -> bool [n*k, n*k]: bilinear value >= thr - 1/2 (k = 1: q >= thr)."""
    q = np.asarray(q, np.int64)
    if k == 1:
        return q >= thr
    a, b, wa, wb = up_index(q.shape[0], k)
    rows = q[a, :] * wa[:, None] + q[b, :] * wb[:, None]           # x 2k
    v = rows[:, a] * wa[None, :] + rows[:, b] * wb[None, :]         # x 4k^2
    d = 4 * k * k
    return 2 * v >= (2 * thr - 1) * d


def locate(q1, thr=1):
    """Stage-1 logits [128, 128] -> dict(cy, cx, size, area) on the 512 grid, or None."""
    m = largest_cc(np.asarray(q1) >= thr)
    area = int(m.sum())
    if area < MIN_DISC_PX:
        return None
    r, c = np.nonzero(m)
    r0, r1, c0, c1 = int(r.min()), int(r.max()), int(c.min()), int(c.max())
    return dict(cy=2 * (r0 + r1 + 1), cx=2 * (c0 + c1 + 1), size=4 * max(r1 - r0 + 1, c1 - c0 + 1), area=area,
                bbox128=[r0, r1, c0, c1])


def side_for(size):
    """Stage-2 window side for a disc of `size` 512-grid pixels: 128 k, smallest k in 1..4 covering RATIO * size."""
    for k in (1, 2, 3):
        if 128 * k * RATIO_DEN >= RATIO_NUM * size:
            return 128 * k
    return 512


def window(cy, cx, side):
    """Top-left of the side x side window centred on (cy, cx), clamped inside 512 x 512."""
    y0 = min(max(cy - side // 2, 0), 512 - side)
    x0 = min(max(cx - side // 2, 0), 512 - side)
    return y0, x0


def payload1(img512):
    return box(img512, 4)


def payload2(img512, y0, x0, side):
    return np.ascontiguousarray(box(np.asarray(img512)[y0:y0 + side, x0:x0 + side], side // PAY))


def rows_spanned(m):
    r = np.nonzero(np.asarray(m).any(1))[0]
    return 0 if len(r) == 0 else int(r.max() - r.min() + 1), (int(r.min()) if len(r) else -1)


def measure(qd, qc, side, thr_d=1, thr_c=1):
    """Stage-2 logits -> masks in the window + vertical diameters + vCDR."""
    k = side // PAY
    disc = fill_holes(largest_cc(up_mask(qd, k, thr_d)))
    cup = fill_holes(largest_cc(up_mask(qc, k, thr_c) & disc))
    dh, dtop = rows_spanned(disc)
    ch, ctop = rows_spanned(cup)
    return dict(disc=disc, cup=cup, disc_h=dh, cup_h=ch, disc_top=dtop, cup_top=ctop,
                disc_area=int(disc.sum()), cup_area=int(cup.sum()),
                vcdr=(ch / dh) if dh else None)


def run_case(img512, prog, thr=1):
    """Whole pipeline. prog(name, payload uint8 [128,128,3]) -> int8 logits [128,128]; name in {'disc','cup'}."""
    q1 = prog('disc', payload1(img512))
    loc = locate(q1, thr)
    if loc is None:
        return dict(ok=False, error='disc not found', q1=q1)
    side = side_for(loc['size'])
    y0, x0 = window(loc['cy'], loc['cx'], side)
    p2 = payload2(img512, y0, x0, side)
    qd, qc = prog('disc', p2), prog('cup', p2)
    res = measure(qd, qc, side, thr, thr)
    res.update(ok=res['disc_h'] > 0, loc=loc, y0=y0, x0=x0, side=side, q1=q1, qd=qd, qc=qc)
    if not res['ok']:
        res['error'] = 'disc not found in window'
    return res
