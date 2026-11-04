"""③ export the quantized EchoNet LV model as the FPGA layer table and prove it bit-exact (as echo/export_verify_echo.py):
  1. golden VAL frame, all 30 nodes;   2. final output of every VAL ED / ES frame (cropped 112 == integer reference);
  3. one frame on the full 512 x 512 table.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I export_verify_echonet.py --tag t3
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

from common import PAD, RESULTS, load_frames   # noqa: E402
from export_network import build_table      # noqa: E402
from generate_vectors import pack_activations  # noqa: E402
from int8_reference import run_integer      # noqa: E402
from sim_device import SimDevice            # noqa: E402
from train_echonet import to_input          # noqa: E402
from unet_fpga import UNetAccelerator       # noqa: E402

COLS = ('name', 'kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'h', 'w', 'relu', 'shift', 'qmin', 'qmax', 'm0', 'm1')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SizedRunner(object):
    """the real driver (unet_fpga.py) on the NumPy bit-level models of both HLS kernels, table scaled to the run size."""

    def __init__(self, deploy):
        self.deploy, self.tmp, self.cache = deploy, tempfile.mkdtemp(prefix='echonet_sim_'), {}

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
    arrays = {k: npz[k] for k in npz.files}
    xva, _, _ = load_frames('val')
    xv = to_input(np.asarray(xva), summary['input'])
    ref = np.load(qdir / 'val_outputs.npy')
    hwc = lambda x: np.ascontiguousarray(x.transpose(1, 2, 0))
    runner = SizedRunner(deploy)
    report = dict(tag=args.tag, input=summary['input'], nodes=len(rows))

    g = summary['golden_index']
    gdir = qdir / 'golden' / summary['golden']
    diff = {}

    def check(node, tensor):
        r = np.load(gdir / f'{node.name}.npy')[0].transpose(1, 2, 0)
        diff[node.name] = int((r.astype(np.int64) != tensor.astype(np.int64)).sum())

    out = runner.run(hwc(xv[g]), check)
    report['golden'] = dict(frame=gdir.name, nodes_checked=len(diff), nodes_with_diff=[n for n, v in diff.items() if v],
                            final_equal=bool(np.array_equal(out[PAD:PAD + 112, PAD:PAD + 112], ref[g])))
    vdir = deploy / 'images' / gdir.name
    (vdir / 'golden').mkdir(parents=True, exist_ok=True)
    pack_activations(hwc(xv[g])).astype('<i2').tofile(vdir / 'input.bin')
    pack_activations(out[:, :, None]).astype('<i2').tofile(vdir / 'expected_final.bin')
    for r in rows:
        t = np.load(gdir / f"{r['name']}.npy")[0].transpose(1, 2, 0)
        pack_activations(t).astype('<i2').tofile(vdir / 'golden' / f"{r['name']}.bin")
    print('golden', report['golden'], flush=True)

    bad = []
    for i in range(len(xv)):
        if not np.array_equal(runner.run(hwc(xv[i]))[PAD:PAD + 112, PAD:PAD + 112], ref[i]):
            bad.append(i)
        if (i + 1) % 500 == 0:
            print(i + 1, 'frames, mismatching', len(bad), flush=True)
    report['final_outputs'] = dict(frames=len(xv), mismatching=bad)
    full = np.ascontiguousarray(np.kron(hwc(xv[g]), np.ones((4, 4, 1), np.uint8)))
    with torch.inference_mode():
        ref512 = run_integer(plan, arrays, torch.from_numpy(full).permute(2, 0, 1).unsqueeze(0))[0][0, 0].numpy()
    report['full512_mismatching_pixels'] = int((runner.run(full) != ref512).sum())
    runner.close()
    report['pass'] = (not report['golden']['nodes_with_diff'] and report['golden']['final_equal'] and not bad
                      and report['full512_mismatching_pixels'] == 0)
    (deploy / 'manifest.json').write_text(json.dumps(dict(
        quant_plan_sha256=sha256(qdir / 'quant_plan.json'), parameters_sha256=sha256(qdir / 'parameters.npz'),
        deploy_resolution=128, output_integer_threshold_ge=1, images=[gdir.name],
        input_encoding='EchoNet 112 grey zero-padded to 128 (8 px), ' + summary['input'] + '; output cropped [8:120, 8:120]',
        nodes=rows, output_shapes={k: list(v) for k, v in shapes.items()}), indent=1), encoding='utf-8')
    (deploy / 'verify_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('PASS' if report['pass'] else 'FAIL', json.dumps({k: v for k, v in report.items() if k != 'final_outputs'}),
          'final', len(bad), 'of', len(xv), 'mismatching')
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
