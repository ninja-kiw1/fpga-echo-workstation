"""Experiment (VAL): anchored tracking. The template stays in an anchor frame (re-anchored every K frames; K = 0: ED
for the whole half cycle) and the search window in frame t is centred on the predicted position (previous estimate),
so per-step sub-pixel bias / noise does not accumulate over ~18 steps. Combined with SAD-table aggregation over k line
neighbours. Kernel unchanged (bm_sad_table_c takes separate template / search pointers). Same chain and references as
track_val.py (reads results/track_val.json for the references)."""
import ctypes
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import track_val as tv  # noqa: E402

lib = tv.lib
lib.bm_sad_table_c.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]


def fit(l, c, r, kind):
    d = (max(l, r) - c) if kind == 'v' else (l + r - 2 * c)
    return 0.0 if d <= 0 else (l - r) / (2 * d)


def track_anchor(frames, pts0, R, k, K, kind='v'):
    n, nc = len(pts0), 2 * R + 1
    H, W = frames[0].shape
    pos = pts0.copy()
    anc_f, anc_p = 0, pts0.copy()
    out = [pos.copy()]
    for t in range(1, len(frames)):
        A = np.ascontiguousarray(frames[anc_f])
        B = np.ascontiguousarray(frames[t])
        tabs = np.zeros((n, nc, nc))
        ok = np.zeros(n, bool)
        off = np.zeros((n, 2), int)
        for i in range(n):
            ty, tx = int(np.floor(anc_p[i, 0] + 0.5)) - 8, int(np.floor(anc_p[i, 1] + 0.5)) - 8
            oy, ox = np.round(pos[i] - anc_p[i]).astype(int)        # search centre = prediction
            by, bx = ty + oy, tx + ox
            if (min(tx, ty) < 0 or tx + 16 > W or ty + 16 > H or bx - R < 0 or by - R < 0
                    or bx + 16 + R > W or by + 16 + R > H):
                continue
            tab = np.zeros(nc * nc, np.uint32)
            lib.bm_sad_table_c(A[ty:, tx:].ctypes.data, W, B[by:, bx:].ctypes.data, W, R, tab.ctypes.data)
            tabs[i] = tab.reshape(nc, nc)
            off[i] = (oy, ox)
            ok[i] = True
        new = pos.copy()
        for i in range(n):
            if not ok[i]:
                continue
            # aggregate only neighbours with the same search centre offset (same displacement grid)
            lo, hi = max(0, i - k // 2), min(n, i + k // 2 + 1)
            sel = [j for j in range(lo, hi) if ok[j] and (off[j] == off[i]).all()]
            s = tabs[sel].sum(0)
            iy, ix = np.unravel_index(np.argmin(s), s.shape)
            fy = fit(s[iy - 1, ix], s[iy, ix], s[iy + 1, ix], kind) if 0 < iy < nc - 1 else 0
            fx = fit(s[iy, ix - 1], s[iy, ix], s[iy, ix + 1], kind) if 0 < ix < nc - 1 else 0
            new[i] = anc_p[i] + off[i] + (iy - R + fy, ix - R + fx)
        pos = new
        out.append(pos.copy())
        if K and t % K == 0:
            anc_f, anc_p = t, pos.copy()
    return np.stack(out)


VARIANTS = [  # tier, R, k (aggregation), K (re-anchor period, 0 = ED only)
    (256, 4, 5, 1), (256, 4, 5, 4), (256, 4, 5, 0), (256, 4, 9, 0), (256, 4, 1, 0),
    (512, 6, 9, 1), (512, 6, 9, 4), (512, 6, 9, 0), (512, 6, 5, 0), (512, 6, 15, 0),
]


def main():
    prev = json.loads((HERE / 'results' / 'track_val.json').read_text(encoding='utf-8'))
    meta = json.loads((tv.CACHE / 'val_meta.json').read_text(encoding='utf-8'))
    mi = {(m['patient'], m['view'], m['phase']): i for i, m in enumerate(meta)}
    qual = {(m['patient'], m['view']): m['quality'] for m in meta}
    res = {v: [] for v in VARIANTS}
    for r in prev['per']:
        p, view = r['patient'], r['view']
        for tier in (256, 512):
            frames, sx, sy, _ = tv.load_sequence(p, view, tier)
            lv = tv.model_mask('lv_r128', mi[(p, view, 'ED')], tier)
            epi = tv.model_mask('epi_r128', mi[(p, view, 'ED')], tier)
            pts0 = tv.resample(tv.midline(epi & ~lv, sx, sy), sx, sy, tv.STEP_MM)
            c = dict(trim=0, smooth=5)
            for v in VARIANTS:
                if v[0] != tier:
                    continue
                trk = track_anchor(frames, pts0, v[1], v[2], v[3])
                L = np.array([tv.line_length(q, sx, sy, c) for q in trk])
                res[v].append((float(L[-1] / L[0] - 1) * 100, r[f'ref_mid_{tier}'] * 100, qual[(p, view)], p,
                               (L / L[0] - 1).tolist()))
    out = {}
    for v, rows in res.items():
        e = np.array([x[0] for x in rows])
        f = np.array([x[1] for x in rows])
        gm = np.array([x[2] != 'Poor' for x in rows])
        name = f't{v[0]}_r{v[1]}_agg{v[2]}_anchor{v[3] or "ED"}_v_s5'
        out[name] = dict(all=tv.stats(e, f), good_medium=tv.stats(e[gm], f[gm]),
                         per=[dict(es=x[0], ref=x[1], quality=x[2], patient=x[3], curve=x[4]) for x in rows])
        a, b = out[name]['all'], out[name]['good_medium']
        print(f"{name:30s} all: bias {a['bias']:5.2f} MAE {a['mae']:5.2f} r {a['r']:.2f} | "
              f"good+medium: bias {b['bias']:5.2f} MAE {b['mae']:5.2f} r {b['r']:.2f}", flush=True)
    (HERE / 'results' / 'exp_anchor.json').write_text(json.dumps(out, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
