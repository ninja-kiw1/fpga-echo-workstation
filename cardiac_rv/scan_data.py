"""CardiacUDA (Kaggle v1, A4C only) inventory: every .nii.gz -> shape, spacing, labelled frames, label values, hash.

Questions answered (results/scan.json + printed summary):
  * which folder is train / val / test (official 8:1:1; train = 5 labelled frames per video, val / test = every frame);
  * are names reused across folders (same patient in two sites or two splits?) - by content hash and by name;
  * image / label shape agreement, frame counts, label values (A4C: 0 bg, 1 LV, 2 LA, 3 RA, 4 RV).

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I scan_data.py
"""
import hashlib
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'echo'))
from nifti import read_nifti  # noqa: E402

ROOT = HERE.parent / 'CardiacUDA' / 'data' / 'cardiacUDC_dataset'
OUT = HERE / 'results'


def scan(path):
    raw = path.read_bytes()
    r = dict(folder=path.parent.name, name=path.name, bytes=len(raw), sha1=hashlib.sha1(raw).hexdigest())
    try:
        a, sp = read_nifti(path)
    except Exception as e:      # e.g. a gzip stream that ends early
        r.update(error=f'{type(e).__name__}: {e}')
        return r
    r.update(shape=list(a.shape), dtype=str(a.dtype), spacing=[float(x) for x in sp])
    if path.name.endswith('_label.nii.gz'):
        a = np.asarray(a)
        # read_nifti gives [z, y, x] = [frame, row, col] for a 3D file
        per = [np.bincount(f.astype(np.int64).ravel(), minlength=5)[:8].tolist() for f in a] if a.ndim == 3 else []
        lab = [i for i, c in enumerate(per) if sum(c[1:]) > 100]
        r.update(labelled_frames=lab, n_labelled=len(lab), values=sorted(int(v) for v in np.unique(a)),
                 classes_per_labelled=[[k for k in range(1, 5) if per[i][k] > 0] for i in lab],
                 px_per_class_median=[float(np.median([per[i][k] for i in lab])) if lab else 0 for k in range(1, 5)])
    else:
        r.update(min=float(np.min(a)), max=float(np.max(a)))
    return r


def main():
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    files = sorted(ROOT.rglob('*.nii.gz'))
    with Pool(12) as pool:
        rows = pool.map(scan, files, chunksize=4)
    (OUT / 'scan.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')
    bad = [r for r in rows if 'error' in r]
    print('unreadable files:', len(bad))
    for r in bad:
        print('  ', r['folder'], r['name'], r['bytes'], r['error'])
    by_folder = {}
    for r in rows:
        if 'error' in r:
            continue
        f = by_folder.setdefault(r['folder'], dict(images=0, labels=0, nlab=[], frames=[], shapes=set(), values=set()))
        if r['name'].endswith('_label.nii.gz'):
            f['labels'] += 1
            f['nlab'].append(r['n_labelled'])
            f['values'] |= set(r['values'])
        else:
            f['images'] += 1
            f['frames'].append(r['shape'][0])
            f['shapes'].add(tuple(r['shape'][1:]))
    for k, f in sorted(by_folder.items()):
        print(f"{k:16s} images {f['images']:3d} labels {f['labels']:3d} labelled frames/video "
              f"{(min(f['nlab']), int(np.median(f['nlab'])), max(f['nlab'])) if f['nlab'] else '-'} "
              f"frames {(min(f['frames']), int(np.median(f['frames'])), max(f['frames'])) if f['frames'] else '-'} "
              f"sizes {sorted(f['shapes'])[:4]}{'...' if len(f['shapes']) > 4 else ''} values {sorted(f['values'])}")
    sha = {}
    for r in rows:
        sha.setdefault(r['sha1'], []).append(f"{r['folder']}/{r['name']}")
    dup = [v for v in sha.values() if len(v) > 1]
    print('identical files in >1 place:', len(dup))
    for v in dup[:10]:
        print('  ', v)
    names = {}
    for r in rows:
        names.setdefault(r['name'], []).append(r['folder'])
    multi = {k: v for k, v in names.items() if len(v) > 1}
    print('same file name in >1 folder:', len(multi), 'e.g.', list(multi.items())[:5])
    print(f'{len(rows)} files, {time.time() - t0:.0f} s')


if __name__ == '__main__':
    main()
