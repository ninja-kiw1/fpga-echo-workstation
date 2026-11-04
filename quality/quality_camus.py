"""Innovation ② step 3: quality score from the zero-extra-model features, fitted on CAMUS TRAIN, validated against the
CAMUS expert quality grades (Good / Medium / Poor, per patient and view) on VAL. TEST not touched.

Score = L2-regularised logistic regression (numpy IRLS, lambda 1) of "Poor" on the standardised sequence features of
features.FEATURES (portable: also computable on EchoNet; the variant +la_ok adds the LA program). Board cost: one dot
product per sequence. Lights (thresholds from TRAIN only): red when the score reaches the value that puts 80 % of the
TRAIN Poor sequences in red; green below the value that keeps 80 % of the TRAIN Good sequences green; yellow between.
Reported on VAL: AUC Poor vs rest, AUC Good vs rest (low score = good), Spearman with the grade (0 / 1 / 2), per-feature
AUC (sign fixed on TRAIN), lights x grade table, per view. Also F1's ED / ES Dice by light (does red mean worse
segmentation?).

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I quality_camus.py
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from features import FEATURES, sequence_features  # noqa: E402

PLD = HERE.parent
CACHE = PLD / 'echo' / 'data' / 'cache'
OUT = HERE / 'results'
GRADE = {'Good': 0, 'Medium': 1, 'Poor': 2}


def auc(score, pos):
    s, p = np.asarray(score, float), np.asarray(pos, bool)
    r = np.argsort(np.argsort(s)) + 1.0
    for v in np.unique(s):                      # average ranks for ties
        k = s == v
        r[k] = r[k].mean()
    n1, n0 = p.sum(), (~p).sum()
    return float((r[p].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else float('nan')


def spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def logistic(X, y, lam=1.0, iters=50):
    Xb = np.c_[np.ones(len(X)), X]
    w = np.zeros(Xb.shape[1])
    R = lam * np.eye(Xb.shape[1])
    R[0, 0] = 0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Xb @ w))
        g = Xb.T @ (p - y) + R @ w
        H = Xb.T @ (Xb * (p * (1 - p))[:, None]) + R
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def table(split):
    x = np.load(CACHE / f'seq_{split}_img128.npy', mmap_mode='r')
    q = np.load(OUT / f'seq_{split}_lv_r128_q.npy', mmap_mode='r')
    qa = np.load(OUT / f'seq_{split}_la_r128_q.npy', mmap_mode='r')
    ix = json.loads((CACHE / f'seq_{split}_index.json').read_text(encoding='utf-8'))
    meta = json.loads((CACHE / f'{split}_meta.json').read_text(encoding='utf-8'))
    meta = meta if isinstance(meta, list) else meta['frames']
    qual = {(m['patient'], m['view']): m['quality'] for m in meta}
    rows = []
    for e in ix:
        s, n = e['start'], e['length']
        use = list(range(1, n - 1))             # in-between frames only (see features.py)
        f, _ = sequence_features(np.asarray(x[s:s + n]), np.asarray(q[s:s + n]), use, np.asarray(qa[s:s + n]))
        rows.append(dict(patient=e['patient'], view=e['view'], quality=qual[(e['patient'], e['view'])], **f))
    return rows


def f1_dice():
    """F1 board-equivalent Dice per VAL (patient, view, phase), from echo/results/quant/lv_r128/summary.json if it
    lists per-frame values, else None"""
    p = PLD / 'echo' / 'results' / 'final_val_per_frame.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None


def main():
    OUT.mkdir(exist_ok=True)
    tr, va = table('train'), table('val')
    res = dict(n_train=len(tr), n_val=len(va), models={})
    for name, feats in (('portable', FEATURES), ('portable+la', FEATURES + ['la_ok'])):
        Xtr = np.array([[r[k] for k in feats] for r in tr])
        Xva = np.array([[r[k] for k in feats] for r in va])
        mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
        ytr = np.array([r['quality'] == 'Poor' for r in tr], float)
        w = logistic((Xtr - mu) / sd, ytr)
        s_tr = np.c_[np.ones(len(Xtr)), (Xtr - mu) / sd] @ w
        s_va = np.c_[np.ones(len(Xva)), (Xva - mu) / sd] @ w
        g_tr = np.array([GRADE[r['quality']] for r in tr])
        g_va = np.array([GRADE[r['quality']] for r in va])
        t_red = float(np.quantile(s_tr[g_tr == 2], 0.20))      # 80 % of TRAIN Poor at or above
        t_green = float(np.quantile(s_tr[g_tr == 0], 0.80))    # 80 % of TRAIN Good below
        t_green = min(t_green, t_red)
        light = lambda s: np.where(s >= t_red, 2, np.where(s >= t_green, 1, 0))  # noqa: E731
        L = light(s_va)
        m = dict(features=feats, weights=dict(zip(['bias'] + feats, w.tolist())), mean=mu.tolist(), sd=sd.tolist(),
                 t_green=t_green, t_red=t_red,
                 train=dict(auc_poor=auc(s_tr, g_tr == 2), auc_good=auc(-s_tr, g_tr == 0), spearman=spearman(s_tr, g_tr)),
                 val=dict(auc_poor=auc(s_va, g_va == 2), auc_good=auc(-s_va, g_va == 0), spearman=spearman(s_va, g_va),
                          lights_by_grade={gname: np.bincount(L[g_va == gi], minlength=3).tolist()
                                           for gname, gi in GRADE.items()},
                          red_sens_poor=float(np.mean(L[g_va == 2] == 2)), green_spec_good=float(np.mean(L[g_va == 0] == 0))))
        for view in ('4CH', '2CH'):
            k = np.array([r['view'] == view for r in va])
            m['val'][f'auc_poor_{view}'] = auc(s_va[k], g_va[k] == 2)
            m['val'][f'spearman_{view}'] = spearman(s_va[k], g_va[k])
        # single features, direction from TRAIN
        m['val_single'] = {}
        for j, k in enumerate(feats):
            sign = 1 if auc(Xtr[:, j], g_tr == 2) >= 0.5 else -1
            m['val_single'][k] = dict(sign=sign, auc_poor=auc(sign * Xva[:, j], g_va == 2),
                                      spearman=spearman(sign * Xva[:, j], g_va))
        res['models'][name] = m
        for r, s, l in zip(va, s_va, L):
            r[f'score_{name}'], r[f'light_{name}'] = float(s), int(l)
    (OUT / 'quality_camus.json').write_text(json.dumps(dict(res, val_rows=va), indent=1), encoding='utf-8')
    for name, m in res['models'].items():
        v = m['val']
        print(f"[{name}] TRAIN AUC poor {m['train']['auc_poor']:.3f} | VAL AUC poor {v['auc_poor']:.3f} "
              f"(4CH {v['auc_poor_4CH']:.3f}, 2CH {v['auc_poor_2CH']:.3f}) AUC good {v['auc_good']:.3f} "
              f"Spearman {v['spearman']:.3f}; lights by grade {v['lights_by_grade']} "
              f"(red sens Poor {v['red_sens_poor']:.2f}, green spec Good {v['green_spec_good']:.2f})")
        print('   single VAL AUC poor: ' + ', '.join(f"{k} {s['auc_poor']:.2f}" for k, s in m['val_single'].items()))
        print('   weights: ' + ', '.join(f'{k} {x:+.2f}' for k, x in m['weights'].items()))


if __name__ == '__main__':
    main()
