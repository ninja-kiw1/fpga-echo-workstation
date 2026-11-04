"""Shared F3 evaluation: propagate over the official validation half sequences with any predictor.

predict(rgb uint8 (B,128,128,3)) -> bool (B,128,128). Encodings of the propagation input:
  prev  R frame t, G previous step's mask 0/255, B frame of the previous step   (model echo_seq)
  none  R frame t, G previous step's mask 0/255, B 128 (= model 3 without clicks)
Start masks are the board's F1 integer results at ED (forward) and ES (backward).
Expert labels only at ED / ES (original resolution); in-between labels (provenance not
stated by CAMUS) only as an auxiliary 128-grid reference.
"""
import json

import numpy as np

from common import CACHE, RESULTS, board_upsample, load_cache, seg_metrics


def encode(enc, img_t, prior, img_prev):
    if enc == 'prev':
        b = img_prev
    elif enc == 'none':
        b = np.full(img_t.shape, 128, np.uint8)
    else:
        raise ValueError(enc)
    return np.stack([img_t, prior.astype(np.uint8) * 255, b], -1)


def load_val_seq(f1_tag='lv_r128'):
    img = np.load(CACHE / 'seq_val_img128.npy')
    lab = np.load(CACHE / 'seq_val_lv128.npy').astype(bool)
    index = json.loads((CACHE / 'seq_val_index.json').read_text(encoding='utf-8'))
    x512, _, cells, rows, cols, meta = load_cache('val')
    where = {(m['patient'], m['view'], m['phase']): i for i, m in enumerate(meta)}
    for s in index:
        s['ed'], s['es'] = where[(s['patient'], s['view'], 'ED')], where[(s['patient'], s['view'], 'ES')]
        assert np.array_equal(img[s['start']], np.asarray(x512[s['ed']])[::4, ::4])
        assert np.array_equal(img[s['start'] + s['length'] - 1], np.asarray(x512[s['es']])[::4, ::4])
        s['quality'] = meta[s['ed']]['quality']
    f1 = lambda i: np.load(RESULTS / 'quant' / f1_tag / 'outputs' / f'val_{i:03d}.npy') >= 1
    return dict(img=img, lab=lab, index=index, cells=cells, rows=rows, cols=cols, meta=meta,
                f1_ed=[f1(s['ed']) for s in index], f1_es=[f1(s['es']) for s in index])


def propagate(predict, img, orders, starts, enc, record=False):
    """All sequences stepped together. orders: frame index arrays; starts: masks of step 0."""
    n = len(orders)
    out = [np.zeros((len(o), 128, 128), bool) for o in orders]
    rec = [np.zeros((len(o), 128, 128, 3), np.uint8) for o in orders] if record else None
    for s in range(n):
        out[s][0] = starts[s]
    for t in range(1, max(len(o) for o in orders)):
        live = [s for s in range(n) if t < len(orders[s])]
        rgb = np.stack([encode(enc, img[orders[s][t]], out[s][t - 1], img[orders[s][t - 1]]) for s in live])
        m = predict(rgb)
        for j, s in enumerate(live):
            out[s][t] = m[j]
            if record:
                rec[s][t] = rgb[j]
    return out, rec


def dice128(a, b):
    s = a.sum() + b.sum()
    return 1.0 if s == 0 else float(2 * (a & b).sum() / s)


