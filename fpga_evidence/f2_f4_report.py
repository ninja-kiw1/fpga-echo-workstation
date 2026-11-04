# -*- coding: utf-8 -*-
"""F2 每帧预算表 + F4 异构并行甘特图：数据来自现场模式（echo_ef mode 3）的板上逐帧时间戳。

输入 E:/PLD/live/results/live_trace.npz（verify_board_echo_live.py 存的，每帧 7 个 u32：采到 / 缩好 / 开始处理 /
工作开始 / 工作结束 / 处理完 / 上一块发完，单位 µs，任务开始为 0）+ live_acceptance.json。
  F2：同一段信号源、固定裁切，推荐档（关键帧）与每帧推理两种跑法，按"每帧平均占用"列出各步：缩放（PS 采集线程）、
      PL 推理（含 t3 编码与 pp_layer 清理）、PS 跟踪、容积等收尾、排队等待；对照帧间隔预算（1000 / fps ms）。
  F4：两条泳道（采集线程 / 处理线程，处理线程里 PL 段与 PS 段分色）+ 帧到达刻度，取 1 s 窗口；
      另画两种跑法的逐帧延迟（采到 -> 掩膜和容积算完）。
输出 f2_budget.json、f4_gantt.png、f4_latency.png。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe f2_f4_report.py [--source 0X100009310A3BD7FC_z4.y8]
"""
import argparse
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.normpath(os.path.join(HERE, "..", "live", "results"))
CAP, READY, START, W0, W1, END, SENT = range(7)


