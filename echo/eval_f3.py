"""F3 offline study: frame-to-frame propagation with the INTEGER models (validation only).

Strategies on the 100 official validation half sequences (ED -> ES, 10..33 frames):
  f1    every frame on its own with F1 (model 2, grey -> R=G=B)
  fwd   frame 0 (ED) with F1, then model 3 (echo_inter) frame by frame:
        R = frame t, G = output of frame t-1, B = 128 (no clicks)
  bwd   the same from ES backwards to ED
  hold  F1's ED mask copied to every frame (shows how much the propagated model adapts)
The board computes exactly these integer models, so the numbers are board numbers.

Expert annotation exists only for ED and ES: Dice there is measured at the original
resolution (as F1 / F2). The in-between labels of *_half_sequence_gt (provenance not
stated by CAMUS) are used only as an auxiliary reference at the 128 grid.
Also: contour jump 1 - Dice(m_t, m_t-1), area-curve roughness, ED / ES keyframe
position from the area curve (ED = max, ES = min), fractional area change
FAC = (A_ED - A_ES) / A_ED against the expert value, and drift correction at ES
(1..3 standard clicks with model 3, prior = the propagated ES mask).

Writes results/f3/<f2 tag>/summary.json, per-sequence curves, and the recorded
fwd inputs / outputs for the board replay (verify_board_f3.py). Test split untouched.

    python eval_f3.py [--f1 lv_r128] [--f2 inter2_r128]
"""
import argparse
import json
import time

import numpy as np
import torch

from common import CACHE, RESULTS, board_upsample, load_cache, seg_metrics
from int8_reference import run_integer
from train_inter import click_curve

BATCH = 25


