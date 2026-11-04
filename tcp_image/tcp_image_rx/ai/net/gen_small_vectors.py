# -*- coding: utf-8 -*-
"""P4-1: 生成 32x32 的整网测试向量(全分辨率 512 的 1/16)。

- network.txt: 逐行的 h,w 除以 16 (512->32, 256->16, 128->8), 其余字段原样。
- input.bin  : 取 im0002 的 512x512 输入, 每 16 像素/16 行抽 1 个(最近邻), 得到 32x32x8 int16。
- params/    : 原样复制(权重与分辨率无关)。
- 末尾模拟 unet_fpga.py 的"缓冲复用"分配, 确认峰值能塞进 128KB BRAM(8192 个 128bit 字)。

用法: python gen_small_vectors.py [图像名] [输出目录]   (默认 im0002 / vectors_small)
"""
import json
import os
import shutil
import struct
import sys

PLD = r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/unet_network"
SRC = os.path.join(PLD, "vectors")
IMAGE = sys.argv[1] if len(sys.argv) > 1 else "im0002"
IMG = os.path.join(SRC, "images", IMAGE)
OUT = sys.argv[2] if len(sys.argv) > 2 else r"E:/fpga/my_project/tcp_image_rx/ai/net/vectors_small"

LANES = 8
SCALE = 16


def groups(c):
    return (c + LANES - 1) // LANES


def parse_line(line):
    f = line.split()
    return f, dict(name=f[0], kind=f[1], in0=f[2], in1=f[3],
                   mode=int(f[4]), ca=int(f[5]), cb=int(f[6]),
                   h=int(f[7]), w=int(f[8]), relu=int(f[9]), shift=int(f[10]),
                   qmin=int(f[11]), qmax=int(f[12]), m0=int(f[13]), m1=int(f[14]))


