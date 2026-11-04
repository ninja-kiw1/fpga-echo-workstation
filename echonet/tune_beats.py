"""③ choose the beat-analysis parameters and the EF calibration on the TRAINING subset only (1,500 TRAIN videos).

Grid: smoothing {1, 3}, minimum trough distance {0.2 .. 0.6} s, prominence {0.1 .. 0.6}, ED window {0.5, 0.7, 0.9}
x cycle, aggregate {mean, median}. Score = mean absolute error after the linear calibration fitted on the same subset,
for the full-frame mode; the chosen parameters are then also calibrated for the real-time mode (every k-th frame).
Writes results/beats_config_<features>.json.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I tune_beats.py --features t3_fp32
"""
import argparse
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np          # noqa: E402

from beats import analyze, calibrate, metrics, realtime_step   # noqa: E402
from common import RESULTS   # noqa: E402


def video_efs(feat, index, cfg, realtime):
    raw, true, fb = [], [], 0
    for v, e in index.items():
        vol = feat['vol'][e['start']:e['start'] + e['n']]
        k = realtime_step(e['fps']) if realtime else 1
        r = analyze(vol[::k], e['fps'], k, cfg)
        if not np.isfinite(r['ef']):
            r['ef'] = 0.0
        raw.append(r['ef'])
        true.append(e['ef'])
        fb += r['fallback']
    return np.array(raw), np.array(true), fb


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--features', required=True, help='results/frames/<features>_trainsub.npz')
    args = ap.parse_args()
    d = RESULTS / 'frames'
    feat = np.load(d / f'{args.features}_trainsub.npz')
    index = json.loads((d / f'{args.features}_trainsub.json').read_text(encoding='utf-8'))['videos']
    rows = []
    for smooth, dist, prom, win, agg in itertools.product((1, 3), (0.2, 0.3, 0.4, 0.5, 0.6), (0.1, 0.2, 0.3, 0.4, 0.5, 0.6), (0.5, 0.7, 0.9),
                                                           ('mean', 'median')):
        cfg = dict(smooth=smooth, dist_s=dist, prom=prom, win=win, agg=agg)
        raw, true, fb = video_efs(feat, index, cfg, False)
        a, b = calibrate(raw, true)
        m = metrics(a * raw + b, true)
        rows.append(dict(cfg=cfg, a=a, b=b, mae=m['mae'], raw_mae=metrics(raw, true)['mae'], fallback=fb))
    rows.sort(key=lambda r: r['mae'])
    best = rows[0]
    raw, true, fb = video_efs(feat, index, best['cfg'], True)
    a_rt, b_rt = calibrate(raw, true)
    out = dict(features=args.features, videos=len(index), cfg=best['cfg'],
               full=dict(a=best['a'], b=best['b'], train_mae=best['mae'], raw_mae=best['raw_mae'], fallback=best['fallback']),
               realtime=dict(a=a_rt, b=b_rt, train_mae=metrics(a_rt * raw + b_rt, true)['mae'], fallback=fb),
               top10=rows[:10], rule='min calibrated MAE on the 1,500-video TRAIN subset (in-sample)')
    (RESULTS / f'beats_config_{args.features}.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in out.items() if k != 'top10'}, indent=1))


if __name__ == '__main__':
    main()
