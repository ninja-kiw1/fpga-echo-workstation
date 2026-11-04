"""probe: which operation order numpy uses (np.convolve 'valid' with ones(5)/5, linspace, floor_divide, sum, interp)"""
import numpy as np
from fractions import Fraction
rng = np.random.default_rng(1)
k = np.ones(5) / 5
def fma(a, b, c):
    return float(Fraction(a) * Fraction(b) + Fraction(c))
bad_seq = bad_fma = bad_seq0 = 0
for _ in range(3000):
    a = rng.uniform(0, 512, 9) if _ % 2 else np.round(rng.uniform(0, 512, 9)) + rng.integers(0, 4, 9) * 0.25
    r = np.convolve(a, k, 'valid')
    for i in range(len(r)):
        w = a[i:i + 5]
        s = 0.0
        for x in w: s = s + x * 0.2
        f = 0.0
        for x in w: f = fma(x, 0.2, f)
        s0 = w[0] * 0.2
        for x in w[1:]: s0 = s0 + x * 0.2
        bad_seq += s != r[i]; bad_fma += f != r[i]; bad_seq0 += s0 != r[i]
print('convolve: seq', bad_seq, 'fma', bad_fma, 'seq0', bad_seq0)
bad = 0
for _ in range(20000):
    stop = float(rng.uniform(1, 400)); n = int(rng.integers(2, 150))
    t = np.linspace(0, stop, n); step = stop / (n - 1)
    ref = [i * step + 0.0 for i in range(n)]; ref[-1] = stop
    bad += any(float(x) != y for x, y in zip(t, ref))
print('linspace bad', bad)
import math
def npfd(a, b):
    mod = math.fmod(a, b); div = (a - mod) / b
    if mod:
        if (b < 0) != (mod < 0): mod += b; div -= 1.0
    if div:
        fl = math.floor(div)
        if div - fl > 0.5: fl += 1.0
    else: fl = math.copysign(0.0, a / b)
    return fl
bad = 0
for _ in range(200000):
    a = float(rng.uniform(0, 400)) if _ % 3 else float(rng.integers(0, 130)) * 3.0
    bad += npfd(a, 3.0) != float(np.float64(a) // 3.0)
print('floordiv bad', bad)
def pw(a, lo, n):
    if n < 8:
        s = -0.0
        for i in range(n): s = s + a[lo + i]
        return s
    if n <= 128:
        r = [a[lo + i] for i in range(8)]
        i = 8
        while i < n - (n % 8):
            for j in range(8): r[j] = r[j] + a[lo + i + j]
            i += 8
        res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
        while i < n: res = res + a[lo + i]; i += 1
        return res
    n2 = n // 2; n2 -= n2 % 8
    return pw(a, lo, n2) + pw(a, lo + n2, n - n2)
bad = 0
for _ in range(5000):
    a = rng.uniform(0, 5, int(rng.integers(1, 400)))
    bad += pw(list(a), 0, len(a)) != float(a.sum())
print('pairwise sum bad', bad)
