r"""Programs 21 cardiac_rv / 22 cardiac_ra integer logits (= the board's PL, package self-test + export_verify_rv.py)
on 112 x 112 clips through program 16's input chain (pad 8 to 128, R = G = B), window 112 kept, int8.
The GPU integer path (echonet/int8_gpu.py) is first checked equal to the CPU reference outputs of all 275 CardiacUDA
VAL frames (results/quant/<tag>/outputs) at 128.
  hmcqu       : every frame of the HMC-QU cache  -> results/cache/rv_q.npy, ra_q.npy
  echonet_val : every frame of the first --videos EchoNet VAL videos (sorted, = verify_board_echo_kf.py)
                -> results/echonet_val_rh.npz (rv, ra, names, starts)

    E:\PLD\skin\.venv\Scripts\python.exe -I rh_logits.py hmcqu | echonet_val [--videos 20]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
sys.path.insert(0, str(PLD / 'echonet'))
sys.path.insert(0, str(PLD / 'lwnet'))
sys.path.insert(0, str(PLD / 'cardiac_rv'))
from int8_gpu import IntegerModel  # noqa: E402

TAGS = {'rv': 'rv_pl1_5', 'ra': 'ra_pl1_5'}


def model(tag):
    qd = PLD / 'cardiac_rv' / 'results' / 'quant' / tag
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    im = IntegerModel(plan, arrays, 'cuda')
    from common_rv import load
    xva = load('val', json.loads((qd / 'summary.json').read_text())['target'])[0]
    x = np.repeat(np.asarray(xva)[:, None, ::4, ::4], 3, 1)
    out = np.concatenate([im(x[i:i + 64])[:, 0].to(torch.int8).cpu().numpy() for i in range(0, len(x), 64)])
    ref = np.stack([np.load(qd / 'outputs' / f'val_{i:03d}.npy') for i in range(len(x))])
    same = np.array_equal(out, ref)
    print(tag, 'GPU integer == CPU reference on all CardiacUDA VAL frames:', same, flush=True)
    if not same:
        sys.exit('GPU integer path differs')
    return im


def run(im, frames):
    x = np.pad(np.repeat(np.asarray(frames)[:, None], 3, 1), ((0, 0), (0, 0), (8, 8), (8, 8)))
    return np.concatenate([im(x[i:i + 128])[:, 0, 8:120, 8:120].to(torch.int8).cpu().numpy()
                           for i in range(0, len(x), 128)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('what', choices=('hmcqu', 'echonet_val'))
    ap.add_argument('--videos', type=int, default=20)
    a = ap.parse_args()
    ims = {k: model(t) for k, t in TAGS.items()}
    if a.what == 'hmcqu':
        f = np.load(HERE / 'results' / 'cache' / 'frames.npy')
        for k, im in ims.items():
            np.save(HERE / 'results' / 'cache' / f'{k}_q.npy', run(im, f))
        print('hmcqu', f.shape)
    else:
        import importlib.util
        spec = importlib.util.spec_from_file_location('echonet_common', PLD / 'echonet' / 'common.py')
        ec = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ec)
        frames, idx = ec.load_videos('val')
        names = sorted(idx)[:a.videos]
        rv, ra, starts, off = [], [], [], 0
        for n in names:
            e = idx[n]
            fr = np.asarray(frames[e['start']:e['start'] + e['n']])
            rv.append(run(ims['rv'], fr))
            ra.append(run(ims['ra'], fr))
            starts.append(off)
            off += len(fr)
        np.savez_compressed(HERE / 'results' / 'echonet_val_rh.npz', rv=np.concatenate(rv), ra=np.concatenate(ra),
                            names=np.array(names), starts=np.array(starts + [off]))
        print('echonet val', len(names), 'videos', off, 'frames')


if __name__ == '__main__':
    main()
