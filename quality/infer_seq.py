"""Innovation ② step 1: board-equivalent integer outputs of F1 (lv_r128) and program 17 (la_r128) on every frame of
the CAMUS half sequences (TRAIN + VAL; TEST not touched), from echo/data/cache/seq_{split}_img128.npy (F1 chain:
host_stretch 512 -> [::4]).

Runs echonet/int8_gpu.IntegerModel (exact integer arithmetic on the GPU) and first checks it value by value against
the CPU reference outputs saved by echo/quantize_echo.py (VAL ED / ES frames, results/quant/<tag>/outputs/val_*.npy),
matching each sequence's first / last frame to the cached ED / ES frame of the same patient and view.
Writes results/seq_{split}_{tag}_q.npy (int8 logits [N, 128, 128]).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I infer_seq.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
sys.path.insert(0, str(PLD / 'echonet'))
from int8_gpu import IntegerModel  # noqa: E402

ECHO = PLD / 'echo'
CACHE = ECHO / 'data' / 'cache'
OUT = HERE / 'results'


def model(tag):
    qd = ECHO / 'results' / 'quant' / tag
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    im = IntegerModel(plan, arrays, 'cuda')

    def run(x128):
        out = []
        for i in range(0, len(x128), 128):
            xb = torch.from_numpy(np.repeat(np.ascontiguousarray(x128[i:i + 128])[:, None], 3, 1))
            out.append(im(xb)[:, 0].to(torch.int8).cpu().numpy())
        return np.concatenate(out)
    return run


def main():
    OUT.mkdir(exist_ok=True)
    meta = json.loads((CACHE / 'val_meta.json').read_text(encoding='utf-8'))
    x512 = np.load(CACHE / 'val_img512.npy', mmap_mode='r')
    for tag in ('lv_r128', 'la_r128'):
        run = model(tag)
        # check: GPU integer model == CPU reference on the 200 VAL ED / ES frames
        ref = np.stack([np.load(ECHO / 'results' / 'quant' / tag / 'outputs' / f'val_{i:03d}.npy') for i in range(len(meta))])
        got = run(np.asarray(x512)[:, ::4, ::4])
        same = np.array_equal(got, ref)
        print(tag, 'GPU integer == CPU reference on 200 VAL ED/ES frames:', same, flush=True)
        if not same:
            sys.exit('GPU integer path differs')
        for split in ('train', 'val'):
            x = np.load(CACHE / f'seq_{split}_img128.npy', mmap_mode='r')
            q = run(x)
            np.save(OUT / f'seq_{split}_{tag}_q.npy', q)
            print(tag, split, q.shape, flush=True)
        # the sequence ED / ES frames are the same images as the cached ED / ES frames
        ix = json.loads((CACHE / 'seq_val_index.json').read_text(encoding='utf-8'))
        sq = np.load(OUT / f'seq_val_{tag}_q.npy')
        mi = {(m['patient'], m['view'], m['phase']): i for i, m in enumerate(meta)}
        eq = [np.array_equal(sq[e['start']], ref[mi[(e['patient'], e['view'], 'ED')]]) for e in ix]
        print(tag, 'sequence first frame == cached ED frame output:', sum(eq), '/', len(eq), flush=True)


if __name__ == '__main__':
    main()
