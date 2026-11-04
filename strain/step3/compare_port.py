r"""Part E step 3: strain_port.py (library-free reference) vs strain/step2/track_val.py (scipy / skimage), CAMUS VAL.

Per sequence (final config, model placement, tier 512): cleaned masks, 8-component, skeleton, longest path, resampled
points, Q8 start, every tracked position, every curve value, ES value; plus VAL stats vs the expert mid-wall
reference (track_val.json). Writes results/compare_port.json and results/port_val.npz (inputs + outputs for the C
harness). Test split: not used.

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe compare_port.py [n]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy import ndimage
from skimage.morphology import skeletonize

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'step2'))
import track_val as tv  # noqa: E402
import strain_port as sp  # noqa: E402


def main():
    t0 = time.time()
    meta = json.loads((tv.CACHE / 'val_meta.json').read_text(encoding='utf-8'))
    mi = {(m['patient'], m['view'], m['phase']): i for i, m in enumerate(meta)}
    seqs = sorted({(m['patient'], m['view']) for m in meta})
    if len(sys.argv) > 1:
        seqs = seqs[:int(sys.argv[1])]
    tvj = json.loads((HERE.parent / 'step2' / 'results' / 'track_val.json').read_text(encoding='utf-8'))
    tvper = {(r['patient'], r['view']): r for r in tvj['per']}
    c = next(x for x in tv.CONFIGS if x['name'] == tv.FINAL)
    rows, npz = [], {}
    for si, (p, view) in enumerate(seqs):
        frames, sx, sy, _ = tv.load_sequence(p, view, 512)
        i = mi[(p, view, 'ED')]
        q = {tag: np.load(tv.ECHO / 'results' / 'quant' / tag / 'outputs' / f'val_{i:03d}.npy') >= 1
             for tag in ('lv_r128', 'epi_r128')}
        r = dict(patient=p, view=view, T=len(frames))
        # library chain (track_val)
        lv_t, epi_t = tv.model_mask('lv_r128', i, 512), tv.model_mask('epi_r128', i, 512)
        myo_t = epi_t & ~lv_t
        lab, n = ndimage.label(myo_t, structure=np.ones((3, 3)))
        big_t = lab == (int(np.argmax(ndimage.sum(myo_t, lab, range(1, n + 1)))) + 1) if n > 1 else myo_t
        sk_t = skeletonize(big_t)
        path_t = tv.longest_path(sk_t, sx, sy)
        pts_t = tv.resample(path_t, sx, sy, tv.STEP_MM)
        trk_t, edges_t = tv.track_line(frames, pts_t, c)
        L_t = np.array([tv.line_length(qq, sx, sy, c) for qq in trk_t])
        g_t = L_t / L_t[0] - 1
        # port
        e = sp.ed_line(q['lv_r128'], q['epi_r128'], sx, sy)
        qt, L, g, edges = sp.track(frames, e['q8'], sx, sy)
        r.update(
            clean_same=bool(np.array_equal(sp.up4(e['lv']), lv_t) and np.array_equal(sp.up4(e['epi']), epi_t)),
            comp_same=bool(np.array_equal(e['myo'], big_t)),
            skel_same=bool(np.array_equal(sp.skeleton(e['myo']), sk_t)),
            skel_px=int(sk_t.sum()),
            path_same=bool(len(e['path']) == len(path_t) and np.array_equal(e['path'], path_t.astype(np.int64))),
            pts_maxdiff=float(np.abs(e['pts0'] - pts_t).max()) if e['pts0'].shape == pts_t.shape else None,
            pts_same=bool(e['pts0'].shape == pts_t.shape and np.array_equal(e['pts0'], pts_t)),
            trk_same=bool(qt.shape == trk_t.shape and np.array_equal(qt / 256.0, trk_t)),
            curve_same=bool(len(g) == len(g_t) and np.array_equal(np.array(g), g_t)),
            curve_maxdiff=float(np.abs(np.array(g) - g_t).max()) if len(g) == len(g_t) else None,
            es_port=g[-1] * 100, es_tv=float(g_t[-1] * 100), es_tvjson=tvper[(p, view)]['configs'][tv.FINAL]['es'] * 100,
            n_pts=len(e['pts0']), edges=edges, ref_mid=tvper[(p, view)]['ref_mid_512'] * 100)
        rows.append(r)
        k = f'{si:03d}'
        npz.update({f'{k}_frames': frames, f'{k}_lv': q['lv_r128'], f'{k}_epi': q['epi_r128'],
                    f'{k}_sxsy': np.array([sx, sy]), f'{k}_path': e['path'], f'{k}_pts0': e['pts0'],
                    f'{k}_q8': qt, f'{k}_L': np.array(L), f'{k}_g': np.array(g)})
        print(f"{p} {view} T={r['T']:2d} n={r['n_pts']:3d} clean {r['clean_same']} skel {r['skel_same']} "
              f"path {r['path_same']} pts {r['pts_same']} trk {r['trk_same']} curve {r['curve_same']} "
              f"es {r['es_port']:7.3f} / {r['es_tv']:7.3f} / {r['es_tvjson']:7.3f}", flush=True)
    est = np.array([r['es_port'] for r in rows])
    ref = np.array([r['ref_mid'] for r in rows])
    summ = dict(n=len(rows), **{k: int(sum(r[k] for r in rows)) for k in
                                ('clean_same', 'comp_same', 'skel_same', 'path_same', 'pts_same', 'trk_same', 'curve_same')},
                es_maxdiff_vs_tv=float(max(abs(r['es_port'] - r['es_tv']) for r in rows)),
                es_maxdiff_vs_tvjson=float(max(abs(r['es_port'] - r['es_tvjson']) for r in rows)),
                raw=tv.stats(est, ref), offset=tv.stats(est + sp.OFFSET_PP, ref), offset_pp=sp.OFFSET_PP,
                seconds=time.time() - t0)
    out = HERE / 'results'
    out.mkdir(exist_ok=True)
    (out / 'compare_port.json').write_text(json.dumps(dict(summary=summ, per=rows), indent=1), encoding='utf-8')
    np.savez_compressed(out / 'port_val.npz', **npz)
    print(json.dumps(summ, indent=1))


if __name__ == '__main__':
    main()
