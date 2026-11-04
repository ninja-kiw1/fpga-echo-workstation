"""CAMUS ED->ES half sequences -> 128x128 caches for F2 / F3 (interactive / propagation).

Every frame goes through the deployment chain: host LANCZOS stretch to 512x512,
board stride subsampling to 128x128 (x[::4, ::4]). The LV target at 128 is the
same as F1 training: bilinear to 512, >= 0.5, then 4x4 average >= 0.5.

Note: only the ED and ES frames carry the expert annotation; how the in-between
frames of *_half_sequence_gt were produced is not stated on the CAMUS pages.
They are used here for training (priors from neighbouring frames, extra
targets); all evaluation stays on the expert ED / ES frames.

Writes data/cache/seq_{split}_img128.npy, seq_{split}_lv128.npy, seq_{split}_index.json
"""
import json

import numpy as np
from PIL import Image

from common import CACHE, CAMUS, LABELS, host_stretch
from nifti import read_nifti


def lv128(gt_frame):
    m512 = np.array(Image.fromarray((gt_frame == LABELS['lv']).astype(np.uint8) * 255)
                    .resize((512, 512), Image.BILINEAR)) >= 128
    return (m512.reshape(128, 4, 128, 4).mean(axis=(1, 3)) >= 0.5).astype(np.uint8)


def main():
    split = {s: (CAMUS / 'database_split' / f'subgroup_{s}.txt').read_text().split()
             for s in ('training', 'validation', 'testing')}
    for name, patients in (('train', split['training']), ('val', split['validation']), ('test', split['testing'])):
        imgs, lvs, index, edes_ok = [], [], [], 0
        for p in patients:
            d = CAMUS / 'database_nifti' / p
            for view in ('2CH', '4CH'):
                seq, _ = read_nifti(d / f'{p}_{view}_half_sequence.nii.gz')
                gts, _ = read_nifti(d / f'{p}_{view}_half_sequence_gt.nii.gz')
                ed, _ = read_nifti(d / f'{p}_{view}_ED.nii.gz')
                es, _ = read_nifti(d / f'{p}_{view}_ES.nii.gz')
                edes_ok += int(np.array_equal(seq[0], ed) and np.array_equal(seq[-1], es))
                start = len(imgs)
                for t in range(seq.shape[0]):
                    imgs.append(host_stretch(seq[t].astype(np.uint8))[::4, ::4])
                    lvs.append(lv128(gts[t]))
                index.append(dict(patient=p, view=view, start=start, length=seq.shape[0]))
        np.save(CACHE / f'seq_{name}_img128.npy', np.stack(imgs))
        np.save(CACHE / f'seq_{name}_lv128.npy', np.stack(lvs))
        (CACHE / f'seq_{name}_index.json').write_text(json.dumps(index), encoding='utf-8')
        lens = [s['length'] for s in index]
        print(f'{name}: {len(index)} sequences, {len(imgs)} frames (length {min(lens)}..{max(lens)}), '
              f'first/last frame == ED/ES file in {edes_ok}/{len(index)}', flush=True)


if __name__ == '__main__':
    main()
