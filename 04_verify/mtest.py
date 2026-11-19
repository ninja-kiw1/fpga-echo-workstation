# -*- coding: utf-8 -*-
"""心超 112 直跑：基准 / 回归一体的测量工具。

    python mtest.py base            # 跑基线并留 golden（logits + 汇总 json）
    python mtest.py new             # 跑改动版，与 golden 逐项对比
    python mtest.py base --reps 5

产物（都在本目录 golden/ 下）：
    lg_<tag>_<n>_m<model>_<payload>_s<seed>.bin     每例的 logit
    sum_<tag>.json                                  每例的 ai 中位数 + logit 校验和 + 扇形内外的掩膜统计

负载两种：
    syn   合成亮盘（与历史 golden 同源，可跨版本对比）
    real  真实 4CH 帧（112x112 灰度，扇形外纯黑）—— 裁剪版真正的考场
"""
import hashlib
import json
import os
import socket
import statistics
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proto                                                        # noqa: E402

# ---- 放宽 v6c 尺寸白名单（板端 v9 已支持任意 8 的倍数；本机 proto.py 是旧的）----
def _pack_v6c(payload, w, kind=proto.V6C_KIND_GRAY, flags=None, model0=2, model1=4, thr=1,
              track=proto.V6C_TRACK_NONE, extra=(), pp=None):
    if flags is None:
        flags = proto.FLAG_STATS | proto.FLAG_C_LOGIT
    if pp is not None:
        flags |= proto.FLAG_C_PP
    if len(payload) != w * w * kind:
        raise ValueError("v6c payload must be %d bytes, got %d" % (w * w * kind, len(payload)))
    if w % 8 or w > 512:
        raise ValueError("v6c size must be a multiple of 8, <= 512, got %d" % w)
    progs = b"".join(struct.pack("<BBBb", m, src, dst, int(t)) for m, src, dst, t in extra)
    if flags & proto.FLAG_C_PP:
        progs += proto.pack_pp(**(pp or {}))
    return struct.pack(proto._CTRL_FMT_V6C, proto.CTRL_MAGIC_V6C, kind, track, flags & 0xFFFF,
                       model0, model1, int(thr), len(extra), w, w,
                       proto.crc16_ccitt_false(payload)) + progs + bytes(payload)


proto.pack_v6c = _pack_v6c

HOST, PORT = "192.168.1.254", 5060
N = 112
_HERE = os.path.dirname(os.path.abspath(__file__))
GOLD = os.path.join(_HERE, "golden")
PANEL = os.path.join(_HERE, "..", "..", "..",
                     "fpga-echo-workstation", "echo", "results", "quant", "lv_r128",
                     "val_panels_worst3_median3_best3.png")


# ---------------------------------------------------------------- 负载
def frame_syn(seed):
    rng = np.random.RandomState(seed)
    yy, xx = np.mgrid[0:N, 0:N]
    body = (((yy - 56) / 34.0) ** 2 + ((xx - 52) / 26.0) ** 2) < 1.0
    f = np.full((N, N), 25.0)
    f[body] = 150.0
    f += rng.normal(0, 6, (N, N))
    return np.clip(f, 0, 255).astype(np.uint8)


_REAL = {}


def real_frame(name="real"):
    """真实 4CH 帧（112x112，扇形外已清零 = EchoNet 原始帧的输入条件）。
      real     : 37° 那台机器（历史基线用的，别改名，golden 对的是它）
      real43   : 另一帧 43° 的 —— 专门用来验"几何逐帧自标定"扛不扛得住换张角
    """
    if name not in _REAL:
        fn = {"real": "real112.raw", "real43": "real112_B_43deg.raw"}[name]
        with open(os.path.join(_HERE, fn), "rb") as f:
            _REAL[name] = np.frombuffer(f.read(), np.uint8).reshape(N, N).copy()
    return _REAL[name]


def fan_mask(n=N):
    """与 03_脚本/fan_savings.py 同一套几何：实测 4CH —— 顶点在画外 (0.498W,-0.328H)、
    半张角 tan=0.333、外弧半径 1.33H。外扩 2 像素（裁剪版给解码器的余量）。"""
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float64)
    cx, ay = 0.498 * n, -0.328 * n
    inside = (np.abs(xx - cx) <= (yy - ay) * 0.333) & (yy >= ay)
    inside &= np.hypot(xx - cx, yy - ay) <= 1.33 * n
    return inside


def payload(n, kind, seed, which="syn"):
    f = frame_syn(seed) if which == "syn" else real_frame(which)
    if n == 128:
        f = np.pad(f, ((8, 8), (8, 8)))
    elif n != N:
        raise ValueError("只支持 112 / 128")
    if kind == 1:
        return f.tobytes()
    return np.repeat(f[..., None], 3, -1).tobytes()


# ---------------------------------------------------------------- 通信
def round_trip(sock, n, model, kind, seed, which):
    pl = payload(n, kind, seed, which)
    pkt = proto.pack_v6c(pl, n, kind=kind, model0=model, thr=1,
                         flags=proto.FLAG_STATS | proto.FLAG_C_LOGIT)
    import time
    t0 = time.perf_counter()
    sock.sendall(pkt)

    def recv(k):
        buf = b""
        while len(buf) < k:
            b = sock.recv(k - len(buf))
            if not b:
                raise RuntimeError("板端断开（收到 %d/%d 字节）" % (len(buf), k))
            buf += b
        return buf

    st = proto.unpack_stats(recv(proto.AI_STAT_BYTES))
    if proto.expects_reg_block(st):
        recv(proto.REG_BLOCK_BYTES)
    d = recv(n * n + 2)
    if proto.crc16_ccitt_false(d[:n * n]) != (d[n * n] | (d[n * n + 1] << 8)):
        raise RuntimeError("logit CRC 不对")
    return st, d[:n * n], (time.perf_counter() - t0) * 1000.0


