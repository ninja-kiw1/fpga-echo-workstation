r"""ONE-TIME EchoNet-Dynamic TEST evaluation of innovations 1-3 + heart-failure classes through the board C
(echo_core.dll), stage 2 of final_test_2026-10-25/prereg.json. Runs only after echonet/final_test_echonet.py wrote its
lock and quality/echonet_q.py --split test stored program 16's integer logits; writes its own lock first.
Per TEST video, both board modes (quality_kf.run_video: 'full' every frame, 'keyframe' the recommended schedule):
EF calibrated with the mode's TRAIN-subset a / b, interval + light from the deployed eq_model_<mode>.json (q80 / q90 from
VAL). Nothing is fitted on TEST. Metrics = hf_class.py's (with the deployed q only) + MAE / RMSE / r, interval coverage,
keyframe share and keyframe-vs-full EF difference.
Writes results/final_test_kf/{lock.txt, final_test_kf.json, rows.json (EchoNet-derived: not shared)}.

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe -I final_test_kf.py
"""
import json
import sys
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import quality_kf as qk  # noqa: E402
from conformal_ef import band  # noqa: E402
from hf_class import kappa_linear, review_split, with_ci  # noqa: E402

echo_core = qk.echo_core
OUT = HERE / 'results' / 'final_test_kf'
ECHONET_LOCK = qk.E / 'results' / 'final_test' / 'test_set_used.txt'


