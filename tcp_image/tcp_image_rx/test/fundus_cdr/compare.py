"""Host check: src/fundus_cdr.c == E:/PLD/fundus_cdr/cdr_ref.py, bit for bit (part D, vertical cup-to-disc ratio).

Cases
  val       every Chaksu VAL image: img512 + the integer-model logits q1 / qd / qc that eval_val.py recorded
            (results/eval/<name>/cases.npz); C recomputes payload1, the disc location, window side / position,
            payload2 and the measurement from the same logits.
  synth     synthetic logit maps (blobs, rings with holes, several components with equal sizes, noise, empty maps,
            discs at the image border) for every window side 128 / 256 / 384 / 512 and thresholds 1..3, measure only;
            plus whole cases with random images and synthetic stage-1 maps (border clamping, tiny components).
Compared: found flag, cy / cx / size / area, side, y0 / x0, both payloads (bytes), disc_h / cup_h / tops / areas,
vCDR (IEEE double, exact), both masks (bytes). Writes fundus_cdr_host.json; prints FUNDUS_CDR_HOST_PASS.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I compare.py --eval-name v1       (gcc via WSL)
"""
import argparse
import json
import os
import struct
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RX = os.path.normpath(os.path.join(HERE, '..', '..'))
FC = os.path.normpath(os.path.join(RX, '..', '..', 'fundus_cdr'))
sys.path.insert(0, FC)
import cdr_ref  # noqa: E402

WORK = os.path.join(HERE, 'work')
P = 128 * 128


def wsl(p):
    p = os.path.abspath(p).replace('\\', '/')
    return '/mnt/' + p[0].lower() + p[2:]


def blob_map(rng, n=128):
    """Random int8 logit map: 0-3 ellipses (some with holes), noise, sometimes touching the border."""
    y, x = np.mgrid[0:n, 0:n]
    v = np.full((n, n), -40.0)
    for _ in range(rng.integers(0, 4)):
        cy, cx = rng.uniform(-10, n + 10, 2)
        ry, rx = rng.uniform(2, 40, 2)
        d = ((y - cy) / ry) ** 2 + ((x - cx) / rx) ** 2
        v = np.maximum(v, 60 * (1 - d))
        if rng.random() < 0.3:
            v = np.where(d < rng.uniform(0.05, 0.4), -30, v)
    v += rng.normal(0, rng.uniform(0, 25), (n, n))
    return np.clip(np.round(v), -128, 127).astype(np.int8)


def tie_map(n=128):
    """Two equal 5x5 squares + a ring: the tie-break (lower raster number wins) and hole filling."""
    q = np.full((n, n), -5, np.int8)
    q[10:15, 60:65] = 9
    q[40:45, 5:10] = 9
    q[70:100, 70:100] = 9
    q[78:92, 78:92] = -9
    return q


