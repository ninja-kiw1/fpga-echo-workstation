"""RV / RA programs, step 1: fixed split + training cache from CardiacUDA (Kaggle v1, A4C, filled masks).

Split (written to results/split.json BEFORE any training; folder-level because the per-folder file numbers are
independent - same-name files in two folders are no more alike than random pairs, same_patient.py):
  train  Site_G_100 (97) + Site_R_126 (85 minus patient-73: label gzip truncated in the official package,
         minus patient-90: identical video to patient-89)                                          -> 180 videos
  val    Site_G_29 (29) + Site_R_52 at odd positions of the sorted names (26)                      ->  55 videos
  test   Site_G_20 (21) + Site_R_52 at even positions (26)                                         ->  47 videos
         LOCKED: used once at the end (final_test.py writes the lock first)
  unused label_all_frame (contour outlines, codes 5 / 6 in two videos; 8 of 10 are Site_R_126 recordings = train),
         Site_R_73 (no labels)
Labels (official GraphEcho code, checked on every labelled frame by check_labels.py): 1 LV, 2 LA, 3 RA, 4 RV.
Only the 5 labelled frames of each video are used (frames with > 100 labelled pixels).

Chain (same as CAMUS F1 / P1 1.6 so the board / UI path is identical):
  frame -> sector crop (largest component of (temporal max > 20) after a 7 x 7 opening, which drops the ECG trace
  and text, grown back inside (temporal max > 4) below its top row to recover the narrow, dark apex tip;
  bounding box)
  -> rotate 180 deg (CardiacUDA is stored apex-down, LV on the left; CAMUS / EchoNet
  are apex-up, LV right) -> grey -> RGB -> Pillow LANCZOS to 512 x 512 (= echo.common.host_stretch) -> board tier
  128 = x[::4, ::4]. Labels: same crop / rotation, NEAREST to 512 for training; the native-resolution crop is kept
  for evaluation (prediction 128 -> repeat 4 x 4 -> PIL NEAREST back to the crop size).

Writes data/cache/{split}_img512.npy (uint8), {split}_lab512.npy (uint8 0-4), {split}_gt.npz (native crops),
{split}_meta.json.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I prep_cardiacuda.py
"""
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'echo'))
from nifti import read_nifti  # noqa: E402

ROOT = HERE.parent / 'CardiacUDA' / 'data' / 'cardiacUDC_dataset'
OUT = HERE / 'results'
CACHE = HERE / 'data' / 'cache'
EXCLUDE = {('Site_R_126', 'patient-73-4'): 'label .nii.gz truncated in the official package (27,824 B, CRC of the truncated file)',
           ('Site_R_126', 'patient-90-4'): 'identical video to patient-89-4 (dedup.py score 1.0000)'}


def make_split():
    def stems(folder):
        return sorted(p.name[:-len('_image.nii.gz')] for p in (ROOT / folder).glob('*_image.nii.gz'))
    sp = dict(train=[], val=[], test=[])
    for f in ('Site_G_100', 'Site_R_126'):
        sp['train'] += [(f, s) for s in stems(f) if (f, s) not in EXCLUDE]
    sp['val'] += [('Site_G_29', s) for s in stems('Site_G_29')]
    sp['test'] += [('Site_G_20', s) for s in stems('Site_G_20')]
    r52 = stems('Site_R_52')
    sp['val'] += [('Site_R_52', s) for s in r52[1::2]]
    sp['test'] += [('Site_R_52', s) for s in r52[0::2]]
    return sp


def sector_box(video):
    """bounding box (r0, r1, c0, c1) of the moving / bright sector"""
    mx = video.max(0)
    raw = mx > 4            # background is exactly 0; dark cavities near the tip stay in single digits
    m = ndimage.binary_opening(mx > 20, structure=np.ones((7, 7)))
    lab, n = ndimage.label(m)
    k = int(np.argmax(ndimage.sum(m, lab, range(1, n + 1)))) + 1
    seed = lab == k
    # the opening also cuts the narrow sector tip (apex, at the bottom in CardiacUDA): add back the raw mask below
    # the component's top row and within its columns (ECG trace / text above the sector stay out); pieces cut off
    # by a black (= 0) dropout band count too, if >= 200 px (text markers / specks stay out)
    rs, cs = np.nonzero(seed)
    allowed = raw.copy()
    allowed[:rs.min()] = False
    allowed[:, :cs.min()] = False
    allowed[:, cs.max() + 1:] = False
    lab2, n2 = ndimage.label(allowed, structure=np.ones((3, 3)))
    big = np.nonzero(ndimage.sum(allowed, lab2, range(1, n2 + 1)) >= 200)[0] + 1
    sector = seed | np.isin(lab2, big)
    r, c = np.nonzero(sector)
    return int(r.min()), int(r.max()) + 1, int(c.min()), int(c.max()) + 1


