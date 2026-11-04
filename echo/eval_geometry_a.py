"""Part A (2026-10-16): LVMI class on VAL -- agreement between the board-equivalent masks and the CAMUS expert masks,
both through tcp_image_ui/cardiac_measure.py (= src/job_simpson.c, bit for bit).

Shipped (user 10-16): RWT is a value only; the class is LVMI > 95 F / 115 M g/m2 (cardiac_measure.lvmi_class).
Kept for the record (not shipped): the guideline four-class RWT x LVMI split ("four_class_guideline"), which saturated
because 49 / 50 expert-mask RWT are > 0.42.

Masks / frames exactly as eval_p1_16.evaluate('val') (integer la/epi/lv_r128 outputs -> original size -> clean; expert
labels 1 / 1|2 / 3). Test split: not used (results/final_test_p1_16/lock.json stays locked).

CAMUS gives the sex (Info_2CH.cfg) but no height / weight, so LVMI needs an assumed BSA: the agreement is reported for
a fixed BSA grid (the same BSA for the expert and the predicted mass, so only the mask differences move the class), and
the RWT part (concentric or not) needs no BSA at all. The reference here is our own measurement on the expert masks,
not an echocardiographer's linear measurement: it says how much the board segmentation changes the class, not whether
the class is clinically right. Sensitivity: the 2D-method LVMI limits (Table 6: 88 F / 102 M) instead of 95 / 115.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe eval_geometry_a.py
"""
import json
import time

import numpy as np

from common import CAMUS, RESULTS
from eval_p1_16 import cm, evaluate, stats

BSA_GRID = (1.6, 1.8, 2.0)
CODES = ('normal', 'concentric_remodeling', 'concentric_hypertrophy', 'eccentric_hypertrophy')
RWT = cm.RWT_GUIDELINE


def read_sex(p):
    for line in (CAMUS / 'database_nifti' / p / 'Info_2CH.cfg').read_text().splitlines():
        if line.startswith('Sex:'):
            return line.split(':')[1].strip()
    return None


def kappa(a, b, labels):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in labels)
    return float((po - pe) / (1 - pe)) if pe < 1 else 1.0


def agree(a, b, labels):
    cm_ = {x: {y: 0 for y in labels} for x in labels}             # rows = expert, columns = predicted
    for x, y in zip(a, b):
        cm_[x][y] += 1
    return dict(n=len(a), agree=sum(x == y for x, y in zip(a, b)), rate=float(np.mean([x == y for x, y in zip(a, b)])),
                kappa=kappa(a, b, labels), expert_counts={c: a.count(c) for c in labels},
                pred_counts={c: b.count(c) for c in labels}, confusion_expert_rows=cm_)


def classify(rows, bsa, limits):
    exp, prd = [], []
    for r in rows:
        g = []
        for src in ('gt', 'pred'):
            m = r[src]
            lvmi = m['lv_mass_g'] / bsa
            conc, high = m['rwt'] > RWT, lvmi > limits[r['sex']]
            g.append(('concentric_hypertrophy' if conc else 'eccentric_hypertrophy') if high else
                     ('concentric_remodeling' if conc else 'normal'))
        exp.append(g[0])
        prd.append(g[1])
    return agree(exp, prd, CODES)


def classify_lvmi(rows, bsa, limits):
    exp, prd = [], []
    for r in rows:
        g = []
        for src in ('gt', 'pred'):
            lvmi = r[src]['lv_mass_g'] / bsa
            g.append('increased' if lvmi > limits[r['sex']] else 'not_increased')
            if limits is cm.LVMI_LIMIT:                              # the shipped function gives the same class
                assert cm.lvmi_class(lvmi, r['sex'])['code'] == g[-1]
        exp.append(g[0])
        prd.append(g[1])
    return agree(exp, prd, ('not_increased', 'increased'))


