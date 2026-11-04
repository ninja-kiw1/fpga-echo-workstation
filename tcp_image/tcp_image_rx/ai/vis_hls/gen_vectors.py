# -*- coding: utf-8 -*-
"""gen_vectors.py -- test vectors for the vis_layer HLS kernel (r2), each one checked against independent goldens.

Every vector is one kernel call: a BRAM image (descriptor + inputs), the expected return value and the
expected output words. The testbench fills the rest of the BRAM with a pseudo-random pattern and also
checks that nothing outside the expected output changed.

REG goldens (not visref):
  128, integer   registration.Registrar driven through its own compose() + 16 tile sums, where the PL logit
                 is the measured reg_absdiff table (logit depends on |R - G| only; table = all 65 536 pairs
                 through the HLS bit-exact reference, see make_absdiff_table): best, path and every MAD.
  256 / subpel   Registrar.best / three_step on candidates in quarter pixels, with costs from an independent
                 float bilinear (exact here: multiples of 1/16) + floor(x + 0.5).
OVL goldens:
  confidence.overlay_rgb / uncertain_rgb / heat_rgb (the PC code) on the board's logit image; the board's
  own ai_view_overlay_rgb (C, linked into the testbench, flag AIV); scipy binary_erosion for contours;
  a scalar per-pixel loop for the rest. (Kernel v2 samples nearest only; a bilinear flag must give BAD.)
Also prints the known-shift recovery on CAMUS VAL pairs (= echo/verify_board_reg.py part B) and the
sub-pixel accuracy on fractionally shifted frames.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe gen_vectors.py [--quick]
"""
import argparse
import json
import os
import struct
import sys

import numpy as np
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
RX = os.path.normpath(os.path.join(HERE, '..', '..'))
PLD = os.path.normpath(os.path.join(RX, '..', '..'))
UI = os.path.normpath(os.path.join(RX, '..', 'tcp_image_ui'))
sys.path.insert(0, HERE)
sys.path.insert(0, UI)

import visref as V                              # noqa: E402

OUT = os.path.join(HERE, 'vectors')
PPV = os.path.join(RX, 'ai', 'pp_hls', 'vectors')
TABLE = os.path.join(HERE, 'absdiff_table.json')
CAMUS = os.path.join(PLD, 'echo', 'data', 'cache')
MAGIC = 0x56534956                              # 'VISV'
AIV = 1                                          # testbench: also compare with ai_view_overlay_rgb


