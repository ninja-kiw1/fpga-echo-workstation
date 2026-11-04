"""F5 (registration, level B): a hand-built weight set that makes the SAME 30-node U-Net compute
the per-pixel absolute difference of two grey frames, |R - G|, on the PL.

No training. Weights (everything else keeps a fixed-seed random init, whose output is
multiplied by zero before it reaches the result):
  first.block conv1   ch0 = R - G, ch1 = G - R (centre tap only), ReLU, BN = identity
  first.block conv2   identity on ch0 / ch1, ReLU, BN = identity; first.shortcut = 0
  up_path[1].conv_bridge_layer   identity on ch0 / ch1 (the full-resolution skip)
  up_path[1].conv_layer conv1    ch0 = bridge ch0 + bridge ch1 = |R - G|; the upsampled half of
                                 the concat gets weight 0; conv2 identity; shortcut = 0
  final               logit = ch0
Every used tap is a centre tap, so along the path that reaches the output each output pixel
depends only on the same input pixel: tiles of a 512 input are independent (16 candidate
shifts in 4 x 4 tiles of 128 x 128 in one 512 run), checked below.
Quantized with the same tools as the other models (all INT8, max-abs calibration on inputs
that reach |R - G| = 255, so the scale covers the full range): integer logit ~ |a - b| / 2.

    python make_reg_model.py [--tag reg_absdiff]
"""
import argparse
import hashlib
import json

import numpy as np
import torch

from common import CACHE, RESULTS
from int8_reference import Observer, build_plan, fuse_conv_affine, run_integer, self_test
from models.get_model import get_arch


def identity_bn(bn):
    with torch.no_grad():
        bn.weight.fill_(1.0)
        bn.bias.zero_()
        bn.running_mean.zero_()
        bn.running_var.fill_(1.0 - bn.eps)


def centre(conv, pairs):
    """Zero the conv, then set centre taps: pairs = [(out, in, value)]."""
    with torch.no_grad():
        conv.weight.zero_()
        if conv.bias is not None:
            conv.bias.zero_()
        kh, kw = conv.weight.shape[2] // 2, conv.weight.shape[3] // 2
        for o, i, v in pairs:
            conv.weight[o, i, kh, kw] = v


def build():
    torch.manual_seed(20261009)
    m = get_arch('unet').eval()
    f = m.first
    centre(f.block[0], [(0, 0, 1.0), (0, 1, -1.0), (1, 0, -1.0), (1, 1, 1.0)])
    identity_bn(f.block[2])
    centre(f.block[3], [(0, 0, 1.0), (1, 1, 1.0)])
    identity_bn(f.block[5])
    centre(f.shortcut[0], [])
    identity_bn(f.shortcut[1])
    u = m.up_path[1]
    centre(u.conv_bridge_layer.block[0], [(0, 0, 1.0), (1, 1, 1.0)])
    identity_bn(u.conv_bridge_layer.block[2])
    c = u.conv_layer
    half = c.block[0].weight.shape[1] // 2                     # concat = [up (half), bridge (half)]
    centre(c.block[0], [(0, half, 1.0), (0, half + 1, 1.0)])
    identity_bn(c.block[2])
    centre(c.block[3], [(0, 0, 1.0)])
    identity_bn(c.block[5])
    centre(c.shortcut[0], [])
    identity_bn(c.shortcut[1])
    centre(m.final, [(0, 0, 1.0)])
    return m


