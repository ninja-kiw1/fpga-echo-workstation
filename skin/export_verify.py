"""Export the quantized ISIC model as an FPGA layer table and prove it bit-exact.

Layer table / parameter packing reuse medical_fpga/deployment/unet_network/
export_network.py `build_table` unchanged (so the format is exactly what the
board generator gen_unet_c.py reads). The table is 512-based like the fundus
one; the board scales it at run time (unet_set_shift / unet_set_size).

Bit-exact proof uses medical_fpga/embedded/pynq/sim_device.py (NumPy bit-level
references of the two HLS kernels) driven by the real driver unet_fpga.py on a
table scaled to the run size, the same way roi_exactness.CropRunner does:
  1. golden image, every one of the 30 nodes == lwnet integer reference;
  2. final output of all calibration + validation images == integer reference;
  3. one image at the full 512x512 table (model is size-agnostic) == reference.

    python export_verify.py --tag r128
"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time

import numpy as np
import torch

from common import PLD, RESULTS, ROOT, load_split
from int8_reference import run_integer

sys.path.insert(0, str(PLD / 'medical_fpga' / 'deployment' / 'unet_network'))
sys.path.insert(0, str(PLD / 'medical_fpga' / 'embedded' / 'pynq'))
from export_network import build_table      # noqa: E402
from generate_vectors import pack_activations  # noqa: E402  (unet_conv)
from sim_device import SimDevice            # noqa: E402
from unet_fpga import UNetAccelerator       # noqa: E402

COLS = ('name', 'kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'h', 'w', 'relu', 'shift', 'qmin', 'qmax', 'm0', 'm1')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SizedRunner(object):
    """Driver + simulated device on the deployed table scaled to (h, w)."""

    def __init__(self, deploy):
        self.deploy = deploy
        self.tmp = tempfile.mkdtemp(prefix='skin_sim_')
        shutil.copytree(deploy / 'params', f'{self.tmp}/params')
        self.cache = {}

    def accel(self, h, w):
        if (h, w) not in self.cache:
            lines = []
            for line in open(self.deploy / 'network.txt'):
                f = line.split()
                f[7], f[8] = str(int(f[7]) * h // 512), str(int(f[8]) * w // 512)
                lines.append(' '.join(f))
            d = f'{self.tmp}/{h}x{w}'
            shutil.copytree(f'{self.tmp}/params', f'{d}/params')
            open(f'{d}/network.txt', 'w').write('\n'.join(lines) + '\n')
            self.cache[(h, w)] = UNetAccelerator(SimDevice(), d)
        return self.cache[(h, w)]

    def run(self, rgb, on_node=None):
        return self.accel(*rgb.shape[:2]).run(np.ascontiguousarray(rgb), on_node)

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
    shift = summary['shift']
    k = 1 << shift
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    arrays = np.load(qdir / 'parameters.npz')
    rows, shapes = build_table(plan, arrays, deploy / 'params')
    (deploy / 'network.txt').write_text(''.join(' '.join(str(r[c]) for c in COLS) + '\n' for r in rows),
                                        encoding='ascii')
    arrays = {kk: arrays[kk] for kk in arrays.files}

    x, _, meta = load_split('train')
    by_name = {m['name']: i for i, m in enumerate(meta)}
    names = [r['image'] for r in summary['per_image']]
    golden = summary['golden_image']
    runner = SizedRunner(deploy)
    report = dict(tag=args.tag, shift=shift, resolution=512 >> shift, nodes=len(rows))

    # 1) golden image, node by node
    rgb = np.ascontiguousarray(x[by_name[golden], ::k, ::k])
    gdir = qdir / 'golden' / golden
    mism = {}

    def check(node, tensor):
        ref = np.load(gdir / f'{node.name}.npy')[0].transpose(1, 2, 0)
        assert ref.shape == tensor.shape, (node.name, ref.shape, tensor.shape)
        mism[node.name] = int((ref.astype(np.int64) != tensor.astype(np.int64)).sum())

    out = runner.run(rgb, check)
    assert len(mism) == len(rows)
    report['golden'] = dict(image=golden, nodes_checked=len(mism), nodes_with_diff=[n for n, v in mism.items() if v],
                            final_equal=bool(np.array_equal(out, np.load(qdir / 'outputs' / f'{golden}.npy'))))
    print('golden', report['golden'], flush=True)
    vdir = deploy / 'images' / golden
    (vdir / 'golden').mkdir(parents=True, exist_ok=True)
    pack_activations(rgb).astype('<i2').tofile(vdir / 'input.bin')
    pack_activations(out[:, :, None]).astype('<i2').tofile(vdir / 'expected_final.bin')
    for r in rows:
        t = np.load(gdir / f"{r['name']}.npy")[0].transpose(1, 2, 0)
        pack_activations(t).astype('<i2').tofile(vdir / 'golden' / f"{r['name']}.bin")

    # 2) every calibration + validation image, final output
    t0, bad = time.time(), []
    for n, name in enumerate(names):
        rgb = np.ascontiguousarray(x[by_name[name], ::k, ::k])
        if not np.array_equal(runner.run(rgb), np.load(qdir / 'outputs' / f'{name}.npy')):
            bad.append(name)
        if n % 20 == 0:
            print(f'final {n + 1}/{len(names)} mismatching images so far {len(bad)} ({time.time() - t0:.0f}s)', flush=True)
    report['final_outputs'] = dict(images=len(names), mismatching_images=bad, seconds=time.time() - t0)

    # 3) full 512x512 table on one image
    rgb512 = np.ascontiguousarray(x[by_name[golden]])
    with torch.inference_mode():
        ref512, _ = run_integer(plan, arrays, torch.from_numpy(np.array(rgb512)).permute(2, 0, 1).unsqueeze(0))
    sim512 = runner.run(rgb512)
    report['full512'] = dict(image=golden, pixels=512 * 512,
                             mismatching_pixels=int((sim512 != ref512[0, 0].numpy()).sum()))
    print('full512', report['full512'], flush=True)
    runner.close()

    report['pass'] = (not report['golden']['nodes_with_diff'] and report['golden']['final_equal']
                      and not bad and report['full512']['mismatching_pixels'] == 0)
    (deploy / 'images.txt').write_text(golden + '\n', encoding='ascii')
    (deploy / 'manifest.json').write_text(json.dumps(dict(
        quant_dir=str(qdir), quant_plan_sha256=sha256(qdir / 'quant_plan.json'),
        parameters_sha256=sha256(qdir / 'parameters.npz'), deploy_resolution=512 >> shift,
        output_integer_threshold_ge=summary['output_integer_threshold_ge'],
        images=[golden], images_with_golden_tensors=[golden],
        note=f'images/ vectors are at {512 >> shift}x{512 >> shift} (board --ai-res {512 >> shift}); network.txt is 512-based',
        nodes=rows, output_shapes={kk: list(v) for kk, v in shapes.items()}), indent=1), encoding='utf-8')
    (deploy / 'verify_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('PASS' if report['pass'] else 'FAIL', json.dumps(report, indent=1))
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
