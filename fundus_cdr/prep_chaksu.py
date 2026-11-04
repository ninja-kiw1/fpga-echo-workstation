"""Chakshu IMAGE (CC BY 4.0, figshare 10.6084/m9.figshare.20123135 v2) -> caches, read straight from the zips.

Official split: Train.zip (1009 images) -> our train / VAL (20 % per device, seed 20261017; the dataset gives no
patient ids, so both eyes of one person may fall on different sides); Test.zip (336) -> chaksu_test, read ONLY by
final_test.py (this script builds it with --test and computes no metric on it).
Reference masks: 5.0_OD_OC_Mean_Median_Majority_STAPLE/<device>/{Disc,Cup}/Majority (majority vote of 5 experts),
original resolution, 0 / 255, largest 8-connected component kept (common.gt_clean). Reference vCDR = rows spanned by the cup / by the disc on those masks; cross-checked
against the dataset's own 6.0_Glaucoma_Decision/Majority/<device>.csv VCDR column (reported, not used).
VAL / test also get each expert's vCDR (3.0_Doctors_Annotations_Binary_OD_OC/Expert k) for the inter-observer context.
Arrays: img512 (host LANCZOS stretch), disc512 / cup512 (training targets, Pillow BOX >= 1/2),
disccells / cupcells / rows / cols (exact original-resolution Dice), meta.

    python -I prep_chaksu.py            # train + VAL
    python -I prep_chaksu.py --test     # official test (only when final_test.py is about to run)
"""
import argparse
import csv
import io
import sys
import zipfile
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np                       # noqa: E402
from PIL import Image                    # noqa: E402

from common import DATA, SPLIT_SEED, VAL_FRACTION, gt_cells, gt_clean, host_stretch, rows_span, save_cache, train_size_mask  # noqa: E402

DEVICES = ('Bosch', 'Forus', 'Remidio')
G = '5.0_OD_OC_Mean_Median_Majority_STAPLE'
E = '3.0_Doctors_Annotations_Binary_OD_OC'


def listing(zpath, top):
    z = zipfile.ZipFile(zpath)
    names = [n for n in z.namelist() if not n.startswith('__MACOSX') and not n.endswith('/')]
    low = {n.lower(): n for n in names}
    items = []
    for dev in DEVICES:
        pre = f'{top}/1.0_Original_Fundus_Images/{dev}/'
        for n in sorted(x for x in names if x.startswith(pre) and x.lower().endswith(('.jpg', '.png'))):
            stem = n[len(pre):].rsplit('.', 1)[0]
            m = {t: low.get(f'{top}/{G}/{dev}/{t}/Majority/{stem}.tif'.lower()) for t in ('Disc', 'Cup')}
            ex = {}
            for k in range(1, 6):
                for t in ('Disc', 'Cup'):
                    ex[f'e{k}_{t}'] = low.get(f'{top}/{E}/Expert {k}/{dev}/{t}/{stem}.tif'.lower())
            items.append(dict(img=n, device=dev, stem=stem, disc=m['Disc'], cup=m['Cup'], experts=ex))
    csvv = {}
    for dev in DEVICES:
        key = f'{top}/6.0_Glaucoma_Decision/Majority/{dev}.csv'.lower()
        if key in low:
            for row in csv.DictReader(io.StringIO(z.read(low[key]).decode('utf-8', 'replace'))):
                csvv[(dev, row['Images'].split('.')[0].lower())] = float(row['VCDR'])
    return items, csvv


def read_mask(z, name):
    a = np.asarray(Image.open(io.BytesIO(z.read(name))))
    if a.ndim == 3:
        a = a[..., 0]
    return gt_clean(a > 127)


def work(args):
    zpath, it, with_experts = args
    z = zipfile.ZipFile(zpath)
    im = Image.open(io.BytesIO(z.read(it['img']))).convert('RGB')
    w, h = im.size
    d, c = read_mask(z, it['disc']), read_mask(z, it['cup'])
    assert d.shape == (h, w) and c.shape == (h, w), (it['img'], d.shape, (h, w))
    dc, rows, cols = gt_cells(d, w, h)
    cc = gt_cells(c, w, h)[0]
    dh = rows_span(d)
    meta = dict(name=it['stem'], device=it['device'], w=w, h=h, vcdr=rows_span(c) / dh if dh else float('nan'),
                disc_h=dh, cup_h=rows_span(c), cup_outside_disc_px=int((c & ~d).sum()))
    if with_experts:
        for k in range(1, 6):
            nd, nc = it['experts'][f'e{k}_Disc'], it['experts'][f'e{k}_Cup']
            if nd and nc:
                ed, ec = read_mask(z, nd), read_mask(z, nc)
                if ed.shape == (h, w) and ec.shape == (h, w) and rows_span(ed):
                    meta[f'vcdr_e{k}'] = rows_span(ec) / rows_span(ed)
    return (host_stretch(im), train_size_mask(d), train_size_mask(c), dc, cc, rows, cols, meta)


def build(name, zpath, items, csvv, with_experts):
    with Pool(8) as pool:
        res = pool.map(work, [(str(zpath), it, with_experts) for it in items], chunksize=4)
    keys = ('img512', 'disc512', 'cup512', 'disccells', 'cupcells', 'rows', 'cols')
    arrays = {k: np.stack([r[j] for r in res]) for j, k in enumerate(keys)}
    meta = [r[-1] for r in res]
    for m in meta:
        m['vcdr_csv'] = csvv.get((m['device'], m['name'].lower()))
    save_cache(name, arrays, meta)
    diffs = [abs(m['vcdr'] - m['vcdr_csv']) for m in meta if m['vcdr_csv'] is not None]
    print(f'{name}: {len(meta)} images; vCDR vs dataset CSV: n {len(diffs)}, max |diff| {max(diffs) if diffs else None}',
          flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--test', action='store_true')
    a = ap.parse_args()
    dl = DATA / 'dl_chaksu'
    if a.test:
        items, csvv = listing(dl / 'Test.zip', 'Test')
        missing = [it['img'] for it in items if not (it['disc'] and it['cup'])]
        print('test images', len(items), 'without majority masks', missing)
        build('chaksu_test', dl / 'Test.zip', [it for it in items if it['disc'] and it['cup']], csvv, True)
        return
    items, csvv = listing(dl / 'Train.zip', 'Train')
    missing = [it['img'] for it in items if not (it['disc'] and it['cup'])]
    print('train images', len(items), 'without majority masks', missing)
    items = [it for it in items if it['disc'] and it['cup']]
    rng = np.random.default_rng(SPLIT_SEED)
    val = set()
    for dev in DEVICES:
        idx = [i for i, it in enumerate(items) if it['device'] == dev]
        val.update(rng.choice(idx, int(round(VAL_FRACTION * len(idx))), replace=False).tolist())
    build('chaksu_val', dl / 'Train.zip', [it for i, it in enumerate(items) if i in val], csvv, True)
    build('chaksu_train', dl / 'Train.zip', [it for i, it in enumerate(items) if i not in val], csvv, False)


if __name__ == '__main__':
    main()
