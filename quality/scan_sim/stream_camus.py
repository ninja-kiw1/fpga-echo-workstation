"""模拟扫查（离线版）——CAMUS 自己的口径（取代 build_stream.py 的"录屏裁切 + 程序 16"链，那条链上质量灯在 CAMUS VAL 只有
AUC 0.63–0.75、好片也大多红灯，见 restd.py 输出）。

质量灯：10-24 在 CAMUS VAL 上验证过的那版（quality_camus.json 'portable'：F1 左室程序 128 档整数 logits
        seq_<split>_lv_r128_q.npy -> features.sequence_features -> 标准化 + 逻辑回归 + 红 / 绿门限，全部 CAMUS TRAIN 定；
        VAL AUC Poor 0.911）。窗口版：最近 WIN 秒的帧全部进均值（原版只取中间帧）。
切面：右室程序 rv_pl1_5（FP32）在同一 128 档灰度上（cross_check.py 的 CAMUS 链），每帧 RV 最大块 / F1 左室面积，取中位数；
      门限在 CAMUS TRAIN 的半周期序列上取准确率最高的值，VAL 只报一次准确率。
剧本同 build_stream.PLAN；每段 = 该（切面, 专家质量）组里 VAL 打分中位数那段（规则事先定，不挑）。往返播放、50 fps。
输出 results/stream.npz + stream.json（render.py 用；CAMUS 派生，只在本地）。

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I stream_camus.py
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PLD / 'quality'))
sys.path.insert(0, str(PLD / 'cardiac_rv'))
from features import clean, sequence_features  # noqa: E402
import cross_check as cc  # noqa: E402
from build_stream import PLAN, SAY, FPS, WIN, EVERY  # noqa: E402

OUT = HERE / 'results'
CACHE = PLD / 'echo' / 'data' / 'cache'
QR = PLD / 'quality' / 'results'
QC = json.loads((QR / 'quality_camus.json').read_text(encoding='utf-8'))
M = QC['models']['portable']


def load(split):
    ix = json.loads((CACHE / f'seq_{split}_index.json').read_text(encoding='utf-8'))
    return ix, np.load(CACHE / f'seq_{split}_img128.npy', mmap_mode='r'), np.load(QR / f'seq_{split}_lv_r128_q.npy', mmap_mode='r')


def fps_of(p, view):
    C = PLD / 'echo' / 'data' / 'camus' / 'database_nifti' / p / f'Info_{view}.cfg'
    return float(dict(l.split(': ', 1) for l in C.read_text().splitlines() if ': ' in l)['FrameRate'])


def score(f):
    s = M['weights']['bias'] + sum(M['weights'][k] * (f[k] - mu) / sd for k, mu, sd in zip(M['features'], M['mean'], M['sd']))
    return float(s), (2 if s >= M['t_red'] else (1 if s >= M['t_green'] else 0))


def ratios(mrv, x, q):
    rv = cc.run(mrv, np.asarray(x))
    lv = [clean(qq >= 1)[0] if (qq >= 1).any() else qq >= 1 for qq in np.asarray(q)]
    return np.array([cc.largest(a).sum() / max(int(l.sum()), 1) for a, l in zip(rv, lv)]), rv, np.stack(lv)


def view_threshold(mrv):
    out = {}
    for split in ('train', 'val'):
        ix, x, q = load(split)
        r, _, _ = ratios(mrv, x, q)
        out[split] = [(float(np.median(r[e['start']:e['start'] + e['length']])), e['view'] == '4CH') for e in ix]
    tr = np.array(out['train'])
    t = max(np.unique(tr[:, 0]), key=lambda v: ((tr[:, 0] >= v) == tr[:, 1]).mean())
    acc = {s: float(np.mean([(a >= t) == b for a, b in out[s]])) for s in out}
    va = np.array(out['val'])
    auc = float(np.mean([(a > b) + 0.5 * (a == b) for a in va[va[:, 1] == 1, 0] for b in va[va[:, 1] == 0, 0]]))
    print('view (128 tier): threshold %.3f (TRAIN-best)  TRAIN acc %.3f  VAL acc %.3f  VAL AUC %.3f' % (t, acc['train'], acc['val'], auc))
    return float(t), acc, auc


def main():
    mrv = cc.model('rv_pl1_5')
    view_t, vacc, vauc = view_threshold(mrv)
    ix, x, q = load('val')
    rows = {(r['patient'], r['view']): r for r in QC['val_rows']}
    segs, idx = [], []
    for view, qual, reps in PLAN:
        g = sorted((r for r in QC['val_rows'] if r['view'] == view and r['quality'] == qual), key=lambda r: r['score_portable'])
        r = g[(len(g) - 1) // 2]
        e = next(e for e in ix if e['patient'] == r['patient'] and e['view'] == view)
        n0, fps = e['length'], fps_of(r['patient'], view)
        cyc = list(range(n0)) + list(range(n0 - 2, 0, -1))
        n = int(round(2.0 * FPS)) * reps                           # 每段 2 s（Good 段放两遍）
        src = [e['start'] + cyc[int(t / FPS * fps) % len(cyc)] for t in range(n)]
        segs.append(dict(patient=r['patient'], view=view, quality=qual, say=SAY[(view, qual)], clip_score=r['score_portable'],
                         clip_light=r['light_portable'], start=len(idx), n=n))
        idx += src
    idx = np.array(idx)
    fr, qq = np.asarray(x)[idx], np.asarray(q)[idx]
    ratio, rvm, cm = ratios(mrv, fr, qq)
    w = int(WIN * FPS)
    sc, light, view4 = np.full(len(fr), np.nan), np.full(len(fr), -1, int), np.full(len(fr), -1, int)
    for t in range(w - 1, len(fr), EVERY):
        a = t - w + 1
        f, _ = sequence_features(fr[a:t + 1], qq[a:t + 1], list(range(w)))
        sc[t:t + EVERY], light[t:t + EVERY] = score(f)
        view4[t:t + EVERY] = int(np.median(ratio[a:t + 1]) >= view_t)
    np.savez_compressed(OUT / 'stream.npz', frames=fr, masks=cm, rv=rvm, score=sc, light=light, view4=view4, ratio=ratio)
    meta = dict(fps=FPS, win_s=WIN, every=EVERY, view_t=view_t, view_acc=vacc, view_val_auc=vauc, t_green=M['t_green'],
                t_red=M['t_red'], chain='CAMUS 128 tier: F1 LV program + quality_camus portable; RV rv_pl1_5', segments=segs)
    (OUT / 'stream.json').write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding='utf-8')
    for s in segs:
        sl = slice(s['start'] + w, s['start'] + s['n'])
        lt, v4 = light[sl], view4[sl]
        print('%-4s %-6s %s  clip light %d  window g/y/r %s  A4C share %.2f' % (
            s['view'], s['quality'], s['patient'], s['clip_light'], [int((lt == j).sum()) for j in range(3)], (v4 == 1).mean()))


if __name__ == '__main__':
    main()
