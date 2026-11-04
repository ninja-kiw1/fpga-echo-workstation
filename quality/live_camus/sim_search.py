"""Simulated view search on ONE patient (user 11-02: 模拟医生找切面的过程) -> board live source CAMUS_view_search_z3.y8.

Patient: CAMUS VAL, the median-score 4CH Good clip (pre-set rule, same as scan_sim) = patient0255 (2CH Good too).
Script at 50 fps (CAMUS half cycles played back and forth from their own FrameRate, as design.py):
  0.0- 2.0 s  2CH                                  probe at the wrong angle (good image, not A4C)
  2.0 s       cut to the degraded 4CH                 probe rotated (a 1 s 2CH -> 4CH blend was tried first: the
                                                    blend of two different images made the board tracker lose the
                                                    LV, 6 keyframes in a row, P99 111 ms -- not a real image change)
  2.0- 6.0 s  4CH, severity ~1 with a slow wobble   searching: probe slid a little sideways + gain slightly down
  6.0- 9.0 s  4CH, severity 1 -> 0                  adjusting
  9.0-13.0 s  4CH, severity 0                       on target, probe still
Degradation (degrade_test.py: the light reacts monotonically to these two; noise was dropped -- no consistent
reaction; sideways shift only up to 0.6 of the tested range -- beyond, the light turns back green):
  anatomy shifted sideways by DX_MAX r px inside the fixed sector, contrast / gain down by S_MAX r (r = 1 while searching,
  1 -> 0 while adjusting). 11-02 user: the first version (17 px, contrast to the bottom) made program 16 lose the LV
  (volume curve to ~0, too abrupt) -> tune_search.py grid: 6 px + 0.15 = yellow light 100 % with program 16 keeping
  69 % of the LV (user's choice A; a red light only came with the LV at <= 46 %). No wobble: at 6-9 px the light
  flips between green and yellow.
The degraded frames are SYNTHETIC: the light was fitted on real expert grades, not on these.
Preview (no hold): the board chain on the PC (F1 integer + echo_core eq_window, RV integer + median-of-3 rule) ->
results/view_search_preview.json; then the .y8 (zoom x 3 in a 640 x 480 screen, crop 128 48 384 = the 128 frames bit
for bit) + live/src/index.json entry ("quality": "camus", phases).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I sim_search.py
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
os.add_dll_directory(r'C:\msys64\mingw64\bin')
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PLD / 'keyframe'))
sys.path.insert(0, str(PLD / 'live'))
import echo_core as ec  # noqa: E402
from design import CACHE, FPS, fps_of, integer_model  # noqa: E402
from degrade_test import model, sector_of  # noqa: E402
from make_y8 import write_y8  # noqa: E402

MODEL = json.loads((HERE / 'camus_live_model.json').read_text(encoding='utf-8'))
K, WIN, EVERY = MODEL['f1_every'], MODEL['win'], MODEL['every']
PATIENT = 'patient0255'
NAME = 'CAMUS_view_search_z3.y8'
PHASES = [('2CH', 0, 100), ('search', 100, 300), ('adjust', 300, 450), ('on_target', 450, 650)]
SAY = {'2CH': '探头角度不对（两腔）', 'search': '转到四腔但没放好：稍偏 + 增益略低',
       'adjust': '慢慢调好', 'on_target': '到位不动'}
Z, X, Y = 3, 128, 48
DX_MAX, S_MAX = 6, 0.15


def clip(view, n):
    ix = json.loads((CACHE / 'seq_val_index.json').read_text(encoding='utf-8'))
    x = np.load(CACHE / 'seq_val_img128.npy', mmap_mode='r')
    e = next(e for e in ix if e['patient'] == PATIENT and e['view'] == view)
    n0, fps = e['length'], fps_of(PATIENT, view)
    cyc = list(range(n0)) + list(range(n0 - 2, 0, -1))
    return np.asarray(x)[[e['start'] + cyc[int(t / FPS * fps) % len(cyc)] for t in range(n)]]


def degrade1(f, s, dx, sector, rng):
    """one 128 frame: anatomy shifted by dx px inside the sector + contrast / gain down by s"""
    x = f.astype(np.float32)
    if dx:
        x = np.roll(x, dx, axis=1)
        x[:, :dx] = 0
        x = np.where(sector & (x <= 0), rng.gamma(2.0, 6.0, x.shape).astype(np.float32), x)
    if s > 0:
        mu = x[sector].mean()
        x = (mu + (x - mu) * (1 - 0.65 * s)) * (1 - 0.35 * s)
    return np.clip(np.rint(np.where(sector, x, 0)), 0, 255).astype(np.uint8)


def build():
    n = PHASES[-1][2]
    c2, c4 = clip('2CH', n), clip('4CH', n)
    sec4 = sector_of(c4)
    rng = np.random.default_rng(255)
    out, sev = np.zeros((n, 128, 128), np.uint8), np.zeros(n)
    for t in range(n):
        if t < 100:
            out[t], s = c2[t], 0.0
        else:
            r = 1.0 if t < 300 else (1.0 - (t - 300) / 150.0 if t < 450 else 0.0)
            s = S_MAX * r
            out[t] = degrade1(c4[t], s, int(round(DX_MAX * r)), sec4, rng)
        sev[t] = s
    return out, sev


def preview(fr):
    f1 = integer_model(PLD / 'echo' / 'results' / 'quant' / 'lv_r128')
    rvm = integer_model(PLD / 'cardiac_rv' / 'results' / 'quant' / 'rv_pl1_5')
    em = model()
    ts = np.arange(0, len(fr), K)
    q = f1(fr[ts])
    cm, nc = zip(*[ec.eq_clean(qq >= 1) for qq in q])
    cm, nc = np.stack(cm), np.array(nc, np.int32)
    lights = []
    for t in range(WIN - 1, len(fr), EVERY):
        a = t - WIN + 1
        js = [j for j, tt in enumerate(ts) if a <= tt <= t]
        o = ec.eq_window(fr[a:t + 1], [int(ts[j]) - a for j in js], q[js], cm[js], nc[js], em)
        lights.append((t, o['light'], round(o['score'], 2)))
    vt = list(range(MODEL['view_every'], len(fr), MODEL['view_every']))
    lg = rvm(fr[vt])
    ratios, views = [], []
    for t, g in zip(vt, lg):
        lab, nl = ndimage.label(g >= 1)
        rv = int(np.bincount(lab.ravel())[1:].max()) if nl else 0
        ratios.append(rv / max(int(cm[t // K].sum()), 1))
        v = 0 if len(ratios) < MODEL['view_n'] else (2 if np.median(ratios[-MODEL['view_n']:]) >= MODEL['view_t'] else 1)
        views.append((t, v, round(ratios[-1], 3)))          # 0 = still judging (board v5.26: needs view_n ratios)
    return lights, views


def hold_gap(fr):
    """d between the 1 s mean 112 images at the end of searching and on target (unlock needs > hold_thr)"""
    box = ec.LcBox(x=0, y=0, side=384)
    f112 = np.stack([ec.lc_crop_resize(np.kron(f, np.ones((Z, Z), np.uint8)), box) for f in fr]).astype(np.int64)
    S = lambda t: f112[t - 49:t + 1].sum(0)                # noqa: E731
    return float(np.abs(S(649) - S(299)).sum() / (50 * 112 * 112))


def main():
    fr, sev = build()
    print('hold detector: d(on target vs end of searching) = %.2f grey (threshold %.3f)' % (hold_gap(fr), MODEL['hold_thr']))
    lights, views = preview(fr)
    for name, a, b in PHASES:
        ls = [l for t, l, _ in lights if a <= t < b]
        vs = [v for t, v, _ in views if a <= t < b]
        print('%-10s %5.1f-%5.1f s  lights %s  views %s' % (name, a / FPS, b / FPS, ''.join('gyr'[l] for l in ls),
                                                          ''.join('?-A'[v] for v in vs)), flush=True)
    (HERE / 'results').mkdir(exist_ok=True)
    (HERE / 'results' / 'view_search_preview.json').write_text(json.dumps(dict(
        patient=PATIENT, phases=PHASES, severity=sev.round(3).tolist(), lights=lights, views=views), indent=1), encoding='utf-8')
    base = Image.new('L', (640, 480), 0)
    d = ImageDraw.Draw(base)
    d.text((8, 8), 'CAMUS VAL %s (Leclerc 2019, CC BY-NC-SA 4.0) - simulated view search' % PATIENT, fill=140)
    d.text((8, 460), 'non-commercial demo source; degraded frames are synthetic', fill=140)
    screen = np.repeat(np.asarray(base, np.uint8)[None], len(fr), 0)
    screen[:, Y:Y + 128 * Z, X:X + 128 * Z] = np.kron(fr, np.ones((1, Z, Z), np.uint8))
    src = PLD / 'live' / 'src'
    write_y8(str(src / NAME), screen, FPS)
    idx = json.loads((src / 'index.json').read_text(encoding='utf-8'))
    idx[NAME] = dict(video='CAMUS VAL %s simulated view search (quality/live_camus/sim_search.py)' % PATIENT, fps=FPS,
                     frames=len(fr), W=640, H=480, zoom=float(Z), x=X, y=Y, side=128 * Z, quality='camus',
                     segments=[dict(patient=PATIENT, view='2CH' if p == '2CH' else '4CH', quality=p, say=SAY[p], start=a, n=b - a)
                               for p, a, b in PHASES])
    (src / 'index.json').write_text(json.dumps(idx, indent=1, ensure_ascii=False), encoding='utf-8')
    print(NAME, screen.shape)


if __name__ == '__main__':
    main()
