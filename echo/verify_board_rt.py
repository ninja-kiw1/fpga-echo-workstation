"""① real-time path on the board (v5.4): compact frames, board-side tracking state, pipelining.

Exactness (every result must equal the integer reference already used elsewhere):
  1  F1 on the 200 validation ED / ES frames sent as 128x128 grey compact frames
     == results/quant/lv_r128/outputs (the same logits the 512-frame path gives);
  2  board-side tracking: the 100 validation sequences sent as plain grey frames
     (track = reset on frame 0, step afterwards), the BOARD keeps the previous mask and builds
     the echo_seq input itself == the forward masks recorded by quantize_seq.py;
  3  click written into the state: frame 0 = the PC-composed click frame (model 3, track = set),
     then plain grey steps == the 'c1' masks of gen_f4_masks.py (one click on ED, propagate).
Speed (same frames, frames per second over many frames):
  old path (512x512 RGB frame, mask + logit + stats) vs compact (16 KB in, 16 KB out),
  one at a time vs pipelined (2 / 3 / 4 frames in flight), tracking, and fundus at 512.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_rt.py
"""
import json
import sys
import time
from datetime import datetime, timezone

import numpy as np

from common import CACHE, PLD, RESULTS, ROOT, load_cache
from seqeval import load_val_seq
from train_inter import click_maps, next_click

sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
import imagelink  # noqa: E402
import proto      # noqa: E402

F_LOGIT = proto.FLAG_STATS | proto.FLAG_C_LOGIT
F_MASK = proto.FLAG_STATS | proto.FLAG_C_MASK


def unbits(b):
    return np.unpackbits(np.frombuffer(b, np.uint8), bitorder='little').reshape(128, 128).astype(bool)


def pipelined(link, jobs, depth):
    """jobs: list of (payload, w, kwargs). Keeps `depth` frames in flight. -> (results, seconds)."""
    out, sent, t0 = [], 0, time.perf_counter()
    while sent < min(depth, len(jobs)):
        p, w, kw = jobs[sent]
        link.send_v6c(p, w, **kw)
        sent += 1
    for i in range(len(jobs)):
        out.append(link.recv_v6c(jobs[i][2]['flags'], jobs[i][1]))
        if sent < len(jobs):
            p, w, kw = jobs[sent]
            link.send_v6c(p, w, **kw)
            sent += 1
    return out, time.perf_counter() - t0


