r"""ONE-TIME HMC-QU A4C TEST evaluation of the board's wall-motion prompts (final_test_2026-10-25/prereg.json).
Frozen before: split (results/split.json), masks of the board engine (engine_masks.py, label-free, all clips),
parameters (results/wm_params.json, fitted on DEV only). Writes the lock first, then the C features (echo_core.dll =
src/cardiac_ext.c) of the TEST clips' keyframe-schedule masks -> segment / recording metrics (fit_wm.summarise) +
LV EF of MI vs non-MI recordings. Writes results/final_test/{lock.txt, final_test.json}.

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe final_test.py
"""
import json
import os
from datetime import datetime, timezone

import numpy as np

from fit_wm import apply, load, summarise
from wm_proto2 import auc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'results', 'final_test')


def main():
    os.makedirs(OUT, exist_ok=True)
    lock = os.path.join(OUT, 'lock.txt')
    if os.path.exists(lock):
        raise SystemExit('already run: ' + open(lock, encoding='utf-8').read())
    with open(lock, 'w', encoding='utf-8') as f:
        f.write('%s HMC-QU TEST, params results/wm_params.json (DEV only), PREREG final_test_2026-10-25\n'
                % datetime.now(timezone.utc).isoformat())
    tl = json.load(open(os.path.join(HERE, 'results', 'test_lock.json')))
    tl.update(test_used=True, used_utc=datetime.now(timezone.utc).isoformat())
    json.dump(tl, open(os.path.join(HERE, 'results', 'test_lock.json'), 'w'), indent=1)
    lab = {r['name']: np.array(r['seg'], bool) for r in json.load(open(os.path.join(HERE, 'results', 'labels_a4c.json')))['rows']}
    split = json.load(open(os.path.join(HERE, 'results', 'split.json')))['split']
    test = sorted(n for n in split if split[n] == 'test')
    par = json.load(open(os.path.join(HERE, 'results', 'wm_params.json')))['params']
    X, ef, nb, _ = load(test)
    Y = np.stack([lab[n] for n in test])
    s = summarise(Y, apply(par, X, ef, nb))
    e = np.array([v if v is not None else np.nan for v in ef])
    mi = Y.any(1)
    s['ef'] = dict(mi_median=float(np.nanmedian(e[mi])), non_mi_median=float(np.nanmedian(e[~mi])),
                   auc_low_ef_for_mi=auc(-e[mi & ~np.isnan(e)], -e[~mi & ~np.isnan(e)]))
    s.update(n=len(test), mi=int(mi.sum()), segments_mi=int(Y.sum()), params='results/wm_params.json')
    json.dump(s, open(os.path.join(OUT, 'final_test.json'), 'w'), indent=1)
    with open(lock, 'a', encoding='utf-8') as f:
        f.write('%s done\n' % datetime.now(timezone.utc).isoformat())
    print(f"TEST n {len(test)} (MI {int(mi.sum())}): SEG sens {s['segment']['sens']:.3f} spec {s['segment']['spec']:.3f} "
          f"F1 {s['segment']['f1']:.3f} AUC {s['segment']['auc']:.3f} | REC sens {s['recording']['sens']:.3f} "
          f"spec {s['recording']['spec']:.3f} F1 {s['recording']['f1']:.3f} AUC {s['recording']['auc']:.3f}")
    print('per position AUC', {k: round(v['auc'], 3) for k, v in s['per_position'].items()}, 'EF', s['ef'])


if __name__ == '__main__':
    main()
