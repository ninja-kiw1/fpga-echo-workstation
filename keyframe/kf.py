"""Innovation ① simulator: phase-adaptive keyframe inference = PL U-Net on keyframes, PS block-matching between.

Between keyframes the LV contour is carried by the PS SAD block matcher of part E (strain/step2/bm_sad.dll = bm_sad.c,
the same C that is bit-exact with the board's NEON kernel): one 16 x 16 block per contour point, full search +-R,
equiangular sub-pixel fit, SAD tables of k contour neighbours summed before the argmin (bm_track_line), then a
circular moving average of the displacements over `reg` neighbours; the tracked polygon is filled back to a mask.

propagate(frames, key_mask, policy, cfg) runs a causal schedule over a whole sequence:
  frames    [T, H, W] uint8 (the tracking tier)
  key_mask  callable t -> bool mask [H, W] (what the PL would return on frame t: model output or expert label)
  policy    dict(kind=...)
     full       every frame is a keyframe (= today's per-frame inference)
     hold       keyframes every n, masks between are copies of the last keyframe (lower bound, no tracking)
     fixed      keyframe every n frames, tracking between
     adaptive   tracking, keyframe when (a) n_max frames since the last one, (b) the mean block residual
                1 - alpha (alpha = 1 - SAD / (255 * 256)) rises above `res` x its value on the first tracked frame
                after the keyframe, or (c) the frame falls within +-`win` of a predicted ED / ES (phase forcing:
                extremes of the causal area curve, cycle = last extreme spacing); n_min frames at least
  cfg       tracking parameters: mode 'chain' (template = previous frame) or 'key' (template = keyframe, search centred
            on the previous estimate), R, k, reg, spacing (contour point spacing, px), pad (zero border so blocks near
            the image edge can still be searched)
Returns masks [T, H, W] bool, is_key [T] bool, info dict (per-frame mean residual, keyframe reasons).
"""
import ctypes
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.signal import find_peaks
from skimage.draw import polygon as fill_polygon
from skimage.measure import find_contours

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
lib = ctypes.CDLL(str(PLD / 'strain' / 'step2' / 'bm_sad.dll'))
lib.bm_track_line.argtypes = [ctypes.c_void_p] * 2 + [ctypes.c_int] * 7 + [ctypes.c_void_p] * 6
lib.bm_track_line.restype = ctypes.c_int
BM_OPT_VFIT = 2
BM_EDGE = 4

DEFAULT = dict(mode='chain', R=4, k=5, reg=5, spacing=3.0, pad=24)


def largest(mask):
    lab, n = ndimage.label(mask)
    if n > 1:
        mask = lab == (int(np.argmax(ndimage.sum(mask, lab, range(1, n + 1)))) + 1)
    return ndimage.binary_fill_holes(mask)


def contour(mask, spacing):
    """outer boundary of the largest component -> (n, 2) (y, x) points every `spacing` px along the closed curve,
    starting at the top-most point (apex side), fixed orientation. None for an empty mask."""
    if not mask.any():
        return None
    c = find_contours(np.pad(largest(mask), 1).astype(float), 0.5)
    c = max(c, key=len) - 1.0
    if np.allclose(c[0], c[-1]):
        c = c[:-1]
    seg = np.hypot(*np.diff(np.vstack([c, c[:1]]), axis=0).T)
    s = np.r_[0, np.cumsum(seg)]
    n = max(8, int(round(s[-1] / spacing)))
    t = np.linspace(0, s[-1], n, endpoint=False)
    cc = np.vstack([c, c[:1]])
    p = np.stack([np.interp(t, s, cc[:, 0]), np.interp(t, s, cc[:, 1])], 1)
    return np.roll(p, -int(np.argmin(p[:, 0])), 0)


def fill(pts, shape):
    m = np.zeros(shape, bool)
    if pts is None or len(pts) < 3:
        return m
    r, c = fill_polygon(pts[:, 0], pts[:, 1], shape)    # contour runs on pixel edges (level 0.5), centres inside
    m[r, c] = True
    return m


