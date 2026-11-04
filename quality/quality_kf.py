"""Innovations 2 + 3 through the board C (echo_core.dll = tcp_image_rx/src echo_num.c / kf.c / echo_quality.c), for
the two ways the board can run a clip: every frame inferred ('full') and the recommended keyframe schedule
('keyframe' = adapt_nmax6_res1.15_winoff, phase forcing off). Per EchoNet video (TRAIN subset + VAL; TEST untouched):
  masks   PL frames: en_clean112(q >= 1) of program 16's integer logits (results/q, echonet_q.py); others: kf.c
  EF      en_volume_pca + en_analyze, calibration a, b fitted on the TRAIN subset for that mode
          (full: beats_config_t3_int.json; keyframe: keyframe/results/c/echonet_summary.json)
  quality eq_clip_features (per-frame features averaged over the PL frames; stability / roughness over all frames)
Model per mode (written to results/c/eq_model_<mode>.json, the numbers the PC sends to the board in the job params):
  score   CAMUS TRAIN logistic weights + light thresholds (quality_camus.json 'portable'), standardised with this
          mode's TRAIN-subset feature means / SDs (label-free)
  sigma   log(|y - yhat| + 1) ~ linear in the standardised [beat SD, 1 / beats, fallback, unc, roughness, stability,
          contrast, apex_gap, multi] on the TRAIN subset
  q80/q90 split-conformal quantiles of |y - yhat| / sigma on all of VAL (deployable); the VAL report uses 20 random
          halves (calibrate on one, evaluate on the other) as conformal_ef.py
Also: C eq_interval == the Python formula on every VAL video (tolerance 1e-9: exp() of two libms).

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I quality_kf.py
"""
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
os.environ['PATH'] = r'C:\msys64\mingw64\bin;' + os.environ['PATH']
sys.path.insert(0, str(PLD / 'keyframe'))
sys.path.insert(0, str(HERE))
import echo_core  # noqa: E402
from conformal_ef import band, conformal_q, evaluate  # noqa: E402

E = PLD / 'echonet'
OUT = HERE / 'results' / 'c'
POLICY = ('adapt_nmax6_res1.15_winoff', dict(kind='adaptive', n_max=6, res=1.15, win=-10 ** 6))
TRACK = dict(R=3, k=9, reg=5, spacing=2.0)
_d = {}


def data(split):
    if split not in _d:
        idx = json.loads((E / 'results' / 'frames' / f't3_int_{split}.json').read_text(encoding='utf-8'))['videos']
        vid = json.loads((E / 'data' / 'cache' / f'{split}_videos.json').read_text(encoding='utf-8'))
        _d[split] = (idx, vid, np.load(E / 'data' / 'cache' / f'{split}_videos.npy', mmap_mode='r'),
                     np.load(HERE / 'results' / 'q' / f't3_{split}_q112.npy', mmap_mode='r'))
    return _d[split]


def run_video(job):
    split, v = job
    idx, vid, frames, qall = data(split)
    e, ev = idx[v], vid[v]
    fr = np.ascontiguousarray(frames[ev['start']:ev['start'] + ev['n']])
    q = np.ascontiguousarray(qall[e['start']:e['start'] + e['n']])
    cm, nc = zip(*[echo_core.clean112(x >= 1) for x in q])
    cm, nc = np.stack(cm), np.array(nc, np.int32)
    out = dict(video=v, ef=e['ef'], fps=e['fps'])
    for mode, pol in (('full', dict(kind='full')), ('keyframe', POLICY[1])):
        masks, key, _ = echo_core.propagate(fr, cm, pol, TRACK, e['fps'])
        out[mode] = dict(feat=echo_core.clip_features(fr, q, masks, key, nc), key_frac=float(key.mean()),
                         vol=[echo_core.volume_pca(m) for m in masks])
    return out


