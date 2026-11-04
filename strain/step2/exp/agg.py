"""Experiment (VAL): spatial regularisation in the cost domain - sum the SAD tables of the k neighbours of each point
along the line (same displacement grid) before the argmin, vs median of displacements (track_val.py).
Same chain and references as track_val.py; reads its per-sequence references from results/track_val.json."""
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


def track_agg(frames, pts0, R, k, kind, med):
    n, nc = len(pts0), 2 * R + 1
    pos = pts0.copy()
    out = [pos.copy()]
    H, W = frames[0].shape
    for t in range(len(frames) - 1):
        A = np.ascontiguousarray(frames[t])
        B = np.ascontiguousarray(frames[t + 1])
        tabs = np.zeros((n, nc, nc))
        ok = np.zeros(n, bool)
        for i in range(n):
            ty, tx = int(np.floor(pos[i, 0] + 0.5)) - 8, int(np.floor(pos[i, 1] + 0.5)) - 8
            if tx - R < 0 or ty - R < 0 or tx + 16 + R > W or ty + 16 + R > H:
                continue
            tab = np.zeros(nc * nc, np.uint32)
            lib.bm_sad_table_c(A[ty:, tx:].ctypes.data, W, B[ty:, tx:].ctypes.data, W, R, tab.ctypes.data)
            tabs[i] = tab.reshape(nc, nc)
            ok[i] = True
        disp = np.zeros((n, 2))
        for i in range(n):
            if not ok[i]:
                continue
            lo, hi = max(0, i - k // 2), min(n, i + k // 2 + 1)
            s = tabs[lo:hi][ok[lo:hi]].sum(0)
            iy, ix = np.unravel_index(np.argmin(s), s.shape)
            fy = fit(s[iy - 1, ix], s[iy, ix], s[iy + 1, ix], kind) if 0 < iy < nc - 1 else 0
            fx = fit(s[iy, ix - 1], s[iy, ix], s[iy, ix + 1], kind) if 0 < ix < nc - 1 else 0
            disp[i] = (iy - R + fy, ix - R + fx)
        if med > 1:
            pad = med // 2
            win = np.lib.stride_tricks.sliding_window_view
            disp = np.stack([np.median(win(np.pad(disp[:, j], pad, mode='edge'), med), 1) for j in range(2)], 1)
        pos = pos + disp
        out.append(pos.copy())
    return np.stack(out)


def main():
    prev = json.loads((HERE / 'results' / 'track_val.json').read_text(encoding='utf-8'))
    meta = json.loads((tv.CACHE / 'val_meta.json').read_text(encoding='utf-8'))
    mi = {(m['patient'], m['view'], m['phase']): i for i, m in enumerate(meta)}
    qual = {(m['patient'], m['view']): m['quality'] for m in meta}
    variants = [(256, 4, 3, 'v', 1), (256, 4, 5, 'v', 1), (256, 4, 3, 'v', 5), (256, 4, 5, 'p', 1),
                (512, 8, 5, 'v', 1), (512, 8, 5, 'v', 5), (512, 8, 9, 'v', 1)]
    res = {v: [] for v in variants}
    for r in prev['per']:
        p, view = r['patient'], r['view']
        for tier in (256, 512):
            frames, sx, sy, _ = tv.load_sequence(p, view, tier)
            lv = tv.model_mask('lv_r128', mi[(p, view, 'ED')], tier)
            epi = tv.model_mask('epi_r128', mi[(p, view, 'ED')], tier)
            pts0 = tv.resample(tv.midline(epi & ~lv, sx, sy), sx, sy, tv.STEP_MM)
            c = dict(trim=0, smooth=5)
            for v in variants:
                if v[0] != tier:
                    continue
                trk = track_agg(frames, pts0, v[1], v[2], v[3], v[4])
                g = tv.line_length(trk[-1], sx, sy, c) / tv.line_length(trk[0], sx, sy, c) - 1
                res[v].append((g * 100, r[f'ref_mid_{tier}'] * 100, qual[(p, view)]))
    out = {}
    for v, rows in res.items():
        e = np.array([x[0] for x in rows])
        f = np.array([x[1] for x in rows])
        gm = np.array([x[2] != 'Poor' for x in rows])
        name = f't{v[0]}_r{v[1]}_agg{v[2]}_{v[3]}_med{v[4]}_s5'
        out[name] = dict(all=tv.stats(e, f), good_medium=tv.stats(e[gm], f[gm]))
        a, b = out[name]['all'], out[name]['good_medium']
        print(f"{name:28s} all: bias {a['bias']:5.2f} MAE {a['mae']:5.2f} r {a['r']:.2f} | "
              f"good+medium: bias {b['bias']:5.2f} MAE {b['mae']:5.2f} r {b['r']:.2f}", flush=True)
    (HERE / 'results' / 'exp_agg.json').write_text(json.dumps(out, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
