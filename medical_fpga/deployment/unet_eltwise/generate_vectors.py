"""Generate HLS test vectors for the Add / Concat / MaxPool layer.

Uses the activation packing and rounding helpers of unet_conv/generate_vectors.py.
Real nodes are checked against the lwnet golden tensors before being written.

Per case directory: config.txt (mode ca cb h w m0 m1 shift qmin qmax),
a.bin, b.bin (absent for MaxPool) and expected.bin, all packed int16 NHWC.
"""
from pathlib import Path
import argparse
import json
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'unet_conv'))
from generate_vectors import DEFAULT_QUANT, LANES, MAX_C, MAX_SHIFT, groups, pack_activations, round_shift  # noqa: E402

MODE_ADD, MODE_CONCAT, MODE_MAXPOOL = 0, 1, 2
MAX_W = MAX_H = 512
# Must match the m_axi depth pragmas in src/eltwise_layer.cpp.
COSIM_DEPTH = 262144


def eltwise_reference(mode, a, b, m0, m1, shift, qmin, qmax):
    """a, b: HWC integer arrays. Returns the HWC result as int64."""
    a = a.astype(np.int64)
    if mode == MODE_ADD:
        return np.clip(round_shift(a * m0 + b.astype(np.int64) * m1, shift), qmin, qmax)
    if mode == MODE_CONCAT:
        return np.clip(np.concatenate([round_shift(a * m0, shift),
                                       round_shift(b.astype(np.int64) * m1, shift)], axis=2), qmin, qmax)
    h, w, c = a.shape
    return a.reshape(h // 2, 2, w // 2, 2, c).max(axis=(1, 3))


def check_config(mode, ca, cb, h, w, shift, qmin, qmax):
    assert 1 <= ca <= MAX_C and 1 <= h <= MAX_H and 1 <= w <= MAX_W
    if mode == MODE_MAXPOOL:
        assert h % 2 == 0 and w % 2 == 0
        return
    assert 0 <= shift <= MAX_SHIFT and -32768 <= qmin <= qmax <= 32767
    if mode == MODE_ADD:
        assert cb == ca
    else:
        assert ca % LANES == 0 and 1 <= cb and ca + cb <= MAX_C


def write_case(root, name, mode, a, b, m0, m1, shift, qmin, qmax, expected, source):
    h, w, ca = a.shape
    cb = b.shape[2] if b is not None else 0
    check_config(mode, ca, cb, h, w, shift, qmin, qmax)
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / 'config.txt').write_text(f'{mode} {ca} {cb} {h} {w} {m0} {m1} {shift} {qmin} {qmax}\n', encoding='ascii')
    words = {}
    for key, t in (('a', a), ('b', b), ('expected', expected)):
        if t is None:
            (d / f'{key}.bin').unlink(missing_ok=True)
            continue
        packed = pack_activations(t)
        packed.astype('<i2').tofile(d / f'{key}.bin')
        words[key] = packed.size // LANES
    if mode == MODE_ADD:
        cycles = h * w * groups(ca)
    elif mode == MODE_CONCAT:
        cycles = h * w * 2 * (groups(ca) + groups(cb))
    else:
        cycles = (h // 2) * (2 * w * groups(ca) + (w // 2) * groups(ca))
    return dict(name=name, source=source, mode=mode, ca=ca, cb=cb, h=h, w=w, m0=int(m0), m1=int(m1),
                shift=shift, qmin=qmin, qmax=qmax, words=words, cycles=cycles)


def real_cases(quant_dir, image):
    plan = json.loads((quant_dir / 'quant_plan.json').read_text(encoding='utf-8'))
    arrays = np.load(quant_dir / 'parameters.npz')
    golden = quant_dir / image / 'golden_tensors'

    def load(name):
        return np.load(golden / (name + '.npy'))[0].transpose(1, 2, 0)

    for s in plan:
        op = s['op']
        if op not in ('Add', 'Concat', 'MaxPool2d'):
            continue
        a = load(s['inputs'][0])
        ref = load(s['name']).astype(np.int64)
        if op == 'MaxPool2d':
            mode, b, m0, m1, shift, qmin, qmax = MODE_MAXPOOL, None, 0, 0, 0, s['qmin'], s['qmax']
        else:
            mode = MODE_ADD if op == 'Add' else MODE_CONCAT
            b = load(s['inputs'][1])
            m0, m1 = (int(v) for v in arrays[s['name'] + '__multiplier'])
            shift, qmin, qmax = s['shift'], s['qmin'], s['qmax']
        expected = eltwise_reference(mode, a, b, m0, m1, shift, qmin, qmax)
        if not np.array_equal(expected, ref):
            raise AssertionError(f"{s['name']}: NumPy reference differs from golden at "
                                 f"{int((expected != ref).sum())} values")
        yield s['name'], mode, a, b, m0, m1, shift, qmin, qmax, expected, f'{image}/golden_tensors/{s["name"]}.npy'


def synthetic_cases(rng):
    def rand(h, w, c, lo=-127, hi=127):
        return rng.integers(lo, hi + 1, (h, w, c), dtype=np.int64)

    def m(bits):
        return int(rng.integers(-(1 << bits), (1 << bits) + 1))

    def make(name, mode, a, b=None, m0=0, m1=0, shift=0, qmin=-127, qmax=127):
        expected = eltwise_reference(mode, a, b, m0, m1, shift, qmin, qmax)
        return name, mode, a, b, m0, m1, shift, qmin, qmax, expected, 'synthetic'

    i16 = dict(lo=-32767, hi=32767)
    int32_max = (1 << 31) - 1
    return [
        make('syn_add_int16_full_mult', MODE_ADD, rand(5, 7, 16, **i16), rand(5, 7, 16, **i16),
             int32_max, -(1 << 31), 31, -32767, 32767),
        make('syn_add_c5', MODE_ADD, rand(3, 4, 5), rand(3, 4, 5), m(30), m(30), 30),
        make('syn_add_ties_shift1', MODE_ADD, rand(6, 6, 8, -9, 9), rand(6, 6, 8, -9, 9), 1, 2, 1, -32767, 32767),
        make('syn_add_ties_shift2', MODE_ADD, rand(6, 6, 8, -9, 9), rand(6, 6, 8, -9, 9), 3, -1, 2, -32767, 32767),
        make('syn_add_shift0', MODE_ADD, rand(2, 3, 8), rand(2, 3, 8), 2, -3, 0, -32767, 32767),
        make('syn_add_saturate', MODE_ADD, rand(4, 4, 8), rand(4, 4, 8), 1 << 12, 1 << 12, 10),
        make('syn_concat_8_5', MODE_CONCAT, rand(3, 5, 8), rand(3, 5, 5), m(30), m(30), 30),
        make('syn_concat_16_16', MODE_CONCAT, rand(4, 3, 16), rand(4, 3, 16), m(30), m(30), 30),
        make('syn_concat_24_8_int16', MODE_CONCAT, rand(2, 2, 24, **i16), rand(2, 2, 8, **i16),
             m(31), m(31), 31, -32767, 32767),
        make('syn_concat_ties', MODE_CONCAT, rand(4, 4, 8, -9, 9), rand(4, 4, 8, -9, 9), 1, -1, 1, -32767, 32767),
        make('syn_concat_max_width', MODE_CONCAT, rand(2, 512, 8), rand(2, 512, 8), m(30), m(30), 30),
        make('syn_pool_2x2_c3', MODE_MAXPOOL, rand(2, 2, 3)),
        make('syn_pool_c32_int16', MODE_MAXPOOL, rand(4, 6, 32, **i16)),
        make('syn_pool_all_negative', MODE_MAXPOOL, rand(4, 4, 8, -127, -1)),
        make('syn_pool_max_width', MODE_MAXPOOL, rand(2, 512, 8)),
        # Run after large real nodes: stale row-buffer contents must not leak.
        make('syn_pool_after_large', MODE_MAXPOOL, rand(2, 4, 8)),
        make('syn_concat_after_large', MODE_CONCAT, rand(1, 3, 8), rand(1, 3, 3), m(30), m(30), 30),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--quant-dir', type=Path, default=DEFAULT_QUANT)
    parser.add_argument('--image', default='im0002')
    parser.add_argument('--output', type=Path, default=HERE / 'vectors')
    args = parser.parse_args()
    rng = np.random.default_rng(20260924)
    args.output.mkdir(parents=True, exist_ok=True)
    syn = synthetic_cases(rng)
    records = [write_case(args.output, *c) for c in syn[:-2]]
    records += [write_case(args.output, *c) for c in real_cases(args.quant_dir, args.image)]
    records += [write_case(args.output, *c) for c in syn[-2:]]
    cosim = [r['name'] for r in records if r['source'] == 'synthetic'] + ['add_2', 'cat', 'down_path_1_pool']
    for r in records:
        if r['name'] in cosim:
            assert max(r['words'].values()) <= COSIM_DEPTH, r['name']
    (args.output / 'cases_all.txt').write_text(''.join(r['name'] + '\n' for r in records), encoding='ascii')
    (args.output / 'cases_cosim.txt').write_text(''.join(n + '\n' for n in cosim), encoding='ascii')
    real = [r for r in records if r['source'] != 'synthetic']
    (args.output / 'manifest.json').write_text(json.dumps({
        'quant_dir': str(args.quant_dir), 'image': args.image, 'seed': 20260924,
        'cosim_depth_words': COSIM_DEPTH, 'cases': records,
        'network_eltwise_cycle_model': sum(r['cycles'] for r in real)}, indent=2), encoding='utf-8')
    print(f'{len(records)} cases ({len(real)} real nodes checked against golden tensors).')
    print(f"Network eltwise cycle model: {sum(r['cycles'] for r in real)}")


if __name__ == '__main__':
    main()