def predictions(per, mode, a, b, cfg):
    rows = []
    for p in per:
        z = echo_core.analyze(p[mode]['vol'], p['fps'], 1, cfg)
        raw = z['ef'] if np.isfinite(z['ef']) else 0.0
        rows.append(dict(video=p['video'], y=p['ef'], ef=a * raw + b, sd=(z['ef_sd'] or 0.0) * abs(a),
                         beats=len(z['beats']), fallback=int(z['fallback']), **p[mode]['feat']))
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    per = {}
    for split in ('trainsub', 'val'):
        names = sorted(data(split)[0])
        with Pool(16) as pool:
            per[split] = list(pool.imap(run_video, [(split, v) for v in names], chunksize=4))
        print(split, len(per[split]), flush=True)
    conf = json.loads((E / 'results' / 'beats_config_t3_int.json').read_text(encoding='utf-8'))
    kfs = json.loads((PLD / 'keyframe' / 'results' / 'c' / 'echonet_summary.json').read_text(encoding='utf-8'))[POLICY[0]]
    camus = json.loads((HERE / 'results' / 'quality_camus.json').read_text(encoding='utf-8'))['models']['portable']
    assert camus['features'] == echo_core.QF
    report = {}
    for mode, (a, b) in (('full', (conf['full']['a'], conf['full']['b'])), ('keyframe', (kfs['a'], kfs['b']))):
        tr = predictions(per['trainsub'], mode, a, b, conf['cfg'])
        va = predictions(per['val'], mode, a, b, conf['cfg'])
        Ftr = np.array([[r[k] for k in echo_core.QF] for r in tr])
        Fva = np.array([[r[k] for k in echo_core.QF] for r in va])
        mu, sd = Ftr.mean(0), Ftr.std(0) + 1e-9
        w = np.array([camus['weights'][k] for k in ['bias'] + echo_core.QF])
        score = np.c_[np.ones(len(Fva)), (Fva - mu) / sd] @ w
        light = np.where(score >= camus['t_red'], 2, np.where(score >= camus['t_green'], 1, 0))

        def design(rows):
            return np.array([[r['sd'], 1.0 / max(r['beats'], 1), r['fallback']] + [r[k] for k in echo_core.QS[3:]] for r in rows])
        Xtr, Xva = design(tr), design(va)
        zmu, zsd = Xtr.mean(0), Xtr.std(0) + 1e-9
        ytr, yhtr = np.array([r['y'] for r in tr]), np.array([r['ef'] for r in tr])
        beta, *_ = np.linalg.lstsq(np.c_[np.ones(len(Xtr)), (Xtr - zmu) / zsd], np.log(np.abs(ytr - yhtr) + 1), rcond=None)
        sig = np.exp(np.c_[np.ones(len(Xva)), (Xva - zmu) / zsd] @ beta)
        y, yh = np.array([r['y'] for r in va]), np.array([r['ef'] for r in va])
        err = np.abs(y - yh)
        model = dict(mode=mode, policy=POLICY[0] if mode == 'keyframe' else 'full', a=a, b=b, features=echo_core.QF,
                     w=w.tolist(), mu=mu.tolist(), sd=sd.tolist(), t_green=camus['t_green'], t_red=camus['t_red'],
                     sigma_inputs=echo_core.QS, b_sigma=beta.tolist(), zmu=zmu.tolist(), zsd=zsd.tolist(),
                     q80=conformal_q(err / sig, 0.2), q90=conformal_q(err / sig, 0.1))
        (OUT / f'eq_model_{mode}.json').write_text(json.dumps(model, indent=1), encoding='utf-8')
        # C eq_interval vs the formula
        em = echo_core.eq_model(dict(model, b=model['b_sigma']))
        maxd, rev_diff = 0.0, 0
        for r, s in zip(va, sig):
            c = echo_core.interval(em, r['ef'], r['sd'], r['beats'], r['fallback'], r)
            maxd = max(maxd, abs(c['sigma'] - s) / s)
            lo, hi = r['ef'] - model['q80'] * s, r['ef'] + model['q80'] * s
            rev_diff += c['review'] != bool((lo < 40 < hi) or (lo < 50 < hi))
        rep = dict(mae=float(err.mean()), key_frac=float(np.mean([p[mode]['key_frac'] for p in per['val']])),
                   lights=np.bincount(light, minlength=3).tolist(),
                   mae_by_light=[float(err[light == j].mean()) if (light == j).any() else None for j in range(3)],
                   big_err_red=float(np.mean(light[err > 10] == 2)), big_err_red_or_yellow=float(np.mean(light[err > 10] >= 1)),
                   sigma_vs_err_spearman=float(np.corrcoef(np.argsort(np.argsort(sig)), np.argsort(np.argsort(err)))[0, 1]),
                   c_vs_python_sigma_max_rel=maxd, c_vs_python_review_diff=rev_diff, q80=model['q80'], q90=model['q90'])
        for alpha in (0.1, 0.2):
            agg = []
            for seed in range(20):
                perm = np.random.default_rng(seed).permutation(len(va))
                for cal, ev in ((perm[:len(va) // 2], perm[len(va) // 2:]), (perm[len(va) // 2:], perm[:len(va) // 2])):
                    qq = conformal_q(err[cal] / sig[cal], alpha)
                    r_ = evaluate(yh[ev], y[ev], qq * sig[ev], alpha)
                    r_.pop('cover_vec')
                    agg.append(r_)
            rep[f'alpha_{alpha}'] = {k: float(np.nanmean([g[k] for g in agg])) for k in agg[0]}
        rep['band_agree_all'] = float(np.mean([band(p) == band(t) for p, t in zip(yh, y)]))
        report[mode] = rep
    (OUT / 'quality_kf.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    for mode, r in report.items():
        print(f"[{mode}] key {r['key_frac']:.3f} EF MAE {r['mae']:.2f}; lights {r['lights']} MAE by light "
              f"{[round(x, 2) if x else x for x in r['mae_by_light']]}; |err|>10 in red {r['big_err_red']:.2f}; "
              f"sigma~|err| {r['sigma_vs_err_spearman']:.3f}; C vs Python sigma max rel {r['c_vs_python_sigma_max_rel']:.1e}, "
              f"review diffs {r['c_vs_python_review_diff']}")
        for alpha in (0.1, 0.2):
            o = r[f'alpha_{alpha}']
            print(f"   alpha {alpha}: coverage {o['coverage']:.3f} width {o['width']:.1f} review {o['review']:.3f} "
                  f"band agree unreviewed {o['band_agree_unreviewed']:.3f} misgraded caught {o['misgraded_caught']:.3f}")


if __name__ == '__main__':
    main()
