"""v5.26 view rule settings for the CAMUS chain: how often to run the RV program and how many recent ratios to take the
median of. Streams as design.py (CAMUS half sequences played back and forth at 50 fps, 2 s); RV (rv_pl1_5 integer) on
the 128 frame and F1 LV area (eq_clean) at frames t > 0, t % view_every == 0; decision at each of them = median of the
last view_n ratios >= threshold. Per setting: threshold = TRAIN-best decision accuracy (all decisions of all TRAIN
clips), VAL decision accuracy reported once, plus the share of VAL clips whose LAST decision is right and the PL cost.
TEST not touched.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I view_cfg.py
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
os.add_dll_directory(r'C:\msys64\mingw64\bin')
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PLD / 'keyframe'))
import echo_core as ec  # noqa: E402
from design import FPS, integer_model, streams  # noqa: E402

SETTINGS = [(25, 3), (25, 5), (25, 7), (20, 5), (15, 5), (15, 7), (10, 5), (10, 7), (10, 10), (5, 10)]


def ratios(split, rvm):
    out = []
    for e, fr, q, _ in streams(split):
        ts = np.arange(5, len(fr), 5)
        lg = rvm(fr[ts])
        r = {}
        for t, g in zip(ts, lg):
            lab, n = ndimage.label(g >= 1)
            rv = int(np.bincount(lab.ravel())[1:].max()) if n else 0
            lv = int(ec.eq_clean(q[t] >= 1)[0].sum())
            r[int(t)] = rv / max(lv, 1)
        out.append((e['view'] == '4CH', r))
    return out


def decisions(rows, ve, vn):
    d = []
    for is4, r in rows:
        hist, dec = [], []
        for t in sorted(r):
            if t % ve:
                continue
            hist.append(r[t])
            dec.append(float(np.median(hist[-vn:])))
        d.append((is4, dec))
    return d


def main():
    rvm = integer_model(PLD / 'cardiac_rv' / 'results' / 'quant' / 'rv_pl1_5')
    tr, va = ratios('train', rvm), ratios('val', rvm)
    res = []
    for ve, vn in SETTINGS:
        dt, dv = decisions(tr, ve, vn), decisions(va, ve, vn)
        allt = np.array([(m, is4) for is4, dec in dt for m in dec])
        thr = float(max(np.unique(allt[:, 0]), key=lambda v: ((allt[:, 0] >= v) == allt[:, 1]).mean()))
        allv = np.array([(m, is4) for is4, dec in dv for m in dec])
        acc_v = float(((allv[:, 0] >= thr) == allv[:, 1]).mean())
        last_v = float(np.mean([(dec[-1] >= thr) == is4 for is4, dec in dv if dec]))
        acc_t = float(((allt[:, 0] >= thr) == allt[:, 1]).mean())
        res.append(dict(view_every=ve, view_n=vn, threshold=thr, train_acc=acc_t, val_decision_acc=acc_v, val_last_acc=last_v,
                        rv_runs_per_s=FPS / ve, pl_share=FPS / ve * 0.023, window_s=ve * vn / FPS))
        print('every %2d n %2d (window %.1f s, RV %.0f/s = %2.0f%% PL): thr %.3f  TRAIN %.3f  VAL decisions %.3f  VAL last %.3f'
              % (ve, vn, ve * vn / FPS, FPS / ve, 100 * FPS / ve * 0.023, thr, acc_t, acc_v, last_v), flush=True)
    (HERE / 'view_cfg.json').write_text(json.dumps(res, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
