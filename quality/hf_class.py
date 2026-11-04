"""Heart-failure EF classes (2021 ESC: reduced <= 40, mildly reduced 41-49, preserved >= 50) from the board C pipeline,
EchoNet-Dynamic VAL only (TEST untouched: it has never been used and needs the user's decision + a lock file).

Per VAL video, both ways the board can run a clip (mode 2 'full' = every frame inferred, 'keyframe' = the recommended
adapt_nmax6_res1.15_winoff schedule), through echo_core.dll = the same C as the board (quality_kf.run_video):
  EF        calibrated with that mode's TRAIN-subset a, b (no VAL fitting)
  interval  the deployed eq_model_<mode>.json (sigma on TRAIN subset; q80 / q90 from all of VAL -> review figures below
            use 20 random VAL halves instead: calibrate q on one half, evaluate on the other, as conformal_ef.py)
Reported:
  * binary EF < 50 (the EchoNet-Dynamic paper's HF task, AUC 0.97 there) and EF <= 40 (HFrEF): AUC (predicted EF as the
    score), sensitivity / specificity / PPV / NPV at the guideline cut, 2000 bootstrap 95 % CIs (video resampling);
  * 3-class confusion (rows = FileList EF band, cols = predicted band), agreement, Cohen's kappa (linear weights);
  * the review split: share flagged, band agreement among unflagged clips, share of misgraded clips that were flagged
    (80 % interval crossing 40 / 50), against the older fixed-margin rule (|EF - 40| or |EF - 50| < 6, eval_clinical.py);
  * safety with the deployed flag: positives called or flagged, positives missed silently, silent misgrades;
  * per quality light: band agreement and EF MAE.
Writes results/c/hf_class.json and results/c/hf_val_rows.json (per-video predictions; derived numbers only).

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I hf_class.py
"""
import json
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import quality_kf as qk  # noqa: E402  (sets PATH for echo_core.dll)
from conformal_ef import band, conformal_q  # noqa: E402

echo_core = qk.echo_core
OUT = qk.OUT
MARGIN = 6.0          # eval_clinical.py review margin (chosen on VAL)


def auc(score, pos):
    """Mann-Whitney AUC, ties counted half."""
    s, p = np.asarray(score, float), np.asarray(pos, bool)
    r = np.argsort(np.argsort(s, kind='mergesort'), kind='mergesort').astype(float)
    for v in np.unique(s):               # average ranks over ties
        m = s == v
        if m.sum() > 1:
            r[m] = r[m].mean()
    n1, n0 = p.sum(), (~p).sum()
    return float((r[p].sum() - n1 * (n1 - 1) / 2) / (n1 * n0))


def binary(yhat, y, cut, strict):
    pos = (y < cut) if strict else (y <= cut)
    pred = (yhat < cut) if strict else (yhat <= cut)
    tp, fn = int((pred & pos).sum()), int((~pred & pos).sum())
    tn, fp = int((~pred & ~pos).sum()), int((pred & ~pos).sum())
    return dict(auc=auc(-yhat, pos), sens=tp / max(tp + fn, 1), spec=tn / max(tn + fp, 1), ppv=tp / max(tp + fp, 1),
                npv=tn / max(tn + fn, 1), tp=tp, fn=fn, tn=tn, fp=fp, prevalence=float(pos.mean()))


def with_ci(yhat, y, cut, strict, nboot=2000):
    point = binary(yhat, y, cut, strict)
    rng = np.random.default_rng(0)
    bs = {k: [] for k in ('auc', 'sens', 'spec', 'ppv', 'npv')}
    for _ in range(nboot):
        i = rng.integers(0, len(y), len(y))
        b = binary(yhat[i], y[i], cut, strict)
        for k in bs:
            bs[k].append(b[k])
    point['ci95'] = {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in bs.items()}
    return point


def kappa_linear(cm):
    cm = np.asarray(cm, float)
    n, k = cm.sum(), len(cm)
    w = 1 - np.abs(np.subtract.outer(np.arange(k), np.arange(k))) / (k - 1)
    po = (w * cm).sum() / n
    pe = (w * np.outer(cm.sum(1), cm.sum(0))).sum() / n ** 2
    return float((po - pe) / (1 - pe))


def review_split(flag, wrong, ok_band):
    return dict(flagged=float(flag.mean()), agree_unflagged=float(ok_band[~flag].mean()),
                agree_flagged=float(ok_band[flag].mean()) if flag.any() else None,
                misgraded=int(wrong.sum()), misgraded_flagged=float(flag[wrong].mean()))