def main():
    t0 = time.time()
    ev = evaluate('val', lambda k, i: np.load(RESULTS / 'quant' / {'endo': 'lv_r128', 'epi': 'epi_r128', 'la': 'la_r128'}[k]
                                              / 'outputs' / f'val_{i:03d}.npy'))
    rows = []
    for r in ev['rows']:
        if 'error' in r['gt'] or 'error' in r['pred'] or r['gt'].get('rwt') is None or r['pred'].get('rwt') is None:
            continue
        rows.append(dict(patient=r['patient'], sex=read_sex(r['patient']), quality=r['quality'],
                         gt={k: r['gt'][k] for k in ('lv_mass_g', 'wall_mm', 'lvidd_mm', 'rwt', 'lvidd_mm_views')},
                         pred={k: r['pred'][k] for k in ('lv_mass_g', 'wall_mm', 'lvidd_mm', 'rwt', 'lvidd_mm_views')}))
    lim2d = {s: hi for s, (lo, hi) in cm.LVMI_2D.items()}
    rwt_e = ['concentric' if r['gt']['rwt'] > RWT else 'not' for r in rows]
    rwt_p = ['concentric' if r['pred']['rwt'] > RWT else 'not' for r in rows]
    summary = dict(
        patients=len(ev['rows']), usable=len(rows), sex={s: sum(r['sex'] == s for r in rows) for s in ('F', 'M')},
        thresholds=dict(lvmi_increased_gt=cm.LVMI_LIMIT, rwt_guideline_not_applied=RWT, lvidd_disks=cm.LVIDD_DISKS,
                        source='Lang 2015 JASE 28:1-39 / EHJ-CVI 16:233-271, Table 6 + RWT x LVMI figure (checked on '
                               'the ASE-hosted Japanese full translation, pp. 13-14)'),
        continuous={k: stats([r['pred'][k] for r in rows], [r['gt'][k] for r in rows])
                    for k in ('rwt', 'lvidd_mm', 'wall_mm', 'lv_mass_g')},
        expert_rwt=dict(median=float(np.median([r['gt']['rwt'] for r in rows])),
                        above_guideline=int(sum(r['gt']['rwt'] > RWT for r in rows)),
                        p10_p90=[float(np.percentile([r['gt']['rwt'] for r in rows], q)) for q in (10, 90)]),
        lvmi_class={'bsa_%.1f' % b: classify_lvmi(rows, b, cm.LVMI_LIMIT) for b in BSA_GRID},
        lvmi_class_2d_limits={'bsa_%.1f' % b: classify_lvmi(rows, b, lim2d) for b in BSA_GRID},
        not_shipped=dict(rwt_class=agree(rwt_e, rwt_p, ('concentric', 'not')),
                         four_class_guideline={'bsa_%.1f' % b: classify(rows, b, cm.LVMI_LIMIT) for b in BSA_GRID},
                         four_class_2d_limits={'bsa_%.1f' % b: classify(rows, b, lim2d) for b in BSA_GRID}),
        note='reference = our measurement on the expert masks (2D apical masks), not a linear-method reading; '
             'BSA assumed (CAMUS has no height / weight); test split not used')
    out = dict(split='val', test_set_used=False, summary=summary, rows=rows, seconds=time.time() - t0)
    dst = RESULTS / 'p1_16' / 'eval_geometry_val.json'
    dst.write_text(json.dumps(out, indent=1, default=float), encoding='utf-8')
    s = summary
    print(json.dumps({k: s[k] for k in ('patients', 'usable', 'sex', 'continuous', 'expert_rwt')}, indent=1, default=float))
    ns = s['not_shipped']
    print('RWT > 0.42 (not shipped)', ns['rwt_class']['agree'], '/', ns['rwt_class']['n'],
          'kappa %.2f' % ns['rwt_class']['kappa'], ns['rwt_class']['expert_counts'], ns['rwt_class']['pred_counts'])
    for name, d in (('lvmi_class', s['lvmi_class']), ('lvmi_class_2d_limits', s['lvmi_class_2d_limits']),
                    ('four_class_guideline (not shipped)', ns['four_class_guideline'])):
        for b, a in d.items():
            print(name, b, a['agree'], '/', a['n'], 'kappa %.2f' % a['kappa'], 'expert', a['expert_counts'], 'pred', a['pred_counts'])


if __name__ == '__main__':
    main()
