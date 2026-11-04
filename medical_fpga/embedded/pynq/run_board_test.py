"""On-board verification of the U-Net accelerator (run as root on PYNQ):

    sudo python3 run_board_test.py              # real hardware
    python run_board_test.py --sim              # PC: emulated IPs (sim_device.py)

1. Golden image (im0002): every node compared with the lwnet golden tensor.
2. Every packaged image: final logits compared with lwnet output_quantized.
3. Per-layer and end-to-end timing.
Results go to results/board_results.json (or sim_results.json with --sim).
"""
import argparse
import json
import os
import platform
import statistics
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from unet_fpga import PynqDevice, UNetAccelerator  # noqa: E402


def load_package(pkg):
    with open(os.path.join(pkg, 'manifest.json')) as f:
        return json.load(f)


def read_image(pkg, name):
    rgb = np.fromfile(os.path.join(pkg, 'images', name, 'input_rgb.bin'), dtype=np.uint8).reshape(512, 512, 3)
    expected = np.fromfile(os.path.join(pkg, 'images', name, 'expected_final.bin'), dtype=np.int8).reshape(512, 512)
    return rgb, expected


def check_golden(acc, pkg, name):
    rgb, _ = read_image(pkg, name)
    rows = []

    def compare(node, tensor):
        golden = np.load(os.path.join(pkg, 'golden', name, node.name + '.npy'))
        bad = int((tensor.astype(np.int32) != golden.astype(np.int32)).sum()) if tensor.shape == golden.shape else -1
        rows.append(dict(node=node.name, shape=list(golden.shape), mismatches=bad))
        print('%s node %-40s %-14s mismatches=%d' % ('PASS' if bad == 0 else 'FAIL', node.name,
                                                   'x'.join(map(str, golden.shape)), bad), flush=True)

    acc.run(rgb, on_node=compare)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--package', default=HERE, help='board package directory (default: this folder)')
    parser.add_argument('--sim', action='store_true', help='use the NumPy emulation instead of hardware')
    parser.add_argument('--images', nargs='*', help='subset of images (default: all in manifest)')
    parser.add_argument('--repeat', type=int, default=3, help='timed runs of the golden image')
    args = parser.parse_args()
    pkg = os.path.abspath(args.package)
    manifest = load_package(pkg)

    t0 = time.perf_counter()
    if args.sim:
        from sim_device import SimDevice
        device = SimDevice()
    else:
        device = PynqDevice(os.path.join(pkg, manifest['bitstream']))
    acc = UNetAccelerator(device, pkg, threshold=manifest['threshold_integer_ge'])
    setup_s = time.perf_counter() - t0
    print('Overlay/driver ready in %.2f s; %d nodes; %d activation buffers, %.1f MiB'
          % (setup_s, len(acc.nodes), len(acc.buffers), acc.buffer_bytes / 2**20), flush=True)

    golden_name = manifest['golden_image']
    print('\n== Per-node check: %s' % golden_name, flush=True)
    golden_rows = check_golden(acc, pkg, golden_name)
    golden_ok = all(r['mismatches'] == 0 for r in golden_rows)

    print('\n== Timing: %s x %d' % (golden_name, args.repeat), flush=True)
    rgb, _ = read_image(pkg, golden_name)
    per_node, frames = {}, []
    for _ in range(args.repeat):
        t = time.perf_counter()
        acc.run(rgb)
        frames.append(time.perf_counter() - t)
        for name, sec in acc.last_timings:
            per_node.setdefault(name, []).append(sec)
    node_ms = [(n, 1e3 * statistics.median(v)) for n, v in per_node.items()]
    for n, ms in node_ms:
        print('  %-40s %8.2f ms' % (n, ms))
    frame_ms = 1e3 * statistics.median(frames)
    accel_ms = sum(ms for _, ms in node_ms)
    print('  accelerator (sum of nodes) %.1f ms; end-to-end run() %.1f ms' % (accel_ms, frame_ms), flush=True)

    print('\n== Final output of every image', flush=True)
    image_rows = []
    for name in args.images or manifest['images']:
        rgb, expected = read_image(pkg, name)
        t = time.perf_counter()
        logits = acc.run(rgb)
        sec = time.perf_counter() - t
        bad = int((logits != expected).sum())
        vessels = int((logits >= acc.threshold).sum())
        image_rows.append(dict(image=name, mismatches=bad, vessel_pixels=vessels, run_ms=1e3 * sec))
        print('%s %s mismatches=%d vessel_pixels=%d %.1f ms' % ('PASS' if bad == 0 else 'FAIL', name, bad, vessels,
                                                                1e3 * sec), flush=True)
    images_ok = all(r['mismatches'] == 0 for r in image_rows)

    result = dict(
        mode='sim' if args.sim else 'hardware', host=platform.node(), time=time.strftime('%Y-%m-%d %H:%M:%S'),
        package_sha256=manifest['files_sha256'], setup_s=setup_s,
        golden_image=golden_name, golden_all_nodes_match=golden_ok, golden_nodes=golden_rows,
        timing=dict(repeats=args.repeat, frame_ms_median=frame_ms, accelerator_ms=accel_ms,
                    per_node_ms=dict(node_ms)),
        images_all_match=images_ok, images=image_rows)
    os.makedirs(os.path.join(pkg, 'results'), exist_ok=True)
    out = os.path.join(pkg, 'results', 'sim_results.json' if args.sim else 'board_results.json')
    with open(out, 'w') as f:
        json.dump(result, f, indent=2)
    acc.close()
    ok = golden_ok and images_ok
    print('\nSUMMARY %s: golden nodes %s, images %d/%d match; frame %.1f ms -> %s'
          % ('PASS' if ok else 'FAIL', 'match' if golden_ok else 'MISMATCH',
             sum(r['mismatches'] == 0 for r in image_rows), len(image_rows), frame_ms, out))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
