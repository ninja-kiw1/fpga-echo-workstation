"""Real-board check of F3: closed-loop propagation ON THE BOARD over the validation sequences.

For every official validation half sequence, forward (ED -> ES) and backward (ES -> ED):
step 0 sends the frame to model 2 (echo_lv, F1); every later step builds the propagation
input from the BOARD's previous mask (R frame t, G board mask of the previous step,
B previous frame) and sends it to the propagation model. Each 128x128 input goes out as
its exact 4x nearest upsampling, so the board's x[::4, ::4] recovers it bit for bit.
Every board mask must equal the integer reference recorded by quantize_seq.py, so the
board's own loop reproduces the offline ED / ES Dice, FAC and jump numbers.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_f3.py --tag seq_prev [--seqs 100]
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone

import numpy as np

from common import PLD, RESULTS, ROOT
from seqeval import encode, load_val_seq, sequence_metrics, summarize

sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
import confidence  # noqa: E402
import imagelink   # noqa: E402
import proto       # noqa: E402

AUTO, SEQ = 2, 4


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    ap.add_argument('--seqs', type=int, default=100)
    args = ap.parse_args()
    qdir = RESULTS / 'quant' / args.tag
    summ = json.loads((qdir / 'summary.json').read_text(encoding='utf-8'))
    enc = summ['encoding']
    V = load_val_seq()
    lengths = np.load(qdir / 'seq_inputs' / 'lengths.npy')
    link = imagelink.ImageLink('192.168.1.254', 5060, timeout=20)
    flags = proto.FLAG_STATS | proto.FLAG_ECHO_LOGIT | proto.FLAG_ECHO_MASK
    ai, rt = {AUTO: [], SEQ: []}, {AUTO: [], SEQ: []}

    def board(rgb128, model):
        up = np.ascontiguousarray(np.repeat(np.repeat(rgb128, 4, 0), 4, 1))
        imgs, tim, _, st = link.round_trip(up.tobytes(), mode=0, flags=flags,
                                           aiparam=proto.make_aiparam(0, 1, 128, model))
        assert st['model'] == model and st['ai_res'] == 128, st
        plane = np.frombuffer(confidence.logit_plane(imgs['logit']), np.uint8).reshape(512, 512)
        full = (plane.astype(np.int64) - proto.LOGIT_IMG_OFFSET) >= 1
        mask_img = np.frombuffer(imgs['mask'], np.uint8).reshape(512, 512, 3)[:, :, 0] > 0
        assert np.array_equal(full, mask_img), 'board mask image differs from its logit >= 1'
        ai[model].append(st['ai_us'] / 1000)
        rt[model].append(tim['total_ms'])
        return full[::4, ::4]

    report = dict(utc=datetime.now(timezone.utc).isoformat(), tag=args.tag, encoding=enc, seqs=args.seqs, dirs={})
    ok = True
    t0 = time.time()
    n = min(args.seqs, len(V['index']))
    for d in ('fwd', 'bwd'):
        ref = np.load(qdir / 'seq_inputs' / f'{d}_mask.npy')
        ref_start = np.load(qdir / 'seq_inputs' / f'{d}_start_mask.npy')
        offs = np.concatenate([[0], np.cumsum(lengths - 1)])
        masks, bad = [], []
        for k, s in enumerate(V['index'][:n]):
            order = np.arange(s['start'], s['start'] + s['length'])
            if d == 'bwd':
                order = order[::-1]
            m = [board(np.repeat(V['img'][order[0]][..., None], 3, -1), AUTO)]
            if not np.array_equal(m[0], ref_start[k]):
                bad.append((k, 0))
            for t in range(1, len(order)):
                m.append(board(encode(enc, V['img'][order[t]], m[-1], V['img'][order[t - 1]]), SEQ))
                if not np.array_equal(m[-1], ref[offs[k] + t - 1]):
                    bad.append((k, t))
            m = np.stack(m)
            masks.append(m if d == 'fwd' else m[::-1])
            if (k + 1) % 10 == 0:
                print(f'{d}: {k + 1}/{n} sequences, mismatching steps {len(bad)} ({time.time() - t0:.0f}s)', flush=True)
        sub = dict(V, index=V['index'][:n])
        board_summary = summarize(sequence_metrics(sub, masks))
        offline = summ['quantized'][d] if n == len(V['index']) else None
        same = offline is None or all(abs(board_summary[k] - offline[k]) < 1e-12
                                      for k in ('ed_dice', 'es_dice', 'fac_abs_err_pct', 'jump_mean'))
        report['dirs'][d] = dict(board=board_summary, offline=offline, mismatching=bad[:20], steps=int(sum(len(x) for x in masks)))
        ok &= (not bad) and same
    report['timing_ms'] = {proto.MODELS[m]['name'] if m < len(proto.MODELS) else str(m):
                           dict(ai_mean=float(np.mean(ai[m])), round_trip_mean=float(np.mean(rt[m])), frames=len(ai[m]))
                           for m in (AUTO, SEQ)}
    report['pass'] = bool(ok)
    out = ROOT / 'board_check'
    out.mkdir(exist_ok=True)
    (out / f'acceptance_f3_{args.tag}.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    for d, r in report['dirs'].items():
        b = r['board']
        print(f"{d}: board ED {b['ed_dice']:.4f} ES {b['es_dice']:.4f} FAC err {b['fac_abs_err_pct']:.2f} "
              f"jump {b['jump_mean']:.4f}; mismatching {len(r['mismatching'])}")
    print(json.dumps(report['timing_ms']))
    print('F3_BOARD_ACCEPTANCE_PASS' if ok else 'F3_BOARD_ACCEPTANCE_FAIL', flush=True)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
