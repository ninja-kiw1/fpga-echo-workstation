"""Generate HLS test vectors for the generic U-Net convolution layer.

The NumPy reference here is independent of the PyTorch integer reference in
lwnet/int8_reference.py. Every real layer is first checked against the lwnet
golden tensors, then written in the packed layout expected by conv_layer.

Layouts (all little-endian):
  activations  NHWC, int16 container, channels padded to a multiple of 8;
               one 128-bit AXI word holds 8 consecutive channels of a pixel.
  params.bin   64-bit words: packed weights, then bias[Cp], multiplier[Cp],
               offset[Cp] (each sign-extended to int64). Weight word order is
               tap, output group, input group, output lane; the 8 input lanes
               of one word are int8 at bits 8*i..8*i+7.
"""
from pathlib import Path
import argparse
import json

import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT_QUANT = HERE.parents[2] / 'lwnet' / 'results' / 'quant_sensitivity'
LANES, MAX_C, MAX_W, MAX_H, MAX_SHIFT = 8, 32, 512, 512, 31
MODE_CONV3, MODE_CONV1, MODE_TCONV2 = 0, 1, 2
TAPS = {MODE_CONV3: 9, MODE_CONV1: 1, MODE_TCONV2: 4}
# Must match the m_axi depth pragmas in src/conv_layer.cpp.
COSIM_DEPTH = {'in': 65536, 'out': 131072, 'params': 2048}


def groups(c):
    return (c + LANES - 1) // LANES


def round_shift(v, shift):
    """Signed round to nearest, ties to even, on int64 arrays."""
    v = np.asarray(v, dtype=np.int64)
    if shift == 0:
        return v.copy()
    mag = np.abs(v)
    q = mag >> shift
    rem = mag & ((1 << shift) - 1)
    half = 1 << (shift - 1)
    q = q + ((rem > half) | ((rem == half) & ((q & 1) == 1)))
    return np.where(v < 0, -q, q)


def conv_accumulate(x, w_taps, bias, mode):
    """x: HWC int64; w_taps: (taps, cout, cin) int64. Returns HWC int64 sums."""
    h, wd, _ = x.shape
    if mode == MODE_CONV3:
        xp = np.pad(x, ((1, 1), (1, 1), (0, 0)))
        acc = np.zeros((h, wd, w_taps.shape[1]), np.int64)
        for t in range(9):
            ky, kx = divmod(t, 3)
            acc += xp[ky:ky + h, kx:kx + wd, :] @ w_taps[t].T
    elif mode == MODE_CONV1:
        acc = x @ w_taps[0].T
    else:
        acc = np.zeros((2 * h, 2 * wd, w_taps.shape[1]), np.int64)
        for t in range(4):
            ky, kx = divmod(t, 2)
            acc[ky::2, kx::2, :] = x @ w_taps[t].T
    return acc + bias


def layer_reference(x, w_taps, bias, mult, offset, mode, relu, shift, qmin, qmax):
    acc = conv_accumulate(x.astype(np.int64), w_taps.astype(np.int64), bias.astype(np.int64), mode)
    if np.abs(acc).max(initial=0) >= 2**31:
        raise OverflowError('accumulator exceeds INT32')
    if relu:
        acc = np.maximum(acc, 0)
    v = acc * mult.astype(np.int64) + offset.astype(np.int64)
    return np.clip(round_shift(v, shift), qmin, qmax)


def taps_from_torch(weight, mode):
    """OIHW conv or IOHW transposed conv weight -> (taps, cout, cin)."""
    if mode == MODE_TCONV2:
        return np.transpose(weight, (2, 3, 1, 0)).reshape(4, weight.shape[1], weight.shape[0])
    return np.transpose(weight, (2, 3, 0, 1)).reshape(-1, weight.shape[0], weight.shape[1])


def pack_activations(hwc):
    h, w, c = hwc.shape
    padded = np.zeros((h, w, groups(c) * LANES), np.int16)
    padded[:, :, :c] = hwc
    return padded


def pack_params(w_taps, bias, mult, offset):
    taps, cout, cin = w_taps.shape
    cog, cig = groups(cout), groups(cin)
    w = np.zeros((taps, cog * LANES, cig * LANES), np.int8)
    w[:, :cout, :cin] = w_taps
    # (tap, og, ig, o, i): one 64-bit word per (tap, og, ig, o).
    w = w.reshape(taps, cog, LANES, cig, LANES).transpose(0, 1, 3, 2, 4)
    words = np.ascontiguousarray(w).view(np.uint64).reshape(-1)
    per_channel = []
    for values in (bias, mult, offset):
        a = np.zeros(cog * LANES, np.int64)
        a[:cout] = values
        per_channel.append(a.view(np.uint64))
    return np.concatenate([words] + per_channel).astype('<u8')


