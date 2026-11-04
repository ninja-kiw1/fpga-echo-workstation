"""Real-board check of the F2 click-correction model (model library entry echo_inter).

Replays the click protocol inputs recorded by quantize_inter.py (official
validation ED / ES frames, k = 0..5, from the F1 board result and from empty):
each 128x128 input is sent as its exact 4x nearest upsampling, so the board's
x[::4, ::4] recovers it bit for bit. Every board mask must equal the integer
reference mask, so the click curve measured ON THE BOARD equals the offline one.
Also reports the per-click board AI time and round trip.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_inter.py --tag inter2_r128 [--frames 200]
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone

import numpy as np

from common import PLD, RESULTS, ROOT, board_upsample, load_cache, seg_metrics

sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
import confidence  # noqa: E402
import imagelink   # noqa: E402
import proto       # noqa: E402

INTER = 3                        # echo_inter in the model library


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    ap.add_argument('--frames', type=int, default=200)
    args = ap.parse_args()
    qdir = RESULTS / 'quant' / args.tag
    summ = json.loads((qdir / 'summary.json').read_text(encoding='utf-8'))
    _, _, cells, rows, cols, meta = load_cache('val')
    link = imagelink.ImageLink('192.168.1.254', 5060, timeout=20)
    flags = proto.FLAG_STATS | proto.FLAG_ECHO_LOGIT | proto.FLAG_ECHO_MASK
    report = dict(utc=datetime.now(timezone.utc).isoformat(), tag=args.tag, frames=args.frames, modes={})
    ok = True
    for mode in ('from_f1', 'from_empty'):
        rgb = np.load(qdir / 'click_inputs' / f'{mode}_rgb.npy')
        masks = np.load(qdir / 'click_inputs' / f'{mode}_mask.npy')
        bad, ai, rt, curve = [], [], [], []
        t0 = time.time()
        for k in range(rgb.shape[0]):
            dice = []
            for i in range(args.frames):
                up = np.repeat(np.repeat(rgb[k, i], 4, 0), 4, 1)
                imgs, tim, _, st = link.round_trip(np.ascontiguousarray(up).tobytes(), mode=0, flags=flags,
                                                   aiparam=proto.make_aiparam(0, 1, 128, INTER))
                assert st['model'] == INTER and st['ai_res'] == 128, st
                plane = np.frombuffer(confidence.logit_plane(imgs['logit']), np.uint8).reshape(512, 512)
                m = (plane[::4, ::4].astype(np.int64) - proto.LOGIT_IMG_OFFSET) >= 1
                if not np.array_equal(m, masks[k, i]):
                    bad.append((k, i))
                dice.append(seg_metrics(board_upsample(m, 2), cells[i], rows[i], cols[i])['dice'])
                ai.append(st['ai_us'] / 1000)
                rt.append(tim['total_ms'])
            curve.append(float(np.mean(dice)))
            print(f'{mode} k={k}: board Dice {curve[-1]:.4f}, mismatches so far {len(bad)} ({time.time() - t0:.0f}s)', flush=True)
        offline = summ['curves'][mode]['quantized'] if args.frames == len(meta) else None
        same = offline is None or all(abs(a - b) < 1e-12 for a, b in zip(curve, offline))
        report['modes'][mode] = dict(board_curve=curve, offline_curve=offline, mismatching=bad[:20],
                                     ai_ms_mean=float(np.mean(ai)), round_trip_ms_mean=float(np.mean(rt)))
        ok &= (not bad) and same
    report['pass'] = bool(ok)
    out = ROOT / 'board_check'
    out.mkdir(exist_ok=True)
    (out / f'acceptance_inter_{args.tag}.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('INTER_BOARD_ACCEPTANCE_PASS' if ok else 'INTER_BOARD_ACCEPTANCE_FAIL', flush=True)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
