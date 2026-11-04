"""③ beat-to-beat analysis of an LV volume curve (numpy + scipy; the host UI uses this same file).

analyze(vol, fps, step, cfg) -> dict
  vol   single-plane volumes of the analysed frames (every `step`-th frame of the video; step = 1 full, > 1 real time)
  1. optional median smoothing (cfg['smooth'] frames, odd);
  2. end-systole = troughs of the curve: scipy.signal.find_peaks(-v) with a minimum distance of cfg['dist_s'] seconds
     and a prominence of cfg['prom'] x the curve's 5-95 % range;
  3. cycle length = median trough spacing (0.8 s when fewer than two troughs);
  4. for every trough: end-diastole = the maximum within the preceding cfg['win'] x cycle; beat EF = (V_ED - V_ES) / V_ED;
  5. video EF = mean (cfg['agg']) of the beats; with no complete beat: (max - min) / max of the whole curve (fallback);
  heart rate = 60 / cycle length.
Calibration (fit on the training set only): EF = a * EF_raw + b.
"""
import numpy as np
from scipy.signal import find_peaks, medfilt

DEFAULT = dict(smooth=1, dist_s=0.3, prom=0.2, win=0.6, agg='mean')


def analyze(vol, fps, step=1, cfg=None):
    cfg = dict(DEFAULT, **(cfg or {}))
    v = np.asarray(vol, np.float64)
    fe = fps / step                                         # analysed frames per second
    if cfg['smooth'] > 1 and len(v) >= cfg['smooth']:
        v = medfilt(v, cfg['smooth'])
    out = dict(beats=[], fallback=False, hr=None, cycle_s=None)
    if len(v) < 3 or v.max() <= 0:
        out.update(ef=float('nan'), fallback=True)
        return out
    rng = np.percentile(v, 95) - np.percentile(v, 5)
    troughs, _ = find_peaks(-v, distance=max(1, int(round(cfg['dist_s'] * fe))), prominence=max(cfg['prom'] * rng, 1e-9))
    cycle = float(np.median(np.diff(troughs))) if len(troughs) >= 2 else 0.8 * fe
    if len(troughs) >= 2:
        out['cycle_s'] = cycle / fe
        out['hr'] = 60.0 / out['cycle_s']
    w = max(2, int(round(cfg['win'] * cycle)))
    for t in troughs:
        a = max(0, t - w)
        if t - a < 2:
            continue
        e = a + int(np.argmax(v[a:t]))
        if v[e] <= v[t]:
            continue
        out['beats'].append(dict(ed=int(e * step), es=int(t * step), v_ed=float(v[e]), v_es=float(v[t]),
                                 ef=float(100 * (v[e] - v[t]) / v[e])))
    if out['beats']:
        efs = [b['ef'] for b in out['beats']]
        out['ef'] = float(np.mean(efs) if cfg['agg'] == 'mean' else np.median(efs))
        out['ef_sd'] = float(np.std(efs))
    else:
        out['ef'] = float(100 * (v.max() - v.min()) / v.max())
        out['fallback'] = True
    return out


def realtime_step(fps, board_fps=25.0):
    """analyse every k-th frame so that the analysed rate keeps up with the board: k = ceil(fps / board_fps)."""
    return max(1, int(np.ceil(fps / board_fps - 1e-9)))


def calibrate(raw, true):
    a, b = np.polyfit(np.asarray(raw), np.asarray(true), 1)
    return float(a), float(b)


def metrics(pred, true):
    p, t = np.asarray(pred, float), np.asarray(true, float)
    d = p - t
    return dict(mae=float(np.abs(d).mean()), rmse=float(np.sqrt((d ** 2).mean())), bias=float(d.mean()),
                r2=float(1 - (d ** 2).sum() / ((t - t.mean()) ** 2).sum()), r=float(np.corrcoef(p, t)[0, 1]), n=len(t))
