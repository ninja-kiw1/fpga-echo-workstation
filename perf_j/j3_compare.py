"""J3 before (r2 @101.376 MHz) / after (f150 @150.079 MHz) tables, server v5.17 on both sides -> stdout (markdown rows)
+ results/j3_f150_compare.json.  PL before = j3b_v517.json (same test binary); CPU before = j3b.json (10-16, CPU is
independent of the PL clock); after = j3b_f150.json; c = j3c_r2v517.json vs j3c_f150.json; service = j3s_server*.json."""
import json, sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
R = Path(__file__).parent / "results"
L = lambda n: json.loads((R / n).read_text())
b_pl, b_cpu, a = L("j3b_v517.json"), L("j3b.json"), L("j3b_f150.json")
cmp = {}
def f(v): return "—" if v is None else f"{v['p50']:.1f} / {v['p99']:.1f} / {v['max']:.1f}"
rows = [("PL 512, nice −20", "v517_512_nice", "pl512_nice", b_pl), ("PL 512, nice 0", "v517_512_nice0", "pl512_nice0", b_pl),
        ("PL 128, nice −20", "v517_128_nice", "pl128_nice", b_pl), ("PL 128, nice 0", None, "pl128_nice0", b_pl),
        ("CPU 4 线程 512, nice 0", "cpu4_512", "cpu4_512", b_cpu), ("CPU 4 线程 512, nice −20", None, "cpu4_512_nice", b_cpu),
        ("CPU 4 线程 128, nice 0", "cpu4_128", "cpu4_128", b_cpu), ("CPU 4 线程 128, nice −20", None, "cpu4_128_nice", b_cpu),
        ("CPU 1 线程 128, nice 0", "cpu1_128", "cpu1_128", b_cpu), ("CPU 1 线程 128, nice −20", None, "cpu1_128_nice", b_cpu)]
for name, kb, ka, B in rows:
    cells = []
    for c in ("idle", "cpu4", "mem4"):
        vb = B.get(f"{c}/{kb}") if kb else None
        va = a.get(f"{c}/{ka}") or (a.get(f"{c}/{ka.replace('_nice', '')}") if c == "idle" and ka.endswith("_nice") and "cpu" in ka else None)
        cells.append(f"{f(vb)} → **{f(va)}**")
        cmp[f"{c}/{name}"] = dict(before=vb and {k: vb[k] for k in ("p50", "p99", "max")}, after=va and {k: va[k] for k in ("p50", "p99", "max")})
    print(f"| {name} | " + " | ".join(cells) + " |")
print()
for c in ("cpu4", "mem4"):
    for t, pl, cpu in (("512", "pl512_nice", "cpu4_512"), ("128", "pl128_nice", "cpu4_128")):
        p, q, qn = a[f"{c}/{pl}"], a[f"{c}/{cpu}"], a[f"{c}/{cpu}_nice"]
        print(f"{c} {t}: PL nice-20 P50 {p['p50']:.1f} P99 {p['p99']:.1f} | CPU4 nice0 {q['p50']:.1f}/{q['p99']:.1f} (x{q['p50']/p['p50']:.2f} P50, x{q['p99']/p['p99']:.2f} P99)"
              f" | CPU4 nice-20 {qn['p50']:.1f}/{qn['p99']:.1f}/{qn['max']:.1f} (x{qn['p50']/p['p50']:.2f} P50)")
    print(f"{c} ap p50 512 nice: {a[f'{c}/pl512_nice']['ap_p50']:.2f} put {a[f'{c}/pl512_nice']['put_p50']:.2f} get {a[f'{c}/pl512_nice']['get_p50']:.2f}"
          f" | nice0 ap {a[f'{c}/pl512_nice0']['ap_p50']:.2f} put {a[f'{c}/pl512_nice0']['put_p50']:.2f} get {a[f'{c}/pl512_nice0']['get_p50']:.2f}")
print(f"idle ap 512 {a['idle/pl512_nice']['ap_p50']:.2f} put {a['idle/pl512_nice']['put_p50']:.2f} get {a['idle/pl512_nice']['get_p50']:.2f}")
cb, ca = L("j3c_r2v517.json")["summary"], L("j3c_f150.json")["summary"]
for t in ("512", "128"):
    for k in ("net_ms", "net_ms_j1ovl", "ap_ms", "conv_ap_ms", "elt_ap_ms", "put_ms", "get_ms", "other_ms", "peak_conv_gops",
              "gops_conv_only", "gops_ap", "gops_net", "gops_net_j1ovl", "util_conv_engine_during_conv",
              "util_conv_engine_padded_during_conv", "util_conv_engine_ap", "util_conv_engine_net", "util_dsp360_net"):
        print(f"c {t} {k:38s} {cb[t][k]:9.3f} -> {ca[t][k]:9.3f}")
    for s in (cb[t], ca[t]):
        s["util_conv_engine_net_j1ovl"] = s["gmac_useful"] * 1e9 / (s["net_ms_j1ovl"] * 1e-3 * s["peak_conv_gops"] / 2 * 1e9)
    print(f"c {t} util_conv_engine_net_j1ovl {cb[t]['util_conv_engine_net_j1ovl']:.3f} -> {ca[t]['util_conv_engine_net_j1ovl']:.3f}")
cmp["c"] = dict(before=cb, after=ca)
(R / "j3_f150_compare.json").write_text(json.dumps(cmp, indent=1, ensure_ascii=False), encoding="utf-8")
