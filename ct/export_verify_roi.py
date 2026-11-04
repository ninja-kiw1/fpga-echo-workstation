"""⑤ stage 2: export the quantized ROI fine model as the FPGA layer table and prove it bit-exact (as export_verify_ct.py):
  1. golden window slice, all 30 nodes;
  2. final output of every fine payload of the 7 validation cases (windows from the model-14 coarse outputs);
  3. one payload on the full 512 x 512 table.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I export_verify_roi.py --tag roi_prior
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from export_verify_ct import COLS, SizedRunner, build_table, pack_activations, run_integer, sha256   # noqa: E402
import numpy as np          # noqa: E402
import torch                # noqa: E402

from common import RESULTS, load_split   # noqa: E402
from quantize_roi import stage_inputs   # noqa: E402
from train_ct import load_set   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()
    qdir = RESULTS / 'quant' / args.tag
    deploy = ROOT / 'deploy' / args.tag
    (deploy / 'params').mkdir(parents=True, exist_ok=True)
    summary = json.loads((qdir / 'summary.json').read_text(encoding='utf-8'))
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    npz = np.load(qdir / 'parameters.npz')
    rows, shapes = build_table(plan, npz, deploy / 'params')
    (deploy / 'network.txt').write_text(''.join(' '.join(str(r[c]) for c in COLS) + '\n' for r in rows), encoding='ascii')
    arrays = {kk: npz[kk] for kk in npz.files}
    va = stage_inputs(load_set(load_split()['val']),
                      lambda c: np.load(RESULTS / 'quant' / 'z3_r128' / 'outputs' / f'{c}.npy'), summary['input'])
    hwc = lambda x: np.ascontiguousarray(x.transpose(1, 2, 0))
    runner = SizedRunner(deploy)
    report = dict(tag=args.tag, input=summary['input'], nodes=len(rows))

    gname = summary['golden']
    gcase, gz = gname.rsplit('_roi', 1)
    t0 = next(t for t in va if t['case'] == gcase)
    gz = int(gz)
    gdir = qdir / 'golden' / gname
    diff = {}

    def check(node, tensor):
        ref = np.load(gdir / f'{node.name}.npy')[0].transpose(1, 2, 0)
        diff[node.name] = int((ref.astype(np.int64) != tensor.astype(np.int64)).sum())

    out = runner.run(hwc(t0['x'][gz]), check)
    report['golden'] = dict(slice=gname, nodes_checked=len(diff), nodes_with_diff=[n for n, v in diff.items() if v],
                            final_equal=bool(np.array_equal(out, np.load(qdir / 'outputs' / f'{gcase}.npy')[gz])))
    vdir = deploy / 'images' / gname
    (vdir / 'golden').mkdir(parents=True, exist_ok=True)
    pack_activations(hwc(t0['x'][gz])).astype('<i2').tofile(vdir / 'input.bin')
    pack_activations(out[:, :, None]).astype('<i2').tofile(vdir / 'expected_final.bin')
    for r in rows:
        t = np.load(gdir / f"{r['name']}.npy")[0].transpose(1, 2, 0)
        pack_activations(t).astype('<i2').tofile(vdir / 'golden' / f"{r['name']}.bin")
    print('golden', report['golden'], flush=True)

    bad, n = [], 0
    for t in va:
        ref = np.load(qdir / 'outputs' / f"{t['case']}.npy")
        for z in range(len(t['x'])):
            n += 1
            if not np.array_equal(runner.run(hwc(t['x'][z])), ref[z]):
                bad.append(f"{t['case']}_roi{z:03d}")
        print(t['case'], len(t['x']), 'payloads, mismatching so far', len(bad), flush=True)
    report['final_outputs'] = dict(payloads=n, cases=len(va), mismatching=bad)
    full = np.ascontiguousarray(np.kron(hwc(t0['x'][gz]), np.ones((4, 4, 1), np.uint8)))   # any 512 x 512 x 3 input
    with torch.inference_mode():
        ref512 = run_integer(plan, arrays, torch.from_numpy(full).permute(2, 0, 1).unsqueeze(0))[0][0, 0].numpy()
    report['full512_mismatching_pixels'] = int((runner.run(full) != ref512).sum())
    runner.close()
    report['pass'] = (not report['golden']['nodes_with_diff'] and report['golden']['final_equal'] and not bad
                      and report['full512_mismatching_pixels'] == 0)
    (deploy / 'manifest.json').write_text(json.dumps(dict(
        quant_plan_sha256=sha256(qdir / 'quant_plan.json'), parameters_sha256=sha256(qdir / 'parameters.npz'),
        deploy_resolution=128, output_integer_threshold_ge=1, images=[gname],
        input_encoding='stage-2 ROI: 256 x 256 window (roi.py), 2 x 2 box average; ' + summary['input'],
        nodes=rows, output_shapes={kk: list(v) for kk, v in shapes.items()}), indent=1), encoding='utf-8')
    (deploy / 'verify_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('PASS' if report['pass'] else 'FAIL', json.dumps({kk: v for kk, v in report.items() if kk != 'final_outputs'}),
          'final', len(bad), 'of', n, 'mismatching')
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
