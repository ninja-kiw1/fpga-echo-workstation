"""Feasibility for porting the CAMUS-calibrated quality light (scan_sim/stream_camus.py) into the board's live mode:
does the F1 LV program (lv_r128, CAMUS-trained) + the CAMUS 'portable' score still mean something on EchoNet frames
(the live demo sources are EchoNet / HMC-QU screen recordings, not CAMUS)?

EchoNet VAL videos (TEST not touched), first N frames (about 1 s, = one live window), 112 -> 128 bilinear, R = G = B,
F1 integer model (int8_gpu, bit-exact with the PL). Per video:
  dice16     F1 mask (cleaned, back to 112 nearest) vs program 16's cached board mask (t3_int) -- does F1 segment EchoNet?
  score/light  CAMUS portable (CAMUS TRAIN standardisation, weights, thresholds; nothing refitted) on
               full = every frame of the window, sub = every SUB-th frame only (PL budget on the board)
Against |EF error| of the board chain (conformal_ef.predictions, keyframe mode): Spearman, MAE by light,
share of |err| > 10 caught by red / red + yellow. Reference = the current board light (program 16, EchoNet
standardisation): ../results/quality_echonet.json.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I echonet_f1.py
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

HERE = Path(__file__).resolve().parent
Q = HERE.parent
PLD = Q.parent
sys.path.insert(0, str(Q))
import features as _features  # noqa: E402
from features import FEATURES, clean, sequence_features  # noqa: E402

_frame_features = _features.frame_features


def _ff_no_la(*a, **kw):        # the empty-mask path adds 'la_ok' even without LA logits -> key mismatch across frames
    f, m = _frame_features(*a, **kw)
    f.pop('la_ok', None)
    return f, m


_features.frame_features = _ff_no_la
from infer_seq import model  # noqa: E402
from conformal_ef import predictions  # noqa: E402

E = PLD / 'echonet'
N = 50          # frames per video (EchoNet ~50 fps -> about 1 s)
SUB = 5         # board budget variant: F1 on every 5th frame
M = json.loads((Q / 'results' / 'quality_camus.json').read_text(encoding='utf-8'))['models']['portable']


def score(f):
    s = M['weights']['bias'] + sum(M['weights'][k] * (f[k] - mu) / sd for k, mu, sd in zip(FEATURES, M['mean'], M['sd']))
    return float(s), (2 if s >= M['t_red'] else (1 if s >= M['t_green'] else 0))


def spearman(a, b):
    return float(np.corrcoef(np.argsort(np.argsort(a)), np.argsort(np.argsort(b)))[0, 1])


def main():
    run = model('lv_r128')
    va = {r['video']: r for r in predictions('val')}
    idx = json.loads((E / 'results' / 'frames' / 't3_int_val.json').read_text(encoding='utf-8'))['videos']
    vid = json.loads((E / 'data' / 'cache' / 'val_videos.json').read_text(encoding='utf-8'))
    frames = np.load(E / 'data' / 'cache' / 'val_videos.npy', mmap_mode='r')
    packed = np.load(E / 'results' / 'frames' / 't3_int_val.npz')['masks']
    rows, t0 = [], time.time()
    for k, v in enumerate(sorted(idx)):
        e, ev = idx[v], vid[v]
        n = min(N, ev['n'])
        fr = np.ascontiguousarray(frames[ev['start']:ev['start'] + n])
        x = np.clip(np.rint(ndimage.zoom(fr.astype(np.float32), (1, 128 / 112, 128 / 112), order=1)), 0, 255).astype(np.uint8)
        q = run(x)
        m16 = np.unpackbits(packed[e['start']:e['start'] + n], axis=1)[:, :112 * 112].reshape(-1, 112, 112).astype(bool)
        d = []
        for qq, mm in zip(q, m16):
            mf = clean(qq >= 1)[0] if (qq >= 1).any() else qq >= 1
            mf = ndimage.zoom(mf.astype(np.uint8), 112 / 128, order=0).astype(bool)
            s = mf.sum() + mm.sum()
            d.append(2 * (mf & mm).sum() / s if s else 1.0)
        ff, _ = sequence_features(x, q, list(range(n)))
        sub = list(range(0, n, SUB))
        fs, _ = sequence_features(x[sub], q[sub], list(range(len(sub))))
        r = va[v]
        rows.append(dict(video=v, dice16=float(np.mean(d)), err=abs(r['keyframe']['ef'] - r['ef']),
                         full=score(ff), sub=score(fs), feat_full=ff, feat_sub=fs))
        if (k + 1) % 200 == 0:
            print(f'{k + 1}/{len(idx)} ({time.time() - t0:.0f} s)', flush=True)
    err = np.array([r['err'] for r in rows])
    big = err > 10
    out = dict(videos=len(rows), n_frames=N, sub=SUB, dice16_mean=float(np.mean([r['dice16'] for r in rows])),
               dice16_p10=float(np.percentile([r['dice16'] for r in rows], 10)))
    for name in ('full', 'sub'):
        sc = np.array([r[name][0] for r in rows])
        lt = np.array([r[name][1] for r in rows])
        out[name] = dict(lights=np.bincount(lt, minlength=3).tolist(), spearman=spearman(sc, err),
                         mae_by_light=[float(err[lt == j].mean()) if (lt == j).any() else None for j in range(3)],
                         big_err=int(big.sum()), big_red=float(np.mean(lt[big] == 2)), big_red_yellow=float(np.mean(lt[big] >= 1)),
                         feature_means={k: float(np.mean([r['feat_' + name][k] for r in rows])) for k in FEATURES})
    out['camus_train_feature_means'] = dict(zip(FEATURES, M['mean']))
    ref = json.loads((Q / 'results' / 'quality_echonet.json').read_text(encoding='utf-8'))
    out['reference_board_light'] = dict(lights=ref['lights'], spearman=ref['keyframe']['score']['spearman'],
                                        mae_by_light=ref['keyframe']['mae_by_light'],
                                        big_red=ref['keyframe']['big_err_red'], big_red_yellow=ref['keyframe']['big_err_red_or_yellow'])
    (HERE / 'echonet_f1.json').write_text(json.dumps(dict(summary=out, rows=[{k: r[k] for k in ('video', 'dice16', 'err', 'full', 'sub')}
                                                                             for r in rows]), indent=1), encoding='utf-8')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
