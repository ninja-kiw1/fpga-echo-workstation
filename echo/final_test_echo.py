"""One-time evaluation of the frozen CAMUS models (F1 / F2 / F3, and the masks for F4), stage A.

Frozen before this script was written (2026-10-09): F1 lv_r128, F2 inter2_r128,
F3 seq_none_ex, F4 = tcp_image_ui/ef_simpson.py with clean(); integer models = board.
The same script runs on 'val' first (must reproduce the numbers already in README) and
then ONCE on 'test' -- a lock file stops a second test run.

  F1  Dice on the expert ED / ES frames (original resolution), by quality / phase / view;
      Hausdorff distance and mean absolute contour distance in mm (boundary pixels of
      the raw board mask NEAREST-upsampled to the original size vs the expert mask)
  F2  standard click protocol k = 0..5, from the F1 result and from empty; NoC@0.90
  F3  forward / backward propagation (seqeval.run_both) and the F1 every-frame baseline
  F4  saves the f1 / fwd / click1 / click2 masks of every frame for final_test_echo_ef.py

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe final_test_echo.py --split val|test
"""
import argparse
import json
import sys
import time
from datetime import datetime

import numpy as np
import torch
from PIL import Image
from scipy import ndimage

from common import CACHE, CAMUS, LABELS, RESULTS, board_upsample, grouped, load_cache, seg_metrics
from compare_f3 import int_predict
from nifti import read_nifti
from seqeval import propagate, run_both, sequence_metrics, summarize
from train_inter import click_curve, click_maps, next_click

TAGS = dict(f1='lv_r128', f2='inter2_r128', f3='seq_none_ex')


def contour_distances(pred, gt, spacing_hw):
    """(Hausdorff, mean absolute distance) in mm between the boundaries of two masks."""
    if not pred.any():
        return float('nan'), float('nan')
    bp = pred & ~ndimage.binary_erosion(pred)
    bg = gt & ~ndimage.binary_erosion(gt)
    to_g = ndimage.distance_transform_edt(~bg, sampling=spacing_hw)[bp]
    to_p = ndimage.distance_transform_edt(~bp, sampling=spacing_hw)[bg]
    return float(max(to_g.max(), to_p.max())), float(0.5 * (to_g.mean() + to_p.mean()))


