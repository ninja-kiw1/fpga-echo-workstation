"""⑤ CT spleen: case split + per-case caches.

Split (fixed before any training, seed 20261010): the 41 labelled cases -> 27 train / 7 val / 7 test,
by case (all slices of a case stay in one set). imagesTs has no public labels and is not used.
Training, calibration and model selection use train / val only; test is evaluated once at the end.

Cache per case (data/cache/<case>.npz):
  img      [z, 512, 512] uint8, soft-tissue window W400 / L40 (common.window_u8)
  lab      [z, 512, 512] uint8 {0, 1}
  spacing  (dx, dy, dz) mm
Orientation: the stored arrays show the patient rotated by 180 degrees (spine at the top, liver on the
right; all 61 cases have the same sform signs), so both are rotated to the radiological view
(anterior up, patient's right on the image left): a[:, ::-1, ::-1].

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I prep_ct.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # run with python -I (downloaded data)
import numpy as np                                            # noqa: E402

from common import CACHE, DATA, RESULTS, read_nifti, window_u8   # noqa: E402

SEED = 20261010
N_VAL, N_TEST = 7, 7


def main():
    t0 = time.time()
    CACHE.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    meta = json.loads((DATA / 'dataset.json').read_text(encoding='utf-8'))
    cases = sorted((e['image'].split('/')[-1][:-len('.nii.gz')] for e in meta['training']),
                   key=lambda c: int(c.split('_')[1]))
    assert len(cases) == 41
    order = list(np.random.default_rng(SEED).permutation(len(cases)))
    test = sorted((cases[i] for i in order[:N_TEST]), key=lambda c: int(c.split('_')[1]))
    val = sorted((cases[i] for i in order[N_TEST:N_TEST + N_VAL]), key=lambda c: int(c.split('_')[1]))
    train = sorted((c for c in cases if c not in test and c not in val), key=lambda c: int(c.split('_')[1]))
    info = {}
    for c in cases:
        img, sp = read_nifti(DATA / 'imagesTr' / f'{c}.nii.gz')
        lab, sp2 = read_nifti(DATA / 'labelsTr' / f'{c}.nii.gz')
        assert img.shape == lab.shape and np.allclose(sp, sp2)
        u8 = window_u8(img)[:, ::-1, ::-1]
        lb = (lab > 0).astype(np.uint8)[:, ::-1, ::-1]
        np.savez(CACHE / f'{c}.npz', img=np.ascontiguousarray(u8), lab=np.ascontiguousarray(lb),
                 spacing=np.array(sp, np.float64))
        zs = np.nonzero(lb.reshape(len(lb), -1).any(1))[0]
        info[c] = dict(slices=int(len(lb)), spacing_mm=[round(v, 4) for v in sp],
                       spleen_slices=int(len(zs)), spleen_z=[int(zs.min()), int(zs.max())],
                       spleen_ml=round(float(lb.sum()) * sp[0] * sp[1] * sp[2] / 1000, 1))
        print(c, info[c], '(%.0fs)' % (time.time() - t0), flush=True)
    split = dict(seed=SEED, rule='41 labelled cases, permutation(seed): first 7 test, next 7 val, rest train',
                 train=train, val=val, test=test,
                 slices={k: int(sum(info[c]['slices'] for c in v)) for k, v in (('train', train), ('val', val), ('test', test))},
                 spleen_slices={k: int(sum(info[c]['spleen_slices'] for c in v))
                                for k, v in (('train', train), ('val', val), ('test', test))},
                 orientation='cache = stored array[:, ::-1, ::-1] (radiological view)',
                 window='W400 / L40 HU', cases=info)
    (RESULTS / 'split.json').write_text(json.dumps(split, indent=1), encoding='utf-8')
    print(json.dumps({k: split[k] for k in ('train', 'val', 'test', 'slices', 'spleen_slices')}, indent=1))


if __name__ == '__main__':
    main()