def tile16(a128, b128s):
    """4 x 4 tiles: tile k = (R a, G b_k, B 0)."""
    out = np.zeros((512, 512, 3), np.uint8)
    for k, b in enumerate(b128s):
        y, x = (k // 4) * 128, (k % 4) * 128
        out[y:y + 128, x:x + 128, 0] = a128
        out[y:y + 128, x:x + 128, 1] = b
    return out


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', default='reg_absdiff')
    args = ap.parse_args()
    out = RESULTS / 'quant' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(8)
    tests = self_test()
    model = build()
    graph = torch.fx.symbolic_trace(model)

    # float check: exactly |R - G| (up to float32 rounding)
    rng = np.random.default_rng(0)
    x = torch.from_numpy(rng.integers(0, 256, (4, 3, 128, 128)).astype(np.float32) / 255)
    with torch.inference_mode():
        y = model(x)[:, 0]
    float_err = float((y - (x[:, 0] - x[:, 1]).abs()).abs().max())
    print('float max |out - |R-G|| =', float_err, flush=True)
    assert float_err < 1e-5

    # calibration: real consecutive CAMUS training frames + synthetic full-range pairs
    img = np.load(CACHE / 'seq_train_img128.npy', mmap_mode='r')
    cal = []
    for s in (0, 400, 900, 1500, 2600, 4100):
        cal.append(np.stack([img[s], img[s + 2], np.zeros_like(img[s])], -1))
    ramp = np.tile(np.arange(128, dtype=np.uint8) * 2, (128, 1))
    cal.append(np.stack([ramp, 255 - ramp, np.zeros_like(ramp)], -1))
    cal.append(np.stack([255 - ramp.T, ramp.T, np.zeros_like(ramp)], -1))
    cal_q = torch.from_numpy(np.stack(cal)).permute(0, 3, 1, 2).contiguous()
    obs = Observer(graph)
    with torch.inference_mode():
        for i in range(len(cal)):
            obs.run(cal_q[i:i + 1].float() / 255)
    plan, arrays = build_plan(graph, dict(obs.ranges), {})
    plan, arrays = fuse_conv_affine(graph, plan, arrays)
    (out / 'quant_plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    np.savez(out / 'parameters.npz', **arrays)
    plan = json.loads((out / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(out / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}

    def run(rgb):
        with torch.inference_mode():
            q = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).unsqueeze(0)
            return run_integer(plan, arrays, q)[0][0, 0].numpy().astype(np.int64)

    # integer vs exact on real pairs (validation sequences) and random pairs
    vimg = np.load(CACHE / 'seq_val_img128.npy', mmap_mode='r')
    errs, pairs = [], []
    for s in range(0, 1800, 60):
        a, b = np.asarray(vimg[s]), np.asarray(vimg[s + 1])
        pairs.append((a, b))
    for _ in range(10):
        pairs.append((rng.integers(0, 256, (128, 128)).astype(np.uint8), rng.integers(0, 256, (128, 128)).astype(np.uint8)))
    for a, b in pairs:
        lg = run(np.stack([a, b, np.zeros_like(a)], -1))
        exact = np.abs(a.astype(np.int64) - b.astype(np.int64)) * 127 / 255
        errs.append(lg - exact)
    e = np.concatenate([x.ravel() for x in errs])
    print(f'integer logit - |a-b|*127/255: mean {e.mean():+.3f}, max abs {np.abs(e).max():.3f}', flush=True)

    # tile independence at 512: 16 candidates in one run == 16 separate 128 runs
    a = np.asarray(vimg[100])
    bs = [np.roll(np.asarray(vimg[101]), (dy, dx), (0, 1)) for dy in (-3, -1, 1, 3) for dx in (-3, -1, 1, 3)]
    big = run(tile16(a, bs))
    tiles_equal = all(np.array_equal(big[(k // 4) * 128:(k // 4 + 1) * 128, (k % 4) * 128:(k % 4 + 1) * 128],
                                     run(np.stack([a, b, np.zeros_like(a)], -1))) for k, b in enumerate(bs))
    print('512 tiled run == 16 separate 128 runs:', tiles_equal, flush=True)
    assert tiles_equal

    gdir = out / 'golden' / 'tile16_val100'
    gdir.mkdir(parents=True, exist_ok=True)
    grgb = tile16(a, bs)
    fp = Observer(graph, capture=True)
    q = torch.from_numpy(grgb).permute(2, 0, 1).unsqueeze(0)
    with torch.inference_mode():
        fp.run(q.float() / 255)
        run_integer(plan, arrays, q, fp.tensors, gdir)
    np.save(gdir / 'input_rgb.npy', grgb)
    summary = dict(tag=args.tag, kind='hand-built weights, no training', computes='|R - G| per pixel',
                   float_max_error=float_err, selected_int16=[], output_scale=plan[-1]['scale'],
                   integer_vs_exact=dict(mean=float(e.mean()), max_abs=float(np.abs(e).max()),
                                         definition='logit - |a-b|*127/255'),
                   tiles_independent_at_512=tiles_equal,
                   max_accumulator_abs_bound=max(s.get('accumulator_abs_bound', 0) for s in plan),
                   plan_sha256=sha256(out / 'quant_plan.json'), parameters_sha256=sha256(out / 'parameters.npz'),
                   arithmetic_tests=tests)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    print('output scale', plan[-1]['scale'], '-> done', flush=True)


if __name__ == '__main__':
    main()
