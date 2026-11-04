"""③ beat-to-beat EF on whole videos (VAL for development; TEST once with --final-test), plus quality control.

Uses per-frame features from infer_videos.py and the parameters / calibration from tune_beats.py (TRAIN only).
Reports, per mode (full frames / real time = every k-th frame):
  * EF MAE / RMSE / R2 / bias (calibrated and raw), heart rate, beats per video, fallbacks;
  * diagnosis: EF from the predicted masks of the two traced frames (known ED / ES, no beat detection), and the
    tracing-disk ceiling, so segmentation error and beat-detection error can be told apart;
  * quality control: a per-video QC score (fraction of frames with >1 component before cleaning, mean uncertainty,
    frames touching the border, spread of beat EFs, fallback) -> MAE of the kept videos when the 5 / 10 / 20 % most
    suspicious are flagged, and MAE of the flagged ones.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I eval_ef.py --features t3_fp32 --split val
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np          # noqa: E402

from beats import analyze, metrics, realtime_step   # noqa: E402
from common import CACHE, RESULTS   # noqa: E402
from volume import ef as ef_of   # noqa: E402

LOCK = RESULTS / 'final_test' / 'test_set_used.txt'


def qc_score(feat, s, n, r):
    """higher = more suspicious; each term roughly 0..1."""
    nc = feat['ncomp'][s:s + n]
    terms = dict(multi=float(np.mean(nc > 1)), unc=float(np.mean(feat['unc'][s:s + n])),
                 border=float(np.mean(feat['border'][s:s + n])), empty=float(np.mean(feat['area_clean'][s:s + n] == 0)),
                 spread=min(1.0, r.get('ef_sd', 0.0) / 20.0), fallback=float(r['fallback']))
    return terms, terms['multi'] + 2 * terms['unc'] + terms['border'] + 2 * terms['empty'] + terms['spread'] + terms['fallback']


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--features', required=True)
    ap.add_argument('--config', default=None, help='beats_config_<x>.json (default: same features name, fp32 tuning)')
    ap.add_argument('--split', default='val', choices=('val', 'test'))
    ap.add_argument('--final-test', action='store_true')
    ap.add_argument('--skip-lock', action='store_true', help='only for final_test_echonet.py, which wrote the lock itself')
    args = ap.parse_args()
    if args.split == 'test' and args.skip_lock:
        if not (args.final_test and LOCK.exists()):
            sys.exit('--skip-lock is only for final_test_echonet.py after it wrote the lock')
    elif args.split == 'test':
        if not args.final_test:
            sys.exit('the test split needs --final-test (used once)')
        if LOCK.exists():
            sys.exit('test set already used: ' + LOCK.read_text(encoding='utf-8'))
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        LOCK.write_text('%s  features=%s config=%s\n' % (datetime.now(timezone.utc).isoformat(), args.features, args.config),
                        encoding='utf-8')
    cfgf = RESULTS / (args.config or f'beats_config_{args.features.replace("_int", "_fp32")}.json')
    conf = json.loads(cfgf.read_text(encoding='utf-8'))
    d = RESULTS / 'frames'
    feat = np.load(d / f'{args.features}_{args.split}.npz')
    index = json.loads((d / f'{args.features}_{args.split}.json').read_text(encoding='utf-8'))['videos']
    meta = json.loads((CACHE / f'{args.split}_meta.json').read_text(encoding='utf-8'))['frames']
    trace = {m['video']: m for m in meta[::2]}
    rows = []
    for v, e in index.items():
        s, n = e['start'], e['n']
        vol = feat['vol'][s:s + n]
        row = dict(video=v, ef=e['ef'], fps=e['fps'], n=n)
        for mode in ('full', 'realtime'):
            k = realtime_step(e['fps']) if mode == 'realtime' else 1
            r = analyze(vol[::k], e['fps'], k, conf['cfg'])
            cal = conf[mode]
            row[mode] = dict(step=k, raw=r['ef'], ef=cal['a'] * r['ef'] + cal['b'], beats=len(r['beats']), hr=r['hr'],
                             fallback=r['fallback'], ef_sd=r.get('ef_sd'))
            if mode == 'full':
                row['qc_terms'], row['qc'] = qc_score(feat, s, n, r)
        if v in trace and len(e['traced']) == 2:
            t = trace[v]
            ed, es = t['frame'], next(m for m in meta if m['video'] == v and m['phase'] == 'ES')['frame']
            row['known_frames_raw'] = ef_of(vol[ed], vol[es])
            row['trace_disk'] = t['ef_disk_trace']
        rows.append(row)
    true = np.array([r['ef'] for r in rows])
    out = dict(features=args.features, split=args.split, config=conf['cfg'], videos=len(rows), modes={})
    for mode in ('full', 'realtime'):
        p = np.array([r[mode]['ef'] for r in rows])
        raw = np.array([r[mode]['raw'] for r in rows])
        ok = np.isfinite(p)
        out['modes'][mode] = dict(calibrated=metrics(p[ok], true[ok]), raw=metrics(raw[ok], true[ok]),
                                  beats_mean=float(np.mean([r[mode]['beats'] for r in rows])),
                                  fallback=int(sum(r[mode]['fallback'] for r in rows)),
                                  hr_median=float(np.nanmedian([r[mode]['hr'] or np.nan for r in rows])))
    kn = [r for r in rows if 'known_frames_raw' in r and np.isfinite(r['known_frames_raw'])]
    a, b = conf['full']['a'], conf['full']['b']
    out['diagnosis'] = dict(
        known_frames_calibrated=metrics([a * r['known_frames_raw'] + b for r in kn], [r['ef'] for r in kn]),
        known_frames_raw=metrics([r['known_frames_raw'] for r in kn], [r['ef'] for r in kn]),
        tracing_disk_ceiling=metrics([r['trace_disk'] for r in kn], [r['ef'] for r in kn]),
        tracing_disk_ceiling_good=metrics([r['trace_disk'] for r in kn if abs(r['trace_disk'] - r['ef']) <= 10],
                                          [r['ef'] for r in kn if abs(r['trace_disk'] - r['ef']) <= 10]))
    err = np.abs(np.array([r['full']['ef'] for r in rows]) - true)
    qc = np.array([r['qc'] for r in rows])
    order = np.argsort(-qc)
    out['qc'] = {}
    for frac in (0.05, 0.10, 0.20):
        k = int(round(frac * len(rows)))
        flagged = np.zeros(len(rows), bool)
        flagged[order[:k]] = True
        out['qc'][f'flag_{int(frac * 100)}pct'] = dict(flagged=k, mae_kept=float(err[~flagged].mean()),
                                                       mae_flagged=float(err[flagged].mean()), mae_all=float(err.mean()))
    out['qc']['spearman_qc_vs_error'] = float(np.corrcoef(np.argsort(np.argsort(qc)), np.argsort(np.argsort(err)))[0, 1])
    od = RESULTS / ('final_test' if args.split == 'test' else 'ef')
    od.mkdir(parents=True, exist_ok=True)
    (od / f'{args.features}_{args.split}.json').write_text(json.dumps(dict(out, per_video=rows), indent=1), encoding='utf-8')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
