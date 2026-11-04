# -*- coding: utf-8 -*-
"""registration.py —— F5 连续帧配准（B 级）：PL 逐像素算 |a − b|，PS 分块求和，PC 生成候选、搜索。

板子上跑的还是同一个 30 节点网络，用模型库 5 号参数 reg_absdiff（手工构造：logit ≈ |R − G| / 2，
每个输出像素只依赖同一位置的输入，所以 512 档的一帧可以拼 4×4 = 16 个互不影响的候选）。

  1. 两帧都按板端 128 档的取样方式降到 128×128（512 画布的 [::4, ::4]，取 R 通道）；
  2. 候选平移 (dx, dy)（128 网格的像素）：G(y, x) = B(y − dy, x − dx)，移出重叠区的位置填 A(y, x)，
     差为 0，所以每块的和 = 重叠区的 SAD；
  3. 16 个候选拼成 512×512 一帧（R = A，G = 候选，B = 0），带 FLAG_REG 发给板子，
     PS 回 16 个分块和；PC 除以重叠面积得到平均差（MAD，logit 单位 ≈ 灰度差 / 2），再选最小；
  4. 三步搜索（步长 4、2、1，每步 9 个候选 = 1 帧，范围 ±7）或全搜索（±R，每帧 16 个候选）。
几何变换（平移）和搜索在 PC，代价的逐像素计算在 PL、求和在 PS；报告里照此写清楚。

纯 Python、无 numpy（与 interactive.py 一致）；send 回调由调用方提供（界面 / 验收脚本）。
"""

import proto

T = 128                     # 一块（候选）的边长 = 板端 128 档
GRID = proto.REG_GRID       # 4x4 块
W = proto.IMG_WIDTH
MODEL = 5                   # proto.MODELS[5] = reg_absdiff


def down128(frame512):
    """512x512 RGB bytes -> 128x128 灰度（R 通道的 [::4, ::4]，与板端 128 档取样相同）。"""
    r = frame512[0::3]
    return b"".join(r[(4 * y) * W:(4 * y) * W + W:4] for y in range(T))


def shifted(b, a, dx, dy):
    """候选平面：G(y, x) = B(y - dy, x - dx)；重叠区以外填 A(y, x)。b、a 是 128x128 bytes。"""
    out = bytearray(a)
    x0, x1 = max(0, dx), min(T, T + dx)
    for y in range(max(0, dy), min(T, T + dy)):
        sy = y - dy
        out[y * T + x0:y * T + x1] = b[sy * T + x0 - dx:sy * T + x1 - dx]
    return bytes(out)


def overlap(dx, dy):
    return max(0, T - abs(dx)) * max(0, T - abs(dy))


def compose(a, planes):
    """16 块拼成 512x512 RGB：R = A，G = 各候选（不足 16 块时补 A，差为 0），B = 0。"""
    planes = list(planes) + [a] * (GRID * GRID - len(planes))
    r = bytearray(W * W)
    g = bytearray(W * W)
    for k, p in enumerate(planes):
        ty, tx = (k // GRID) * T, (k % GRID) * T
        for y in range(T):
            o = (ty + y) * W + tx
            r[o:o + T] = a[y * T:(y + 1) * T]
            g[o:o + T] = p[y * T:(y + 1) * T]
    out = bytearray(3 * W * W)
    out[0::3] = r
    out[1::3] = g
    return bytes(out)


def aiparam():
    m = proto.MODELS[MODEL]
    return proto.make_aiparam(proto.AIPARAM_SRC_RAW, m["thr"], m["res"], MODEL)


class Registrar(object):
    """send(frame512_bytes) -> (16 个分块和, 额外信息 dict)。搜索结果和全部代价都记下来供显示。"""

    def __init__(self, a128, b128, send):
        self.a, self.b, self.send = a128, b128, send
        self.cost = {}              # (dx, dy) -> MAD（logit 单位）
        self.frames = 0
        self.info = []

    def evaluate(self, cands):
        todo = [c for c in dict.fromkeys(cands) if c not in self.cost]
        for i in range(0, len(todo), GRID * GRID):
            chunk = todo[i:i + GRID * GRID]
            sums, info = self.send(compose(self.a, [shifted(self.b, self.a, dx, dy) for dx, dy in chunk]))
            self.frames += 1
            self.info.append(info)
            for (dx, dy), s in zip(chunk, sums):
                self.cost[(dx, dy)] = s / float(overlap(dx, dy))
        return [self.cost[c] for c in cands]

    def best(self, cands):
        cs = self.evaluate(cands)
        k = min(range(len(cands)), key=lambda i: (cs[i], abs(cands[i][0]) + abs(cands[i][1]), cands[i]))
        return cands[k]

    def three_step(self, steps=(4, 2, 1)):
        """经典三步搜索（视频编码的块匹配）。返回 (最优平移, 路径)。"""
        cx, cy = 0, 0
        path = [(0, 0)]
        for s in steps:
            cx, cy = self.best([(cx + i * s, cy + j * s) for j in (-1, 0, 1) for i in (-1, 0, 1)])
            path.append((cx, cy))
        return (cx, cy), path

    def full(self, radius=7):
        cands = [(dx, dy) for dy in range(-radius, radius + 1) for dx in range(-radius, radius + 1)]
        return self.best(cands)

    def surface(self, radius=7):
        """全搜索后的代价曲面（行 = dy，列 = dx），没算过的为 None。"""
        return [[self.cost.get((dx, dy)) for dx in range(-radius, radius + 1)] for dy in range(-radius, radius + 1)]


def apply_shift(b, a, dx, dy):
    """对齐后的 B（重叠区外用 A 填，与代价的口径一致）。"""
    return shifted(b, a, dx, dy)


def _self_check():
    errs = []
    a = bytes((x * 7 + y * 3) & 0xFF for y in range(T) for x in range(T))
    b = bytes(range(256)) * (T * T // 256)
    s = shifted(b, a, 3, -2)
    if s[10 * T + 5] != b[12 * T + 2] or s[0 * T + 0] != a[0] or s[127 * T + 127] != a[127 * T + 127]:
        errs.append("shifted() geometry")
    if shifted(b, a, 0, 0) != b or overlap(3, -2) != 125 * 126:
        errs.append("shifted() identity / overlap")
    f = compose(a, [b])
    if f[0] != a[0] or f[1] != b[0] or f[2] != 0 or f[3 * (W * 0 + T) + 1] != a[0]:
        errs.append("compose() layout")
    frame = bytes((i // 3) & 0xFF for i in range(3 * W * W))
    d = down128(frame)
    if len(d) != T * T or d[1] != (4 & 0xFF) or d[T] != ((4 * W) & 0xFF):
        errs.append("down128() sampling")
    return errs


SELF_CHECK_ERRORS = _self_check()