def load_int(tag):
    d = RESULTS / 'quant' / tag
    plan = json.loads((d / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(d / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}

    def run(rgb):
        """uint8 (B,128,128,3) -> integer logits (B,128,128); the board's mask is logit >= 1."""
        outs = []
        with torch.inference_mode():
            for i in range(0, len(rgb), BATCH):
                q = torch.from_numpy(np.ascontiguousarray(rgb[i:i + BATCH])).permute(0, 3, 1, 2)
                outs.append(run_integer(plan, arrays, q)[0][:, 0].numpy().astype(np.int16))
        return np.concatenate(outs)
    return run


def grey3(img):
    return np.repeat(img[..., None], 3, -1)


def encode(img, prior):
    """Model-3 input without clicks: R frame, G prior 0/255, B 128."""
    b = np.full(img.shape, 128, np.uint8)
    return np.stack([img, prior.astype(np.uint8) * 255, b], -1)


def dice128(a, b):
    s = a.sum() + b.sum()
    return 1.0 if s == 0 else float(2 * (a & b).sum() / s)


def propagate(run3, img, start_mask, order):
    """Step all sequences together (batched by time step). order: list of index arrays."""
    n = len(order)
    out = [np.zeros((len(o), 128, 128), bool) for o in order]
    rec_in = [np.zeros((len(o), 128, 128, 3), np.uint8) for o in order]
    for s in range(n):
        out[s][0] = start_mask[s]
    for t in range(1, max(len(o) for o in order)):
        live = [s for s in range(n) if t < len(order[s])]
        rgb = np.stack([encode(img[order[s][t]], out[s][t - 1]) for s in live])
        m = run3(rgb) >= 1
        for j, s in enumerate(live):
            out[s][t] = m[j]
            rec_in[s][t] = rgb[j]
    return out, rec_in


def seq_stats(masks, label):
    area = masks.reshape(len(masks), -1).sum(1).astype(float)
    jump = [1 - dice128(masks[t], masks[t - 1]) for t in range(1, len(masks))]
    rough = float(np.abs(np.diff(area, 2)).mean() / max(area.mean(), 1)) if len(area) > 2 else 0.0
    aux = [dice128(masks[t], label[t]) for t in range(len(masks))]
    return dict(area=area.tolist(), jump=jump, roughness=rough, aux_dice=aux,
                ed_hat=int(np.argmax(area)), es_hat=int(np.argmin(area)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--f1', default='lv_r128')
    ap.add_argument('--f2', default='inter2_r128')
    args = ap.parse_args()
    torch.set_num_threads(8)
    out_dir = RESULTS / 'f3' / args.f2
    out_dir.mkdir(parents=True, exist_ok=True)
    img = np.load(CACHE / 'seq_val_img128.npy')
    lab = np.load(CACHE / 'seq_val_lv128.npy').astype(bool)
    index = json.loads((CACHE / 'seq_val_index.json').read_text(encoding='utf-8'))
    x512, y512, cells, rows, cols, meta = load_cache('val')
    where = {(m['patient'], m['view'], m['phase']): i for i, m in enumerate(meta)}
    for s in index:                                  # first / last frame are the expert ED / ES frames
        s['ed'], s['es'] = where[(s['patient'], s['view'], 'ED')], where[(s['patient'], s['view'], 'ES')]
        assert np.array_equal(img[s['start']], np.asarray(x512[s['ed']])[::4, ::4])
        assert np.array_equal(img[s['start'] + s['length'] - 1], np.asarray(x512[s['es']])[::4, ::4])
        s['quality'] = meta[s['ed']]['quality']
    run1, run3 = load_int(args.f1), load_int(args.f2)
    t0 = time.time()

    f1_logit = run1(grey3(img))
    f1 = f1_logit >= 1
    ref = np.array([np.load(RESULTS / 'quant' / args.f1 / 'outputs' / f'val_{s["ed"]:03d}.npy') for s in index])
    assert np.array_equal(f1_logit[[s['start'] for s in index]], ref), 'F1 integer run differs from quantize_echo outputs'
    print(f'F1 on {len(img)} frames ({time.time() - t0:.0f}s)', flush=True)

    fwd_order = [np.arange(s['start'], s['start'] + s['length']) for s in index]
    bwd_order = [o[::-1] for o in fwd_order]
    fwd_l, fwd_in = propagate(run3, img, [f1[o[0]] for o in fwd_order], fwd_order)
    print(f'fwd done ({time.time() - t0:.0f}s)', flush=True)
    bwd_l, _ = propagate(run3, img, [f1[o[0]] for o in bwd_order], bwd_order)
    bwd_l = [m[::-1] for m in bwd_l]
    print(f'bwd done ({time.time() - t0:.0f}s)', flush=True)
    strategies = dict(f1=[f1[o] for o in fwd_order], fwd=fwd_l, bwd=bwd_l,
                      hold=[np.repeat(f1[o[:1]], len(o), 0) for o in fwd_order])

    def expert(mask128, i):
        return seg_metrics(board_upsample(mask128, 2), cells[i], rows[i], cols[i])

    summary = dict(f1=args.f1, f2=args.f2, sequences=len(index), frames=int(len(img)), strategies={})
    per_seq = {}
    label_stats = [seq_stats(lab[o], lab[o]) for o in fwd_order]
    for name, masks in strategies.items():
        rows_ = []
        for s, m, ls in zip(index, masks, label_stats):
            ed, es = expert(m[0], s['ed']), expert(m[-1], s['es'])
            st = seq_stats(m, lab[s['start']:s['start'] + s['length']])
            fac_hat = (ed['pred_area_px'] - es['pred_area_px']) / max(ed['pred_area_px'], 1)
            fac_ref = (ed['gt_area_px'] - es['gt_area_px']) / max(ed['gt_area_px'], 1)
            rows_.append(dict(patient=s['patient'], view=s['view'], quality=s['quality'], length=s['length'],
                              ed_dice=ed['dice'], es_dice=es['dice'], fac_hat=fac_hat, fac_ref=fac_ref,
                              ed_err=st['ed_hat'], es_err=s['length'] - 1 - st['es_hat'],
                              jump_mean=float(np.mean(st['jump'])), jump_max=float(np.max(st['jump'])),
                              roughness=st['roughness'], aux_dice_mid=float(np.mean(st['aux_dice'][1:-1])),
                              area=st['area'], aux_dice=st['aux_dice']))
        per_seq[name] = rows_
        ok = [r for r in rows_ if r['quality'] != 'Poor']
        mean = lambda key, rs=rows_: float(np.mean([r[key] for r in rs]))
        summary['strategies'][name] = dict(
            ed_dice=mean('ed_dice'), es_dice=mean('es_dice'), es_dice_good_medium=mean('es_dice', ok),
            edes_dice=0.5 * (mean('ed_dice') + mean('es_dice')),
            fac_abs_err_pct=float(np.mean([abs(r['fac_hat'] - r['fac_ref']) for r in rows_]) * 100),
            fac_corr=float(np.corrcoef([r['fac_hat'] for r in rows_], [r['fac_ref'] for r in rows_])[0, 1]),
            keyframe_exact_ed=float(np.mean([r['ed_err'] == 0 for r in rows_])),
            keyframe_exact_es=float(np.mean([r['es_err'] == 0 for r in rows_])),
            keyframe_mean_err_frames=float(np.mean([r['ed_err'] + r['es_err'] for r in rows_]) / 2),
            jump_mean=mean('jump_mean'), jump_max_mean=mean('jump_max'), roughness=mean('roughness'),
            aux_dice_mid=mean('aux_dice_mid'))
    summary['labels'] = dict(jump_mean=float(np.mean([np.mean(s['jump']) for s in label_stats])),
                             jump_max_mean=float(np.mean([np.max(s['jump']) for s in label_stats])),
                             roughness=float(np.mean([s['roughness'] for s in label_stats])),
                             keyframe_exact_ed=float(np.mean([s['ed_hat'] == 0 for s in label_stats])),
                             keyframe_exact_es=float(np.mean([s['es_hat'] == len(o) - 1 for s, o in zip(label_stats, fwd_order)])))
    # per-transition jump distribution (95th percentile), strategies and labels
    for name, masks in strategies.items():
        js = [1 - dice128(m[t], m[t - 1]) for m in masks for t in range(1, len(m))]
        summary['strategies'][name]['jump_p95'] = float(np.percentile(js, 95))
    summary['labels']['jump_p95'] = float(np.percentile([j for s in label_stats for j in s['jump']], 95))

    # drift correction at ES: standard clicks with model 3 on the propagated ES mask
    es_idx = [s['es'] for s in index]
    gt128 = np.asarray(y512)[es_idx].reshape(-1, 128, 4, 128, 4).mean(axis=(2, 4)) >= 0.5
    predict = lambda rgbs: list(run3(np.stack(rgbs)) >= 1)
    d, _ = click_curve(predict, [img[o[-1]] for o in fwd_order], gt128, [m[-1] for m in fwd_l],
                       [cells[i] for i in es_idx], [rows[i] for i in es_idx], [cols[i] for i in es_idx], k_max=3)
    summary['fwd_es_clicks'] = [float(np.mean(v)) for v in d]
    summary['seconds'] = time.time() - t0
    summary['test_set_used'] = False

    np.savez_compressed(out_dir / 'fwd_record.npz', inputs=np.concatenate(fwd_in), masks=np.concatenate(fwd_l),
                        f1=f1, starts=np.array([s['start'] for s in index]), lengths=np.array([s['length'] for s in index]))
    (out_dir / 'per_sequence.json').write_text(json.dumps(per_seq), encoding='utf-8')
    (out_dir / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    keys = ('ed_dice', 'es_dice', 'es_dice_good_medium', 'fac_abs_err_pct', 'fac_corr', 'keyframe_exact_es',
            'jump_mean', 'jump_p95', 'roughness', 'aux_dice_mid')
    print('strategy ' + ' '.join(f'{k:>10s}' for k in keys))
    for name, r in summary['strategies'].items():
        print(f'{name:8s} ' + ' '.join(f'{r[k]:10.4f}' for k in keys))
    print('labels   ' + json.dumps(summary['labels']))
    print('fwd ES after 0..3 clicks ' + ' '.join(f'{v:.4f}' for v in summary['fwd_es_clicks']))


if __name__ == '__main__':
    main()
