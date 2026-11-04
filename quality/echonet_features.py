"""Innovation ② step 4: the same quality features on EchoNet whole videos (VAL + the 1,500-video TRAIN subset; TEST not
touched), from program 16's integer logits (t3 encoding, board chain 112 -> pad 128 -> crop 112), recomputed with
echonet/int8_gpu.IntegerModel because only the cleaned masks were cached. Consistency check: clean(q >= 1) must equal
the cached mask (echonet/results/frames/t3_int_<split>.npz) on every frame.
Writes results/echonet_quality_<split>.json {video: features}.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I echonet_features.py
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PLD / 'echonet'))
from features import sequence_features  # noqa: E402
from frame_ops import video_inputs  # noqa: E402
from int8_gpu import IntegerModel  # noqa: E402
from volume import clean  # noqa: E402

E = PLD / 'echonet'
OUT = HERE / 'results'
PAD = 8


def main():
    qd = E / 'results' / 'quant' / 't3'
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        im = IntegerModel(plan, {k: z[k] for k in z.files}, 'cuda')
    for split in ('val', 'trainsub'):
        t0 = time.time()
        idx = json.loads((E / 'results' / 'frames' / f't3_int_{split}.json').read_text(encoding='utf-8'))['videos']
        vid = json.loads((E / 'data' / 'cache' / f'{split}_videos.json').read_text(encoding='utf-8'))
        frames = np.load(E / 'data' / 'cache' / f'{split}_videos.npy', mmap_mode='r')
        packed = np.load(E / 'results' / 'frames' / f't3_int_{split}.npz')['masks']
        out, mismatch = {}, 0
        for k, v in enumerate(sorted(idx)):
            e, ev = idx[v], vid[v]
            fr = np.ascontiguousarray(frames[ev['start']:ev['start'] + ev['n']])
            x = video_inputs(fr, 't3', e['fps'])
            q = np.concatenate([im(torch.from_numpy(x[i:i + 128]))[:, 0, PAD:PAD + 112, PAD:PAD + 112].to(torch.int8).cpu().numpy()
                                for i in range(0, len(x), 128)])
            cached = np.unpackbits(packed[e['start']:e['start'] + e['n']], axis=1)[:, :112 * 112].reshape(-1, 112, 112).astype(bool)
            if k % 50 == 0:     # spot check every 50th video, every frame
                mismatch += sum(not np.array_equal(clean(qq >= 1) if (qq >= 1).any() else qq >= 1, c) for qq, c in zip(q, cached))
            f, _ = sequence_features(fr, q, list(range(len(fr))))
            out[v] = dict(fps=e['fps'], ef=e['ef'], n=e['n'], **f)
            if (k + 1) % 200 == 0:
                print(f'{split} {k + 1}/{len(idx)} ({time.time() - t0:.0f} s), mask mismatches so far {mismatch}', flush=True)
        (OUT / f'echonet_quality_{split}.json').write_text(json.dumps(dict(mask_mismatch_spot_check=mismatch, videos=out)),
                                                           encoding='utf-8')
        print(f'{split}: {len(out)} videos, spot-check mask mismatches {mismatch} ({time.time() - t0:.0f} s)', flush=True)


if __name__ == '__main__':
    main()
