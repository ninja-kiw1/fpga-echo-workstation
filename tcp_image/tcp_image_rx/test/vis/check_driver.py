"""Host check of src/vis_ai.c (PS driver of vis_layer): descriptor packing, BRAM layout and band splitting.

The driver runs against the vis_layer C++ kernel on a fake tensor BRAM (host_vis.cpp, -DVIS_HOST_TEST); results
are compared with ai/vis_hls/visref.py (the kernel spec, itself checked against registration.py / confidence.py /
ai_view.c) and, for 128 integer registration, directly with registration.Registrar's MAD of every candidate.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe check_driver.py
"""
import json
import os
import struct
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RX = os.path.normpath(os.path.join(HERE, '..', '..'))
UI = os.path.normpath(os.path.join(RX, '..', 'tcp_image_ui'))
VH = os.path.join(RX, 'ai', 'vis_hls')
sys.path.insert(0, VH)
sys.path.insert(0, UI)
import visref as V                    # noqa: E402
import registration as reg            # noqa: E402
import confidence                     # noqa: E402
from gen_vectors import compose_send, pp_logits, shift_int  # noqa: E402

TAB = np.array(json.load(open(os.path.join(VH, 'absdiff_table.json')))['table'], np.int64)
WORK = os.path.join(HERE, 'work')


def wsl(p):
    p = os.path.abspath(p).replace('\\', '/')
    return '/mnt/' + p[0].lower() + p[2:]


def build():
    os.makedirs(WORK, exist_ok=True)
    inc = '/mnt/e/AMD/Xilinx/2025.2/Vitis/include'
    sh = ('set -e; gcc -O2 -std=c99 -D_GNU_SOURCE -DVIS_HOST_TEST -Wall -Wextra -I{src} -c {src}/vis_ai.c -o {w}/vis_ai.o; '
          'g++ -O2 -std=c++14 -I{inc} -I{vh}/src -I{src} {h}/host_vis.cpp {vh}/src/vis_layer.cpp {w}/vis_ai.o -o {w}/host_vis'
          ).format(src=wsl(os.path.join(RX, 'src')), w=wsl(WORK), inc=inc, vh=wsl(VH), h=wsl(HERE))
    subprocess.check_call(['wsl', 'sh', '-c', sh])


