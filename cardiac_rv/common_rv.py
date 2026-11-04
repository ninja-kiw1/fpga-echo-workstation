"""RV / RA programs: shared cache access and native-resolution metrics (CardiacUDA cache from prep_cardiacuda.py)."""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
CACHE = HERE / 'data' / 'cache'
RESULTS = HERE / 'results'
sys.path.insert(0, str(PLD / 'echo'))
sys.path.insert(0, str(PLD / 'lwnet'))
TARGETS = {'rv': 4, 'ra': 3, 'lv': 1, 'la': 2}


def load(split, target):
    """-> x512 [N, 512, 512] uint8, y512 [N, 512, 512] bool (target), gts list of native crops (bool), meta"""
    x = np.load(CACHE / f'{split}_img512.npy', mmap_mode='r')
    lab = np.load(CACHE / f'{split}_lab512.npy', mmap_mode='r')
    v = TARGETS[target]
    with np.load(CACHE / f'{split}_gt.npz') as z:
        gts = [z[f'g{i}'] == v for i in range(len(z.files))]
    meta = json.loads((CACHE / f'{split}_meta.json').read_text(encoding='utf-8'))
    return x, np.asarray(lab) == v, gts, meta


def to_native(mask128, shape):
    """board tier 128 mask -> repeat 4 x 4 to 512 -> PIL NEAREST to the native crop (h, w)"""
    m512 = np.repeat(np.repeat(np.asarray(mask128, np.uint8), 4, 0), 4, 1)
    return np.array(Image.fromarray(m512 * 255).resize((shape[1], shape[0]), Image.NEAREST)) > 0


def dice(a, b):
    s = int(a.sum()) + int(b.sum())
    return 1.0 if s == 0 else 2.0 * int((a & b).sum()) / s


def grouped(per, meta):
    d = np.array(per)
    out = dict(all=float(d.mean()))
    for site in ('G', 'R'):
        k = np.array([m['site'] == site for m in meta])
        out[f'site_{site}'] = float(d[k].mean())
    out['p5'] = float(np.percentile(d, 5))
    return out
