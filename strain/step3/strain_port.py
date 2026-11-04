"""Part E step 3: library-free Python reference of the board strain chain (C twin: tcp_image_rx/src/strain.c).

Same chain as strain/step2/track_val.py, final config `final_t512_r6_k9_v_s5`, written so that every step has one
fixed operation order that C reproduces bit for bit (no scipy / skimage; integer steps may use numpy arrays):
  lv, epi: 128 x 128 masks (logit >= 1 of F1 echo_lv / program 18 camus_epi) -> clean (largest 4-component + fill
  holes; = ef_simpson.clean, and cleaning at 128 then x4 = cleaning the x4 mask at 512) -> x4 -> myo = epi & ~lv
  -> largest 8-component (scipy label order: raster order of the first pixel, np.argmax = first largest)
  -> Zhang thinning (skimage 0.25 `_fast_skeletonize`: its LUT, 2 parallel sub-passes, until no change)
  -> longest path: Dijkstra from the first skeleton pixel (raster order), farthest node a (first max), Dijkstra from a,
     farthest b, path b -> a; edge weights sx, sy, sqrt(sx^2 + sy^2) (track_val uses np.hypot: see compare_port.py)
  -> 5-point moving average with edge padding (np.convolve order: sum of x * 0.2, left to right) -> arc length
     (sqrt, sequential cumsum) -> n = max(floor_divide(L, 3) + 1, 2) points at np.linspace positions -> np.interp
  -> Q8 = rint(256 p) (round half even) -> per frame pair bm_track_line(512, R 6, k 9, V fit), anchor = current
  -> per frame: 5-point moving average ('valid') of the tracked points, length = numpy pairwise sum of the sqrt
     segment lengths -> GLS-like(t) = L(t) / L(0) - 1; ES value + TRAIN offset (calib_train.json).
Frames: 512 x 512 u8 (host LANCZOS stretch = the UI's 512 frame, R channel), frame 0 = ED, last = ES.
"""
import ctypes
import heapq
import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
STEP2 = HERE.parent / 'step2'

R, K, STEP_MM, SMOOTH = 6, 9, 3.0, 5
BM_OPT_VFIT = 2
CALIB = STEP2 / 'results' / 'calib_train.json'
OFFSET_PP = -json.loads(CALIB.read_text(encoding='utf-8'))['raw']['bias']     # -3.8059230431912217

# skimage 0.25.2 skimage/morphology/_skeletonize_various_cy.pyx, _fast_skeletonize (BSD); weights NW 1, N 2, NE 4,
# E 8, SE 16, S 32, SW 64, W 128; pass 1 removes 1 and 3, pass 2 removes 2 and 3
_LUT1 = (3, 6, 11, 12, 14, 131, 134, 143, 159, 161, 194, 198, 207, 227, 231, 243)
_LUT2 = (24, 26, 56, 62, 63, 96, 104, 108, 126, 176, 192, 224, 248, 249, 252)
_LUT3 = (7, 10, 15, 28, 30, 31, 40, 60, 112, 120, 124, 130, 135, 160, 193, 195, 199, 225, 240, 241)
LUT = np.zeros(256, np.uint8)
LUT[list(_LUT1)], LUT[list(_LUT2)], LUT[list(_LUT3)] = 1, 2, 3

_lib = None


def lib():
    global _lib
    if _lib is None:
        _lib = ctypes.CDLL(str(STEP2 / 'bm_sad.dll'))
        _lib.bm_track_line.argtypes = [ctypes.c_void_p] * 2 + [ctypes.c_int] * 7 + [ctypes.c_void_p] * 6
        _lib.bm_track_line.restype = ctypes.c_int
    return _lib


# ---------------------------------------------------------------- masks
def _label(mask, conn8):
    """connected components in raster order of their first pixel -> (labels int32, sizes list; label k = index k-1)"""
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    sizes = []
    nb = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)) if conn8 else \
        ((-1, 0), (0, -1), (0, 1), (1, 0))
    m = mask.tolist()
    lb = lab.tolist()
    for y0 in range(h):
        row = m[y0]
        for x0 in range(w):
            if row[x0] and not lb[y0][x0]:
                k = len(sizes) + 1
                lb[y0][x0] = k
                st, n = [(y0, x0)], 0
                while st:
                    y, x = st.pop()
                    n += 1
                    for dy, dx in nb:
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < h and 0 <= xx < w and m[yy][xx] and not lb[yy][xx]:
                            lb[yy][xx] = k
                            st.append((yy, xx))
                sizes.append(n)
    return np.array(lb, np.int32), sizes


def largest(mask, conn8):
    lab, sizes = _label(mask, conn8)
    if not sizes:
        return mask.copy(), 0
    return lab == (int(np.argmax(sizes)) + 1), len(sizes)


