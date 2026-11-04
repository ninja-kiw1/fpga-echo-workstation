"""PAPILA (GPL-3.0+, figshare 10.6084/m9.figshare.14798004 v2) -> external-test cache. Used ONCE by final_test.py.

488 images (244 patients, both eyes), Topcon TRC-NW400 2576 x 1934, 30 deg disc-centred; two experts drew disc and
cup contours as point lists (x y per line, pixel units). Masks = filled polygons (Pillow ImageDraw, outline included),
as the dataset's HelpCode/utils.contour_to_mask does with skimage.draw.polygon. The 11 '* 2.txt' duplicates are ignored.
This script only builds the cache; it computes no metric.

    python -I prep_papila.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np                       # noqa: E402
from PIL import Image, ImageDraw         # noqa: E402

from common import DATA, gt_cells, gt_clean, host_stretch, save_cache, vcdr_of   # noqa: E402

SRC = next((DATA / 'papila').glob('PapilaDB-PAPILA-*'))


def poly_mask(path, w, h):
    pts = np.loadtxt(path)
    im = Image.new('L', (w, h), 0)
    ImageDraw.Draw(im).polygon([(float(x), float(y)) for x, y in pts], fill=1, outline=1)
    return gt_clean(np.asarray(im, bool))


def main():
    imgs = sorted((SRC / 'FundusImages').glob('RET*.jpg'))
    con = SRC / 'ExpertsSegmentations' / 'Contours'
    n = len(imgs)
    arr = dict(img512=np.zeros((n, 512, 512, 3), np.uint8))
    for e in (1, 2):
        for t in ('disc', 'cup'):
            arr[f'{t}cells_e{e}'] = np.zeros((n, 512, 512), np.uint8)
    rows, cols, meta = np.zeros((n, 512), np.int32), np.zeros((n, 512), np.int32), []
    for i, p in enumerate(imgs):
        im = Image.open(p).convert('RGB')
        w, h = im.size
        arr['img512'][i] = host_stretch(im)
        m = dict(name=p.stem, source='PAPILA', device='Topcon TRC-NW400', w=w, h=h)
        for e in (1, 2):
            d = poly_mask(con / f'{p.stem}_disc_exp{e}.txt', w, h)
            c = poly_mask(con / f'{p.stem}_cup_exp{e}.txt', w, h)
            arr[f'disccells_e{e}'][i], rows[i], cols[i] = gt_cells(d, w, h)
            arr[f'cupcells_e{e}'][i] = gt_cells(c, w, h)[0]
            m[f'vcdr_e{e}'] = vcdr_of(d, c)
            m[f'cup_outside_disc_px_e{e}'] = int((c & ~d).sum())
        meta.append(m)
        if i % 50 == 0:
            print(i, n, flush=True)
    arr.update(rows=rows, cols=cols)
    save_cache('papila', arr, meta)
    print('papila cache:', n, 'images')


if __name__ == '__main__':
    main()
