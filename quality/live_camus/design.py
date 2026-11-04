"""v5.26 design: the CAMUS-calibrated rolling quality light + view as the board's live mode will compute them.

Board chain (CAMUS source = a 128 x 128 .y8 at 50 fps, fixed crop 0 0 128 -> the 128 frame is the source frame itself):
  F1 (program 2, lv_r128, CAMUS-trained) on every k-th frame of the stream (t % k == 0), integer logits q, mask
  clean(q >= 1); window = the last WIN frames, updated every EVERY frames: sector from ALL window frames, per-frame
  features on the F1 frames, stability / roughness over the F1 masks in order; score = portable weights + light
  thresholds (quality_camus.json, CAMUS TRAIN), standardisation mu / sd re-taken here on CAMUS TRAIN windows of the same
  chain (label-free). View: RV program (rv_pl1_5 integer, full 128 frame) on every VK-th frame (a multiple of k),
  ratio = largest RV component / F1 LV area of that frame, A4C when the median of the last 3 ratios >= view_t
  (TRAIN-best on this chain).
Streams: each CAMUS half sequence (ED -> ES) played back and forth at 50 fps from its own FrameRate, 2 s per clip
(same as scan_sim/stream_camus.py). TRAIN fits, VAL reports once. TEST not touched.
Writes design.json (per k: mu / sd, VAL AUC Poor of the clip-mean window score, window lights by expert grade, view).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I design.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch
from scipy import ndimage

HERE = Path(__file__).resolve().parent
Q = HERE.parent
PLD = Q.parent
sys.path.insert(0, str(Q))
sys.path.insert(0, str(PLD / 'echonet'))
from features import FEATURES, clean, frame_features, sector_mask  # noqa: E402
from int8_gpu import IntegerModel  # noqa: E402

CACHE = PLD / 'echo' / 'data' / 'cache'
CAMUS = PLD / 'echo' / 'data' / 'camus' / 'database_nifti'
QC = json.loads((Q / 'results' / 'quality_camus.json').read_text(encoding='utf-8'))
M = QC['models']['portable']
FPS, SECONDS, WIN, EVERY, VIEW_N = 50.0, 2.0, 50, 10, 3
KS = (2, 3, 4, 5)


def integer_model(qd):
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        im = IntegerModel(plan, {k: z[k] for k in z.files}, 'cuda')

    def run(x128):
        out = []
        for i in range(0, len(x128), 128):
            xb = torch.from_numpy(np.repeat(np.ascontiguousarray(x128[i:i + 128])[:, None], 3, 1))
            out.append(im(xb)[:, 0].to(torch.int8).cpu().numpy())
        return np.concatenate(out)
    return run


def fps_of(p, view):
    t = (CAMUS / p / f'Info_{view}.cfg').read_text()
    return float(dict(l.split(': ', 1) for l in t.splitlines() if ': ' in l)['FrameRate'])


def streams(split):
    ix = json.loads((CACHE / f'seq_{split}_index.json').read_text(encoding='utf-8'))
    x = np.load(CACHE / f'seq_{split}_img128.npy', mmap_mode='r')
    q = np.load(Q / 'results' / f'seq_{split}_lv_r128_q.npy', mmap_mode='r')
    for e in ix:
        n0, fps = e['length'], fps_of(e['patient'], e['view'])
        cyc = list(range(n0)) + list(range(n0 - 2, 0, -1))
        src = [e['start'] + cyc[int(t / FPS * fps) % len(cyc)] for t in range(int(round(SECONDS * FPS)))]
        yield e, np.asarray(x)[src], np.asarray(q)[src], np.array(src)


def window_features(fr, q, sel):
    """fr [WIN, 128, 128] all window frames, sel = indices (in the window) of the F1 frames"""
    sector = sector_mask(fr)
    edge = sector & ~ndimage.binary_erosion(sector, iterations=2)
    rows = np.nonzero(sector.any(1))[0]
    top, h = rows.min(), rows.max() - rows.min() + 1
    per, masks = [], []
    for t in sel:
        f, m = frame_features(fr[t], q[t], sector, edge, top, h)
        f.pop('la_ok', None)
        per.append(f)
        masks.append(m)
    out = {k: float(np.mean([p[k] for p in per])) for k in FEATURES if k not in ('stability', 'roughness')}
    d = [2 * (a & b).sum() / (a.sum() + b.sum()) if a.sum() + b.sum() else 1.0 for a, b in zip(masks[1:], masks[:-1])]
    out['stability'] = float(np.mean(d)) if d else 1.0
    area = np.array([m.sum() for m in masks], float)
    out['roughness'] = float(np.abs(np.diff(area, 2)).mean() / max(area.mean(), 1)) if len(area) > 2 else 0.0
    return [out[k] for k in FEATURES], masks


def collect(split, k, rv):
    rows = []
    for e, fr, q, src in streams(split):
        W = []
        for t in range(WIN - 1, len(fr), EVERY):
            a = t - WIN + 1
            sel = [i for i in range(WIN) if (a + i) % k == 0]
            f, _ = window_features(fr[a:t + 1], q[a:t + 1], sel)
            W.append(f)
        vk = k * max(1, round(25 / k))                      # view about every 0.5 s, a multiple of k
        vt = [t for t in range(len(fr)) if t % vk == 0 and t > 0]
        lv = [int(clean(q[t] >= 1)[0].sum()) if (q[t] >= 1).any() else 0 for t in vt]
        rv_area = rv(fr[vt]) if vt else []
        ratio = [r / max(l, 1) for r, l in zip(rv_area, lv)]
        rows.append(dict(patient=e['patient'], view=e['view'], W=np.array(W), ratio=ratio))
    return rows


def main():
    f1 = integer_model(PLD / 'echo' / 'results' / 'quant' / 'lv_r128')
    rvm = integer_model(PLD / 'cardiac_rv' / 'results' / 'quant' / 'rv_pl1_5')

    def rv(x):
        lg = rvm(x)
        out = []
        for g in lg:
            lab, n = ndimage.label(g >= 1)
            out.append(int(np.bincount(lab.ravel())[1:].max()) if n else 0)
        return out
    # the cached F1 logits are the board's (infer_seq.py checked GPU == CPU reference); spot check once more here
    x = np.load(CACHE / 'seq_val_img128.npy', mmap_mode='r')
    qv = np.load(Q / 'results' / 'seq_val_lv_r128_q.npy', mmap_mode='r')
    assert np.array_equal(f1(np.asarray(x[:64])), np.asarray(qv[:64])), 'F1 cache differs from the integer model'
    grade = {(r['patient'], r['view']): r['quality'] for r in QC['val_rows']}
    w = np.array([M['weights'][c] for c in FEATURES])
    res = {}
    for k in KS:
        tr, va = collect('train', k, rv), collect('val', k, rv)
        Xt = np.concatenate([r['W'] for r in tr])
        mu, sd = Xt.mean(0), Xt.std(0) + 1e-12
        sc_all, lt_all, g_all, clip = [], [], [], []
        for r in va:
            s = M['weights']['bias'] + ((r['W'] - mu) / sd) @ w
            lt = np.where(s >= M['t_red'], 2, np.where(s >= M['t_green'], 1, 0))
            g = grade[(r['patient'], r['view'])]
            sc_all += list(s)
            lt_all += list(lt)
            g_all += [g] * len(s)
            clip.append((float(s.mean()), g))
        cs = np.array([c[0] for c in clip])
        cg = np.array([c[1] for c in clip])
        auc = float(np.mean([(a > b) + 0.5 * (a == b) for a in cs[cg == 'Poor'] for b in cs[cg != 'Poor']]))
        lt_all, g_all = np.array(lt_all), np.array(g_all)
        lights = {G: np.bincount(lt_all[g_all == G], minlength=3).tolist() for G in ('Good', 'Medium', 'Poor')}

        def view_rows(rows):
            return [(float(np.median(r['ratio'])), r['view'] == '4CH') for r in rows if r['ratio']]
        vtr, vva = np.array(view_rows(tr)), np.array(view_rows(va))
        vt = float(max(np.unique(vtr[:, 0]), key=lambda v: ((vtr[:, 0] >= v) == vtr[:, 1]).mean()))
        vacc_tr = float(((vtr[:, 0] >= vt) == vtr[:, 1]).mean())
        vacc_va = float(((vva[:, 0] >= vt) == vva[:, 1]).mean())
        res[k] = dict(mu=mu.tolist(), sd=sd.tolist(), val_auc_poor=auc, val_window_lights=lights, view_t=vt,
                      view_acc_train=vacc_tr, view_acc_val=vacc_va, pl_share_f1=FPS / k * 0.023)
        print(f'k={k}: VAL AUC Poor {auc:.3f}; window lights G/M/P {lights}; view_t {vt:.3f} acc TRAIN {vacc_tr:.3f} '
              f'VAL {vacc_va:.3f}; F1 PL share ~{FPS / k * 0.023:.0%}', flush=True)
    (HERE / 'design.json').write_text(json.dumps(dict(win=WIN, every=EVERY, fps=FPS, seconds=SECONDS, features=FEATURES,
                                                      portable=M, by_k=res), indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
