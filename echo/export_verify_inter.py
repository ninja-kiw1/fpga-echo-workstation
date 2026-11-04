"""Export the quantized F2 model and prove it bit-exact on the inputs the click loop produced.

Inputs here are real 3-channel encodings (frame, prior, clicks), recorded by
quantize_inter.py while it ran the standard click protocol with the integer
model on the 200 official validation ED / ES frames (k = 0..5, from the F1
result and from empty: 2400 inputs). Every one is re-run through the HLS
bit-level simulator on the exported table and must give the same mask; the
golden input is checked node by node, and once on the full 512 table.

    python export_verify_inter.py --tag inter2_r128
"""
import argparse
import hashlib
import json
import sys
import time

import numpy as np
import torch

from common import RESULTS, ROOT
from export_verify_echo import COLS, SizedRunner, build_table, pack_activations
from int8_reference import run_integer


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
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

    gdir = qdir / 'golden' / 'val000_from_f1_k2'
    grgb = np.load(gdir / 'input_rgb.npy')
    diff = {}

    def check(node, tensor):
        ref = np.load(gdir / f'{node.name}.npy')[0].transpose(1, 2, 0)
        diff[node.name] = int((ref.astype(np.int64) != tensor.astype(np.int64)).sum())

    out = runner.run(grgb, check)
    report['golden'] = dict(nodes_checked=len(diff), nodes_with_diff=[n for n, v in diff.items() if v])
    vdir = deploy / 'images' / gdir.name
    (vdir / 'golden').mkdir(parents=True, exist_ok=True)
    pack_activations(grgb).astype('<i2').tofile(vdir / 'input.bin')
    pack_activations(out[:, :, None]).astype('<i2').tofile(vdir / 'expected_final.bin')
    for r in rows:
        t = np.load(gdir / f"{r['name']}.npy")[0].transpose(1, 2, 0)
        pack_activations(t).astype('<i2').tofile(vdir / 'golden' / f"{r['name']}.bin")
    print('golden', report['golden'], flush=True)

    t0 = time.time()
    for mode in ('from_f1', 'from_empty'):
        rgb = np.load(qdir / 'click_inputs' / f'{mode}_rgb.npy')          # (k, frames, 128, 128, 3)
        masks = np.load(qdir / 'click_inputs' / f'{mode}_mask.npy')       # (k, frames, 128, 128)
        bad = []
        for k in range(rgb.shape[0]):
            for i in range(rgb.shape[1]):
                if not np.array_equal(runner.run(rgb[k, i]) >= 1, masks[k, i]):
                    bad.append((k, i))
            print(f'{mode} k={k}: {rgb.shape[1]} inputs, mismatches so far {len(bad)} ({time.time() - t0:.0f}s)', flush=True)
        report[mode] = dict(inputs=int(rgb.shape[0] * rgb.shape[1]), mismatching=bad[:20])

    full = np.ascontiguousarray(np.repeat(np.repeat(grgb, 4, 0), 4, 1))
    with torch.inference_mode():
        ref512 = run_integer(plan, arrays, torch.from_numpy(full).permute(2, 0, 1).unsqueeze(0))[0][0, 0].numpy()
    report['full512_mismatching_pixels'] = int((runner.run(full) != ref512).sum())
    runner.close()
    report['pass'] = (not report['golden']['nodes_with_diff'] and not report['from_f1']['mismatching']
                      and not report['from_empty']['mismatching'] and report['full512_mismatching_pixels'] == 0)
    (deploy / 'manifest.json').write_text(json.dumps(dict(
        quant_plan_sha256=sha256(qdir / 'quant_plan.json'), parameters_sha256=sha256(qdir / 'parameters.npz'),
        deploy_resolution=128, output_integer_threshold_ge=1,
        input_encoding='R grey frame, G prior mask 0/255, B 128 + 127*pos - 128*neg click cones (R=40 px @512)',
        nodes=rows, output_shapes={k: list(v) for k, v in shapes.items()}), indent=1), encoding='utf-8')
    (deploy / 'verify_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('PASS' if report['pass'] else 'FAIL', json.dumps(report)[:500])
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
