"""Keyframe step 2: EchoNet-Dynamic whole videos - keyframe schedule -> agreement, expert Dice, beat-to-beat EF, PL load.

Keyframe mask = the board-equivalent integer model 16 (echonet_lv, t3) output of that frame after clean(), cached
for every frame by echonet/infer_videos.py (results/frames/t3_int_<split>.npz, == board bit for bit). Between
keyframes: kf.propagate on the native 112 x 112 grey frames (the grid the board sees, no resampling).
Per video and setting: agreement Dice with per-frame inference on the non-keyframes, Dice vs the expert tracing on
the 2 traced frames (VAL), keyframe fraction, the single-plane disk volume of every frame -> echonet/beats.analyze
with the fixed integer-model beat parameters (beats_config_t3_int.json, chosen on TRAIN for per-frame inference).
EF calibration a, b is refitted per setting on the 1,500-video TRAIN subset only, then applied to VAL.
TEST is not touched.

    python -I echonet_sim.py tune        tracker scale for the 112 grid on the first 300 TRAIN-subset videos
    python -I echonet_sim.py run         all settings on TRAIN subset + VAL (vol curves -> results/echonet_vol/)
    python -I echonet_sim.py summary     EF / Dice / load table -> results/echonet_summary.json
"""
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ECHONET = HERE.parent / 'echonet'
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ECHONET))
import kf  # noqa: E402
from beats import analyze, calibrate, metrics, realtime_step  # noqa: E402
from volume import volume_pca  # noqa: E402

OUT = HERE / 'results'
VOL = OUT / 'echonet_vol'
CACHE = ECHONET / 'data' / 'cache'
FRAMES = ECHONET / 'results' / 'frames'
N = 112
_data = {}


def data(split):
    if split not in _data:
        idx = json.loads((FRAMES / f't3_int_{split}.json').read_text(encoding='utf-8'))['videos']
        vid = json.loads((CACHE / f'{split}_videos.json').read_text(encoding='utf-8'))
        lab = None
        if split == 'val':
            meta = json.loads((CACHE / 'val_meta.json').read_text(encoding='utf-8'))['frames']
            y = np.load(CACHE / 'val_lab112.npy', mmap_mode='r')
            lab = {}
            for i, m in enumerate(meta):
                lab.setdefault(m['video'], []).append((m['frame'], m['phase'], i))
            lab = (lab, y)
        _data[split] = (np.load(CACHE / f'{split}_videos.npy', mmap_mode='r'),
                        np.load(FRAMES / f't3_int_{split}.npz')['masks'], idx, vid, lab)
    return _data[split]


def run_video(job):
    split, v, settings = job
    frames_all, packed, idx, vid, lab = data(split)
    e, ev = idx[v], vid[v]
    fr = np.ascontiguousarray(frames_all[ev['start']:ev['start'] + ev['n']])
    full = np.unpackbits(packed[e['start']:e['start'] + e['n']], axis=1)[:, :N * N].reshape(-1, N, N).astype(bool)
    out = dict(video=v, fps=e['fps'], ef=e['ef'], n=e['n'], runs={})
    for name, policy, cfg in settings:
        t0 = time.perf_counter()
        masks, key, info = kf.propagate(fr, lambda i: full[i], policy, cfg, e['fps'])
        dt = time.perf_counter() - t0
        nk = ~key
        agree = [kf.dice(a, b) for a, b in zip(masks[nk], full[nk])]
        r = dict(key_frac=float(key.mean()), agree=float(np.mean(agree)) if agree else 1.0,
                 agree_min=float(np.min(agree)) if agree else 1.0, ms_per_frame=1000 * dt / e['n'],
                 vol=[round(volume_pca(m), 2) for m in masks],
                 reasons={k: info['reason'].count(k) for k in set(info['reason']) if k})
        if lab is not None and v in lab[0]:
            r['expert'] = {ph: kf.dice(masks[f], np.asarray(lab[1][i], bool)) for f, ph, i in lab[0][v] if f < e['n']}
            r['expert_key'] = {ph: bool(key[f]) for f, ph, i in lab[0][v] if f < e['n']}
        out['runs'][name] = r
    return out


def settings_tune():
    st = []
    for sp in (2.0, 3.0):
        for R in (3, 4):
            for k in (5, 9):
                st.append((f'chain_R{R}_k{k}_s{sp:g}_n4', dict(kind='fixed', n=4), dict(mode='chain', R=R, k=k, reg=5, spacing=sp)))
    st.append(('hold_n4', dict(kind='hold', n=4), {}))
    return st


def settings_run(cfg):
    st = [('full', dict(kind='full'), {})]
    for n in (2, 3, 4, 6, 8):
        st.append((f'hold_n{n}', dict(kind='hold', n=n), {}))
        st.append((f'fixed_n{n}', dict(kind='fixed', n=n), cfg))
    for n_max in (6, 8, 12):
        for res in (1.15, None):
            for win in (None, 0, 1):
                pol = dict(kind='adaptive', n_max=n_max, res=res if res else 1e9, win=win if win is not None else -10 ** 6)
                st.append((f'adapt_nmax{n_max}_res{res or "off"}_win{"off" if win is None else win}', pol, cfg))
    return st


def pool_run(split, names, st):
    with Pool(16) as pool:
        return list(pool.imap(run_video, [(split, v, st) for v in names], chunksize=4))


