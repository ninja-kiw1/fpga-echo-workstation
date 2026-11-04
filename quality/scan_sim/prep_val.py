"""模拟扫查（离线版）第 1 步：CAMUS VAL 半周期序列 -> 112 帧片段（与上位机录屏同一条裁切链 echo_ws.screen_to_112）。

CAMUS 只给 ED -> ES 半个周期：往返播放（ED..ES..ED）拼成整周期，重复到约 2 s，帧率用 Info_*.cfg 的 FrameRate。
只用 VAL / TRAIN（TEST 不碰；TRAIN 只用来标准化质量特征、定门限）。输出 results/<split>112.npz（CAMUS 派生，CC BY-NC-SA，只在本地）+ val112.json（每段的病人 / 切面 / 专家质量）。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I prep_val.py [val|train]
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
sys.path.insert(0, str(PLD / 'echo'))
sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
from nifti import read_nifti  # noqa: E402
from echo_ws import screen_to_112  # noqa: E402

C = PLD / 'echo' / 'data' / 'camus'
OUT = HERE / 'results'
SECONDS = 2.0


def cfg(p, view):
    kv = dict(line.split(': ', 1) for line in (C / 'database_nifti' / p / f'Info_{view}.cfg').read_text().splitlines()
              if ': ' in line)
    return float(kv['FrameRate']), kv['ImageQuality'], float(kv['EF'])


def loop(seq, fps):
    cyc = np.concatenate([seq, seq[-2:0:-1]])                 # ED..ES..(ES-1)..(ED+1)，下一圈从 ED 接上
    reps = int(np.ceil(SECONDS * fps / len(cyc)))
    return np.concatenate([cyc] * reps)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    split = sys.argv[1] if len(sys.argv) > 1 else 'val'
    pats = (C / 'database_split' / f"subgroup_{dict(val='validation', train='training')[split]}.txt").read_text().split()
    clips, meta = [], []
    for p in pats:
        for view in ('2CH', '4CH'):
            seq, _ = read_nifti(C / 'database_nifti' / p / f'{p}_{view}_half_sequence.nii.gz')
            fps, qual, ef = cfg(p, view)
            v, box = screen_to_112(loop(np.clip(seq, 0, 255).astype(np.uint8), fps))
            meta.append(dict(patient=p, view=view, quality=qual, ef=ef, fps=fps, half=int(len(seq)), n=int(len(v)),
                             start=int(sum(len(c) for c in clips)), crop=box))
            clips.append(v)
    np.savez_compressed(OUT / f'{split}112.npz', frames=np.concatenate(clips))
    (OUT / f'{split}112.json').write_text(json.dumps(meta, indent=1), encoding='utf-8')
    print('%d clips, %d frames; quality %s' % (len(meta), sum(m['n'] for m in meta),
                                                {q: sum(m['quality'] == q for m in meta) for q in ('Good', 'Medium', 'Poor')}))


if __name__ == '__main__':
    main()
