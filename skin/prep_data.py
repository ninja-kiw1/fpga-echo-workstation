"""ISIC 2016 Task 1 -> 512x512 RGB caches that match what the board receives.

The host UI (tcp_image_ui/imageio.py `_to_target`) stretches any image to
512x512 with Pillow LANCZOS; the board then subsamples by nearest neighbour
(`ai_view_downsample_rgb`, i.e. x[::k, ::k]) for the 256/128 resolution tiers.
So the cache stores exactly the 512x512 uint8 the board would get.

Masks are cached at 512x512 (bilinear then >= 128) only for training.
Final test evaluation uses the ORIGINAL-resolution ground truth, read from
data/raw/ by evaluate code, never from this cache.

Writes data/cache/{train,test}_{img512,mask512}.npy and {train,test}_meta.json.
"""
import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
ZIPS = ROOT / 'data' / 'zips'
RAW = ROOT / 'data' / 'raw'
CACHE = ROOT / 'data' / 'cache'
SETS = {
    'train': ('ISBI2016_ISIC_Part1_Training_Data.zip', 'ISBI2016_ISIC_Part1_Training_GroundTruth.zip', 900),
    'test': ('ISBI2016_ISIC_Part1_Test_Data.zip', 'ISBI2016_ISIC_Part1_Test_GroundTruth.zip', 379),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def stretch_like_host(path):
    with Image.open(path) as im:
        rgb = im if im.mode == 'RGB' else im.convert('RGB')
        size = rgb.size
        if size == (512, 512):
            return np.array(rgb), size
        return np.array(rgb.resize((512, 512), Image.LANCZOS)), size


def nearest_map(n_src, n_dst):
    """Index into the 512 grid for each original pixel, as PIL NEAREST computes it."""
    idx = Image.fromarray(np.arange(n_src, dtype=np.int32)[None, :], mode='I')
    return np.array(idx.resize((n_dst, 1), Image.NEAREST))[0].astype(np.int64)


def gt_cells(gt, size):
    """Per 512-cell GT pixel counts and per-row/column original pixel counts.

    A 512x512 mask upsampled to the original size with PIL NEAREST gives
    tp = sum(pred512 * cells), predicted area = sum(pred512 * outer(rows, cols)),
    so ISIC metrics at the original resolution need only these three arrays.
    """
    w, h = size
    cx, cy = nearest_map(512, w), nearest_map(512, h)
    flat = (cy[:, None] * 512 + cx[None, :])[gt]
    cells = np.bincount(flat, minlength=512 * 512).reshape(512, 512)
    return cells.astype(np.uint16), np.bincount(cy, minlength=512), np.bincount(cx, minlength=512)


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    zip_hashes = {}
    for split, (zimg, zgt, count) in SETS.items():
        for z in (zimg, zgt):
            zip_hashes[z] = sha256(ZIPS / z)
            dest = RAW / Path(z).stem
            if not dest.is_dir():
                with zipfile.ZipFile(ZIPS / z) as f:
                    f.extractall(dest)
        images = sorted((RAW / Path(zimg).stem).rglob('ISIC_*.jpg'))
        masks = {p.name.replace('_Segmentation.png', ''): p
                 for p in (RAW / Path(zgt).stem).rglob('ISIC_*_Segmentation.png')}
        assert len(images) == count, (split, len(images))
        assert len(masks) == count and all(p.stem in masks for p in images), split
        x = np.zeros((count, 512, 512, 3), np.uint8)
        y = np.zeros((count, 512, 512), np.uint8)
        cells = np.zeros((count, 512, 512), np.uint16)
        rows = np.zeros((count, 512), np.int64)
        cols = np.zeros((count, 512), np.int64)
        meta = []
        for i, p in enumerate(images):
            x[i], size = stretch_like_host(p)
            with Image.open(masks[p.stem]) as m:
                m = m.convert('L')
                assert m.size == size, p.stem
                gt = np.array(m)
                assert set(np.unique(gt)) <= {0, 255}, p.stem
                y[i] = np.array(m.resize((512, 512), Image.BILINEAR)) >= 128
            cells[i], rows[i], cols[i] = gt_cells(gt > 127, size)
            if i < 3:   # the cell shortcut must equal a real PIL NEAREST upsample
                pred = np.random.default_rng(i).random((512, 512)) < 0.5
                up = np.array(Image.fromarray(pred).resize(size, Image.NEAREST))
                assert int((up & (gt > 127)).sum()) == int((pred * cells[i]).sum())
                assert int(up.sum()) == int((pred * np.outer(rows[i], cols[i])).sum())
            meta.append(dict(name=p.stem, width=size[0], height=size[1],
                             image=str(p.relative_to(ROOT)), mask=str(masks[p.stem].relative_to(ROOT)),
                             lesion_fraction=float((gt > 127).mean())))
            if i % 100 == 0:
                print(split, i, p.stem, size, flush=True)
        np.save(CACHE / f'{split}_img512.npy', x)
        np.save(CACHE / f'{split}_mask512.npy', y)
        np.save(CACHE / f'{split}_gtcells.npy', cells)
        np.save(CACHE / f'{split}_rows.npy', rows)
        np.save(CACHE / f'{split}_cols.npy', cols)
        (CACHE / f'{split}_meta.json').write_text(json.dumps(meta, indent=1), encoding='utf-8')
        sizes = sorted({(m['width'], m['height']) for m in meta})
        frac = [m['lesion_fraction'] for m in meta]
        print(f'{split}: {count} images, {len(sizes)} distinct sizes, '
              f'min {min(sizes)} max {max(sizes)}, lesion fraction mean {np.mean(frac):.3f}', flush=True)
    (CACHE / 'zip_sha256.json').write_text(json.dumps(zip_hashes, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