def fill_holes(mask):
    """background pixels not 4-connected to the border become foreground (= ndimage.binary_fill_holes)"""
    h, w = mask.shape
    out = np.ones((h, w), bool)
    seen = mask.copy()
    st = [(y, x) for y in range(h) for x in (0, w - 1)] + [(y, x) for x in range(w) for y in (0, h - 1)]
    st = [(y, x) for y, x in st if not seen[y, x]]
    for y, x in st:
        seen[y, x] = True
    while st:
        y, x = st.pop()
        out[y, x] = False
        for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= yy < h and 0 <= xx < w and not seen[yy, xx]:
                seen[yy, xx] = True
                st.append((yy, xx))
    return out


def clean(mask):
    m, n = largest(mask, False)
    return fill_holes(m) if n else m


def up4(m):
    return np.repeat(np.repeat(m, 4, 0), 4, 1)


def skeleton(mask):
    """Zhang thinning exactly as skimage _fast_skeletonize (parallel sub-passes; zero border outside the image)"""
    s = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), np.uint8)
    s[1:-1, 1:-1] = mask
    removed = True
    while removed:
        removed = False
        for first in (True, False):
            c = s[1:-1, 1:-1]
            code = (s[:-2, :-2] + 2 * s[:-2, 1:-1] + 4 * s[:-2, 2:] + 8 * s[1:-1, 2:] + 16 * s[2:, 2:]
                    + 32 * s[2:, 1:-1] + 64 * s[2:, :-2] + 128 * s[1:-1, :-2]).astype(np.int32)
            v = LUT[code]
            rm = (c == 1) & ((v == 3) | (v == (1 if first else 2)))
            if rm.any():
                removed = True
                c[rm] = 0
    return s[1:-1, 1:-1] > 0


# ---------------------------------------------------------------- line
def _dijkstra(nbrs, src, n):
    d = [math.inf] * n
    pred = [-1] * n
    done = [False] * n
    d[src] = 0.0
    hq = [(0.0, src)]
    while hq:
        du, u = heapq.heappop(hq)
        if done[u]:
            continue
        done[u] = True
        for v, wt in nbrs[u]:
            if not done[v]:
                nd = du + wt
                if nd < d[v]:
                    d[v] = nd
                    pred[v] = u
                    heapq.heappush(hq, (nd, v))
    return d, pred


def _argmax_first(d):
    best, bi = -math.inf, 0
    for i, x in enumerate(d):
        x = -1.0 if x == math.inf else x
        if x > best:
            best, bi = x, i
    return bi


def longest_path(skel, sx, sy):
    """ordered (y, x) int pixels of the longest path (b -> a), None if < 3 pixels"""
    ys, xs = np.nonzero(skel)
    n = len(ys)
    if n < 3:
        return None
    idx = -np.ones(skel.shape, np.int64)
    idx[ys, xs] = np.arange(n)
    H, W = skel.shape
    diag = math.sqrt(sx * sx + sy * sy)
    nbrs = [[] for _ in range(n)]
    for i in range(n):
        y, x = int(ys[i]), int(xs[i])
        for dy, dx in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
            yy, xx = y + dy, x + dx
            if 0 <= yy < H and 0 <= xx < W and idx[yy, xx] >= 0:
                nbrs[i].append((int(idx[yy, xx]), diag if dy and dx else (sy if dy else sx)))
    d0, _ = _dijkstra(nbrs, 0, n)
    a = _argmax_first(d0)
    da, pred = _dijkstra(nbrs, a, n)
    b = _argmax_first(da)
    path = [b]
    while path[-1] != a:
        path.append(pred[path[-1]])
    return np.stack([ys[path], xs[path]], 1).astype(np.int64)


def _movavg(v, pad):
    """5-point moving average as np.convolve(v, ones(5) / 5, 'valid') (after edge padding when pad)"""
    v = [float(x) for x in v]
    if pad:
        v = [v[0]] * (SMOOTH // 2) + v + [v[-1]] * (SMOOTH // 2)
    out = []
    for i in range(len(v) - SMOOTH + 1):
        s = 0.0
        for j in range(SMOOTH):
            s = s + v[i + j] * 0.2
        out.append(s)
    return out


def _seglen(ys, xs, sx, sy):
    out = []
    for i in range(len(ys) - 1):
        dy, dx = (ys[i + 1] - ys[i]) * sy, (xs[i + 1] - xs[i]) * sx
        out.append(math.sqrt(dy * dy + dx * dx))
    return out


def _floordiv(a, b):
    """np.floor_divide for doubles (npy_divmod)"""
    mod = math.fmod(a, b)
    div = (a - mod) / b
    if mod and (b < 0) != (mod < 0):
        div -= 1.0
    if div:
        fl = math.floor(div)
        if div - fl > 0.5:
            fl += 1.0
        return fl
    return math.copysign(0.0, a / b)


def _interp(x, xp, fp):
    """np.interp for one x (xp non-decreasing): j = last index with xp[j] <= x"""
    if x > xp[-1]:
        return fp[-1]
    if x < xp[0]:
        return fp[0]
    lo, hi = 0, len(xp)
    while lo < hi:
        mid = (lo + hi) >> 1
        if x >= xp[mid]:
            lo = mid + 1
        else:
            hi = mid
    j = lo - 1
    if j == len(xp) - 1 or xp[j] == x:
        return fp[j]
    slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j])
    return slope * (x - xp[j]) + fp[j]


