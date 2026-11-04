# -*- coding: utf-8 -*-
"""verify_board_simpson.py —— F4 双平面 Simpson 上板（病例任务 simpson_ef，板端 src/job_simpson.c）验收。

  A  CAMUS 训练 + 验证集全部 450 例的专家掩膜（不缩放、不清理）：板上 EDV / ESV / EF == ef_simpson.volumes（逐位：
     每个掩膜 20 个圆盘直径、步长也比），并统计板上 EF 复现 CAMUS 参考 EF（Info_2CH.cfg）的比例；
  B  界面路径：验证集 50 例的 F3 预测掩膜（128 -> x4 -> 512 上传，板上 Pillow NEAREST 回原图尺寸 + 清理），
     面积曲线最大 / 最小帧当 ED / ES（eval_f4.py 的 f3_auto）：板上 EDV / ESV / EF == eval_f4.py 的结果（results/f4/eval_f4_val.json）。
测试集不碰。用 srtp-vision 的 python（scikit-image 等）：
    python verify_board_simpson.py [--limit N] [--out board_check_v5/simpson_acceptance.json]
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ECHO = os.path.normpath(os.path.join(HERE, "..", "..", "echo"))
sys.path.insert(0, HERE)
sys.path.insert(0, ECHO)
import numpy as np  # noqa: E402

import ef_simpson as ef  # noqa: E402
import imagelink  # noqa: E402
import jobs  # noqa: E402
from common import CAMUS, LABELS, RESULTS  # noqa: E402
from nifti import read_nifti  # noqa: E402

ORDER = (("2CH", "ED"), ("2CH", "ES"), ("4CH", "ED"), ("4CH", "ES"))


def info_cfg(path):
    return dict(line.split(": ", 1) for line in open(path, encoding="utf-8").read().splitlines() if ": " in line)


def load_gt(p, view, phase):
    gt, s = read_nifti(CAMUS / "database_nifti" / p / f"{p}_{view}_{phase}_gt.nii.gz")
    return gt == LABELS["lv"], (s[1], s[0])


def part_a(link, limit):
    pats = []
    for split in ("training", "validation"):
        pats += (CAMUS / "database_split" / f"subgroup_{split}.txt").read_text().split()
    pats = pats[:limit] if limit else pats
    rows, bad = [], []
    t_board = t_pc = 0.0
    for i, p in enumerate(pats):
        m = {k: load_gt(p, *k) for k in ORDER}
        t0 = time.perf_counter()
        link.connect()
        r = jobs.simpson_ef(link, [m[k][0] for k in ORDER], m[("2CH", "ED")][1], m[("4CH", "ED")][1])
        t_board += time.perf_counter() - t0
        res = r["result"]
        t0 = time.perf_counter()
        d = [ef.diameters(m[k][0], m[k][1]) for k in ORDER]
        edv, esv = ef.volumes(*(m[k][0] for k in ORDER[:2]), m[("2CH", "ED")][1], *(m[k][0] for k in ORDER[2:]),
                              m[("4CH", "ED")][1])
        t_pc += time.perf_counter() - t0
        ref = float(info_cfg(CAMUS / "database_nifti" / p / "Info_2CH.cfg")["EF"])
        same = (res.get("ok") and res["edv"] == edv and res["esv"] == esv and res["ef"] == ef.ejection_fraction(edv, esv)
                and all(list(mm["diam"]) == list(dd[0]) and mm["step"] == dd[1] for mm, dd in zip(res["masks"], d)))
        row = dict(patient=p, ok=bool(same), edv=res.get("edv"), esv=res.get("esv"), ef=res.get("ef"), ef_ref=ref,
                   pc=(edv, esv), board_ms=res.get("ms"), error=res.get("error"))
        rows.append(row)
        if not same:
            bad.append(row)
        if (i + 1) % 25 == 0 or not same:
            print("  A %3d/%d %s %s  board %s/%s/%s  PC %d/%d  ref EF %.0f" % (i + 1, len(pats), p, "OK" if same else "DIFF",
                  res.get("edv"), res.get("esv"), res.get("ef"), edv, esv, ref), flush=True)
    rep = sum(1 for r in rows if r["ef"] is not None and r["ef"] == r["ef_ref"])
    return dict(pass_=not bad, patients=len(rows), identical=len(rows) - len(bad), mismatches=bad[:20],
                reproduces_reference_ef=rep, board_s=round(t_board, 2), pc_s=round(t_pc, 2),
                board_ms_mean=float(np.mean([r["board_ms"] for r in rows if r["board_ms"]])), rows=rows)


def part_b(link, limit):
    from PIL import Image
    z = np.load(RESULTS / "f4" / "val_masks128.npz")["fwd"]
    index = json.loads((RESULTS / "f4" / "val_masks128_index.json").read_text(encoding="utf-8"))
    ev = {r["patient"]: r for r in json.loads((RESULTS / "f4" / "eval_f4_val.json").read_text(encoding="utf-8"))["rows"]}
    by = {(s["patient"], s["view"]): s for s in index["sequences"]}
    pats = sorted({s["patient"] for s in index["sequences"]})
    pats = pats[:limit] if limit else pats
    rows, bad = [], []
    for p in pats:
        masks, outs, sp = [], [], {}
        for view in ("2CH", "4CH"):
            s = by[(p, view)]
            w, h = s["width"], s["height"]
            seq = z[s["start"]:s["start"] + s["length"]]
            m512 = [np.repeat(np.repeat(m, 4, 0), 4, 1) for m in seq]
            # keyframes exactly as eval_f4 f3_auto: area of the masks at the original size
            area = [int((np.array(Image.fromarray(m.astype(np.uint8) * 255).resize((w, h), Image.NEAREST)) > 0).sum())
                    for m in m512]
            ed, es = int(np.argmax(area)), int(np.argmin(area))
            masks += [m512[ed], m512[es]]
            outs += [(w, h), (w, h)]
            sp[view] = load_gt(p, view, "ED")[1]
        link.connect()
        r = jobs.simpson_ef(link, masks, sp["2CH"], sp["4CH"], out_sizes=outs, clean=True)
        res = r["result"]
        want = ev[p]["f3_auto"]
        same = res.get("ok") and (res["edv"], res["esv"], res["ef"]) == (want.get("edv"), want.get("esv"), want.get("ef"))
        rows.append(dict(patient=p, ok=bool(same), board=(res.get("edv"), res.get("esv"), res.get("ef")),
                         eval_f4=(want.get("edv"), want.get("esv"), want.get("ef")), ef_ref=ev[p]["ef_ref"]))
        if not same:
            bad.append(rows[-1])
        print("  B %s %s  board %s  eval_f4 %s  ref %.0f" % (p, "OK" if same else "DIFF", rows[-1]["board"],
              rows[-1]["eval_f4"], ev[p]["ef_ref"]), flush=True)
    err = [abs(r["board"][2] - r["ef_ref"]) for r in rows if r["board"][2] is not None]
    return dict(pass_=not bad, patients=len(rows), identical=len(rows) - len(bad), mismatches=bad,
                ef_mae_vs_reference=float(np.mean(err)) if err else None, rows=rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.254")
    ap.add_argument("--port", type=int, default=5060)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 例（调试用）")
    ap.add_argument("--out", default=os.path.join(HERE, "board_check_v5", "simpson_acceptance.json"))
    a = ap.parse_args()
    link = imagelink.ImageLink(a.host, a.port, timeout=30.0)
    rep = dict(date=time.strftime("%Y-%m-%d %H:%M:%S"), caps=jobs.caps(link))
    print("caps:", rep["caps"], flush=True)
    print("== A expert masks (training + validation)", flush=True)
    rep["expert"] = part_a(link, a.limit)
    print("   identical %d/%d, board EF == CAMUS reference EF %d/%d, board %.1f s vs PC %.1f s"
          % (rep["expert"]["identical"], rep["expert"]["patients"], rep["expert"]["reproduces_reference_ef"],
             rep["expert"]["patients"], rep["expert"]["board_s"], rep["expert"]["pc_s"]), flush=True)
    print("== B UI path: F3 masks, 512 -> original size + clean on the board (validation)", flush=True)
    rep["ui_path"] = part_b(link, a.limit)
    print("   identical %d/%d, EF MAE vs reference %.2f" % (rep["ui_path"]["identical"], rep["ui_path"]["patients"],
                                                          rep["ui_path"]["ef_mae_vs_reference"] or float("nan")))
    rep["pass"] = bool(rep["expert"]["pass_"] and rep["ui_path"]["pass_"])
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    print("SIMPSON_BOARD_PASS" if rep["pass"] else "SIMPSON_BOARD_FAIL", "->", a.out)
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
