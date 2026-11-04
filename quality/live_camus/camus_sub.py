"""How much of the CAMUS quality light survives when F1 runs only on every k-th frame (board PL budget in live mode)?

CAMUS half sequences (ED -> ES, median 18 frames), F1 integer logits already cached (results/seq_<split>_lv_r128_q.npy).
For k = 1 (= quality_camus.py, in-between frames), 2, 3, 5: frames 0, k, 2k, ... ; means over the in-between ones of
those, stability / roughness between the kept frames. Score = CAMUS portable weights + light thresholds;
standardisation either unchanged (CAMUS TRAIN, k = 1) or re-taken on CAMUS TRAIN at the same k (label-free).
VAL only reports (AUC Poor, lights by expert grade). TEST not touched.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I camus_sub.py
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
Q = HERE.parent
PLD = Q.parent
sys.path.insert(0, str(Q))
from features import FEATURES, sequence_features  # noqa: E402

CACHE = PLD / 'echo' / 'data' / 'cache'
QC = json.loads((Q / 'results' / 'quality_camus.json').read_text(encoding='utf-8'))
M = QC['models']['portable']
GRADE = {(r['patient'], r['view']): r['quality'] for r in QC['val_rows']}


def feats(split, k):
    ix = json.loads((CACHE / f'seq_{split}_index.json').read_text(encoding='utf-8'))
    x = np.load(CACHE / f'seq_{split}_img128.npy', mmap_mode='r')
    q = np.load(Q / 'results' / f'seq_{split}_lv_r128_q.npy', mmap_mode='r')
    out = []
    for e in ix:
        s, n = e['start'], e['length']
        keep = list(range(0, n, k))
        use = list(range(1, len(keep) - 1)) or list(range(len(keep)))
        f, _ = sequence_features(np.asarray(x[s:s + n])[keep], np.asarray(q[s:s + n])[keep], use)
        out.append((e['patient'], e['view'], [f[c] for c in FEATURES]))
    return out


def evaluate(rows, mean, sd):
    w = np.array([M['weights'][c] for c in FEATURES])
    X = np.array([r[2] for r in rows])
    sc = M['weights']['bias'] + ((X - mean) / sd) @ w
    lt = np.where(sc >= M['t_red'], 2, np.where(sc >= M['t_green'], 1, 0))
    g = np.array([GRADE[(p, v)] for p, v, _ in rows])
    pos, neg = sc[g == 'Poor'], sc[g != 'Poor']
    auc = float(np.mean([(a > b) + 0.5 * (a == b) for a in pos for b in neg]))
    by = {G: np.bincount(lt[g == G], minlength=3).tolist() for G in ('Good', 'Medium', 'Poor')}
    return auc, by


def main():
    res = {}
    for k in (1, 2, 3, 5):
        tr, va = feats('train', k), feats('val', k)
        Xt = np.array([r[2] for r in tr])
        a0, b0 = evaluate(va, np.array(M['mean']), np.array(M['sd']))
        a1, b1 = evaluate(va, Xt.mean(0), Xt.std(0) + 1e-9)
        res[k] = dict(auc_camus_std=a0, lights_camus_std=b0, auc_train_std_at_k=a1, lights_train_std_at_k=b1)
        print(f'k={k}: AUC Poor {a0:.3f} (k=1 standardisation) lights G/M/P {b0} | {a1:.3f} (TRAIN at k) lights {b1}', flush=True)
    (HERE / 'camus_sub.json').write_text(json.dumps(res, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
