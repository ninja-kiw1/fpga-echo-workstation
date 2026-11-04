"""Board-equivalent LV masks on every frame of the validation half sequences, for F4 (EF).

  f1   F1 (model 2) on every frame independently, integer model (= board)
  fwd  F3 forward propagation from F1 at ED (model 4), integer, as recorded by quantize_seq.py
       and reproduced step for step on the board by verify_board_f3.py
  c1   the interactive workflow with ONE click: on the ED frame, one standard click (centre of
       the largest error vs the expert ED label, model 3 on the F1 result), then model 4 forward
  c2   c1 plus one standard click on the last (ES) frame against the expert ES label
The clicks simulate a user with the standard protocol of F2 (train_inter.next_click).
Saved at the 128 grid; eval_f4.py upsamples them exactly like the UI does (x4 repeat to
512, Pillow NEAREST to the original size). Test split untouched.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe gen_f4_masks.py [--seq-tag seq_none_ex]
"""
import argparse
import json

import numpy as np
import torch

from common import RESULTS, load_cache
from compare_f3 import int_predict
from seqeval import load_val_seq, propagate
from train_inter import click_maps, next_click


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--seq-tag', default='seq_none_ex')
    args = ap.parse_args()
    torch.set_num_threads(8)
    V = load_val_seq()
    f1 = int_predict('lv_r128')(np.repeat(V['img'][..., None], 3, -1))
    rec = RESULTS / 'quant' / args.seq_tag / 'seq_inputs'
    start, steps = np.load(rec / 'fwd_start_mask.npy'), np.load(rec / 'fwd_mask.npy')
    lengths = np.load(rec / 'lengths.npy')
    offs = np.concatenate([[0], np.cumsum(lengths - 1)])
    fwd = np.zeros_like(f1)
    for k, s in enumerate(V['index']):
        assert s['length'] == lengths[k]
        fwd[s['start']] = start[k]
        fwd[s['start'] + 1:s['start'] + s['length']] = steps[offs[k]:offs[k + 1]]
        assert np.array_equal(start[k], f1[s['start']]), 'forward start must be the F1 result'
    # interactive workflow: one click on ED, propagate, (one click on ES)
    _, y512, *_ = load_cache('val')
    gt128 = lambda i: np.asarray(y512[i]).reshape(128, 4, 128, 4).mean(axis=(1, 3)) >= 0.5
    p3, p4 = int_predict('inter2_r128'), int_predict(args.seq_tag)

    def click_once(img, mask, gt):
        c = next_click(mask, gt)
        if c is None:
            return mask
        rgb = np.stack([img, mask.astype(np.uint8) * 255, click_maps([c])], -1)
        return p3(rgb[None])[0]
    starts = [click_once(V['img'][s['start']], f1[s['start']], gt128(s['ed'])) for s in V['index']]
    orders = [np.arange(s['start'], s['start'] + s['length']) for s in V['index']]
    prop, _ = propagate(p4, V['img'], orders, starts, 'none')
    c1, c2 = np.zeros_like(f1), np.zeros_like(f1)
    for k, s in enumerate(V['index']):
        c1[s['start']:s['start'] + s['length']] = prop[k]
        c2[s['start']:s['start'] + s['length']] = prop[k]
        last = s['start'] + s['length'] - 1
        c2[last] = click_once(V['img'][last], prop[k][-1], gt128(s['es']))
    print('clicked sequences done', flush=True)
    meta = [dict(patient=s['patient'], view=s['view'], start=s['start'], length=s['length'],
                 width=V['meta'][s['ed']]['width'], height=V['meta'][s['ed']]['height'],
                 quality=V['meta'][s['ed']]['quality']) for s in V['index']]
    out = RESULTS / 'f4'
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / 'val_masks128.npz', f1=f1, fwd=fwd, c1=c1, c2=c2)
    (out / 'val_masks128_index.json').write_text(json.dumps(dict(seq_tag=args.seq_tag, sequences=meta)), encoding='utf-8')
    print('saved', f1.shape, 'frames;', len(meta), 'sequences')


if __name__ == '__main__':
    main()
