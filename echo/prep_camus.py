"""CAMUS -> caches matching the board's input chain (F1: single ED / ES frames).

Official split (database_split: 400 train / 50 validation / 50 test patients).
Per patient and view (2CH, 4CH): the expert-annotated ED and ES frames.
  img512   grey uint8, host LANCZOS stretch of the original frame
  lv512    LV cavity (label 1) for training, bilinear-resized then >= 0.5
  lvcells  original-resolution GT counts for exact Dice at native size
  meta     patient, view, phase, quality, size, spacing (mm), reference EF

    python prep_camus.py
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

from common import CACHE, CAMUS, LABELS, gt_cells, host_stretch
from nifti import read_nifti


def info(path):
    d = {}
    for line in Path(path).read_text().splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            d[k.strip()] = v.strip()
    return d


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    split = {s: (CAMUS / 'database_split' / f'subgroup_{s}.txt').read_text().split()
             for s in ('training', 'validation', 'testing')}
    assert sum(len(v) for v in split.values()) == 500 and len(set(sum(split.values(), []))) == 500
    for name, patients in (('train', split['training']), ('val', split['validation']), ('test', split['testing'])):
        imgs, lvs, cells, rows, cols, meta = [], [], [], [], [], []
        for p in patients:
            d = CAMUS / 'database_nifti' / p
            for view in ('2CH', '4CH'):
                cfg = info(d / f'Info_{view}.cfg')
                for phase in ('ED', 'ES'):
                    im, sp = read_nifti(d / f'{p}_{view}_{phase}.nii.gz')
                    gt, _ = read_nifti(d / f'{p}_{view}_{phase}_gt.nii.gz')
                    assert im.shape == gt.shape and np.array_equal(im, np.round(im)) and im.min() >= 0 and im.max() <= 255
                    gray = im.astype(np.uint8)
                    h, w = gray.shape
                    lv = gt == LABELS['lv']
                    imgs.append(host_stretch(gray))
                    lvs.append(np.array(Image.fromarray(lv.astype(np.uint8) * 255).resize((512, 512), Image.BILINEAR)) >= 128)
                    c, r, q = gt_cells(lv, w, h)
                    cells.append(c)
                    rows.append(r)
                    cols.append(q)
                    meta.append(dict(patient=p, view=view, phase=phase, quality=cfg['ImageQuality'],
                                     ef=float(cfg['EF']), frame=int(cfg[phase]), nframes=int(cfg['NbFrame']),
                                     width=w, height=h, spacing_mm=[sp[0], sp[1]],
                                     lv_area_px=int(lv.sum())))
        np.save(CACHE / f'{name}_img512.npy', np.stack(imgs))
        np.save(CACHE / f'{name}_lv512.npy', np.stack(lvs).astype(np.uint8))
        np.save(CACHE / f'{name}_lvcells.npy', np.stack(cells))
        np.save(CACHE / f'{name}_rows.npy', np.stack(rows))
        np.save(CACHE / f'{name}_cols.npy', np.stack(cols))
        (CACHE / f'{name}_meta.json').write_text(json.dumps(meta, indent=0), encoding='utf-8')
        q = {k: sum(m['quality'] == k for m in meta) // 4 for k in ('Good', 'Medium', 'Poor')}
        sizes = sorted({(m['width'], m['height']) for m in meta})
        print(f'{name}: {len(patients)} patients, {len(meta)} frames, quality {q}, '
              f'{len(sizes)} sizes {sizes[0]}..{sizes[-1]}', flush=True)


if __name__ == '__main__':
    main()