# ------------------------------------------------------------------ vector file
class Mem(object):
    def __init__(self):
        self.chunks = []
        self.top = V.DESC_WORDS

    def alloc(self, nwords):
        off = self.top
        self.top += nwords
        assert self.top <= V.BRAM_WORDS, 'BRAM overflow'
        return off

    def put(self, off, data):
        data = bytes(data)
        if len(data) % 16:
            data += bytes(16 - len(data) % 16)
        self.chunks.append((off, data))
        return off

    def add(self, data):
        data = bytes(data)
        return self.put(self.alloc((len(data) + 15) // 16), data)


def desc_bytes(fields):
    f = list(fields) + [0] * (32 - len(fields))
    return struct.pack('<32i', *[int(v) for v in f])


def write_vec(name, mem, desc_off, ret, expect, flags=0, extra=b''):
    """expect: list of (word offset, bytes)."""
    with open(os.path.join(OUT, name + '.bin'), 'wb') as f:
        f.write(struct.pack('<6i', MAGIC, desc_off, ret, len(mem.chunks), len(expect), flags))
        for off, data in list(mem.chunks) + list(expect):
            f.write(struct.pack('<2i', off, len(data) // 16) + data)
        f.write(extra)
    return name


# ------------------------------------------------------------------ the reg_absdiff table
def make_absdiff_table():
    """logit of model 5 (reg_absdiff) for every |R - G|, via the HLS bit-exact reference (one 256 x 256 run)."""
    if os.path.exists(TABLE):
        return np.array(json.load(open(TABLE))['table'], np.int64)
    sys.path.insert(0, os.path.join(RX, 'ai', 'net'))
    from make_program_pkg import reference_logits
    a = np.tile(np.arange(256, dtype=np.uint8), (256, 1))
    rgb = np.stack([a, a.T.copy(), np.zeros_like(a)], -1)
    lg = reference_logits(os.path.join(PLD, 'echo', 'deploy', 'reg_absdiff'), rgb).astype(np.int64)
    d = np.abs(a.astype(np.int64) - a.T.astype(np.int64))
    tab = []
    for k in range(256):
        u = np.unique(lg[d == k])
        if len(u) != 1:
            raise SystemExit('reg_absdiff logit is not a function of |R - G| at %d: %s' % (k, u))
        tab.append(int(u[0]))
    json.dump(dict(source='HLS bit-exact reference of echo/deploy/reg_absdiff, 256 x 256 input R = column, '
                          'G = row; logit is a function of |R - G| only (checked on all 65 536 pairs)',
                   table=tab), open(TABLE, 'w'), indent=0)
    return np.array(tab, np.int64)


# ------------------------------------------------------------------ REG goldens
import registration as reg                      # noqa: E402


def compose_send(tab):
    """the board path of today: 512 frame -> logit = tab[|R - G|] -> 16 tile sums (ai_view_fill_reg)."""
    def send(frame):
        fr = np.frombuffer(frame, np.uint8).reshape(512, 512, 3).astype(np.int64)
        lg = tab[np.abs(fr[..., 0] - fr[..., 1])]
        return [int(lg[ty * 128:(ty + 1) * 128, tx * 128:(tx + 1) * 128].sum()) for ty in range(4) for tx in range(4)], {}
    return send


class QReg(reg.Registrar):
    """Registrar with candidates in quarter pixels and costs from an independent float bilinear."""

    def __init__(self, a, b, tab):
        reg.Registrar.__init__(self, a, b, None)
        self.A = np.asarray(a, np.float64)
        self.B = np.asarray(b, np.float64)
        self.tab = tab

    def evaluate(self, cands):
        n = self.A.shape[0]
        for qx, qy in cands:
            if (qx, qy) in self.cost:
                continue
            g = np.empty((n, n))
            ok = np.zeros((n, n), bool)
            for y in range(n):
                sy = y - qy / 4.0
                y0 = int(np.floor(sy))
                ty = sy - y0
                for_rows = (y0 >= 0) and (y0 + (1 if ty > 0 else 0) <= n - 1)
                if not for_rows:
                    continue
                xs = np.arange(n) - qx / 4.0
                x0 = np.floor(xs).astype(int)
                tx = xs - x0
                okx = (x0 >= 0) & (x0 + (tx > 0) <= n - 1)
                x0c = np.clip(x0, 0, n - 1)
                x1c = np.clip(x0 + 1, 0, n - 1)
                y1 = min(y0 + 1, n - 1)
                top = self.B[y0, x0c] * (1 - tx) + self.B[y0, x1c] * tx
                bot = self.B[y1, x0c] * (1 - tx) + self.B[y1, x1c] * tx
                g[y] = np.floor(top * (1 - ty) + bot * ty + 0.5)
                ok[y] = okx
            d = np.where(ok, np.abs(self.A - g), 0).astype(np.int64)
            cnt = int(ok.sum())
            self.cost[(qx, qy)] = float(self.tab[d].sum()) / float(cnt)
        return [self.cost[c] for c in cands]


def golden_search(a, b, tab, mode, radius, steps, subpel):
    n = a.shape[0]
    if n == 128 and subpel == 0:
        r = reg.Registrar(a.tobytes(), b.tobytes(), compose_send(tab))
        if mode == 0:
            best, path = r.three_step(tuple(steps))
        else:
            best, path = r.full(radius), [(0, 0)]
            path.append(best)
        q4 = lambda p: (4 * p[0], 4 * p[1])
        return q4(best), [q4(p) for p in path], {q4(k): v for k, v in r.cost.items()}
    r = QReg(a, b, tab)
    if mode == 0:
        best, path = r.three_step(tuple(4 * s for s in steps))
    else:
        best = r.best([(4 * dx, 4 * dy) for dy in range(-radius, radius + 1) for dx in range(-radius, radius + 1)])
        path = [(0, 0), best]
    for s in (2, 1)[:subpel]:
        best = r.best([(best[0] + i * s, best[1] + j * s) for j in (-1, 0, 1) for i in (-1, 0, 1)])
        path.append(best)
    return best, path, dict(r.cost)


def reg_vector(name, a, b, tab, mode=0, radius=7, steps=(4, 2, 1), subpel=0, check=True, stats=None):
    a = np.ascontiguousarray(a, np.uint8)
    b = np.ascontiguousarray(b, np.uint8)
    n = a.shape[0]
    r = V.search(a, b, tab, mode, radius, steps, subpel)
    if check:
        best, path, cost = golden_search(a, b, tab, mode, radius, steps, subpel)
        bad = [q for q in r['records'] if cost.get((q[0], q[1])) != q[2] / float(q[3])]
        if r['best'] != tuple(best) or r['path'] != [tuple(p) for p in path] or bad or \
                len({(q[0], q[1]) for q in r['records']}) != len(cost):
            raise SystemExit('visref REG disagrees with the golden on %s: %s %s / %s %s, %d bad costs'
                             % (name, r['best'], r['path'], best, path, len(bad)))
    m = Mem()
    fw = n * n // 16
    ao = m.add(a.tobytes())
    bo = m.add(b.tobytes())
    to = m.add(bytes(int(v) & 255 for v in tab))
    oo = m.alloc(4 + len(r['records']))
    st = 0
    for i, s in enumerate(steps):
        st |= s << (8 * i)
    m.put(0, desc_bytes([V.MAGIC | V.OP_REG, n, mode | subpel << 8 | radius << 16, st, ao, bo, to, oo]))
    assert fw == (len(a.tobytes()) // 16)
    w0 = struct.pack('<4i', 0, len(r['records']), r['best'][0], r['best'][1])
    w1 = struct.pack('<4i', r['S'], r['O'], len(r['path']), 0)
    pts = bytearray(16)
    for i, (px, py) in enumerate(r['path']):
        pts[2 * i], pts[2 * i + 1] = px & 255, py & 255
    recs = b''.join(struct.pack('<4i', *q) for q in r['records'])
    if stats is not None:
        stats.append(dict(name=name, n=n, mode=mode, subpel=subpel, evals=len(r['records']), best=r['best']))
    return write_vec(name, m, 0, 0, [(oo, w0 + w1 + bytes(pts) + bytes(16) + recs)])


def bad_vector(name, fields, extra_chunks=()):
    m = Mem()
    for off, data in extra_chunks:
        m.put(off, data)
    m.put(0, desc_bytes(fields))
    return write_vec(name, m, 0, V.ST_BAD, [])


# ------------------------------------------------------------------ OVL goldens
import confidence                               # noqa: E402
import proto                                    # noqa: E402


def contour_golden(m):
    cross = ndimage.generate_binary_structure(2, 1)
    return m & ~ndimage.binary_erosion(m, structure=cross, border_value=0)


def bilinear_float(q, k):
    """ct_volume.upsample_bilinear (float, half-pixel centres, edge clamp) with separate row / column sizes."""
    q = np.asarray(q, np.float64)

    def axis(n):
        src = np.maximum((np.arange(n * k, dtype=np.float64) + 0.5) / k - 0.5, 0.0)
        i0 = np.floor(src).astype(np.int64)
        return i0, np.minimum(i0 + 1, n - 1), src - i0

    y0, y1, wy = axis(q.shape[0])
    x0, x1, wx = axis(q.shape[1])
    rows = q[y0] * (1 - wy)[:, None] + q[y1] * wy[:, None]
    return rows[:, x0] * (1 - wx)[None, :] + rows[:, x1] * wx[None, :]


def scalar_overlay(ow, oh, flags, base, q, bits, lut, thr, band, fill, fa, uns, ua, con):
    """per-pixel loop written from the spec text (not visref): the golden for the generic cases."""
    kind = flags & 3
    bil = bool(flags & V.F_BILINEAR)
    lh, lw = q.shape if q is not None else (0, 0)
    out = np.zeros((oh, ow, 3), np.int64)
    if flags & V.F_HAS_LOGIT:
        if bil:
            vals = bilinear_float(q, ow // lw)
        else:
            vals = np.array([[float(q[y * lh // oh, x * lw // ow]) for x in range(ow)] for y in range(oh)])
    m = np.zeros((oh, ow), bool)
    for y in range(oh):
        for x in range(ow):
            if flags & V.F_FILL_FROM_BITS:
                mh, mw = bits.shape
                m[y, x] = bits[y * mh // oh, x * mw // ow]
            elif flags & V.F_HAS_LOGIT:
                m[y, x] = vals[y, x] >= thr
    edge = contour_golden(m)
    for y in range(oh):
        for x in range(ow):
            if kind == V.BASE_NONE:
                b = (0, 0, 0)
            elif kind == V.BASE_GREY:
                b = (int(base[y, x]),) * 3
            elif kind == V.BASE_RGB:
                b = tuple(int(c) for c in base[y, x])
            else:
                b = tuple(int(v) for v in lut[int(np.floor(vals[y, x] + 0.5)) + 128])
            c = a = None
            if flags & V.F_FILL and m[y, x]:
                c, a = fill, fa
            if flags & V.F_UNSURE and abs(vals[y, x] - thr) <= band:
                c, a = uns, ua
            px = b if c is None else tuple((a * c[i] + (16 - a) * b[i] + 8) // 16 for i in range(3))
            if flags & V.F_CONTOUR and edge[y, x]:
                px = con
            out[y, x] = px
    return out.astype(np.uint8)


def rgb24(c):
    return c[0] | c[1] << 8 | c[2] << 16


def ovl_vectors(name, ow, oh, flags, base=None, q=None, bits=None, lut=None, thr=0, band=0, fill=(255, 0, 0),
                fa=16, uns=(255, 200, 0), ua=16, con=(0, 255, 0), golden=None, bands=None, aiv=False):
    """check visref against `golden` (whole frame), then write one vector per band."""
    ref = V.overlay(ow, oh, flags, base, q, bits, lut, thr, band, fill, fa, uns, ua, con)
    if golden is not None and not np.array_equal(ref, golden):
        diff = np.argwhere(np.any(ref != golden, -1))
        raise SystemExit('visref OVL disagrees with the golden on %s: %d pixels, first %s %s %s'
                         % (name, len(diff), diff[0], ref[tuple(diff[0])], golden[tuple(diff[0])]))
    kind = flags & 3
    bil = bool(flags & V.F_BILINEAR)
    bstride = 3 * ow // 16 if kind == V.BASE_RGB else (ow // 16 if kind == V.BASE_GREY else 0)
    ostride = 3 * ow // 16
    lh, lw = q.shape if q is not None else (0, 0)
    mh, mw = bits.shape if bits is not None else (0, 0)
    lstride, mstride = lw // 16, (mw + 127) // 128
    if bands is None:
        src = lambda stride, n: -(-stride * n // oh) if n else 0
        per = (V.BRAM_WORDS - 64 - 48 - 4 * (lstride + mstride)) // (bstride + ostride + src(lstride, lh) + src(mstride, mh))
        per = max(1, min(oh, per))
        bands = [(y, min(oh, y + per)) for y in range(0, oh, per)]
    names = []
    for bi, (ya, yb) in enumerate(bands):
        m = Mem()
        lut_off = m.add(b''.join(bytes(c) + bytes(1) for c in np.asarray(lut, np.uint8))) if kind == V.BASE_HEAT else 0
        lo_l = hi_l = lo_m = hi_m = 0
        loff = moff = 0
        if flags & V.F_HAS_LOGIT:
            lo_l, hi_l = V.ovl_rows_needed(ya, yb, oh, lh, bil, ow // lw if bil else 1)
            loff = m.add(np.ascontiguousarray(q[lo_l:hi_l + 1], np.int8).tobytes())
        if flags & V.F_HAS_BITS:
            lo_m, hi_m = V.ovl_rows_needed(ya, yb, oh, mh, False)
            moff = m.add(V.bits_pack(bits[lo_m:hi_m + 1]))
        boff = m.add(np.ascontiguousarray(base[ya:yb], np.uint8).tobytes()) if kind in (V.BASE_GREY, V.BASE_RGB) else 0
        ooff = m.alloc((yb - ya) * ostride)
        fields = [V.MAGIC | V.OP_OVL, ow | oh << 16, ya | yb << 16, flags, lw | lh << 16, loff,
                  lo_l | (hi_l - lo_l + 1) << 16 if q is not None else 0, mw | mh << 16, moff,
                  lo_m | (hi_m - lo_m + 1) << 16 if bits is not None else 0, boff, ooff, lut_off,
                  (thr & 255) | band << 8 | fa << 16, rgb24(fill), rgb24(uns), ua, rgb24(con)]
        m.put(0, desc_bytes(fields))
        extra = b''
        vflags = 0
        if aiv:
            vflags = AIV
            extra = struct.pack('<9i', ow, oh, lw, lh, thr, ya, yb, ooff, kind) + \
                (np.ascontiguousarray(base, np.uint8).tobytes() if kind == V.BASE_RGB else b'') + \
                np.ascontiguousarray(q, np.int8).tobytes()
        nm = '%s_b%d' % (name, bi) if len(bands) > 1 else name
        names.append(write_vec(nm, m, 0, 0, [(ooff, np.ascontiguousarray(ref[ya:yb]).tobytes())], vflags, extra))
    return names


def board_plane(q):
    """the board's 512 logit image (ai_view_logit_rgb, nearest) as confidence.logit_plane gives it."""
    lh, lw = q.shape
    y = (np.arange(512) * lh) // 512
    x = (np.arange(512) * lw) // 512
    return (np.asarray(q, np.int64)[y][:, x] + proto.LOGIT_IMG_OFFSET).astype(np.uint8).tobytes()


def from_bytes_rgb(b, n=512):
    return np.frombuffer(b, np.uint8).reshape(n, n, 3)


def pp_logits(name):
    """logits of a pp_hls vector (HLS bit-exact reference of the deployed model)."""
    b = open(os.path.join(PPV, name + '.bin'), 'rb').read()
    hd = struct.unpack('<16i', b[:64])
    h, w = hd[2], hd[3]
    q = np.frombuffer(b[128:128 + h * w], np.int8).reshape(h, w)
    mh, mw = hd[14], hd[15]
    mask = np.frombuffer(b[128 + h * w:128 + h * w + mh * mw], np.uint8).reshape(mh, mw).astype(bool)
    return q, mask


# ------------------------------------------------------------------ cases
def reg_cases(tab, quick, stats):
    names = []
    img = np.load(os.path.join(CAMUS, 'seq_val_img128.npy'), mmap_mode='r')
    index = json.load(open(os.path.join(CAMUS, 'seq_val_index.json')))
    rng = np.random.default_rng(20261014)

    # known shifts on CAMUS VAL consecutive frames (as echo/verify_board_reg.py B): B = t + 1 moved by -d
    rec = dict(tss=0, full=0, pairs=0, full_pairs=0)
    npairs = 20 if quick else 100
    for i in range(npairs):
        s = index[int(rng.integers(len(index)))]
        t = s['start'] + int(rng.integers(s['length'] - 1))
        while True:
            d = (int(rng.integers(-7, 8)), int(rng.integers(-7, 8)))
            if d != (0, 0):
                break
        a = np.asarray(img[t])
        b0 = np.asarray(img[t + 1])
        b = np.frombuffer(reg.shifted(b0.tobytes(), b0.tobytes(), -d[0], -d[1]), np.uint8).reshape(128, 128)
        names.append(reg_vector('reg128_tss_%03d' % i, a, b, tab, 0, stats=stats))
        rec['pairs'] += 1
        rec['tss'] += stats[-1]['best'] == (4 * d[0], 4 * d[1])
        if i < (6 if quick else 30):
            names.append(reg_vector('reg128_full_%03d' % i, a, b, tab, 1, 7, stats=stats))
            rec['full_pairs'] += 1
            rec['full'] += stats[-1]['best'] == (4 * d[0], 4 * d[1])
    print('known shifts (128, CAMUS VAL t / t+1): three-step %d/%d, full %d/%d'
          % (rec['tss'], rec['pairs'], rec['full'], rec['full_pairs']), flush=True)

    # 256: CAMUS VAL 512 frames [::2, ::2] (consecutive cache rows are different patients: use one frame + shift)
    v512 = np.load(os.path.join(CAMUS, 'val_img512.npy'), mmap_mode='r')
    rec256 = [0, 0]
    for i in range(4 if quick else 12):
        g = np.asarray(v512[int(rng.integers(len(v512)))])
        g = g[..., 0] if g.ndim == 3 else g
        g = g[::2, ::2]
        d = (int(rng.integers(-15, 16)), int(rng.integers(-15, 16)))
        b = shift_int(g, -d[0], -d[1])
        names.append(reg_vector('reg256_tss_%03d' % i, g, b, tab, 0, steps=(8, 4, 2, 1), stats=stats))
        rec256[0] += stats[-1]['best'] == (4 * d[0], 4 * d[1])
        rec256[1] += 1
    print('known shifts (256, steps 8/4/2/1): %d/%d' % tuple(rec256), flush=True)
    a = np.asarray(v512[3])
    a = (a[..., 0] if a.ndim == 3 else a)[::2, ::2]
    names.append(reg_vector('reg256_full_r4', a, shift_int(a, 3, -2), tab, 1, 4, stats=stats))
    if not quick:
        names.append(reg_vector('reg256_full_r31', a, shift_int(a, 20, -27), tab, 1, 31, check=False, stats=stats))
        names.append(reg_vector('reg128_full_r15', np.asarray(img[50]), shift_int(np.asarray(img[50]), -13, 11), tab,
                                1, 15, stats=stats))

    # sub-pixel: golden = float bilinear; B = a smooth frame resampled at a fractional shift (cubic spline)
    sub = []
    for i in range(3 if quick else 10):
        n = 128 if i % 2 == 0 else 256
        src = np.asarray(img[int(rng.integers(len(img)))]) if n == 128 else \
            (lambda g: (g[..., 0] if g.ndim == 3 else g)[::2, ::2])(np.asarray(v512[int(rng.integers(len(v512)))]))
        src = ndimage.gaussian_filter(src.astype(np.float64), 1.0)
        tq = (int(rng.integers(-20, 21)), int(rng.integers(-20, 21)))           # true shift, quarter pixels
        b = np.clip(np.round(ndimage.shift(src, (tq[1] / -4.0, tq[0] / -4.0), order=3, mode='nearest')), 0, 255)
        a8 = np.clip(np.round(src), 0, 255).astype(np.uint8)
        names.append(reg_vector('regsub%d_%02d' % (n, i), a8, b.astype(np.uint8), tab, 0,
                                steps=(4, 2, 1) if n == 128 else (8, 4, 2, 1), subpel=2, stats=stats))
        got = stats[-1]['best']
        sub.append(dict(n=n, true=tq, got=got, err_q=max(abs(got[0] - tq[0]), abs(got[1] - tq[1]))))
    # B(y, x) = src(y + t / 4): G(y, x) = B(y - q / 4, ..) = A when q = t
    print('sub-pixel (true, found, error in 1/4 px):', [(s['true'], s['got'], s['err_q']) for s in sub], flush=True)
    # identity and random cost tables, a dark / flat pair, half-pel only
    ident = np.arange(256)
    a = np.asarray(img[200])
    names.append(reg_vector('reg128_ident_tab', a, shift_int(a, 2, 5), ident, 0, stats=stats))
    names.append(reg_vector('reg128_rand_tab', a, shift_int(a, -4, 1), rng.integers(0, 256, 256), 1, 5, stats=stats))
    names.append(reg_vector('reg128_flat', np.zeros((128, 128), np.uint8), np.zeros((128, 128), np.uint8), tab, 0,
                            stats=stats))
    names.append(reg_vector('reg128_half', a, shift_int(a, 1, -1), tab, 0, steps=(2, 1), subpel=1, stats=stats))
    names.append(reg_vector('reg128_one_step', a, shift_int(a, 3, 3), tab, 0, steps=(3,), stats=stats))

    # bad descriptors: nothing may be written
    names.append(bad_vector('bad_magic', [0x12345601, 128, 0, 0x010204, 8, 1032, 2056, 2072]))
    names.append(bad_vector('bad_n64', [V.MAGIC | 1, 64, 0, 0x010204, 8, 1032, 2056, 2072]))
    names.append(bad_vector('bad_steps_sum', [V.MAGIC | 1, 128, 0, 0x08080808, 8, 1032, 2056, 2072]))
    names.append(bad_vector('bad_no_steps', [V.MAGIC | 1, 128, 0, 0, 8, 1032, 2056, 2072]))
    names.append(bad_vector('bad_radius', [V.MAGIC | 1, 128, 1 | 16 << 16, 0, 8, 1032, 2056, 2072]))
    names.append(bad_vector('bad_subpel', [V.MAGIC | 1, 128, 3 << 8, 0x010204, 8, 1032, 2056, 2072]))
    names.append(bad_vector('bad_b_off', [V.MAGIC | 1, 128, 0, 0x010204, 8, 15400, 2056, 2072]))
    names.append(bad_vector('bad_out_off', [V.MAGIC | 1, 256, 1 | 31 << 16, 0, 8, 4104, 8200, 12500]))
    names.append(bad_vector('bad_op', [V.MAGIC | 3, 128, 0, 0x010204, 8, 1032, 2056, 2072]))
    return names, sub


def shift_int(g, dx, dy):
    """B(y, x) = g(y - dy, x - dx) with edge replication (the content a camera move would bring in)."""
    n = g.shape[0]
    y = np.clip(np.arange(n) - dy, 0, n - 1)
    x = np.clip(np.arange(n) - dx, 0, n - 1)
    return np.ascontiguousarray(np.asarray(g)[y][:, x], np.uint8)


def ovl_cases(quick):
    names = []
    rng = np.random.default_rng(20261015)
    import imageio as uiio
    heat = np.array(confidence._HEAT_LUT, np.uint8)
    # ---- fundus 512: logits 512 (= lw), RGB base = the sample image
    for i in (1, 2) if quick else (1, 2, 3, 4, 5):
        raw, _ = uiio.load_image(os.path.join(UI, 'samples', 'im%04d.ppm' % i))
        base = np.frombuffer(raw, np.uint8).reshape(512, 512, 3)
        q, _ = pp_logits('fundus_im%04d' % i)
        plane = board_plane(q)
        g = from_bytes_rgb(confidence.overlay_rgb(raw, plane, -3))
        names += ovl_vectors('fundus%d_overlay' % i, 512, 512, V.BASE_RGB | V.F_HAS_LOGIT | V.F_FILL, base, q,
                             thr=-3, golden=g, aiv=True)
        g = from_bytes_rgb(confidence.heat_rgb(plane))
        names += ovl_vectors('fundus%d_heat' % i, 512, 512, V.BASE_HEAT | V.F_HAS_LOGIT, None, q, lut=heat, golden=g)
        g = from_bytes_rgb(confidence.uncertain_rgb(raw, plane, -3, 3))
        names += ovl_vectors('fundus%d_unsure' % i, 512, 512, V.BASE_RGB | V.F_HAS_LOGIT | V.F_FILL | V.F_UNSURE,
                             base, q, thr=-3, band=3, golden=g)
        if i == 1:
            g = from_bytes_rgb(confidence.overlay_rgb(bytes(512 * 512 * 3), plane, -3))
            names += ovl_vectors('fundus1_overlay_nobase', 512, 512, V.BASE_NONE | V.F_HAS_LOGIT | V.F_FILL, None, q,
                                 thr=-3, golden=g, aiv=True)
            # contour + half alpha: golden scipy + formula
            m = np.asarray(q, np.int64) >= -3
            e = contour_golden(m)
            gb = ((8 * np.array([0, 160, 255]) + 8 * base.astype(np.int64) + 8) >> 4)
            gg = np.where(m[..., None], gb, base)
            gg[e] = (255, 255, 0)
            names += ovl_vectors('fundus1_contour', 512, 512, V.BASE_RGB | V.F_HAS_LOGIT | V.F_FILL | V.F_CONTOUR,
                                 base, q, thr=-3, fill=(0, 160, 255), fa=8, con=(255, 255, 0),
                                 golden=gg.astype(np.uint8))
    # ---- 128 logits on a 512 output (CT / echo tier): nearest = the board's logit image, bilinear = new
    import ct_volume
    v = ct_volume.load_ct(os.path.join(PLD, 'Task09_Spleen', 'Task09_Spleen', 'imagesTr', 'spleen_2.nii.gz'))
    u8 = ct_volume.window_u8(v['hu'])
    zs = (45, 70, 79) if not quick else (70,)
    for z in zs:
        q, mask512 = pp_logits('ct_spleen_2_z%03d_k4' % z)
        grey = np.asarray(u8[z], np.uint8)
        if grey.shape != (512, 512):
            raise SystemExit('CT slice is %s' % (grey.shape,))
        plane = board_plane(q)
        g = from_bytes_rgb(confidence.heat_rgb(plane))
        names += ovl_vectors('ct%d_heat_nearest' % z, 512, 512, V.BASE_HEAT | V.F_HAS_LOGIT, None, q, lut=heat, golden=g)
        # final mask as bits (pp_layer x4 mask), alpha fill + contour on the grey slice
        fl = V.BASE_GREY | V.F_HAS_BITS | V.F_FILL_FROM_BITS | V.F_FILL | V.F_CONTOUR
        e = contour_golden(mask512)
        gb = (6 * np.array([0, 255, 0]) + 10 * grey[..., None].astype(np.int64) + 8) >> 4
        gg = np.where(mask512[..., None], gb, np.repeat(grey[..., None], 3, 2))
        gg[e] = (255, 255, 0)
        names += ovl_vectors('ct%d_bits_contour' % z, 512, 512, fl, grey, None, mask512, fill=(0, 255, 0), fa=6,
                             con=(255, 255, 0), golden=gg.astype(np.uint8))
        # logits (nearest x4) as the mask + contour + unsure band on the grey slice
        fl = V.BASE_GREY | V.F_HAS_LOGIT | V.F_FILL | V.F_CONTOUR | V.F_UNSURE
        gs = scalar_overlay(512, 512, fl, grey, q, None, None, 1, 2, (255, 0, 0), 5, (255, 200, 0), 12,
                            (0, 255, 255)) if z == 70 else None
        names += ovl_vectors('ct%d_logit_mask' % z, 512, 512, fl, grey, q, thr=1, band=2, fa=5, ua=12,
                             con=(0, 255, 255), golden=gs)
    # ---- echo: 112 clean mask (bits) on the 112 crop, and 128 logits x4 heat
    for nm in sorted(f[:-4] for f in os.listdir(PPV) if f.startswith('echo_') and f.endswith('.bin'))[:2 if quick else 4]:
        q, clean = pp_logits(nm)
        crop = np.ascontiguousarray(q[8:120, 8:120])
        base = np.clip(crop.astype(np.int64) * 2 + 128, 0, 255).astype(np.uint8)    # a stand-in grey frame
        fl = V.BASE_GREY | V.F_HAS_BITS | V.F_FILL_FROM_BITS | V.F_FILL | V.F_CONTOUR
        e = contour_golden(clean)
        gb = (4 * np.array([255, 0, 0]) + 12 * base[..., None].astype(np.int64) + 8) >> 4
        gg = np.where(clean[..., None], gb, np.repeat(base[..., None], 3, 2))
        gg[e] = (0, 255, 0)
        names += ovl_vectors(nm + '_clean', 112, 112, fl, base, None, clean, fa=4, golden=gg.astype(np.uint8))
        g = from_bytes_rgb(confidence.heat_rgb(board_plane(q)))
        names += ovl_vectors(nm + '_heat', 512, 512, V.BASE_HEAT | V.F_HAS_LOGIT, None, q, lut=heat, golden=g)
    # ---- random small frames, every flag combination, odd sizes, nearest down to 1 source pixel
    for i in range(30 if quick else 120):
        ow = 16 * int(rng.integers(1, 9))
        oh = int(rng.integers(1, 97))
        kind = int(rng.integers(0, 4))
        bil = False                                  # kernel v2: nearest only (bilinear -> BAD, see bad_ovl_bilinear)
        if bil:
            k = int(rng.choice([2, 4]))
            if ow % (16 * k) or oh % k:
                bil = False
        if bil:
            lw, lh = ow // k, oh // k
        else:
            lw = 16 * int(rng.integers(1, ow // 16 + 1))
            lh = int(rng.integers(1, oh + 1))
        q = rng.integers(-40, 40, (lh, lw)).astype(np.int8)
        mw, mh = int(rng.integers(1, ow + 1)), int(rng.integers(1, oh + 1))
        bits = rng.random((mh, mw)) < rng.random()
        flags = kind | V.F_HAS_LOGIT | (V.F_BILINEAR if bil else 0)
        if rng.random() < 0.5:
            flags |= V.F_HAS_BITS | (V.F_FILL_FROM_BITS if rng.random() < 0.7 else 0)
        for f, p in ((V.F_FILL, 0.7), (V.F_UNSURE, 0.4), (V.F_CONTOUR, 0.6)):
            if rng.random() < p:
                flags |= f
        base = rng.integers(0, 256, (oh, ow, 3) if kind == V.BASE_RGB else (oh, ow)).astype(np.uint8)
        lut = rng.integers(0, 256, (256, 3)).astype(np.uint8)
        thr, band = int(rng.integers(-20, 20)), int(rng.integers(0, 8))
        cols = [tuple(int(c) for c in rng.integers(0, 256, 3)) for _ in range(3)]
        fa, ua = int(rng.integers(0, 17)), int(rng.integers(0, 17))
        bits_in = bits if flags & V.F_HAS_BITS else None
        gs = scalar_overlay(ow, oh, flags, base, q, bits_in, lut, thr, band, cols[0], fa, cols[1], ua, cols[2])
        nb = int(rng.integers(1, 4))
        cuts = sorted(set([0, oh] + [int(c) for c in rng.integers(1, max(2, oh), nb - 1)])) if oh > 1 else [0, oh]
        bands = [(cuts[j], cuts[j + 1]) for j in range(len(cuts) - 1) if cuts[j] < cuts[j + 1]]
        names += ovl_vectors('rnd%03d' % i, ow, oh, flags, base, q, bits_in, lut, thr, band, cols[0], fa, cols[1], ua,
                             cols[2], golden=gs, bands=bands)
    # ---- bad OVL descriptors
    names.append(bad_vector('bad_ovl_ow', [V.MAGIC | 2, 120 | 64 << 16, 0 | 64 << 16, V.BASE_NONE]))
    names.append(bad_vector('bad_ovl_band', [V.MAGIC | 2, 128 | 64 << 16, 10 | 10 << 16, V.BASE_NONE]))
    names.append(bad_vector('bad_ovl_need_logit', [V.MAGIC | 2, 128 | 64 << 16, 0 | 64 << 16, V.BASE_HEAT, 0, 0, 0,
                                                   0, 0, 0, 0, 1000, 8]))
    names.append(bad_vector('bad_ovl_rows', [V.MAGIC | 2, 128 | 128 << 16, 0 | 64 << 16,
                                             V.BASE_NONE | V.F_HAS_LOGIT | V.F_FILL, 128 | 128 << 16, 100, 0 | 60 << 16,
                                             0, 0, 0, 0, 2000]))
    names.append(bad_vector('bad_ovl_bilinear', [V.MAGIC | 2, 128 | 128 << 16, 0 | 128 << 16,
                                                 V.BASE_NONE | V.F_HAS_LOGIT | V.F_BILINEAR | V.F_FILL, 64 | 64 << 16,
                                                 100, 0 | 64 << 16, 0, 0, 0, 0, 2000]))
    names.append(bad_vector('bad_ovl_bil_k3', [V.MAGIC | 2, 96 | 96 << 16, 0 | 96 << 16,
                                               V.BASE_NONE | V.F_HAS_LOGIT | V.F_BILINEAR | V.F_FILL, 32 | 32 << 16,
                                               100, 0 | 32 << 16, 0, 0, 0, 0, 2000]))
    names.append(bad_vector('bad_ovl_out', [V.MAGIC | 2, 512 | 512 << 16, 0 | 512 << 16, V.BASE_NONE, 0, 0, 0, 0, 0,
                                            0, 0, 100]))
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--only', choices=('reg', 'ovl'))
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        os.remove(os.path.join(OUT, f))
    tab = make_absdiff_table()
    stats = []
    names = []
    sub = []
    if args.only != 'ovl':
        n, sub = reg_cases(tab, args.quick, stats)
        names += n
        print('reg vectors', len(n), flush=True)
    if args.only != 'reg':
        n = ovl_cases(args.quick)
        names += n
        print('ovl vectors', len(n), flush=True)
    with open(os.path.join(OUT, 'cases.txt'), 'w') as f:
        f.write('\n'.join(names) + '\n')
    cos = [x for x in names if x in ('reg128_tss_000', 'reg128_full_000', 'regsub128_00', 'reg256_full_r4',
                                     'fundus1_contour_b0', 'ct70_heat_nearest_b0', 'ct70_bits_contour_b0',
                                     'rnd000', 'rnd001_b0', 'bad_magic', 'bad_ovl_rows')]
    with open(os.path.join(OUT, 'cases_cosim.txt'), 'w') as f:
        f.write('\n'.join(cos) + '\n')
    json.dump(dict(reg=stats, subpel=sub), open(os.path.join(OUT, 'stats.json'), 'w'), indent=1, default=int)
    print('cases', len(names), 'cosim', len(cos))
    print('GEN_VIS_VECTORS_PASS')


if __name__ == '__main__':
    main()
