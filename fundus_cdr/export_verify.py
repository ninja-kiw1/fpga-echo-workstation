"""Export a quantized part D model as the FPGA layer table and prove it bit-exact (same method as echo/export_verify_echo.py).

build_table (medical_fpga export_network.py, unchanged) writes deploy/<tag>/network.txt + params/*.bin; the real
driver unet_fpga.py on sim_device.py (NumPy bit-level models of both HLS kernels) must equal the lwnet integer reference:
  1. golden VAL payload, all 30 nodes;
  2. final output of every VAL payload (quantize.py outputs/);
  3. one image on the full 512 x 512 table.

    python -I export_verify.py --tag disc_full_a
"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np                       # noqa: E402
import torch                             # noqa: E402

from common import PLD, RESULTS, ROOT, load_cache   # noqa: E402
from int8_reference import run_integer   # noqa: E402

sys.path.insert(0, str(PLD / 'medical_fpga' / 'deployment' / 'unet_network'))
sys.path.insert(0, str(PLD / 'medical_fpga' / 'embedded' / 'pynq'))
from export_network import build_table      # noqa: E402
from generate_vectors import pack_activations  # noqa: E402
from sim_device import SimDevice            # noqa: E402
from unet_fpga import UNetAccelerator       # noqa: E402

COLS = ('name', 'kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'h', 'w', 'relu', 'shift', 'qmin', 'qmax', 'm0', 'm1')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class SizedRunner(object):
    def __init__(self, deploy):
        self.deploy, self.tmp, self.cache = deploy, tempfile.mkdtemp(prefix='fcdr_sim_'), {}

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
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    npz = np.load(qdir / 'parameters.npz')
    rows, shapes = build_table(plan, npz, deploy / 'params')
    (deploy / 'network.txt').write_text(''.join(' '.join(str(r[c]) for c in COLS) + '\n' for r in rows), encoding='ascii')
    arrays = {kk: npz[kk] for kk in npz.files}
    runner = SizedRunner(deploy)
    report = dict(tag=args.tag, task=summary['task'], resolution=128, nodes=len(rows))

    golden_in = np.load(qdir / 'golden_input.npy')
    diff = {}

    def check(node, tensor):
        ref = np.load(qdir / 'golden' / f'{node.name}.npy')[0].transpose(1, 2, 0)
        diff[node.name] = int((ref.astype(np.int64) != tensor.astype(np.int64)).sum())

    out = runner.run(golden_in, check)
    report['golden'] = dict(input=summary['golden'], nodes_checked=len(diff), nodes_with_diff=[n for n, v in diff.items() if v],
                            final_equal=bool(np.array_equal(out, np.load(qdir / 'outputs' / f"{summary['golden']}.npy"))))
    vdir = deploy / 'images' / summary['golden']
    (vdir / 'golden').mkdir(parents=True, exist_ok=True)
    pack_activations(golden_in).astype('<i2').tofile(vdir / 'input.bin')
    pack_activations(out[:, :, None]).astype('<i2').tofile(vdir / 'expected_final.bin')
    for r in rows:
        t = np.load(qdir / 'golden' / f"{r['name']}.npy")[0].transpose(1, 2, 0)
        pack_activations(t).astype('<i2').tofile(vdir / 'golden' / f"{r['name']}.bin")
    print('golden', report['golden'], flush=True)

    checked, bad = 0, []
    for pf in sorted(qdir.glob('val_payloads_*.npy')):
        kind = pf.stem[len('val_payloads_'):]
        for i, p in enumerate(np.load(pf)):
            checked += 1
            if not np.array_equal(runner.run(p), np.load(qdir / 'outputs' / f'{kind}_{i:03d}.npy')):
                bad.append(f'{kind}_{i:03d}')
    report['final_outputs'] = dict(inputs=checked, mismatching=bad)
    full = np.ascontiguousarray(load_cache('chaksu_val')['img512'][0])
    with torch.inference_mode():
        ref512 = run_integer(plan, arrays, torch.from_numpy(full).permute(2, 0, 1).unsqueeze(0))[0][0, 0].numpy()
    report['full512_mismatching_pixels'] = int((runner.run(full) != ref512).sum())
    runner.close()
    report['pass'] = (not report['golden']['nodes_with_diff'] and report['golden']['final_equal'] and not bad
                      and report['full512_mismatching_pixels'] == 0)
    (deploy / 'manifest.json').write_text(json.dumps(dict(
        quant_plan_sha256=sha256(qdir / 'quant_plan.json'), parameters_sha256=sha256(qdir / 'parameters.npz'),
        deploy_resolution=128, output_integer_threshold_ge=1, images=[summary['golden']], task=summary['task'],
        input_encoding='RGB fundus, host LANCZOS 512 x 512, then cdr_ref payload (box mean)', nodes=rows,
        output_shapes={kk: list(v) for kk, v in shapes.items()}), indent=1), encoding='utf-8')
    (deploy / 'verify_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('PASS' if report['pass'] else 'FAIL', json.dumps(report))
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
