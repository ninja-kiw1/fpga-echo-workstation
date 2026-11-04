r"""Recording- and segment-level rules on DEV (5-fold CV), building on wm_proto.clip_features. DEV only.
Per position k: z_k = (x_k - mean_k) / sd_k with mean / sd from the fold's NON-MI segments of that position.
  segment abnormal  <=> z_k < t_seg               (one shared t, Youden on the fold's segments)
  recording MI      <=> min_k z_k < t_rec          (worst segment; Youden on the fold's recordings)
Also reported: global LV FAC alone at recording level (does regional analysis add anything?).
Selection metric fixed before running: recording-level CV balanced accuracy, then segment-level balanced accuracy.

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe wm_proto2.py
"""
import json
import os

import numpy as np

from wm_proto import C, HERE, SEGS, clip_features, metrics


def auc(pos, neg):
    pos, neg = np.asarray(pos), np.asarray(neg)
    return float((pos[:, None] > neg[None]).mean() + 0.5 * (pos[:, None] == neg[None]).mean())


def youden(score, y):
    """threshold on score (higher = more abnormal) maximising sens + spec"""
    grid = np.unique(score)
    best = max(grid, key=lambda t: (score[y] >= t).mean() + (score[~y] < t).mean())
    return best


def main():
    lab = {r['name']: np.array(r['seg'], bool) for r in json.load(open(os.path.join(HERE, 'results', 'labels_a4c.json')))['rows']}
    split = json.load(open(os.path.join(HERE, 'results', 'split.json')))['split']
    idx = json.load(open(os.path.join(C, 'index.json')))
    dev = sorted(n for n in idx if split[n] == 'dev')
    Y = np.stack([lab[n] for n in dev])
    folds = np.random.default_rng(7).permutation(len(dev)) % 5
    out = {}
    for sched in ('keyframe', 'full'):
        masks = np.load(os.path.join(C, f'masks_{sched}.npy'), mmap_mode='r')
        beats = json.load(open(os.path.join(C, f'beats_{sched}.json')))
        F = {}
        for n in dev:
            e = idx[n]
            m = np.asarray(masks[e['start']:e['start'] + e['n']])
            b = beats[n]['beats'] or [[int(np.argmax(beats[n]['vols'])), int(np.argmin(beats[n]['vols']))]]
            F[n] = clip_features(m, b)
        gfac = np.array([1 - min(beats[n]['vols']) / max(beats[n]['vols']) for n in dev])   # crude global
        ef = np.array([beats[n]['ef'] if beats[n]['ef'] is not None else 50.0 for n in dev])
        for fname, fi in (('fac', 0), ('exc', 1)):
            X = np.stack([F[n][fi] for n in dev])
            Z = np.zeros_like(X)
            Ps, Pr, Sr = np.zeros_like(Y), np.zeros(len(dev), bool), np.zeros(len(dev))
            for k in range(5):
                tr, te = folds != k, folds == k
                mu = np.array([X[tr][~Y[tr][:, j], j].mean() for j in range(6)])
                sd = np.array([X[tr][~Y[tr][:, j], j].std() for j in range(6)])
                Ztr, Zte = (X[tr] - mu) / sd, (X[te] - mu) / sd
                Z[te] = Zte
                ts = youden(-Ztr.ravel(), Y[tr].ravel())
                Ps[te] = -Zte >= ts
                tr_score = -Ztr.min(1)
                trc = youden(tr_score, Y[tr].any(1))
                Sr[te] = -Zte.min(1)
                Pr[te] = Sr[te] >= trc
            seg, rec = metrics(Y.ravel(), Ps.ravel()), metrics(Y.any(1), Pr)
            for d in (seg, rec):
                d['bal'] = (d['sens'] + d['spec']) / 2
            res = dict(segment=seg, recording=rec, rec_auc_worst_z=auc(Sr[Y.any(1)], Sr[~Y.any(1)]),
                       seg_auc_pooled_z=auc(-Z[Y], -Z[~Y]),
                       rec_auc_global_fac=auc(-gfac[Y.any(1)], -gfac[~Y.any(1)]),
                       rec_auc_ef=auc(-ef[Y.any(1)], -ef[~Y.any(1)]),
                       seg_auc_per_position={s: auc(-Z[Y[:, j], j], -Z[~Y[:, j], j]) for j, s in enumerate(SEGS)})
            out[f'{sched}_{fname}'] = res
            print(f"{sched:8s} {fname}: SEG sens {seg['sens']:.3f} spec {seg['spec']:.3f} F1 {seg['f1']:.3f} bal {seg['bal']:.3f} "
                  f"AUC {res['seg_auc_pooled_z']:.3f} | REC sens {rec['sens']:.3f} spec {rec['spec']:.3f} F1 {rec['f1']:.3f} "
                  f"bal {rec['bal']:.3f} AUC worst-z {res['rec_auc_worst_z']:.3f} (EF alone {res['rec_auc_ef']:.3f}, "
                  f"global FAC {res['rec_auc_global_fac']:.3f})", flush=True)
    json.dump(out, open(os.path.join(HERE, 'results', 'dev_cv2.json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
