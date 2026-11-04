"""⑤ CT spleen: export the quantized model as the FPGA layer table and prove it bit-exact.

Same method as echo/export_verify_echo.py: build_table (medical_fpga export_network.py, unchanged) writes
network.txt + params/*.bin; the real driver unet_fpga.py on sim_device.py (NumPy bit-level models of both
HLS kernels) runs the table scaled to the run size and must equal the lwnet integer reference:
  1. golden slice, all 30 nodes;
  2. final output of every validation slice (7 cases, whole volumes);
  3. one slice on the full 512 x 512 table.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I export_verify_ct.py --tag z3_r128
"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PLD / 'lwnet'))
sys.path.insert(0, str(PLD / 'medical_fpga' / 'deployment' / 'unet_network'))
sys.path.insert(0, str(PLD / 'medical_fpga' / 'embedded' / 'pynq'))

import numpy as np          # noqa: E402
import torch                # noqa: E402

from common import RESULTS, load_split, stack_input   # noqa: E402
from export_network import build_table      # noqa: E402
from generate_vectors import pack_activations  # noqa: E402
from int8_reference import run_integer      # noqa: E402
from sim_device import SimDevice            # noqa: E402
from train_ct import case_inputs, load_set  # noqa: E402
from unet_fpga import UNetAccelerator       # noqa: E402

COLS = ('name', 'kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'h', 'w', 'relu', 'shift', 'qmin', 'qmax', 'm0', 'm1')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SizedRunner(object):
    def __init__(self, deploy):
        self.deploy, self.tmp, self.cache = deploy, tempfile.mkdtemp(prefix='ct_sim_'), {}

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
    mode, res = summary['input'], summary['res']
    k = 512 // res
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    npz = np.load(qdir / 'parameters.npz')
    rows, shapes = build_table(plan, npz, deploy / 'params')
    (deploy / 'network.txt').write_text(''.join(' '.join(str(r[c]) for c in COLS) + '\n' for r in rows), encoding='ascii')
    arrays = {kk: npz[kk] for kk in npz.files}
    va = load_set(load_split()['val'])
    va_in = case_inputs(va, mode, k)                     # [z, 3, h, w] per case
    hwc = lambda x: np.ascontiguousarray(x.transpose(1, 2, 0))
    runner = SizedRunner(deploy)
    report = dict(tag=args.tag, resolution=res, input=mode, nodes=len(rows))

    gname = summary['golden']
    gcase, gz = gname.rsplit('_z', 1)
    ci, gz = va['cases'].index(gcase), int(gz)
    gdir = qdir / 'golden' / gname
    diff = {}

    def check(node, tensor):
        ref = np.load(gdir / f'{node.name}.npy')[0].transpose(1, 2, 0)
        diff[node.name] = int((ref.astype(np.int64) != tensor.astype(np.int64)).sum())

    xg = hwc(va_in[ci][gz])
    out = runner.run(xg, check)
    report['golden'] = dict(slice=gname, nodes_checked=len(diff), nodes_with_diff=[n for n, v in diff.items() if v],
                            final_equal=bool(np.array_equal(out, np.load(qdir / 'outputs' / f'{gcase}.npy')[gz])))
    vdir = deploy / 'images' / gname
    (vdir / 'golden').mkdir(parents=True, exist_ok=True)
    pack_activations(xg).astype('<i2').tofile(vdir / 'input.bin')
    pack_activations(out[:, :, None]).astype('<i2').tofile(vdir / 'expected_final.bin')
    for r in rows:
        t = np.load(gdir / f"{r['name']}.npy")[0].transpose(1, 2, 0)
        pack_activations(t).astype('<i2').tofile(vdir / 'golden' / f"{r['name']}.bin")
    print('golden', report['golden'], flush=True)

    bad, n = [], 0
    for c, x in zip(va['cases'], va_in):
        ref = np.load(qdir / 'outputs' / f'{c}.npy')
        for z in range(len(x)):
            n += 1
            if not np.array_equal(runner.run(hwc(x[z])), ref[z]):
                bad.append(f'{c}_z{z:03d}')
        print(c, len(x), 'slices, mismatching so far', len(bad), flush=True)
    report['final_outputs'] = dict(slices=n, cases=len(va['cases']), mismatching=bad)

    a, b = va['spans'][ci]
    full = np.ascontiguousarray(stack_input(va['img'][a:b], mode, va['spacing'][ci][2])[gz])     # 512 x 512 x 3
    with torch.inference_mode():
        ref512 = run_integer(plan, arrays, torch.from_numpy(full).permute(2, 0, 1).unsqueeze(0))[0][0, 0].numpy()
    report['full512_mismatching_pixels'] = int((runner.run(full) != ref512).sum())
    runner.close()
    report['pass'] = (not report['golden']['nodes_with_diff'] and report['golden']['final_equal'] and not bad
                      and report['full512_mismatching_pixels'] == 0)
    (deploy / 'manifest.json').write_text(json.dumps(dict(
        quant_plan_sha256=sha256(qdir / 'quant_plan.json'), parameters_sha256=sha256(qdir / 'parameters.npz'),
        deploy_resolution=res, output_integer_threshold_ge=1, images=[gname],
        input_encoding=('R/G/B = CT slices z-o, z, z+o (o ~ 5 mm)' if mode == 'z3' else 'slice replicated to R=G=B')
        + ', W400/L40 window, host %dx%d box average' % (k, k), nodes=rows,
        output_shapes={kk: list(v) for kk, v in shapes.items()}), indent=1), encoding='utf-8')
    (deploy / 'verify_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('PASS' if report['pass'] else 'FAIL', json.dumps({kk: v for kk, v in report.items() if kk != 'final_outputs'}),
          'final', len(bad), 'of', n, 'mismatching')
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