CASES = [
    ("112_m16_syn", 112, 16, "syn"),
    ("112_m16_real", 112, 16, "real"),
    ("112_m3_real", 112, 3, "real"),
    ("112_m4_real", 112, 4, "real"),
    ("112_m16_43", 112, 16, "real43"),
    ("128_m16_real", 128, 16, "real"),
]


def content_mask(name, n):
    """负载里"有内容"的地方（= 扇形本身）。这是比"几何扇形"更硬的判据：
    只要输入在这一格非零，logit 就必须和基线**逐字节相同**。"""
    kind = proto.V6C_KIND_GRAY
    pl = np.frombuffer(payload(n, kind, 0, CASES_DICT[name][3]), np.uint8).reshape(n, n)
    m = pl != 0
    p = np.pad(m, 1)
    return p[0:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, 0:-2] | p[1:-1, 2:] | m


CASES_DICT = {c[0]: c for c in CASES}


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "base"
    reps = 7
    if "--reps" in sys.argv:
        reps = int(sys.argv[sys.argv.index("--reps") + 1])
    os.makedirs(GOLD, exist_ok=True)
    fan = fan_mask()
    fan112 = fan if True else None

    sock = socket.create_connection((HOST, PORT), timeout=10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(60)
    summary = {}
    try:
        for name, n, model, which in CASES:
            ai, walls = [], []
            first = None
            for i in range(reps):
                st, logit, wall = round_trip(sock, n, model, proto.V6C_KIND_RGB, i, which)
                ai.append(st.get("ai_us", 0) / 1000.0)
                walls.append(wall)
                if i == 0:
                    first = (st, logit)
                    with open(os.path.join(GOLD, "lg_%s_%s.bin" % (tag, name)), "wb") as f:
                        f.write(logit)
            st, logit = first
            lg = np.frombuffer(logit, np.int8).reshape(n, n)
            thr = 1
            pos = lg >= thr
            m = fan if n == 112 else np.pad(fan, ((8, 8), (8, 8)))
            rec = dict(
                name=name, n=n, model=model, payload=which,
                ai_med=round(statistics.median(ai), 3), ai_min=round(min(ai), 3),
                ai_max=round(max(ai), 3), wall_med=round(statistics.median(walls), 3),
                sha=hashlib.sha256(logit).hexdigest()[:16],
                logit_min=int(lg.min()), logit_max=int(lg.max()),
                pos_total=int(pos.sum()),
                pos_in_fan=int((pos & m).sum()),
                pos_out_fan=int((pos & ~m).sum()),
                pos_in_content=int((pos & content_mask(name, n)).sum()),
                pos_out_content=int((pos & ~content_mask(name, n)).sum()),
                ver=st.get("version"), ai_res=st.get("ai_res"), vessels=st.get("vessels"),
            )
            summary[name] = rec
            print("%-14s ai %6.2f ms (%.2f~%.2f)  logit[%4d,%4d]  >=1: %5d "
                  "(扇形内 %5d / 外 %4d)  sha %s"
                  % (name, rec["ai_med"], rec["ai_min"], rec["ai_max"],
                     rec["logit_min"], rec["logit_max"], rec["pos_total"],
                     rec["pos_in_fan"], rec["pos_out_fan"], rec["sha"]))
    finally:
        sock.close()

    with open(os.path.join(GOLD, "sum_%s.json" % tag), "w") as f:
        json.dump(summary, f, indent=2)
    print("\n汇总写盘 golden/sum_%s.json" % tag)

    # ---- 与既有 golden 比 ----
    ref_tag = os.environ.get("MT_REF", "base")
    prev = os.path.join(GOLD, "sum_%s.json" % ref_tag)
    if tag != ref_tag and os.path.exists(prev):
        base = json.load(open(prev))
        print("\n===== 与基线对比 =====")
        bad = 0
        for name in summary:
            a, b = base.get(name), summary[name]
            if not a:
                print("  %-14s 基线没有这一项，跳过" % name)
                continue
            da = b["ai_med"] - a["ai_med"]
            pa = os.path.join(GOLD, "lg_%s_%s.bin" % (ref_tag, name))
            la = np.frombuffer(open(pa, "rb").read(), np.int8).reshape(a["n"], a["n"])
            lb = np.frombuffer(open(os.path.join(GOLD, "lg_%s_%s.bin" % (tag, name)), "rb").read(),
                               np.int8).reshape(b["n"], b["n"])
            m = content_mask(name, a["n"])
            diff_in = int((la[m] != lb[m]).sum())
            diff_out = int((la[~m] != lb[~m]).sum())
            mask_in = int(((la >= 1) != (lb >= 1))[m].sum())
            mask_out_new = int(((lb >= 1) & ~m).sum())
            ok = (diff_in == 0) and (mask_in == 0)
            bad += 0 if ok else 1
            print("  %-14s ai %6.2f -> %6.2f (%+5.2f ms, %+5.1f%%)  "
                  "内容内差异 %d/%d  内容外差异 %d  掩膜差异(内) %d  掩膜(外)新版 %d  %s"
                  % (name, a["ai_med"], b["ai_med"], da, 100.0 * da / a["ai_med"],
                     diff_in, int(m.sum()), diff_out, mask_in, mask_out_new,
                     "PASS" if ok else "FAIL"))
        print("\n%s" % ("全部 PASS" if bad == 0 else "%d 项 FAIL" % bad))
        return 0 if bad == 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
