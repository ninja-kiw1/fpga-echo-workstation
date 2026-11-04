r"""Final wall-motion parameters from HMC-QU DEV only, on the board's own C (echo_core.dll = src/cardiac_ext.c) features
of the board's recommended keyframe schedule masks (engine_masks.py). Steps:
  1. C features == numpy prototype (wm_proto.clip_features) up to rounding, on every DEV clip;
  2. 5-fold CV on DEV with the C features (same folds as wm_proto2 / 3) -> the numbers reported for DEV;
  3. fit on all DEV: mu / sd of the non-MI segments per position, t_seg (Youden over all DEV segments), logistic
     (EF, mean z, min z) and t_rec (Youden over DEV recordings) -> results/wm_params.json.
TEST is not read.

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe fit_wm.py
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'keyframe'))
os.environ['PATH'] = r'C:\msys64\mingw64\bin;' + os.environ['PATH']
import echo_core  # noqa: E402
from wm_proto import C, SEGS, clip_features, metrics  # noqa: E402
from wm_proto2 import auc, youden  # noqa: E402
from wm_proto3 import logreg  # noqa: E402

SCHED = 'keyframe'


def load(names):
    idx = json.load(open(os.path.join(C, 'index.json')))
    masks = np.load(os.path.join(C, f'masks_{SCHED}.npy'), mmap_mode='r')
    beats = json.load(open(os.path.join(C, f'beats_{SCHED}.json')))
    X, ef, nb, dmax = [], [], [], 0.0
    for n in names:
        e = idx[n]
        m = np.asarray(masks[e['start']:e['start'] + e['n']])
        b = beats[n]['beats']
        fac, k = echo_core.wm_clip(m, b, beats[n]['vols'])
        bb = b or [[int(np.argmax(beats[n]['vols'])), int(np.argmin(beats[n]['vols']))]]
        dmax = max(dmax, float(np.abs(fac - clip_features(m, bb)[0]).max()))
        X.append(fac)
        ef.append(beats[n]['ef'])
        nb.append(k)
    return np.array(X), ef, nb, dmax


def fit(X, Y, ef):
    mu = np.array([X[~Y[:, j], j].mean() for j in range(6)])
    sd = np.array([X[~Y[:, j], j].std() for j in range(6)])
    Z = (X - mu) / sd
    t_seg = float(youden(-Z.ravel(), Y.ravel()))
    e = np.array([v if v is not None else 50.0 for v in ef])
    feat = np.c_[(e - 50) / 10, Z.mean(1), Z.min(1)]
    w = logreg(feat, Y.any(1).astype(float))
    score = np.c_[np.ones(len(X)), feat] @ w
    t_rec = float(youden(score, Y.any(1)))
    return dict(mu=mu.tolist(), sd=sd.tolist(), t_seg=t_seg, w=w.tolist(), t_rec=t_rec)


def apply(par, X, ef, nb):
    hp = echo_core.HcPar(flags=1, mu=(echo_core.ctypes.c_double * 6)(*par['mu']),
                         sd=(echo_core.ctypes.c_double * 6)(*par['sd']), t_seg=par['t_seg'],
                         w=(echo_core.ctypes.c_double * 4)(*par['w']), t_rec=par['t_rec'])
    return [echo_core.wm_eval(hp, x, k, e) for x, e, k in zip(X, ef, nb)]


def summarise(Y, outs):
    S = np.array([o['seg'] for o in outs])
    zs = np.array([o['z'] for o in outs])
    yr = Y.any(1)
    has = np.array([o['flag'] is not None for o in outs])
    pr = np.array([bool(o['flag']) for o in outs])
    sc = np.array([o['score'] if o['score'] is not None else -1e9 for o in outs])
    seg, rec = metrics(Y.ravel(), S.ravel()), metrics(yr[has], pr[has])
    for d in (seg, rec):
        d['bal'] = (d['sens'] + d['spec']) / 2
    seg['auc'] = auc(-zs[Y], -zs[~Y])
    rec['auc'] = auc(sc[has & yr], sc[has & ~yr])
    rec['no_score'] = int((~has).sum())
    per = {s: dict(auc=auc(-zs[Y[:, j], j], -zs[~Y[:, j], j]), **metrics(Y[:, j], S[:, j])) for j, s in enumerate(SEGS)}
    return dict(segment=seg, recording=rec, per_position=per)


def main():
    lab = {r['name']: np.array(r['seg'], bool) for r in json.load(open(os.path.join(HERE, 'results', 'labels_a4c.json')))['rows']}
    split = json.load(open(os.path.join(HERE, 'results', 'split.json')))['split']
    dev = sorted(n for n in split if split[n] == 'dev')
    X, ef, nb, dmax = load(dev)
    Y = np.stack([lab[n] for n in dev])
    print(f'C features vs numpy prototype: max |diff| {dmax:.3g}; clips without a usable beat {sum(k == 0 for k in nb)}')
    folds = np.random.default_rng(7).permutation(len(dev)) % 5
    cv = [None] * len(dev)
    for k in range(5):
        tr, te = folds != k, folds == k
        par = fit(X[tr], Y[tr], [ef[i] for i in np.nonzero(tr)[0]])
        for i, o in zip(np.nonzero(te)[0], apply(par, X[te], [ef[i] for i in np.nonzero(te)[0]], [nb[i] for i in np.nonzero(te)[0]])):
            cv[i] = o
    cvs = summarise(Y, cv)
    par = fit(X, Y, ef)
    fin = summarise(Y, apply(par, X, ef, nb))
    for name, s in (('DEV 5-fold CV', cvs), ('DEV refit (optimistic)', fin)):
        print(f"{name}: SEG sens {s['segment']['sens']:.3f} spec {s['segment']['spec']:.3f} F1 {s['segment']['f1']:.3f} "
              f"AUC {s['segment']['auc']:.3f} | REC sens {s['recording']['sens']:.3f} spec {s['recording']['spec']:.3f} "
              f"F1 {s['recording']['f1']:.3f} AUC {s['recording']['auc']:.3f}")
    print('per position CV AUC', {k: round(v['auc'], 3) for k, v in cvs['per_position'].items()})
    out = dict(schedule=SCHED, segments=SEGS, params=par, dev_n=len(dev), dev_mi=int(Y.any(1).sum()),
               c_vs_numpy_max_diff=dmax, dev_cv=cvs, dev_refit=fin, test_used=False,
               rule='segment prompt: -z >= t_seg; clip prompt: w0 + w1 (EF-50)/10 + w2 mean z + w3 min z >= t_rec')
    json.dump(out, open(os.path.join(HERE, 'results', 'wm_params.json'), 'w'), indent=1)
    print(json.dumps(par))


if __name__ == '__main__':
    main()