def main():
    names = sorted(qk.data('val')[0])
    with Pool(16) as pool:
        per = list(pool.imap(qk.run_video, [('val', v) for v in names], chunksize=4))
    conf = json.loads((qk.E / 'results' / 'beats_config_t3_int.json').read_text(encoding='utf-8'))
    report, rows_out = dict(split='val', videos=len(per), test='untouched (never used)'), {}
    for mode in ('full', 'keyframe'):
        m = json.loads((OUT / f'eq_model_{mode}.json').read_text(encoding='utf-8'))
        rows = qk.predictions(per, mode, m['a'], m['b'], conf['cfg'])
        em = echo_core.eq_model(dict(m, b=m['b_sigma']))
        y = np.array([r['y'] for r in rows])
        yh = np.array([r['ef'] for r in rows])
        iv = [echo_core.interval(em, r['ef'], r['sd'], r['beats'], r['fallback'], r) for r in rows]
        sig = np.array([v['sigma'] for v in iv])
        F = np.array([[r[k] for k in echo_core.QF] for r in rows])
        score = np.c_[np.ones(len(F)), (F - np.array(m['mu'])) / np.array(m['sd'])] @ np.array(m['w'])
        light = np.where(score >= m['t_red'], 2, np.where(score >= m['t_green'], 1, 0))
        bt, bp = np.array([band(v) for v in y]), np.array([band(v) for v in yh])
        cm = np.zeros((3, 3), int)
        np.add.at(cm, (bt, bp), 1)
        ok_band, wrong = bp == bt, bp != bt
        rep = dict(ef_mae=float(np.abs(yh - y).mean()),
                   ef_lt50=with_ci(yh, y, 50, True), ef_le40=with_ci(yh, y, 40, False),
                   three_class=dict(confusion_ref_rows_pred_cols=cm.tolist(), agree=float(ok_band.mean()),
                                    kappa_linear=kappa_linear(cm),
                                    recall_per_band=[float(cm[j, j] / max(cm[j].sum(), 1)) for j in range(3)]))
        # review: fixed margin (old) vs 80 % interval (split halves; deployed q from all VAL shown too)
        dist = np.minimum(np.abs(yh - 40), np.abs(yh - 50))
        rep['review_margin6'] = review_split(dist < MARGIN, wrong, ok_band)
        lo, hi = np.array([v['lo80'] for v in iv]), np.array([v['hi80'] for v in iv])
        dep = ((lo < 40) & (hi > 40)) | ((lo < 50) & (hi > 50))
        rep['review_interval80_deployed_q'] = review_split(dep, wrong, ok_band)
        err = np.abs(y - yh)
        agg = []
        for seed in range(20):
            perm = np.random.default_rng(seed).permutation(len(y))
            h = len(y) // 2
            for cal, ev in ((perm[:h], perm[h:]), (perm[h:], perm[:h])):
                q = conformal_q(err[cal] / sig[cal], 0.2)
                l_, h_ = yh[ev] - q * sig[ev], yh[ev] + q * sig[ev]
                f = ((l_ < 40) & (h_ > 40)) | ((l_ < 50) & (h_ > 50))
                agg.append(review_split(f, wrong[ev], ok_band[ev]))
        rep['review_interval80_split_halves'] = {k: float(np.mean([a[k] for a in agg if a[k] is not None]))
                                                 for k in agg[0]}
        # safety with the deployed flag: a positive clip is "caught" when it is called positive or sent to review
        rep['safety_deployed_q'] = {}
        for name, pos, pred in (('ef_le40', bt == 0, bp == 0), ('ef_lt50', bt <= 1, bp <= 1)):
            silent = pos & ~pred & ~dep
            rep['safety_deployed_q'][name] = dict(n=int(pos.sum()), called_or_flagged=float((pred | dep)[pos].mean()),
                                                  missed_silently=int(silent.sum()),
                                                  silent_false_alarm=float((pred & ~dep)[~pos].mean()))
        sm = wrong & ~dep
        rep['silent_misgrades'] = dict(n=int(sm.sum()), y=y[sm].round(1).tolist(), ef=yh[sm].round(1).tolist())
        rep['by_light'] = [dict(n=int((light == j).sum()), agree=float(ok_band[light == j].mean()),
                                ef_mae=float(err[light == j].mean())) for j in range(3)]
        report[mode] = rep
        rows_out[mode] = [dict(video=r['video'], y=r['y'], ef=r['ef'], sigma=s, lo80=v['lo80'], hi80=v['hi80'],
                               lo90=v['lo90'], hi90=v['hi90'], review=v['review'], light=int(lt))
                          for r, s, v, lt in zip(rows, sig, iv, light)]
    (OUT / 'hf_class.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    (OUT / 'hf_val_rows.json').write_text(json.dumps(rows_out), encoding='utf-8')
    for mode in ('full', 'keyframe'):
        r = report[mode]
        print(f'[{mode}] EF MAE {r["ef_mae"]:.2f}')
        for k, name in (('ef_lt50', 'EF<50'), ('ef_le40', 'EF<=40')):
            b = r[k]
            print(f'  {name}: AUC {b["auc"]:.3f} [{b["ci95"]["auc"][0]:.3f}, {b["ci95"]["auc"][1]:.3f}]  '
                  f'sens {b["sens"]:.3f} [{b["ci95"]["sens"][0]:.3f}, {b["ci95"]["sens"][1]:.3f}]  '
                  f'spec {b["spec"]:.3f} [{b["ci95"]["spec"][0]:.3f}, {b["ci95"]["spec"][1]:.3f}]  '
                  f'PPV {b["ppv"]:.3f} NPV {b["npv"]:.3f}  prevalence {b["prevalence"]:.3f}')
        t = r['three_class']
        print(f'  3-class agree {t["agree"]:.3f} kappa(lin) {t["kappa_linear"]:.3f} recall {[round(x, 3) for x in t["recall_per_band"]]}'
              f' confusion {t["confusion_ref_rows_pred_cols"]}')
        for k in ('review_margin6', 'review_interval80_split_halves', 'review_interval80_deployed_q'):
            v = r[k]
            print(f'  {k}: flagged {v["flagged"]:.3f} agree unflagged {v["agree_unflagged"]:.3f} '
                  f'misgraded flagged {v["misgraded_flagged"]:.3f}')
        for k, v in r['safety_deployed_q'].items():
            print(f'  {k}: {v["n"]} positives, called or flagged {v["called_or_flagged"]:.3f}, missed silently '
                  f'{v["missed_silently"]}, silent false alarm {v["silent_false_alarm"]:.3f}')
        print(f'  silent misgrades {r["silent_misgrades"]["n"]}: true {r["silent_misgrades"]["y"]} -> {r["silent_misgrades"]["ef"]}')
        print('  by light (green/yellow/red):', [(d['n'], round(d['agree'], 3), round(d['ef_mae'], 2)) for d in r['by_light']])


if __name__ == '__main__':
    main()
