"""2a clinical measurements on EchoNet VAL only (TEST is never touched here).

Uses the cached per-frame volumes of the on-board model (results/frames/t3_int_val.npz, the int model that runs on
the FPGA) and the very functions the UI / board use (tcp_image_ui/echo_ws.analyze / calibrated / clinical).
Decides, for each mode (full frames / real time):
  * review margin m around the LVEF boundaries 40 / 50 (2021 ESC bands <=40 / 41-49 / >=50): the smallest m (0.5 steps)
    such that >= 90 % of the VAL videos whose predicted band differs from the reference band are flagged
    "near a boundary, review"; reports how many videos get flagged and the band agreement of the rest;
  * RR irregularity threshold: RR intervals come from consecutive ED frames of the volume curve (EchoNet has no ECG);
    the threshold is the 90th percentile of the RR coefficient of variation over VAL videos with >= 3 beats
    (no rhythm labels exist, so "irregular" = the 10 % least regular clips); reported with the EF error of the
    flagged vs. the other clips and the index-beat EF error.
Writes results/clinical/clinical_val.json and prints the summary.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I eval_clinical.py
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
UI = ROOT.parents[0] / 'tcp_image' / 'tcp_image_ui'
sys.path.insert(0, str(UI))
sys.path.insert(0, str(ROOT))

import echo_ws  # noqa: E402
from beats import realtime_step  # noqa: E402


def band_ref(ef):
    return 0 if ef <= 40 else (1 if ef < 50 else 2)


def main():
    conf = echo_ws.load_config()
    d = ROOT / 'results' / 'frames'
    feat = np.load(d / 't3_int_val.npz')
    index = json.loads((d / 't3_int_val.json').read_text(encoding='utf-8'))['videos']
    out = dict(split='val', videos=len(index), features='t3_int', source=__doc__.split('\n')[0], modes={})
    for mode in ('full', 'realtime'):
        rows = []
        for v, e in index.items():
            s, n = e['start'], e['n']
            k = realtime_step(e['fps']) if mode == 'realtime' else 1
            r = echo_ws.analyze(feat['vol'][s:s + n][::k], e['fps'], k, conf['cfg'])
            ef = echo_ws.calibrated(r['ef'], conf, mode)
            c = echo_ws.clinical(r['beats'], e['fps'], ef, cal=lambda x: echo_ws.calibrated(x, conf, mode),
                                 cc=dict(review_margin=0.0, rr_cv_irregular=1e9))
            rows.append(dict(video=v, true=e['ef'], ef=ef, band=c['band'], band_ref=band_ref(e['ef']),
                             dist=min(abs(ef - 40), abs(ef - 50)) if np.isfinite(ef) else np.nan,
                             nbeats=c['n_beats'], rr_cv=c['rr_cv'], beat_sd=c['beat_sd'],
                             index_ef=c['index_beat']['ef'] if c['index_beat'] else None))
        ok = [r for r in rows if r['band'] is not None]
        wrong = [r for r in ok if r['band'] != r['band_ref']]
        dist_wrong = np.array([r['dist'] for r in wrong])
        m = 0.0
        while np.mean(dist_wrong < m) < 0.90:
            m += 0.5
        flagged = [r for r in ok if r['dist'] < m]
        kept = [r for r in ok if r['dist'] >= m]
        conf_mat = np.zeros((3, 3), int)
        for r in ok:
            conf_mat[r['band_ref'], r['band']] += 1
        res = dict(
            lvef=dict(videos=len(ok), agree=float(np.mean([r['band'] == r['band_ref'] for r in ok])),
                      confusion_ref_rows_pred_cols=conf_mat.tolist(), misgraded=len(wrong),
                      review_margin=m, misgraded_flagged=float(np.mean(dist_wrong < m)),
                      flagged=len(flagged), flagged_frac=len(flagged) / len(ok),
                      agree_unflagged=float(np.mean([r['band'] == r['band_ref'] for r in kept])),
                      agree_flagged=float(np.mean([r['band'] == r['band_ref'] for r in flagged]))))
        rr = [r for r in ok if r['rr_cv'] is not None]
        cv = np.array([r['rr_cv'] for r in rr])
        thr = float(np.percentile(cv, 90))
        err = np.array([abs(r['ef'] - r['true']) for r in rr])
        irr = cv > thr
        ib = [r for r in rr if r['index_ef'] is not None]
        res['rhythm'] = dict(
            videos_with_2rr=len(rr), videos_lt_3_beats=len(ok) - len(rr),
            rr_cv_percentiles={p: float(np.percentile(cv, p)) for p in (50, 75, 90, 95)},
            rr_cv_irregular=thr, irregular=int(irr.sum()),
            ef_mae_irregular=float(err[irr].mean()), ef_mae_regular=float(err[~irr].mean()),
            beat_sd_irregular=float(np.mean([r['beat_sd'] for r, f in zip(rr, irr) if f])),
            beat_sd_regular=float(np.mean([r['beat_sd'] for r, f in zip(rr, irr) if not f])),
            index_beat_mae_irregular=float(np.mean([abs(r['index_ef'] - r['true']) for r, f in zip(rr, irr)
                                                    if f and r['index_ef'] is not None])),
            median_ef_mae_same_clips=float(np.mean([abs(r['ef'] - r['true']) for r, f in zip(rr, irr)
                                                    if f and r['index_ef'] is not None])),
            beats_median=float(np.median([r['nbeats'] for r in ok])),
            clips_ge_5_beats=int(sum(r['nbeats'] >= 5 for r in ok)), index_beat_available=len(ib))
        out['modes'][mode] = res
    od = ROOT / 'results' / 'clinical'
    od.mkdir(parents=True, exist_ok=True)
    (od / 'clinical_val.json').write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
