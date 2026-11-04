"""模拟扫查（离线版）第 2 步：在 VAL 片段上算板上同口径的质量灯 + 试 A4C / A2C 切面规则（只看 VAL，不定门限到 TEST）。

质量灯：程序 16 整数模型（与板上 PL 逐位相同）每帧推理 -> echo_core.dll 的 eq_clip_features（板上 C）
        -> eq_model_full.json 的打分与红 / 黄 / 绿门限（CAMUS TRAIN 拟合，EchoNet TRAIN 子集标准化，未在 CAMUS 上重标定）。
切面：右室程序 rv_pl1_5（FP32 检查点，整数版差 ~0.003 Dice；输入链 = EchoNet 的 112 灰度 0 填充 8 -> 128）每帧 RV 最大连通块面积
      / 程序 16 左室面积，取片段中位数 rv_lv；A4C 看得到右室，A2C 看不到。这里只报两类的分布和 AUC，门限另定。
输出 results/val_rows.json（每段一行）。

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I run_val.py [val|train]
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
os.environ['PATH'] = r'C:\msys64\mingw64\bin;' + os.environ['PATH']
sys.path.insert(0, str(PLD / 'keyframe'))
sys.path.insert(0, str(PLD / 'echonet'))
sys.path.insert(0, str(PLD / 'cardiac_rv'))
import echo_core  # noqa: E402
from frame_ops import video_inputs  # noqa: E402
from int8_gpu import IntegerModel  # noqa: E402
import cross_check as cc  # noqa: E402

OUT = HERE / 'results'
PAD = 8
E = PLD / 'echonet'
EQ = json.loads((PLD / 'quality' / 'results' / 'c' / 'eq_model_full.json').read_text(encoding='utf-8'))


def program16():
    qd = E / 'results' / 'quant' / 't3'
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        return IntegerModel(plan, {k: z[k] for k in z.files}, 'cuda')


def lv_q(im, fr, fps):
    x = video_inputs(fr, 't3', fps)
    with torch.no_grad():
        return np.concatenate([im(torch.from_numpy(x[i:i + 128]))[:, 0, PAD:PAD + 112, PAD:PAD + 112].to(torch.int8).cpu().numpy()
                               for i in range(0, len(x), 128)])


def quality(fr, q):
    cm, nc = zip(*[echo_core.clean112(x >= 1) for x in q])
    cm, nc = np.stack(cm), np.array(nc, np.int32)
    f = echo_core.clip_features(fr, q, cm, np.ones(len(fr), np.uint8), nc)
    s = EQ['w'][0] + sum(w * (f[k] - mu) / sd for w, k, mu, sd in zip(EQ['w'][1:], EQ['features'], EQ['mu'], EQ['sd']))
    light = 2 if s >= EQ['t_red'] else (1 if s >= EQ['t_green'] else 0)
    return float(s), light, f, cm


def rv_ratio(mrv, fr, lv):
    rv = cc.run(mrv, np.pad(fr, ((0, 0), (PAD, PAD), (PAD, PAD))))[:, PAD:PAD + 112, PAD:PAD + 112]
    r = [cc.largest(a).sum() / max(int(l.sum()), 1) for a, l in zip(rv, lv)]
    return float(np.median(r)), rv


def auc(score, pos):
    score, pos = np.asarray(score, float), np.asarray(pos, bool)
    a, b = score[pos], score[~pos]
    return float(((a[:, None] > b[None]).sum() + 0.5 * (a[:, None] == b[None]).sum()) / (len(a) * len(b)))


def main():
    split = sys.argv[1] if len(sys.argv) > 1 else 'val'
    meta = json.loads((OUT / f'{split}112.json').read_text(encoding='utf-8'))
    frames = np.load(OUT / f'{split}112.npz')['frames']
    im, mrv = program16(), cc.model('rv_pl1_5')
    rows = []
    for m in meta:
        fr = np.ascontiguousarray(frames[m['start']:m['start'] + m['n']])
        q = lv_q(im, fr, m['fps'])
        s, light, f, cm = quality(fr, q)
        rr, _ = rv_ratio(mrv, fr, cm)
        rows.append(dict(m, score=s, light=light, rv_lv=rr, lv_px=float(np.median(cm.sum((1, 2)))), **f))
    (OUT / f'{split}_rows.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')
    g = np.array([{'Good': 0, 'Medium': 1, 'Poor': 2}[r['quality']] for r in rows])
    s = np.array([r['score'] for r in rows])
    lt = np.array([r['light'] for r in rows])
    print('quality  AUC Poor vs rest %.3f   AUC Good vs rest(low=good) %.3f' % (auc(s, g == 2), auc(-s, g == 0)))
    for k, n in enumerate(('Good', 'Medium', 'Poor')):
        print('  %-6s n=%2d  green/yellow/red %s' % (n, (g == k).sum(), [int(((g == k) & (lt == j)).sum()) for j in range(3)]))
    v4 = np.array([r['view'] == '4CH' for r in rows])
    rv = np.array([r['rv_lv'] for r in rows])
    print('view     AUC rv_lv A4C vs A2C %.3f' % auc(rv, v4))
    for name, sel in (('A4C', v4), ('A2C', ~v4)):
        print('  %s rv_lv median %.2f  IQR %.2f-%.2f  min %.2f max %.2f' % (name, np.median(rv[sel]), *np.percentile(rv[sel], [25, 75]),
                                                                          rv[sel].min(), rv[sel].max()))


if __name__ == '__main__':
    main()