def resample(path, sx, sy):
    """track_val.resample(path, sx, sy, 3.0, smooth=5) -> (n, 2) (y, x) doubles"""
    ys, xs = [float(v) for v in path[:, 0]], [float(v) for v in path[:, 1]]
    if len(ys) > SMOOTH:
        ys, xs = _movavg(ys, True), _movavg(xs, True)
    s = [0.0]
    for g in _seglen(ys, xs, sx, sy):
        s.append(s[-1] + g)
    n = max(int(_floordiv(s[-1], STEP_MM)) + 1, 2)
    step = s[-1] / (n - 1)
    t = [i * step + 0.0 for i in range(n)]
    t[-1] = s[-1]
    return np.array([[_interp(v, s, ys), _interp(v, s, xs)] for v in t])


def np_sum(a):
    """numpy pairwise summation of a float64 array (add.reduce), bit for bit"""
    def pw(lo, n):
        if n < 8:
            r = -0.0
            for i in range(n):
                r = r + a[lo + i]
            return r
        if n <= 128:
            r = [a[lo + i] for i in range(8)]
            i = 8
            while i < n - (n % 8):
                for j in range(8):
                    r[j] = r[j] + a[lo + i + j]
                i += 8
            res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
            while i < n:
                res = res + a[lo + i]
                i += 1
            return res
        n2 = n // 2
        n2 -= n2 % 8
        return pw(lo, n2) + pw(lo + n2, n - n2)
    return pw(0, len(a))


def line_length(q8, sx, sy):
    """tracked Q8 positions (n, 2) (y, x) -> mm: moving average ('valid'), pairwise sum of segment lengths"""
    ys, xs = [v / 256.0 for v in q8[:, 0].tolist()], [v / 256.0 for v in q8[:, 1].tolist()]
    ys, xs = _movavg(ys, False), _movavg(xs, False)         # n >= SMOOTH (ed_line)
    return np_sum(_seglen(ys, xs, sx, sy))


# ---------------------------------------------------------------- chain
def ed_line(lv128, epi128, sx, sy, do_clean=True):
    """-> dict(path, pts0, q8_0) or raises ValueError"""
    lv, epi = (clean(lv128), clean(epi128)) if do_clean else (lv128, epi128)
    myo = up4(epi) & ~up4(lv)
    if not myo.any():
        raise ValueError('no myocardium at ED')
    myo, _ = largest(myo, True)
    path = longest_path(skeleton(myo), sx, sy)
    if path is None:
        raise ValueError('mid-wall line too short')
    pts0 = resample(path, sx, sy)
    if len(pts0) < SMOOTH:                  # track_val's np.convolve 'valid' would swap its operands
        raise ValueError('mid-wall line too short')
    q8 = np.stack([np.rint(pts0[:, 0] * 256), np.rint(pts0[:, 1] * 256)], 1).astype(np.int32)
    return dict(lv=lv, epi=epi, myo=myo, path=path, pts0=pts0, q8=q8)


def track(frames, q8_0, sx, sy):
    """frames (T, 512, 512) u8 -> (q8 (T, n, 2) int32 (y, x), L (T,), g (T,), edges)"""
    T, h, w = frames.shape
    n = len(q8_0)
    xq = np.ascontiguousarray(q8_0[:, 1], np.int32)
    yq = np.ascontiguousarray(q8_0[:, 0], np.int32)
    out = [np.stack([yq, xq], 1)]
    edges = 0
    sad = np.zeros(n, np.uint32)
    fl = np.zeros(n, np.int32)
    for t in range(T - 1):
        A = np.ascontiguousarray(frames[t], np.uint8)
        B = np.ascontiguousarray(frames[t + 1], np.uint8)
        ax, ay = xq.copy(), yq.copy()
        rc = lib().bm_track_line(A.ctypes.data, B.ctypes.data, w, h, w, n, R, K, BM_OPT_VFIT, ax.ctypes.data,
                                 ay.ctypes.data, xq.ctypes.data, yq.ctypes.data, sad.ctypes.data, fl.ctypes.data)
        assert rc >= 0
        edges += rc
        out.append(np.stack([yq, xq], 1))
    q = np.stack(out)
    L = [line_length(q[t], sx, sy) for t in range(T)]
    g = [x / L[0] - 1.0 for x in L]
    return q, L, g, edges


def run(frames, lv128, epi128, sx, sy, do_clean=True):
    e = ed_line(lv128, epi128, sx, sy, do_clean)
    q, L, g, edges = track(frames, e['q8'], sx, sy)
    es = g[-1] * 100.0
    e.update(q8_t=q, L=L, g=g, edges=edges, es_raw_pp=es, es_cal_pp=es + OFFSET_PP, peak_pp=min(g) * 100.0)
    return e