def stretch(gray):
    im = Image.fromarray(gray).convert('RGB').resize((512, 512), Image.LANCZOS)
    return np.array(im)[:, :, 0]


def prep_video(job):
    split, folder, stem = job
    img, sp_img = read_nifti(ROOT / folder / f'{stem}_image.nii.gz')
    lab, _ = read_nifti(ROOT / folder / f'{stem}_label.nii.gz')
    assert img.shape == lab.shape, (folder, stem, img.shape, lab.shape)
    img = np.clip(np.asarray(img), 0, 255).astype(np.uint8)
    lab = np.asarray(lab).astype(np.uint8)
    r0, r1, c0, c1 = sector_box(img)
    frames = [t for t in range(len(lab)) if (lab[t] > 0).sum() > 100]
    xs, ys, gts, meta = [], [], [], []
    for t in frames:
        g = img[t, r0:r1, c0:c1][::-1, ::-1]
        y = lab[t, r0:r1, c0:c1][::-1, ::-1]
        assert y.max() <= 4, (folder, stem, t, y.max())
        xs.append(stretch(np.ascontiguousarray(g)))
        ys.append(np.array(Image.fromarray(np.ascontiguousarray(y)).resize((512, 512), Image.NEAREST)))
        gts.append(np.ascontiguousarray(y))
        meta.append(dict(split=split, folder=folder, video=stem, site=folder[5], frame=t, nframes=len(lab),
                         box=[r0, r1, c0, c1], crop=list(y.shape),
                         lost_px={k: int((lab[t] == v).sum() - (y == v).sum()) for k, v in (('rv', 4), ('ra', 3))},
                         area_px={k: int((y == v).sum()) for k, v in (('lv', 1), ('la', 2), ('ra', 3), ('rv', 4))}))
    return xs, ys, gts, meta


def main():
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    sf = OUT / 'split.json'
    sp = make_split()
    if sf.exists():
        old = json.loads(sf.read_text(encoding='utf-8'))
        assert {k: [list(x) for x in v] for k, v in sp.items()} == old['split'], 'split changed - refusing'
    else:
        sf.write_text(json.dumps(dict(rule=__doc__.split('Chain')[0], excluded={f'{a}/{b}': v for (a, b), v in EXCLUDE.items()},
                                      split=sp), indent=1), encoding='utf-8')
    for split, vids in sp.items():
        with Pool(12) as pool:
            res = pool.map(prep_video, [(split, f, s) for f, s in vids], chunksize=2)
        xs = [x for r in res for x in r[0]]
        ys = [y for r in res for y in r[1]]
        gts = [g for r in res for g in r[2]]
        meta = [m for r in res for m in r[3]]
        np.save(CACHE / f'{split}_img512.npy', np.stack(xs))
        np.save(CACHE / f'{split}_lab512.npy', np.stack(ys))
        np.savez_compressed(CACHE / f'{split}_gt.npz', **{f'g{i}': g for i, g in enumerate(gts)})
        (CACHE / f'{split}_meta.json').write_text(json.dumps(meta), encoding='utf-8')
        lost = sum(m['lost_px']['rv'] + m['lost_px']['ra'] for m in meta)
        print(f"{split}: {len(vids)} videos, {len(meta)} frames (site G {sum(m['site'] == 'G' for m in meta)}, "
              f"R {sum(m['site'] == 'R' for m in meta)}), RV/RA pixels outside the crop {lost} "
              f"({time.time() - t0:.0f} s)", flush=True)


if __name__ == '__main__':
    main()