def sequence_metrics(V, masks):
    """masks: per sequence (T,128,128) in ED -> ES order."""
    rows_ = []
    for s, m in zip(V['index'], masks):
        ed = seg_metrics(board_upsample(m[0], 2), V['cells'][s['ed']], V['rows'][s['ed']], V['cols'][s['ed']])
        es = seg_metrics(board_upsample(m[-1], 2), V['cells'][s['es']], V['rows'][s['es']], V['cols'][s['es']])
        area = m.reshape(len(m), -1).sum(1).astype(float)
        r4 = V['rows'][s['ed']].reshape(128, 4).sum(1).astype(np.int64)     # original pixels per 128 cell row
        c4 = V['cols'][s['ed']].reshape(128, 4).sum(1).astype(np.int64)
        area_o = np.array([int(r4 @ f.astype(np.int64) @ c4) for f in m], float)   # = seg_metrics pred_area_px
        lab = V['lab'][s['start']:s['start'] + s['length']]
        rows_.append(dict(
            patient=s['patient'], view=s['view'], quality=s['quality'], length=s['length'],
            ed_dice=ed['dice'], es_dice=es['dice'],
            fac_hat=(ed['pred_area_px'] - es['pred_area_px']) / max(ed['pred_area_px'], 1),
            fac_ref=(ed['gt_area_px'] - es['gt_area_px']) / max(ed['gt_area_px'], 1),
            ed_err=int(np.argmax(area_o)), es_err=int(len(area_o) - 1 - np.argmin(area_o)),
            # deployable FAC: no knowledge of which frames are ED / ES, max / min of the area curve
            fac_auto=float((area_o.max() - area_o.min()) / max(area_o.max(), 1)),
            jump=[1 - dice128(m[t], m[t - 1]) for t in range(1, len(m))],
            roughness=float(np.abs(np.diff(area_o, 2)).mean() / max(area_o.mean(), 1)) if len(area_o) > 2 else 0.0,
            aux_dice=[dice128(m[t], lab[t]) for t in range(len(m))], area=area_o.tolist()))
    return rows_


def summarize(rows_):
    mean = lambda key, rs=rows_: float(np.mean([r[key] for r in rs]))
    ok = [r for r in rows_ if r['quality'] != 'Poor']
    jumps = [j for r in rows_ for j in r['jump']]
    fh, fr = [r['fac_hat'] for r in rows_], [r['fac_ref'] for r in rows_]
    return dict(
        ed_dice=mean('ed_dice'), es_dice=mean('es_dice'),
        ed_dice_good_medium=mean('ed_dice', ok), es_dice_good_medium=mean('es_dice', ok),
        fac_abs_err_pct=float(np.mean(np.abs(np.array(fh) - np.array(fr))) * 100),
        fac_corr=float(np.corrcoef(fh, fr)[0, 1]) if np.std(fh) > 0 else float('nan'),
        fac_bias_pct=float(np.mean(np.array(fh) - np.array(fr)) * 100),
        fac_auto_abs_err_pct=float(np.mean([abs(r['fac_auto'] - r['fac_ref']) for r in rows_]) * 100),
        fac_auto_bias_pct=float(np.mean([r['fac_auto'] - r['fac_ref'] for r in rows_]) * 100),
        fac_auto_corr=float(np.corrcoef([r['fac_auto'] for r in rows_], fr)[0, 1]),
        keyframe_exact_ed=float(np.mean([r['ed_err'] == 0 for r in rows_])),
        keyframe_exact_es=float(np.mean([r['es_err'] == 0 for r in rows_])),
        keyframe_mean_err_frames=float(np.mean([r['ed_err'] + r['es_err'] for r in rows_]) / 2),
        jump_mean=float(np.mean(jumps)), jump_p95=float(np.percentile(jumps, 95)),
        roughness=mean('roughness'),
        aux_dice_mid=float(np.mean([np.mean(r['aux_dice'][1:-1]) for r in rows_])))


def run_both(predict, V, enc, record=False):
    """Forward from the F1 ED result and backward from the F1 ES result.
    Score (pre-registered for F3 selection) = 0.5 * (forward ES Dice + backward ED Dice)."""
    fwd_order = [np.arange(s['start'], s['start'] + s['length']) for s in V['index']]
    bwd_order = [o[::-1] for o in fwd_order]
    fwd, rec_f = propagate(predict, V['img'], fwd_order, V['f1_ed'], enc, record)
    bwd, rec_b = propagate(predict, V['img'], bwd_order, V['f1_es'], enc, record)
    bwd = [m[::-1] for m in bwd]
    rf, rb = sequence_metrics(V, fwd), sequence_metrics(V, bwd)
    sf, sb = summarize(rf), summarize(rb)
    out = dict(fwd=sf, bwd=sb, score=0.5 * (sf['es_dice'] + sb['ed_dice']))
    return out, dict(fwd=fwd, bwd=bwd, rows_fwd=rf, rows_bwd=rb, rec_fwd=rec_f, rec_bwd=rec_b)
