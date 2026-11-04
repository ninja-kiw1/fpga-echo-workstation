"""Part D shared helpers: paths, the host input chain, original-resolution metrics, caches.

Input chain (same as the vessel / skin models): photograph -> RGB -> Pillow LANCZOS to 512 x 512 (anisotropic
stretch) -> cdr_ref payloads (box means, numpy == C). Ground truth stays at the ORIGINAL resolution:
Dice is computed exactly there by mapping every original pixel to its 512 cell with Pillow NEAREST
(echo/common.py method), and the reference vCDR is measured on the original expert masks.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
DATA = ROOT / 'data'
CACHE = DATA / 'cache'
RESULTS = ROOT / 'results'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PLD / 'lwnet'))          # read-only reuse: models/, int8_reference.py

SPLIT_SEED = 20261017
VAL_FRACTION = 0.2


def host_stretch(rgb):
    """The UI path: RGB -> Pillow LANCZOS to 512 x 512 (unless already 512 x 512)."""
    im = Image.fromarray(rgb).convert('RGB') if isinstance(rgb, np.ndarray) else rgb.convert('RGB')
    if im.size != (512, 512):
        im = im.resize((512, 512), Image.LANCZOS)
    return np.asarray(im, np.uint8)


def nearest_map(n_src, n_dst):
    """Index into the 512 grid for each of n_dst original pixels, as PIL NEAREST computes it."""
    idx = Image.fromarray(np.arange(n_src, dtype=np.int32)[None, :], mode='I')
    return np.array(idx.resize((n_dst, 1), Image.NEAREST))[0].astype(np.int64)


def gt_cells(gt, w, h):
    """Per-512-cell counts of GT pixels (original resolution) + original pixels per 512 row / column."""
    cx, cy = nearest_map(512, w), nearest_map(512, h)
    flat = (cy[:, None] * 512 + cx[None, :])[gt]
    cells = np.bincount(flat, minlength=512 * 512).reshape(512, 512)
    assert cells.max() <= 255
    return cells.astype(np.uint8), np.bincount(cy, minlength=512), np.bincount(cx, minlength=512)


def seg_metrics(pred512, cells, rows, cols):
    """Dice of a 512 mask NEAREST-upsampled to the original size, exact via cell counts."""
    p = np.asarray(pred512, np.int64)
    c = np.asarray(cells, np.int64)
    tp = int((p * c).sum())
    pred_area = int(rows @ p @ cols)
    gt_area = int(c.sum())
    fp, fn = pred_area - tp, gt_area - tp
    return dict(dice=2 * tp / max(2 * tp + fp + fn, 1), pred_area_px=pred_area, gt_area_px=gt_area)


def train_size_mask(gt_orig):
    """Original-resolution bool mask -> 512 x 512 training target (Pillow BOX area mean >= 1/2)."""
    im = Image.fromarray(np.asarray(gt_orig, np.uint8) * 255).resize((512, 512), Image.BOX)
    return np.asarray(im) >= 128


def gt_clean(m):
    """Reference mask -> its largest 8-connected component (some Chaksu majority masks carry 1-6 px specks far
    from the disc, which would inflate the row span; the dataset's own CSV VCDR ignores them too)."""
    from scipy import ndimage
    m = np.asarray(m, bool)
    lab, n = ndimage.label(m, structure=np.ones((3, 3), bool))
    if n <= 1:
        return m
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == int(np.argmax(sizes))


def rows_span(m):
    r = np.nonzero(np.asarray(m).any(1))[0]
    return 0 if len(r) == 0 else int(r[-1] - r[0] + 1)


def vcdr_of(disc, cup):
    d = rows_span(disc)
    return rows_span(cup) / d if d else float('nan')


def load_cache(name):
    """name e.g. 'chaksu_train' -> dict of arrays (memory-mapped) + meta list."""
    d = {}
    for p in sorted(CACHE.glob(f'{name}_*.npy')):
        d[p.stem[len(name) + 1:]] = np.load(p, mmap_mode='r')
    d['meta'] = json.loads((CACHE / f'{name}_meta.json').read_text(encoding='utf-8'))
    return d


def save_cache(name, arrays, meta):
    CACHE.mkdir(parents=True, exist_ok=True)
    for k, v in arrays.items():
        np.save(CACHE / f'{name}_{k}.npy', v)
    (CACHE / f'{name}_meta.json').write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding='utf-8')
