# -*- coding: utf-8 -*-
"""gen_vectors.py -- test vectors for the pp_layer HLS prototype, from real model outputs + synthetic hard cases.

Real logits come from the HLS bit-exact NumPy reference of the deployed networks (the same values the
board produces):
  fundus   5 STARE samples (tcp_image_ui/samples), 512, thr -3: count + 4-conn components + tile sums
           (golden = the board's C ai_view_count semantics, via scipy 4-connected labels)
  ct       train cases spleen_2 / spleen_3 (never the test split), z3 input, bilinear x4 (coarse 512 mask)
           and x2 (stage-2 style 256 mask) > 0.5 (golden = ct_volume.upsample_bilinear)
  echonet  3 VAL videos, t3 input, 112 crop of the 128 logits, >= 1, keep largest + fill holes
           (golden = echo_ws.mask_from_logit + echo_ws.clean)
  synth    spirals, combs, U shapes, checkerboards, noise: counts (4 / 8-conn) and clean, scipy golden
Every case is also run through ppref.py (the kernel's bit-level model) and must agree with the golden.

Writes vectors/<name>.bin + vectors/cases.txt and prints the provisional label counts (sizes the tables).

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe gen_vectors.py
"""
import json
import os
import struct
import sys

import numpy as np
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
RX = os.path.normpath(os.path.join(HERE, '..', '..'))
PLD = os.path.normpath(os.path.join(RX, '..', '..'))
UI = os.path.join(RX, '..', 'tcp_image_ui')
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(RX, 'ai', 'net'))
sys.path.insert(0, UI)

import ppref                                  # noqa: E402
from make_program_pkg import reference_logits  # noqa: E402

OUT = os.path.join(HERE, 'vectors')
OP_MASK, OP_CLEAN, OP_CONN8, OP_TILES = 1, 2, 4, 8
MAGIC = 0x31565050                          # 'PPV1'


def write_case(name, q, op, k, thr, tn, win, exp):
    q = np.ascontiguousarray(q, np.int8)
    h, w = q.shape
    x0, y0, cw, ch = win
    m = np.ascontiguousarray(exp['mask'], np.uint8)
    hdr = struct.pack('<16i', MAGIC, op, h, w, x0, y0, cw, ch, k, thr, tn, exp['count'], exp['comps'],
                      exp['largest'], m.shape[0], m.shape[1])
    with open(os.path.join(OUT, name + '.bin'), 'wb') as f:
        f.write(hdr + struct.pack('<16i', *[int(v) for v in exp['tiles']]) + q.tobytes() + m.tobytes())
    return name


