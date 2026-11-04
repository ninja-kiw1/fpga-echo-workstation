"""opcomp IR: image operators as expressions, to be compiled into weights of the fixed 30-node U-Net.

Values are images of the same size. Inputs are the three channels R, G, B of the frame, scaled
to [0, 1] (uint8 / 255, exactly what the PL engine receives). Primitive nodes:
  In(c)            input channel c (0 = R, 1 = G, 2 = B); non-negative
  Lin(terms, b)    pointwise sum(coef * node) + b
  Conv(x, K)       3x3 linear filter, zero padding (K[dy+1][dx+1] multiplies x(y+dy, x+dx))
  Relu(x)
Derived (built from the primitives, exact): abs_, max_, min_, clip, median3, mean.
The compiler only needs these four primitives: every ReLU becomes a ReLU stage of the engine,
every linear part is folded into the 3x3 convolutions feeding the stages.
"""
import itertools

import numpy as np

_ids = itertools.count()


class Node(object):
    """`lo` / `hi`: interval bounds of the value for inputs in [0, 1] (zero padding included);
    `nonneg` = lo >= 0, which lets the compiler carry the value in 1 channel instead of 2."""
    __slots__ = ('id', 'op', 'args', 'K', 'b', 'lo', 'hi', 'name')

    def __init__(self, op, args=(), K=None, b=0.0, lo=0.0, hi=0.0, name=None):
        self.id = next(_ids)
        self.op, self.args, self.K, self.b, self.name = op, tuple(args), K, float(b), name
        self.lo, self.hi = float(lo), float(hi)

    @property
    def nonneg(self):
        return self.lo >= 0.0

    # ---- operator sugar
    def __add__(self, o):
        return lin([(1.0, self), (1.0, o)]) if isinstance(o, Node) else lin([(1.0, self)], float(o))

    __radd__ = __add__

    def __sub__(self, o):
        return lin([(1.0, self), (-1.0, o)]) if isinstance(o, Node) else lin([(1.0, self)], -float(o))

    def __rsub__(self, o):
        return lin([(-1.0, self)], float(o))

    def __mul__(self, c):
        return lin([(float(c), self)])

    __rmul__ = __mul__

    def __truediv__(self, c):
        return lin([(1.0 / float(c), self)])

    def __neg__(self):
        return lin([(-1.0, self)])

    def __repr__(self):
        return '%s#%d' % (self.name or self.op, self.id)


def In(c):
    return Node('in', (), b=c, lo=0.0, hi=1.0, name='RGB'[c])


R, G, B = In(0), In(1), In(2)


def lin(terms, b=0.0):
    """Flatten nested Lin nodes so the compiler sees one linear form."""
    flat = {}
    bias = float(b)
    for c, n in terms:
        if n.op == 'lin':
            for cc, nn in n.args:
                flat[nn] = flat.get(nn, 0.0) + c * cc
            bias += c * n.b
        else:
            flat[n] = flat.get(n, 0.0) + c
    items = [(c, n) for n, c in flat.items() if c != 0.0]
    if len(items) == 1 and items[0][0] == 1.0 and bias == 0.0:
        return items[0][1]
    lo = bias + sum(min(c * n.lo, c * n.hi) for c, n in items)
    hi = bias + sum(max(c * n.lo, c * n.hi) for c, n in items)
    return Node('lin', items, b=bias, lo=lo, hi=hi)


def conv(x, K):
    K = np.asarray(K, dtype=np.float64)
    assert K.shape == (3, 3), 'Conv takes a 3x3 kernel; build larger ones by cascading'
    # each tap sees x in [lo, hi], or 0 outside the image (zero padding)
    lo = sum(min(k * x.lo, k * x.hi, 0.0) for k in K.ravel())
    hi = sum(max(k * x.lo, k * x.hi, 0.0) for k in K.ravel())
    return Node('conv', (x,), K=K, lo=lo, hi=hi)


def relu(x):
    return x if x.nonneg else Node('relu', (x,), lo=0.0, hi=max(x.hi, 0.0))


# ---------------------------------------------------------------- derived operators (exact)
def abs_(x):
    return relu(x) + relu(-x)


def max_(a, b):
    return b + relu(a - b)


def min_(a, b):
    return a - relu(a - b)


def clip(x, lo, hi):
    return lo + relu(x - lo) - relu(x - hi)


def median3(a, b, c):
    """median = a + b + c - max - min, with max / min built from ReLUs (exact)."""
    mx_ab, mn_ab = max_(a, b), min_(a, b)
    mx = max_(mx_ab, c)
    mn = min_(mn_ab, c)
    return a + b + c - mx - mn


def mean(*xs):
    return lin([(1.0 / len(xs), x) for x in xs])


# ---------------------------------------------------------------- NumPy reference (float64)
def _shift(img, dy, dx):
    """out(y, x) = img(y + dy, x + dx), zero outside (matches zero padding)."""
    h, w = img.shape
    out = np.zeros_like(img)
    ys, yd = (slice(dy, h), slice(0, h - dy)) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
    xs, xd = (slice(dx, w), slice(0, w - dx)) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
    out[yd, xd] = img[ys, xs]
    return out


def evaluate(node, rgb01, memo=None):
    """Reference value of `node` on an (H, W, 3) float image in [0, 1]."""
    memo = {} if memo is None else memo
    if node.id in memo:
        return memo[node.id]
    if node.op == 'in':
        v = rgb01[:, :, int(node.b)]
    elif node.op == 'lin':
        v = sum(c * evaluate(n, rgb01, memo) for c, n in node.args) + node.b
    elif node.op == 'conv':
        x = evaluate(node.args[0], rgb01, memo)
        v = sum(node.K[dy + 1, dx + 1] * _shift(x, dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    elif node.op == 'relu':
        v = np.maximum(evaluate(node.args[0], rgb01, memo), 0.0)
    else:
        raise ValueError(node.op)
    memo[node.id] = v
    return v