def ref_whole(img, q1, qd, qc, thr):
    p1 = cdr_ref.payload1(img)
    loc = cdr_ref.locate(q1, thr)
    if loc is None:
        return dict(found=0, p1=p1)
    side = cdr_ref.side_for(loc['size'])
    y0, x0 = cdr_ref.window(loc['cy'], loc['cx'], side)
    r = cdr_ref.measure(qd, qc, side, thr, thr)
    return dict(found=1, loc=loc, side=side, y0=y0, x0=x0, p1=p1, p2=cdr_ref.payload2(img, y0, x0, side), r=r)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--eval-name', required=True, help='results/eval/<name> written by eval_val.py')
    ap.add_argument('--synth', type=int, default=400)
    a = ap.parse_args()
    os.makedirs(WORK, exist_ok=True)
    sys.path.insert(0, FC)
    from common import RESULTS, load_cache
    if a.eval_name != 'none':                   # 'none': synthetic cases only (before any model exists)
        val = load_cache('chaksu_val')
        cases = np.load(RESULTS / 'eval' / a.eval_name / 'cases.npz')

    rng = np.random.default_rng(20261017)
    items = []                      # (kind, thr, side, img, q1, qd, qc, label)
    for i in range(len(val['meta']) if a.eval_name != 'none' else 0):
        items.append((0, 1, 0, np.asarray(val['img512'][i]), cases['q1'][i], cases['qd'][i], cases['qc'][i], 'val'))
    for j in range(a.synth):
        side = (128, 256, 384, 512)[j % 4]
        thr = 1 + (j // 4) % 3
        qd, qc = (tie_map(), blob_map(rng)) if j % 25 == 0 else (blob_map(rng), blob_map(rng))
        items.append((1, thr, side, None, None, qd, qc, 'synth_measure'))
    for j in range(a.synth // 4):
        img = rng.integers(0, 256, (512, 512, 3), dtype=np.uint8)
        items.append((0, 1 + j % 2, 0, img, blob_map(rng), blob_map(rng), blob_map(rng), 'synth_whole'))

    fin, fout = os.path.join(WORK, 'cases.bin'), os.path.join(WORK, 'out.bin')
    with open(fin, 'wb') as f:
        f.write(struct.pack('<I', len(items)))
        for kind, thr, side, img, q1, qd, qc, _ in items:
            f.write(struct.pack('<BBH', kind, thr, side))
            if kind == 0:
                f.write(img.tobytes() + q1.tobytes())
            f.write(qd.tobytes() + qc.tobytes())
    exe = os.path.join(WORK, 'harness_fcdr')
    cmd = (f"gcc -O2 -Wall -Wextra -Werror -std=c99 -D_GNU_SOURCE -I{wsl(os.path.join(RX, 'src'))} "
           f"-o {wsl(exe)} {wsl(os.path.join(HERE, 'harness_fcdr.c'))} {wsl(os.path.join(RX, 'src', 'fundus_cdr.c'))} "
           f"&& {wsl(exe)} {wsl(fin)} {wsl(fout)}")
    subprocess.run(['wsl', '-e', 'sh', '-c', cmd], check=True)

    buf = open(fout, 'rb').read()
    off, fails, counts = 0, [], {}

    def take(n):
        nonlocal off
        b = buf[off:off + n]
        off += n
        return b
    for idx, (kind, thr, side, img, q1, qd, qc, label) in enumerate(items):
        found, cy, cx, size, area, s, y0, x0 = struct.unpack('<8i', take(32))
        p1 = np.frombuffer(take(P * 3), np.uint8).reshape(128, 128, 3)
        p2 = np.frombuffer(take(P * 3), np.uint8).reshape(128, 128, 3)
        dh, ch, dtop, ctop = struct.unpack('<4i', take(16))
        da, ca = struct.unpack('<2q', take(16))
        (vc,) = struct.unpack('<d', take(8))
        dm = np.frombuffer(take(s * s), np.uint8).reshape(s, s)
        cm = np.frombuffer(take(s * s), np.uint8).reshape(s, s)
        bad = []
        if kind == 0:
            ref = ref_whole(img, q1, qd, qc, thr)
            if not np.array_equal(p1, ref['p1']):
                bad.append('payload1')
            if found != ref['found']:
                bad.append('found')
            if ref['found'] and found:
                L = ref['loc']
                if (cy, cx, size, area) != (L['cy'], L['cx'], L['size'], L['area']):
                    bad.append('loc')
                if (s, y0, x0) != (ref['side'], ref['y0'], ref['x0']):
                    bad.append('window')
                if not np.array_equal(p2, ref['p2']):
                    bad.append('payload2')
                r = ref['r']
        else:
            r = cdr_ref.measure(qd, qc, side, thr, thr)
        if kind == 1 or (found and not bad):
            if (dh, ch, dtop, ctop, da, ca) != (r['disc_h'], r['cup_h'], r['disc_top'], r['cup_top'],
                                                r['disc_area'], r['cup_area']):
                bad.append('numbers')
            if (r['vcdr'] if r['vcdr'] is not None else 0.0) != vc:
                bad.append('vcdr')
            if not np.array_equal(dm.astype(bool), r['disc']) or not np.array_equal(cm.astype(bool), r['cup']):
                bad.append('masks')
        c = counts.setdefault(label, dict(cases=0, found=0, failed=0))
        c['cases'] += 1
        c['found'] += int(kind == 1 or found == 1)
        if bad:
            c['failed'] += 1
            fails.append(dict(case=idx, label=label, what=bad))
    assert off == len(buf)
    report = dict(eval_name=a.eval_name, counts=counts, failures=fails[:50], n_failures=len(fails),
                  pass_=not fails, checked='found, loc, window, payload1/2, heights, tops, areas, vcdr (double), masks')
    with open(os.path.join(HERE, 'fundus_cdr_host.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=1)
    print(json.dumps(counts), 'failures', len(fails))
    print('FUNDUS_CDR_HOST_PASS' if not fails else 'FUNDUS_CDR_HOST_FAIL')
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
