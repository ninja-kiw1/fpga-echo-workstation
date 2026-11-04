# -*- coding: utf-8 -*-
"""_verify_timeline.py —— P1 1.7 可观测面板的无人值守检查（板端 v5.16）。

  CT    spleen_10（VAL）：ct_seg 默认（PL 后处理）与 CT_PS_UPSAMPLE（PS 工作线程放大）各跑一次：最终掩膜逐位相同；
        两次结果都带 v5.16 的 pl_split / pp_ms；timeline.segments() 能排出各泳道；TimelineWin 画出来（导出 PostScript）
  心超  EchoNet VAL 第一段：echo_ef 默认（PL clean）与 ECHO_PS_CLEAN：掩膜、容积逐位相同；同样画时间线
数据都是软件计时。srtp-vision（Tk）：
    python _verify_timeline.py [--out board_check_v5/timeline_check.json]
"""
import argparse
import csv
import json
import os
import sys
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import echo_ws  # noqa: E402
import imagelink  # noqa: E402
import jobs  # noqa: E402
import proto  # noqa: E402
import timeline  # noqa: E402
from verify_board_jobs import MSD  # noqa: E402

ECHONET = os.path.normpath(os.path.join(HERE, "..", "..", "EchoNet-Dynamic"))
MODEL = next(m for m in proto.MODELS if m["name"] == "echonet_lv")


def describe(kind, r):
    total, seg, s = timeline.segments(kind, r)
    lanes = sorted({x[0] for x in seg})
    return dict(total_ms=round(total, 1), lanes=lanes, n_segments=len(seg), split=s["split"],
                pl_compute_ms=round(s["pl_compute_ms"], 1), pl_move_ms=round(s["pl_move_ms"], 1),
                pl_post_ms=round(s["pl_post_ms"], 1), ps_post_ms=round(s["ps_post_ms"], 1),
                pl_split=r["result"].get("pl_split"), pp_ms=r["result"].get("pp_ms"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "board_check_v5", "timeline_check.json"))
    a = ap.parse_args()
    link = imagelink.ImageLink("192.168.1.254", 5060, timeout=60)
    rep, ok = {}, True
    root = tk.Tk()
    root.withdraw()

    p = os.path.join(MSD, "spleen_10.nii.gz")
    link.connect()
    r_pl = jobs.ct_seg(link, p, flags=jobs.CT_MASK_FINAL)
    link.connect()
    r_ps = jobs.ct_seg(link, p, flags=jobs.CT_MASK_FINAL | jobs.CT_PS_UPSAMPLE)
    same = r_pl["blobs"].get("mask_final") == r_ps["blobs"].get("mask_final") and r_pl["blobs"].get("mask_final")
    rep["ct"] = dict(same_mask=bool(same), pl=describe("ct", r_pl), ps=describe("ct", r_ps))
    ok &= bool(same) and rep["ct"]["pl"]["split"] and rep["ct"]["pl"]["pp_ms"] is not None
    w = timeline.TimelineWin(root, "CT")
    w.add("PL 后处理", "ct", r_pl)
    w.add("PS 后处理", "ct", r_ps, "掩膜逐位相同" if same else "不同")
    w.top.update()
    w.cv.postscript(file=os.path.join(HERE, "board_check_v5", "timeline_ct.ps"))
    rep["ct"]["text"] = w.var.get()

    with open(os.path.join(ECHONET, "FileList.csv"), encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["Split"].upper() == "VAL"]
    name = sorted(r["FileName"] if r["FileName"].endswith(".avi") else r["FileName"] + ".avi" for r in rows)[0]
    conf = echo_ws.load_config()
    v, fps = echo_ws.read_avi(os.path.join(ECHONET, "Videos", name))
    link.connect()
    e_pl = jobs.echo_ef(link, conf, MODEL["scale"], frames=v, fps=fps, flags=jobs.ECHO_MASKS)
    link.connect()
    e_ps = jobs.echo_ef(link, conf, MODEL["scale"], frames=v, fps=fps, flags=jobs.ECHO_MASKS | jobs.ECHO_PS_CLEAN)
    same_e = e_pl["blobs"].get("masks") == e_ps["blobs"].get("masks") and e_pl["result"].get("vols") == e_ps["result"].get("vols")
    rep["echo"] = dict(video=name, same=bool(same_e), pl=describe("echo", e_pl), ps=describe("echo", e_ps))
    ok &= bool(same_e) and rep["echo"]["pl"]["split"]
    w2 = timeline.TimelineWin(root, "echo")
    w2.add("PL 后处理", "echo", e_pl)
    w2.add("PS 后处理", "echo", e_ps, "相同" if same_e else "不同")
    w2.top.update()
    w2.cv.postscript(file=os.path.join(HERE, "board_check_v5", "timeline_echo.ps"))
    rep["echo"]["text"] = w2.var.get()
    root.destroy()
    rep["pass"] = bool(ok)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    print("TIMELINE_BOARD_PASS" if ok else "TIMELINE_BOARD_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
