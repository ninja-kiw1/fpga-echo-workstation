"""Export the mixed-precision U-Net as a layer table for the two HLS kernels.

Run with the lwnet environment so the input resize is the exact PIL code that
produced the lwnet reference outputs:

    & 'E:/PLD/lwnet/.venv/Scripts/python.exe' export_network.py

Writes (default: ./vectors):
  network.txt          one node per line, in execution order (format below)
  params/<node>.bin    packed conv parameters, same layout as unet_conv
  images.txt           image names
  images/<img>/input.bin           packed int16 NHWC RGB input (3 -> 8 channels)
  images/<img>/expected_final.bin  packed lwnet output_quantized (1 -> 8 channels)
  images/<img>/golden/<node>.bin   every node, only for the image with golden tensors

network.txt columns:
  name kind in0 in1 mode ca cb h w relu shift qmin qmax m0 m1
  kind INPUT | CONV | ELT; in1 is '-' when unused; h, w are the input size.
  CONV: mode 0 3x3 / 1 1x1 / 2 transposed 2x2, ca=cin, cb=cout, m0=m1=0.
  ELT:  mode 0 Add / 1 Concat / 2 MaxPool, m0/m1 are the branch multipliers.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / 'unet_conv'))
sys.path.insert(0, str(PLD / 'lwnet'))
from generate_vectors import (DEFAULT_QUANT, MODE_CONV1, MODE_CONV3, MODE_TCONV2,  # noqa: E402
                              pack_activations, pack_params, taps_from_torch)
from quantize_stage1 import read_input  # noqa: E402  (same PIL resize as lwnet)

ELT_MODES = {'Add': 0, 'Concat': 1, 'MaxPool2d': 2}
IMAGES = PLD / 'medical_fpga' / 'data' / 'stare' / 'png' / 'images'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_table(plan, arrays, params_dir):
    shapes, rows = {}, []
    for s in plan:
        name, op = s['name'], s['op']
        ins = s['inputs'] + ['-'] * (2 - len(s['inputs']))
        row = dict(name=name, in0=ins[0], in1=ins[1], relu=0, shift=0, qmin=s['qmin'], qmax=s['qmax'], m0=0, m1=0)
        if op == 'Input':
            shapes[name] = (512, 512, 3)
            row.update(kind='INPUT', mode=0, ca=3, cb=0, h=512, w=512)
        elif op in ('ConvAffine', 'Conv2d', 'ConvTranspose2d'):
            h, w, cin = shapes[ins[0]]
            mode = MODE_TCONV2 if op == 'ConvTranspose2d' else (
                MODE_CONV3 if s['kernel'] == [3, 3] else MODE_CONV1)
            weight = arrays[name + '__weight']
            cout = weight.shape[1] if mode == MODE_TCONV2 else weight.shape[0]
            mult = arrays[name + '__multiplier']
            offset = arrays[name + '__offset'] if name + '__offset' in arrays.files else np.zeros(mult.shape, np.int64)
            pack_params(taps_from_torch(weight, mode), arrays[name + '__bias'], mult, offset) \
                .tofile(params_dir / f'{name}.bin')
            scale = 2 if mode == MODE_TCONV2 else 1
            shapes[name] = (h * scale, w * scale, cout)
            row.update(kind='CONV', mode=mode, ca=cin, cb=cout, h=h, w=w,
                       relu=int(bool(s.get('relu_before_affine', False))), shift=s['shift'])
        else:
            mode = ELT_MODES[op]
            h, w, ca = shapes[ins[0]]
            cb = shapes[ins[1]][2] if mode != 2 else 0
            if mode == 2:
                shapes[name] = (h // 2, w // 2, ca)
            else:
                m0, m1 = (int(v) for v in arrays[name + '__multiplier'])
                row.update(m0=m0, m1=m1, shift=s['shift'])
                shapes[name] = (h, w, ca + cb if mode == 1 else ca)
            row.update(kind='ELT', mode=mode, ca=ca, cb=cb, h=h, w=w)
        rows.append(row)
    return rows, shapes


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--quant-dir', type=Path, default=DEFAULT_QUANT)
    parser.add_argument('--output', type=Path, default=HERE / 'vectors')
    parser.add_argument('--images', nargs='*', help='image names (default: all STARE images)')
    args = parser.parse_args()
    out = args.output
    (out / 'params').mkdir(parents=True, exist_ok=True)
    plan = json.loads((args.quant_dir / 'quant_plan.json').read_text(encoding='utf-8'))
    arrays = np.load(args.quant_dir / 'parameters.npz')
    rows, shapes = build_table(plan, arrays, out / 'params')
    cols = ('name', 'kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'h', 'w', 'relu', 'shift', 'qmin', 'qmax', 'm0', 'm1')
    (out / 'network.txt').write_text(''.join(' '.join(str(r[c]) for c in cols) + '\n' for r in rows), encoding='ascii')

    names = args.images or sorted(p.stem for p in IMAGES.glob('*.png'))
    golden_images = []
    for name in names:
        d = out / 'images' / name
        d.mkdir(parents=True, exist_ok=True)
        _, q, _ = read_input(IMAGES / f'{name}.png')
        x = q[0].numpy().transpose(1, 2, 0)
        pack_activations(x).astype('<i2').tofile(d / 'input.bin')
        final = np.load(args.quant_dir / name / 'output_quantized.npy')
        pack_activations(final[:, :, None]).astype('<i2').tofile(d / 'expected_final.bin')
        golden = args.quant_dir / name / 'golden_tensors'
        if golden.is_dir():
            if not np.array_equal(np.load(golden / 'x.npy')[0].transpose(1, 2, 0), x):
                raise AssertionError(f'{name}: exported input differs from golden x.npy')
            (d / 'golden').mkdir(exist_ok=True)
            for r in rows:
                t = np.load(golden / f"{r['name']}.npy")[0].transpose(1, 2, 0)
                assert t.shape == shapes[r['name']], r['name']
                pack_activations(t).astype('<i2').tofile(d / 'golden' / f"{r['name']}.bin")
            golden_images.append(name)
    (out / 'images.txt').write_text(''.join(n + '\n' for n in names), encoding='ascii')
    (out / 'manifest.json').write_text(json.dumps({
        'quant_dir': str(args.quant_dir),
        'quant_plan_sha256': sha256(args.quant_dir / 'quant_plan.json'),
        'parameters_sha256': sha256(args.quant_dir / 'parameters.npz'),
        'images': names, 'images_with_golden_tensors': golden_images,
        'nodes': rows, 'output_shapes': {k: list(v) for k, v in shapes.items()},
    }, indent=2), encoding='utf-8')
    print(f'{len(rows)} nodes, {len(names)} images, per-node golden for {golden_images}')


if __name__ == '__main__':
    main()
