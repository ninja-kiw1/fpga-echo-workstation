"""Shared helpers: paths, cached data, the board's resampling, ISIC metrics."""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
LWNET = PLD / 'lwnet'
CACHE = ROOT / 'data' / 'cache'
RESULTS = ROOT / 'results'
sys.path.insert(0, str(LWNET))          # read-only reuse: models/, int8_reference.py

SPLIT_SEED = 20261007
N_VAL = 180


def load_split(split):
    """uint8 images (N,512,512,3), training masks (N,512,512), metadata list."""
    x = np.load(CACHE / f'{split}_img512.npy', mmap_mode='r')
    y = np.load(CACHE / f'{split}_mask512.npy', mmap_mode='r')
    meta = json.loads((CACHE / f'{split}_meta.json').read_text(encoding='utf-8'))
    return x, y, meta


def load_cells(split):
    return (np.load(CACHE / f'{split}_gtcells.npy', mmap_mode='r'),
            np.load(CACHE / f'{split}_rows.npy'), np.load(CACHE / f'{split}_cols.npy'))


def train_val_indices():
    """Fixed split of the 900 official training images; test images never enter it."""
    perm = np.random.default_rng(SPLIT_SEED).permutation(900)
    return np.sort(perm[N_VAL:]), np.sort(perm[:N_VAL])


def board_downsample(x512, shift):
    """ai_view_downsample_rgb: sy = y*512/dh, i.e. plain stride subsampling."""
    k = 1 << shift
    return x512[..., ::k, ::k, :] if x512.shape[-1] == 3 else x512[..., ::k, ::k]


def board_upsample(mask_r, shift):
    """ai_view_mask_rgb: ly = y*lh/oh, i.e. each value repeated k x k."""
    k = 1 << shift
    return np.repeat(np.repeat(mask_r, k, axis=-2), k, axis=-1)


def isic_metrics(pred512, cells, rows, cols):
    """ISIC 2016 Task 1 metrics at the ORIGINAL resolution.

    pred512 is the board's 512x512 mask; it is upsampled to the original image
    size with PIL NEAREST (exactness asserted in prep_data.py), never resampled
    in any smoother way.
    """
    p = pred512.astype(np.int64)
    c = np.asarray(cells, dtype=np.int64)
    tp = int((p * c).sum())
    pred_area = int(rows @ p @ cols)
    gt_area = int(c.sum())
    total = int(rows.sum()) * int(cols.sum())
    fp, fn = pred_area - tp, gt_area - tp
    tn = total - tp - fp - fn
    return dict(jaccard=tp / max(tp + fp + fn, 1), dice=2 * tp / max(2 * tp + fp + fn, 1),
                accuracy=(tp + tn) / total, sensitivity=tp / max(tp + fn, 1),
                specificity=tn / max(tn + fp, 1))


def mean_metrics(rows):
    keys = ('jaccard', 'dice', 'accuracy', 'sensitivity', 'specificity')
    out = {k: float(np.mean([r[k] for r in rows])) for k in keys}
    # ISIC 2017 style: per-image Jaccard below 0.65 counts as 0
    out['thresholded_jaccard'] = float(np.mean([r['jaccard'] if r['jaccard'] >= 0.65 else 0.0 for r in rows]))
    return out