def cycle_estimate(cfg):
    """Loop-trip model of the v0 architecture; excludes pipeline fill and AXI latency."""
    cig, cog, taps = groups(cfg['cin']), groups(cfg['cout']), TAPS[cfg['mode']]
    kk = 9 if cfg['mode'] == MODE_CONV3 else 1
    out_h, out_w = cfg['out_h'], cfg['out_w']
    params = taps * cog * cig * LANES + 3 * cog * LANES
    load = cfg['in_h'] * cfg['in_w'] * cig
    compute = out_h * out_w * cog * kk * cig
    store = out_h * out_w * cog
    return dict(params=params, load=load, compute=compute, store=store,
                total=params + load + compute + store,
                useful_macs=out_h * out_w * cfg['cout'] * cfg['cin'] * kk)


def write_case(root, name, cfg, x, w_taps, bias, mult, offset, expected, source):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    mode = cfg['mode']
    out_h, out_w = expected.shape[:2]
    cfg = dict(cfg, out_h=out_h, out_w=out_w)
    (d / 'config.txt').write_text(
        ' '.join(str(int(cfg[k])) for k in
                 ('mode', 'cin', 'cout', 'in_h', 'in_w', 'relu', 'shift', 'qmin', 'qmax')) + '\n',
        encoding='ascii')
    params = pack_params(w_taps, bias, mult, offset)
    params.tofile(d / 'params.bin')
    xin, yout = pack_activations(x), pack_activations(expected)
    xin.astype('<i2').tofile(d / 'input.bin')
    yout.astype('<i2').tofile(d / 'expected.bin')
    sizes = {'in': xin.size // LANES, 'out': yout.size // LANES, 'params': params.size}
    return dict(name=name, source=source, **{k: int(v) for k, v in cfg.items()},
                words=sizes, cycles=cycle_estimate(cfg))


def check_config(cfg):
    mode = cfg['mode']
    assert mode in TAPS and 1 <= cfg['cin'] <= MAX_C and 1 <= cfg['cout'] <= MAX_C
    limit_w = MAX_W // 2 if mode == MODE_TCONV2 else MAX_W
    limit_h = MAX_H // 2 if mode == MODE_TCONV2 else MAX_H
    assert 1 <= cfg['in_w'] <= limit_w and 1 <= cfg['in_h'] <= limit_h
    assert 0 <= cfg['shift'] <= MAX_SHIFT and -32768 <= cfg['qmin'] <= cfg['qmax'] <= 32767


def real_cases(quant_dir, image):
    plan = json.loads((quant_dir / 'quant_plan.json').read_text(encoding='utf-8'))
    arrays = np.load(quant_dir / 'parameters.npz')
    golden = quant_dir / image / 'golden_tensors'
    for s in plan:
        if s['op'] not in ('ConvAffine', 'Conv2d', 'ConvTranspose2d'):
            continue
        name = s['name']
        mode = MODE_TCONV2 if s['op'] == 'ConvTranspose2d' else (
            MODE_CONV3 if s['kernel'] == [3, 3] else MODE_CONV1)
        assert s['padding'] == ([1, 1] if mode == MODE_CONV3 else [0, 0])
        assert s['stride'] == ([2, 2] if mode == MODE_TCONV2 else [1, 1])
        x = np.load(golden / (s['inputs'][0] + '.npy'))[0].transpose(1, 2, 0)
        ref = np.load(golden / (name + '.npy'))[0].transpose(1, 2, 0)
        w_taps = taps_from_torch(arrays[name + '__weight'], mode)
        bias = arrays[name + '__bias']
        mult = arrays[name + '__multiplier']
        # Plain Conv2d / ConvTranspose2d requantize without an offset.
        offset = arrays[name + '__offset'] if name + '__offset' in arrays.files \
            else np.zeros(mult.shape, np.int64)
        relu = bool(s.get('relu_before_affine', False))
        cfg = dict(mode=mode, cin=x.shape[2], cout=ref.shape[2], in_h=x.shape[0], in_w=x.shape[1],
                   relu=int(relu), shift=s['shift'], qmin=s['qmin'], qmax=s['qmax'])
        check_config(cfg)
        # Input qmin/qmax of the previous node bound the INT32 accumulator.
        expected = layer_reference(x, w_taps, bias, mult, offset, mode, relu,
                                   s['shift'], s['qmin'], s['qmax'])
        if not np.array_equal(expected, ref.astype(np.int64)):
            bad = int((expected != ref).sum())
            raise AssertionError(f'{name}: NumPy reference differs from golden tensor at {bad} values')
        yield name, cfg, x, w_taps, bias, mult, offset, expected, f'{image}/golden_tensors/{name}.npy'


def synthetic_cases(rng):
    """Small cases for boundaries, rounding ties, saturation and state reuse."""
    def make(name, mode, cin, cout, h, w, relu, shift, qmin, qmax, xlo, xhi, mult_bits=24,
             offset_bits=40, wlo=-127, whi=127, x=None):
        taps = TAPS[mode]
        if x is None:
            x = rng.integers(xlo, xhi + 1, (h, w, cin), dtype=np.int64)
        w_taps = rng.integers(wlo, whi + 1, (taps, cout, cin), dtype=np.int64)
        bias = rng.integers(-50000, 50001, cout, dtype=np.int64)
        mult = rng.integers(-(1 << mult_bits), (1 << mult_bits) + 1, cout, dtype=np.int64)
        offset = rng.integers(-(1 << offset_bits), (1 << offset_bits) + 1, cout, dtype=np.int64)
        cfg = dict(mode=mode, cin=cin, cout=cout, in_h=h, in_w=w, relu=int(relu), shift=shift,
                   qmin=qmin, qmax=qmax)
        check_config(cfg)
        expected = layer_reference(x, w_taps, bias, mult, offset, mode, relu, shift, qmin, qmax)
        return name, cfg, x, w_taps, bias, mult, offset, expected, 'synthetic'

    cases = [
        make('syn_rgb_3x3_int16', MODE_CONV3, 3, 8, 7, 9, True, 27, -32767, 32767, 0, 255),
        make('syn_c32_3x3_int8', MODE_CONV3, 32, 32, 5, 6, True, 30, -127, 127, -127, 127),
        make('syn_int16_in_3x3', MODE_CONV3, 16, 8, 4, 5, False, 30, -127, 127, -32767, 32767,
             mult_bits=20),
        make('syn_1x1_c13_c5', MODE_CONV1, 13, 5, 6, 7, False, 30, -127, 127, -127, 127),
        make('syn_tconv_c16_c8', MODE_TCONV2, 16, 8, 3, 4, False, 30, -127, 127, -127, 127),
        make('syn_single_pixel_3x3', MODE_CONV3, 8, 8, 1, 1, True, 30, -127, 127, -127, 127),
        make('syn_single_row_3x3', MODE_CONV3, 9, 3, 1, 11, True, 30, -127, 127, -127, 127),
        make('syn_single_col_3x3', MODE_CONV3, 9, 3, 11, 1, True, 30, -127, 127, -127, 127),
        make('syn_shift0', MODE_CONV1, 8, 8, 4, 4, False, 0, -32767, 32767, -127, 127,
             mult_bits=2, offset_bits=6),
        # Small shifts make exact halves common: covers ties on both signs.
        make('syn_ties_shift1', MODE_CONV1, 2, 8, 8, 8, False, 1, -32767, 32767, -20, 20,
             mult_bits=1, offset_bits=1, wlo=-3, whi=3),
        make('syn_ties_shift2', MODE_CONV1, 2, 8, 8, 8, False, 2, -32767, 32767, -20, 20,
             mult_bits=2, offset_bits=2, wlo=-3, whi=3),
        # Large multipliers force saturation on both limits.
        make('syn_saturate', MODE_CONV3, 8, 8, 6, 6, False, 10, -127, 127, -127, 127,
             mult_bits=12, offset_bits=20),
        make('syn_max_width_rows', MODE_CONV3, 8, 8, 2, 512, True, 30, -127, 127, -127, 127),
        make('syn_tconv_max_width', MODE_TCONV2, 8, 8, 2, 256, False, 30, -127, 127, -127, 127),
    ]
    # Re-run a small case after large ones: stale line-buffer or weight state must not leak.
    cases.append(make('syn_after_large', MODE_CONV3, 5, 7, 3, 3, True, 30, -127, 127, -127, 127))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--quant-dir', type=Path, default=DEFAULT_QUANT)
    parser.add_argument('--image', default='im0002')
    parser.add_argument('--output', type=Path, default=HERE / 'vectors')
    args = parser.parse_args()
    rng = np.random.default_rng(20260923)
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    syn = synthetic_cases(rng)
    for case in syn[:-1]:
        records.append(write_case(args.output, *case))
    for case in real_cases(args.quant_dir, args.image):
        records.append(write_case(args.output, *case))
    records.append(write_case(args.output, *syn[-1]))
    cosim = [r['name'] for r in records if r['source'] == 'synthetic'] + [
        'down_path_1_block_5', 'up_path_0_up_layer_block_0']
    for r in records:
        if r['name'] in cosim:
            assert all(r['words'][k] <= COSIM_DEPTH[k] for k in COSIM_DEPTH), r['name']
    (args.output / 'cases_all.txt').write_text(''.join(r['name'] + '\n' for r in records), encoding='ascii')
    (args.output / 'cases_cosim.txt').write_text(''.join(n + '\n' for n in cosim), encoding='ascii')
    real = [r for r in records if r['source'] != 'synthetic']
    totals = {k: sum(r['cycles'][k] for r in real) for k in real[0]['cycles']}
    (args.output / 'manifest.json').write_text(json.dumps({
        'quant_dir': str(args.quant_dir), 'image': args.image, 'seed': 20260923,
        'lanes': LANES, 'cosim_depth_words': COSIM_DEPTH, 'cases': records,
        'network_conv_cycle_model': totals,
    }, indent=2), encoding='utf-8')
    print(f'{len(records)} cases ({len(real)} real layers checked against golden tensors).')
    print('Network conv-layer cycle model:', totals)
    print(f"  at 100 MHz: {totals['total'] / 1e5:.1f} ms  (loop trips only, excludes AXI latency)")


if __name__ == '__main__':
    main()
