"""③ EF estimators from a volume curve, chosen on the TRAINING subset only (VAL only reports).

Per video (full-frame and real-time curves):
  beat_median   the tuned beat-to-beat EF (beats.py, results/beats_config_*.json)
  pct_5_95      1 - P5(V) / P95(V) over the whole curve (no beat detection; robust to missed beats)
  pct_10_90     1 - P10(V) / P90(V)
Candidates: each feature alone with a linear calibration, and a linear model on all three; fit on the 1,500 TRAIN
videos, picked by 5-fold cross-validated MAE inside TRAIN; then applied unchanged to VAL.
Writes results/ef_estimator_<features>.json.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I ef_features.py --features t3_fp32
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np          # noqa: E402

from beats import analyze, metrics, realtime_step   # noqa: E402
from common import RESULTS   # noqa: E402

NAMES = ('beat_median', 'pct_5_95', 'pct_10_90')
CANDIDATES = {'beat_median': [0], 'pct_5_95': [1], 'pct_10_90': [2], 'all3': [0, 1, 2]}


def video_features(vol, fps, cfg, realtime):
    k = realtime_step(fps) if realtime else 1
    v = vol[::k]
    r = analyze(v, fps, k, cfg)
    p = lambda lo, hi: 100 * (1 - np.percentile(v, lo) / max(np.percentile(v, hi), 1e-9))
    return [r['ef'] if np.isfinite(r['ef']) else 0.0, p(5, 95), p(10, 90)]


def table(features, split, cfg, realtime):
    d = RESULTS / 'frames'
    feat = np.load(d / f'{features}_{split}.npz')
    index = json.loads((d / f'{features}_{split}.json').read_text(encoding='utf-8'))['videos']
    X, y = [], []
    for v, e in index.items():
        X.append(video_features(feat['vol'][e['start']:e['start'] + e['n']], e['fps'], cfg, realtime))
        y.append(e['ef'])
    return np.array(X), np.array(y)


def fit(X, y):
    A = np.c_[X, np.ones(len(X))]
    w, *_ = np.linalg.lstsq(A, y, rcond=None)
    return w


def predict(w, X):
    return np.c_[X, np.ones(len(X))] @ w


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--features', required=True)
    args = ap.parse_args()
    conf = json.loads((RESULTS / f'beats_config_{args.features}.json').read_text(encoding='utf-8'))
    out = dict(features=args.features, cfg=conf['cfg'], modes={})
    for mode in ('full', 'realtime'):
        Xt, yt = table(args.features, 'trainsub', conf['cfg'], mode == 'realtime')
        Xv, yv = table(args.features, 'val', conf['cfg'], mode == 'realtime')
        folds = np.arange(len(yt)) % 5
        cv = {}
        for name, cols in CANDIDATES.items():
            err = []
            for f in range(5):
                w = fit(Xt[folds != f][:, cols], yt[folds != f])
                err.append(np.abs(predict(w, Xt[folds == f][:, cols]) - yt[folds == f]))
            cv[name] = float(np.concatenate(err).mean())
        best = min(cv, key=cv.get)
        res = {}
        for name, cols in CANDIDATES.items():
            w = fit(Xt[:, cols], yt)
            res[name] = dict(weights=w.tolist(), train_cv_mae=cv[name], val=metrics(predict(w, Xv[:, cols]), yv))
        out['modes'][mode] = dict(chosen=best, candidates=res)
        print(mode, 'chosen on TRAIN (5-fold CV):', best, {k: round(v, 3) for k, v in cv.items()})
        for name in CANDIDATES:
            m = res[name]['val']
            print('   %-12s VAL MAE %.2f RMSE %.2f R2 %.3f' % (name, m['mae'], m['rmse'], m['r2']))
    (RESULTS / f'ef_estimator_{args.features}.json').write_text(json.dumps(out, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
