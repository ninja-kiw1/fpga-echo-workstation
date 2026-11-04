"""Export the quantized CAMUS LV model as the FPGA layer table and prove it bit-exact.

Same method as skin/export_verify.py: build_table from medical_fpga
export_network.py (unchanged) writes network.txt + params/*.bin; the real
driver unet_fpga.py on sim_device.py (NumPy bit-level models of both HLS
kernels) runs the table scaled to the run size and must equal the lwnet
integer reference:
  1. golden validation frame, all 30 nodes;
  2. final output of all 200 validation frames;
  3. one frame on the full 512x512 table.

    python export_verify_echo.py --tag lv_r128
"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile

import numpy as np
import torch

from common import PLD, RESULTS, ROOT, load_cache
from int8_reference import run_integer

sys.path.insert(0, str(PLD / 'medical_fpga' / 'deployment' / 'unet_network'))
sys.path.insert(0, str(PLD / 'medical_fpga' / 'embedded' / 'pynq'))
from export_network import build_table      # noqa: E402
from generate_vectors import pack_activations  # noqa: E402
from sim_device import SimDevice            # noqa: E402
from unet_fpga import UNetAccelerator       # noqa: E402

COLS = ('name', 'kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'h', 'w', 'relu', 'shift', 'qmin', 'qmax', 'm0', 'm1')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SizedRunner(object):
    def __init__(self, deploy):
        self.deploy, self.tmp, self.cache = deploy, tempfile.mkdtemp(prefix='echo_sim_'), {}

    def run(self, rgb, on_node=None):
        h, w = rgb.shape[:2]
        if (h, w) not in self.cache:
            d = f'{self.tmp}/{h}x{w}'
            shutil.copytree(self.deploy / 'params', f'{d}/params')
            lines = []
            for line in open(self.deploy / 'network.txt'):
                f = line.split()
                f[7], f[8] = str(int(f[7]) * h // 512), str(int(f[8]) * w // 512)
                lines.append(' '.join(f))
            open(f'{d}/network.txt', 'w').write('\n'.join(lines) + '\n')
            self.cache[(h, w)] = UNetAccelerator(SimDevice(), d)
        return self.cache[(h, w)].run(np.ascontiguousarray(rgb), on_node)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()
    qdir = RESULTS / 'quant' / args.tag
    deploy = ROOT / 'deploy' / args.tag
    (deploy / 'params').mkdir(parents=True, exist_ok=True)
    summary = json.loads((qdir / 'summary.json').read_text(encoding='utf-8'))
    k = 1 << summary['shift']
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    npz = np.load(qdir / 'parameters.npz')
    rows, shapes = build_table(plan, npz, deploy / 'params')
    (deploy / 'network.txt').write_text(''.join(' '.join(str(r[c]) for c in COLS) + '\n' for r in rows), encoding='ascii')
    arrays = {kk: npz[kk] for kk in npz.files}
    xva, *_, meta = load_cache('val')
    rgb_of = lambda x512: np.ascontiguousarray(np.repeat(np.asarray(x512)[::k, ::k, None], 3, axis=2))
    runner = SizedRunner(deploy)
    report = dict(tag=args.tag, resolution=512 // k, nodes=len(rows))

    g = summary['golden_val_index']
    gm = meta[g]
    gdir = qdir / 'golden' / f"{gm['patient']}_{gm['view']}_{gm['phase']}"
    diff = {}

    def check(node, tensor):
        ref = np.load(gdir / f'{node.name}.npy')[0].transpose(1, 2, 0)
        diff[node.name] = int((ref.astype(np.int64) != tensor.astype(np.int64)).sum())

    out = runner.run(rgb_of(xva[g]), check)
    report['golden'] = dict(frame=gdir.name, nodes_checked=len(diff), nodes_with_diff=[n for n, v in diff.items() if v],
                            final_equal=bool(np.array_equal(out, np.load(qdir / 'outputs' / f'val_{g:03d}.npy'))))
    vdir = deploy / 'images' / gdir.name
    (vdir / 'golden').mkdir(parents=True, exist_ok=True)
    pack_activations(rgb_of(xva[g])).astype('<i2').tofile(vdir / 'input.bin')
    pack_activations(out[:, :, None]).astype('<i2').tofile(vdir / 'expected_final.bin')
    for r in rows:
        t = np.load(gdir / f"{r['name']}.npy")[0].transpose(1, 2, 0)
        pack_activations(t).astype('<i2').tofile(vdir / 'golden' / f"{r['name']}.bin")
    print('golden', report['golden'], flush=True)

    bad = [i for i in range(len(meta)) if not np.array_equal(runner.run(rgb_of(xva[i])),
                                                              np.load(qdir / 'outputs' / f'val_{i:03d}.npy'))]
    report['final_outputs'] = dict(frames=len(meta), mismatching=bad)
    full = np.ascontiguousarray(np.repeat(np.asarray(xva[g])[:, :, None], 3, axis=2))
    with torch.inference_mode():
        ref512 = run_integer(plan, arrays, torch.from_numpy(full).permute(2, 0, 1).unsqueeze(0))[0][0, 0].numpy()
    report['full512_mismatching_pixels'] = int((runner.run(full) != ref512).sum())
    runner.close()
    report['pass'] = (not report['golden']['nodes_with_diff'] and report['golden']['final_equal'] and not bad
                      and report['full512_mismatching_pixels'] == 0)
    (deploy / 'manifest.json').write_text(json.dumps(dict(
        quant_plan_sha256=sha256(qdir / 'quant_plan.json'), parameters_sha256=sha256(qdir / 'parameters.npz'),
        deploy_resolution=512 // k, output_integer_threshold_ge=1, images=[gdir.name],
        input_encoding='grey replicated to R=G=B', nodes=rows,
        output_shapes={kk: list(v) for kk, v in shapes.items()}), indent=1), encoding='utf-8')
    (deploy / 'verify_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('PASS' if report['pass'] else 'FAIL', json.dumps(report))
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
