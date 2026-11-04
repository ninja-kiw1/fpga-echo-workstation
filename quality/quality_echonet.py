"""Innovation ② step 5: does the CAMUS-fitted quality score flag the EchoNet videos whose EF is far off?

EchoNet has no quality grades, so the external check is the plan's fallback metric: "how many large-error cases does
the light stop". The score is the CAMUS TRAIN logistic model 'portable' (its weights and light thresholds) on the
EchoNet VAL video features (echonet_features.py), standardised with the EchoNet TRAIN subset's own feature
means / SDs (label-free per-program normalisation; the CAMUS standardisation is reported too: it puts every video in
green because the logit scale and gain differ). Nothing is fitted on EchoNet EF labels. Errors: EF of
per-frame inference and of the recommended keyframe schedule (conformal_ef.predictions, a, b from the TRAIN subset).
Reported: Spearman(score, |error|), MAE by light, share of the |error| > 10 videos in red / red + yellow, MAE of the
kept videos when the 10 / 20 % highest scores are flagged; reference = eval_ef.py's QC score (results/ef/t3_int_val.json).
Also single features. TEST not touched.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I quality_echonet.py
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from conformal_ef import predictions  # noqa: E402
from features import FEATURES  # noqa: E402

OUT = HERE / 'results'
PLD = HERE.parent


def spearman(a, b):
    return float(np.corrcoef(np.argsort(np.argsort(a)), np.argsort(np.argsort(b)))[0, 1])


def flag_stats(score, err):
    out = dict(spearman=spearman(score, err))
    order = np.argsort(-score)
    for frac in (0.1, 0.2):
        k = int(round(frac * len(score)))
        f = np.zeros(len(score), bool)
        f[order[:k]] = True
        out[f'flag{int(frac * 100)}'] = dict(mae_kept=float(err[~f].mean()), mae_flagged=float(err[f].mean()),
                                             big_err_caught=float(np.mean(f[err > 10])))
    return out


def main():
    m = json.loads((OUT / 'quality_camus.json').read_text(encoding='utf-8'))['models']['portable']
    va = predictions('val')
    qv = json.loads((OUT / 'echonet_quality_val.json').read_text(encoding='utf-8'))['videos']
    X = np.array([[qv[r['video']][k] for k in FEATURES] for r in va])
    w = np.array([m['weights'][k] for k in ['bias'] + FEATURES])
    score_raw = np.c_[np.ones(len(X)), (X - np.array(m['mean'])) / np.array(m['sd'])] @ w
    # label-free domain fix: the LV programs' logit scales and the datasets' gain differ (unc 0.099 vs 0.055, contrast
    # 0.26 vs 0.67), so standardise with the EchoNet TRAIN subset's own feature means / SDs, keep the CAMUS weights
    # and light thresholds (no EF labels used)
    qt = json.loads((OUT / 'echonet_quality_trainsub.json').read_text(encoding='utf-8'))['videos']
    Xt = np.array([[v[k] for k in FEATURES] for v in qt.values()])
    score = np.c_[np.ones(len(X)), (X - Xt.mean(0)) / (Xt.std(0) + 1e-9)] @ w
    light = np.where(score >= m['t_red'], 2, np.where(score >= m['t_green'], 1, 0))
    qc_ref = {r['video']: r['qc'] for r in json.loads((PLD / 'echonet' / 'results' / 'ef' / 't3_int_val.json')
                                                      .read_text(encoding='utf-8'))['per_video']}
    qc = np.array([qc_ref[r['video']] for r in va])
    res = dict(videos=len(va), lights=np.bincount(light, minlength=3).tolist(),
               camus_feature_means=dict(zip(FEATURES, m['mean'])), echonet_feature_means=dict(zip(FEATURES, X.mean(0).tolist())))
    for mode in ('full', 'keyframe'):
        err = np.abs(np.array([r[mode]['ef'] for r in va]) - np.array([r['ef'] for r in va]))
        big = err > 10
        res[mode] = dict(mae=float(err.mean()), big_err=int(big.sum()),
                         mae_by_light=[float(err[light == j].mean()) if (light == j).any() else None for j in range(3)],
                         big_err_red=float(np.mean(light[big] == 2)), big_err_red_or_yellow=float(np.mean(light[big] >= 1)),
                         score=flag_stats(score, err), score_camus_standardisation=flag_stats(score_raw, err),
                         qc_reference=flag_stats(qc, err),
                         single={k: flag_stats(X[:, j] * (1 if m['weights'][k] >= 0 else -1), err)['spearman']
                                 for j, k in enumerate(FEATURES)})
    (OUT / 'quality_echonet.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    print('lights (green / yellow / red):', res['lights'])
    for mode in ('full', 'keyframe'):
        r = res[mode]
        print(f"[{mode}] MAE {r['mae']:.2f}; MAE by light {[round(x, 2) if x else x for x in r['mae_by_light']]}; "
              f"|err| > 10: {r['big_err']} videos, red {r['big_err_red']:.2f}, red+yellow {r['big_err_red_or_yellow']:.2f}")
        for name in ('score', 'score_camus_standardisation', 'qc_reference'):
            s = r[name]
            print(f"   {name:28s} Spearman {s['spearman']:.3f}; flag 10 %: kept {s['flag10']['mae_kept']:.2f} flagged "
                  f"{s['flag10']['mae_flagged']:.2f} big caught {s['flag10']['big_err_caught']:.2f}; flag 20 %: kept "
                  f"{s['flag20']['mae_kept']:.2f} flagged {s['flag20']['mae_flagged']:.2f} big caught {s['flag20']['big_err_caught']:.2f}")
        print('   single-feature Spearman: ' + ', '.join(f'{k} {v:.2f}' for k, v in r['single'].items()))


if __name__ == '__main__':
    main()
