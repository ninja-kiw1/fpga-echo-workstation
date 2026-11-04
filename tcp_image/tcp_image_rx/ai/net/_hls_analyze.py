# -*- coding: utf-8 -*-
"""HLS 算子分析: 每个节点要算多少 MAC / PL 实际跑了多久 / 折合每周期几个 MAC。

数据来源:
  * 网络结构   PLD 的 vectors/network.txt
  * 每节点实测 ap_us  ai/net/_profall_ai.log 里的 [prof] per-node 表
  * PL 主频    101.375992 MHz (pl0_ref)
结论用来判断: 是**算力打满**(那只能加并行度), 还是**被开销吃掉**(那先杀开销)。
"""
import os
import re
import sys

NET = r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/unet_network/vectors/network.txt"
PROF = r"E:/fpga/my_project/tcp_image_rx/ai/net/_profall_ai.log"
FCLK = 101.375992e6
FIELDS = ('name', 'kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'h', 'w',
          'relu', 'shift', 'qmin', 'qmax', 'm0', 'm1')

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:                                 # noqa: BLE001
    pass


def load_net():
    nodes = []
    for line in open(NET):
        if not line.strip():
            continue
        f = line.split()
        n = dict(zip(FIELDS, f))
        for k in FIELDS[4:]:
            n[k] = int(n[k])
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
        n.update(out_h=oh, out_w=ow, out_c=oc)
        nodes.append(n)
    return nodes


def parse_prof():
    """从日志里取 **512 档**的 per-node 表 (idx -> ap_us)。

    日志里 shift=0 与 shift=4 各有一份 per-node 表, 后者数值小 100 倍 ——
    按"最大值"挑, 别按最后一块挑(踩过)。
    """
    txt = open(PROF, errors="replace").read()
    blocks = re.findall(r"\[prof\] per-node:.*?\n((?:\[prof\]\s+\d+.*\n)+)", txt)
    best = {}
    for blk in blocks:
        out = {}
        for line in blk.strip().splitlines():
            m = re.match(r"\[prof\]\s+(\d+)\s+(\d+)\s+(\d+)", line.strip())
            if m:
                out[int(m.group(1))] = int(m.group(2))
        if sum(out.values()) > sum(best.values()):
            best = out
    return best


def main():
    nodes = load_net()
    ap = parse_prof()
    if not ap:
        print("!! 没解析到 per-node 表, 先跑 _prof_all.py ai")
        return 1

    print("=== 逐节点: 计算量 vs 实测 PL 时间 ===")
    print("  %-36s %-5s %5s %5s %10s %9s %8s" %
          ("节点", "类型", "cin", "cout", "MAC", "ap_us", "MAC/cyc"))
    tot_mac = tot_us = 0
    rows = []
    for i, n in enumerate(nodes):
        if i not in ap or n["kind"] == "INPUT":
            continue
        if n["kind"] == "CONV":
            kk = 3 if n["mode"] == 0 else (1 if n["mode"] == 1 else 2)
            mac = n["out_h"] * n["out_w"] * n["out_c"] * n["ca"] * kk * kk
        else:
            # ELT: 每个输出像素读 1~2 路、做 1 次乘加/比较
            mac = n["out_h"] * n["out_w"] * n["out_c"] * (2 if n["mode"] != 2 else 1)
        us = ap[i]
        cyc = us * 1e-6 * FCLK
        rows.append((n["name"], n["kind"], n["ca"], n["out_c"], mac, us,
                     mac / cyc if cyc > 0 else 0))
        tot_mac += mac
        tot_us += us

    for r in sorted(rows, key=lambda x: -x[5]):
        print("  %-36s %-5s %5d %5d %10d %9d %8.1f" % r)

    tot_cyc = tot_us * 1e-6 * FCLK
    print("\n  合计: %.1f MMAC, %.1f ms, %.1f M周期 -> **平均 %.1f MAC/周期**"
          % (tot_mac / 1e6, tot_us / 1000.0, tot_cyc / 1e6, tot_mac / tot_cyc))

    print("\n=== 逐档位(tp=3x3/1x1/转置/池化) 的平均效率 ===")
    bymode = {}
    for i, n in enumerate(nodes):
        if i not in ap or n["kind"] == "INPUT":
            continue
        key = ("CONV3" if (n["kind"] == "CONV" and n["mode"] == 0) else
               "CONV1" if (n["kind"] == "CONV" and n["mode"] == 1) else
               "TCONV2" if n["kind"] == "CONV" else
               "MaxPool" if n["mode"] == 2 else "Add/Concat")
        kk = 3 if (n["kind"] == "CONV" and n["mode"] == 0) else 1
        if n["kind"] == "CONV":
            mac = n["out_h"] * n["out_w"] * n["out_c"] * n["ca"] * kk * kk
        else:
            mac = n["out_h"] * n["out_w"] * n["out_c"] * (2 if n["mode"] != 2 else 1)
        d = bymode.setdefault(key, [0, 0.0])
        d[0] += mac
        d[1] += ap[i]
    for k, (m, u) in sorted(bymode.items(), key=lambda x: -x[1][1]):
        cyc = u * 1e-6 * FCLK
        print("  %-10s %8.1f MMAC  %8.1f ms  %7.1f MAC/周期  (占 %4.1f%% 时间)"
              % (k, m / 1e6, u / 1000.0, m / cyc, 100.0 * u / tot_us))

    print("\n=== 与理论峰值比 ===")
    print("  现状 COMPUTE 循环 II=1、每周期 8 输出通道 x 8 输入 lane = 64 MAC/周期")
    print("  => 平均 %.1f / 64 = **%.0f%% 的峰值利用率**"
          % (tot_mac / tot_cyc, 100.0 * (tot_mac / tot_cyc) / 64))
    print("  512 档整网 %.1f ms; 若算力翻倍(128 MAC/周期)理论上 -> %.1f ms"
          % (tot_us / 1000.0, tot_us / 1000.0 / 2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
