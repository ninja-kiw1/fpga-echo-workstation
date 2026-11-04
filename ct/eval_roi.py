"""⑤ final evaluation of the CT spleen system, both pipelines from the integer models (= the board):
  single  model 14 coarse, bilinear + largest 3D component                    (stage 1, accepted on board v5.6)
  two     + stage-2 ROI fine model (roi.py)                                       (stage 2)
Metrics per case on the original grid: 3D Dice, volume error, HD95, ASSD (eval_ct.surface_distances).
Which pipeline is "the system" is decided on validation only (README rule: stage 2 only if it beats single stage).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I eval_roi.py --fine roi_prior                 # validation
    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I eval_roi.py --fine roi_prior --final-test    # ONCE, writes the lock
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'lwnet'))

import numpy as np          # noqa: E402
import torch                # noqa: E402

from common import RESULTS, dice3d, load_split   # noqa: E402
from eval_ct import surface_distances   # noqa: E402
from quantize_ct import run_int   # noqa: E402
from roi import coarse_mask, combine, fine_inputs, roi_window   # noqa: E402
from train_ct import case_inputs, load_set   # noqa: E402

LOCK = RESULTS / 'final_test' / 'test_set_used.txt'


def load_plan(tag):
    d = RESULTS / 'quant' / tag
    plan = json.loads((d / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(d / 'parameters.npz', allow_pickle=False) as z:
        return plan, {k: z[k] for k in z.files}


def metrics(m, gt, sp):
    ml = sp[0] * sp[1] * sp[2] / 1000
    hd95, assd = surface_distances(m, gt, (sp[2], sp[1], sp[0]))
    gml = float(gt.sum()) * ml
    return dict(dice=dice3d(m, gt), pred_ml=round(float(m.sum()) * ml, 1),
                vol_err_pct=round(100 * (float(m.sum()) * ml - gml) / gml, 2), hd95_mm=hd95, assd_mm=assd)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--coarse', default='z3_r128')
    ap.add_argument('--fine', required=True)
    ap.add_argument('--final-test', action='store_true')
    args = ap.parse_args()
    torch.set_num_threads(8)
    split = load_split()
    if args.final_test:
        if LOCK.exists():
            sys.exit('test set already used: ' + LOCK.read_text(encoding='utf-8'))
        val = json.loads((RESULTS / 'eval' / f'two_stage_{args.fine}' / 'val.json').read_text(encoding='utf-8'))
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        LOCK.write_text('%s  coarse=%s fine=%s  system chosen on validation before this run: %s\n'
                        % (datetime.now(timezone.utc).isoformat(), args.coarse, args.fine, val['system']), encoding='utf-8')
        cases = split['test']
    else:
        cases = split['val']
    cmode = json.loads((RESULTS / 'quant' / args.coarse / 'summary.json').read_text(encoding='utf-8'))['input']
    fmode = json.loads((RESULTS / 'quant' / args.fine / 'summary.json').read_text(encoding='utf-8'))['input']
    pc, pf = load_plan(args.coarse), load_plan(args.fine)
    data = load_set(cases)
    inputs = case_inputs(data, cmode, 4)
    rows, t0 = [], time.time()
    for i, c in enumerate(cases):
        a, b = data['spans'][i]
        sp, gt, img = data['spacing'][i], data['lab'][a:b].astype(bool), data['img'][a:b]
        q0 = run_int(*pc, inputs[i])
        m0 = coarse_mask(q0)
        win = roi_window(m0, sp[2])
        if win is None:
            m1, nf = m0, 0
        else:
            x = fine_inputs(img, m0, win, fmode, sp[2]).transpose(0, 3, 1, 2).copy()
            q1 = run_int(*pf, x)
            if not args.final_test:
                assert np.array_equal(q1, np.load(RESULTS / 'quant' / args.fine / 'outputs' / f'{c}.npy'))
            m1, nf = combine(q1, win, b - a, 0.5), len(x)
        rows.append(dict(case=c, slices=b - a, fine_slices=nf, window=None if win is None else list(win),
                         spacing_mm=list(sp), gt_ml=round(float(gt.sum()) * sp[0] * sp[1] * sp[2] / 1000, 1),
                         single=metrics(m0, gt, sp), two=metrics(m1, gt, sp)))
        print(c, 'single %.4f two %.4f' % (rows[-1]['single']['dice'], rows[-1]['two']['dice']),
              'fine slices %d/%d' % (nf, b - a), '(%.0fs)' % (time.time() - t0), flush=True)
    summ = {}
    for k in ('single', 'two'):
        g = lambda key: [r[k][key] for r in rows]
        summ[k] = dict(dice_mean=float(np.mean(g('dice'))), dice_min=float(np.min(g('dice'))),
                       abs_vol_err_pct_mean=float(np.mean(np.abs(g('vol_err_pct')))),
                       hd95_mm_mean=float(np.mean(g('hd95_mm'))), assd_mm_mean=float(np.mean(g('assd_mm'))))
    summ['fine_slices_fraction'] = sum(r['fine_slices'] for r in rows) / sum(r['slices'] for r in rows)
    out = dict(coarse=args.coarse, fine=args.fine, split='test' if args.final_test else 'val', cases=cases,
               summary=summ, per_case=rows)
    if not args.final_test:
        out['system'] = 'two' if summ['two']['dice_mean'] > summ['single']['dice_mean'] else 'single'
        out['rule'] = 'stage 2 adopted only if its mean validation 3D Dice beats single stage'
    d = RESULTS / ('final_test' if args.final_test else 'eval') / f'two_stage_{args.fine}'
    d.mkdir(parents=True, exist_ok=True)
    (d / ('test.json' if args.final_test else 'val.json')).write_text(json.dumps(out, indent=1), encoding='utf-8')
    for k in ('single', 'two'):
        s = summ[k]
        print(f"{k:6s} Dice {s['dice_mean']:.4f} (min {s['dice_min']:.4f}) |vol err| {s['abs_vol_err_pct_mean']:.1f}% "
              f"HD95 {s['hd95_mm_mean']:.1f} mm ASSD {s['assd_mm_mean']:.2f} mm")
    print('fine slices %.1f%% of all slices' % (100 * summ['fine_slices_fraction']), '| system:', out.get('system', '(test)'))


if __name__ == '__main__':
    main()
