"""Export the F5 |R - G| weight set and prove it bit-exact on the HLS bit-level simulator.

  * golden 512 tiled input (16 candidate shifts): every node vs the integer reference;
  * 40 more inputs: 30 real consecutive validation frame pairs and 10 random pairs at 128,
    plus 3 tiled 512 inputs, final logits vs the integer reference.

    python export_verify_reg.py [--tag reg_absdiff]
"""
import argparse
import hashlib
import json
import sys

import numpy as np
import torch

from common import CACHE, RESULTS, ROOT
from export_verify_echo import COLS, SizedRunner, build_table, pack_activations
from int8_reference import run_integer
from make_reg_model import tile16


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', default='reg_absdiff')
    args = ap.parse_args()
    qdir = RESULTS / 'quant' / args.tag
    deploy = ROOT / 'deploy' / args.tag
    (deploy / 'params').mkdir(parents=True, exist_ok=True)
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    npz = np.load(qdir / 'parameters.npz')
    rows, shapes = build_table(plan, npz, deploy / 'params')
    (deploy / 'network.txt').write_text(''.join(' '.join(str(r[c]) for c in COLS) + '\n' for r in rows), encoding='ascii')
    arrays = {k: npz[k] for k in npz.files}
    runner = SizedRunner(deploy)
    report = dict(tag=args.tag, nodes=len(rows))

    def ref(rgb):
        with torch.inference_mode():
            q = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).unsqueeze(0)
            return run_integer(plan, arrays, q)[0][0, 0].numpy()

    gdir = qdir / 'golden' / 'tile16_val100'
    grgb = np.load(gdir / 'input_rgb.npy')
    diff = {}

    def check(node, tensor):
        r = np.load(gdir / f'{node.name}.npy')[0].transpose(1, 2, 0)
        diff[node.name] = int((r.astype(np.int64) != tensor.astype(np.int64)).sum())

    out = runner.run(grgb, check)
    report['golden_512'] = dict(nodes_checked=len(diff), nodes_with_diff=[n for n, v in diff.items() if v])
    vdir = deploy / 'images' / gdir.name
    (vdir / 'golden').mkdir(parents=True, exist_ok=True)
    pack_activations(grgb).astype('<i2').tofile(vdir / 'input.bin')
    pack_activations(out[:, :, None]).astype('<i2').tofile(vdir / 'expected_final.bin')
    for r in rows:
        t = np.load(gdir / f"{r['name']}.npy")[0].transpose(1, 2, 0)
        pack_activations(t).astype('<i2').tofile(vdir / 'golden' / f"{r['name']}.bin")
    print('golden', report['golden_512'], flush=True)

    vimg = np.load(CACHE / 'seq_val_img128.npy', mmap_mode='r')
    rng = np.random.default_rng(1)
    inputs = [np.stack([vimg[s], vimg[s + 1], np.zeros((128, 128), np.uint8)], -1) for s in range(5, 1800, 60)]
    inputs += [np.stack([rng.integers(0, 256, (128, 128)), rng.integers(0, 256, (128, 128)),
                         np.zeros((128, 128))], -1).astype(np.uint8) for _ in range(10)]
    for s in (300, 900, 1500):
        a = np.asarray(vimg[s])
        inputs.append(tile16(a, [np.roll(np.asarray(vimg[s + 1]), (dy, dx), (0, 1)) for dy in (-2, 0, 1, 3) for dx in (-3, -1, 0, 2)]))
    bad = [i for i, rgb in enumerate(inputs) if not np.array_equal(runner.run(rgb), ref(rgb))]
    report['inputs'] = dict(checked=len(inputs), mismatching=bad)
    runner.close()
    report['pass'] = not report['golden_512']['nodes_with_diff'] and not bad
    (deploy / 'manifest.json').write_text(json.dumps(dict(
        quant_plan_sha256=sha256(qdir / 'quant_plan.json'), parameters_sha256=sha256(qdir / 'parameters.npz'),
        deploy_resolution=512, output='logit ~ |R - G| / 2 (scale 1/127 of |R-G|/255)',
        input_encoding='R frame a, G frame b (shifted candidate), B unused; 4 x 4 tiles of 128 = 16 candidates per 512 run',
        nodes=rows, output_shapes={k: list(v) for k, v in shapes.items()}), indent=1), encoding='utf-8')
    (deploy / 'verify_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('PASS' if report['pass'] else 'FAIL', json.dumps(report))
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