def out_shape(n):
    oh, ow, oc = n["h"], n["w"], n["ca"]
    if n["kind"] == "CONV":
        oc = n["cb"]
        if n["mode"] == 2:
            oh, ow = 2 * oh, 2 * ow
    elif n["kind"] == "ELT":
        if n["mode"] == 1:
            oc = n["ca"] + n["cb"]
        elif n["mode"] == 2:
            oh, ow = oh // 2, ow // 2
    return oh, ow, groups(oc) * LANES


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(OUT, "params"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "golden"), exist_ok=True)

    nodes = []
    with open(os.path.join(SRC, "network.txt")) as f:
        for line in f:
            if not line.strip():
                continue
            fields, n = parse_line(line)
            assert n["h"] % SCALE == 0 and n["w"] % SCALE == 0, line
            n["h"] //= SCALE
            n["w"] //= SCALE
            fields[7] = str(n["h"])
            fields[8] = str(n["w"])
            nodes.append((fields, n))

    with open(os.path.join(OUT, "network.txt"), "w") as f:
        for fields, _ in nodes:
            f.write(" ".join(fields) + "\n")
    print("[net] wrote network.txt, %d nodes, sizes scaled by 1/%d" % (len(nodes), SCALE))

    # ---- input: 512x512x8 int16 -> 32x32x8 int16 ----
    src_p = os.path.join(IMG, "input.bin")
    raw = open(src_p, "rb").read()
    assert len(raw) == 512 * 512 * 8 * 2, "unexpected input.bin size %d" % len(raw)
    vals = struct.unpack("<%dh" % (512 * 512 * 8), raw)
    off = int(os.environ.get("P4_OFFSET", "0"))   # 抽样相位: 换一组就是"另一张输入"
    out = []
    for y in range(32):
        for x in range(32):
            sy = (y * SCALE + off) % 512
            sx = (x * SCALE + off) % 512
            base = (sy * 512 + sx) * 8
            out.extend(vals[base:base + 8])
    buf = struct.pack("<%dh" % len(out), *out)
    with open(os.path.join(OUT, "input.bin"), "wb") as f:
        f.write(buf)
    print("[in]  input.bin %d -> %d bytes (32x32x8 int16)" % (len(raw), len(buf)))

    # ---- params ----
    n_par = 0
    for name in os.listdir(os.path.join(SRC, "params")):
        shutil.copyfile(os.path.join(SRC, "params", name), os.path.join(OUT, "params", name))
        n_par += 1
    print("[par] copied %d param files" % n_par)

    # ---- 缓冲复用 + first-fit 分配, 产出 plan.json ----
    last_use = {}
    for i, (_, n) in enumerate(nodes):
        for nm in (n["in0"], n["in1"]):
            if nm != "-":
                last_use[nm] = i
    final = nodes[-1][1]["name"]

    # 单位为"128bit 字"。分配顺序固定 = 节点顺序, 板端照抄同一份计划。
    free = []          # [(offset_words, words)]
    bump = [0]

    def words_of(shp):
        return shp[0] * shp[1] * shp[2] // LANES

    def alloc(w):
        for i, (o, sz) in enumerate(free):
            if sz >= w:
                free.pop(i)
                if sz > w:
                    free.append((o + w, sz - w))
                return o
        o = bump[0]
        bump[0] += w
        return o

    def release(o, w):
        free.append((o, w))
        free.sort()
        merged = []
        for o2, w2 in free:
            if merged and merged[-1][0] + merged[-1][1] == o2:
                merged[-1] = (merged[-1][0], merged[-1][1] + w2)
            else:
                merged.append((o2, w2))
        del free[:]
        free.extend(merged)

    stamp = {}
    plan_nodes = []
    for i, (_, n) in enumerate(nodes):
        shp = out_shape(n)
        w = words_of(shp)
        off = alloc(w)
        stamp[n["name"]] = off
        plan_nodes.append(dict(name=n["name"], kind=n["kind"], off_words=off,
                               words=w, h=shp[0], w_=shp[1], c_pad=shp[2]))
        for nm in (n["in0"], n["in1"]):
            if nm != "-" and last_use[nm] == i and nm != final:
                bw = words_of(out_shape(next(m for _, m in nodes if m["name"] == nm)))
                release(stamp[nm], bw)

    tensor_peak = bump[0]

    # ---- 校验: 任何两个"同时存活"的缓冲不得重叠 ----
    # 存活区间 = [定义节点, 最后一次被消费的节点](final 一直活到结束)
    spans = []
    for i, p in enumerate(plan_nodes):
        lu = last_use.get(p["name"], i)
        spans.append((i, lu, p["off_words"], p["words"], p["name"]))
    for a in range(len(spans)):
        for b in range(a + 1, len(spans)):
            s1, e1, o1, w1, n1 = spans[a]
            s2, e2, o2, w2, n2 = spans[b]
            if e1 < s2 or e2 < s1:
                continue
            if o1 < o2 + w2 and o2 < o1 + w1:
                raise AssertionError("plan overlap: %s[%d,%d)@%d+%d vs %s[%d,%d)@%d+%d"
                                     % (n1, s1, e1, o1, w1, n2, s2, e2, o2, w2))
    print("[chk] no overlapping live buffers")
    # 参数区: 放在张量区之后, 大小取最大 params 文件(按 128bit 字上取整)
    max_par_bytes = max(os.path.getsize(os.path.join(OUT, "params", f))
                        for f in os.listdir(os.path.join(OUT, "params")))
    par_words = (max_par_bytes + 15) // 16
    param_off = tensor_peak
    total = param_off + par_words

    with open(os.path.join(OUT, "plan.json"), "w") as f:
        json.dump(dict(bram_base=0xB0000000, bram_words=8192,
                       tensor_peak_words=tensor_peak,
                       param_off_words=param_off, param_words=par_words,
                       max_param_bytes=int(max_par_bytes),
                       total_words=total, nodes=plan_nodes), f, indent=1)

    print("[mem] tensor peak=%d words (%d KB); param @%d words(%dB); total=%d / 8192"
          % (tensor_peak, tensor_peak * 16 // 1024, param_off, max_par_bytes, total))
    for p in plan_nodes:
        print("      %-38s off=%5d words  %dx%d c%d" %
              (p["name"], p["off_words"], p["h"], p["w_"], p["c_pad"]))
    assert total <= 8192, "does not fit in the 128KB tensor BRAM!"


if __name__ == "__main__":
    main()
