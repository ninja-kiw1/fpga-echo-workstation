"""v5.26 final model of the CAMUS live chain, computed with the board's own C (echo_core.dll: eq_clean + eq_window), so
the mu / sd the PC sends in the CQ01 block are those of the code that runs on the board. Chain and streams as design.py,
k = K (chosen there: VAL AUC Poor 0.911 at the lowest PL share). TRAIN fits mu / sd (weights + light thresholds =
quality_camus.json 'portable'), VAL reports once. Also checks DLL features == features.py (design.py) on VAL windows.
Writes camus_live_model.json (read by tcp_image_ui/jobs.py ROLL_CAMUS).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I fit_dll.py
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
os.add_dll_directory(r'C:\msys64\mingw64\bin')
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PLD / 'keyframe'))
import echo_core as ec  # noqa: E402
from design import FEATURES, M, QC, WIN, EVERY, FPS, streams, window_features  # noqa: E402

K = 5
VIEW = json.loads((HERE / 'design.json').read_text(encoding='utf-8'))['by_k'][str(K)]


def unit_model():
    e = ec.EqModel()
    e.sd[:] = [1.0] * 9
    return e


def windows(split, check=False):
    um, rows, diff = unit_model(), [], 0.0
    for e, fr, q, _ in streams(split):
        cm, nc = zip(*[ec.eq_clean(qq >= 1) for qq in q])
        cm, nc = np.stack(cm), np.array(nc, np.int32)
        W = []
        for t in range(WIN - 1, len(fr), EVERY):
            a = t - WIN + 1
            sel = [i for i in range(WIN) if (a + i) % K == 0]
            j = [a + i for i in sel]
            o = ec.eq_window(fr[a:t + 1], sel, q[j], cm[j], nc[j], um)
            f = [o['f'][k] for k in FEATURES]
            W.append(f)
            if check:
                ref, _ = window_features(fr[a:t + 1], q[a:t + 1], sel)
                diff = max(diff, float(np.max(np.abs(np.array(f) - np.array(ref)))))
        rows.append(dict(patient=e['patient'], view=e['view'], W=np.array(W)))
    return rows, diff


def main():
    tr, _ = windows('train')
    va, diff = windows('val', check=True)
    Xt = np.concatenate([r['W'] for r in tr])
    mu, sd = Xt.mean(0), Xt.std(0)
    w = np.array([M['weights'][c] for c in FEATURES])
    grade = {(r['patient'], r['view']): r['quality'] for r in QC['val_rows']}
    cs, cg, lt_all, g_all = [], [], [], []
    for r in va:
        s = M['weights']['bias'] + ((r['W'] - mu) / sd) @ w
        lt = np.where(s >= M['t_red'], 2, np.where(s >= M['t_green'], 1, 0))
        g = grade[(r['patient'], r['view'])]
        cs.append(float(s.mean()))
        cg.append(g)
        lt_all += list(lt)
        g_all += [g] * len(s)
    cs, cg, lt_all, g_all = np.array(cs), np.array(cg), np.array(lt_all), np.array(g_all)
    auc = float(np.mean([(a > b) + 0.5 * (a == b) for a in cs[cg == 'Poor'] for b in cs[cg != 'Poor']]))
    lights = {G: np.bincount(lt_all[g_all == G], minlength=3).tolist() for G in ('Good', 'Medium', 'Poor')}
    model = dict(chain='CAMUS live (v5.26)', f1_every=K, f1_model=2, win=WIN, every=EVERY, fps=FPS, features=FEATURES,
                 w=[M['weights']['bias']] + w.tolist(), mu=mu.tolist(), sd=sd.tolist(), t_green=M['t_green'], t_red=M['t_red'],
                 view_every=K * round(25 / K), view_n=3, view_t=VIEW['view_t'], rv_model=21,
                 val=dict(auc_poor=auc, window_lights=lights, view_acc=VIEW['view_acc_val']),
                 train_windows=int(len(Xt)), dll_vs_python_max_abs_diff=diff,
                 source='CAMUS TRAIN fit / VAL report (Leclerc 2019, CC BY-NC-SA 4.0); TEST not touched')
    (HERE / 'camus_live_model.json').write_text(json.dumps(model, indent=1), encoding='utf-8')
    print(f'DLL vs features.py max |diff| on VAL windows: {diff:.3g}')
    print(f'k={K}: VAL AUC Poor {auc:.3f}; window lights G/M/P {lights}; view_t {VIEW["view_t"]:.3f} '
          f'(VAL acc {VIEW["view_acc_val"]:.3f}); TRAIN windows {len(Xt)}')


if __name__ == '__main__':
    main()
