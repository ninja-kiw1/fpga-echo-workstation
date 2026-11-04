"""Program 16's integer logits (112 x 112 crop, int8) for every frame of EchoNet VAL and the TRAIN subset, stored once
(results/q/t3_<split>_q112.npy, memmap; EchoNet-derived -> excluded from sharing) so the board-C quality / interval
evaluation (quality_kf.py) can re-run in minutes. Same check as echonet_features.py: clean(q >= 1) == the cached mask
on every 50th video.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I echonet_q.py
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
sys.path.insert(0, str(PLD / 'echonet'))
from frame_ops import video_inputs  # noqa: E402
from int8_gpu import IntegerModel  # noqa: E402
from volume import clean  # noqa: E402

E = PLD / 'echonet'
OUT = HERE / 'results' / 'q'
PAD = 8


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--split', choices=('test',), help='10-25: only after echonet/final_test_echonet.py wrote its lock')
    a = ap.parse_args()
    if a.split == 'test' and not (E / 'results' / 'final_test' / 'test_set_used.txt').exists():
        sys.exit('TEST logits only after echonet/final_test_echonet.py wrote its lock')
    OUT.mkdir(parents=True, exist_ok=True)
    qd = E / 'results' / 'quant' / 't3'
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        im = IntegerModel(plan, {k: z[k] for k in z.files}, 'cuda')
    for split in ([a.split] if a.split else ['val', 'trainsub']):
        t0 = time.time()
        idx = json.loads((E / 'results' / 'frames' / f't3_int_{split}.json').read_text(encoding='utf-8'))['videos']
        vid = json.loads((E / 'data' / 'cache' / f'{split}_videos.json').read_text(encoding='utf-8'))
        frames = np.load(E / 'data' / 'cache' / f'{split}_videos.npy', mmap_mode='r')
        packed = np.load(E / 'results' / 'frames' / f't3_int_{split}.npz')['masks']
        total = sum(e['n'] for e in idx.values())
        out = np.lib.format.open_memmap(OUT / f't3_{split}_q112.npy', mode='w+', dtype=np.int8, shape=(total, 112, 112))
        bad = 0
        for k, v in enumerate(sorted(idx)):
            e, ev = idx[v], vid[v]
            fr = np.ascontiguousarray(frames[ev['start']:ev['start'] + ev['n']])
            x = video_inputs(fr, 't3', e['fps'])
            q = np.concatenate([im(torch.from_numpy(x[i:i + 128]))[:, 0, PAD:PAD + 112, PAD:PAD + 112].to(torch.int8).cpu().numpy()
                                for i in range(0, len(x), 128)])
            out[e['start']:e['start'] + e['n']] = q
            if k % 50 == 0:
                cached = np.unpackbits(packed[e['start']:e['start'] + e['n']], axis=1)[:, :112 * 112].reshape(-1, 112, 112).astype(bool)
                bad += sum(not np.array_equal(clean(qq >= 1) if (qq >= 1).any() else qq >= 1, c) for qq, c in zip(q, cached))
        out.flush()
        print(f'{split}: {len(idx)} videos, {total} frames, spot-check mismatches {bad} ({time.time() - t0:.0f} s)', flush=True)


if __name__ == '__main__':
    main()
