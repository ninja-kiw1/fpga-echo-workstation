"""J3(c): ops per frame -> achieved GOPS -> utilisation vs conv-engine / DSP peak; compute vs transfer split.
Inputs: layer table (baseline/results/board_cpu.log LAYER lines, CPU run of the same net),
per-node PL ap_us (board_acceptance_r2/after_profile_*.log, r2 firmware, warm block),
perf_j/results/j1_ab.json (this session's A/B medians).
Variants (argv[1], default r2 = the 10-16 baseline, output unchanged):
  r2     r2 101.376 MHz, board_acceptance_r2 profile, j1_ab.json (v5.16 base vs J1 prototype)  -> results/j3c.json
  r2v517 r2 101.376 MHz, board_acceptance_r2 profile, j1_v517_ab.json (v5.17 UNET_NOJ1 vs J1) -> results/j3c_r2v517.json
  f150   f150 150.079 MHz (MMCM), board_acceptance_f profile, j1_f150_ab.json               -> results/j3c_f150.json"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VARIANT = sys.argv[1] if len(sys.argv) > 1 else "r2"
F_PL = {"r2": 101.376e6, "r2v517": 101.376e6,   # pl0_ref measured on the board (handoff §3 / line 100)
        "f150": 101.376e6 * 116.5 / (9 * 8.625)}[VARIANT]
# f150: clk_wiz M 116.5 / D 9 / O 8.625 multiplies the REAL pl_clk0. The BD assumed 99.999001 MHz (-> 150.079 MHz), but the
# FSBL runs RPLL in fractional mode (RPLL_CTRL 0x00013000, RPLL_FRAC 0x8000a915 -> 811.008 MHz, /8 = 101.376 MHz; read
# from /dev/mem 10-17), so the PL actually runs at 152.146 MHz. ap ratio r2 -> f150 (0.6677) confirms it.
PROF_DIR = {"r2": "board_acceptance_r2", "r2v517": "board_acceptance_r2", "f150": "board_acceptance_f"}[VARIANT]
AB_JSON, AB_BASE, AB_J1 = {"r2": ("j1_ab.json", "base", "j1"), "r2v517": ("j1_v517_ab.json", "noj1", "j1"),
                           "f150": ("j1_f150_ab.json", "noj1", "j1")}[VARIANT]
OUT = {"r2": "j3c.json", "r2v517": "j3c_r2v517.json", "f150": "j3c_f150.json"}[VARIANT]
MAC_CYCLE = 8 * 8 * 2       # conv V12: 8 in-lanes x 8 out-lanes x XU=2 output columns, II=1
DSP_CONV, DSP_ALL, DSP_DEV = 185, 306, 360   # r2 routed: conv core / whole design / XCZU3EG

layers = []
for line in open(ROOT / "tcp_image/baseline/results/board_cpu.log", encoding="utf-8"):
    m = re.match(r"LAYER\s+(\d+) kind=(\d) mode=(\d) in=(\d+)x(\d+)x(\d+) out_c=(\d+)", line)
    if m:
        i, k, mo, h, w, ca, cb = map(int, m.groups())
        if i == 1 and layers:
            break
        layers.append(dict(idx=i, kind=k, mode=mo, h=h, w=w, ca=ca, cb=cb))
assert len(layers) == 29


def per_node_ap(path):
    blocks, cur = [], None
    for line in open(path, encoding="utf-8"):
        if "[prof] per-node" in line:
            cur = {}; blocks.append(cur); continue
        m = re.match(r"\[prof\]\s+(\d+)\s+(\d+)\s+(\d+)", line)
        if m and cur is not None:
            cur[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    return blocks[-1]                        # last block = warm


def g8(c):
    return (c + 7) // 8 * 8


res = {}
for tag, scale, prof in (("512", 1, "after_profile_fundus512.log"), ("128", 4, "after_profile_skin128.log")):
    ap = per_node_ap(ROOT / PROF_DIR / prof)
    rows, tot = [], dict(mac=0, macp=0, conv_ap=0, elt_ap=0, bram=0)
    for L in layers:
        h, w = L["h"] // scale, L["w"] // scale
        a_us, b_us = ap[L["idx"]]
        tot["bram"] += b_us
        if L["kind"] == 1:
            taps = {0: 9, 1: 1, 2: 4}[L["mode"]]
            mac = h * w * taps * L["ca"] * L["cb"]
            macp = h * w * taps * g8(L["ca"]) * g8(L["cb"])
            util = mac / (a_us * 1e-6 * MAC_CYCLE * F_PL)
            utilp = macp / (a_us * 1e-6 * MAC_CYCLE * F_PL)
            tot["mac"] += mac; tot["macp"] += macp; tot["conv_ap"] += a_us
            rows.append(dict(idx=L["idx"], op={0: "conv3x3", 1: "conv1x1", 2: "tconv2x2"}[L["mode"]],
                             in_=f"{h}x{w}x{L['ca']}", out_c=L["cb"], mac_M=mac / 1e6, ap_ms=a_us / 1e3,
                             util_useful=util, util_padded=utilp))
        else:
            tot["elt_ap"] += a_us
            rows.append(dict(idx=L["idx"], op={0: "add", 1: "concat", 2: "maxpool"}[L["mode"]],
                             in_=f"{h}x{w}x{L['ca']}", out_c=L["cb"], ap_ms=a_us / 1e3))
    res[tag] = dict(rows=rows, **tot)

ab = json.loads((Path(__file__).parent / "results" / AB_JSON).read_text())
if VARIANT != "r2":                              # parse_ab2.py writes ms and "variant/case" keys; j1_ab.json has us, "case/variant"
    ab = {f"{k.split('/')[1]}/{'base' if k.split('/')[0] == AB_BASE else 'j1'}":
          {f: (v[f] * 1000 if f.endswith("_us") else v[f]) for f in v} for k, v in ab.items()}
peak_conv = MAC_CYCLE * F_PL                      # MAC/s
peak_dev = DSP_DEV * F_PL
summary = {}
for tag, case in (("512", "fundus512"), ("128", "skin128")):
    r = res[tag]
    b, j = ab[f"{case}/base"], ab[f"{case}/j1"]
    ap_ms = b["ap_us"] / 1e3
    d = dict(
        gmac_useful=r["mac"] / 1e9, gmac_padded=r["macp"] / 1e9, gop_useful=2 * r["mac"] / 1e9,
        conv_ap_ms=r["conv_ap"] / 1e3, elt_ap_ms=r["elt_ap"] / 1e3,
        net_ms=b["run_us"] / 1e3, ap_ms=ap_ms, put_ms=b["put_us"] / 1e3, get_ms=b["get_us"] / 1e3,
        other_ms=(b["run_us"] - b["ap_us"] - b["put_us"] - b["get_us"]) / 1e3,
        net_ms_j1ovl=j["run_us"] / 1e3,
        peak_conv_gops=2 * peak_conv / 1e9, peak_dsp360_gops=2 * peak_dev / 1e9,
    )
    d["gops_net"] = d["gop_useful"] / (d["net_ms"] / 1e3)
    d["gops_net_j1ovl"] = d["gop_useful"] / (d["net_ms_j1ovl"] / 1e3)
    d["gops_ap"] = d["gop_useful"] / (ap_ms / 1e3)
    d["gops_conv_only"] = d["gop_useful"] / (d["conv_ap_ms"] / 1e3)
    d["util_conv_engine_during_conv"] = r["mac"] / (r["conv_ap"] * 1e-6 * peak_conv)
    d["util_conv_engine_padded_during_conv"] = r["macp"] / (r["conv_ap"] * 1e-6 * peak_conv)
    d["util_conv_engine_ap"] = r["mac"] / (ap_ms * 1e-3 * peak_conv)
    d["util_conv_engine_net"] = r["mac"] / (d["net_ms"] * 1e-3 * peak_conv)
    d["util_dsp360_net"] = r["mac"] / (d["net_ms"] * 1e-3 * peak_dev)
    summary[tag] = d
    print(f"--- {tag}: useful {d['gmac_useful']:.3f} GMAC ({d['gop_useful']:.2f} GOP), padded {d['gmac_padded']:.3f} GMAC")
    print(f"  net {d['net_ms']:.1f} ms = ap {ap_ms:.1f} (conv {d['conv_ap_ms']:.1f} + elt {d['elt_ap_ms']:.1f}) + put {d['put_ms']:.1f} + get {d['get_ms']:.1f} + other {d['other_ms']:.1f}")
    print(f"  GOPS: whole net {d['gops_net']:.2f} (J1-ovl {d['gops_net_j1ovl']:.2f}), during ap {d['gops_ap']:.2f}, during conv {d['gops_conv_only']:.2f};"
          f" conv-engine peak {d['peak_conv_gops']:.2f}, 360-DSP peak {d['peak_dsp360_gops']:.1f}")
    print(f"  conv engine utilisation: during conv {100*d['util_conv_engine_during_conv']:.1f}% (padded {100*d['util_conv_engine_padded_during_conv']:.1f}%),"
          f" over ap {100*d['util_conv_engine_ap']:.1f}%, over whole net {100*d['util_conv_engine_net']:.1f}%; 360-DSP {100*d['util_dsp360_net']:.1f}%")
for r in res["512"]["rows"]:
    if "util_useful" in r:
        print(f"  {r['idx']:2d} {r['op']:8s} {r['in_']:>12s}->{r['out_c']:3d}  {r['mac_M']:7.1f} MMAC  ap {r['ap_ms']:6.2f} ms  util {100*r['util_useful']:5.1f}% (padded {100*r['util_padded']:5.1f}%)")
(Path(__file__).parent / "results" / OUT).write_text(json.dumps(dict(summary=summary, nodes=res,
    consts=dict(VARIANT=VARIANT, F_PL=F_PL, MAC_CYCLE=MAC_CYCLE, DSP_CONV=DSP_CONV, DSP_ALL=DSP_ALL, DSP_DEV=DSP_DEV)), indent=1))
