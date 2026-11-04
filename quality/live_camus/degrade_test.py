"""Simulated view search, step 1: does the CAMUS-calibrated light react to synthetic "probe not placed right" degradations,
and through which features? (The light was fitted on real expert grades; its response to these synthetic changes is NOT
validated by those grades -- this only checks the direction before using them in a demo source.)

Degradations of a 128 CAMUS frame (severity s in [0, 1]):
  shift     anatomy moved sideways inside the fixed sector by 28 s px (probe slid; what comes into view = dim speckle),
            pushes the LV toward the sector edge
  contrast  gain / contrast down: grey = (mean + (grey - mean) * (1 - 0.65 s)) * (1 - 0.35 s)
  noise     speckle-like multiplicative + additive noise (sigma 0.45 s, 18 s grey), fixed seed per frame
  all       the three together (what the demo source uses)
Data: every CAMUS VAL 4CH Good clip (TEST untouched), streams as design.py (2 s, 50 fps), F1 every 5 frames, 1 s windows
every 10 frames, board C (echo_core eq_clean / eq_window) with the v5.26 model. Per type x severity: mean score,
light shares, and the mean standardized contribution of each feature (which features move the score).
Writes degrade_test.json.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I degrade_test.py
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
os.add_dll_directory(r'C:\msys64\mingw64\bin')
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PLD / 'keyframe'))
import echo_core as ec  # noqa: E402
from design import CACHE, FEATURES, QC, WIN, EVERY, integer_model, streams  # noqa: E402

MODEL = json.loads((HERE / 'camus_live_model.json').read_text(encoding='utf-8'))
K = MODEL['f1_every']
SEV = (0.0, 0.25, 0.5, 0.75, 1.0)


def sector_of(fr):
    m = fr.max(0) > 4
    lab, n = ndimage.label(m)
    if n > 1:
        m = lab == 1 + int(np.argmax(ndimage.sum(m, lab, range(1, n + 1))))
    return ndimage.binary_fill_holes(m)


def degrade(fr, kind, s, sector, seed=0):
    """fr [T, 128, 128] uint8 -> degraded copy (sector kept fixed)"""
    if s <= 0:
        return fr.copy()
    x = fr.astype(np.float32)
    rng = np.random.default_rng(seed)
    if kind in ('shift', 'all'):
        dx = int(round(28 * s))
        x = np.roll(x, dx, axis=2)
        x[:, :, :dx] = 0
        hole = sector[None] & (x <= 0)
        x = np.where(hole, rng.gamma(2.0, 6.0, x.shape).astype(np.float32), x)
    if kind in ('contrast', 'all'):
        mu = x[:, sector].mean()
        x = (mu + (x - mu) * (1 - 0.65 * s)) * (1 - 0.35 * s)
    if kind in ('noise', 'all'):
        x = x * (1 + 0.45 * s * rng.standard_normal(x.shape).astype(np.float32)) + 18 * s * rng.standard_normal(x.shape).astype(np.float32)
    x = np.where(sector[None], x, 0)
    return np.clip(np.rint(x), 0, 255).astype(np.uint8)


def model():
    e = ec.EqModel()
    e.w[:] = [float(a) for a in MODEL['w']]
    e.mu[:] = [float(a) for a in MODEL['mu']]
    e.sd[:] = [float(a) for a in MODEL['sd']]
    e.t_green, e.t_red = float(MODEL['t_green']), float(MODEL['t_red'])
    return e


def windows(fr, f1, em):
    ts = np.arange(0, len(fr), K)
    q = f1(fr[ts])
    cm, nc = zip(*[ec.eq_clean(qq >= 1) for qq in q])
    cm, nc = np.stack(cm), np.array(nc, np.int32)
    out = []
    for t in range(WIN - 1, len(fr), EVERY):
        a = t - WIN + 1
        js = [j for j, tt in enumerate(ts) if a <= tt <= t]
        o = ec.eq_window(fr[a:t + 1], [int(ts[j]) - a for j in js], q[js], cm[js], nc[js], em)
        z = [(o['f'][f] - MODEL['mu'][i]) / MODEL['sd'][i] * MODEL['w'][i + 1] for i, f in enumerate(FEATURES)]
        out.append((o['score'], o['light'], z))
    return out


def main():
    f1 = integer_model(PLD / 'echo' / 'results' / 'quant' / 'lv_r128')
    em = model()
    good = {r['patient'] for r in QC['val_rows'] if r['view'] == '4CH' and r['quality'] == 'Good'}
    clips = [(e['patient'], fr) for e, fr, _, _ in streams('val') if e['view'] == '4CH' and e['patient'] in good]
    print('VAL 4CH Good clips:', len(clips), flush=True)
    res = {}
    for kind in ('shift', 'contrast', 'noise', 'all'):
        for s in SEV:
            sc, lt, zz = [], [], []
            for i, (p, fr) in enumerate(clips):
                for score, light, z in windows(degrade(fr, kind, s, sector_of(fr), seed=i), f1, em):
                    sc.append(score)
                    lt.append(light)
                    zz.append(z)
            lt = np.array(lt)
            zm = np.mean(zz, 0)
            res[f'{kind}_{s}'] = dict(score=float(np.mean(sc)), green=float((lt == 0).mean()), yellow=float((lt == 1).mean()),
                                      red=float((lt == 2).mean()), contrib=dict(zip(FEATURES, zm.round(2).tolist())))
            top = sorted(zip(FEATURES, zm), key=lambda a: -a[1])[:3]
            print('%-8s s=%.2f score %6.2f  g/y/r %.2f/%.2f/%.2f  top %s' % (
                kind, s, np.mean(sc), (lt == 0).mean(), (lt == 1).mean(), (lt == 2).mean(),
                ', '.join('%s %+.2f' % (f, v) for f, v in top)), flush=True)
    (HERE / 'degrade_test.json').write_text(json.dumps(res, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
