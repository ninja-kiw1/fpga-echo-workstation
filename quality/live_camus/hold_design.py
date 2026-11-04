"""v5.26 "judge once, hold while the probe does not move" for the CAMUS chain (user 11-02: 先判断好坏，后续不动就不用继续判断).

Change score (PS only, integers, the same on the board): S_t = per-pixel sum of the last W = 50 board 112 frames (screen
route: CAMUS 128 frame zoomed x 3 -> crop 384 -> lc_crop_resize 112, as the live source); at the moment the light is
judged R = S_t; then d_t = sum |S_t - R| / (W * 112 * 112) = mean absolute grey change of the 1 s mean image. The 1 s
mean averages the heartbeat out; a probe move / view change alters the mean image.
Streams (50 fps, back and forth like design.py), 4 s per segment:
  still    one clip 4 s: reference at frame 75 (1.5 s), d over frames 75..199 -> false triggers
  view     same patient 2CH 4 s then 4CH 4 s: reference at 75, switch at 200 -> detection, delay
  patient  patient A 4CH then patient B 4CH (next in the list)
Threshold = the 99th percentile of the TRAIN 'still' maxima x 1.2 (label-free: no quality / view labels); VAL reported
once: false-trigger share on still clips, detection share + median / max delay (frames) on view / patient switches.
TEST not touched. Writes hold_design.json.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I hold_design.py
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
from design import CACHE, FPS, fps_of  # noqa: E402

W, SEG, REF = 50, 200, 75
BOX = ec.LcBox(x=0, y=0, side=384)


def clip(split):
    ix = json.loads((CACHE / f'seq_{split}_index.json').read_text(encoding='utf-8'))
    x = np.load(CACHE / f'seq_{split}_img128.npy', mmap_mode='r')
    by = {}
    for e in ix:
        by.setdefault(e['patient'], {})[e['view']] = e

    def frames(e, n=SEG):
        n0, fps = e['length'], fps_of(e['patient'], e['view'])
        cyc = list(range(n0)) + list(range(n0 - 2, 0, -1))
        src = [e['start'] + cyc[int(t / FPS * fps) % len(cyc)] for t in range(n)]
        f128 = np.asarray(x)[src]
        return np.stack([ec.lc_crop_resize(np.kron(f, np.ones((3, 3), np.uint8)), BOX) for f in f128])
    return by, frames


def scores(f112, ref_t=REF):
    """d_t for t >= ref_t (integer sums, as the board will do)"""
    c = np.cumsum(np.concatenate([np.zeros((1, 112, 112), np.int64), f112.astype(np.int64)]), 0)
    S = lambda t: c[t + 1] - c[t + 1 - W]                    # noqa: E731
    R = S(ref_t)
    return np.array([np.abs(S(t) - R).sum() / (W * 112 * 112) for t in range(ref_t, len(f112))])


def run(split):
    by, frames = clip(split)
    pts = sorted(by)
    still, view, pat = [], [], []
    for i, p in enumerate(pts):
        v = by[p]
        for name in ('2CH', '4CH'):
            if name in v:
                still.append(float(scores(frames(v[name])).max()))
        if '2CH' in v and '4CH' in v:
            view.append(scores(np.concatenate([frames(v['2CH']), frames(v['4CH'])])))
        q = pts[(i + 1) % len(pts)]
        if '4CH' in v and '4CH' in by[q]:
            pat.append(scores(np.concatenate([frames(v['4CH']), frames(by[q]['4CH'])])))
    return np.array(still), view, pat


def detect(d_list, thr):
    """switch at frame SEG; d index 0 = frame REF -> per stream: (false before the switch, delay or None)"""
    out = []
    for d in d_list:
        pre, post = d[:SEG - REF], d[SEG - REF:]
        hit = np.nonzero(post > thr)[0]
        out.append((bool((pre > thr).any()), int(hit[0]) if len(hit) else None))
    return out


def main():
    s_tr, v_tr, p_tr = run('train')
    thr = float(np.percentile(s_tr, 99) * 1.2)
    s_va, v_va, p_va = run('val')
    res = dict(window=W, segment=SEG, ref=REF, threshold=thr, train_still_max_p50=float(np.median(s_tr)),
               train_still_max_p99=float(np.percentile(s_tr, 99)))
    for name, s, v, p in (('train', s_tr, v_tr, p_tr), ('val', s_va, v_va, p_va)):
        dv, dp = detect(v, thr), detect(p, thr)
        r = dict(still_clips=len(s), still_false_share=float((s > thr).mean()))
        for kind, dd in (('view_switch', dv), ('patient_switch', dp)):
            delays = [x for _, x in dd if x is not None]
            r[kind] = dict(n=len(dd), detected=float(len(delays) / max(len(dd), 1)), false_before=float(np.mean([f for f, _ in dd])),
                           delay_median_frames=float(np.median(delays)) if delays else None,
                           delay_max_frames=int(max(delays)) if delays else None)
        res[name] = r
        print(f'{name}: still false {r["still_false_share"]:.3f} ({len(s)} clips); view switch detected '
              f'{r["view_switch"]["detected"]:.3f} delay median {r["view_switch"]["delay_median_frames"]} max '
              f'{r["view_switch"]["delay_max_frames"]} frames; patient switch detected {r["patient_switch"]["detected"]:.3f} '
              f'delay median {r["patient_switch"]["delay_median_frames"]} max {r["patient_switch"]["delay_max_frames"]}', flush=True)
    print(f'threshold {thr:.3f} grey levels (TRAIN still max: median {res["train_still_max_p50"]:.3f}, '
          f'p99 {res["train_still_max_p99"]:.3f})')
    (HERE / 'hold_design.json').write_text(json.dumps(res, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
