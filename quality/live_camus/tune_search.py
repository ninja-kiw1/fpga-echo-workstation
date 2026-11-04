"""Simulated view search, step 3 (user 11-02: 调一下对比度，使之不要这么突兀): find an off-target degradation for
patient0255 4CH that keeps program 16's LV (the volume curve / contour the screen shows) while the CAMUS light still
says the image is bad.

Grid: sideways shift dx (px) x contrast / gain severity s (sim_search.degrade1). Per setting on a 2 s 4CH stream:
  light      CAMUS chain on the board C (F1 every 5, eq_window, v5.26 model): share of green / yellow / red windows
  lv16       program 16 (t3 integer, the live engine's LV program) on the board 112 frames (screen x 3 -> crop 384 ->
             lc_crop_resize 112), clean112 area, mean relative to the clean clip (1.0 = same LV size)
Prints the grid; nothing is fitted (choice by eye between "LV kept" and "light red").

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I tune_search.py
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
os.add_dll_directory(r'C:\msys64\mingw64\bin')
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PLD / 'keyframe'))
sys.path.insert(0, str(PLD / 'echonet'))
import echo_core as ec  # noqa: E402
from degrade_test import model, sector_of, windows  # noqa: E402
from design import integer_model  # noqa: E402
from frame_ops import video_inputs  # noqa: E402
from int8_gpu import IntegerModel  # noqa: E402
from sim_search import clip, degrade1  # noqa: E402

PAD = 8
BOX = ec.LcBox(x=0, y=0, side=384)


def program16():
    qd = PLD / 'echonet' / 'results' / 'quant' / 't3'
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        return IntegerModel(plan, {k: z[k] for k in z.files}, 'cuda')


def lv16(im, f128):
    f112 = np.stack([ec.lc_crop_resize(np.kron(f, np.ones((3, 3), np.uint8)), BOX) for f in f128])
    with torch.no_grad():
        x = video_inputs(f112, 't3', 50.0)
        q = np.concatenate([im(torch.from_numpy(x[i:i + 128]))[:, 0, PAD:PAD + 112, PAD:PAD + 112].to(torch.int8).cpu().numpy()
                            for i in range(0, len(x), 128)])
    return np.array([ec.clean112(qq >= 1)[0].sum() for qq in q], float)


def main():
    f1, im, em = integer_model(PLD / 'echo' / 'results' / 'quant' / 'lv_r128'), program16(), model()
    c4 = clip('4CH', 100)
    sec = sector_of(c4)
    base = lv16(im, c4).mean()
    print('clean 4CH: program-16 LV area %.0f px' % base)
    res = []
    for dx in (0, 6, 10, 17):
        for s in (0.0, 0.3, 0.45, 0.6, 0.75, 0.9, 1.0):
            rng = np.random.default_rng(255)
            fr = np.stack([degrade1(f, s, dx, sec, rng) for f in c4])
            w = windows(fr, f1, em)
            lt = np.array([l for _, l, _ in w])
            a = lv16(im, fr)
            r = dict(dx=dx, s=s, green=float((lt == 0).mean()), yellow=float((lt == 1).mean()), red=float((lt == 2).mean()),
                     score=float(np.mean([sc for sc, _, _ in w])), lv16=float(a.mean() / base), lv16_min=float(a.min() / base))
            res.append(r)
            print('dx %2d s %.2f  light g/y/r %.2f/%.2f/%.2f (score %5.2f)  program-16 LV %.2f (min %.2f)' % (
                dx, s, r['green'], r['yellow'], r['red'], r['score'], r['lv16'], r['lv16_min']), flush=True)
    (HERE / 'results' / 'tune_search.json').write_text(json.dumps(res, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
