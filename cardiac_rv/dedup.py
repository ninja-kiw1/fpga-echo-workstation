"""CardiacUDA: find the same video (or the same patient's video) in more than one folder by image content.

Each image video -> 3 frames (first, middle, last) resized to 64 x 64 (bilinear, zero-mean unit-norm); pair score =
max over the 3 x 3 frame pairs of the NCC (catches re-cut clips of the same recording). Also a per-video sector
"fingerprint" is avoided by subtracting the folder-wide mean frame before the NCC (the black sector shape alone makes
every pair look similar). Pairs above 0.9 are listed; results/dedup.json.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I dedup.py
"""
import json
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'echo'))
from nifti import read_nifti  # noqa: E402

ROOT = HERE.parent / 'CardiacUDA' / 'data' / 'cardiacUDC_dataset'
OUT = HERE / 'results'
G = 64


def thumbs(path):
    a, _ = read_nifti(path)
    T = len(a)
    out = []
    for t in (0, T // 2, T - 1):
        g = np.asarray(Image.fromarray(np.asarray(a[t], np.float32)).resize((G, G), Image.BILINEAR), np.float64)
        out.append(g)
    return f'{path.parent.name}/{path.name}', np.stack(out), T, list(a.shape[1:])


def main():
    files = sorted(ROOT.rglob('*_image.nii.gz'))
    with Pool(12) as pool:
        res = pool.map(thumbs, files, chunksize=4)
    names = [r[0] for r in res]
    X = np.stack([r[1] for r in res])             # [n, 3, G, G]
    X = X - X.reshape(-1, G, G).mean(0)           # remove the common sector / background
    X = X - X.mean(axis=(2, 3), keepdims=True)
    X /= np.linalg.norm(X, axis=(2, 3), keepdims=True) + 1e-9
    F = X.reshape(len(X) * 3, -1)
    S = (F @ F.T).reshape(len(X), 3, len(X), 3).max(axis=(1, 3))
    np.fill_diagonal(S, -1)
    pairs = [(float(S[i, j]), names[i], names[j], res[i][2], res[j][2], res[i][3], res[j][3])
             for i in range(len(X)) for j in range(i + 1, len(X)) if S[i, j] > 0.9]
    pairs.sort(reverse=True)
    best = S.max(1)
    out = dict(videos=len(X), pairs_over_0_9=len(pairs), pairs=pairs,
               score_quantiles=[float(np.quantile(S[np.triu_indices(len(X), 1)], q)) for q in (0.5, 0.99, 0.999)],
               nearest=dict(zip(names, best.tolist())))
    OUT.mkdir(exist_ok=True)
    (OUT / 'dedup.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print('videos', len(X), 'pairs > 0.9:', len(pairs), 'score quantiles 50/99/99.9 %:', out['score_quantiles'])
    for p in pairs[:40]:
        print(f'{p[0]:.4f}  {p[1]:42s} T{p[3]:4d} {p[5]}  {p[2]:42s} T{p[4]:4d} {p[6]}')


if __name__ == '__main__':
    main()
