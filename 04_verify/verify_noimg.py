# -*- coding: utf-8 -*-
"""WS_OPT_V2「少回图」上板验证：等价性 + 各勾选组合的往返耗时。

    python verify_noimg.py               # 默认连 192.168.1.254:5060
    python verify_noimg.py <ip> <port>

做两件事：
  1. **等价性**（4 张真实 512 图）：板端回的 proc 是否等于发出去的原图；
     本地 mask_rgb / overlay_rgb 是否等于板端回传的那两幅。全部逐字节相同才算 PASS。
  2. **耗时**：同一张图，按界面上四种「回传内容」组合各跑 5 次取中位数，
     对比「老口径（板端回图）」与「新口径（只回 logit，本地生成）」。

口径提醒：老口径 = 界面上勾了什么就回什么；新口径 = 预处理图 / 掩膜 / 叠加图 全部本地生成，
回传只剩 logit 一幅。两者显示出来的像素**逐字节相同**（见第 1 步）。
"""
import glob
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "07_ui"))
import numpy as np
import workstation as W
from PIL import Image

proto = W.proto
S = os.path.join(HERE, "..", "07_ui", "echo", "samples")
AI_THR = proto.AIPARAM_THR_DEFAULT          # AI 判血管的门限在 aiparam 里（不是 v4 头里的 thr）
HDR_THR = 1                                 # v4 控制头里的分割门限（AI 不用它）

CASES = [
    ("只「预处理图」",            proto.FLAG_ECHO_PROC),
    ("预处理 + 叠加（默认 AI）",  proto.FLAG_ECHO_PROC | proto.FLAG_ECHO_OVER),
    ("预处理 + 叠加 + 掩膜",      proto.FLAG_ECHO_PROC | proto.FLAG_ECHO_OVER | proto.FLAG_ECHO_MASK),
    ("预处理 + 叠加 + 掩膜 + 置信度", proto.FLAG_ECHO_PROC | proto.FLAG_ECHO_OVER | proto.FLAG_ECHO_MASK
                                       | proto.FLAG_ECHO_LOGIT),
]


def frames():
    out = []
    for p in sorted(glob.glob(os.path.join(S, "*.png"))):
        if "_gt" in p:
            continue
        a = np.asarray(Image.open(p).convert("L").resize((512, 512), Image.BILINEAR))
        out.append((os.path.basename(p), np.repeat(a[..., None], 3, -1).tobytes()))
    return out


def run_once(link, img, flags):
    """发一帧、把回传图像全收下来 -> (统计块, {名字: 786432 B}, 往返 ms, 回传字节数)。"""
    flags |= proto.FLAG_STATS                  # 靠统计块的 version 判断回哪几幅
    aiparam = proto.make_aiparam(proto.AIPARAM_SRC_RAW, AI_THR, 512, 0)
    t0 = time.perf_counter()
    link.send_frame(img, mode=5, flags=flags, aiparam=aiparam, thr=HDR_THR)
    st = proto.unpack_stats(link._recv_exact(proto.AI_STAT_BYTES))
    if proto.expects_reg_block(st):
        link._recv_exact(proto.REG_BLOCK_BYTES)
    views, nbytes = {}, 0
    for name in proto.expected_images(flags, st):
        if name == "morph":
            continue
        raw = link._recv_exact(789504)
        body = bytearray()
        for i in range(0, len(raw), 1542):     # 每包 6 B 头 + 1536 B 体
            body += raw[i + 6:i + 1542]
        views[name] = bytes(body[:786432])
        nbytes += len(raw)
    ms = (time.perf_counter() - t0) * 1000
    return st, views, ms, nbytes


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.254"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5060
    link = W.ImageLink(host, port, timeout=60)
    fs = frames()

    print("== 1. 等价性（%d 张真实图） ==" % len(fs))
    ok = True
    for name, img in fs:
        st, v, ms, nb = run_once(link, img, proto.FLAG_ECHO_PROC | proto.FLAG_ECHO_OVER
                                 | proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_LOGIT)
        plane = W.logit_plane(v["logit"])
        checks = [("proc==原图", v["proc"] == img),
                  ("mask 本地==板端", W.mask_rgb(plane, AI_THR) == v["mask"]),
                  ("overlay 本地==板端", W.overlay_rgb(img, plane, AI_THR) == v["overlay"])]
        ok = ok and all(c[1] for c in checks)
        print("  %-28s %s" % (name, "  ".join("%s %s" % (k, "OK" if x else "差异") for k, x in checks)))
    print("  结论：%s\n" % ("PASS（三幅图都能本地逐字节复现）" if ok else "FAIL"))

    img = fs[0][1]
    print("== 2. 往返耗时（每档 5 次取中位，同一张图、同一块板） ==")
    print("  %-30s %10s %10s %10s %10s" % ("回传内容", "改前", "改后", "省", "回传字节"))
    for label, flags in CASES:
        old = [run_once(link, img, flags)[2] for _ in range(5)]
        new_flags = ((flags & ~(proto.FLAG_ECHO_PROC | proto.FLAG_ECHO_MASK | proto.FLAG_ECHO_OVER))
                     | proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS)
        same = (new_flags == flags)
        new = [run_once(link, img, new_flags)[2] for _ in range(5)]
        nb = run_once(link, img, new_flags)[3]
        print("  %-30s %8.1f ms %8.1f ms %8.1f ms %8d B%s"
              % (label, statistics.median(old), statistics.median(new),
                 statistics.median(old) - statistics.median(new), nb,
                 "   (这一档本来就只回一幅，优化不生效)" if same else ""))


if __name__ == "__main__":
    main()
