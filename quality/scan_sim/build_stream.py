"""模拟扫查（离线版）第 3 步：把 VAL 片段拼成一段"边扫边调"的信号流，逐窗口算质量灯和切面。

拼接剧本（不同病人的片段拼起来，不是同一次扫查；每段按事先定的规则选：该（切面, 专家质量）组里质量打分取中位数的那段）：
  1 A2C Good   探头在两腔位置：切面提示"A2C，测 EF 请转到四腔"
  2 A4C Poor   转到四腔但画面差：预期红灯
  3 A4C Medium 调整中：预期黄灯
  4 A4C Good   到位：预期绿灯（放两遍，约 4 s）
全部按时间重采样到 50 fps（与现场模式演示源同帧率）。逐窗口（最近 WIN 秒）：程序 16 掩膜 -> 板上 C eq_clip_features
-> eq_model_camus112.json 打分 / 灯（restd.py：本来源 TRAIN 标准化）；右室程序 rv_lv 中位数 >= VIEW_T 判 A4C（VIEW_T 只在 VAL 上看分布定，见 run_val.py）。
输出 results/stream.npz + stream.json（CAMUS 派生，只在本地）。

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I build_stream.py VIEW_T
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_val as rv  # noqa: E402

OUT = HERE / 'results'
FPS = 50.0
WIN = 1.0                      # 质量 / 切面窗口（秒）
EVERY = 5                      # 每 5 帧（0.1 s）更新一次
PLAN = [('2CH', 'Good', 1), ('4CH', 'Poor', 1), ('4CH', 'Medium', 1), ('4CH', 'Good', 2)]
SAY = {('2CH', 'Good'): '探头在两腔位置', ('4CH', 'Poor'): '转到四腔，画面差', ('4CH', 'Medium'): '调整中',
       ('4CH', 'Good'): '到位'}


def pick(rows, view, qual):
    g = sorted((r for r in rows if r['view'] == view and r['quality'] == qual), key=lambda r: r['score'])
    return g[(len(g) - 1) // 2]


def resample(fr, fps, reps):
    n = int(round(len(fr) / fps * FPS)) * reps
    t = np.arange(n) / FPS * fps
    return fr[np.minimum(t.astype(int) % len(fr), len(fr) - 1)]


def main(view_t):
    rv.EQ = json.loads((OUT / 'eq_model_camus112.json').read_text(encoding='utf-8'))    # 本来源 TRAIN 标准化（restd.py）
    rows = json.loads((OUT / 'val_rows.json').read_text(encoding='utf-8'))
    for r in rows:                                     # 选段规则用新打分
        r['score'] = float(rv.EQ['w'][0] + sum(w * (r[k] - mu) / sd for w, k, mu, sd in
                                                zip(rv.EQ['w'][1:], rv.EQ['features'], rv.EQ['mu'], rv.EQ['sd'])))
        r['light'] = 2 if r['score'] >= rv.EQ['t_red'] else (1 if r['score'] >= rv.EQ['t_green'] else 0)
    frames = np.load(OUT / 'val112.npz')['frames']
    segs, parts = [], []
    for view, qual, reps in PLAN:
        r = pick(rows, view, qual)
        fr = resample(frames[r['start']:r['start'] + r['n']], r['fps'], reps)
        segs.append(dict(patient=r['patient'], view=view, quality=qual, say=SAY[(view, qual)], clip_score=r['score'],
                         clip_light=r['light'], clip_rv_lv=r['rv_lv'], start=sum(len(p) for p in parts), n=len(fr)))
        parts.append(fr)
    fr = np.ascontiguousarray(np.concatenate(parts))
    im, mrv = rv.program16(), rv.cc.model('rv_pl1_5')
    q = rv.lv_q(im, fr, FPS)
    cm = np.stack([rv.echo_core.clean112(x >= 1)[0] for x in q])
    _, rvm = rv.rv_ratio(mrv, fr, cm)
    ratio = np.array([rv.cc.largest(a).sum() / max(int(l.sum()), 1) for a, l in zip(rvm, cm)])
    w = int(WIN * FPS)
    score = np.full(len(fr), np.nan)
    light = np.full(len(fr), -1, int)
    view4 = np.full(len(fr), -1, int)
    for t in range(w - 1, len(fr), EVERY):
        a = t - w + 1
        s, lt, _, _ = rv.quality(fr[a:t + 1], q[a:t + 1])
        score[t:t + EVERY], light[t:t + EVERY] = s, lt
        view4[t:t + EVERY] = int(np.median(ratio[a:t + 1]) >= view_t)
    np.savez_compressed(OUT / 'stream.npz', frames=fr, masks=cm, rv=rvm, score=score, light=light, view4=view4, ratio=ratio)
    meta = dict(fps=FPS, win_s=WIN, every=EVERY, view_t=view_t, t_green=rv.EQ['t_green'], t_red=rv.EQ['t_red'], segments=segs)
    (OUT / 'stream.json').write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding='utf-8')
    for s in segs:
        sl = slice(s['start'] + w, s['start'] + s['n'])         # 窗口完全落在本段里的帧
        lt, v4 = light[sl], view4[sl]
        print('%-6s %-6s %s  clip light %d  window lights g/y/r %s  A4C share %.2f' % (
            s['view'], s['quality'], s['patient'], s['clip_light'], [int((lt == j).sum()) for j in range(3)], (v4 == 1).mean()))


if __name__ == '__main__':
    main(float(sys.argv[1]))
