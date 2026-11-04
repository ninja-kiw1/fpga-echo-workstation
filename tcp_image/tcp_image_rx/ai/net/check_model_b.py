# -*- coding: utf-8 -*-
"""校验"换权重"模型 B 的性质: logit_B == -logit_A(逐值精确), 掩膜近似取反。"""
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.join(HERE, "vectors_small", "golden", "final.bin")
B = os.path.join(HERE, "vectors_small_alt", "golden", "final.bin")
THR = -3


def lane0(path):
    raw = open(path, "rb").read()
    vals = struct.unpack("<%dh" % (len(raw) // 2), raw)
    return [vals[p * 8] for p in range(len(vals) // 8)]      # 每像素的 lane 0


a = lane0(A)
b = lane0(B)
n = len(a)

neg_ok = sum(1 for k in range(n) if b[k] == -a[k])
print("模型 A logit 范围 [%d..%d]" % (min(a), max(a)))
print("模型 B logit 范围 [%d..%d]" % (min(b), max(b)))
print("logit_B == -logit_A 的像素: %d/%d  ->  %s"
      % (neg_ok, n, "PASS" if neg_ok == n else "FAIL"))
bad = [k for k in range(n) if b[k] != -a[k]]
if bad:
    k = bad[0]
    print("  首个不符: pixel %d  A=%d B=%d" % (k, a[k], b[k]))

va = sum(1 for v in a if v >= THR)
vb = sum(1 for v in b if v >= THR)
both = sum(1 for k in range(n) if a[k] >= THR and b[k] >= THR)
print("掩膜(thr=%d): A 血管 %d/%d (%.1f%%), B 血管 %d/%d (%.1f%%), 同时命中 %d"
      % (THR, va, n, 100.0 * va / n, vb, n, 100.0 * vb / n, both))
print("A 与 B 掩膜相同的像素: %d/%d (%.1f%%) —— 越接近 0 说明功能确实换掉了"
      % (sum(1 for k in range(n) if (a[k] >= THR) == (b[k] >= THR)), n,
         100.0 * sum(1 for k in range(n) if (a[k] >= THR) == (b[k] >= THR)) / n))
print("MODELB_CHECK_DONE")