def to_original(m128, w, h):
    m512 = np.repeat(np.repeat(m128.astype(np.uint8) * 255, 4, 0), 4, 1)
    return np.array(Image.fromarray(m512).resize((w, h), Image.NEAREST)) > 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--split', choices=('val', 'test'), required=True)
    ap.add_argument('--resume-after-crash', default=None, metavar='REASON',
                    help='test only: rerun stage A after a crash that happened BEFORE it finished; the reason is logged')
    args = ap.parse_args()
    out = RESULTS / f'final_{args.split}'
    out.mkdir(parents=True, exist_ok=True)
    lock = out / 'test_set_used.txt'
    if args.split == 'test':
        if lock.exists():
            text = lock.read_text(encoding='utf-8')
            if 'stage A finished' in text or not args.resume_after_crash:
                sys.exit(f'{lock} exists: the CAMUS test set was already evaluated.')
            lock.write_text(text + f'stage A restarted {datetime.now().isoformat()} after a crash: {args.resume_after_crash}\n',
                            encoding='utf-8')
        else:
            lock.write_text(f'test evaluation (stage A) started {datetime.now().isoformat()}\nmodels {TAGS}\n', encoding='utf-8')
    torch.set_num_threads(8)
    t0 = time.time()
    p1, p2, p3 = (int_predict(TAGS[k]) for k in ('f1', 'f2', 'f3'))
    x512, y512, cells, rows, cols, meta = load_cache(args.split)
    n = len(meta)
    img128 = np.asarray(x512)[:, ::4, ::4].copy()
    gt128 = np.asarray(y512).reshape(-1, 128, 4, 128, 4).mean(axis=(2, 4)) >= 0.5
    report = dict(split=args.split, models=TAGS, frames=n, utc=datetime.now().isoformat())

    # ---------------- F1
    f1 = p1(np.repeat(img128[..., None], 3, -1))
    per = []
    for i, m in enumerate(meta):
        r = seg_metrics(board_upsample(f1[i], 2), cells[i], rows[i], cols[i])
        gt, sp = read_nifti(CAMUS / 'database_nifti' / m['patient'] / f"{m['patient']}_{m['view']}_{m['phase']}_gt.nii.gz")
        gt = gt == LABELS['lv']
        r['hd_mm'], r['mad_mm'] = contour_distances(to_original(f1[i], m['width'], m['height']), gt, (sp[1], sp[0]))
        per.append(r)
    ok = [r for r in per if not np.isnan(r['hd_mm'])]
    report['f1'] = dict(dice=grouped(per, meta, 'dice'), jaccard=float(np.mean([r['jaccard'] for r in per])),
                        hd_mm_mean=float(np.mean([r['hd_mm'] for r in ok])), hd_mm_median=float(np.median([r['hd_mm'] for r in ok])),
                        mad_mm_mean=float(np.mean([r['mad_mm'] for r in ok])), empty_predictions=n - len(ok),
                        per_frame=[dict(patient=m['patient'], view=m['view'], phase=m['phase'], quality=m['quality'],
                                        dice=r['dice'], hd_mm=r['hd_mm'], mad_mm=r['mad_mm']) for r, m in zip(per, meta)])
    print(f"F1 Dice {report['f1']['dice']['all']:.4f} (good+med {report['f1']['dice']['good+medium']:.4f}), "
          f"HD {report['f1']['hd_mm_mean']:.2f} mm, MAD {report['f1']['mad_mm_mean']:.2f} mm ({time.time() - t0:.0f}s)", flush=True)

    # ---------------- F2
    pred3 = lambda rgbs: list(p2(np.stack(rgbs)))
    curves = {}
    for name, prior in (('from_f1', list(f1)), ('from_empty', [np.zeros_like(m) for m in f1])):
        d, _ = click_curve(pred3, img128, gt128, prior, cells, rows, cols)
        curves[name] = dict(dice=[float(np.mean(v)) for v in d],
                            noc90=float(np.mean([next((k for k in range(len(d)) if d[k][i] >= 0.9), 6) for i in range(n)])))
    report['f2'] = curves
    print('F2 from F1 ' + ' '.join(f'{v:.4f}' for v in curves['from_f1']['dice'])
          + ' | from empty ' + ' '.join(f'{v:.4f}' for v in curves['from_empty']['dice']) + f' ({time.time() - t0:.0f}s)', flush=True)

    # ---------------- F3
    simg0 = np.load(CACHE / f'seq_{args.split}_img128.npy')
    slab0 = np.load(CACHE / f'seq_{args.split}_lv128.npy').astype(bool)
    index = json.loads((CACHE / f'seq_{args.split}_index.json').read_text(encoding='utf-8'))
    # Some CAMUS half sequences are stored ES -> ED. The stored images decide (Info_*.cfg frame numbers are
    # not reliable for this: patient0217_2CH says "ED: 21; ES: 1" yet starts with the ED image): a sequence
    # whose first frame is the ES image and last frame the ED image is turned around, so every sequence
    # runs ED -> ES as prep_camus_seq.py assumed. Below, both ends must then match the expert frames.
    where = {(m['patient'], m['view'], m['phase']): i for i, m in enumerate(meta)}
    parts_i, parts_l, pos, reversed_ = [], [], 0, []
    for s in index:
        sl = slice(s['start'], s['start'] + s['length'])
        ed_i, es_i = where[(s['patient'], s['view'], 'ED')], where[(s['patient'], s['view'], 'ES')]
        rev = (np.array_equal(simg0[s['start']], img128[es_i]) and np.array_equal(simg0[sl.stop - 1], img128[ed_i])
               and not np.array_equal(simg0[s['start']], img128[ed_i]))
        parts_i.append(simg0[sl][::-1] if rev else simg0[sl])
        parts_l.append(slab0[sl][::-1] if rev else slab0[sl])
        if rev:
            reversed_.append(f"{s['patient']}_{s['view']}")
        s['start'] = pos
        pos += s['length']
    simg, slab = np.concatenate(parts_i), np.concatenate(parts_l)
    report['sequences_stored_es_to_ed'] = reversed_
    print('sequences stored ES -> ED (turned around):', reversed_, flush=True)
    for s in index:
        s['ed'], s['es'] = where[(s['patient'], s['view'], 'ED')], where[(s['patient'], s['view'], 'ES')]
        assert np.array_equal(simg[s['start']], img128[s['ed']]) and np.array_equal(simg[s['start'] + s['length'] - 1], img128[s['es']])
        s['quality'] = meta[s['ed']]['quality']
    V = dict(img=simg, lab=slab, index=index, cells=cells, rows=rows, cols=cols, meta=meta,
             f1_ed=[f1[s['ed']] for s in index], f1_es=[f1[s['es']] for s in index])
    f1_all = p1(np.repeat(simg[..., None], 3, -1))
    assert all(np.array_equal(f1_all[s['start']], f1[s['ed']]) for s in index)
    sf = summarize(sequence_metrics(V, [f1_all[s['start']:s['start'] + s['length']] for s in index]))
    r3, det = run_both(p3, V, 'none')
    report['f3'] = dict(f1_every_frame=sf, fwd=r3['fwd'], bwd=r3['bwd'], score=r3['score'],
                        f1_score=0.5 * (sf['es_dice'] + sf['ed_dice']))
    print(f"F3 fwd ES {r3['fwd']['es_dice']:.4f} (F1 {sf['es_dice']:.4f}), bwd ED {r3['bwd']['ed_dice']:.4f} "
          f"(F1 {sf['ed_dice']:.4f}), jump {r3['fwd']['jump_mean']:.4f} (F1 {sf['jump_mean']:.4f}) ({time.time() - t0:.0f}s)", flush=True)

    # ---------------- F4 masks (as gen_f4_masks.py)
    fwd = np.zeros_like(f1_all)
    for k, s in enumerate(index):
        fwd[s['start']:s['start'] + s['length']] = det['fwd'][k]

    def click_once(im, mask, gt):
        c = next_click(mask, gt)
        if c is None:
            return mask
        return p2(np.stack([im, mask.astype(np.uint8) * 255, click_maps([c])], -1)[None])[0]
    starts = [click_once(simg[s['start']], f1[s['ed']], gt128[s['ed']]) for s in index]
    prop, _ = propagate(p3, simg, [np.arange(s['start'], s['start'] + s['length']) for s in index], starts, 'none')
    c1, c2 = np.zeros_like(f1_all), np.zeros_like(f1_all)
    for k, s in enumerate(index):
        c1[s['start']:s['start'] + s['length']] = prop[k]
        c2[s['start']:s['start'] + s['length']] = prop[k]
        last = s['start'] + s['length'] - 1
        c2[last] = click_once(simg[last], prop[k][-1], gt128[s['es']])
    np.savez_compressed(out / 'masks128.npz', f1=f1_all, fwd=fwd, c1=c1, c2=c2)
    (out / 'masks128_index.json').write_text(json.dumps(dict(seq_tag=TAGS['f3'], sequences=[
        dict(patient=s['patient'], view=s['view'], start=s['start'], length=s['length'], width=meta[s['ed']]['width'],
             height=meta[s['ed']]['height'], quality=s['quality']) for s in index])), encoding='utf-8')
    report['seconds'] = time.time() - t0
    (out / 'final_f1_f2_f3.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    if args.split == 'test':
        lock.write_text(lock.read_text(encoding='utf-8') + f'stage A finished {datetime.now().isoformat()}\n', encoding='utf-8')
    print(f'STAGE_A_DONE {args.split} ({time.time() - t0:.0f}s)', flush=True)


if __name__ == '__main__':
    main()
