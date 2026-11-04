"""F5 on the real board (v5.3): |a - b| on the PL, tile sums on the PS, search on the PC.

A  exactness: 20 tiled 512 frames (16 candidates each, real CAMUS validation pairs): the board's
   logit plane equals the integer reference (tile by tile; tiles are independent, checked in
   make_reg_model.py), each of the 16 PS tile sums equals the sum of the reference tile, the
   stats count equals count(reference >= 8).
B  accuracy with ground truth: real consecutive validation frames (t, t+1), frame t+1 moved
   by a known shift (dx0, dy0) in [-7, 7]^2 on the 128 grid; registration must find (dx0, dy0).
   Three-step search on 100 pairs, full search (+/-7, 225 candidates) on the first 30.
C  real pairs, no added shift (t / t+1 and ED / ES of the same view): the shift found and the
   change of the mean absolute difference.
Inputs are the 128-tier grey frames (= the UI's down128 of its 512 frames).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_reg.py
"""
import json
import sys
import time
from datetime import datetime, timezone

import numpy as np
import torch

from common import CACHE, PLD, RESULTS, ROOT
from int8_reference import run_integer

sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
import confidence    # noqa: E402
import imagelink     # noqa: E402
import proto         # noqa: E402
import registration as reg  # noqa: E402

TAG = 'reg_absdiff'


