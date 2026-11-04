"""verify_board_clinical.py —— 2a / 2b 临床测量上板核对（板端 v5.15 的 job_echo.c "clinical"、job_ct.c "spleen"）。

只用验证集（EchoNet VAL、MSD 脾脏 VAL 7 卷）；测试集不碰。
  E  心超 echo_ef（全帧 --videos 段 + 实时 --rt 段）：板上 "clinical"（LVEF 区间 / 复核 / 逐拍离散度 / RR / 指数心拍）
     == echo_ws.clinical(板上的逐拍结果)，逐位相同（同一套规则、同样的浮点顺序）；门限取 echonet_config.json 的 "clinical"。
  C  CT ct_seg（VAL 7 卷，两阶段）：板上 "spleen"（长 / 宽 / 厚 / 脾指数）== ct_volume.spleen_measure(板上最终掩膜)，逐位相同；
     另外列出与标注掩膜测量的差别（预测 vs 标注），作为测量误差的参考。
仅辅助测量与复核，不作诊断。用 srtp-vision 的 python：
    python verify_board_clinical.py [--port 5060] [--videos 40] [--rt 10] [--out board_check_v5/clinical_acceptance.json]
"""
import argparse
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np  # noqa: E402

import ct_volume  # noqa: E402
import echo_ws  # noqa: E402
import imagelink  # noqa: E402
import jobs  # noqa: E402
import proto  # noqa: E402

ECHONET = os.path.normpath(os.path.join(HERE, "..", "..", "EchoNet-Dynamic"))
MSD = os.path.normpath(os.path.join(HERE, "..", "..", "Task09_Spleen", "Task09_Spleen", "imagesTr"))
SPLIT = os.path.normpath(os.path.join(HERE, "..", "..", "ct", "results", "split.json"))
MODEL = next(m for m in proto.MODELS if m["name"] == "echonet_lv")
KEYS = ("length_mm", "width_mm", "thickness_mm", "splenic_index", "width_slice", "width_ends")


def val_videos(n):
    with open(os.path.join(ECHONET, "FileList.csv"), encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["Split"].upper() == "VAL"]
    names = sorted(r["FileName"] if r["FileName"].endswith(".avi") else r["FileName"] + ".avi" for r in rows)
    return names[:n]


def echo_part(link, conf, n, n_rt):
    rows, bad = [], 0
    for i, name in enumerate(val_videos(n)):
        v, fps = echo_ws.read_avi(os.path.join(ECHONET, "Videos", name))
        for rt in ((False, True) if i < n_rt else (False,)):
            mode = "realtime" if rt else "full"
            r = jobs.echo_ef(link, conf, MODEL["scale"], frames=v, fps=fps, realtime=rt, flags=0)
            res = r["result"]
            cal = lambda e: echo_ws.calibrated(e, conf, mode)
            ef = cal(res["ef_raw"]) if res["ef_raw"] is not None else float("nan")
            pc = echo_ws.clinical(res["beats"], fps, ef, cal, echo_ws.clinical_conf(conf, mode))
            pc = json.loads(json.dumps(pc))
            bc = res.get("clinical")
            same = bc == pc
            bad += not same
            rows.append(dict(video=name, mode=mode, same=same, band=pc["band"], review=pc["review"],
                             n_beats=pc["n_beats"], rr_cv=pc["rr_cv"], irregular=pc["irregular"],
                             board=bc if not same else None, pc=pc if not same else None))
            print("%-26s %-8s %s band=%s review=%s beats=%d rr_cv=%s" % (
                name, mode, "SAME" if same else "DIFF", pc["band"], pc["review"], pc["n_beats"],
                None if pc["rr_cv"] is None else round(pc["rr_cv"], 3)), flush=True)
    return dict(runs=len(rows), different=bad, rows=rows)


def ct_part(link):
    cases = json.load(open(SPLIT, encoding="utf-8"))["val"]
    rows, bad = [], 0
    for c in cases:
        path = os.path.join(MSD, c + ".nii.gz")
        t0 = time.perf_counter()
        r = jobs.ct_seg(link, path, flags=jobs.CT_MASK_FINAL)
        res = r["result"]
        vol = ct_volume.load_ct(path)
        n = len(vol["hu"])
        mf = jobs.unpack_mask(r["blobs"]["mask_final"], (n, 512, 512))
        pm = ct_volume.spleen_measure(mf, vol["spacing"])
        bm = res.get("spleen")
        same = bm is not None and pm is not None and all(bm[k] == pm[k] for k in KEYS) \
            and res["volume_ml"] == pm["volume_ml"]
        bad += not same
        gm = ct_volume.spleen_measure(vol["label"], vol["spacing"])
        row = dict(case=c, same=same, board=bm, pred={k: pm[k] for k in pm}, gt=gm,
                   err_pct={k: 100.0 * (pm[k] / gm[k] - 1.0) for k in ("volume_ml", "length_mm", "width_mm",
                                                                        "thickness_mm", "splenic_index")},
                   wall_s=time.perf_counter() - t0)
        rows.append(row)
        print("%-10s %s  vol %.1f/%.1f  L %.1f/%.1f  W %.1f/%.1f  T %.1f/%.1f mm  SI %.0f/%.0f  (pred/gt)" % (
            c, "SAME" if same else "DIFF", pm["volume_ml"], gm["volume_ml"], pm["length_mm"], gm["length_mm"],
            pm["width_mm"], gm["width_mm"], pm["thickness_mm"], gm["thickness_mm"], pm["splenic_index"],
            gm["splenic_index"]), flush=True)
    summ = {}
    for k in ("volume_ml", "length_mm", "width_mm", "thickness_mm", "splenic_index"):
        e = np.array([r["err_pct"][k] for r in rows])
        summ[k] = dict(mean_pct=float(e.mean()), sd_pct=float(e.std(ddof=1)), max_abs_pct=float(np.abs(e).max()))
    return dict(cases=len(rows), different=bad, error_vs_gt=summ, rows=rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.254")
    ap.add_argument("--port", type=int, default=5060)
    ap.add_argument("--videos", type=int, default=40)
    ap.add_argument("--rt", type=int, default=10)
    ap.add_argument("--skip-echo", action="store_true")
    ap.add_argument("--skip-ct", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "board_check_v5", "clinical_acceptance.json"))
    a = ap.parse_args()
    conf = echo_ws.load_config()
    echo_ws.SCALE[0] = MODEL["scale"]
    link = imagelink.ImageLink(a.host, a.port, timeout=20.0)
    link.connect()
    out = dict(time=time.strftime("%Y-%m-%d %H:%M:%S"), host=a.host, port=a.port, clinical_conf=conf.get("clinical"))
    if not a.skip_echo:
        out["echo"] = echo_part(link, conf, a.videos, a.rt)
    if not a.skip_ct:
        out["ct"] = ct_part(link)
    ok = all(out[k]["different"] == 0 for k in ("echo", "ct") if k in out)
    out["verdict"] = "CLINICAL_BOARD_PASS" if ok else "CLINICAL_BOARD_FAIL"
    out["pass"] = ok
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    if "ct" in out:
        print(json.dumps(out["ct"]["error_vs_gt"], indent=1))
    print(out["verdict"])
    link.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
