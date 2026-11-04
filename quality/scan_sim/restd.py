"""模拟扫查（离线版）第 2b 步：质量打分换成"CAMUS 画面经录屏裁切链 + 程序 16"这条来源自己的标准化。

和 quality_kf.py 给 EchoNet 做的完全同法（不用标签）：权重 w 与红 / 绿门限沿用 quality_camus.json 'portable'（CAMUS TRAIN 拟合），
只把 9 个特征的均值 / 标准差换成本来源 TRAIN 片段上的值；然后在 VAL 上报 AUC 和灯 × 专家质量表。
另报一个对照：门限也在本来源 TRAIN 上按原规则重定（80 % TRAIN Poor 红、80 % TRAIN Good 绿），看差多少。
输出 results/eq_model_camus112.json（build_stream.py 用）。

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I restd.py
"""
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / 'results'
EQ = json.loads((HERE.parent / 'results' / 'c' / 'eq_model_full.json').read_text(encoding='utf-8'))
G = {'Good': 0, 'Medium': 1, 'Poor': 2}


def auc(score, pos):
    a, b = score[pos], score[~pos]
    return float(((a[:, None] > b[None]).sum() + 0.5 * (a[:, None] == b[None]).sum()) / (len(a) * len(b)))


def scores(rows, mu, sd):
    F = np.array([[r[k] for k in EQ['features']] for r in rows])
    return EQ['w'][0] + ((F - mu) / sd) @ np.array(EQ['w'][1:])


def report(name, s, g, tg, tr):
    lt = np.where(s >= tr, 2, np.where(s >= tg, 1, 0))
    print('%s: AUC Poor %.3f  Good %.3f' % (name, auc(s, g == 2), auc(-s, g == 0)))
    for k, n in enumerate(G):
        print('   %-6s n=%2d  green/yellow/red %s' % (n, (g == k).sum(), [int(((g == k) & (lt == j)).sum()) for j in range(3)]))


def main():
    tr = json.loads((OUT / 'train_rows.json').read_text(encoding='utf-8'))
    va = json.loads((OUT / 'val_rows.json').read_text(encoding='utf-8'))
    Ftr = np.array([[r[k] for k in EQ['features']] for r in tr])
    mu, sd = Ftr.mean(0), np.maximum(Ftr.std(0), 1e-9)
    s_tr, s_va = scores(tr, mu, sd), scores(va, mu, sd)
    g_tr = np.array([G[r['quality']] for r in tr])
    g_va = np.array([G[r['quality']] for r in va])
    report('VAL, EchoNet standardisation (as on the board)', scores(va, np.array(EQ['mu']), np.array(EQ['sd'])), g_va,
           EQ['t_green'], EQ['t_red'])
    report('VAL, own TRAIN standardisation, portable thresholds', s_va, g_va, EQ['t_green'], EQ['t_red'])
    t_red = float(np.quantile(s_tr[g_tr == 2], 0.20))
    t_green = min(float(np.quantile(s_tr[g_tr == 0], 0.80)), t_red)
    report('VAL, own TRAIN standardisation, thresholds re-set on TRAIN (%.2f / %.2f)' % (t_green, t_red), s_va, g_va,
           t_green, t_red)
    m = dict(EQ, mu=mu.tolist(), sd=sd.tolist(), source='CAMUS half sequences, ping-pong 2 s, echo_ws.screen_to_112, '
             'program 16 every frame; mu/sd = CAMUS TRAIN clips (label-free); w / thresholds = quality_camus portable')
    (OUT / 'eq_model_camus112.json').write_text(json.dumps(m, indent=1), encoding='utf-8')
    v4 = np.array([r['view'] == '4CH' for r in tr])
    rr = np.array([r['rv_lv'] for r in tr])
    best = max(np.unique(rr), key=lambda t: ((rr >= t) == v4).mean())
    acc = lambda rows, t: np.mean([(r['rv_lv'] >= t) == (r['view'] == '4CH') for r in rows])  # noqa: E731
    print('view: TRAIN-best rv_lv threshold %.3f  TRAIN acc %.3f  VAL acc %.3f; fixed 0.25: TRAIN %.3f VAL %.3f'
          % (best, acc(tr, best), acc(va, best), acc(tr, 0.25), acc(va, 0.25)))


if __name__ == '__main__':
    main()
