"""CardiacUDA A4C label sanity: is 1 LV / 2 LA / 3 RA / 4 RV (official GraphEcho code) consistent on every labelled frame?

Per labelled frame: area and centroid (row, col) of every value present. Anatomy of a standard A4C (apex at the top):
ventricles (LV, RV) above the atria (LA, RA); LV and LA on the same side, RV and RA on the other side.
Per video: the majority side of LV (left / right of RV), flags for frames that break the rules, and the classes
present. Writes results/label_check.json and a montage results/label_montage.png (one labelled frame per folder +
the odd label_all_frame videos).

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I check_labels.py
"""
import json
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'echo'))
from nifti import read_nifti  # noqa: E402

ROOT = HERE.parent / 'CardiacUDA' / 'data' / 'cardiacUDC_dataset'
OUT = HERE / 'results'
NAMES = {1: 'LV', 2: 'LA', 3: 'RA', 4: 'RV'}


def check(path):
    key = f'{path.parent.name}/{path.name}'
    try:
        lab, _ = read_nifti(path)
    except Exception as e:
        return dict(video=key, error=str(e))
    frames = []
    for t, f in enumerate(lab):
        if (f > 0).sum() <= 100:
            continue
        c = {}
        for v in np.unique(f):
            if v == 0:
                continue
            r, cc = np.nonzero(f == v)
            c[int(v)] = dict(area=int(len(r)), row=float(r.mean()), col=float(cc.mean()))
        frames.append(dict(t=t, cls=c))
    flags = []
    sides = []
    for fr in frames:
        c = fr['cls']
        if all(k in c for k in (1, 2, 3, 4)):
            ok_v = max(c[1]['row'], c[4]['row']) < min(c[2]['row'], c[3]['row']) + 0.15 * 600
            lv_right = c[1]['col'] > c[4]['col']
            la_right = c[2]['col'] > c[3]['col']
            sides.append(lv_right)
            if not ok_v:
                flags.append((fr['t'], 'ventricle below atrium'))
            if lv_right != la_right:
                flags.append((fr['t'], 'LV/LA on different sides'))
        else:
            flags.append((fr['t'], f'classes {sorted(c)}'))
    return dict(video=key, n_labelled=len(frames), values=sorted({k for fr in frames for k in fr['cls']}),
                lv_right_frac=float(np.mean(sides)) if sides else None, flags=flags,
                area_median={NAMES.get(k, str(k)): float(np.median([fr['cls'][k]['area'] for fr in frames if k in fr['cls']]))
                             for k in sorted({k for fr in frames for k in fr['cls']})})


def montage(rows):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    pick = []
    for folder in sorted({r['video'].split('/')[0] for r in rows}):
        rr = [r for r in rows if r['video'].startswith(folder + '/') and r.get('n_labelled')]
        pick += rr[:2]
    pick += [r for r in rows if r['video'].startswith('label_all_frame') and set(r['values']) - {1, 2, 3, 4}]
    fig, ax = plt.subplots(3, (len(pick) + 2) // 3, figsize=(4 * ((len(pick) + 2) // 3), 9))
    for a, r in zip(ax.ravel(), pick):
        img, _ = read_nifti(ROOT / r['video'].replace('_label', '_image'))
        lab, _ = read_nifti(ROOT / r['video'])
        t = next(i for i, f in enumerate(lab) if (f > 0).sum() > 100)
        a.imshow(img[t], cmap='gray')
        a.imshow(np.ma.masked_equal(lab[t], 0), cmap='tab10', vmin=0, vmax=9, alpha=0.45)
        for v in np.unique(lab[t]):
            if v:
                rr_, cc = np.nonzero(lab[t] == v)
                a.text(cc.mean(), rr_.mean(), str(int(v)), color='w', fontsize=13, ha='center')
        a.set_title(f"{r['video']} t{t}", fontsize=7)
        a.axis('off')
    for a in ax.ravel()[len(pick):]:
        a.axis('off')
    fig.tight_layout()
    fig.savefig(OUT / 'label_montage.png', dpi=70)


def main():
    files = sorted(ROOT.rglob('*_label.nii.gz'))
    with Pool(12) as pool:
        rows = pool.map(check, files, chunksize=2)
    (OUT / 'label_check.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')
    for r in rows:
        if 'error' in r:
            print('ERROR', r['video'], r['error'])
        elif r['flags'] or set(r['values']) != {1, 2, 3, 4} or r['lv_right_frac'] not in (1.0,):
            print(f"{r['video']:45s} n{r['n_labelled']:3d} values {r['values']} LV-right {r['lv_right_frac']} "
                  f"flags {r['flags'][:4]}{' ...' if len(r['flags']) > 4 else ''}")
    ok = [r for r in rows if 'error' not in r and not r['flags'] and r['lv_right_frac'] == 1.0]
    print(f'{len(ok)} / {len(rows)} label files fully consistent (all 4 classes, ventricles above atria, LV right of RV)')
    montage([r for r in rows if 'error' not in r])


if __name__ == '__main__':
    main()