def main():
    link = imagelink.ImageLink('192.168.1.254', 5060, timeout=30)
    report = dict(utc=datetime.now(timezone.utc).isoformat())
    x512, y512, *_ = load_cache('val')
    g128 = [np.ascontiguousarray(np.asarray(x512[i])[::4, ::4]) for i in range(200)]

    # 1 F1 exactness (compact, pipelined depth 3)
    jobs = [(g.tobytes(), 128, dict(model0=2, thr=1, flags=F_LOGIT)) for g in g128]
    res, _ = pipelined(link, jobs, 3)
    bad = [i for i, (st, lg, _) in enumerate(res)
           if not np.array_equal(np.frombuffer(lg, np.int8).reshape(128, 128).astype(np.int64),
                                 np.load(RESULTS / 'quant' / 'lv_r128' / 'outputs' / f'val_{i:03d}.npy').astype(np.int64))
           or st['model'] != 2]
    report['1_f1_compact'] = dict(frames=200, mismatching=bad)
    print('1', report['1_f1_compact'], flush=True)

    # 2 board-side tracking == recorded F3 forward masks
    V = load_val_seq()
    rec = RESULTS / 'quant' / 'seq_none_ex' / 'seq_inputs'
    start, steps = np.load(rec / 'fwd_start_mask.npy'), np.load(rec / 'fwd_mask.npy')
    lengths = np.load(rec / 'lengths.npy')
    offs = np.concatenate([[0], np.cumsum(lengths - 1)])
    jobs, want = [], []
    for k, s in enumerate(V['index']):
        for t in range(s['length']):
            jobs.append((V['img'][s['start'] + t].tobytes(), 128,
                         dict(model0=2, model1=4, thr=1, flags=F_MASK,
                              track=proto.V6C_TRACK_RESET if t == 0 else proto.V6C_TRACK_STEP)))
            want.append(start[k] if t == 0 else steps[offs[k] + t - 1])
    res, sec = pipelined(link, jobs, 3)
    bad = [i for i, (st, _, mk) in enumerate(res) if not np.array_equal(unbits(mk), want[i])]
    used_state = sum(proto.stats_compact_info(st)[1] for st, _, _ in res)
    report['2_board_tracking'] = dict(frames=len(jobs), sequences=len(V['index']), mismatching=bad[:20],
                                      frames_with_board_prior=int(used_state), fps_pipelined3=len(jobs) / sec)
    print('2', {k: v for k, v in report['2_board_tracking'].items()}, flush=True)

    # 3 click frame written into the state, then board-side propagation == gen_f4_masks 'c1'
    c1 = np.load(RESULTS / 'f4' / 'val_masks128.npz')['c1']
    gt128 = lambda i: np.asarray(y512[i]).reshape(128, 4, 128, 4).mean(axis=(1, 3)) >= 0.5
    jobs, want = [], []
    for k, s in enumerate(V['index']):
        img0 = V['img'][s['start']]
        c = next_click(V['f1_ed'][k], gt128(s['ed']))
        if c is not None:                  # the click frame (model 3) becomes the board state
            rgb = np.stack([img0, V['f1_ed'][k].astype(np.uint8) * 255, click_maps([c])], -1)
            jobs.append((np.ascontiguousarray(rgb).tobytes(), 128, dict(kind=proto.V6C_KIND_RGB, model0=3, thr=1,
                                                                       flags=F_MASK, track=proto.V6C_TRACK_SET)))
        else:                              # nothing to click: F1 result is the start (as gen_f4_masks)
            jobs.append((img0.tobytes(), 128, dict(model0=2, model1=4, thr=1, flags=F_MASK, track=proto.V6C_TRACK_RESET)))
        want.append(c1[s['start']])
        for t in range(1, s['length']):
            jobs.append((V['img'][s['start'] + t].tobytes(), 128, dict(model0=2, model1=4, thr=1, flags=F_MASK,
                                                                       track=proto.V6C_TRACK_STEP)))
            want.append(c1[s['start'] + t])
    res, _ = pipelined(link, jobs, 3)
    bad = [i for i, (st, _, mk) in enumerate(res) if not np.array_equal(unbits(mk), want[i])]
    report['3_click_into_state'] = dict(frames=len(jobs), mismatching=bad[:20])
    print('3', report['3_click_into_state'], flush=True)

    # 4 speed
    speed = {}
    old_flags = proto.FLAG_STATS | proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_LOGIT
    t0 = time.perf_counter()
    for i in range(100):
        link.round_trip(np.ascontiguousarray(np.repeat(np.asarray(x512[i])[:, :, None], 3, 2)).tobytes(), mode=0,
                        flags=old_flags, aiparam=proto.make_aiparam(0, 1, 128, 2))
    speed['old_512frame_serial_fps'] = 100 / (time.perf_counter() - t0)
    jobs = [(g128[i % 200].tobytes(), 128, dict(model0=2, thr=1, flags=F_LOGIT)) for i in range(300)]
    for depth in (1, 2, 3, 4):
        _, sec = pipelined(link, jobs, depth)
        speed[f'compact_depth{depth}_fps'] = len(jobs) / sec
    jobs_m = [(g128[i % 200].tobytes(), 128, dict(model0=2, thr=1, flags=F_MASK)) for i in range(300)]
    _, sec = pipelined(link, jobs_m, 3)
    speed['compact_mask_only_depth3_fps'] = len(jobs_m) / sec
    # fundus at 512: old path vs compact RGB 512 pipelined
    vec = PLD / 'tcp_image' / 'baseline' / 'vectors'
    f512 = np.fromfile(vec / 'fundus_512.rgb', np.uint8)
    fref = np.fromfile(vec / 'fundus_512.i8', np.int8).astype(np.int64).reshape(512, 512)
    t0 = time.perf_counter()
    for _ in range(10):
        link.round_trip(f512.tobytes(), mode=0, flags=old_flags, aiparam=proto.make_aiparam(0, -3, 512, 0))
    speed['fundus512_old_serial_fps'] = 10 / (time.perf_counter() - t0)
    jobs_f = [(f512.tobytes(), 512, dict(kind=proto.V6C_KIND_RGB, model0=0, thr=-3, flags=F_LOGIT)) for _ in range(10)]
    res, sec = pipelined(link, jobs_f, 2)
    speed['fundus512_compact_depth2_fps'] = 10 / sec
    speed['fundus512_compact_exact'] = all(np.array_equal(np.frombuffer(lg, np.int8).reshape(512, 512).astype(np.int64), fref)
                                           for _, lg, _ in res)
    speed['board_ai_ms_128'] = float(np.mean([s['ai_us'] for s, _, _ in pipelined(link, jobs[:50], 1)[0]]) / 1000)
    report['4_speed'] = speed
    print('4', json.dumps(speed), flush=True)

    report['pass'] = (not report['1_f1_compact']['mismatching'] and not report['2_board_tracking']['mismatching']
                      and not report['3_click_into_state']['mismatching'] and speed['fundus512_compact_exact'])
    out = ROOT / 'board_check'
    (out / 'acceptance_rt_v54.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('RT_BOARD_PASS' if report['pass'] else 'RT_BOARD_FAIL', flush=True)
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
