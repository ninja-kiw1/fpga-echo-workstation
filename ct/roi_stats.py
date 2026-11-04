"""⑤ stage 2 planning (train + val labels only): spleen extent per slice / per volume on the 512 grid, and the
resolution ceiling -- Dice of the label itself after going down to the payload grid and back up
(cell fraction -> bilinear -> > 0.5), for the coarse grid (512 / 4 = 128) and a 2x ROI grid (256 crop / 2 = 128).
Writes results/roi_stats.json.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I roi_stats.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np          # noqa: E402

from common import RESULTS, dice3d, load_case, load_split, upsample_bilinear   # noqa: E402


def ceiling(lab, k):
    """label -> k x k cell fractions -> (fraction - 0.5) bilinear x k -> > 0: best a perfect model could do."""
    n = 512 // k
    f = lab.reshape(len(lab), n, k, n, k).mean(axis=(2, 4)).astype(np.float32)
    up = upsample_bilinear(f - 0.5, k) if k > 1 else f - 0.5
    return dice3d(up > 0, lab)


def main():
    split = load_split()
    out = dict(per_case={})
    for s in ('train', 'val'):
        for c in split[s]:
            lab = load_case(c)['lab'].astype(bool)
            zs = np.nonzero(lab.reshape(len(lab), -1).any(1))[0]
            ext = []
            for z in zs:
                r, q = np.nonzero(lab[z])
                ext.append(max(r.max() - r.min() + 1, q.max() - q.min() + 1))
            r, q = np.nonzero(lab.any(0))
            out['per_case'][c] = dict(split=s, slice_extent_px_max=int(max(ext)), slice_extent_px_median=int(np.median(ext)),
                                      volume_extent_px=int(max(r.max() - r.min() + 1, q.max() - q.min() + 1)),
                                      ceiling_k4=round(ceiling(lab, 4), 4), ceiling_k2=round(ceiling(lab, 2), 4))
            print(c, out['per_case'][c], flush=True)
    pc = out['per_case'].values()
    out['summary'] = dict(
        slice_extent_px_max=max(p['slice_extent_px_max'] for p in pc),
        cases_slice_extent_over_224=[c for c, p in out['per_case'].items() if p['slice_extent_px_max'] > 224],
        volume_extent_px_max=max(p['volume_extent_px'] for p in pc),
        ceiling_k4_val=float(np.mean([p['ceiling_k4'] for p in pc if p['split'] == 'val'])),
        ceiling_k2_val=float(np.mean([p['ceiling_k2'] for p in pc if p['split'] == 'val'])))
    (RESULTS / 'roi_stats.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(json.dumps(out['summary'], indent=1))


if __name__ == '__main__':
    main()
