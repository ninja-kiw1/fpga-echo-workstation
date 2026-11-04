"""Keyframe step 1: propagation error frame by frame on TED (expert LV label on every frame of a full cycle).

Keyframe masks = the expert label of that frame, so every number here is the error of the PS propagation alone
(independent of any segmentation model). TED = CAMUS TRAINING patients (ted_match.py: 94 training, 4 testing,
0 validation); the 4 CAMUS-testing cases are excluded from everything, so tuning on the other 94 is TRAIN-only.

Tier: frames like the UI path (host_stretch: LANCZOS to 512) then every 2nd pixel -> 256 x 256 (as part E);
expert masks NEAREST to 512 then [::2]. Frame rate from the matched CAMUS Info_4CH.cfg.
Per case and setting: Dice of every frame vs the expert (mean over non-keyframes, at ED / ES, minimum), the
single-plane disk volume curve (volume.volume_pca) and EF at the expert ED / ES frames vs the same on expert masks.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I ted_sim.py sweep     (tracker parameters, fixed n)
    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I ted_sim.py policies  (schedules with the chosen cfg)
"""
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'echonet'))
import kf  # noqa: E402
from ted_match import TED, cfg as read_cfg, mhd  # noqa: E402
from volume import volume_pca  # noqa: E402

OUT = HERE / 'results'
TIER = 256


def stretch(img):
    im = Image.fromarray(img).convert('RGB').resize((512, 512), Image.LANCZOS)
    return np.array(im)[::2, ::2, 0]


def load_case(t):
    d = TED / t
    seq, _ = mhd(d / f'{t}_4CH_sequence.mhd')
    gt, _ = mhd(d / f'{t}_4CH_sequence_gt.mhd')
    frames = np.stack([stretch(f) for f in seq])
    lv = np.stack([np.array(Image.fromarray((g == 1).astype(np.uint8)).resize((512, 512), Image.NEAREST))[::2, ::2] > 0
                   for g in gt])
    return frames, lv


def cases():
    m = json.loads((OUT / 'ted_camus_map.json').read_text(encoding='utf-8'))['cases']
    return [(t, float(r['camus_info']['FrameRate']), r['ed'], r['es'], r['quality'])
            for t, r in sorted(m.items()) if r['split'] != 'testing']


def ef(v_ed, v_es):
    return 100.0 * (v_ed - v_es) / v_ed if v_ed > 0 else float('nan')


def run_case(job):
    (t, fps, ed, es, qual), settings = job
    frames, lv = load_case(t)
    vol_gt = np.array([volume_pca(m) for m in lv])
    out = dict(case=t, fps=fps, frames=len(frames), quality=qual, ef_gt=ef(vol_gt[ed - 1], vol_gt[es - 1]), runs={})
    for name, policy, cfg in settings:
        t0 = time.perf_counter()
        masks, key, info = kf.propagate(frames, lambda i: lv[i], policy, cfg, fps)
        dt = time.perf_counter() - t0
        d = np.array([kf.dice(a, b) for a, b in zip(masks, lv)])
        vol = np.array([volume_pca(m) for m in masks])
        nk = ~key
        out['runs'][name] = dict(
            key_frac=float(key.mean()), dice_nonkey=float(d[nk].mean()) if nk.any() else 1.0,
            dice_all=float(d.mean()), dice_min=float(d.min()), dice_ed=float(d[ed - 1]), dice_es=float(d[es - 1]),
            ef=ef(vol[ed - 1], vol[es - 1]), vol_err=float(np.mean(np.abs(vol - vol_gt) / vol_gt.max())),
            ms_per_frame=1000 * dt / len(frames), reasons={r: info['reason'].count(r) for r in set(info['reason']) if r})
    return out


def summarise(per, names):
    s = {}
    for n in names:
        rr = [p['runs'][n] for p in per]
        efe = np.array([r['ef'] - p['ef_gt'] for r, p in zip(rr, per)])
        s[n] = dict(key_frac=float(np.mean([r['key_frac'] for r in rr])),
                    dice_nonkey=float(np.mean([r['dice_nonkey'] for r in rr])),
                    dice_all=float(np.mean([r['dice_all'] for r in rr])),
                    dice_min_p5=float(np.percentile([r['dice_min'] for r in rr], 5)),
                    dice_es=float(np.mean([r['dice_es'] for r in rr])),
                    ef_mae=float(np.nanmean(np.abs(efe))), ef_bias=float(np.nanmean(efe)),
                    vol_err=float(np.mean([r['vol_err'] for r in rr])),
                    host_ms_per_frame=float(np.mean([r['ms_per_frame'] for r in rr])))
    return s


def settings_sweep():
    st = []
    for n in (4, 8):
        st.append((f'hold_n{n}', dict(kind='hold', n=n), {}))
        for mode in ('chain', 'key'):
            for R in (4, 6):
                for k in (1, 5, 9):
                    for reg in (1, 5):
                        for sp in (3.0, 4.0):
                            st.append((f'{mode}_R{R}_k{k}_reg{reg}_s{sp:g}_n{n}', dict(kind='fixed', n=n),
                                       dict(mode=mode, R=R, k=k, reg=reg, spacing=sp)))
    return st


def settings_policies(cfg):
    st = [('full', dict(kind='full'), {})]
    for n in (2, 3, 4, 6, 8):
        st.append((f'hold_n{n}', dict(kind='hold', n=n), {}))
        st.append((f'fixed_n{n}', dict(kind='fixed', n=n), cfg))
    for n_max in (4, 6, 8):
        for res in (1.15, 1.3, 1e9):
            for win in (0, 1, -1):
                pol = dict(kind='adaptive', n_max=n_max, res=res, win=win if win >= 0 else -10 ** 6)
                st.append((f'adapt_nmax{n_max}_res{res if res < 1e8 else "off"}_win{win if win >= 0 else "off"}', pol, cfg))
    return st


def main():
    what = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    cs = cases()[:limit or None]
    if what == 'sweep':
        st = settings_sweep()
    else:
        best = json.loads((OUT / 'ted_sweep.json').read_text(encoding='utf-8'))['chosen']
        st = settings_policies(best['cfg'])
    t0 = time.time()
    with Pool(16) as pool:
        per = list(pool.imap(run_case, [(c, st) for c in cs]))
    names = [s[0] for s in st]
    summ = summarise(per, names)
    res = dict(cases=len(per), tier=TIER, settings=[dict(name=a, policy=b, cfg=c) for a, b, c in st], summary=summ, per=per)
    if what == 'sweep':
        # choice rule (written before running): best mean non-keyframe Dice at n = 8, ties -> n = 4
        cand = [s for s in st if s[1]['kind'] == 'fixed' and s[1]['n'] == 8]
        top = max(cand, key=lambda s: (summ[s[0]]['dice_nonkey'], summ[s[0].replace('_n8', '_n4')]['dice_nonkey']))
        res['chosen'] = dict(name=top[0], cfg=top[2])
    (OUT / f'ted_{what}.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    for n in names:
        s = summ[n]
        print(f"{n:42s} key {s['key_frac']:.3f} dice_nonkey {s['dice_nonkey']:.4f} all {s['dice_all']:.4f} "
              f"minP5 {s['dice_min_p5']:.3f} ES {s['dice_es']:.4f} EF MAE {s['ef_mae']:5.2f} bias {s['ef_bias']:+5.2f} "
              f"vol {s['vol_err']:.3f} {s['host_ms_per_frame']:.1f} ms")
    if 'chosen' in res:
        print('chosen:', res['chosen'])
    print(f'{len(per)} cases, {time.time() - t0:.0f} s')


if __name__ == '__main__':
    main()