def golden(q, k, thr, tn, win, conn8, clean):
    """independent golden: scipy / the original host code, not ppref."""
    q = np.asarray(q, np.int8)
    h, w = q.shape
    x0, y0, cw, ch = win
    if k == 1:
        m = q[y0:y0 + ch, x0:x0 + cw] >= thr
    else:
        import ct_volume
        m = ct_volume.upsample_bilinear(q[None, y0:y0 + ch, x0:x0 + cw], k)[0] > tn / 2.0
        if (x0, y0, cw, ch) != (0, 0, w, h):
            raise ValueError('upsampling golden only for the whole frame')
    st = np.ones((3, 3), bool) if conn8 else None
    lab, n = ndimage.label(m, structure=st)
    sizes = np.bincount(lab.ravel())[1:] if n else np.zeros(0, int)
    out = m
    if clean:
        import echo_ws
        out, _ = echo_ws.clean(m)
    t = q.astype(np.int64).reshape(4, h // 4, 4, w // 4).sum(axis=(1, 3)).ravel()
    return dict(mask=out, count=int(m.sum()), comps=int(n), largest=int(sizes.max()) if n else 0, tiles=t)


def check(name, q, op, k=1, thr=1, tn=1, win=None, stats=None):
    h, w = q.shape
    win = win or (0, 0, w, h)
    conn8, clean = bool(op & OP_CONN8), bool(op & OP_CLEAN)
    g = golden(q, k, thr, tn, win, conn8, clean)
    r = ppref.run(q, k, thr, tn, win, conn8, clean)
    same = (np.array_equal(g['mask'], r['mask']) and g['count'] == r['count'] and g['comps'] == r['comps']
            and g['largest'] == r['largest'] and np.array_equal(g['tiles'], r['tiles']))
    if not same:
        raise SystemExit('ppref disagrees with the golden on %s: %s' % (
            name, {kk: (g[kk], r[kk]) for kk in ('count', 'comps', 'largest')}))
    if stats is not None:
        stats.append(dict(name=name, labels=r['labels'], bg_labels=r.get('bg_labels', 0), comps=r['comps'],
                          count=r['count'], out=list(g['mask'].shape)))
    return write_case(name, q, op, k, thr, tn, win, g)


def synthetic():
    rng = np.random.RandomState(20261012)
    shapes = {}
    y, x = np.mgrid[0:128, 0:128]
    r, a = np.hypot(y - 64, x - 64), np.arctan2(y - 64, x - 64)
    shapes['spiral'] = (np.mod(r - 4 * a, 12) < 5) & (r < 60)
    comb = np.zeros((128, 128), bool)
    comb[10:118, 10:14] = True
    comb[10:118:6, 10:118] = True
    comb[114:118, 10:118] = True
    shapes['comb'] = comb
    u = np.zeros((128, 128), bool)
    for i in range(6):                         # nested U shapes: merges arrive late, from the bottom
        u[10 + 8 * i:118, 10 + 8 * i:14 + 8 * i] = True
        u[10 + 8 * i:118, 114 - 8 * i:118 - 8 * i] = True
        u[114 - 0:118, 10 + 8 * i:118 - 8 * i] = True
    shapes['nested_u'] = u
    shapes['checker'] = ((y // 3 + x // 3) % 2 == 0)
    shapes['diag_lines'] = ((x - y) % 5 == 0)               # only 8-connected along the diagonal
    ring = (r > 20) & (r < 30)
    ring2 = (r > 40) & (r < 50)
    shapes['rings_with_holes'] = ring | ring2 | (r < 5)
    shapes['noise'] = rng.rand(128, 128) < 0.45
    shapes['empty'] = np.zeros((128, 128), bool)
    shapes['full'] = np.ones((128, 128), bool)
    blobs = np.zeros((128, 128), bool)
    for cy, cx, rr in ((30, 30, 12), (90, 40, 12), (60, 95, 9)):      # two equal largest (ties)
        blobs |= np.hypot(y - cy, x - cx) < rr
    blobs[30, 30] = False                                             # a one-pixel hole
    shapes['ties_hole'] = blobs
    # 8-connected joins through a pixel whose only labelled neighbours are UL and UR (U and L empty):
    # rows of "X . X" above ". X ." (V shapes), and a zig-zag that only connects diagonally
    v = np.zeros((128, 128), bool)
    for yy in range(4, 124, 6):
        v[yy, 4:124:2] = True                    # isolated dots, every other column
        v[yy + 1, 5:123:4] = True                # joins pairs of dots below their gap
        v[yy + 2, 7:121:8] = True                # and pairs of pairs one row further down
    shapes['v_shapes'] = v
    zz = np.zeros((128, 128), bool)
    for xx in range(0, 128):
        zz[(xx // 2) % 2 * 2 + (xx // 4) * 0 + 60 - (xx % 2), xx] = True
    zz[10:20:2, 10:50:2] = True                  # dotted rows, diagonal joins only one way
    zz[11:21:2, 11:51:2] = True
    shapes['zigzag'] = zz
    return {n: np.where(s, 20, -20).astype(np.int8) for n, s in shapes.items()}


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        os.remove(os.path.join(OUT, f))
    names, stats = [], []

    for n, q in synthetic().items():
        names.append(check('syn_%s_c4' % n, q, OP_MASK | OP_TILES, thr=1, stats=stats))
        names.append(check('syn_%s_c8' % n, q, OP_MASK | OP_CONN8, thr=1, stats=stats))
        names.append(check('syn_%s_clean' % n, q, OP_MASK | OP_CLEAN, thr=1, stats=stats))
        names.append(check('syn_%s_clean_crop' % n, q, OP_MASK | OP_CLEAN, thr=1, win=(8, 8, 112, 112),
                           stats=stats))
    print('synthetic ok', flush=True)

    import imageio as uiio
    fund = os.path.join(PLD, 'medical_fpga', 'deployment', 'unet_network', 'vectors')
    for i in range(1, 6):
        raw, _ = uiio.load_image(os.path.join(UI, 'samples', 'im%04d.ppm' % i))
        img = np.frombuffer(raw, np.uint8).reshape(512, 512, 3)
        q = reference_logits(fund, img)
        names.append(check('fundus_im%04d' % i, q, OP_MASK | OP_TILES, thr=-3, stats=stats))
        names.append(check('fundus_im%04d_c8' % i, q, OP_MASK | OP_CONN8, thr=-3, stats=stats))
        print('fundus', i, stats[-2], flush=True)

    import ct_volume
    ctdep = os.path.join(PLD, 'ct', 'deploy', 'z3_r128')
    for case in ('spleen_2', 'spleen_3'):
        v = ct_volume.load_ct(os.path.join(PLD, 'Task09_Spleen', 'Task09_Spleen', 'imagesTr', case + '.nii.gz'))
        u8 = ct_volume.window_u8(v['hu'])
        pl = ct_volume.payloads(u8, v['spacing'][2])
        zs = np.nonzero(v['label'].reshape(len(u8), -1).any(1))[0]
        pick = sorted(set(list(zs[::3]) + [0, len(u8) // 2, len(u8) - 1]))
        for z in pick:
            q = reference_logits(ctdep, np.frombuffer(pl[z], np.uint8).reshape(128, 128, 3))
            names.append(check('ct_%s_z%03d_k4' % (case, z), q, OP_MASK, k=4, tn=1, stats=stats))
            names.append(check('ct_%s_z%03d_k2' % (case, z), q, OP_MASK, k=2, tn=1, stats=stats))
        print('ct', case, len(pick), 'slices', flush=True)

    import csv
    import echo_ws
    ecdep = os.path.join(PLD, 'echonet', 'deploy', 't3')
    vids = [r['FileName'] for r in csv.DictReader(open(os.path.join(PLD, 'EchoNet-Dynamic', 'FileList.csv')))
            if r['Split'] == 'VAL'][:3]
    for vid in vids:
        frames, fps = echo_ws.read_avi(os.path.join(PLD, 'EchoNet-Dynamic', 'Videos', vid + '.avi'))
        idx, pl = echo_ws.payloads(frames, fps, 't3', 1)
        for j in range(0, len(idx), 6):
            q = reference_logits(ecdep, np.frombuffer(pl[j], np.uint8).reshape(128, 128, 3))
            names.append(check('echo_%s_f%03d' % (vid[:10], idx[j]), q, OP_MASK | OP_CLEAN, thr=1,
                               win=(8, 8, 112, 112), stats=stats))
        print('echonet', vid, flush=True)

    with open(os.path.join(OUT, 'cases.txt'), 'w') as f:
        f.write('\n'.join(names) + '\n')
    with open(os.path.join(OUT, 'stats.json'), 'w') as f:
        json.dump(stats, f, indent=1)
    lab = max(s['labels'] for s in stats)
    print('cases', len(names), 'max provisional labels', lab, 'max bg labels', max(s['bg_labels'] for s in stats))
    for kind in ('syn', 'fundus', 'ct', 'echo'):
        sub = [s for s in stats if s['name'].startswith(kind)]
        print(kind, 'max labels', max(s['labels'] for s in sub), 'max bg', max(s['bg_labels'] for s in sub))
    print('GEN_VECTORS_PASS')


if __name__ == '__main__':
    main()
