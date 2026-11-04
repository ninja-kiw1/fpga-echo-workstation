# -*- coding: utf-8 -*-
"""P6-2: 造一个"换过权重"的模型 B —— 把 final 层的 mult 与 offset 取负。

数学上这是**精确取反**：conv 的 requantize 是
    v = acc*M + offset;  round-to-nearest-even(v >> shift);  clamp(qmin,qmax)
把 M、offset 同时取负得到 v' = -v，而"对 |v| 做就近舍入再取符号"是对称的，
qmin/qmax 也是对称的(-127..127)，所以 **logit_B == -logit_A**（逐值精确）。
于是掩膜判据 logit >= thr 会近似取反：血管 ↔ 背景。

参数布局(见 deployment/unet_conv/src/conv_layer.cpp):
    [ weights: taps*cog*cig*8 个 64bit ]
    [ bias:   cop 个 int64 ]
    [ mult:   cop 个 int64(只用低 32 位) ]
    [ offset: cop 个 int64 ]
final 层是 1x1 cin=8 cout=1 -> taps=1 cig=1 cog=1 cop=8:
    权重 8*8=64 B, bias 64 B, mult 64 B, offset 64 B, 共 256 B

用法: python make_model_b.py [源vectors目录] [目标vectors目录] [层名]
默认: vectors_small / vectors_small_alt / final
"""
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "vectors_small")
DST = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "vectors_small_alt")
LAYER = sys.argv[3] if len(sys.argv) > 3 else "final"

LANES = 8


def neg_i64_as_bytes(v):
    """int64 取负，返回 8 字节小端。"""
    n = -v
    if n < -(1 << 63) or n > (1 << 63) - 1:
        raise ValueError("int64 overflow negating %d" % v)
    return struct.pack("<q", n)


def main():
    os.makedirs(os.path.join(DST, "params"), exist_ok=True)
    os.makedirs(os.path.join(DST, "golden"), exist_ok=True)
    for name in ("network.txt", "input.bin", "plan.json"):
        shutil.copyfile(os.path.join(SRC, name), os.path.join(DST, name))
    for name in os.listdir(os.path.join(SRC, "params")):
        shutil.copyfile(os.path.join(SRC, "params", name),
                        os.path.join(DST, "params", name))

    # 找 final 层的形状（从 network.txt）
    cfg = None
    for line in open(os.path.join(SRC, "network.txt")):
        f = line.split()
        if f and f[0] == LAYER:
            cfg = dict(kind=f[1], mode=int(f[4]), cin=int(f[5]), cout=int(f[6]))
            break
    if cfg is None or cfg["kind"] != "CONV":
        raise SystemExit("layer %s not found or not CONV" % LAYER)

    taps = 3 * 3 if cfg["mode"] == 0 else (1 if cfg["mode"] == 1 else 4)
    cig = (cfg["cin"] + LANES - 1) // LANES
    cog = (cfg["cout"] + LANES - 1) // LANES
    weight_words = taps * cog * cig * LANES
    cop = cog * LANES
    want = (weight_words + 3 * cop) * 8

    path = os.path.join(DST, "params", LAYER + ".bin")
    blob = bytearray(open(path, "rb").read())
    if len(blob) != want:
        raise SystemExit("%s: %d bytes, expected %d (taps=%d cig=%d cog=%d cop=%d)"
                         % (path, len(blob), want, taps, cig, cog, cop))

    mult_off = (weight_words + cop) * 8
    off_off = (weight_words + 2 * cop) * 8

    changed = 0
    for c in range(cop):
        # mult: 低 32 位是有效值, 上层只取 ap_int<32>
        m = struct.unpack_from("<q", blob, mult_off + c * 8)[0]
        m32 = m & 0xFFFFFFFF
        if m32 >= 0x80000000:
            m32 -= 0x100000000
        nm32 = -m32
        blob[mult_off + c * 8: mult_off + c * 8 + 8] = struct.pack("<q", nm32)
        if nm32 != m32:
            changed += 1
        # offset: 完整 int64
        f = struct.unpack_from("<q", blob, off_off + c * 8)[0]
        blob[off_off + c * 8: off_off + c * 8 + 8] = neg_i64_as_bytes(f)
        if f != 0:
            changed += 1

    with open(path, "wb") as fh:
        fh.write(bytes(blob))

    print("[modelB] %s -> %s" % (SRC, DST))
    print("         layer=%s 1x1 cin=%d cout=%d  taps=%d cop=%d  params=%d B"
          % (LAYER, cfg["cin"], cfg["cout"], taps, cop, len(blob)))
    print("         mult 段 @%d, offset 段 @%d, 改了 %d 个字段"
          % (mult_off, off_off, changed))
    print("         预期: logit_B == -logit_A (逐值精确)，掩膜近似取反")


if __name__ == "__main__":
    main()
