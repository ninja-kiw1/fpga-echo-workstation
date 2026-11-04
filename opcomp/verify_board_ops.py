"""② on the real board (v5.5): compiled operator programs and on-board multi-program pipelines.

A  each compiled operator (models 6..13) on 12 test images sent as compact RGB frames: the board's
   int8 output == the integer reference (results/<op>), i.e. the compiler's programs run bit-exact on the PL;
B  chain on the board: 3 echo frames -> op_median3 -> (output as grey) -> echo_lv  (denoise, then segment);
C  fan-out on the board: 3 echo frames (t-1, t, t+1); program 0 = echo_lv on t (track reset, G channel),
   then op_motion and op_sobel on the same frames -- three results from one request;
   B and C are compared with the same chain computed offline with the integer models;
D  timing: one / two / three programs per request (board AI time and round trip).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_ops.py
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
sys.path.insert(0, str(PLD / 'echo'))
sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
sys.path.insert(0, str(ROOT))

import numpy as np          # noqa: E402
import torch                # noqa: E402

import imagelink            # noqa: E402
import proto                # noqa: E402
from build_op import test_images   # noqa: E402
from int8_reference import run_integer   # noqa: E402

OPS = ['median3', 'mean3', 'sobel', 'gauss11', 'unsharp', 'motion', 'change', 'vessel4']
IDX = {n: 6 + i for i, n in enumerate(OPS)}
F = proto.FLAG_STATS | proto.FLAG_C_LOGIT


def int_model(qdir):
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qdir / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}

    def run(rgb):
        with torch.inference_mode():
            q = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).unsqueeze(0)
            return run_integer(plan, arrays, q)[0][0, 0].numpy().astype(np.int8)
    return run


def as_input(logit):
    """the board's conversion of a program output into the next program's input (offset code)."""
    return np.clip(logit.astype(np.int16) + 128, 0, 255).astype(np.uint8)


def main():
    torch.set_num_threads(8)
    link = imagelink.ImageLink('192.168.1.254', 5060, timeout=30)
    ref = {n: int_model(ROOT / 'results' / n) for n in OPS}
    lv = int_model(PLD / 'echo' / 'results' / 'quant' / 'lv_r128')
    imgs = test_images()
    report = dict(utc=datetime.now(timezone.utc).isoformat(), A={}, ai_ms={})

    # A
    ok_a = True
    for n in OPS:
        bad, ai = [], []
        for i, im in enumerate(imgs):
            st, lg, _, _ = link.v6c_round_trip(np.ascontiguousarray(im).tobytes(), 128, kind=proto.V6C_KIND_RGB,
                                               model0=IDX[n], thr=0, flags=F)
            assert st['model'] == IDX[n], st
            if not np.array_equal(np.frombuffer(lg, np.int8).reshape(128, 128), ref[n](im)):
                bad.append(i)
            ai.append(st['ai_us'] / 1000)
        report['A'][n] = dict(images=len(imgs), mismatching=bad, ai_ms=float(np.mean(ai)))
        ok_a &= not bad
        print('A', n, report['A'][n], flush=True)

    echo = np.load(PLD / 'echo' / 'data' / 'cache' / 'seq_val_img128.npy', mmap_mode='r')
    starts = [10, 300, 700, 1100, 1500]

    # B chain: median3 -> echo_lv
    bad_b = []
    for s in starts:
        rgb = np.stack([echo[s], echo[s + 1], echo[s + 2]], -1).astype(np.uint8)
        st, lgs, _, _ = link.v6c_round_trip(rgb.tobytes(), 128, kind=proto.V6C_KIND_RGB, model0=IDX['median3'], thr=0,
                                            flags=F, extra=[(2, 1, 0, 1)])
        den = ref['median3'](rgb)
        seg = lv(np.repeat(as_input(den)[..., None], 3, -1))
        got = [np.frombuffer(x, np.int8).reshape(128, 128) for x in lgs]
        if not (np.array_equal(got[0], den) and np.array_equal(got[1], seg)):
            bad_b.append(s)
    report['B_chain_median3_to_echo_lv'] = dict(requests=len(starts), mismatching=bad_b)
    print('B', report['B_chain_median3_to_echo_lv'], flush=True)

    # C fan-out: echo_lv (t, via G) + motion + sobel on the same request
    bad_c = []
    for s in starts:
        rgb = np.stack([echo[s], echo[s + 1], echo[s + 2]], -1).astype(np.uint8)
        st, lgs, _, _ = link.v6c_round_trip(rgb.tobytes(), 128, kind=proto.V6C_KIND_RGB, model0=2, model1=4, thr=1,
                                            flags=F, track=proto.V6C_TRACK_RESET,
                                            extra=[(IDX['motion'], 0, 0, 0), (IDX['sobel'], 0, 0, 0)])
        got = [np.frombuffer(x, np.int8).reshape(128, 128) for x in lgs]
        want = [lv(np.repeat(rgb[..., 1:2], 3, -1)), ref['motion'](rgb), ref['sobel'](rgb)]
        if not all(np.array_equal(g, w) for g, w in zip(got, want)):
            bad_c.append(s)
    report['C_fanout_seg_motion_sobel'] = dict(requests=len(starts), mismatching=bad_c)
    print('C', report['C_fanout_seg_motion_sobel'], flush=True)

    # D timing (pipelined depth 2 over 60 requests each)
    rgb = np.stack([echo[400], echo[401], echo[402]], -1).astype(np.uint8).tobytes()
    timing = {}
    for k, extra in ((1, []), (2, [(IDX['motion'], 0, 0, 0)]), (3, [(IDX['motion'], 0, 0, 0), (IDX['sobel'], 0, 0, 0)])):
        t0 = time.perf_counter()
        inflight = 0
        for i in range(60):
            link.send_v6c(rgb, 128, kind=proto.V6C_KIND_RGB, model0=2, thr=1, flags=F, track=proto.V6C_TRACK_RESET,
                          extra=extra)
            inflight += 1
            if inflight == 2:
                link.recv_v6c(F, 128, len(extra))
                inflight -= 1
        link.recv_v6c(F, 128, len(extra))
        timing['%d_programs_per_request_fps' % k] = 60 / (time.perf_counter() - t0)
    report['D_timing'] = timing
    print('D', timing, flush=True)

    report['pass'] = ok_a and not bad_b and not bad_c
    (ROOT / 'board_check').mkdir(exist_ok=True)
    (ROOT / 'board_check' / 'acceptance_ops_v55.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('OPS_BOARD_PASS' if report['pass'] else 'OPS_BOARD_FAIL', flush=True)
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