def main():
    if not ECHONET_LOCK.exists():
        sys.exit('run echonet/final_test_echonet.py first (its lock is missing)')
    if not (HERE / 'results' / 'q' / 't3_test_q112.npy').exists():
        sys.exit('run echonet_q.py --split test first')
    OUT.mkdir(parents=True, exist_ok=True)
    lock = OUT / 'lock.txt'
    if lock.exists():
        sys.exit('already run: ' + lock.read_text(encoding='utf-8'))
    lock.write_text('%s stage 2 of final_test_2026-10-25/prereg.json (deployed configs, nothing fitted on TEST)\n'
                    % datetime.now(timezone.utc).isoformat(), encoding='utf-8')
    names = sorted(qk.data('test')[0])
    with Pool(16) as pool:
        per = list(pool.imap(qk.run_video, [('test', v) for v in names], chunksize=4))
    conf = json.loads((qk.E / 'results' / 'beats_config_t3_int.json').read_text(encoding='utf-8'))
    report, rows_out, yh_mode = dict(split='test', videos=len(per), prereg='final_test_2026-10-25/prereg.json'), {}, {}
    for mode in ('full', 'keyframe'):
        m = json.loads((qk.OUT / f'eq_model_{mode}.json').read_text(encoding='utf-8'))
        rows = qk.predictions(per, mode, m['a'], m['b'], conf['cfg'])
        em = echo_core.eq_model(dict(m, b=m['b_sigma']))
        y = np.array([r['y'] for r in rows])
        yh = np.array([r['ef'] for r in rows])
        yh_mode[mode] = yh
        iv = [echo_core.interval(em, r['ef'], r['sd'], r['beats'], r['fallback'], r) for r in rows]
        F = np.array([[r[k] for k in echo_core.QF] for r in rows])
        score = np.c_[np.ones(len(F)), (F - np.array(m['mu'])) / np.array(m['sd'])] @ np.array(m['w'])
        light = np.where(score >= m['t_red'], 2, np.where(score >= m['t_green'], 1, 0))
        bt, bp = np.array([band(v) for v in y]), np.array([band(v) for v in yh])
        cm = np.zeros((3, 3), int)
        np.add.at(cm, (bt, bp), 1)
        ok_band, wrong = bp == bt, bp != bt
        err = np.abs(yh - y)
        lo80, hi80 = np.array([v['lo80'] for v in iv]), np.array([v['hi80'] for v in iv])
        lo90, hi90 = np.array([v['lo90'] for v in iv]), np.array([v['hi90'] for v in iv])
        dep = np.array([bool(v['review']) for v in iv])
        rep = dict(ef_mae=float(err.mean()), ef_rmse=float(np.sqrt((err ** 2).mean())),
                   ef_r=float(np.corrcoef(y, yh)[0, 1]), ef_bias=float((yh - y).mean()),
                   key_frac=float(np.mean([p[mode]['key_frac'] for p in per])),
                   coverage80=float(((y >= lo80) & (y <= hi80)).mean()), coverage90=float(((y >= lo90) & (y <= hi90)).mean()),
                   width80_median=float(np.median(hi80 - lo80)), review_rate=float(dep.mean()),
                   ef_lt50=with_ci(yh, y, 50, True), ef_le40=with_ci(yh, y, 40, False),
                   three_class=dict(confusion_ref_rows_pred_cols=cm.tolist(), agree=float(ok_band.mean()),
                                    kappa_linear=kappa_linear(cm),
                                    recall_per_band=[float(cm[j, j] / max(cm[j].sum(), 1)) for j in range(3)]),
                   review_interval80_deployed_q=review_split(dep, wrong, ok_band))
        rep['safety_deployed_q'] = {}
        for name, pos, pred in (('ef_le40', bt == 0, bp == 0), ('ef_lt50', bt <= 1, bp <= 1)):
            silent = pos & ~pred & ~dep
            rep['safety_deployed_q'][name] = dict(n=int(pos.sum()), called_or_flagged=float((pred | dep)[pos].mean()),
                                                  missed_silently=int(silent.sum()),
                                                  silent_false_alarm=float((pred & ~dep)[~pos].mean()))
        sm = wrong & ~dep
        rep['silent_misgrades'] = dict(n=int(sm.sum()), y=y[sm].round(1).tolist(), ef=yh[sm].round(1).tolist())
        rep['by_light'] = [dict(n=int((light == j).sum()), agree=float(ok_band[light == j].mean()) if (light == j).any() else None,
                                ef_mae=float(err[light == j].mean()) if (light == j).any() else None) for j in range(3)]
        report[mode] = rep
        rows_out[mode] = [dict(video=r['video'], y=r['y'], ef=r['ef'], lo80=v['lo80'], hi80=v['hi80'], lo90=v['lo90'],
                               hi90=v['hi90'], review=v['review'], light=int(lt)) for r, v, lt in zip(rows, iv, light)]
    d = np.abs(yh_mode['keyframe'] - yh_mode['full'])
    report['keyframe_vs_full'] = dict(mean_abs_diff=float(d.mean()), p95_abs_diff=float(np.percentile(d, 95)),
                                      mae_change=report['keyframe']['ef_mae'] - report['full']['ef_mae'])
    (OUT / 'final_test_kf.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    (OUT / 'rows.json').write_text(json.dumps(rows_out), encoding='utf-8')
    lock.write_text(lock.read_text(encoding='utf-8') + '%s done\n' % datetime.now(timezone.utc).isoformat(), encoding='utf-8')
    for mode in ('full', 'keyframe'):
        r = report[mode]
        print(f'[{mode}] EF MAE {r["ef_mae"]:.2f} RMSE {r["ef_rmse"]:.2f} r {r["ef_r"]:.3f} bias {r["ef_bias"]:+.2f} '
              f'key {r["key_frac"]:.3f} | coverage 80 {r["coverage80"]:.3f} 90 {r["coverage90"]:.3f} review {r["review_rate"]:.3f}')
        for k, name in (('ef_lt50', 'EF<50'), ('ef_le40', 'EF<=40')):
            b = r[k]
            print(f'  {name}: AUC {b["auc"]:.3f} [{b["ci95"]["auc"][0]:.3f}, {b["ci95"]["auc"][1]:.3f}] sens {b["sens"]:.3f} '
                  f'spec {b["spec"]:.3f} PPV {b["ppv"]:.3f} NPV {b["npv"]:.3f} prevalence {b["prevalence"]:.3f}')
        t = r['three_class']
        print(f'  3-class agree {t["agree"]:.3f} kappa {t["kappa_linear"]:.3f}; review split {r["review_interval80_deployed_q"]}')
        for k, v in r['safety_deployed_q'].items():
            print(f'  {k}: {v["n"]} positives, called or flagged {v["called_or_flagged"]:.3f}, missed silently {v["missed_silently"]}')
        print(f'  silent misgrades {r["silent_misgrades"]["n"]}; by light', [(x['n'], x['agree'], x['ef_mae']) for x in r['by_light']])
    print('keyframe vs full', report['keyframe_vs_full'])


if __name__ == '__main__':
    main()