def main():
    what = sys.argv[1]
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    if what == 'tune':
        st = settings_tune()
        names = sorted(data('trainsub')[2])[:300]
        per = pool_run('trainsub', names, st)
        summ = {n: dict(agree=float(np.mean([p['runs'][n]['agree'] for p in per])),
                        agree_p5=float(np.percentile([p['runs'][n]['agree'] for p in per], 5)),
                        ms=float(np.mean([p['runs'][n]['ms_per_frame'] for p in per]))) for n, _, _ in st}
        cand = [s for s in st if s[1]['kind'] == 'fixed']
        top = max(cand, key=lambda s: summ[s[0]]['agree'])      # rule: best mean agreement on TRAIN
        (OUT / 'echonet_tune.json').write_text(json.dumps(dict(videos=len(per), summary=summ,
                                                                chosen=dict(name=top[0], cfg=top[2])), indent=1), encoding='utf-8')
        for n, s in summ.items():
            print(f"{n:28s} agree {s['agree']:.4f} P5 {s['agree_p5']:.4f} {s['ms']:.2f} ms/frame")
        print('chosen', top[0])
    elif what == 'run':
        cfg = json.loads((OUT / 'echonet_tune.json').read_text(encoding='utf-8'))['chosen']['cfg']
        st = settings_run(cfg)
        VOL.mkdir(exist_ok=True)
        for split in ('trainsub', 'val'):
            names = sorted(data(split)[2])
            per = pool_run(split, names, st)
            (VOL / f'{split}.json').write_text(json.dumps(dict(settings=[dict(name=a, policy=b, cfg=c) for a, b, c in st],
                                                               per=per)), encoding='utf-8')
            print(f'{split}: {len(per)} videos ({time.time() - t0:.0f} s)', flush=True)
    elif what == 'summary':
        summary()
    print(f'done in {time.time() - t0:.0f} s')


def summary():
    conf = json.loads((ECHONET / 'results' / 'beats_config_t3_int.json').read_text(encoding='utf-8'))
    bc = conf['cfg']
    tr = json.loads((VOL / 'trainsub.json').read_text(encoding='utf-8'))
    va = json.loads((VOL / 'val.json').read_text(encoding='utf-8'))
    names = [s['name'] for s in va['settings']]

    def efs(per, name):
        raw = []
        for p in per:
            r = analyze(p['runs'][name]['vol'], p['fps'], 1, bc)
            raw.append(r['ef'] if np.isfinite(r['ef']) else 0.0)
        return np.array(raw), np.array([p['ef'] for p in per])

    out = {}
    for n in names:
        rt, tt = efs(tr['per'], n)
        a, b = calibrate(rt, tt)
        rv, tv = efs(va['per'], n)
        runs = [p['runs'][n] for p in va['per']]
        exp = [d for r in runs for d in r.get('expert', {}).values()]
        exp_es = [r['expert']['ES'] for r in runs if 'ES' in r.get('expert', {})]
        exp_nonkey = [r['expert'][ph] for r in runs for ph in r.get('expert', {}) if not r['expert_key'][ph]]
        fps = np.array([p['fps'] for p in va['per']])
        kf_ = np.array([r['key_frac'] for r in runs])
        out[n] = dict(a=a, b=b, ef=metrics(a * rv + b, tv), ef_train=metrics(a * rt + b, tt)['mae'],
                      key_frac=float(kf_.mean()), key_per_s=float(np.mean(kf_ * fps)),
                      agree=float(np.mean([r['agree'] for r in runs])),
                      agree_p5=float(np.percentile([r['agree'] for r in runs], 5)),
                      expert_dice=float(np.mean(exp)), expert_dice_es=float(np.mean(exp_es)),
                      expert_dice_nonkey=float(np.mean(exp_nonkey)) if exp_nonkey else None,
                      n_expert_nonkey=len(exp_nonkey),
                      host_ms_per_frame=float(np.mean([r['ms_per_frame'] for r in runs])),
                      reasons={k: int(sum(r['reasons'].get(k, 0) for r in runs)) for k in ('interval', 'residual', 'phase', 'start', 'lost')})
    # today's real-time mode for reference: every k-th frame inferred, beats on the sub-sampled curve (README 5.49)
    feat = np.load(FRAMES / 't3_int_val.npz')
    idx = json.loads((FRAMES / 't3_int_val.json').read_text(encoding='utf-8'))['videos']
    p, t, kfr = [], [], []
    for v, e in idx.items():
        k = realtime_step(e['fps'])
        r = analyze(feat['vol'][e['start']:e['start'] + e['n']][::k], e['fps'], k, bc)
        p.append(conf['realtime']['a'] * r['ef'] + conf['realtime']['b'])
        t.append(e['ef'])
        kfr.append(1.0 / k)
    out['_today_realtime_subsample'] = dict(ef=metrics(p, t), key_frac=float(np.mean(kfr)))
    (OUT / 'echonet_summary.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    for n, s in out.items():
        e = s['ef']
        extra = '' if n.startswith('_') else (f" agree {s['agree']:.4f} P5 {s['agree_p5']:.3f} expert {s['expert_dice']:.4f} "
                                             f"ES {s['expert_dice_es']:.4f} key/s {s['key_per_s']:5.1f} {s['reasons']}")
        print(f"{n:30s} key {s['key_frac']:.3f} EF MAE {e['mae']:.2f} RMSE {e['rmse']:.2f} r {e['r']:.3f}{extra}")


if __name__ == '__main__':
    main()
