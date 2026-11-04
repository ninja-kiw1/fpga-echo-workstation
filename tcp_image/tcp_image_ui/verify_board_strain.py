# -*- coding: utf-8 -*-
r"""部分 E 第三步：板上病例任务 strain（src/job_strain.c，服务端 v5.19）的验收 + 追踪耗时实测。

输入 / 参考 = E:/PLD/strain/step3/results/port_val.npz（compare_port.py：CAMUS VAL 100 段，512 帧 = 上位机路径，
参考输出 = strain_port.py，它与主机原型 track_val.py 每一步逐位相同；test/strain/compare_host.py 已证 C = port）。
每段发一次任务（带 STRAIN_LOGITS），核对：
  - PL：模型 2 / 程序 18 的 128 logit = echo/results/quant/{lv,epi}_r128/outputs/val_*.npy（整数参考，逐位）
  - PL 清理 = PS 清理（"clean_pl_eq_ps"，且 "clean" = "pl"）
  - 中线像素、每帧追踪坐标（Q8）、每帧长度、GLS-like 曲线、ES 值（原始 / TRAIN 偏移后）全部逐位 = 参考
再对前 --c-kernel 段用纯 C 核（STRAIN_C_KERNEL）、前 --ps-clean 段用 PS 清理各跑一遍：结果必须相同，C 核给耗时对照。
耗时：板上 "ms"（分割 / 提线 / 追踪 / 每对帧）+ PC 墙钟；每例（两切面）追踪合计的 P50 / P95。测试集没碰。

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe verify_board_strain.py [--limit N] [--out ...]
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STEP3 = os.path.normpath(os.path.join(HERE, "..", "..", "strain", "step3"))
ECHO = os.path.normpath(os.path.join(HERE, "..", "..", "echo"))
sys.path.insert(0, ECHO)

import numpy as np  # noqa: E402

import imagelink  # noqa: E402
import jobs  # noqa: E402
from common import CACHE  # noqa: E402

OFFSET_PP = -3.8059230431912217          # strain.h STRAIN_OFFSET_PP = -(TRAIN raw bias), calib_train.json


def run_one(link, z, k, flags):
    fr = z[k + "_frames"]
    sx, sy = (float(v) for v in z[k + "_sxsy"])
    link.connect()
    out = jobs.strain(link, [f.tobytes() for f in fr], sx, sy, flags=flags)
    return out


def check(out, z, k, lv_ref=None, epi_ref=None):
    r = out["result"]
    bad = []
    if not r.get("ok"):
        return ["error: %s" % r.get("error")]
    T, n = r["frames"], r["n_points"]
    q8 = np.frombuffer(out["blobs"]["points"], np.int32).reshape(T, n, 2)
    line = np.frombuffer(out["blobs"]["line"], np.int32).reshape(-1, 2)
    g = z[k + "_g"]
    if not np.array_equal(q8, z[k + "_q8"]):
        bad.append("points")
    if not np.array_equal(line, z[k + "_path"]):
        bad.append("line")
    if not np.array_equal(np.array(r["length_mm"]), z[k + "_L"]):
        bad.append("length")
    if not np.array_equal(np.array(r["gls_pct"]), g * 100.0):
        bad.append("curve")
    if r["es_raw_pct"] != g[-1] * 100.0 or r["es_cal_pct"] != g[-1] * 100.0 + OFFSET_PP:
        bad.append("es")
    if lv_ref is not None:
        lg = np.frombuffer(out["blobs"]["logits"], np.int8).reshape(2, 128, 128)
        if not np.array_equal(lg[0], lv_ref):
            bad.append("logit_lv")
        if not np.array_equal(lg[1], epi_ref):
            bad.append("logit_epi")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.254")
    ap.add_argument("--port", type=int, default=5060)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 段（调试用）")
    ap.add_argument("--c-kernel", type=int, default=20, help="前 N 段再用纯 C 核跑一遍（耗时对照）")
    ap.add_argument("--ps-clean", type=int, default=10, help="前 N 段再用 PS 清理跑一遍")
    ap.add_argument("--server", default="v5.19", help="caps 里应报的服务端版本")
    ap.add_argument("--out", default=os.path.join(HERE, "board_check_v5", "strain_acceptance.json"))
    a = ap.parse_args()
    t0 = time.time()
    z = np.load(os.path.join(STEP3, "results", "port_val.npz"))
    per_ref = json.load(open(os.path.join(STEP3, "results", "compare_port.json"), encoding="utf-8"))["per"]
    meta = json.load(open(os.path.join(str(CACHE), "val_meta.json"), encoding="utf-8"))
    mi = {(m["patient"], m["view"], m["phase"]): i for i, m in enumerate(meta)}
    keys = sorted({f.split("_")[0] for f in z.files})
    if a.limit:
        keys = keys[:a.limit]
    link = imagelink.ImageLink(a.host, a.port, timeout=30.0)
    link.connect()
    caps = jobs.caps(link)
    rep = dict(caps=caps, caps_ok=("strain" in caps.get("jobs", []) and caps.get("server") == a.server), per=[])
    print("caps", caps, flush=True)
    for si, k in enumerate(keys):
        pr = per_ref[si]
        i = mi[(pr["patient"], pr["view"], "ED")]
        lv_ref = np.load(os.path.join(ECHO, "results", "quant", "lv_r128", "outputs", "val_%03d.npy" % i))
        epi_ref = np.load(os.path.join(ECHO, "results", "quant", "epi_r128", "outputs", "val_%03d.npy" % i))
        out = run_one(link, z, k, jobs.STRAIN_LOGITS)
        bad = check(out, z, k, lv_ref, epi_ref)
        r = out["result"]
        if r.get("ok"):
            if r.get("clean") != "pl":
                bad.append("clean_not_pl")
            if r.get("clean_pl_eq_ps") is not True:
                bad.append("pl_clean_ne_ps")
        row = dict(seq=k, patient=pr["patient"], view=pr["view"], bad=bad, frames=r.get("frames"),
                   n_points=r.get("n_points"), ms=r.get("ms"), wall_s=out["wall_s"], upload_s=out["upload_s"],
                   es_cal_pct=r.get("es_cal_pct"), edge_points=r.get("edge_points"), kernel=r.get("kernel"))
        if si < a.c_kernel:
            oc = run_one(link, z, k, jobs.STRAIN_C_KERNEL)
            row["c_kernel_bad"] = check(oc, z, k)
            row["c_kernel_ms"] = oc["result"].get("ms")
        if si < a.ps_clean:
            op = run_one(link, z, k, jobs.STRAIN_PS_CLEAN)
            row["ps_clean_bad"] = check(op, z, k) + ([] if op["result"].get("clean") == "ps" else ["clean_not_ps"])
        rep["per"].append(row)
        print("%s %s %s T=%s n=%s track %.1f ms (%.2f / pair) total %.1f ms wall %.2f s %s%s%s" % (
            k, pr["patient"], pr["view"], row["frames"], row["n_points"], (row["ms"] or {}).get("track", -1),
            (row["ms"] or {}).get("track_per_pair", -1), (row["ms"] or {}).get("total", -1), row["wall_s"],
            "OK" if not bad else bad, "" if "c_kernel_bad" not in row else " C:%s" % (row["c_kernel_bad"] or "OK"),
            "" if "ps_clean_bad" not in row else " PS:%s" % (row["ps_clean_bad"] or "OK")), flush=True)
    rows = rep["per"]
    ok = rep["caps_ok"] and all(not r["bad"] and not r.get("c_kernel_bad") and not r.get("ps_clean_bad") for r in rows)
    good = [r for r in rows if r["ms"]]
    case = {}
    for r in good:
        case.setdefault(r["patient"], []).append(r["ms"]["track"])
    per_case = [sum(v) for v in case.values() if len(v) == 2]
    pct = lambda v, q: float(np.percentile(v, q)) if v else None
    cker = [(r["ms"]["track"], r["c_kernel_ms"]["track"]) for r in good if r.get("c_kernel_ms")]
    rep["summary"] = dict(
        n=len(rows), failed=[(r["seq"], r["bad"]) for r in rows if r["bad"]],
        track_ms_per_view_p50=pct([r["ms"]["track"] for r in good], 50),
        track_ms_per_pair_p50=pct([r["ms"]["track_per_pair"] for r in good], 50),
        track_ms_per_pair_p95=pct([r["ms"]["track_per_pair"] for r in good], 95),
        track_ms_per_case_p50=pct(per_case, 50), track_ms_per_case_p95=pct(per_case, 95), cases=len(per_case),
        board_total_ms_per_view_p50=pct([r["ms"]["total"] for r in good], 50),
        segment_ms_p50=pct([r["ms"]["segment"] for r in good], 50), line_ms_p50=pct([r["ms"]["line"] for r in good], 50),
        wall_s_per_view_p50=pct([r["wall_s"] for r in good], 50),
        c_kernel_track_ratio_p50=pct([c / n for n, c in cker], 50), c_kernel_n=len(cker),
        verdict="STRAIN_BOARD_PASS" if ok else "STRAIN_BOARD_FAIL", seconds=time.time() - t0)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    print(json.dumps(rep["summary"], indent=1, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
