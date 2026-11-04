"""⑤ CT spleen: evaluate the integer model (= what the board computes) on whole volumes, with host post-processing.

The board returns int8 logits on the 128 grid for every slice. Host variants (all cheap, PC side):
  nearest       mask = logit >= 1, each cell copied 4 x 4          (the board's own mask)
  bilinear      logits bilinearly upsampled to 512, mask = value > 0.5   (same decision at the cell centres)
  + lcc         keep the largest 26-connected 3D component
Metrics per case on the original 512 x 512 x z grid: 3D Dice, volume (mL) and its error, HD95 and ASSD (mm,
anisotropic spacing). The post-processing variant is chosen on the validation cases only (best mean Dice).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I eval_ct.py --tag z3_r128                  # validation
    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I eval_ct.py --tag z3_r128 --final-test     # once, at the end
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
import torch.nn.functional as F   # noqa: E402
from scipy import ndimage   # noqa: E402

from common import RESULTS, dice3d, largest_cc3d, load_split, upsample_bilinear   # noqa: E402
from quantize_ct import run_int   # noqa: E402
from train_ct import case_inputs, load_set   # noqa: E402

VARIANTS = ('nearest', 'nearest+lcc', 'bilinear', 'bilinear+lcc')
LOCK = RESULTS / 'final_test' / 'test_set_used.txt'


def upsample(q, mode, k):
    if mode == 'nearest':
        return np.repeat(np.repeat(q >= 1, k, 1), k, 2)
    out = np.empty((len(q), q.shape[1] * k, q.shape[2] * k), bool)
    for i in range(0, len(q), 32):
        up = upsample_bilinear(q[i:i + 32], k)
        if i == 0:                      # same as torch (align_corners=False), checked on the first chunk
            with torch.inference_mode():
                t = F.interpolate(torch.from_numpy(q[:32].astype(np.float32)).unsqueeze(1), scale_factor=k,
                                  mode='bilinear', align_corners=False)[:, 0].numpy()
            assert np.array_equal(t, up)
        out[i:i + 32] = up > 0.5
    return out


def surface_distances(pred, gt, spacing_zyx):
    """HD95 and ASSD (mm); inf if one of the masks is empty."""
    if not pred.any() or not gt.any():
        return float('inf'), float('inf')
    sp = lambda m: m & ~ndimage.binary_erosion(m)
    ps, gs = sp(pred), sp(gt)
    d_pg = ndimage.distance_transform_edt(~gs, sampling=spacing_zyx)[ps]
    d_gp = ndimage.distance_transform_edt(~ps, sampling=spacing_zyx)[gs]
    d = np.concatenate([d_pg, d_gp])
    return float(np.percentile(d, 95)), float(d.mean())


def evaluate_case(q, gt, spacing, k, variants=VARIANTS):
    ml = spacing[0] * spacing[1] * spacing[2] / 1000
    zyx = (spacing[2], spacing[1], spacing[0])
    gt_ml = float(gt.sum()) * ml
    res = {}
    for v in variants:
        base, lcc = v.split('+')[0], v.endswith('+lcc')
        m = upsample(q, base, k)
        if lcc:
            m = largest_cc3d(m)
        hd95, assd = surface_distances(m, gt, zyx)
        res[v] = dict(dice=dice3d(m, gt), pred_ml=round(float(m.sum()) * ml, 1),
                      vol_err_pct=round(100 * (float(m.sum()) * ml - gt_ml) / gt_ml, 2), hd95_mm=hd95, assd_mm=assd)
    return round(gt_ml, 1), res


def summarize(rows, variants):
    s = {}
    for v in variants:
        g = lambda key: [r[v][key] for r in rows]
        s[v] = dict(dice_mean=float(np.mean(g('dice'))), dice_min=float(np.min(g('dice'))),
                    dice_median=float(np.median(g('dice'))),
                    abs_vol_err_pct_mean=float(np.mean(np.abs(g('vol_err_pct')))),
                    hd95_mm_mean=float(np.mean(g('hd95_mm'))), assd_mm_mean=float(np.mean(g('assd_mm'))))
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    ap.add_argument('--final-test', action='store_true')
    args = ap.parse_args()
    qdir = RESULTS / 'quant' / args.tag
    summary = json.loads((qdir / 'summary.json').read_text(encoding='utf-8'))
    mode, k = summary['input'], 512 // summary['res']
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qdir / 'parameters.npz', allow_pickle=False) as z:
        arrays = {kk: z[kk] for kk in z.files}
    torch.set_num_threads(8)
    split = load_split()
    t0 = time.time()
    if args.final_test:
        if LOCK.exists():
            sys.exit('test set already used: ' + LOCK.read_text(encoding='utf-8'))
        sel = json.loads((RESULTS / 'eval' / args.tag / 'val.json').read_text(encoding='utf-8'))
        variants = (sel['chosen'],) + tuple(v for v in VARIANTS if v != sel['chosen'])
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        LOCK.write_text('%s  tag=%s  chosen post-processing=%s (fixed on validation before this run)\n'
                        % (datetime.now(timezone.utc).isoformat(), args.tag, sel['chosen']), encoding='utf-8')
        cases = split['test']
    else:
        variants, cases = VARIANTS, split['val']
    data = load_set(cases)
    inputs = case_inputs(data, mode, k)
    rows = []
    for i, c in enumerate(cases):
        a, b = data['spans'][i]
        q = run_int(plan, arrays, inputs[i])
        if not args.final_test:
            assert np.array_equal(q, np.load(qdir / 'outputs' / f'{c}.npy'))
        gt_ml, res = evaluate_case(q, data['lab'][a:b].astype(bool), data['spacing'][i], k, variants)
        rows.append(dict(case=c, slices=b - a, spacing_mm=list(data['spacing'][i]), gt_ml=gt_ml, **res))
        print(c, gt_ml, {v: (round(res[v]['dice'], 4), res[v]['vol_err_pct'], round(res[v]['hd95_mm'], 1)) for v in variants},
              '(%.0fs)' % (time.time() - t0), flush=True)
    s = summarize(rows, variants)
    out = dict(tag=args.tag, split='test' if args.final_test else 'val', cases=cases, summary=s, per_case=rows)
    if not args.final_test:
        out['chosen'] = max(VARIANTS, key=lambda v: (round(s[v]['dice_mean'], 6), -VARIANTS.index(v)))
        out['rule'] = 'highest mean 3D Dice on the validation cases; ties -> the simpler variant'
    d = RESULTS / ('final_test' if args.final_test else 'eval') / args.tag
    d.mkdir(parents=True, exist_ok=True)
    (d / ('test.json' if args.final_test else 'val.json')).write_text(json.dumps(out, indent=1), encoding='utf-8')
    for v in variants:
        print(f"{v:14s} Dice {s[v]['dice_mean']:.4f} (min {s[v]['dice_min']:.4f}) |vol err| {s[v]['abs_vol_err_pct_mean']:.1f}% "
              f"HD95 {s[v]['hd95_mm_mean']:.1f} mm ASSD {s[v]['assd_mm_mean']:.2f} mm")
    if 'chosen' in out:
        print('chosen:', out['chosen'])


if __name__ == '__main__':
    main()
