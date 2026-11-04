# -*- coding: utf-8 -*-
"""AI 识别提速调研 —— 解析模型（**GDMA 落地之后的新单价**）。

逐行镜像 src/unet_ai.c 的条带调度(run_conv_body / run_elt_body), 再用**实测单价**换算:

    get  = 字节/1290MB/s  +  条带数 x 25µs  +  字节/2320MB/s
           \\__ DMA ___/     \\_ 每笔固定 _/     \\_ bounce->张量 memcpy _/
    put  = 字节/1085MB/s            (仍走 CPU + Normal-NC 写合并)
    ap   = 实测(PL 计算, 不能解析建模)

单价来源(2026-10-06 实测):
  * gdmatest.ko: BRAM->DDR 1290 MB/s(256KB 一笔)、每笔固定开销 ~12µs(模块内)
  * 新埋点 `[prof] get DMA detail`: 512 档 826 笔 / ioctl 88.2ms / bounce memcpy 36.9ms
    → 反推每笔固定 ~25µs(含 ioctl + dma_map/unmap + 完成唤醒), memcpy 有效 2320 MB/s
  * 256 档同样反推得 968 MB/s(含开销) 与 2326 MB/s, 与 512 档一致

用法: python _prof_model.py
"""
import os

VEC = r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/unet_network/vectors"
FIELDS = ('name', 'kind', 'in0', 'in1', 'mode', 'ca', 'cb', 'h', 'w',
          'relu', 'shift', 'qmin', 'qmax', 'm0', 'm1')

WORD = 16
GDMA_BW = 1290e6          # B/s, BRAM->DDR 纯传输(实测)
GDMA_FIXED_US = 25.0      # µs, 每笔 DMA 的固定开销(实测反推)
MEMCPY_BW = 2320e6        # B/s, bounce->张量 memcpy 有效带宽(实测反推)
PUT_BW = 1085e6           # B/s, CPU 写 BRAM(Normal-NC 写合并, 实测)
AP_BASE_CLK = 101.375992e6

# 实测基线(2026-10-06, 3 次迭代稳态)
MEAS = dict(
    res512=dict(total=777.3, ap=531.4, get=125.4, put=108.3, other=12.2,
                strips=826, get_words=5341184, put_words=7322564),
    res256=dict(total=189.2, ap=128.4, get=31.4, put=25.8, other=3.6, strips=213),
    res128=dict(total=48.6, ap=33.0, get=8.1, put=6.2, other=1.3, strips=66),
    res64=dict(total=13.3, ap=9.0, get=2.3, put=1.6, other=0.5, strips=29),
    res32=dict(total=4.2, ap=2.7, get=0.9, put=0.4, other=0.2, strips=29),
)


def load_net():
    nodes = []
    for line in open(os.path.join(VEC, "network.txt")):
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


