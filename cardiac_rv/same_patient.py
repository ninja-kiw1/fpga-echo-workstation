"""CardiacUDA: are same-name files in different folders the same patient (different recording)?

Different recordings of one patient share anatomy, depth and gain, so their frames should be more alike than two
random patients from the same site. Score = dedup.py's score (max NCC over first / middle / last frames, common
background removed) but on the masked region only. Compares the score of same-name cross-folder pairs with
different-name pairs from the same site and folder pair. results/same_patient.json.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I same_patient.py
"""
import json
import re
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dedup import ROOT, OUT, thumbs  # noqa: E402


def pid(name):
    m = re.match(r'(patient|normal)-?(\d+)-4_image', name)
    return f'{m.group(1)}{int(m.group(2))}'


def main():
    files = [f for f in sorted(ROOT.rglob('*_image.nii.gz')) if f.parent.name != 'label_all_frame']
    with Pool(12) as pool:
        res = pool.map(thumbs, files, chunksize=4)
    names = [r[0] for r in res]
    folder = [n.split('/')[0] for n in names]
    ids = [pid(n.split('/')[1]) for n in names]
    X = np.stack([r[1] for r in res])
    X = X - X.reshape(-1, 64, 64).mean(0)
    X = X - X.mean(axis=(2, 3), keepdims=True)
    X /= np.linalg.norm(X, axis=(2, 3), keepdims=True) + 1e-9
    F = X.reshape(len(X) * 3, -1)
    S = (F @ F.T).reshape(len(X), 3, len(X), 3).max(axis=(1, 3))
    out = {}
    fs = sorted(set(folder))
    for a in range(len(fs)):
        for b in range(a + 1, len(fs)):
            fa, fb = fs[a], fs[b]
            if fa[:6] != fb[:6]:
                continue                       # only within one site
            ia = [i for i in range(len(X)) if folder[i] == fa]
            ib = [i for i in range(len(X)) if folder[i] == fb]
            same = [S[i, j] for i in ia for j in ib if ids[i] == ids[j]]
            diff = [S[i, j] for i in ia for j in ib if ids[i] != ids[j]]
            # rank of the same-name partner among all candidates of the other folder
            ranks = []
            for i in ia:
                js = [j for j in ib if ids[j] == ids[i]]
                if js:
                    order = np.argsort(-S[i, ib])
                    ranks.append(int(np.where(np.array(ib)[order] == js[0])[0][0]) + 1)
            out[f'{fa}~{fb}'] = dict(n_same=len(same), same_median=float(np.median(same)) if same else None,
                                     diff_median=float(np.median(diff)), diff_p95=float(np.percentile(diff, 95)),
                                     same_partner_rank_median=float(np.median(ranks)) if ranks else None,
                                     same_partner_top1=int(sum(r == 1 for r in ranks)), candidates=len(ib))
            print(f"{fa:11s} ~ {fb:11s} same-name pairs {len(same):3d}  score same {out[f'{fa}~{fb}']['same_median'] or 0:.3f} "
                  f"vs different {out[f'{fa}~{fb}']['diff_median']:.3f} (P95 {out[f'{fa}~{fb}']['diff_p95']:.3f}); "
                  f"partner rank median {out[f'{fa}~{fb}']['same_partner_rank_median']} of {len(ib)}, "
                  f"top-1 {out[f'{fa}~{fb}']['same_partner_top1']}")
    (OUT / 'same_patient.json').write_text(json.dumps(out, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
