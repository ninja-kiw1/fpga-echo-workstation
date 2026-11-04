"""Collect everything the board needs into one folder (run on the PC):

    & 'C:/Users/21888/anaconda3/envs/srtp-vision/python.exe' package_for_board.py

Output (default ./board_package, about 70 MB), copy it to the board as-is:
  unet_system.bit / .hwh        from medical_fpga/vivado/output
  network.txt, params/          from deployment/unet_network/vectors
  images/<img>/input_rgb.bin    uint8 512x512x3 (the exact lwnet integer input)
  images/<img>/expected_final.bin  int8 512x512 (lwnet output_quantized)
  golden/<golden image>/<node>.npy  every node, true channels and value type
  unet_fpga.py, run_board_test.py, smoke_test.py, manifest.json (with SHA-256 of every file)
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MEDICAL = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
from unet_fpga import DEFAULT_THRESHOLD, load_network  # noqa: E402

NETWORK = os.path.join(MEDICAL, 'deployment', 'unet_network', 'vectors')
VIVADO = os.path.join(MEDICAL, 'vivado', 'output')


def packed(path, shape):
    return np.fromfile(path, dtype='<i2').reshape(shape)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--output', default=os.path.join(HERE, 'board_package'))
    parser.add_argument('--golden-image', default='im0002')
    args = parser.parse_args()
    out = args.output
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, 'params'))

    for name in ('unet_system.bit', 'unet_system.hwh'):
        shutil.copy2(os.path.join(VIVADO, name), out)
    for name in ('unet_fpga.py', 'run_board_test.py', 'smoke_test.py'):
        shutil.copy2(os.path.join(HERE, name), out)
    shutil.copy2(os.path.join(NETWORK, 'network.txt'), out)
    nodes = load_network(os.path.join(out, 'network.txt'))
    for n in nodes:
        if n.kind == 'CONV':
            shutil.copy2(os.path.join(NETWORK, 'params', n.name + '.bin'), os.path.join(out, 'params'))

    with open(os.path.join(NETWORK, 'images.txt')) as f:
        images = f.read().split()
    first, last = nodes[0], nodes[-1]
    for name in images:
        src, dst = os.path.join(NETWORK, 'images', name), os.path.join(out, 'images', name)
        os.makedirs(dst)
        x = packed(os.path.join(src, 'input.bin'), first.shape)
        assert not x[:, :, first.ca:].any() and x.min() >= 0 and x.max() <= 255
        x[:, :, :first.ca].astype(np.uint8).tofile(os.path.join(dst, 'input_rgb.bin'))
        y = packed(os.path.join(src, 'expected_final.bin'), last.shape)
        assert not y[:, :, 1:].any() and -128 <= y.min() and y.max() <= 127
        y[:, :, 0].astype(np.int8).tofile(os.path.join(dst, 'expected_final.bin'))

    gdst = os.path.join(out, 'golden', args.golden_image)
    os.makedirs(gdst)
    for n in nodes:
        t = packed(os.path.join(NETWORK, 'images', args.golden_image, 'golden', n.name + '.bin'), n.shape)
        valid = t[:, :, :n.out_c]
        info = np.iinfo(n.dtype)
        assert not t[:, :, n.out_c:].any() and info.min <= valid.min() and valid.max() <= info.max, n.name
        np.save(os.path.join(gdst, n.name + '.npy'), valid.astype(n.dtype))

    hashes = {}
    for root, _, files in os.walk(out):
        for fname in sorted(files):
            path = os.path.join(root, fname)
            with open(path, 'rb') as f:
                hashes[os.path.relpath(path, out).replace(os.sep, '/')] = hashlib.sha256(f.read()).hexdigest()
    manifest = dict(bitstream='unet_system.bit', threshold_integer_ge=DEFAULT_THRESHOLD,
                    golden_image=args.golden_image, images=images, files_sha256=hashes,
                    note='Engineering demo only; not for clinical diagnosis.')
    with open(os.path.join(out, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2)
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(out) for f in fs)
    print('%s: %d images, %d nodes, %.1f MB' % (out, len(images), len(nodes), size / 1e6))


if __name__ == '__main__':
    main()