def main():
    build()
    img = np.load(os.path.join(RX, '..', '..', 'echo', 'data', 'cache', 'seq_val_img128.npy'), mmap_mode='r')
    rng = np.random.default_rng(7)
    cases, expect = [], []
    for i in range(6):
        t = int(rng.integers(len(img) - 1))
        a = np.asarray(img[t])
        d = (int(rng.integers(-7, 8)), int(rng.integers(-7, 8)))
        b0 = np.asarray(img[t + 1])
        b = np.frombuffer(reg.shifted(b0.tobytes(), b0.tobytes(), -d[0], -d[1]), np.uint8).reshape(128, 128)
        for mode, radius, steps, sub in ((0, 7, (4, 2, 1), 0), (1, 7, (4, 2, 1), 0), (0, 7, (4, 2, 1), 2)):
            cases.append(('reg', a, b, mode, radius, steps, sub))
    v512 = np.load(os.path.join(RX, '..', '..', 'echo', 'data', 'cache', 'val_img512.npy'), mmap_mode='r')
    g = np.asarray(v512[5])
    g = (g[..., 0] if g.ndim == 3 else g)[::2, ::2]
    cases.append(('reg', g, shift_int(g, 11, -6), 0, 0, (8, 4, 2, 1), 2))
    cases.append(('reg', g, shift_int(g, -3, 4), 1, 5, (4, 2, 1), 1))
    import imageio as uiio
    raw, _ = uiio.load_image(os.path.join(UI, 'samples', 'im0001.ppm'))
    base = np.frombuffer(raw, np.uint8).reshape(512, 512, 3)
    q, _ = pp_logits('fundus_im0001')
    heat = np.array(confidence._HEAT_LUT, np.uint8)
    cases.append(('ovl', dict(ow=512, oh=512, flags=V.BASE_RGB | V.F_HAS_LOGIT | V.F_FILL, thr=-3), base, q, None, None,
                  np.frombuffer(confidence.overlay_rgb(raw, bytes((np.asarray(q, np.int64) + 128).astype(np.uint8).ravel()), -3), np.uint8).reshape(512, 512, 3)))
    qc, mask = pp_logits('ct_spleen_2_z070_k4')
    cases.append(('ovl', dict(ow=512, oh=512, flags=V.BASE_HEAT | V.F_HAS_LOGIT), None, qc, None, heat, None))
    grey = rng.integers(0, 256, (512, 512)).astype(np.uint8)
    cases.append(('ovl', dict(ow=512, oh=512, flags=V.BASE_GREY | V.F_HAS_BITS | V.F_FILL_FROM_BITS | V.F_FILL | V.F_CONTOUR,
                              fill_a=6, fill=(0, 255, 0), contour=(255, 255, 0)), grey, None, mask, None, None))
    for i in range(8):
        ow, oh = 16 * int(rng.integers(1, 33)), int(rng.integers(1, 513))
        lw, lh = 16 * int(rng.integers(1, ow // 16 + 1)), int(rng.integers(1, oh + 1))
        mw, mh = int(rng.integers(1, ow + 1)), int(rng.integers(1, oh + 1))
        kind = int(rng.integers(0, 4))
        fl = kind | V.F_HAS_LOGIT | V.F_HAS_BITS | int(rng.choice([0, V.F_FILL_FROM_BITS])) | V.F_FILL | V.F_CONTOUR | V.F_UNSURE
        bs = rng.integers(0, 256, (oh, ow, 3) if kind == V.BASE_RGB else (oh, ow)).astype(np.uint8)
        cases.append(('ovl', dict(ow=ow, oh=oh, flags=fl, thr=int(rng.integers(-10, 10)), band=int(rng.integers(0, 5)),
                                  fill_a=int(rng.integers(0, 17)), unsure_a=int(rng.integers(0, 17)),
                                  fill=(200, 10, 30), unsure=(1, 2, 250), contour=(9, 99, 199)),
                      bs, rng.integers(-30, 30, (lh, lw)).astype(np.int8), rng.random((mh, mw)) < 0.4,
                      rng.integers(0, 256, (256, 3)).astype(np.uint8), None))
    with open(os.path.join(WORK, 'cases.bin'), 'wb') as f:
        for c in cases:
            if c[0] == 'reg':
                _, a, b, mode, radius, steps, sub = c
                st = list(steps) + [0] * (4 - len(steps))
                f.write(struct.pack('<i', 1) + struct.pack('<9i', a.shape[0], mode, radius, *st, sub, 0))
                f.write(np.ascontiguousarray(a, np.uint8).tobytes() + np.ascontiguousarray(b, np.uint8).tobytes())
                f.write(bytes(int(v) for v in TAB))
            else:
                _, p, bs, qq, bits, lut, _ = c
                ow, oh, fl = p['ow'], p['oh'], p['flags']
                lh, lw = qq.shape if qq is not None else (0, 0)
                mh, mw = bits.shape if bits is not None else (0, 0)
                rgb = lambda t: t[0] | t[1] << 8 | t[2] << 16
                f.write(struct.pack('<i', 2) + struct.pack('<16i', ow, oh, fl, lw, lh, mw, mh, p.get('thr', 0), p.get('band', 0),
                                                           p.get('fill_a', 16), p.get('unsure_a', 16), rgb(p.get('fill', (255, 0, 0))),
                                                           rgb(p.get('unsure', (255, 200, 0))), rgb(p.get('contour', (0, 255, 0))), 0, 0))
                if fl & 3 in (V.BASE_GREY, V.BASE_RGB):
                    f.write(np.ascontiguousarray(bs, np.uint8).tobytes())
                if fl & V.F_HAS_LOGIT:
                    f.write(np.ascontiguousarray(qq, np.int8).tobytes())
                if fl & V.F_HAS_BITS:
                    f.write(V.bits_pack(bits))
                if fl & 3 == V.BASE_HEAT:
                    f.write(np.ascontiguousarray(lut, np.uint8).tobytes())
    subprocess.check_call(['wsl', wsl(os.path.join(WORK, 'host_vis')), wsl(os.path.join(WORK, 'cases.bin')), wsl(os.path.join(WORK, 'out.bin'))])
    data = open(os.path.join(WORK, 'out.bin'), 'rb').read()
    pos, bad, n_reg, n_ovl, bands = 0, [], 0, 0, []
    for k, c in enumerate(cases):
        rc = struct.unpack_from('<i', data, pos)[0]
        pos += 4
        if c[0] == 'reg':
            qx, qy, S, O, npath, nrec = struct.unpack_from('<6i', data, pos)
            pos += 24
            path = [(struct.unpack_from('b', data, pos + 2 * i)[0], struct.unpack_from('b', data, pos + 2 * i + 1)[0]) for i in range(npath)]
            pos += 16
            recs = [struct.unpack_from('<4i', data, pos + 16 * i) for i in range(nrec)]
            pos += 16 * nrec
            _, a, b, mode, radius, steps, sub = c
            r = V.search(a, b, TAB, mode, radius, steps, sub)
            ok = rc == 0 and (qx, qy) == r['best'] and (S, O) == (r['S'], r['O']) and path == r['path'] and \
                [tuple(x) for x in recs] == [tuple(x) for x in r['records']]
            if ok and a.shape[0] == 128 and sub == 0:            # and the board's F5 path, candidate by candidate
                g = reg.Registrar(a.tobytes(), b.tobytes(), compose_send(TAB))
                best = g.three_step(steps)[0] if mode == 0 else g.full(radius)
                ok = (4 * best[0], 4 * best[1]) == (qx, qy) and all(g.cost[(x[0] // 4, x[1] // 4)] == x[2] / float(x[3]) for x in recs)
            n_reg += 1
        else:
            calls = struct.unpack_from('<i', data, pos)[0]
            pos += 4
            _, p, bs, qq, bits, lut, golden = c
            ow, oh = p['ow'], p['oh']
            got = np.frombuffer(data, np.uint8, 3 * ow * oh, pos).reshape(oh, ow, 3)
            pos += 3 * ow * oh
            ref = V.overlay(ow, oh, p['flags'], bs, qq, bits, lut, p.get('thr', 0), p.get('band', 0), p.get('fill', (255, 0, 0)),
                            p.get('fill_a', 16), p.get('unsure', (255, 200, 0)), p.get('unsure_a', 16), p.get('contour', (0, 255, 0)))
            ok = rc == 0 and np.array_equal(got, ref) and (golden is None or np.array_equal(got, golden))
            if p['flags'] & 3 == V.BASE_HEAT and qq.shape == (128, 128):
                ok = ok and np.array_equal(got, np.frombuffer(confidence.heat_rgb(bytes(
                    (np.repeat(np.repeat(np.asarray(qq, np.int64), 4, 0), 4, 1) + 128).astype(np.uint8).ravel())), np.uint8).reshape(512, 512, 3))
            bands.append(calls)
            n_ovl += 1
        if not ok:
            bad.append(k)
    print('reg cases %d, overlay cases %d (bands per frame %s), failed %s' % (n_reg, n_ovl, bands, bad))
    if not bad:
        print('VIS_DRIVER_HOST_PASS')
    # the PS fallback of JOB_OVERLAY (job_overlay.c overlay_ps) on the same overlay cases
    sh = 'set -e; gcc -O2 -std=c99 -D_GNU_SOURCE -Wall -Wextra -I{src} {h}/host_ovl_ps.c -o {w}/host_ovl_ps'.format(
        src=wsl(os.path.join(RX, 'src')), h=wsl(HERE), w=wsl(WORK))
    subprocess.check_call(['wsl', 'sh', '-c', sh])
    subprocess.check_call(['wsl', wsl(os.path.join(WORK, 'host_ovl_ps')), wsl(os.path.join(WORK, 'cases.bin')), wsl(os.path.join(WORK, 'out_ps.bin'))])
    data = open(os.path.join(WORK, 'out_ps.bin'), 'rb').read()
    pos, bad_ps = 0, []
    for k, c in enumerate([c for c in cases if c[0] == 'ovl']):
        rc, _ = struct.unpack_from('<2i', data, pos)
        pos += 8
        _, p, bs, qq, bits, lut, _ = c
        n = 3 * p['ow'] * p['oh']
        got = np.frombuffer(data, np.uint8, n, pos).reshape(p['oh'], p['ow'], 3)
        pos += n
        ref = V.overlay(p['ow'], p['oh'], p['flags'], bs, qq, bits, lut, p.get('thr', 0), p.get('band', 0), p.get('fill', (255, 0, 0)),
                        p.get('fill_a', 16), p.get('unsure', (255, 200, 0)), p.get('unsure_a', 16), p.get('contour', (0, 255, 0)))
        if rc != 0 or not np.array_equal(got, ref):
            bad_ps.append(k)
    print('PS fallback overlay cases %d, failed %s' % (len(cases) - n_reg, bad_ps))
    if not bad_ps:
        print('OVERLAY_PS_HOST_PASS')


if __name__ == '__main__':
    main()
