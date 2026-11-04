"""P1 1.6 evaluation on VAL: left-atrial volume, LV mass, wall thickness from board-equivalent masks vs the same
measurements on the CAMUS expert masks (tcp_image_ui/cardiac_measure.py, biplane Simpson as F4).

Masks: integer-model logits (results/quant/<tag>/outputs/val_*.npy = what the board returns) >= 1 at 128 ->
x4 repeat -> Pillow NEAREST to the original size -> ef.clean(), exactly the F4 path. ED / ES frames = the expert
frames (as F4 "f1_expert"). LA at ES (largest LA), endocardium (F1 lv_r128) and epicardium at ED.
Expert masks: labels 1 (LV), 1|2 (epicardium), 3 (LA). Test split: not used (results/final_test_p1_16/lock.json).

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe eval_p1_16.py
"""
import json
import sys
import time

import numpy as np
from PIL import Image

from common import CACHE, CAMUS, LABELS, RESULTS, seg_metrics
from ef import clean
from nifti import read_nifti

sys.path.insert(0, str(RESULTS.parent.parent / 'tcp_image' / 'tcp_image_ui'))
import cardiac_measure as cm  # noqa: E402

TAGS = {'endo': 'lv_r128', 'epi': 'epi_r128', 'la': 'la_r128'}


def to_original(q128, w, h):
    m512 = np.repeat(np.repeat((q128 >= 1).astype(np.uint8) * 255, 4, 0), 4, 1)
    return clean(np.array(Image.fromarray(m512).resize((w, h), Image.NEAREST)) > 0)


def stats(pred, ref):
    pred, ref = np.asarray(pred, float), np.asarray(ref, float)
    d = pred - ref
    rel = d / np.where(ref == 0, np.nan, ref)
    return dict(n=len(d), mae=float(np.mean(np.abs(d))), bias=float(np.mean(d)), sd=float(np.std(d, ddof=1)),
                rel_bias_pct=float(np.nanmean(rel) * 100), rel_mae_pct=float(np.nanmean(np.abs(rel)) * 100),
                r=float(np.corrcoef(pred, ref)[0, 1]), ref_mean=float(np.mean(ref)), pred_mean=float(np.mean(pred)))


def main():
    out = evaluate('val', lambda k, i: np.load(RESULTS / 'quant' / TAGS[k] / 'outputs' / f'val_{i:03d}.npy'))
    dst = RESULTS / 'p1_16'
    dst.mkdir(exist_ok=True)
    (dst / 'eval_val.json').write_text(json.dumps(out, indent=1, default=float), encoding='utf-8')
    print(json.dumps(out['summary'], indent=1))


def evaluate(split, qload):
    """qload(target, frame index) -> int8 128x128 board-equivalent logits. Returns the result dict."""
    t0 = time.time()
    meta = json.loads((CACHE / f'{split}_meta.json').read_text(encoding='utf-8'))
    q = {k: [qload(k, i) for i in range(len(meta))] for k in TAGS}
    dice = {k: [] for k in TAGS}
    views = {}
    for i, m in enumerate(meta):
        p, view, phase = m['patient'], m['view'], m['phase']
        gt, sp = read_nifti(CAMUS / 'database_nifti' / p / f'{p}_{view}_{phase}_gt.nii.gz')
        spacing = (sp[1], sp[0])
        ref = {'endo': gt == LABELS['lv'], 'epi': np.isin(gt, (LABELS['lv'], LABELS['myo'])), 'la': gt == LABELS['la']}
        v = views.setdefault((p, view), dict(spacing=spacing, gt={}, pred={}, quality=m['quality']))
        for k in TAGS:
            pm = to_original(q[k][i], m['width'], m['height'])
            inter = (pm & ref[k]).sum()
            dice[k].append(2 * inter / max(pm.sum() + ref[k].sum(), 1))
            v['gt'][(k, phase)] = ref[k]
            v['pred'][(k, phase)] = pm
    rows = []
    for p in sorted({k[0] for k in views}):
        row = dict(patient=p, quality=[views[(p, v)]['quality'] for v in ('2CH', '4CH')])
        for src in ('gt', 'pred'):
            vv = {}
            for view in ('2CH', '4CH'):
                s = views[(p, view)]
                vv[view] = dict(spacing=s['spacing'], ed_endo=s[src][('endo', 'ED')], ed_epi=s[src][('epi', 'ED')],
                                es_la=s[src][('la', 'ES')])
            try:
                row[src] = cm.measure(vv, calibrate=src == 'pred')
            except Exception as e:                     # counted, not hidden
                row[src] = dict(error=repr(e))
        rows.append(row)
        print(p, {s: {k: round(row[s][k], 1) for k in ('la_ml', 'lv_mass_g', 'wall_mm') if row[s].get(k) is not None}
                  for s in ('gt', 'pred')}, f'({time.time() - t0:.0f}s)', flush=True)
    for r in rows:                                    # expert-mask volume even when truncated (to count the cases)
        for src in ('gt', 'pred'):
            if 'error' not in r[src]:
                vv = {view: views[(r['patient'], view)] for view in ('2CH', '4CH')}
                try:
                    r[src]['la_any_ml'] = cm.biplane_volume(vv['2CH'][src][('la', 'ES')], vv['2CH']['spacing'],
                                                            vv['4CH'][src][('la', 'ES')], vv['4CH']['spacing'])
                except Exception:
                    r[src]['la_any_ml'] = None
    ok = [r for r in rows if 'error' not in r['gt'] and 'error' not in r['pred']]
    summary = dict(patients=len(rows), failed=len(rows) - len(ok),
                   dice={k: dict(mean=float(np.mean(v)), n=len(v)) for k, v in dice.items()})
    tr = [(r['pred']['la_truncated'], r['gt']['la_truncated']) for r in ok]
    summary['la_truncation'] = dict(rule='any LA pixel in the bottom %d rows, either view' % cm.LA_EDGE_ROWS,
                                    pred=sum(a for a, _ in tr), gt=sum(b for _, b in tr), both=sum(a and b for a, b in tr),
                                    pred_only=sum(a and not b for a, b in tr), gt_only=sum(b and not a for a, b in tr),
                                    calibration=cm.LA_CAL)
    pairs = [(r['pred']['la_raw_ml'], r['gt']['la_raw_ml']) for r in ok
             if r['pred']['la_raw_ml'] is not None and r['gt']['la_raw_ml'] is not None]
    summary['la_raw_ml'] = stats([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) > 2 else None
    for key in ('la_ml', 'lv_mass_g', 'epi_ml', 'endo_ml', 'wall_mm'):
        pairs = [(r['pred'][key], r['gt'][key]) for r in ok if r['pred'].get(key) is not None and r['gt'].get(key) is not None]
        summary[key] = stats([a for a, _ in pairs], [b for _, b in pairs])
    return dict(split=split, tags=TAGS, test_set_used=split == 'test', summary=summary, rows=rows,
                seconds=time.time() - t0)


if __name__ == '__main__':
    main()
