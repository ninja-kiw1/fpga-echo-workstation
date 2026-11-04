r"""Recording-level score candidates on DEV (5-fold CV, same folds / z as wm_proto2). DEV only.
  mean_z      : -mean_k z_k
  two_lowest  : -(mean of the two lowest z_k)
  ef          : -EF (board calibrated EF of the clip)
  lr          : logistic regression on (EF, mean z, min z), fitted on the fold's training recordings
Threshold = Youden on the training folds. Pre-set selection: best CV balanced accuracy.
"""
import json
import os

import numpy as np

from wm_proto import C, HERE, clip_features, metrics
from wm_proto2 import auc, youden


def logreg(X, y, l2=1.0, it=200):
    X = np.c_[np.ones(len(X)), X]
    w = np.zeros(X.shape[1])
    for _ in range(it):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) + l2 * np.r_[0, w[1:]]
        H = (X.T * (p * (1 - p))) @ X + l2 * np.diag(np.r_[0, np.ones(len(w) - 1)])
        w -= np.linalg.solve(H, g)
    return w


def main():
    lab = {r['name']: np.array(r['seg'], bool) for r in json.load(open(os.path.join(HERE, 'results', 'labels_a4c.json')))['rows']}
    split = json.load(open(os.path.join(HERE, 'results', 'split.json')))['split']
    idx = json.load(open(os.path.join(C, 'index.json')))
    dev = sorted(n for n in idx if split[n] == 'dev')
    Y = np.stack([lab[n] for n in dev])
    yr = Y.any(1)
    folds = np.random.default_rng(7).permutation(len(dev)) % 5
    out = {}
    for sched in ('keyframe', 'full'):
        masks = np.load(os.path.join(C, f'masks_{sched}.npy'), mmap_mode='r')
        beats = json.load(open(os.path.join(C, f'beats_{sched}.json')))
        X = []
        for n in dev:
            e = idx[n]
            m = np.asarray(masks[e['start']:e['start'] + e['n']])
            b = beats[n]['beats'] or [[int(np.argmax(beats[n]['vols'])), int(np.argmin(beats[n]['vols']))]]
            X.append(clip_features(m, b)[0])
        X = np.array(X)
        ef = np.array([beats[n]['ef'] if beats[n]['ef'] is not None else 50.0 for n in dev])
        S = {k: np.zeros(len(dev)) for k in ('mean_z', 'two_lowest', 'ef', 'lr')}
        P = {k: np.zeros(len(dev), bool) for k in S}
        for k in range(5):
            tr, te = folds != k, folds == k
            mu = np.array([X[tr][~Y[tr][:, j], j].mean() for j in range(6)])
            sd = np.array([X[tr][~Y[tr][:, j], j].std() for j in range(6)])
            Z = (X - mu) / sd
            Zs = np.sort(Z, 1)
            sc = dict(mean_z=-Z.mean(1), two_lowest=-Zs[:, :2].mean(1), ef=-ef)
            feat = np.c_[(ef - 50) / 10, Z.mean(1), Zs[:, 0]]
            w = logreg(feat[tr], yr[tr].astype(float))
            sc['lr'] = np.c_[np.ones(len(dev)), feat] @ w
            for key, s in sc.items():
                t = youden(s[tr], yr[tr])
                S[key][te], P[key][te] = s[te], s[te] >= t
        out[sched] = {}
        for key in S:
            m = metrics(yr, P[key])
            m['bal'] = (m['sens'] + m['spec']) / 2
            m['auc'] = auc(S[key][yr], S[key][~yr])
            out[sched][key] = m
            print(f"{sched:8s} {key:10s} REC sens {m['sens']:.3f} spec {m['spec']:.3f} F1 {m['f1']:.3f} bal {m['bal']:.3f} AUC {m['auc']:.3f}")
    json.dump(out, open(os.path.join(HERE, 'results', 'dev_cv3.json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
