"""模拟扫查（离线版）第 4 步：stream.npz -> 演示视频 results/模拟扫查_质量灯.mp4（50 fps，960×540）+ 关键时刻截图。

左：112 画面放大到 448，叠左室轮廓（颜色 = 当前灯）和右室轮廓（蓝）；右：质量灯、打分条（绿 / 红门限）、切面、本段说明；
下：整段时间线（每帧的灯色），白线 = 当前位置。CAMUS 派生（CC BY-NC-SA 4.0，非商业，须引用 Leclerc 2019），只在本地。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I render.py
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / 'results'
W, H, S = 960, 540, 448
COL = {-1: (150, 150, 150), 0: (22, 163, 74), 1: (234, 179, 8), 2: (220, 38, 38)}
TXT = {-1: '等待（攒满 1 s）', 0: '绿灯：画面可用', 1: '黄灯：注意', 2: '红灯：画面差，建议调整探头'}
FONT = 'C:/Windows/Fonts/msyh.ttc'


def font(n):
    return ImageFont.truetype(FONT, n)


def contour(img, mask, col, th=2):
    m = cv2.resize(mask.astype(np.uint8), (S, S), interpolation=cv2.INTER_NEAREST)
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(img, cs, -1, col, th)


def main():
    z = np.load(OUT / 'stream.npz')
    meta = json.loads((OUT / 'stream.json').read_text(encoding='utf-8'))
    fr, cm, rvm, score, light, view4 = (z[k] for k in ('frames', 'masks', 'rv', 'score', 'light', 'view4'))
    n, fps = len(fr), meta['fps']
    tg, tr = meta['t_green'], meta['t_red']
    lo, hi = tg - 1.5, tr + 1.5
    seg_of = np.zeros(n, int)
    for k, s in enumerate(meta['segments']):
        seg_of[s['start']:s['start'] + s['n']] = k
    tl = np.zeros((14, W - 40, 3), np.uint8)                   # 时间线
    for t in range(n):
        x = int(t * (W - 40) / n)
        tl[:, x:x + 2] = COL[int(light[t])][::-1]           # 画布是 BGR
    f_big, f_mid, f_small = font(30), font(20), font(15)
    vw = cv2.VideoWriter(str(OUT / '模拟扫查_质量灯.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), fps, (W, H))
    shots = {}
    for k, s in enumerate(meta['segments']):
        shots[s['start'] + s['n'] - 5] = 'shot_%d_%s_%s.png' % (k + 1, s['view'], s['quality'])
    for t in range(n):
        lt, s = int(light[t]), meta['segments'][seg_of[t]]
        g = cv2.cvtColor(cv2.resize(fr[t], (S, S), interpolation=cv2.INTER_LINEAR), cv2.COLOR_GRAY2BGR)
        contour(g, rvm[t], (235, 140, 40))
        contour(g, cm[t], COL[lt][::-1], 3)
        can = np.full((H, W, 3), 24, np.uint8)
        can[20:20 + S, 20:20 + S] = g
        im = Image.fromarray(can[:, :, ::-1])
        d = ImageDraw.Draw(im)
        x0 = 500
        d.ellipse((x0, 30, x0 + 70, 100), fill=COL[lt])
        d.text((x0 + 90, 45), TXT[lt], font=f_mid, fill=(240, 240, 240))
        # 打分条
        d.text((x0, 120), '质量打分（越高越差，最近 1 s）', font=f_small, fill=(200, 200, 200))
        bx0, bx1, by = x0, W - 40, 150
        px = lambda v: int(bx0 + (min(max(v, lo), hi) - lo) / (hi - lo) * (bx1 - bx0))  # noqa: E731
        d.rectangle((bx0, by, px(tg), by + 16), fill=(20, 90, 45))
        d.rectangle((px(tg), by, px(tr), by + 16), fill=(120, 95, 10))
        d.rectangle((px(tr), by, bx1, by + 16), fill=(120, 25, 25))
        if np.isfinite(score[t]):
            d.rectangle((px(score[t]) - 2, by - 5, px(score[t]) + 2, by + 21), fill=(255, 255, 255))
        # 切面
        v = int(view4[t])
        vt = {-1: '切面：判断中', 1: '切面：心尖四腔 A4C（看得到右室）', 0: '切面：不是四腔（右室太小）→ 请转到四腔'}[v]
        d.text((x0, 200), vt, font=f_mid, fill=(120, 180, 255) if v == 1 else (240, 200, 120))
        d.text((x0, 235), '蓝线 = 右室程序（21 号）  轮廓色 = 当前灯', font=f_small, fill=(170, 170, 170))
        # 本段说明
        d.text((x0, 290), '第 %d 段：%s' % (seg_of[t] + 1, s['say']), font=f_big, fill=(255, 255, 255))
        d.text((x0, 335), 'CAMUS 验证集 %s %s，专家质量 %s' % (s['patient'], {'2CH': 'A2C', '4CH': 'A4C'}[s['view']], s['quality']),
               font=f_small, fill=(200, 200, 200))
        d.text((x0, 360), '%.1f s / %.1f s' % (t / fps, n / fps), font=f_small, fill=(200, 200, 200))
        d.text((x0, 395), '离线模拟：不同病人的片段拼接，不是一次真扫查；CAMUS 只有\n半周期，往返播放成循环。质量灯 = CAMUS 口径（F1 左室程序\n'
                          '+ 10-24 验证版打分，VAL AUC 0.91）；切面 VAL 准确率 %.2f。\n板上现场模式用的是程序 16 口径，尚未移植。'
               % meta['view_acc']['val'], font=f_small, fill=(150, 150, 150))
        d.text((20, 478), 'CAMUS: Leclerc et al., IEEE TMI 2019（CC BY-NC-SA 4.0）', font=f_small, fill=(130, 130, 130))
        can = np.array(im)[:, :, ::-1].copy()
        can[505:519, 20:W - 20] = tl
        xc = 20 + int(t * (W - 40) / n)
        can[500:524, xc:xc + 2] = 255
        vw.write(can)
        if t in shots:
            cv2.imencode('.png', can)[1].tofile(str(OUT / shots[t]))
    vw.release()
    print('wrote', OUT / '模拟扫查_质量灯.mp4', n, 'frames', '%.1f s' % (n / fps))


if __name__ == '__main__':
    main()
