"""P1 1.6 left-atrial volume: truncation flag + linear calibration fitted on the TRAIN split only.

Why: on VAL, 41/100 ES LA masks reach the bottom edge of the image (the atrium is cut off by the sector), so a
biplane volume there is a lower bound, not a measurement; and the remaining (non-truncated) cases show a systematic
under-estimate (area ratio ~0.91). Decided with the user 10-15: a truncated LA is reported as "左房被截断，不可测"
(no volume / LAVI); the remaining bias is removed by a linear calibration la = a * raw + b fitted on TRAIN (same idea
as the EchoNet EF calibration); evaluation is on non-truncated cases only.

Steps
  1. integer la_r128 model (results/quant/la_r128, what the board runs) on every TRAIN ES frame -> outputs_train/
     (cached; the board-equivalent mask path of eval_p1_16.to_original)
  2. per TRAIN patient: expert / predicted biplane LA volume, truncation of each (cardiac_measure.la_truncated)
  3. least squares gt = a * pred + b on patients where neither mask is truncated
  4. writes results/p1_16/la_calib.json (copied into cardiac_measure.LA_CAL by hand, with its sha256)
The test split is not touched (results/final_test_p1_16/lock.json).

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe fit_la_calib.py
"""
import json
import sys
import time

import numpy as np

from common import CAMUS, LABELS, RESULTS, load_cache
from eval_p1_16 import to_original
from nifti import read_nifti

sys.path.insert(0, str(RESULTS.parent.parent / 'tcp_image' / 'tcp_image_ui'))
import cardiac_measure as cm  # noqa: E402

TAG = 'la_r128'


def main():
    t0 = time.time()
    qdir = RESULTS / 'quant' / TAG
    odir = qdir / 'outputs_train'
    odir.mkdir(exist_ok=True)
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qdir / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    summ = json.loads((qdir / 'summary.json').read_text(encoding='utf-8'))
    k = 1 << int(summ['shift'])
    x, _, _, _, _, meta = load_cache('train', 'la')
    views = {}
    for i, m in enumerate(meta):
        if m['phase'] != 'ES':
            continue
        f = odir / f'train_{i:04d}.npy'
        if not f.exists():                      # torch only when outputs are missing (MKL/OpenMP clash at polyfit)
            import torch
            from int8_reference import run_integer
            g = torch.from_numpy(np.ascontiguousarray(x[i][::k, ::k]))
            q = g.unsqueeze(0).expand(3, -1, -1).unsqueeze(0).contiguous()
            with torch.inference_mode():
                np.save(f, run_integer(plan, arrays, q)[0][0, 0].numpy().astype(np.int8))
        p = m['patient']
        gt, sp = read_nifti(CAMUS / 'database_nifti' / p / f"{p}_{m['view']}_ES_gt.nii.gz")
        views[(p, m['view'])] = dict(spacing=(sp[1], sp[0]), gt=gt == LABELS['la'],
                                     pred=to_original(np.load(f), m['width'], m['height']))
        if len(views) % 100 == 0:
            print(len(views), f'{time.time() - t0:.0f}s', flush=True)
    rows = []
    for p in sorted({kk[0] for kk in views}):
        a, b = views[(p, '2CH')], views[(p, '4CH')]
        r = dict(patient=p)
        for src in ('gt', 'pred'):
            try:
                r[src + '_ml'] = cm.biplane_volume(a[src], a['spacing'], b[src], b['spacing'])
            except Exception as e:
                r[src + '_ml'] = None
                r[src + '_error'] = repr(e)
            r[src + '_trunc'] = bool(cm.la_truncated(a[src]) or cm.la_truncated(b[src]))
        rows.append(r)
    fit = [r for r in rows if not r['gt_trunc'] and not r['pred_trunc'] and r['gt_ml'] and r['pred_ml']]
    xp = np.array([r['pred_ml'] for r in fit])
    yg = np.array([r['gt_ml'] for r in fit])
    A, B = np.polyfit(xp, yg, 1)
    res = yg - (A * xp + B)
    out = dict(split='train', tag=TAG, a=float(A), b=float(B), n_fit=len(fit), n_patients=len(rows),
               gt_truncated=sum(r['gt_trunc'] for r in rows), pred_truncated=sum(r['pred_trunc'] for r in rows),
               raw_bias_ml=float(np.mean(xp - yg)), raw_rel_bias_pct=float(np.mean((xp - yg) / yg) * 100),
               cal_residual_sd_ml=float(np.std(res, ddof=2)), r=float(np.corrcoef(xp, yg)[0, 1]),
               truncation_rule='any LA pixel in the bottom %d rows of the original image, either view' % cm.LA_EDGE_ROWS,
               test_set_used=False, seconds=time.time() - t0, rows=rows)
    dst = RESULTS / 'p1_16'
    dst.mkdir(exist_ok=True)
    (dst / 'la_calib.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(json.dumps({kk: v for kk, v in out.items() if kk != 'rows'}, indent=1))


if __name__ == '__main__':
    main()
