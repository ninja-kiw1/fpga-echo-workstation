"""opcomp compiler: lower an IR expression onto the full-resolution path of the fixed U-Net.

Target (the same 30-node network the board runs, nothing else changes):
  stage 1  first.block[0]                      conv 3x3, 3 -> 8, ReLU, BN = identity
  stage 2  first.block[3]                      conv 3x3, 8 -> 8, ReLU, BN = identity (shortcut = 0)
  stage 3  up_path[1].conv_bridge_layer        conv 3x3, 8 -> 8, ReLU, BN = identity
  stage 4  up_path[1].conv_layer.block[0]      conv 3x3, (8 up + 8 bridge) -> 8; the up half gets 0
  stage 5  up_path[1].conv_layer.block[3]      conv 3x3, 8 -> 8, ReLU, BN = identity (shortcut = 0)
  output   final                               1x1, 8 -> 1, + bias
Every stage computes channel = ReLU(3x3 linear form of the previous stage's channels + bias).
Values flow as linear forms over the channels of a stage:
  * a ReLU node is materialised as one channel at the stage after its argument is available;
  * a value needed later than it is available is carried: non-negative -> 1 channel (ReLU is the
    identity on it), signed -> 2 channels (relu(v), relu(-v));
  * Conv(x) needs x as a pure 1x1 combination of channels with no constant; otherwise x is carried
    first. This keeps zero padding identical to the reference at the image border (folding two
    3x3 convs into one 5x5, or a constant through a conv, would differ at the edge).
Limits of the engine: 5 stages, 8 channels per stage. Exceeding them is a compile error.
"""
import numpy as np

from ir import Node

STAGES, WIDTH = 5, 8


class CompileError(Exception):
    pass


class Form(object):
    """sum coef * channel_j(p + (dy, dx)) over the channels of `stage`, + const."""
    __slots__ = ('stage', 'terms', 'const')

    def __init__(self, stage, terms=None, const=0.0):
        self.stage, self.terms, self.const = stage, dict(terms or {}), float(const)

    @property
    def window(self):
        return max([max(abs(dy), abs(dx)) for (_, dy, dx) in self.terms] or [0])

    def scaled(self, c):
        return Form(self.stage, {k: v * c for k, v in self.terms.items()}, self.const * c)

    def add(self, other, c=1.0):
        assert other.stage == self.stage
        t = dict(self.terms)
        for k, v in other.terms.items():
            t[k] = t.get(k, 0.0) + c * v
        return Form(self.stage, {k: v for k, v in t.items() if v != 0.0}, self.const + c * other.const)


class Compiler(object):
    def __init__(self, out):
        self.out = out
        self.channels = {0: [('in', 0), ('in', 1), ('in', 2)]}      # stage -> list of definitions
        for s in range(1, STAGES + 1):
            self.channels[s] = []
        self.memo = {}          # (node id, stage) -> Form
        self.carry_memo = {}    # (node id, stage) -> Form after carry
        self.relu_at = {}       # node id -> (stage, channel index)
        self.earliest_memo = {}

    # ---- channel allocation
    def _new_channel(self, stage, form):
        if stage < 1 or stage > STAGES:
            raise CompileError('needs stage %d: the engine has %d ReLU stages' % (stage, STAGES))
        assert form.stage == stage - 1 and form.window <= 1
        chans = self.channels[stage]
        for j, f in enumerate(chans):              # identical definition already there -> reuse
            if f.terms == form.terms and f.const == form.const:
                return j
        if len(chans) >= WIDTH:
            raise CompileError('stage %d needs more than %d channels' % (stage, WIDTH))
        chans.append(form)
        return len(chans) - 1

    # ---- scheduling
    def earliest(self, n):
        if n.id in self.earliest_memo:
            return self.earliest_memo[n.id]
        if n.op == 'in':
            e = 0
        elif n.op == 'lin':
            e = max(self.earliest(a) for _, a in n.args)
        elif n.op == 'conv':
            x = n.args[0]
            ex = self.earliest(x)
            f = self.form_at(x, ex)
            e = ex if (f.window == 0 and f.const == 0.0) else ex + 1
        elif n.op == 'relu':
            e = self.earliest(n.args[0]) + 1
        else:
            raise ValueError(n.op)
        self.earliest_memo[n.id] = e
        return e

    def carry(self, n, stage):
        """n as a pure 1x1 channel combination at `stage` (materialising channels at `stage`)."""
        key = (n.id, stage)
        if key in self.carry_memo:
            return self.carry_memo[key]
        prev = self.form_at(n, stage - 1)
        if n.nonneg:
            j = self._new_channel(stage, prev)
            f = Form(stage, {(j, 0, 0): 1.0})
        else:
            jp = self._new_channel(stage, prev)
            jn = self._new_channel(stage, prev.scaled(-1.0))
            f = Form(stage, {(jp, 0, 0): 1.0, (jn, 0, 0): -1.0})
        self.carry_memo[key] = f
        return f

    def form_at(self, n, stage):
        key = (n.id, stage)
        if key in self.memo:
            return self.memo[key]
        e = self.earliest(n) if n.id in self.earliest_memo or n.op == 'in' else None
        if e is None:
            e = self.earliest(n)
        if stage < e:
            raise CompileError('%r is not available at stage %d' % (n, stage))
        if n.op == 'relu':
            if n.id not in self.relu_at:
                ms = self.earliest(n)
                arg = self.form_at(n.args[0], ms - 1)
                self.relu_at[n.id] = (ms, self._new_channel(ms, arg))
            ms, j = self.relu_at[n.id]
            f = Form(ms, {(j, 0, 0): 1.0}) if stage == ms else self.carry(n, stage)
        elif stage > e and n.op != 'lin':
            f = self.carry(n, stage)
        elif n.op == 'in':
            f = Form(0, {(int(n.b), 0, 0): 1.0})
        elif n.op == 'lin':
            f = Form(stage, {}, n.b)
            for c, a in n.args:
                f = f.add(self.form_at(a, stage), c)
        elif n.op == 'conv':
            x = n.args[0]
            fx = self.form_at(x, stage)
            if fx.window != 0 or fx.const != 0.0:
                fx = self.carry(x, stage)
            t = {}
            for (j, _, _), c in fx.terms.items():
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        k = n.K[dy + 1, dx + 1]
                        if k != 0.0:
                            t[(j, dy, dx)] = t.get((j, dy, dx), 0.0) + c * k
            f = Form(stage, t, 0.0)
        else:
            raise ValueError(n.op)
        self.memo[key] = f
        return f

    def compile(self):
        f = self.form_at(self.out, STAGES)
        if f.window != 0:
            raise CompileError('the output is a 3x3 form at the last stage; the output layer is 1x1')
        self.final = f
        return self

    # ---- report
    def summary(self):
        return dict(channels_per_stage=[len(self.channels[s]) for s in range(1, STAGES + 1)],
                    output_terms=len(self.final.terms))

    # ---- interval bounds (for quantization ranges; guarantees no saturation)
    def bounds(self):
        lo = {0: [0.0, 0.0, 0.0]}
        hi = {0: [1.0, 1.0, 1.0]}
        pre = {}
        for s in range(1, STAGES + 1):
            lo[s], hi[s], pre[s] = [], [], []
            for f in self.channels[s]:
                a = f.const
                b = f.const
                for (j, _, _), c in f.terms.items():
                    a += min(c * lo[s - 1][j], c * hi[s - 1][j])
                    b += max(c * lo[s - 1][j], c * hi[s - 1][j])
                pre[s].append((a, b))
                lo[s].append(max(a, 0.0))
                hi[s].append(max(b, 0.0))
        a = b = self.final.const
        for (j, _, _), c in self.final.terms.items():
            a += min(c * lo[STAGES][j], c * hi[STAGES][j])
            b += max(c * lo[STAGES][j], c * hi[STAGES][j])
        return pre, hi, (a, b)


