"""Export the quantized F3 propagation model and prove it bit-exact on the inputs the propagation loop produced.

Inputs are the real 3-channel encodings recorded by quantize_seq.py while the integer
model propagated over the 100 official validation half sequences (forward ED -> ES and
backward ES -> ED). Every one is re-run through the HLS bit-level simulator on the
exported table and must give the same mask; the golden input is checked node by node,
and once on the full 512 table.

    python export_verify_seq.py --tag seq_prev
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

ENCODING = {'prev': 'R grey frame t, G mask of the previous step 0/255, B grey frame of the previous step',
            'none': 'R grey frame t, G mask of the previous step 0/255, B 128'}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()
    qdir = RESULTS / 'quant' / args.tag
    deploy = ROOT / 'deploy' / args.tag
    (deploy / 'params').mkdir(parents=True, exist_ok=True)
    summ = json.loads((qdir / 'summary.json').read_text(encoding='utf-8'))
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    npz = np.load(qdir / 'parameters.npz')
    rows, shapes = build_table(plan, npz, deploy / 'params')
    (deploy / 'network.txt').write_text(''.join(' '.join(str(r[c]) for c in COLS) + '\n' for r in rows), encoding='ascii')
    arrays = {k: npz[k] for k in npz.files}
    runner = SizedRunner(deploy)
    report = dict(tag=args.tag, nodes=len(rows))

    gdir = qdir / 'golden' / 'val_seq000_fwd_step2'
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
    for d in ('fwd', 'bwd'):
        rgb = np.load(qdir / 'seq_inputs' / f'{d}_rgb.npy')
        masks = np.load(qdir / 'seq_inputs' / f'{d}_mask.npy')
        bad = []
        for i in range(len(rgb)):
            if not np.array_equal(runner.run(rgb[i]) >= 1, masks[i]):
                bad.append(i)
            if (i + 1) % 250 == 0 or i + 1 == len(rgb):
                print(f'{d}: {i + 1}/{len(rgb)} inputs, mismatches so far {len(bad)} ({time.time() - t0:.0f}s)', flush=True)
        report[d] = dict(inputs=int(len(rgb)), mismatching=bad[:20])

    full = np.ascontiguousarray(np.repeat(np.repeat(grgb, 4, 0), 4, 1))
    with torch.inference_mode():
        ref512 = run_integer(plan, arrays, torch.from_numpy(full).permute(2, 0, 1).unsqueeze(0))[0][0, 0].numpy()
    report['full512_mismatching_pixels'] = int((runner.run(full) != ref512).sum())
    runner.close()
    report['pass'] = (not report['golden']['nodes_with_diff'] and not report['fwd']['mismatching']
                      and not report['bwd']['mismatching'] and report['full512_mismatching_pixels'] == 0)
    (deploy / 'manifest.json').write_text(json.dumps(dict(
        quant_plan_sha256=sha256(qdir / 'quant_plan.json'), parameters_sha256=sha256(qdir / 'parameters.npz'),
        deploy_resolution=128, output_integer_threshold_ge=1, input_encoding=ENCODING[summ['encoding']],
        nodes=rows, output_shapes={k: list(v) for k, v in shapes.items()}), indent=1), encoding='utf-8')
    (deploy / 'verify_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('PASS' if report['pass'] else 'FAIL', json.dumps(report)[:500])
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
