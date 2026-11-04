"""CAMUS -> extra training targets for P1 1.6 (left atrium, LV epicardium), same chain as prep_camus.py.

The ED / ES frames, their order and the image caches are exactly those of prep_camus.py (img512 is not
rewritten); only the targets are new:
  la   label 3 (left atrium)
  epi  label 1 or 2 (LV cavity + myocardium = the epicardial contour; myocardium = epi - lv)
Per target and split: {split}_{target}512.npy (bilinear-resized then >= 0.5) and {split}_{target}cells.npy
(original-resolution counts per 512 cell, for Dice at native size). The test split is cached like the
others but stays locked (results/final_test_p1_16/LOCK) until the single final evaluation.

    python prep_camus_targets.py
"""
import json

import numpy as np
from PIL import Image

from common import CACHE, CAMUS, LABELS, gt_cells
from nifti import read_nifti

TARGETS = {'la': (LABELS['la'],), 'epi': (LABELS['lv'], LABELS['myo'])}


def main():
    split = {s: (CAMUS / 'database_split' / f'subgroup_{s}.txt').read_text().split()
             for s in ('training', 'validation', 'testing')}
    for name, patients in (('train', split['training']), ('val', split['validation']), ('test', split['testing'])):
        meta = json.loads((CACHE / f'{name}_meta.json').read_text(encoding='utf-8'))
        out = {t: ([], []) for t in TARGETS}
        i = 0
        for p in patients:
            d = CAMUS / 'database_nifti' / p
            for view in ('2CH', '4CH'):
                for phase in ('ED', 'ES'):
                    m = meta[i]
                    assert (m['patient'], m['view'], m['phase']) == (p, view, phase), (m, p, view, phase)
                    gt, _ = read_nifti(d / f'{p}_{view}_{phase}_gt.nii.gz')
                    h, w = gt.shape
                    assert (w, h) == (m['width'], m['height'])
                    for t, labels in TARGETS.items():
                        mask = np.isin(gt, labels)
                        out[t][0].append(np.array(Image.fromarray(mask.astype(np.uint8) * 255).resize(
                            (512, 512), Image.BILINEAR)) >= 128)
                        out[t][1].append(gt_cells(mask, w, h)[0])
                    i += 1
        assert i == len(meta)
        for t, (masks, cells) in out.items():
            np.save(CACHE / f'{name}_{t}512.npy', np.stack(masks).astype(np.uint8))
            np.save(CACHE / f'{name}_{t}cells.npy', np.stack(cells))
        print(name, len(meta), 'frames', {t: float(np.mean([c.sum() for c in out[t][1]])) for t in out}, flush=True)


if __name__ == '__main__':
    main()
