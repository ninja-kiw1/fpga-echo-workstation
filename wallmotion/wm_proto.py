"""Regional wall motion from the board's own LV masks (zero extra model), DEV only.

Geometry from the ED mask of each beat: long axis = principal axis of the ED pixels (apex = upper end), u in [0, 1] from
the base to the apex along it, side = left / right of the axis through the ED centroid (left = septum: LV is on the
right of an A4C screen, RV on the left). Six regions = thirds of u x side, in HMC-QU order:
SEG3 basal septal, SEG9 mid septal, SEG14 apical septal, SEG16 apical lateral, SEG12 mid lateral, SEG6 basal lateral.
ES pixels are classified in the same ED frame (u clipped to [0, 1]).
  fac_k  = 1 - A_es_k / A_ed_k                      (regional fractional area change)
  exc_k  = (A_ed_k - A_es_k) / (L_ed * len_k)        (mean inward excursion of the region's ED border, / LV length)
averaged over the beats of the clip. Rules compared by 5-fold CV on DEV (recordings), segment-level F1 decides.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe wm_proto.py
"""
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
C = os.path.join(HERE, 'results', 'cache')
SEGS = ['SEG3', 'SEG9', 'SEG14', 'SEG16', 'SEG12', 'SEG6']
REG = [(0, 0), (0, 1), (0, 2), (1, 2), (1, 1), (1, 0)]       # (side 0 septal / 1 lateral, third 0 basal .. 2 apical)


def frame_of(ed):
    ys, xs = np.nonzero(ed)
    c = np.array([xs.mean(), ys.mean()])
    p = np.stack([xs, ys], 1) - c
    w, v = np.linalg.eigh(p.T @ p)
    a = v[:, 1]
    if a[1] > 0:                      # point the axis to the apex (up = smaller y)
        a = -a
    n = np.array([-a[1], a[0]])       # perpendicular; make it point to image right
    if n[0] < 0:
        n = -n
    s = p @ a
    return c, a, n, s.min(), s.max() - s.min()


def regions(mask, fr):
    c, a, n, s0, L = fr
    ys, xs = np.nonzero(mask)
    p = np.stack([xs, ys], 1) - c
    u = np.clip((p @ a - s0) / L, 0, 1 - 1e-9)
    side = (p @ n > 0).astype(int)
    third = (u * 3).astype(int)
    return np.bincount(side * 3 + third, minlength=6)


def border_len(ed, fr):
    """ED border pixels (4-neighbour edge) per region."""
    e = ed & ~(np.roll(ed, 1, 0) & np.roll(ed, -1, 0) & np.roll(ed, 1, 1) & np.roll(ed, -1, 1))
    return regions(e, fr)


def clip_features(masks, beats):
    fac, exc = [], []
    for ed_i, es_i in beats:
        ed, es = masks[ed_i], masks[es_i]
        if ed.sum() < 50:
            continue
        fr = frame_of(ed)
        a_ed, a_es, bl = regions(ed, fr), regions(es, fr), border_len(ed, fr)
        f = 1 - a_es / np.maximum(a_ed, 1)
        x = (a_ed - a_es) / (fr[4] * np.maximum(bl, 1))
        fac.append([f[s * 3 + t] for s, t in REG])
        exc.append([x[s * 3 + t] for s, t in REG])
    return np.mean(fac, 0), np.mean(exc, 0)


def f1(y, p):
    tp, fp, fn = int((y & p).sum()), int((~y & p).sum()), int((y & ~p).sum())
    return 2 * tp / max(1, 2 * tp + fp + fn)


def metrics(y, p):
    y, p = np.asarray(y, bool), np.asarray(p, bool)
    return dict(sens=float((y & p).sum() / max(1, y.sum())), spec=float((~y & ~p).sum() / max(1, (~y).sum())),
                f1=f1(y, p), acc=float((y == p).mean()))


def fit(X, Y, rule):
    """thresholds: abnormal if X < t. per-position (6 thresholds) or one shared threshold; maximise segment F1."""
    grid = np.unique(np.quantile(X, np.linspace(0.01, 0.99, 197)))
    if rule == 'shared':
        best = max(grid, key=lambda t: f1(Y.ravel(), (X < t).ravel()))
        return np.full(6, best)
    return np.array([max(grid, key=lambda t: f1(Y[:, k], X[:, k] < t)) for k in range(6)])


def main():
    lab = {r['name']: np.array(r['seg'], bool) for r in json.load(open(os.path.join(HERE, 'results', 'labels_a4c.json')))['rows']}
    split = json.load(open(os.path.join(HERE, 'results', 'split.json')))['split']
    idx = json.load(open(os.path.join(C, 'index.json')))
    dev = sorted(n for n in idx if split[n] == 'dev')          # TEST never read here
    out = {}
    for sched in ('keyframe', 'full'):
        masks = np.load(os.path.join(C, f'masks_{sched}.npy'), mmap_mode='r')
        beats = json.load(open(os.path.join(C, f'beats_{sched}.json')))
        feats = {}
        for nme in dev:
            e = idx[nme]
            m = np.asarray(masks[e['start']:e['start'] + e['n']])
            b = beats[nme]['beats'] or [[int(np.argmax(beats[nme]['vols'])), int(np.argmin(beats[nme]['vols']))]]
            feats[nme] = clip_features(m, b)
        Y = np.stack([lab[n] for n in dev])
        rng = np.random.default_rng(7)
        folds = rng.permutation(len(dev)) % 5
        res = {}
        for fname, fi in (('fac', 0), ('exc', 1)):
            X = np.stack([feats[n][fi] for n in dev])
            for norm in ('abs', 'rel'):
                Xn = X / np.median(X, 1, keepdims=True) if norm == 'rel' else X
                for rule in ('per_position', 'shared'):
                    P = np.zeros_like(Y)
                    for k in range(5):
                        t = fit(Xn[folds != k], Y[folds != k], rule)
                        P[folds == k] = Xn[folds == k] < t
                    auc = []
                    for k in range(6):
                        pos, neg = Xn[Y[:, k], k], Xn[~Y[:, k], k]
                        auc.append(float((neg[:, None] > pos[None]).mean() + 0.5 * (neg[:, None] == pos[None]).mean()))
                    key = f'{fname}_{norm}_{rule}'
                    res[key] = dict(segment=metrics(Y.ravel(), P.ravel()), recording=metrics(Y.any(1), P.any(1)),
                                    auc_per_position=dict(zip(SEGS, auc)))
                    print(f"{sched:8s} {key:24s} seg F1 {res[key]['segment']['f1']:.3f} sens {res[key]['segment']['sens']:.3f} "
                          f"spec {res[key]['segment']['spec']:.3f} | rec F1 {res[key]['recording']['f1']:.3f} "
                          f"sens {res[key]['recording']['sens']:.3f} spec {res[key]['recording']['spec']:.3f} | AUC "
                          + ' '.join(f'{a:.2f}' for a in auc), flush=True)
        out[sched] = res
    json.dump(out, open(os.path.join(HERE, 'results', 'dev_cv.json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
