"""Option B feasibility (user 11-02: 实时引擎直接对 CAMUS 源改用 F1): the live keyframe engine with F1 (lv_r128, CAMUS-
trained) as its keyframe program instead of program 16 (EchoNet-trained), on CAMUS streams as the board will see them.

Per stream (CAMUS half sequence played back and forth at 50 fps from its own FrameRate, 4 s, screen route x 3 -> crop 384
-> lc_crop_resize 112 / lc_crop_resize_n 128 = the live source):
  F1 engine   F1 integer logits on the 128 frame -> 112 by nearest sampling (src = (2 y + 1) * 128 // 224) -> logit >= 1
              -> clean112 = the keyframe mask; echo_core.propagate (board C: same schedule, tracker) -> masks -> volumes
              (en_volume_pca) -> echo_ws.analyze (board rules) -> raw EF, beats, HR
  P16 engine  the current live engine (program 16, t3 encoding of 112 frames) -- baseline
Reference: CAMUS Info_<view>.cfg EF (expert, biplane) and the loop period (the true "heart rate" of the stream).
EF calibration a, b (ref = a raw + b) fitted per engine on TRAIN; VAL reported once (MAE, r, HR error). TEST untouched.
Writes f1_engine.json.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I f1_engine.py
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
sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
import echo_core as ec  # noqa: E402
import echo_ws as ws  # noqa: E402
import jobs  # noqa: E402
from design import CACHE, CAMUS, FPS, fps_of, integer_model  # noqa: E402
from frame_ops import video_inputs  # noqa: E402
from tune_search import program16  # noqa: E402

SECONDS = 4.0
BOX = ec.LcBox(x=0, y=0, side=384)
NEAR = (2 * np.arange(112) + 1) * 128 // 224          # 112 -> 128 nearest source index
PAD = 8


def ref_ef(p, view):
    t = (CAMUS / p / f'Info_{view}.cfg').read_text()
    return float(dict(l.split(': ', 1) for l in t.splitlines() if ': ' in l)['EF'])


def streams(split):
    ix = json.loads((CACHE / f'seq_{split}_index.json').read_text(encoding='utf-8'))
    x = np.load(CACHE / f'seq_{split}_img128.npy', mmap_mode='r')
    for e in ix:
        n0, fps = e['length'], fps_of(e['patient'], e['view'])
        cyc = list(range(n0)) + list(range(n0 - 2, 0, -1))
        src = [e['start'] + cyc[int(t / FPS * fps) % len(cyc)] for t in range(int(round(SECONDS * FPS)))]
        f128 = np.asarray(x)[src]
        f112 = np.stack([ec.lc_crop_resize(np.kron(f, np.ones((3, 3), np.uint8)), BOX) for f in f128])
        yield e, f128, f112, len(cyc) / fps


def engine(f112, cm, kf, cfg):
    masks, key, _ = ec.propagate(f112, cm, dict(kf['policy']), kf['track'], FPS)
    vols = [ec.volume_pca(m) for m in masks]
    r = ws.analyze(vols, FPS, 1, cfg)
    return r, float(key.mean())


def main():
    f1 = integer_model(PLD / 'echo' / 'results' / 'quant' / 'lv_r128')
    p16 = program16()
    kf = jobs.load_kf_config('keyframe')
    cfg = ws.load_config()['cfg']
    rows = {}
    for split in ('train', 'val'):
        out = []
        for i, (e, f128, f112, period) in enumerate(streams(split)):
            q = f1(f128)[:, NEAR][:, :, NEAR]
            cm_f1 = np.stack([ec.clean112(qq >= 1)[0] for qq in q])
            with torch.no_grad():
                xx = video_inputs(f112, 't3', FPS)
                q16 = np.concatenate([p16(torch.from_numpy(xx[k:k + 128]))[:, 0, PAD:PAD + 112, PAD:PAD + 112].to(torch.int8).cpu().numpy()
                                      for k in range(0, len(xx), 128)])
            cm_16 = np.stack([ec.clean112(qq >= 1)[0] for qq in q16])
            row = dict(patient=e['patient'], view=e['view'], ref=ref_ef(e['patient'], e['view']), hr_true=60.0 / period)
            for name, cm in (('f1', cm_f1), ('p16', cm_16)):
                r, kfrac = engine(f112, cm, kf, cfg)
                row[name] = dict(raw=r['ef'] if r['ef'] == r['ef'] else None, beats=len(r['beats']), hr=r['hr'],
                                 fallback=bool(r['fallback']), key_frac=kfrac)
            out.append(row)
            if (i + 1) % 100 == 0:
                print(split, i + 1, flush=True)
        rows[split] = out
    res = {}
    for name in ('f1', 'p16'):
        tr = [(r[name]['raw'], r['ref']) for r in rows['train'] if r[name]['raw'] is not None]
        A = np.array(tr)
        a, b = np.polyfit(A[:, 0], A[:, 1], 1)
        va = [r for r in rows['val'] if r[name]['raw'] is not None]
        pred = np.array([a * r[name]['raw'] + b for r in va])
        ref = np.array([r['ref'] for r in va])
        hr_err = np.array([abs((r[name]['hr'] or 0) - r['hr_true']) / r['hr_true'] for r in rows['val']])
        res[name] = dict(a=float(a), b=float(b), val_n=len(va), val_mae=float(np.abs(pred - ref).mean()),
                         val_r=float(np.corrcoef(pred, ref)[0, 1]), raw_r=float(np.corrcoef([r[name]['raw'] for r in va], ref)[0, 1]),
                         hr_within_15pct=float((hr_err <= 0.15).mean()), hr_err_median=float(np.median(hr_err)),
                         key_frac=float(np.mean([r[name]['key_frac'] for r in rows['val']])),
                         fallback=float(np.mean([r[name]['fallback'] for r in rows['val']])))
        print('%-4s calib a %.3f b %.1f | VAL n %d  EF MAE %.1f  r %.2f (raw r %.2f) | HR within 15%% of the loop %.0f%%  '
              '(median error %.0f%%) | keyframes %.0f%%  fallback %.0f%%' % (
                  name, a, b, len(va), res[name]['val_mae'], res[name]['val_r'], res[name]['raw_r'],
                  100 * res[name]['hr_within_15pct'], 100 * res[name]['hr_err_median'], 100 * res[name]['key_frac'],
                  100 * res[name]['fallback']), flush=True)
    p0255 = [r for r in rows['val'] if r['patient'] == 'patient0255']
    for r in p0255:
        print('patient0255 %s ref %.0f | F1 %.0f (hr %.0f) | P16 %.0f (hr %.0f) | loop hr %.0f' % (
            r['view'], r['ref'], res['f1']['a'] * (r['f1']['raw'] or 0) + res['f1']['b'], r['f1']['hr'] or 0,
            res['p16']['a'] * (r['p16']['raw'] or 0) + res['p16']['b'], r['p16']['hr'] or 0, r['hr_true']))
    (HERE / 'f1_engine.json').write_text(json.dumps(dict(summary=res, rows=rows), indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