def param_bytes(name):
    p = os.path.join(VEC, "params", name + ".bin")
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def run(nodes, br_words, lanes, ap_us, direct=False, put_dma=False,
        nin=1, nout=1, verbose=False):
    """返回搬运量与耗时。direct=True 表示 DMA 直写张量(省掉 bounce memcpy)。
    nin/nout = 输入/输出各几块缓冲(重叠要用 2 块)。"""
    idx = {n["name"]: i for i, n in enumerate(nodes)}
    G = lambda c: (c + lanes - 1) // lanes
    g_tot = p_tot = par_tot = strips_tot = 0
    detail = []
    for i, n in enumerate(nodes):
        if n["kind"] == "INPUT":
            continue
        A = nodes[idx[n["in0"]]]
        rin = A["out_w"] * G(A["out_c"])
        rout = n["out_w"] * G(n["out_c"])
        pw = (param_bytes(n["name"]) + WORD - 1) // WORD if n["kind"] == "CONV" else 0
        H = n["out_h"]
        avail = br_words - pw
        if n["kind"] == "CONV":
            if n["mode"] == 0:
                S = (avail - nin * 2 * rin) // (nin * rin + nout * rout)
                nin_of = lambda sa: sa + 2
            elif n["mode"] == 1:
                S = avail // (nin * rin + nout * rout)
                nin_of = lambda sa: sa
            else:
                S = 2 * (avail // (nin * rin + nout * 2 * rout))
                nin_of = lambda sa: max(1, sa // 2)
        else:
            B = nodes[idx[n["in1"]]] if n["in1"] != "-" else None
            rb = (B["out_w"] * G(B["out_c"])) if (B and n["mode"] in (0, 1)) else 0
            if n["mode"] == 2:
                S = avail // (nin * 2 * rin + nout * rout)
                nin_of = lambda sa: 2 * sa
            else:
                S = avail // (nin * (rin + rb) + nout * rout)
                nin_of = lambda sa: sa
        S = max(1, min(S, H))
        if n["kind"] == "CONV" and n["mode"] == 2 and (S & 1):
            S -= 1
        nstrip = (H + S - 1) // S
        iw = 0
        y0 = 0
        while y0 < H:
            sa = min(S, H - y0)
            iw += nin_of(sa) * rin
            if n["kind"] == "ELT":
                B = nodes[idx[n["in1"]]] if n["in1"] != "-" else None
                if B and n["mode"] in (0, 1):
                    iw += nin_of(sa) * (B["out_w"] * G(B["out_c"]))
            y0 += S
        ow = H * rout
        g_tot += ow
        p_tot += iw
        par_tot += pw * nstrip
        strips_tot += nstrip
        detail.append((n["name"], n["kind"], n["mode"], S, nstrip, iw, ow))

    g_bytes = g_tot * WORD
    p_bytes = (p_tot + par_tot) * WORD
    g_us = g_bytes / GDMA_BW * 1e6 + strips_tot * GDMA_FIXED_US
    if not direct:
        g_us += g_bytes / MEMCPY_BW * 1e6
    if put_dma:
        # 写也走 DMA: 同样按条带一笔, 但要先 memcpy 进 bounce
        p_us = (p_bytes / GDMA_BW * 1e6 + strips_tot * GDMA_FIXED_US
                + p_bytes / MEMCPY_BW * 1e6)
    else:
        p_us = p_bytes / PUT_BW * 1e6
    if verbose:
        for d in detail:
            print("    %-36s %-5s m%d S=%-3d x%-3d  in=%-8d out=%d" % d)
    return dict(get_words=g_tot, put_words=p_tot, par_words=par_tot,
                strips=strips_tot, get_us=g_us, put_us=p_us, ap_us=ap_us,
                total_us=ap_us + g_us + p_us)


def show(tag, m, base=None):
    line = ("%-30s | 读 %6.1fM字 %6.0fms | 写 %6.1fM字 %5.0fms | ap %6.0fms | "
            "合计 %6.0fms  (%4.1f fps)"
            % (tag, m["get_words"] / 1e6, m["get_us"] / 1000.0,
               m["put_words"] / 1e6, m["put_us"] / 1000.0, m["ap_us"] / 1000.0,
               m["total_us"] / 1000.0, 1e6 / m["total_us"]))
    if base:
        line += " | 省 %.0f ms" % ((base["total_us"] - m["total_us"]) / 1000.0)
    print(line)


def main():
    nodes = load_net()
    r512 = MEAS["res512"]
    AP = lambda ms: ms * 1000.0        # 表里是 ms, 公式里用 µs
    print("=== 模型校准 vs 实测(512 档, 稳态) ===")
    s0 = run(nodes, 16384, 8, ap_us=AP(r512["ap"]))
    print("  读字数 模型 %.4fM  实测 %.4fM" % (s0["get_words"] / 1e6, r512["get_words"] / 1e6))
    print("  写字数 模型 %.4fM  实测 %.4fM" % (s0["put_words"] / 1e6, r512["put_words"] / 1e6))
    print("  条带数 模型 %d      实测 %d" % (s0["strips"], r512["strips"]))
    print("  get    模型 %.1fms  实测 %.1fms (DMA %.1f + memcpy %.1f)"
          % (s0["get_us"] / 1000, r512["get"], 88.2, 36.9))
    print("  put    模型 %.1fms  实测 %.1fms" % (s0["put_us"] / 1000, r512["put"]))
    print("  合计   模型 %.1fms  实测 %.1fms" % (s0["total_us"] / 1000, r512["total"]))
    print()

    print("=== 现状 vs 各方案 (512 全分辨率, 板端 AI 整网) ===")
    base = s0
    show("S0 现状(GDMA+DMA内存)", s0, base)
    s1 = run(nodes, 16384, 8, ap_us=AP(r512["ap"]), direct=True)
    show("S1 DMA 直写张量", s1, base)
    ap148 = AP(r512["ap"]) * AP_BASE_CLK / 148e6
    s2 = run(nodes, 16384, 8, ap_us=ap148)
    show("S2 PL 提频到 148MHz", s2, base)
    s3 = dict(s0)
    s3["total_us"] = max(s0["ap_us"], s0["get_us"] + s0["put_us"]) + 12.2 * 1000
    show("S3 搬运与计算重叠(理想)", s3, base)
    s4 = dict(s1)
    s4["total_us"] = max(s1["ap_us"], s1["get_us"] + s1["put_us"]) + 12.2 * 1000
    show("S4 S1+S3 一起上", s4, base)
    s5 = run(nodes, 21600, 8, ap_us=AP(r512["ap"]))
    show("S5 BRAM 加大到 ~344KB", s5, base)
    s6 = run(nodes, 16384, 16, ap_us=AP(r512["ap"]))       # HLS 改 16xint8/字
    show("S6 HLS 改 int8x16/字", s6, base)
    s7 = run(nodes, 16384, 8, ap_us=AP(r512["ap"]), put_dma=True)
    show("S7 连 put 也走 DMA", s7, base)
    print()

    print("=== 重叠方案: 双缓冲布局的代价与收益 ===")
    print("  (nin/nout = 输入/输出各几块缓冲; 假设 ap 不变 —— IP 按**输出像素**算, halo 只多读不多算)")
    OTHER = 12.2 * 1000.0
    print("  %-8s %8s %8s %8s %8s %10s %9s" %
          ("nin/nout", "条带/节点", "组装A", "读回C", "ap B", "模型合计", "vs 现状"))
    base_noov = None
    for nin, nout in ((1, 1), (1, 2), (2, 2)):
        m = run(nodes, 16384, 8, ap_us=AP(r512["ap"]), nin=nin, nout=nout)
        A, C, B = m["put_us"], m["get_us"], m["ap_us"]
        c_mem = m["get_words"] * WORD / MEMCPY_BW * 1e6
        if nin == 1 and nout == 1:
            tot = A + B + C + OTHER
        elif nin == 1 and nout == 2:
            tot = A + max(B, C) + OTHER        # 只有"读回"能藏到计算后面
        else:
            tot = max(B, A + c_mem) + OTHER    # 组装与读回都能藏
        if base_noov is None:
            base_noov = tot
        print("  %-8s %7d %7.0fms %7.0fms %7.0fms %9.0fms %8.0fms" %
              ("%d/%d" % (nin, nout), m["strips"] / 30, A / 1000, C / 1000,
               B / 1000, tot / 1000, (base_noov - tot) / 1000))
        if nin == 2 and nout == 2:
            print("      条带数 %d -> %d ; 写量 %.2fM -> %.2fM 字 (DMA 笔数与每笔 25us 开销也按这个涨)"
                  % (s0["strips"], m["strips"], s0["put_words"] / 1e6,
                     m["put_words"] / 1e6))
    print()

    print("=== 分辨率扫描: 实测 vs 模型 ===")
    print("  %-6s %8s %8s %8s %8s %8s" % ("res", "实测AI", "ap", "get", "put", "模型AI"))
    for res in (512, 256, 128, 64, 32):
        m = MEAS["res%d" % res]
        shift = {512: 0, 256: 1, 128: 2, 64: 3, 32: 4}[res]
        mm = run(nodes, 16384, 8, ap_us=m["ap"])
        # 模型是按 512 的条带算的, 这里只对 512 档可比
        print("  %-6d %8.1f %8.1f %8.1f %8.1f   (512 档模型 %.1f)"
              % (res, m["total"], m["ap"], m["get"], m["put"], mm["total_us"] / 1000))
    print()

    print("=== 上位机实测(打 systemd 服务, 每档 3 帧平均) ===")
    print("  res   全回传     只算不传   (板端AI / 非AI固定开销)")
    PC = {512: (898.5, 826.2), 256: (279.9, 233.1), 128: (136.7, 90.5),
          64: (101.6, 54.7), 32: (91.2, 45.4)}
    for res in (512, 256, 128, 64, 32):
        full, noimg = PC[res]
        ai = MEAS["res%d" % res]["total"]
        print("  %-5d %8.1f %9.1f   (%.1f / %.1f)"
              % (res, full, noimg, ai, noimg - ai))
    print()

    print("=== 逐节点明细(512, 现状) ===")
    run(nodes, 16384, 8, ap_us=r512["ap"], verbose=True)


if __name__ == "__main__":
    main()