def main():
    qdir = RESULTS / 'quant' / TAG
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qdir / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    img = np.load(CACHE / 'seq_val_img128.npy')
    index = json.loads((CACHE / 'seq_val_index.json').read_text(encoding='utf-8'))
    link = imagelink.ImageLink('192.168.1.254', 5060, timeout=30)
    report = dict(utc=datetime.now(timezone.utc).isoformat(), tag=TAG)
    timing = dict(ai_ms=[], rt_ms=[], ps_us=[])
    want_logit = {'on': False}

    def send(frame):
        flags = proto.FLAG_STATS | proto.FLAG_REG | (proto.FLAG_ECHO_LOGIT | proto.FLAG_ECHO_MASK if want_logit['on'] else 0)
        imgs, tim, rows, st = link.round_trip(frame, mode=0, flags=flags, aiparam=reg.aiparam())
        assert st['model'] == reg.MODEL and st['ai_res'] == 512 and 'reg' in st, st
        timing['ai_ms'].append(st['ai_us'] / 1000)
        timing['rt_ms'].append(tim['total_ms'])
        timing['ps_us'].append(st['reg']['run_us'])
        return st['reg']['sums'], dict(st=st, imgs=imgs)

    def ref128(a, g):
        with torch.inference_mode():
            q = torch.from_numpy(np.stack([a, g, np.zeros_like(a)], -1)).permute(2, 0, 1).unsqueeze(0)
            return run_integer(plan, arrays, q)[0][0, 0].numpy().astype(np.int64)

    rng = np.random.default_rng(20261010)
    b128 = lambda arr: bytes(np.ascontiguousarray(arr, np.uint8))
    arr = lambda b: np.frombuffer(b, np.uint8).reshape(128, 128)

    # ---------------- A exactness
    want_logit['on'] = True
    bad, t0 = [], time.time()
    for n in range(20):
        s = index[int(rng.integers(len(index)))]
        t = s['start'] + int(rng.integers(s['length'] - 1))
        a, b = b128(img[t]), b128(img[t + 1])
        cands = [(int(rng.integers(-7, 8)), int(rng.integers(-7, 8))) for _ in range(16)]
        planes = [reg.shifted(b, a, dx, dy) for dx, dy in cands]
        sums, info = send(reg.compose(a, planes))
        lg = (np.frombuffer(confidence.logit_plane(info['imgs']['logit']), np.uint8).reshape(512, 512).astype(np.int64)
              - proto.LOGIT_IMG_OFFSET)
        ok = True
        count = 0
        for k, p in enumerate(planes):
            r = ref128(arr(a), arr(p))
            tile = lg[(k // 4) * 128:(k // 4 + 1) * 128, (k % 4) * 128:(k % 4 + 1) * 128]
            ok &= np.array_equal(tile, r) and sums[k] == int(r.sum())
            count += int((r >= proto.MODELS[reg.MODEL]['thr']).sum())
        ok &= info['st']['vessels'] == count
        if not ok:
            bad.append(n)
    report['A_exactness'] = dict(frames=20, candidates=320, mismatching_frames=bad, seconds=round(time.time() - t0, 1))
    print('A', report['A_exactness'], flush=True)
    want_logit['on'] = False

    # ---------------- B known shifts
    def known_pair():
        s = index[int(rng.integers(len(index)))]
        t = s['start'] + int(rng.integers(s['length'] - 1))
        while True:
            d = (int(rng.integers(-7, 8)), int(rng.integers(-7, 8)))
            if d != (0, 0):
                break
        a, b0 = b128(img[t]), b128(img[t + 1])
        return a, reg.shifted(b0, b0, -d[0], -d[1]), d, (s['patient'], s['view'], t - s['start'])

    rows_b = []
    t0 = time.time()
    for n in range(100):
        a, b, d, where = known_pair()
        r3 = reg.Registrar(a, b, send)
        tss, path = r3.three_step()
        row = dict(where=where, true=d, tss=tss, tss_frames=r3.frames, tss_path=path)
        if n < 30:
            rf = reg.Registrar(a, b, send)
            row['full'] = rf.full(7)
            row['full_frames'] = rf.frames
            row['cost_true'] = rf.cost[d]
            row['cost_zero'] = rf.cost[(0, 0)]
            row['cost_found'] = rf.cost[row['full']]
        rows_b.append(row)
        if (n + 1) % 25 == 0:
            print(f'B {n + 1}/100 ({time.time() - t0:.0f}s)', flush=True)
    err = lambda key, rs: [max(abs(r[key][0] - r['true'][0]), abs(r[key][1] - r['true'][1])) for r in rs]
    full_rows = [r for r in rows_b if 'full' in r]
    report['B_known_shift'] = dict(
        pairs=len(rows_b), shift_range='[-7, 7]^2 on the 128 grid (x4 on the 512 canvas)',
        tss_exact=float(np.mean([e == 0 for e in err('tss', rows_b)])), tss_within1=float(np.mean([e <= 1 for e in err('tss', rows_b)])),
        tss_mean_chebyshev_err=float(np.mean(err('tss', rows_b))), tss_frames=3,
        full_exact=float(np.mean([e == 0 for e in err('full', full_rows)])), full_pairs=len(full_rows),
        full_within1=float(np.mean([e <= 1 for e in err('full', full_rows)])), full_frames=15,
        mad_zero_vs_true_logit=[float(np.mean([r['cost_zero'] for r in full_rows])), float(np.mean([r['cost_true'] for r in full_rows]))],
        seconds=round(time.time() - t0, 1), rows=rows_b)
    print('B', {k: v for k, v in report['B_known_shift'].items() if k != 'rows'}, flush=True)

    # ---------------- C real pairs, no added shift
    rows_c = []
    for n, s in enumerate(index[:50]):
        for kind, (i, j) in (('t,t+1', (s['start'] + s['length'] // 2, s['start'] + s['length'] // 2 + 1)),
                             ('ED,ES', (s['start'], s['start'] + s['length'] - 1))):
            r3 = reg.Registrar(b128(img[i]), b128(img[j]), send)
            found, _ = r3.three_step()
            rows_c.append(dict(patient=s['patient'], view=s['view'], kind=kind, found=found,
                               mad_zero=r3.cost[(0, 0)], mad_found=r3.cost[found]))
    summ_c = {}
    for kind in ('t,t+1', 'ED,ES'):
        rs = [r for r in rows_c if r['kind'] == kind]
        summ_c[kind] = dict(pairs=len(rs), found_zero=float(np.mean([r['found'] == (0, 0) for r in rs])),
                            mean_abs_shift=float(np.mean([abs(r['found'][0]) + abs(r['found'][1]) for r in rs])),
                            mad_zero=float(np.mean([r['mad_zero'] for r in rs])), mad_found=float(np.mean([r['mad_found'] for r in rs])))
    report['C_real_pairs'] = dict(summary=summ_c, rows=rows_c)
    print('C', summ_c, flush=True)

    report['timing'] = dict(frames=len(timing['ai_ms']), ai_ms_mean=float(np.mean(timing['ai_ms'])),
                            rt_ms_mean=float(np.mean(timing['rt_ms'])), ps_sum_us_mean=float(np.mean(timing['ps_us'])),
                            per_candidate_ms=float(np.mean(timing['rt_ms'])) / 16)
    report['pass'] = not bad          # pass = bit-exact PL + PS; accuracy is reported, not a gate
    out = ROOT / 'board_check'
    out.mkdir(exist_ok=True)
    (out / 'acceptance_f5_reg.json').write_text(json.dumps(report, indent=1, default=lambda o: list(o)), encoding='utf-8')
    print('timing', report['timing'])
    print('F5_BOARD_PASS' if report['pass'] else 'F5_BOARD_FAIL', flush=True)
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
