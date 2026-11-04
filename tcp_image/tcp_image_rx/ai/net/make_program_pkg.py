# -*- coding: utf-8 -*-
"""Program packages (*.upk): one U-Net parameter set the board loads at run time.

The board keeps its built-in models 0..16 (src/unet_models.c). A package adds a
parameter set for the same 30-node structure without rebuilding the server:
copy it into /root/tcp_image_rx/programs/ and send SIGHUP (boot/install_program.py).
The server registers it under the id written here, but only after its self-test
on the PL reproduces the expected logits bit for bit (src/progpkg.c).

The expected logits come from the HLS bit-exact NumPy reference of the two PL
kernels (medical_fpga/embedded/pynq, the same reference the export_verify_*.py
scripts use), run on the package's own tables at the self-test size.

Layout (little endian; mirrored in src/progpkg.h):
  header 64 B : "UPK1", u16 version 1, u16 header bytes 64, u8 id, u8 default shift,
                i8 default thr, u8 roi_ok, u8 nodes 30, u8 self-test shift, u16 0,
                u32 params bytes, u32 meta bytes, u32 body bytes, u32 CRC-32(body), char name[32]
  body        : 30 x 32 B nodes, u32 param_off[30], u32 param_len[30], params,
                meta (UTF-8 JSON), self-test RGB (s*s*3), expected int8 logits (s*s), s = 512 >> shift

    python make_program_pkg.py --deploy E:/PLD/ct/deploy/z3_r128 --name ct_new --id 40 --shift 2 --thr 1 \
        --meta modality=CT --meta input="R/G/B = slices z-5mm / z / z+5mm" --out ct_new.upk
    python make_program_pkg.py --builtin skin --id 33 --out skin_pkg.upk     # repackage a built-in model
    python make_program_pkg.py --inspect ct_new.upk
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import zlib
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_unet_models as gum   # noqa: E402

sys.path.insert(0, os.path.join(gum.PLD, 'medical_fpga', 'embedded', 'pynq'))
from sim_device import SimDevice            # noqa: E402
from unet_fpga import UNetAccelerator       # noqa: E402

MAGIC = b'UPK1'
VERSION = 1
HEADER = struct.Struct('<4sHHBBbBBBHIIII32s')     # 64 bytes
NODE = struct.Struct('<BbbBBBBBB3xhhiiHHHH')      # 32 bytes
FIRST_ID = len(gum.MODELS)                       # 17: ids below are the built-in models
MAX_ID = 254
NODES = 30
assert HEADER.size == 64 and NODE.size == 32


def reference_logits(deploy, rgb):
    """HLS bit-exact reference at the size of rgb (network.txt scaled like the board's strip scheduler)."""
    h, w = rgb.shape[:2]
    tmp = tempfile.mkdtemp(prefix='upk_sim_')
    try:
        shutil.copytree(os.path.join(deploy, 'params'), os.path.join(tmp, 'params'))
        lines = []
        for line in open(os.path.join(deploy, 'network.txt')):
            f = line.split()
            if not f:
                continue
            f[7], f[8] = str(int(f[7]) * h // 512), str(int(f[8]) * w // 512)
            lines.append(' '.join(f))
        with open(os.path.join(tmp, 'network.txt'), 'w') as fo:
            fo.write('\n'.join(lines) + '\n')
        return UNetAccelerator(SimDevice(), tmp).run(np.ascontiguousarray(rgb))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def selftest_input(name, side):
    """Deterministic test image: smooth blobs + noise, so every layer sees non-trivial values."""
    rng = np.random.RandomState(zlib.crc32(name.encode()) & 0x7FFFFFFF)
    y, x = np.mgrid[0:side, 0:side] / float(side)
    img = np.zeros((side, side, 3))
    for c in range(3):
        for _ in range(4):
            cx, cy, r = rng.uniform(0.1, 0.9), rng.uniform(0.1, 0.9), rng.uniform(0.05, 0.3)
            img[:, :, c] += rng.uniform(60, 200) * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * r * r))
    img += rng.normal(0, 12, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def build(deploy, name, pid, shift, thr, roi_ok, st_shift, meta, image=None):
    if not re.fullmatch(r'[a-z0-9_]{1,31}', name):
        raise SystemExit('name must be 1..31 chars of [a-z0-9_]')
    if not FIRST_ID <= pid <= MAX_ID:
        raise SystemExit('id must be %d..%d (0..%d are the built-in models)' % (FIRST_ID, MAX_ID, FIRST_ID - 1))
    if name in [m[0] for m in gum.MODELS]:
        raise SystemExit('name %r is a built-in model' % name)
    rows, off, ln, blob = gum.load(deploy)
    ref = gum.load(gum.FUNDUS_VEC)
    assert len(rows) == NODES
    diff = [j for j, (a, b) in enumerate(zip(rows, ref[0])) if any(a[k] != b[k] for k in gum.STRUCT)]
    if diff or ln != ref[2]:
        raise SystemExit('%s: structure differs from the built-in models (nodes %s)' % (deploy, diff))

    side = 512 >> st_shift
    rgb = selftest_input(name, side) if image is None else image
    assert rgb.shape == (side, side, 3) and rgb.dtype == np.uint8
    expected = reference_logits(deploy, rgb)

    meta = dict(meta)
    meta.update(name=name, id=pid, default_resolution=512 >> shift, default_threshold=thr,
                source=os.path.abspath(deploy).replace('\\', '/'),
                params_sha256=hashlib.sha256(blob).hexdigest(), built_utc=datetime.now(timezone.utc).isoformat(),
                selftest_resolution=side, intended_use=meta.get('intended_use', '仅用于工程教学与算法演示，不作为临床诊断依据'))
    mb = json.dumps(meta, ensure_ascii=False, sort_keys=True).encode('utf-8')

    body = b''.join(NODE.pack(*(r[k] for k in gum.C_ORDER)) for r in rows)
    body += struct.pack('<%dI' % NODES, *off) + struct.pack('<%dI' % NODES, *ln)
    body += blob + mb + rgb.tobytes() + expected.astype(np.int8).tobytes()
    head = HEADER.pack(MAGIC, VERSION, HEADER.size, pid, shift, thr, roi_ok, NODES, st_shift, 0,
                       len(blob), len(mb), len(body), zlib.crc32(body) & 0xFFFFFFFF, name.encode())
    return head + body, meta


def parse(data):
    """Inverse of build(): header fields, meta, tables, self-test vectors (used by --inspect and the tests)."""
    (magic, ver, hb, pid, shift, thr, roi, nodes, sts, _, pb, mbytes, bb, crc, name) = HEADER.unpack_from(data)
    assert magic == MAGIC and ver == VERSION and hb == HEADER.size and bb == len(data) - hb
    body = data[hb:]
    assert zlib.crc32(body) & 0xFFFFFFFF == crc, 'CRC-32 mismatch'
    p = nodes * NODE.size
    rows = [dict(zip(gum.C_ORDER, NODE.unpack_from(body, i * NODE.size))) for i in range(nodes)]
    off = list(struct.unpack_from('<%dI' % nodes, body, p))
    ln = list(struct.unpack_from('<%dI' % nodes, body, p + 4 * nodes))
    p += 8 * nodes
    blob = body[p:p + pb]
    p += pb
    meta = json.loads(body[p:p + mbytes].decode('utf-8'))
    p += mbytes
    side = 512 >> sts
    rgb = np.frombuffer(body, np.uint8, side * side * 3, p).reshape(side, side, 3)
    exp = np.frombuffer(body, np.int8, side * side, p + side * side * 3).reshape(side, side)
    return dict(id=pid, name=name.rstrip(b'\0').decode(), shift=shift, thr=thr, roi_ok=roi, selftest_shift=sts,
                crc32='%08X' % crc, meta=meta, rows=rows, off=off, len=ln, params=blob, rgb=rgb, expected=exp)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--deploy', help='deploy dir with network.txt + params/*.bin')
    ap.add_argument('--builtin', help='repackage a built-in model by name (gen_unet_models.MODELS)')
    ap.add_argument('--name')
    ap.add_argument('--id', type=int)
    ap.add_argument('--shift', type=int, help='default working size 512 >> shift')
    ap.add_argument('--thr', type=int, help='default threshold: logit >= thr marks the target')
    ap.add_argument('--roi', type=int, default=None, help='1 = full-resolution model, ROI meaningful')
    ap.add_argument('--selftest-shift', type=int, default=None, help='self-test size 512 >> s (default max(shift, 2))')
    ap.add_argument('--image', help='self-test image (.npy, uint8 s x s x 3); default: generated')
    ap.add_argument('--meta', action='append', default=[], help='key=value stored in the package metadata')
    ap.add_argument('--out')
    ap.add_argument('--inspect', help='print a package and check it')
    a = ap.parse_args()

    if a.inspect:
        with open(a.inspect, 'rb') as f:
            p = parse(f.read())
        print(json.dumps({k: p[k] for k in ('id', 'name', 'shift', 'thr', 'roi_ok', 'selftest_shift', 'crc32')}))
        print(json.dumps(p['meta'], ensure_ascii=False, indent=2))
        return 0

    if a.builtin:
        hit = [m for m in gum.MODELS if m[0] == a.builtin]
        if not hit:
            raise SystemExit('unknown built-in model %r' % a.builtin)
        tag, vec, shift, thr, roi = hit[0]
        deploy = vec or gum.FUNDUS_VEC
        name = a.name or tag + '_pkg'
        shift = shift if a.shift is None else a.shift
        thr = thr if a.thr is None else a.thr
        roi = roi if a.roi is None else a.roi
        meta = {'repackaged_builtin': tag, 'builtin_index': [m[0] for m in gum.MODELS].index(tag)}
    else:
        if not (a.deploy and a.name and a.shift is not None and a.thr is not None):
            raise SystemExit('--deploy, --name, --shift and --thr are required (or use --builtin)')
        deploy, name, shift, thr, roi, meta = a.deploy, a.name, a.shift, a.thr, a.roi or 0, {}
    if a.id is None or not a.out:
        raise SystemExit('--id and --out are required')
    for kv in a.meta:
        k, _, v = kv.partition('=')
        meta[k] = v
    sts = max(shift, 2) if a.selftest_shift is None else a.selftest_shift
    image = np.load(a.image) if a.image else None
    data, meta = build(deploy, name, a.id, shift, thr, roi, sts, meta, image)
    assert parse(data)['name'] == name
    with open(a.out, 'wb') as f:
        f.write(data)
    print('[upk] %s: id %d %s, %d bytes, crc %08X, self-test %dx%d' % (
        a.out, a.id, name, len(data), zlib.crc32(data[64:]) & 0xFFFFFFFF, 512 >> sts, 512 >> sts))
    return 0


if __name__ == '__main__':
    sys.exit(main())
