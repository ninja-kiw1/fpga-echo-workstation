"""F4 evaluation: biplane Simpson EF from board-equivalent masks vs the CAMUS reference EF (validation).

Per patient both views are needed (2CH + 4CH). Ways to pick the ED / ES masks:
  gt          expert masks (check: reproduces the reference exactly, see check_ef_gt.py)
  f1_expert   F1 on the expert ED and ES frames (the frames are known)
  f3_expert   ED = F1 at frame 0, ES = end of the F3 forward propagation
  f1_auto     ED / ES = largest / smallest LV area along the F1 every-frame curve
  f3_auto     ED / ES = largest / smallest area along the F3 forward curve  <- what the UI does
  click1_auto as f3_auto after one standard click on the ED frame (F2 model) before propagating
  click2_auto click1_auto plus one standard click on the last frame (see gen_f4_masks.py)
"auto" needs no knowledge of which frames are ED / ES (ties: first frame, as sequence.Tracker).
Masks go 128 -> x4 repeat -> 512 -> Pillow NEAREST to the original size, exactly the UI path,
then ef.clean() (largest component, holes filled; --raw skips it) right before Simpson.
Keyframes use the raw areas, i.e. the curve the UI shows (sequence.Tracker.keyframes).
Segmentation runs on the PL (board), keyframe choice and Simpson on the PC.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe eval_f4.py [--raw] [--final val|test]
--final reads results/final_<split>/ written by final_test_echo.py (stage B of the one-time test).
"""
import json
import sys
import time

import numpy as np
from PIL import Image

from check_ef_gt import info_cfg, load_gt
from common import CAMUS, RESULTS
from ef import clean, ejection_fraction, volumes

STRATEGIES = ('gt', 'f1_expert', 'f3_expert', 'f1_auto', 'f3_auto', 'click1_auto', 'click2_auto')


CLEAN = '--raw' not in sys.argv          # --raw: no clean(), to show why it is needed
FINAL = sys.argv[sys.argv.index('--final') + 1] if '--final' in sys.argv else None   # val | test (stage B)


def to_original(m128, w, h):
    m512 = np.repeat(np.repeat(m128.astype(np.uint8) * 255, 4, 0), 4, 1)
    return np.array(Image.fromarray(m512).resize((w, h), Image.NEAREST)) > 0


def prep(m):
    return clean(m) if CLEAN else m


def stats(pred, ref):
    pred, ref = np.asarray(pred, float), np.asarray(ref, float)
    d = pred - ref
    return dict(n=len(d), mae=float(np.mean(np.abs(d))), bias=float(np.mean(d)), sd=float(np.std(d, ddof=1)),
                loa=[float(np.mean(d) - 1.96 * np.std(d, ddof=1)), float(np.mean(d) + 1.96 * np.std(d, ddof=1))],
                r=float(np.corrcoef(pred, ref)[0, 1]), within_5=float(np.mean(np.abs(d) <= 5)))


