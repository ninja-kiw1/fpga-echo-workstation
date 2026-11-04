"""Innovation ③: split-conformal EF intervals + "review" triage when the interval crosses an ESC 2021 boundary (40 / 50).

EF predictions (EchoNet, beat-to-beat median, calibrated a, b fitted on the TRAIN subset only):
  full      per-frame inference (echonet/results/frames/t3_int_*.npz, beats_config_t3_int.json)
  keyframe  the recommended keyframe schedule adapt_nmax6_res1.15_winoff (keyframe/results/echonet_vol, a, b from
            keyframe/results/echonet_summary.json)
Methods (target coverage 1 - alpha):
  plain     score |y - yhat|, interval yhat +- q
  adaptive  score |y - yhat| / sigma(x), interval yhat +- q sigma(x); log sigma(x) = linear in [beat EF SD, 1 / beats,
            fallback, quality features unc / roughness / stability / contrast / apex_gap / multi], least squares of
            log(|residual| + 1) on the 1,500-video TRAIN subset only (where yhat is in-sample for a, b - only the shape
            of sigma is learnt there, the coverage comes from the calibration split)
Development on VAL (1,288): 20 random halves (seed 0..19): calibrate on one half, evaluate on the other - coverage,
mean width, coverage by sigma tertile, triage (review = interval contains 40 or 50 strictly inside; agreement of the
ESC band among the rest, misgraded videos caught). Reference: eval_clinical's fixed review margin rule (+- m around
40 / 50 catching 90 % of misgraded VAL videos). The deployable q is computed on all of VAL (for the one-time TEST run).
TEST not touched.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I conformal_ef.py
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
sys.path.insert(0, str(PLD / 'echonet'))
from beats import analyze  # noqa: E402

OUT = HERE / 'results'
E = PLD / 'echonet'
QF = ['unc', 'roughness', 'stability', 'contrast', 'apex_gap', 'multi']
KF_POLICY = 'adapt_nmax6_res1.15_winoff'


def band(ef):
    return 0 if ef <= 40 else (1 if ef < 50 else 2)


def predictions(split):
    conf = json.loads((E / 'results' / 'beats_config_t3_int.json').read_text(encoding='utf-8'))
    feat = np.load(E / 'results' / 'frames' / f't3_int_{split}.npz')
    idx = json.loads((E / 'results' / 'frames' / f't3_int_{split}.json').read_text(encoding='utf-8'))['videos']
    qual = json.loads((OUT / f'echonet_quality_{split}.json').read_text(encoding='utf-8'))['videos']
    kfs = json.loads((PLD / 'keyframe' / 'results' / 'echonet_summary.json').read_text(encoding='utf-8'))[KF_POLICY]
    kv = json.loads((PLD / 'keyframe' / 'results' / 'echonet_vol' / f'{split}.json').read_text(encoding='utf-8'))
    kvol = {p['video']: p['runs'][KF_POLICY]['vol'] for p in kv['per']}
    rows = []
    for v, e in sorted(idx.items()):
        s, n = e['start'], e['n']
        r = dict(video=v, ef=e['ef'], fps=e['fps'], **{k: qual[v][k] for k in QF})
        for name, vol, (a, b) in (('full', feat['vol'][s:s + n], (conf['full']['a'], conf['full']['b'])),
                                  ('keyframe', kvol[v], (kfs['a'], kfs['b']))):
            z = analyze(vol, e['fps'], 1, conf['cfg'])
            raw = z['ef'] if np.isfinite(z['ef']) else 0.0
            r[name] = dict(ef=a * raw + b, beats=len(z['beats']), sd=z.get('ef_sd', 0.0) * abs(a), fallback=float(z['fallback']))
        rows.append(r)
    return rows


def design(rows, mode):
    return np.array([[1.0, r[mode]['sd'], 1.0 / max(r[mode]['beats'], 1), r[mode]['fallback']] + [r[k] for k in QF]
                     for r in rows])


def conformal_q(scores, alpha):
    n = len(scores)
    k = int(np.ceil((n + 1) * (1 - alpha)))
    return float(np.sort(scores)[min(k, n) - 1]) if k <= n else float('inf')


def evaluate(yhat, y, half, alpha):
    lo, hi = yhat - half, yhat + half
    cover = (y >= lo) & (y <= hi)
    review = ((lo < 40) & (hi > 40)) | ((lo < 50) & (hi > 50))
    bp = np.array([band(v) for v in yhat])
    bt = np.array([band(v) for v in y])
    wrong = bp != bt
    return dict(coverage=float(cover.mean()), width=float(np.mean(2 * half)), review=float(review.mean()),
                band_agree_unreviewed=float(np.mean(~wrong[~review])) if (~review).any() else float('nan'),
                misgraded_caught=float(np.mean(review[wrong])) if wrong.any() else float('nan'), cover_vec=cover)


def main():
    tr, va = predictions('trainsub'), predictions('val')
    y_va = np.array([r['ef'] for r in va])
    res = dict(n_train=len(tr), n_val=len(va), policy=KF_POLICY, modes={})
    for mode in ('full', 'keyframe'):
        yh_tr = np.array([r[mode]['ef'] for r in tr])
        y_tr = np.array([r['ef'] for r in tr])
        Xtr = design(tr, mode)
        mu, sd = Xtr[:, 1:].mean(0), Xtr[:, 1:].std(0) + 1e-9
        Z = lambda X: np.c_[X[:, :1], (X[:, 1:] - mu) / sd]  # noqa: E731
        beta, *_ = np.linalg.lstsq(Z(Xtr), np.log(np.abs(y_tr - yh_tr) + 1), rcond=None)
        sig_va = np.exp(Z(design(va, mode)) @ beta)
        yh_va = np.array([r[mode]['ef'] for r in va])
        m = dict(mae=float(np.mean(np.abs(yh_va - y_va))), sigma_beta=dict(zip(['const', 'beat_sd', 'inv_beats', 'fallback'] + QF,
                                                                               beta.tolist())),
                 sigma_vs_abs_error_spearman=float(np.corrcoef(np.argsort(np.argsort(sig_va)),
                                                               np.argsort(np.argsort(np.abs(yh_va - y_va))))[0, 1]))
        for alpha in (0.1, 0.2):
            agg = {meth: [] for meth in ('plain', 'adaptive')}
            tert = {meth: [] for meth in ('plain', 'adaptive')}
            for seed in range(20):
                perm = np.random.default_rng(seed).permutation(len(va))
                for cal, ev in ((perm[:len(va) // 2], perm[len(va) // 2:]), (perm[len(va) // 2:], perm[:len(va) // 2])):
                    q_plain = conformal_q(np.abs(y_va[cal] - yh_va[cal]), alpha)
                    q_ad = conformal_q(np.abs(y_va[cal] - yh_va[cal]) / sig_va[cal], alpha)
                    for meth, half in (('plain', np.full(len(ev), q_plain)), ('adaptive', q_ad * sig_va[ev])):
                        r = evaluate(yh_va[ev], y_va[ev], half, alpha)
                        cv = r.pop('cover_vec')
                        agg[meth].append(r)
                        t = np.digitize(sig_va[ev], np.quantile(sig_va, [1 / 3, 2 / 3]))
                        tert[meth].append([float(cv[t == j].mean()) for j in range(3)])
            out = {}
            for meth in agg:
                keys = agg[meth][0].keys()
                out[meth] = {k: float(np.nanmean([a[k] for a in agg[meth]])) for k in keys}
                out[meth]['coverage_sd'] = float(np.std([a['coverage'] for a in agg[meth]]))
                out[meth]['coverage_by_sigma_tertile'] = np.mean(tert[meth], 0).tolist()
            # deployable quantiles from all of VAL (for the one-time TEST run)
            out['q_all_val'] = dict(plain=conformal_q(np.abs(y_va - yh_va), alpha),
                                    adaptive=conformal_q(np.abs(y_va - yh_va) / sig_va, alpha))
            m[f'alpha_{alpha}'] = out
        # reference: fixed margin around 40 / 50 catching 90 % of misgraded videos (eval_clinical's rule), on all VAL
        dist = np.minimum(np.abs(yh_va - 40), np.abs(yh_va - 50))
        wrong = np.array([band(a) != band(b) for a, b in zip(yh_va, y_va)])
        mm = 0.0
        while np.mean(dist[wrong] < mm) < 0.9:
            mm += 0.5
        rv = dist < mm
        m['fixed_margin_reference'] = dict(margin=mm, review=float(rv.mean()), misgraded_caught=float(np.mean(rv[wrong])),
                                           band_agree_unreviewed=float(np.mean(~wrong[~rv])), band_agree_all=float(np.mean(~wrong)))
        res['modes'][mode] = m
    (OUT / 'conformal_ef.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    for mode, m in res['modes'].items():
        print(f"== {mode}: VAL EF MAE {m['mae']:.2f}; sigma vs |error| Spearman {m['sigma_vs_abs_error_spearman']:.3f}")
        for alpha in (0.1, 0.2):
            for meth in ('plain', 'adaptive'):
                o = m[f'alpha_{alpha}'][meth]
                print(f"  alpha {alpha} {meth:8s} coverage {o['coverage']:.3f} (sd {o['coverage_sd']:.3f}) width {o['width']:5.1f} "
                      f"by sigma tertile {[round(c, 3) for c in o['coverage_by_sigma_tertile']]} review {o['review']:.3f} "
                      f"band agree unreviewed {o['band_agree_unreviewed']:.3f} misgraded caught {o['misgraded_caught']:.3f}")
        f = m['fixed_margin_reference']
        print(f"  fixed margin +-{f['margin']}: review {f['review']:.3f} misgraded caught {f['misgraded_caught']:.3f} "
              f"band agree unreviewed {f['band_agree_unreviewed']:.3f} (all {f['band_agree_all']:.3f})")


if __name__ == '__main__':
    main()