STAGE_MODULES = ['first.block.0', 'first.block.3', 'up_path.1.conv_bridge_layer.block.0',
                 'up_path.1.conv_layer.block.0', 'up_path.1.conv_layer.block.3']
STAGE_BN = ['first.block.2', 'first.block.5', 'up_path.1.conv_bridge_layer.block.2',
            'up_path.1.conv_layer.block.2', 'up_path.1.conv_layer.block.5']


def anchor_scale(weights, lo=48):
    """Best per-channel weight scale for int8: the largest weight maps to c in [lo, 127] so that all
    weights land on integers (smallest worst rounding error, larger c on ties). -> (c, error in q units)."""
    wmax = max(abs(w) for w in weights)
    best = None
    for c in range(127, lo - 1, -1):
        ws = wmax / c
        err = max(abs(w / ws - round(w / ws)) for w in weights)
        if best is None or err < best[1] - 1e-9:
            best = (c, err)
        if err < 1e-9:
            break
    return best


def to_model(comp, seed=20261009, anchor=True):
    """Build the float U-Net whose output is exactly the compiled expression.

    anchor: for a channel whose weights do not all land on int8 integers when the largest maps to 127,
    put one extra weight of 127 * ws on an input slot that is identically zero (an unused channel of
    the previous stage, or the zero up-sampling half at stage 4). The float output is unchanged; the
    quantizer then picks ws, so every real weight quantizes exactly (quantization-aware anchoring)."""
    import torch
    from models.get_model import get_arch

    torch.manual_seed(seed)
    m = get_arch('unet').eval()
    mods = dict(m.named_modules())

    def identity_bn(bn):
        bn.weight.fill_(1.0)
        bn.bias.zero_()
        bn.running_mean.zero_()
        bn.running_var.fill_(1.0 - bn.eps)

    with torch.no_grad():
        for s, (cname, bname) in enumerate(zip(STAGE_MODULES, STAGE_BN), start=1):
            conv = mods[cname]
            conv.weight.zero_()
            conv.bias.zero_()
            off = conv.weight.shape[1] // 2 if s == 4 else 0      # stage 4: bridge half of the concat
            n_in = conv.weight.shape[1]
            used_in = {off + i for i in range(len(comp.channels[s - 1]))}
            zero_slots = [i for i in range(n_in) if i not in used_in] if s > 1 else []
            for j, f in enumerate(comp.channels[s]):
                for (i, dy, dx), c in f.terms.items():
                    conv.weight[j, off + i, dy + 1, dx + 1] = c
                conv.bias[j] = f.const
                if anchor and f.terms and zero_slots:
                    c_best, _ = anchor_scale(list(f.terms.values()))
                    if c_best < 127:
                        ws = max(abs(v) for v in f.terms.values()) / c_best
                        conv.weight[j, zero_slots[0], 1, 1] = 127 * ws
                        comp.anchors = getattr(comp, 'anchors', 0) + 1
            identity_bn(mods[bname])
        for sc in ('first.shortcut', 'up_path.1.conv_layer.shortcut'):
            mods[sc + '.0'].weight.zero_()
            identity_bn(mods[sc + '.1'])
        up = mods['up_path.1.up_layer.block.0']                   # the low-resolution half: exactly 0
        up.weight.zero_()                                         # (a bias here would quantize to a huge
        up.bias.zero_()                                           #  integer: the weight scale of 0 is ~0)
        m.final.weight.zero_()
        for (j, _, _), c in comp.final.terms.items():
            m.final.weight[0, j, 0, 0] = c
        m.final.bias.fill_(comp.final.const)
    return m