def main():
    t0 = time.time()
    if FINAL:
        src = RESULTS / f'final_{FINAL}'
        z = np.load(src / 'masks128.npz')
        index = json.loads((src / 'masks128_index.json').read_text(encoding='utf-8'))
        target = src / 'final_f4_ef.json'
        lock = src / 'test_set_used.txt'
        if FINAL == 'test':
            if not lock.exists() or 'stage A finished' not in lock.read_text(encoding='utf-8'):
                sys.exit('run final_test_echo.py --split test first')
            if 'stage B' in lock.read_text(encoding='utf-8'):
                sys.exit(f'{lock}: EF on the test set was already evaluated.')
            lock.write_text(lock.read_text(encoding='utf-8') + f'stage B (EF) started {time.ctime()}\n', encoding='utf-8')
    else:
        z = np.load(RESULTS / 'f4' / 'val_masks128.npz')
        index = json.loads((RESULTS / 'f4' / 'val_masks128_index.json').read_text(encoding='utf-8'))
        target = RESULTS / 'f4' / ('eval_f4_val.json' if CLEAN else 'eval_f4_val_raw.json')
    masks = {'f1': z['f1'], 'fwd': z['fwd'], 'c1': z['c1'], 'c2': z['c2']}
    by = {(s['patient'], s['view']): s for s in index['sequences']}
    patients = sorted({s['patient'] for s in index['sequences']})
    rows = []
    for p in patients:
        row = dict(patient=p, ef_ref=float(info_cfg(CAMUS / 'database_nifti' / p / 'Info_2CH.cfg')['EF']), views={})
        picked = {k: {} for k in STRATEGIES}
        spacing = {}
        for view in ('2CH', '4CH'):
            s = by[(p, view)]
            w, h = s['width'], s['height']
            gt_ed, spacing[view] = load_gt(p, view, 'ED')
            gt_es, _ = load_gt(p, view, 'ES')
            picked['gt'][view] = (gt_ed, gt_es)
            seqs = {k: [to_original(m, w, h) for m in v[s['start']:s['start'] + s['length']]] for k, v in masks.items()}
            area = {k: np.array([m.sum() for m in v]) for k, v in seqs.items()}
            picked['f1_expert'][view] = (seqs['f1'][0], seqs['f1'][-1])
            picked['f3_expert'][view] = (seqs['fwd'][0], seqs['fwd'][-1])
            kf = {}
            for name, key in (('f1_auto', 'f1'), ('f3_auto', 'fwd'), ('click1_auto', 'c1'), ('click2_auto', 'c2')):
                ed, es = int(np.argmax(area[key])), int(np.argmin(area[key]))
                picked[name][view] = (seqs[key][ed], seqs[key][es])
                kf[name] = dict(ed=ed, es=es, length=s['length'])
            row['views'][view] = dict(quality=s['quality'], keyframes=kf)
        for name in STRATEGIES:
            a2, a4 = picked[name]['2CH'], picked[name]['4CH']
            if name != 'gt':
                a2, a4 = (prep(a2[0]), prep(a2[1])), (prep(a4[0]), prep(a4[1]))
            try:
                edv, esv = volumes(a2[0], a2[1], spacing['2CH'], a4[0], a4[1], spacing['4CH'])
                row[name] = dict(edv=edv, esv=esv, ef=ejection_fraction(edv, esv))
            except Exception as e:                       # e.g. no contour; counted, not hidden
                row[name] = dict(error=repr(e))
        rows.append(row)
        print(p, ' '.join(f"{n} {row[n].get('ef')}" for n in STRATEGIES), f"ref {row['ef_ref']:.0f} ({time.time() - t0:.0f}s)", flush=True)
    summary = {}
    for name in STRATEGIES:
        ok = [r for r in rows if 'ef' in r[name] and r[name]['ef'] is not None]
        good = [r for r in ok if all(v['quality'] != 'Poor' for v in r['views'].values())]
        summary[name] = dict(failed=len(rows) - len(ok), ef=stats([r[name]['ef'] for r in ok], [r['ef_ref'] for r in ok]),
                             ef_good_medium=stats([r[name]['ef'] for r in good], [r['ef_ref'] for r in good]),
                             edv=stats([r[name]['edv'] for r in ok], [r['gt']['edv'] for r in ok]),
                             esv=stats([r[name]['esv'] for r in ok], [r['gt']['esv'] for r in ok]))
    out = dict(seq_tag=index['seq_tag'], clean=CLEAN, patients=len(rows), summary=summary, rows=rows,
               split=FINAL or 'val', test_set_used=FINAL == 'test',
               seconds=time.time() - t0)
    target.write_text(json.dumps(out, indent=1), encoding='utf-8')
    if FINAL == 'test':
        lock.write_text(lock.read_text(encoding='utf-8') + f'stage B (EF) finished {time.ctime()}\n', encoding='utf-8')
    print(f"{'':10s} {'EF MAE':>7s} {'bias':>6s} {'SD':>5s} {'r':>6s} {'<=5':>5s} | {'EDV MAE':>7s} {'bias':>6s} {'r':>5s} | {'ESV MAE':>7s} {'bias':>6s} {'r':>5s} | EF MAE good+med")
    for name, s in summary.items():
        e, v1, v2 = s['ef'], s['edv'], s['esv']
        print(f"{name:10s} {e['mae']:7.2f} {e['bias']:6.2f} {e['sd']:5.2f} {e['r']:6.3f} {e['within_5']:5.2f} | "
              f"{v1['mae']:7.1f} {v1['bias']:6.1f} {v1['r']:5.3f} | {v2['mae']:7.1f} {v2['bias']:6.1f} {v2['r']:5.3f} | "
              f"{s['ef_good_medium']['mae']:.2f} (n={s['ef_good_medium']['n']}) failed {s['failed']}")


if __name__ == '__main__':
    main()
