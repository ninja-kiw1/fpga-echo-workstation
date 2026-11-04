"""How do the CAMUS in-between labels behave over time? (evidence for the F3 training choice)

For every half sequence (ED -> ES) the LV area of the provided *_half_sequence_gt labels
and of F1's own prediction on the same frames (F1 sees only the image) is normalised by
its ED value and resampled to 11 points of normalised time. Real contraction is flat
right after ED (isovolumic phase) and then falls; a straight line from ED to ES points
to labels interpolated between the two expert frames. Training split: F1 FP32
(seq_train_f1prior128.npy); validation: F1 FP32 likewise. 128 grid, ratios only.

    python check_seq_labels.py   -> results/f3/label_shape.json
"""
import json

import numpy as np

from common import CACHE, RESULTS


def curves(split):
    lab = np.load(CACHE / f'seq_{split}_lv128.npy').reshape(-1, 128 * 128).sum(1).astype(float)
    f1 = np.load(CACHE / f'seq_{split}_f1prior128.npy').reshape(-1, 128 * 128).sum(1).astype(float)
    index = json.loads((CACHE / f'seq_{split}_index.json').read_text(encoding='utf-8'))
    tt = np.linspace(0, 1, 11)
    L, F, lin_dev_l, lin_dev_f = [], [], [], []
    for s in index:
        a = lab[s['start']:s['start'] + s['length']]
        b = f1[s['start']:s['start'] + s['length']]
        x = np.linspace(0, 1, len(a))
        la, fb = np.interp(tt, x, a / a[0]), np.interp(tt, x, b / b[0])
        L.append(la)
        F.append(fb)
        # largest deviation from the straight line between the first and last value
        lin_dev_l.append(np.abs(la - np.linspace(la[0], la[-1], 11)).max())
        lin_dev_f.append(np.abs(fb - np.linspace(fb[0], fb[-1], 11)).max())
    return dict(sequences=len(index), t=tt.tolist(),
                label_area_over_ed=np.mean(L, 0).round(4).tolist(), f1_area_over_ed=np.mean(F, 0).round(4).tolist(),
                label_max_dev_from_line_median=float(np.median(lin_dev_l)),
                f1_max_dev_from_line_median=float(np.median(lin_dev_f)))


def main():
    out = {split: curves(split) for split in ('train', 'val')}
    (RESULTS / 'f3').mkdir(parents=True, exist_ok=True)
    (RESULTS / 'f3' / 'label_shape.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    for split, r in out.items():
        print(split, r['sequences'], 'sequences')
        print('  labels area/ED', r['label_area_over_ed'])
        print('  F1     area/ED', r['f1_area_over_ed'])
        print('  median max deviation from a straight line: labels %.4f, F1 %.4f'
              % (r['label_max_dev_from_line_median'], r['f1_max_dev_from_line_median']))


if __name__ == '__main__':
    main()
