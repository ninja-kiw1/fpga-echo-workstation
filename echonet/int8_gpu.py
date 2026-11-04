"""The lwnet integer reference (lwnet/int8_reference.run_integer, INTEGER_SPEC v2) on a CUDA device, for running the
integer model over hundreds of thousands of video frames. Same operations in the same order: convolutions as float64
sums of integers (exact below 2^53, bound checked, integrality asserted), int64 requantisation with ties-to-even
shifts, clamping to [qmin, qmax]. Its outputs are compared value by value with the CPU reference before use
(infer_videos.py --check-cpu).
"""
import numpy as np
import torch
import torch.nn.functional as F

torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False
torch.backends.cudnn.allow_tf32 = False          # TF32 would round the products: float32 sums must stay exact
torch.backends.cuda.matmul.allow_tf32 = False
EXACT32 = 2 ** 24                                 # every integer of magnitude <= 2^24 is exact in float32


def round_shift(x, shift):
    if shift == 0:
        return x
    a = x.abs()
    quotient = a >> shift
    remainder = a & ((1 << shift) - 1)
    half = 1 << (shift - 1)
    increment = (remainder > half) | ((remainder == half) & ((quotient & 1) != 0))
    return (quotient + increment.to(torch.int64)) * x.sign()


def _conv32(x, w32, spec):
    """float32 convolution of integer operands whose absolute-sum bound is below 2^24 (checked by the caller), done as
    plain multiply-adds -- im2col + GEMM (TF32 off), or one product per output for the 2x2 / stride-2 transposed
    convolution -- never cuDNN, whose transform-based algorithms (Winograd / FFT) round. With every product and every
    partial sum an exactly representable integer, the summation order does not matter: the result is exact."""
    b, c, h, w = x.shape
    xf = x.float()
    if spec['op'] == 'ConvTranspose2d':
        ci, co, kh, kw = w32.shape
        assert list(spec['stride']) == [kh, kw] and list(spec['padding']) == [0, 0] and \
            list(spec.get('output_padding') or [0, 0]) == [0, 0], spec
        y = torch.einsum('bihw,iokl->bohkwl', xf, w32).reshape(b, co, h * kh, w * kw)   # no overlapping taps
    else:
        co, ci, kh, kw = w32.shape
        assert list(spec['stride']) == [1, 1], spec
        p = list(spec['padding'])
        y = torch.empty((b, co, h + 2 * p[0] - kh + 1, w + 2 * p[1] - kw + 1), device=x.device)
        wm = w32.reshape(co, -1)
        step = max(1, int(2 ** 27 // (ci * kh * kw * h * w * 4)))            # keep the unfolded block < 128 MB
        for i in range(0, b, step):
            cols = F.unfold(xf[i:i + step], (kh, kw), padding=p)            # [s, ci*kh*kw, L]
            y[i:i + step] = torch.matmul(wm, cols).reshape(-1, co, y.shape[2], y.shape[3])
    if not torch.equal(y, y.round()):
        raise AssertionError('Nonintegral float32 convolution sum')
    return y.to(torch.int64)


def conv_integer(x, weight, spec):
    """exact integer convolution: float32 when the bound allows, else the input is split into bytes
    x = 256 * hi + lo (lo in 0..255, arithmetic shift for hi) and the two exact float32 convolutions are combined
    in int64; float64 as the last resort."""
    transpose = spec['op'] == 'ConvTranspose2d'
    axis = (0, 2, 3) if transpose else (1, 2, 3)
    wsum = int(weight.abs().sum(dim=axis).max())
    xmax = int(x.abs().max())
    w32 = weight.float()
    if xmax * wsum < EXACT32:
        return _conv32(x, w32, spec)
    hi = x >> 8
    lo = x - hi * 256
    if 255 * wsum < EXACT32 and int(hi.abs().max()) * wsum < EXACT32:
        return _conv32(hi, w32, spec) * 256 + _conv32(lo, w32, spec)
    if xmax * wsum >= 2 ** 53:
        raise OverflowError('Convolution exceeds exact binary64 integer range')
    if transpose:
        y = F.conv_transpose2d(x.double(), weight.double(), stride=spec['stride'], padding=spec['padding'],
                               output_padding=spec['output_padding'])
    else:
        y = F.conv2d(x.double(), weight.double(), stride=spec['stride'], padding=spec['padding'])
    if not torch.equal(y, y.round()):
        raise AssertionError('Nonintegral convolution sum')
    return y.to(torch.int64)


class IntegerModel(object):
    def __init__(self, plan, arrays, device='cuda'):
        self.plan, self.device = plan, torch.device(device)
        self.t = {k: torch.from_numpy(v.astype('int64')).to(self.device) for k, v in arrays.items()}
        self.remaining = {s['name']: sum(s['name'] in o['inputs'] for o in plan) for s in plan}

    @torch.no_grad()
    def __call__(self, rgb_uint8):
        """[B, 3, H, W] uint8 (numpy or tensor) -> [B, 1, H, W] int64 tensor on the device."""
        x = torch.as_tensor(np.ascontiguousarray(rgb_uint8) if isinstance(rgb_uint8, np.ndarray) else rgb_uint8)
        env, remaining, out = {}, dict(self.remaining), None
        for s in self.plan:
            name, op = s['name'], s['op']
            ins = [env[n] for n in s['inputs']]
            arr = lambda suffix: self.t[name + '__' + suffix]
            if op == 'Input':
                raw = x.to(self.device).to(torch.int64)
            elif op in ('Conv2d', 'ConvTranspose2d', 'ConvAffine'):
                spec = {**s, 'op': 'Conv2d'} if op == 'ConvAffine' else s
                acc = conv_integer(ins[0], arr('weight'), spec) + arr('bias').reshape(1, -1, 1, 1)
                assert int(acc.abs().max()) < 2 ** 31
                if op == 'ConvAffine' and s['relu_before_affine']:
                    acc = acc.clamp_min(0)
                v = acc * arr('multiplier').reshape(1, -1, 1, 1)
                if op == 'ConvAffine':
                    v = v + arr('offset').reshape(1, -1, 1, 1)
                raw = round_shift(v, s['shift'])
            elif op == 'BatchNorm2d':
                raw = round_shift(ins[0] * arr('multiplier').reshape(1, -1, 1, 1) + arr('offset').reshape(1, -1, 1, 1),
                                  s['shift'])
            elif op == 'ReLU':
                raw = ins[0].clamp_min(0)
            elif op == 'MaxPool2d':
                n, c, h, w = ins[0].shape
                raw = ins[0].reshape(n, c, h // 2, 2, w // 2, 2).amax(dim=(3, 5))
            elif op == 'Add':
                mult = arr('multiplier')
                raw = round_shift(sum(v * mult[i] for i, v in enumerate(ins)), s['shift'])
            elif op == 'Concat':
                mult = arr('multiplier')
                raw = torch.cat([round_shift(v * mult[i], s['shift']) for i, v in enumerate(ins)], dim=1)
            else:
                raise NotImplementedError(op)
            q = raw.clamp(s['qmin'], s['qmax'])
            env[name] = q
            for n in s['inputs']:
                remaining[n] -= 1
                if remaining[n] == 0:
                    del env[n]
            out = q
        return out
