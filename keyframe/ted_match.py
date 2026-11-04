"""Keyframe step 0: map every TED case to its CAMUS patient by image content (not by metadata).

TED (98 cases, full A4C cycle, per-frame LV + myocardium labels) was cut from CAMUS. TED frames are 748 wide and
~2 x as many rows as CAMUS (y spacing 0.154 vs 0.308 mm, same field of view). For each TED case the ED frame
(cfg ED, 1-based), resampled to the CAMUS grid, is compared with every CAMUS 4CH ED image of the same width
(normalised cross-correlation on a 2 x decimated grid). Best match + margin to the runner-up; then the confirmed
pair is checked frame by frame: the CAMUS half sequence (ED -> ES) against TED frames ED .. ES.

Output: results/ted_camus_map.json  {ted: {camus, split, ncc, ncc_2nd, seq_ncc_min, ...}} and a split summary.
Test split rule: TED cases that are CAMUS *testing* patients are marked and must not be used to tune anything.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I ted_match.py
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
sys.path.insert(0, str(PLD / 'echo'))
from nifti import read_nifti  # noqa: E402

TED = PLD / 'TED' / 'database'
CAMUS = PLD / 'echo' / 'data' / 'camus'
OUT = HERE / 'results'


def cfg(p):
    d = {}
    for line in Path(p).read_text().splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            d[k.strip()] = v.strip()
    return d


def mhd(path):
    """-> (array [z, y, x], spacing (x, y, z)) for TED's uncompressed MET_UCHAR files"""
    hdr = {}
    for line in Path(path).read_text().splitlines():
        k, v = line.split('=', 1)
        hdr[k.strip()] = v.strip()
    dims = [int(x) for x in hdr['DimSize'].split()]
    sp = [float(x) for x in hdr['ElementSpacing'].split()]
    assert hdr['ElementType'] == 'MET_UCHAR', hdr['ElementType']
    a = np.fromfile(Path(path).parent / hdr['ElementDataFile'], np.uint8).reshape(dims[::-1])
    return a, sp


def to_grid(img, shape):
    """float image -> (rows, cols) grid, bilinear, zero-mean unit-norm"""
    g = np.asarray(Image.fromarray(np.asarray(img, np.float32)).resize(shape[::-1], Image.BILINEAR), np.float64)
    g -= g.mean()
    n = np.linalg.norm(g)
    return g / n if n > 0 else g


def ted_cases():
    return sorted(p.name for p in TED.iterdir() if p.is_dir())


def main():
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    split = {}
    for s in ('training', 'validation', 'testing'):
        for pid in (CAMUS / 'database_split' / f'subgroup_{s}.txt').read_text().split():
            split[pid] = s
    # CAMUS 4CH ED images, grouped by width; grid = (rows/2, cols/2) of the CAMUS image
    cam = {}
    for d in sorted(p for p in (CAMUS / 'database_nifti').iterdir() if p.is_dir()):
        a, sp = read_nifti(d / f'{d.name}_4CH_ED.nii.gz')
        cam[d.name] = dict(shape=a.shape, sp=sp, img=a)
    print(f'CAMUS: {len(cam)} 4CH ED images ({time.time() - t0:.0f} s)', flush=True)
    res = {}
    for t in ted_cases():
        d = TED / t
        c = cfg(d / f'{t}_4CH_info.cfg')
        seq, sp = mhd(d / f'{t}_4CH_sequence.mhd')
        ed = int(c['ED']) - 1
        T, H, W = seq.shape
        phys_h = H * sp[1]
        best = []
        for pid, e in cam.items():
            h, w = e['shape']
            if w != W or abs(h * e['sp'][1] - phys_h) > 2 * e['sp'][1]:
                continue
            grid = (h // 2, w // 2)
            if 'g' not in e or e['g'].shape != grid:
                e['g'] = to_grid(e['img'], grid)
            best.append((float((to_grid(seq[ed], grid) * e['g']).sum()), pid))
        best.sort(reverse=True)
        r = dict(ted_frames=T, ed=int(c['ED']), es=int(c['ES']), ef=c.get('EF'), quality=c.get('ImageQuality'),
                 n_candidates=len(best))
        if best:
            pid = best[0][1]
            r.update(camus=pid, split=split[pid], ncc=best[0][0], ncc_2nd=best[1][0] if len(best) > 1 else None,
                     second=best[1][1] if len(best) > 1 else None)
            # frame-by-frame check against the CAMUS half sequence (ED -> ES)
            hs, _ = read_nifti(CAMUS / 'database_nifti' / pid / f'{pid}_4CH_half_sequence.nii.gz')
            grid = (hs.shape[1] // 2, hs.shape[2] // 2)
            n = min(len(hs), T - ed)
            nccs = [float((to_grid(seq[ed + i], grid) * to_grid(hs[i], grid)).sum()) for i in range(n)]
            r.update(camus_half_frames=len(hs), seq_ncc_min=min(nccs), seq_ncc_mean=float(np.mean(nccs)),
                     camus_info=cfg(CAMUS / 'database_nifti' / pid / 'Info_4CH.cfg'))
        res[t] = r
        print(t, r.get('camus'), r.get('split'), f"ncc {r.get('ncc', 0):.4f} 2nd {r.get('ncc_2nd') or 0:.4f} "
              f"seq_min {r.get('seq_ncc_min', 0):.4f} cand {len(best)}", flush=True)
    # the black sector background alone gives other patients ~0.6-0.91, so the rule is on the margin + the sequence
    ok = [r for r in res.values() if r.get('ncc', 0) > 0.98 and r['ncc'] - (r['ncc_2nd'] or 0) > 0.05
          and r['seq_ncc_min'] > 0.98]
    summary = dict(ted_cases=len(res), confident=len(ok),
                   unique_camus=len({r['camus'] for r in ok}),
                   split_counts={s: sum(r['split'] == s for r in ok) for s in ('training', 'validation', 'testing')},
                   testing_cases=sorted(t for t, r in res.items() if r.get('split') == 'testing'),
                   validation_cases=sorted(t for t, r in res.items() if r.get('split') == 'validation'),
                   unconfident=sorted(t for t, r in res.items() if r not in ok),
                   ncc_min=min(r['ncc'] for r in res.values()),
                   runner_up_max=max(r['ncc_2nd'] or 0 for r in res.values()),
                   seq_ncc_min=min(r['seq_ncc_min'] for r in res.values()),
                   rule='ncc > 0.98, margin to runner-up > 0.05, every half-sequence frame ncc > 0.98')
    (OUT / 'ted_camus_map.json').write_text(json.dumps(dict(summary=summary, cases=res), indent=1), encoding='utf-8')
    print(json.dumps(summary, indent=1))
    print(f'done in {time.time() - t0:.0f} s')


if __name__ == '__main__':
    main()
