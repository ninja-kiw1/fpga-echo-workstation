"""Does ef.py reproduce the CAMUS reference EF from the expert masks?

For every patient of the given splits: the four expert LV masks (2CH / 4CH x ED / ES) ->
ef.volumes -> EF, compared with EF in Info_2CH.cfg (the value CAMUS distributes; the
official notebook says it reproduces them). Also writes the expert-mask EDV / ESV, the
reference for the predicted volumes later. Run with the srtp-vision python (skimage):

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe check_ef_gt.py [--splits training validation]
"""
import argparse
import json
import time

import numpy as np

from common import CAMUS, LABELS, RESULTS
from ef import ejection_fraction, volumes
from nifti import read_nifti


def info_cfg(path):
    return dict(line.split(': ', 1) for line in path.read_text().splitlines() if ': ' in line)


def load_gt(p, view, phase):
    d = CAMUS / 'database_nifti' / p
    gt, spacing = read_nifti(d / f'{p}_{view}_{phase}_gt.nii.gz')
    return gt == LABELS['lv'], (spacing[1], spacing[0])          # spacing as (height, width)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--splits', nargs='+', default=['training', 'validation'])
    args = ap.parse_args()
    rows = []
    t0 = time.time()
    for split in args.splits:
        for p in (CAMUS / 'database_split' / f'subgroup_{split}.txt').read_text().split():
            m = {(v, ph): load_gt(p, v, ph) for v in ('2CH', '4CH') for ph in ('ED', 'ES')}
            edv, esv = volumes(m[('2CH', 'ED')][0], m[('2CH', 'ES')][0], m[('2CH', 'ED')][1],
                               m[('4CH', 'ED')][0], m[('4CH', 'ES')][0], m[('4CH', 'ED')][1])
            cfg2, cfg4 = info_cfg(CAMUS / 'database_nifti' / p / 'Info_2CH.cfg'), info_cfg(CAMUS / 'database_nifti' / p / 'Info_4CH.cfg')
            rows.append(dict(patient=p, split=split, edv=edv, esv=esv, ef=ejection_fraction(edv, esv),
                             ef_ref=float(cfg2['EF']), ef_ref_4ch=float(cfg4['EF']),
                             quality=cfg2['ImageQuality'] + '/' + cfg4['ImageQuality']))
            if len(rows) % 50 == 0:
                print(f'{len(rows)} patients ({time.time() - t0:.0f}s)', flush=True)
    err = np.array([r['ef'] - r['ef_ref'] for r in rows])
    summary = dict(patients=len(rows), splits=args.splits,
                   exact_match=float(np.mean(err == 0)), within_1=float(np.mean(np.abs(err) <= 1)),
                   mae=float(np.mean(np.abs(err))), max_abs=float(np.max(np.abs(err))),
                   ref_2ch_equals_4ch=float(np.mean([r['ef_ref'] == r['ef_ref_4ch'] for r in rows])),
                   seconds=time.time() - t0)
    (RESULTS / 'f4').mkdir(parents=True, exist_ok=True)
    tag = '_'.join(args.splits)
    (RESULTS / 'f4' / f'ef_gt_{tag}.json').write_text(json.dumps(dict(summary=summary, rows=rows), indent=1), encoding='utf-8')
    print(json.dumps(summary, indent=1))
    worst = sorted(rows, key=lambda r: -abs(r['ef'] - r['ef_ref']))[:5]
    for r in worst:
        print(r['patient'], r['ef'], r['ef_ref'], r['edv'], r['esv'])


if __name__ == '__main__':
    main()
