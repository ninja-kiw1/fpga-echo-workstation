"""Part E step 2: linear calibration of the tracked GLS-like value, fitted on TRAIN only (like the LA calibration).

Speckle tracking on CAMUS under-estimates the expert mid-wall length change by ~3-4 percentage points on VAL
(decorrelation / clutter pull frame-to-frame estimates towards zero). Fit ref = a * est + b on TRAIN with the FINAL
tracking config of track_val.py; blocks are placed on the EXPERT ED myocardium because TRAIN has no board-equivalent
epi / lv outputs (on VAL, expert vs model placement differ little: track_val.json 'final_expertED'). Sequences whose
expert ED / ES mid-wall line cannot be extracted are skipped. Writes results/calib_train.json; track_val.py applies it.
VAL and test split are not used here.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe calib_train.py
"""
import json
import time

import numpy as np

import track_val as tv


def main():
    t0 = time.time()
    c = next(x for x in tv.CONFIGS if x['name'] == tv.FINAL)
    tier = c['tier']
    meta = json.loads((tv.CACHE / 'train_meta.json').read_text(encoding='utf-8'))
    seqs = sorted({(m['patient'], m['view']) for m in meta})
    rows, skipped = [], []
    for i, (p, view) in enumerate(seqs):
        try:
            frames, sx, sy, gt = tv.load_sequence(p, view, tier)
            lines = {}
            for ph in ('ED', 'ES'):
                myo = tv.to_tier(gt[ph] == tv.LABELS['myo'], tier)
                lines[ph] = tv.midline(myo, sx, sy)
            ref = (tv.length_mm(tv.resample(lines['ES'], sx, sy, 1.0), sx, sy)
                   / tv.length_mm(tv.resample(lines['ED'], sx, sy, 1.0), sx, sy) - 1)
            pts0 = tv.resample(lines['ED'], sx, sy, tv.STEP_MM)
            trk, edges = tv.track_line(frames, pts0, c)
            est = tv.line_length(trk[-1], sx, sy, c) / tv.line_length(trk[0], sx, sy, c) - 1
            rows.append(dict(patient=p, view=view, est=est * 100, ref=ref * 100, edges=edges))
        except Exception as e:  # noqa: BLE001 - report and skip
            skipped.append(dict(patient=p, view=view, error=repr(e)))
        if (i + 1) % 100 == 0:
            print(i + 1, f'{time.time() - t0:.0f}s', flush=True)
    est = np.array([r['est'] for r in rows])
    ref = np.array([r['ref'] for r in rows])
    a, b = np.polyfit(est, ref, 1)
    out = dict(split='train', config=c, placement='expert ED', a=float(a), b=float(b), n_fit=len(rows),
               skipped=skipped, raw=tv.stats(est, ref), calibrated=tv.stats(a * est + b, ref), rows=rows)
    (tv.HERE / 'results' / 'calib_train.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(f"TRAIN n {len(rows)} (skipped {len(skipped)}): ref = {a:.4f} * est + {b:.3f}; raw bias {out['raw']['bias']:.2f} "
          f"MAE {out['raw']['mae']:.2f} r {out['raw']['r']:.2f} -> calibrated MAE {out['calibrated']['mae']:.2f}")
    print(f'done in {time.time() - t0:.0f} s')


if __name__ == '__main__':
    main()