def track(A, B, anchor, pred, cfg):
    """one step for a closed contour: template around `anchor` in A, search in B around `pred`. Circular SAD
    aggregation (k) by wrap-padding the point list; then a circular moving average (reg) of the displacement
    pred -> new. Returns new points (n, 2) and mean residual 1 - alpha over the points."""
    n, k, h = len(anchor), cfg['k'], cfg['k'] // 2
    idx = np.r_[np.arange(n - h, n), np.arange(n), np.arange(h)] % n if h else np.arange(n)
    q = lambda v: np.ascontiguousarray(np.round(v * 256).astype(np.int32))  # noqa: E731
    ax, ay = q(anchor[idx, 1]), q(anchor[idx, 0])
    x, y = q(pred[idx, 1]), q(pred[idx, 0])
    m = len(idx)
    sad = np.zeros(m, np.uint32)
    fl = np.zeros(m, np.int32)
    H, W = A.shape
    rc = lib.bm_track_line(A.ctypes.data, B.ctypes.data, W, H, W, m, cfg['R'], k, BM_OPT_VFIT,
                           ax.ctypes.data, ay.ctypes.data, x.ctypes.data, y.ctypes.data, sad.ctypes.data, fl.ctypes.data)
    assert rc >= 0
    sl = slice(h, h + n)
    new = np.stack([y[sl], x[sl]], 1) / 256.0
    d = new - pred
    r = cfg['reg']
    if r > 1:
        ker = np.ones(r) / r
        d = np.stack([np.convolve(np.r_[d[-(r // 2):, j], d[:, j], d[:r // 2, j]], ker, 'valid') for j in (0, 1)], 1)
    edge = (fl[sl] & BM_EDGE) != 0
    res = sad[sl][~edge].astype(float) / (255.0 * 256 * (k if k > 1 else 1))
    return pred + d, float(res.mean()) if len(res) else 1.0


class Phase:
    """causal ED / ES prediction from the area curve: extremes confirmed `lag` frames later; next extreme of each kind
    predicted one cycle (= spacing of the last two of that kind) after the last one."""

    def __init__(self, fps, dist_s=0.3, prom=0.2):
        self.dist = max(2, int(round(dist_s * fps)))
        self.prom = prom

    def predicted(self, area):
        a = np.asarray(area, float)
        if len(a) < 2 * self.dist:
            return []
        rng = np.percentile(a, 95) - np.percentile(a, 5)
        out = []
        for sgn in (1, -1):   # ED = maxima, ES = minima
            p, _ = find_peaks(sgn * a, distance=self.dist, prominence=max(self.prom * rng, 1e-9))
            p = p[p < len(a) - 2]   # needs two later frames to be a confirmed extreme
            if len(p) >= 2:
                out.append(int(p[-1] + (p[-1] - p[-2])))
        return out


def propagate(frames, key_mask, policy, cfg=None, fps=50.0):
    cfg = dict(DEFAULT, **(cfg or {}))
    T, H, W = frames.shape
    P = cfg['pad']
    F = np.pad(frames, ((0, 0), (P, P), (P, P)))
    kind = policy['kind']
    masks = np.zeros((T, H, W), bool)
    is_key = np.zeros(T, bool)
    resid = np.full(T, np.nan)
    reason = [''] * T
    area = []
    phase = Phase(fps) if kind == 'adaptive' else None
    pts = kpts = None
    kt = -10 ** 9
    base = None
    for t in range(T):
        since = t - kt
        if kind == 'full' or t == 0 or pts is None:
            key = 'start' if t == 0 else ('lost' if pts is None else 'full')
        elif kind in ('fixed', 'hold'):
            key = 'interval' if since >= policy['n'] else ''
        else:
            key = ''
            if since >= policy['n_max']:
                key = 'interval'
            elif since >= policy.get('n_min', 1):
                if base is not None and resid[t - 1] > policy['res'] * base:
                    key = 'residual'
                elif any(abs(t - p) <= policy['win'] for p in phase.predicted(area)):
                    key = 'phase'
        if key:
            m = key_mask(t)
            m = largest(m) if m.any() else m
            masks[t], is_key[t], reason[t], kt, base = m, True, key, t, None
            pts = contour(m, cfg['spacing'])
            kpts = None if pts is None else pts + P
            pts = kpts
        elif kind == 'hold':
            masks[t] = masks[t - 1]
        else:
            anchor_frame = F[kt] if cfg['mode'] == 'key' else F[t - 1]
            anchor = kpts if cfg['mode'] == 'key' else pts
            pts, resid[t] = track(anchor_frame, F[t], anchor, pts, cfg)
            if base is None:
                base = max(resid[t], 1e-6)
            masks[t] = fill(pts - P, (H, W))
        area.append(float(masks[t].sum()))
    return masks, is_key, dict(resid=resid, reason=reason)


def dice(a, b):
    s = int(a.sum()) + int(b.sum())
    return 1.0 if s == 0 else 2.0 * int((a & b).sum()) / s