def stages(tr, key, fps):
    tr = tr.astype(np.int64)
    n = len(tr)
    ms = lambda a, b: (tr[:, b] - tr[:, a]) / 1000.0  # noqa: E731
    work = ms(W0, W1)
    budget = 1000.0 / fps
    d = max(1, int(round(0.04 * fps)))                       # t3: a keyframe also needs frame t + d
    early = np.where(key, tr[np.minimum(np.arange(n) + d, n - 1), READY], tr[:, READY])
    look = (early - tr[:, READY]) / 1000.0
    queue = (tr[:, START] - early) / 1000.0
    rows = [("缩放（裁切 + 盒滤波到 112）", "PS 采集线程", ms(CAP, READY), np.ones(n, bool)),
            ("PL 推理（t3 编码 + U-Net + pp_layer 清理）", "PL（PS 等待）", work, key),
            ("PS 跟踪（块匹配 + 轮廓 + 填充）", "PS 处理线程", work, ~key),
            ("收尾（容积、记账）", "PS 处理线程", ms(W1, END), np.ones(n, bool)),
            ("关键帧等 t+d 帧（t3 编码看 40 ms 后，结构性）", "—", look, key),
            ("排队（处理线程还在忙前面的帧）", "—", queue, np.ones(n, bool))]
    out = []
    for name, where, d, m in rows:
        per = float(d[m].mean()) if m.any() else 0.0
        frac = float(m.mean())
        out.append(dict(stage=name, where=where, ms_each=per, share_of_frames=frac, ms_per_frame=per * frac,
                        budget_pct=100.0 * per * frac / budget, max_ms=float(d[m].max()) if m.any() else 0.0))
    lat = ms(CAP, END)
    busy = (ms(START, END)).mean()
    return dict(fps=fps, budget_ms=budget, frames=n, key_frac=float(key.mean()), stages=out,
                processing_ms_per_frame=float(busy), processing_budget_pct=float(100 * busy / budget),
                latency_ms=dict(p50=float(np.median(lat)), p99=float(np.percentile(lat, 99)), max=float(lat.max()),
                                last=float(lat[-1])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="0X100009310A3BD7FC_z4.y8")
    ap.add_argument("--t0", type=float, default=1.0, help="甘特图窗口起点（秒）")
    a = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    z = np.load(os.path.join(LIVE, "live_trace.npz"))
    acc = json.load(open(os.path.join(LIVE, "live_acceptance.json"), encoding="utf-8"))
    runs = {(r["source"], r["case"]): r for r in acc["runs"]}
    res = {}
    for case, label in (("A", "关键帧推荐档"), ("C", "每帧推理")):
        k = "%s|%s" % (a.source, case)
        fps = runs[(a.source, case)]["fps"]
        res[label] = stages(z[k], z[k + "|key"], fps)
    with open(os.path.join(HERE, "f2_budget.json"), "w", encoding="utf-8") as f:
        json.dump(dict(source=a.source, runs=res), f, indent=1, ensure_ascii=False)
    for label, r in res.items():
        print("== %s: %d 帧 %.0f fps，预算 %.1f ms/帧，关键帧 %.1f%%" % (label, r["frames"], r["fps"], r["budget_ms"],
                                                                100 * r["key_frac"]))
        for s in r["stages"]:
            print("   %-34s %-14s 每次 %6.2f ms × %5.1f%% 帧 = %6.2f ms/帧（预算 %5.1f%%） 最长 %.2f" % (
                s["stage"], s["where"], s["ms_each"], 100 * s["share_of_frames"], s["ms_per_frame"], s["budget_pct"],
                s["max_ms"]))
        print("   处理线程占用 %.2f ms/帧（预算 %.0f%%）；延迟 P50 %.1f / P99 %.1f / 最大 %.1f / 最后一帧 %.1f ms" % (
            r["processing_ms_per_frame"], r["processing_budget_pct"], r["latency_ms"]["p50"], r["latency_ms"]["p99"],
            r["latency_ms"]["max"], r["latency_ms"]["last"]))
    # ---- F4 Gantt
    fig, axes = plt.subplots(2, 1, figsize=(13, 5.6), sharex=False)
    for ax, (case, label) in zip(axes, (("A", "关键帧推荐档"), ("C", "每帧推理"))):
        k = "%s|%s" % (a.source, case)
        tr, key = z[k].astype(np.int64) / 1000.0, z[k + "|key"]
        lo, hi = a.t0 * 1000.0, a.t0 * 1000.0 + 1000.0
        for t in range(len(tr)):
            if tr[t, END] < lo or tr[t, CAP] > hi:
                continue
            ax.broken_barh([(tr[t, CAP], max(tr[t, READY] - tr[t, CAP], 0.05))], (2.1, 0.8), color="#7a7a7a")
            ax.broken_barh([(tr[t, W0], max(tr[t, W1] - tr[t, W0], 0.05))], (1.1, 0.8),
                           color="#1f5fbf" if key[t] else "#e08a1e")
            ax.broken_barh([(tr[t, W1], max(tr[t, END] - tr[t, W1], 0.05))], (1.1, 0.8), color="#9ccf7a")
            ax.vlines(tr[t, CAP], 0.1, 0.9, color="#444", lw=0.6)
        ax.set_xlim(lo, hi)
        ax.set_yticks([0.5, 1.5, 2.5])
        ax.set_yticklabels(["帧到达", "处理线程", "采集线程"])
        ax.set_title("%s：蓝 = PL 推理，橙 = PS 跟踪，绿 = 容积收尾，灰 = 缩放（采集线程）" % label, fontsize=10, loc="left")
        ax.set_xlabel("任务开始后的时间 / ms（板上 CLOCK_MONOTONIC）")
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "f4_gantt.png"), dpi=130)
    fig, ax = plt.subplots(figsize=(8, 3.6))
    for case, label, col in (("A", "关键帧推荐档", "#1f5fbf"), ("C", "每帧推理", "#c0392b")):
        k = "%s|%s" % (a.source, case)
        tr = z[k].astype(np.int64)
        ax.plot(np.arange(len(tr)), (tr[:, END] - tr[:, CAP]) / 1000.0, color=col, lw=1.2, label=label)
    ax.set_xlabel("帧号")
    ax.set_ylabel("延迟 / ms（采到 -> 掩膜 + 容积）")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "f4_latency.png"), dpi=130)
    print("wrote f2_budget.json, f4_gantt.png, f4_latency.png")


if __name__ == "__main__":
    main()
