"""⑤ stage 2 self-check: the training crop path (train_roi.render with no augmentation) gives exactly the inference
payloads of roi.fine_inputs, for both candidates, on every slice of the windows of 3 validation cases.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I check_roi_render.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'lwnet'))

import numpy as np          # noqa: E402
import torch                # noqa: E402

from common import RESULTS, load_split, neighbours   # noqa: E402
from roi import coarse_mask, fine_inputs, roi_window   # noqa: E402
from train_ct import load_set   # noqa: E402
from train_roi import render   # noqa: E402


def main():
    va = load_set(load_split()['val'][:3])
    bad = 0
    for c, (a, b), sp in zip(va['cases'], va['spans'], va['spacing']):
        img = va['img'][a:b]
        m0 = coarse_mask(np.load(RESULTS / 'quant' / 'z3_r128' / 'outputs' / f'{c}.npy'))
        y0, x0, z0, z1 = win = roi_window(m0, sp[2])
        p, n = neighbours(len(img), sp[2])
        zz = np.arange(z0, z1 + 1)
        t = lambda a_: torch.from_numpy(np.ascontiguousarray(a_)).float()
        lab = t(va['lab'][a:b][zz])
        k = len(zz)
        for mode in ('roi_z3', 'roi_prior'):
            want = fine_inputs(img, m0, win, mode, sp[2])
            if mode == 'roi_z3':
                src = torch.stack([t(img[p[zz]]) / 255, t(img[zz]) / 255, t(img[n[zz]]) / 255, lab], 1)
            else:
                nb = torch.floor((t(img[p[zz]]) + t(img[n[zz]]) + 1) / 2)
                src = torch.stack([t(img[zz]) / 255, t(m0[zz]), nb / 255, lab], 1)
            one, zero = torch.ones(k), torch.zeros(k)
            xb, _ = render(src, torch.full((k,), float(y0)), torch.full((k,), float(x0)), zero, one, one, zero,
                           mode == 'roi_prior')
            got = torch.round(xb * 255).to(torch.uint8).permute(0, 2, 3, 1).numpy()
            diff = int((got != want).sum())
            bad += diff
            print(c, mode, 'window', win, 'slices', k, 'differing values', diff, flush=True)
    print('ROI_RENDER_EXACT' if bad == 0 else 'ROI_RENDER_MISMATCH')
    sys.exit(0 if bad == 0 else 1)


if __name__ == '__main__':
    main()
