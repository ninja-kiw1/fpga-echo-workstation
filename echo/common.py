"""Shared helpers for the CAMUS echo work: paths, caches, board resampling, metrics.

Mirrors skin/common.py (same deployment chain), but the images are grey:
the host sends grey replicated to R=G=B, so caches keep one channel.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
CAMUS = ROOT / 'data' / 'camus'
CACHE = ROOT / 'data' / 'cache'
RESULTS = ROOT / 'results'
sys.path.insert(0, str(PLD / 'lwnet'))          # read-only reuse: models/, int8_reference.py

LABELS = {'lv': 1, 'myo': 2, 'la': 3}           # CAMUS ground-truth values


def host_stretch(gray):
    """The UI path: grey -> RGB -> Pillow LANCZOS to 512x512 (unless already 512x512)."""
    im = Image.fromarray(gray).convert('RGB')
    if im.size != (512, 512):
        im = im.resize((512, 512), Image.LANCZOS)
    return np.array(im)[:, :, 0]


def nearest_map(n_src, n_dst):
    """Index into the 512 grid for each original pixel, as PIL NEAREST computes it."""
    idx = Image.fromarray(np.arange(n_src, dtype=np.int32)[None, :], mode='I')
    return np.array(idx.resize((n_dst, 1), Image.NEAREST))[0].astype(np.int64)


def gt_cells(gt, w, h):
    """Per-512-cell GT counts + per-row/col original pixel counts (see skin/prep_data.py)."""
    cx, cy = nearest_map(512, w), nearest_map(512, h)
    flat = (cy[:, None] * 512 + cx[None, :])[gt]
    cells = np.bincount(flat, minlength=512 * 512).reshape(512, 512)
    return cells.astype(np.uint16), np.bincount(cy, minlength=512), np.bincount(cx, minlength=512)


def board_upsample(mask_r, shift):
    k = 1 << shift
    return np.repeat(np.repeat(mask_r, k, axis=-2), k, axis=-1)


def seg_metrics(pred512, cells, rows, cols):
    """Dice / Jaccard of the board's 512 mask, NEAREST-upsampled to the original size."""
    p = pred512.astype(np.int64)
    c = np.asarray(cells, dtype=np.int64)
    tp = int((p * c).sum())
    pred_area = int(rows @ p @ cols)
    gt_area = int(c.sum())
    fp, fn = pred_area - tp, gt_area - tp
    return dict(dice=2 * tp / max(2 * tp + fp + fn, 1), jaccard=tp / max(tp + fp + fn, 1),
                pred_area_px=pred_area, gt_area_px=gt_area)


def load_cache(split, target='lv'):
    x = np.load(CACHE / f'{split}_img512.npy', mmap_mode='r')
    y = np.load(CACHE / f'{split}_{target}512.npy', mmap_mode='r')
    cells = np.load(CACHE / f'{split}_{target}cells.npy', mmap_mode='r')
    rows = np.load(CACHE / f'{split}_rows.npy')
    cols = np.load(CACHE / f'{split}_cols.npy')
    meta = json.loads((CACHE / f'{split}_meta.json').read_text(encoding='utf-8'))
    return x, y, cells, rows, cols, meta


def grouped(per, meta, key='dice'):
    """Mean of `key` overall and by quality / phase / view."""
    out = {'all': float(np.mean([r[key] for r in per])), 'n': len(per)}
    for field in ('quality', 'phase', 'view'):
        for v in sorted({m[field] for m in meta}):
            vals = [r[key] for r, m in zip(per, meta) if m[field] == v]
            out[f'{field}={v}'] = float(np.mean(vals))
            out[f'n_{field}={v}'] = len(vals)
    gm = [r[key] for r, m in zip(per, meta) if m['quality'] in ('Good', 'Medium')]
    out['good+medium'] = float(np.mean(gm))
    return out
