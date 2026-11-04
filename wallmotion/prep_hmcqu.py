"""HMC-QU A4C videos -> 112 x 112 grey clips in the EchoNet framing + model 16 integer logits (= the board).

Framing (no labels used): grey = BT.601 luma; sector = temporal max > 20, 7 x 7 opening (removes the thin ECG trace and
text), largest component; square of side max(w, h) centred on the sector box horizontally, top at the sector top
(apex up, like EchoNet), zero padded; INTER_AREA to 112. Model 16 = echonet/results/quant/t3 run by int8_gpu
(checked equal to the CPU reference on all EchoNet VAL ED/ES frames first, like echonet/infer_videos.py).
Writes results/cache/{frames.npy, q.npy, index.json} and results/crop_montage.png.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I prep_hmcqu.py
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLD = HERE.parent

VID = PLD / 'HMC-QU Dataset-Kaggle v1.0 (1)' / 'HMC-QU Dataset-Kaggle v1.0' / 'HMC-QU' / 'A4C'
OUT = HERE / 'results' / 'cache'


def read(path):
    c = cv2.VideoCapture(str(path))
    fps, fr = c.get(cv2.CAP_PROP_FPS), []
    while True:
        ok, x = c.read()
        if not ok:
            break
        fr.append(cv2.cvtColor(x, cv2.COLOR_BGR2GRAY))
    return np.stack(fr), fps


def sector_square(fr):
    m = (fr.max(0) > 20).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(v) for v in st[k, :4])
    side = max(w, h)
    return dict(x=x + w // 2 - side // 2, y=y, side=side, sector=[x, y, w, h])


def crop(fr, b):
    n, H, W = fr.shape
    s = b['side']
    canvas = np.zeros((n, s, s), np.uint8)
    x0, y0 = b['x'], b['y']
    sx0, sy0, sx1, sy1 = max(0, x0), max(0, y0), min(W, x0 + s), min(H, y0 + s)
    canvas[:, sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = fr[:, sy0:sy1, sx0:sx1]
    return np.stack([cv2.resize(f, (112, 112), interpolation=cv2.INTER_AREA) for f in canvas])


def frames_stage():
    import cv2
    globals()['cv2'] = cv2
    labels = json.loads((HERE / 'results' / 'labels_a4c.json').read_text())['rows']
    OUT.mkdir(parents=True, exist_ok=True)
    frames, index, off, tiles = [], {}, 0, []
    for r in labels:
        fr, fps = read(VID / (r['name'] + '.avi'))
        b = sector_square(fr)
        v = crop(fr, b)
        frames.append(v)
        index[r['name']] = dict(start=off, n=len(v), fps=fps, box=b, src=list(fr.shape[1:]))
        off += len(v)
        if len(tiles) < 40:
            tiles.append(v[0])
    np.save(OUT / 'frames.npy', np.concatenate(frames))
    (OUT / 'index.json').write_text(json.dumps(index, indent=1), encoding='utf-8')
    cv2.imwrite(str(HERE / 'results' / 'crop_montage.png'),
                np.vstack([np.hstack(tiles[i:i + 10]) for i in range(0, len(tiles), 10)]))
    print(f'{len(index)} videos, {off} frames')


def infer_stage():
    import torch
    sys.path.insert(0, str(PLD / 'echonet'))
    sys.path.insert(0, str(PLD / 'lwnet'))
    from common import PAD, load_frames
    from frame_ops import video_inputs
    from int8_gpu import IntegerModel
    from train_echonet import to_input
    qd = PLD / 'echonet' / 'results' / 'quant' / 't3'
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    mode = json.loads((qd / 'summary.json').read_text(encoding='utf-8'))['input']
    im = IntegerModel(plan, arrays, 'cuda')

    def run_q(x):
        return np.concatenate([im(x[i:i + 128])[:, 0, PAD:PAD + 112, PAD:PAD + 112].to(torch.int8).cpu().numpy()
                               for i in range(0, len(x), 128)])

    xva, _, _ = load_frames('val')
    same = np.array_equal(run_q(to_input(np.asarray(xva), mode)), np.load(qd / 'val_outputs.npy'))
    print('GPU integer model == CPU reference on all EchoNet VAL ED/ES frames:', same, flush=True)
    if not same:
        sys.exit('GPU integer path differs from the CPU reference')
    index = json.loads((OUT / 'index.json').read_text(encoding='utf-8'))
    frames = np.load(OUT / 'frames.npy')
    q = np.zeros(frames.shape, np.int8)
    for e in index.values():
        s = slice(e['start'], e['start'] + e['n'])
        q[s] = run_q(video_inputs(frames[s], mode, e['fps']))
    np.save(OUT / 'q.npy', q)
    print('q', q.shape, 'mask fraction', float((q >= 1).mean()))


if __name__ == '__main__':
    frames_stage() if sys.argv[1] == 'frames' else infer_stage()
