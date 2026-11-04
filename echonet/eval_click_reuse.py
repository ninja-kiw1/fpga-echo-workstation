"""③ click correction on EchoNet with the program already on the board: can the CAMUS click model (model 3 echo_inter,
trained only on CAMUS) correct the EchoNet model's masks? No retraining -- a cross-dataset reuse experiment.

Protocol = F2's (echo/train_inter.py): start from the prior (EchoNet t3 integer output), each step clicks the centre of
the largest error component (positive if it is missed LV, else negative), the output becomes the next prior.
Input of model 3: R = the frame (112 grey padded to 128), G = prior mask 0 / 255, B = signed click cones (radius 10).
Both models integer (int8_gpu, checked against the CPU reference). VAL ED + ES frames, Dice on the native 112 grid.
Writes results/click_reuse.json.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I eval_click_reuse.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PLD / 'lwnet'))

import numpy as np          # noqa: E402
import torch                # noqa: E402
from scipy import ndimage   # noqa: E402

from common import PAD, RESULTS, load_frames   # noqa: E402
from int8_gpu import IntegerModel   # noqa: E402
from train_echonet import dice_batch   # noqa: E402

R128, K = 10.0, 5
INTER = PLD / 'echo' / 'results' / 'quant' / 'inter2_r128'


def next_click(pred, gt):
    err = pred != gt
    if not err.any():
        return None
    lab, n = ndimage.label(err)
    sizes = ndimage.sum(err, lab, range(1, n + 1))
    comp = lab == (int(np.argmax(sizes)) + 1)
    dt = ndimage.distance_transform_edt(np.pad(comp, 1))[1:-1, 1:-1]
    y, x = np.unravel_index(int(np.argmax(dt)), dt.shape)
    return y, x, bool(gt[y, x])


def click_maps(clicks, size=128):
    pos = np.zeros((size, size), np.float32)
    neg = np.zeros((size, size), np.float32)
    yy, xx = np.mgrid[0:size, 0:size]
    for y, x, positive in clicks:
        c = np.clip(1 - np.sqrt((yy - y) ** 2 + (xx - x) ** 2) / R128, 0, None)
        if positive:
            pos = np.maximum(pos, c)
        else:
            neg = np.maximum(neg, c)
    return np.clip(np.round(128 + 127 * pos - 128 * neg), 0, 255).astype(np.uint8)


def load(qd):
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        return IntegerModel(plan, {k: z[k] for k in z.files})


def run(im, x):
    with torch.no_grad():
        return np.concatenate([im(x[i:i + 128])[:, 0].to(torch.int8).cpu().numpy() for i in range(0, len(x), 128)])


def main():
    xva, yva, mva = load_frames('val')
    img = np.pad(np.asarray(xva)[:, 1], ((0, 0), (PAD, PAD), (PAD, PAD)))          # frame t, padded
    gt = np.pad(np.asarray(yva).astype(bool), ((0, 0), (PAD, PAD), (PAD, PAD)))
    gt112 = gt[:, PAD:PAD + 112, PAD:PAD + 112]
    prior = np.pad(np.load(RESULTS / 'quant' / 't3' / 'val_outputs.npy') >= 1, ((0, 0), (PAD, PAD), (PAD, PAD)))
    im3 = load(INTER)
    clicks = [[] for _ in range(len(img))]
    curve, start = [], float(dice_batch(prior[:, PAD:PAD + 112, PAD:PAD + 112], gt112).mean())
    print('start (EchoNet model alone): Dice %.4f' % start, flush=True)
    for k in range(K + 1):
        x = np.stack([img, prior.astype(np.uint8) * 255, np.stack([click_maps(c) for c in clicks])], 1)
        pred = run(im3, x) >= 1
        d = dice_batch(pred[:, PAD:PAD + 112, PAD:PAD + 112], gt112)
        curve.append(dict(clicks=k, dice=float(d.mean()), p05=float(np.percentile(d, 5)), above_090=float(np.mean(d >= 0.9))))
        print(curve[-1], flush=True)
        for i in range(len(img)):
            p = pred[i].copy()
            p[:PAD] = p[-PAD:] = False
            p[:, :PAD] = p[:, -PAD:] = False                     # clicks only inside the real 112 image
            c = next_click(p, gt[i])
            if c is not None:
                clicks[i].append(c)
        prior = pred
    out = dict(model='CAMUS echo_inter (model 3), unchanged', start_dice=start, curve=curve, frames=len(img),
               note='VAL ED + ES frames; protocol of F2 (largest error component centre)')
    (RESULTS / 'click_reuse.json').write_text(json.dumps(out, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
