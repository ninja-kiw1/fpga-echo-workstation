# -*- coding: utf-8 -*-
"""verify_board_echo_jobs.py —— 病例任务 echo_ef（③ 整段心超在板上分析，板端 src/job_echo.c）上板验收。

只用 EchoNet 的 VAL 视频（FileList 按文件名排序取前 --videos 段；TEST 不碰）。每段：
  A  PC 解码（OpenCV）后上传原始灰度帧，全帧模式：
     板上 logit == 紧凑帧逐帧发（老路，早已和整数模型逐位一致）；
     板上清理后的掩膜（PL pp_layer）、每帧质控信息 == echo_ws.frame_info（PC 上 clean）；
     板上容积 vs echo_ws.volume_pca：主轴方向按 LAPACK（dlaev2）的约定逐位复现、投影按 OpenBLAS dgemv 的 FMA 复现，
     但 q.T @ q 用的是 OpenBLAS 分块核（与 CPU 指令集有关，没法复现）；落在中间圆盘边界上的"中点像素"（两个端点像素的格点中点，
     很常见）归哪边由最后一位决定，所以少数帧差一两个像素：要求每帧相对差 < 1e-3，校准后 EF 差 < 0.01，质控等级相同；
     把板上容积喂给 echo_ws.analyze：EF、各拍、心率必须和板上的分析完全相同（考的是 medfilt / percentile / find_peaks 的移植）；
     最终 EF（校准后）、质控等级 vs PC 老流程（PC 的容积 + PC 的分析），差 < 1e-6；
  B  前 --rt 段再跑实时模式（每 k 帧分析 1 帧），同样比对；
  C  清理默认在 PL 的 pp_layer 上（v5.14 起，A / B 要求每帧都由 PL 清理）；一段改用 PS 工作线程（flags bit2），结果必须一样；一段 LIVE + PACE（模拟探头按帧率送帧），逐帧块数 == 分析帧数，
     结果与 B 的实时模式相同。
视频只在 PC 上解码（OpenCV）；板上解码（libjpeg）和 OpenCV 差几个灰度级，10-13 决定不做。
用 srtp-vision 的 python（numpy / scipy / OpenCV）：
    python verify_board_echo_jobs.py [--videos 20] [--rt 5] [--out board_check_v5/echo_jobs_acceptance.json]
"""
import argparse
import csv
import json
import os
import sys
import time
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np  # noqa: E402

import echo_ws  # noqa: E402
import imagelink  # noqa: E402
import jobs  # noqa: E402
import proto  # noqa: E402

ECHONET = os.path.normpath(os.path.join(HERE, "..", "..", "EchoNet-Dynamic"))
MODEL = next(m for m in proto.MODELS if m["name"] == "echonet_lv")
F = proto.FLAG_STATS | proto.FLAG_C_LOGIT


def val_videos(n):
    with open(os.path.join(ECHONET, "FileList.csv"), encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["Split"].upper() == "VAL"]
    names = sorted(r["FileName"] if r["FileName"].endswith(".avi") else r["FileName"] + ".avi" for r in rows)
    return names[:n]


def stream(link, payloads):
    link.connect()
    out = [None] * len(payloads)
    q, k, t0 = deque(), 0, time.perf_counter()
    while k < len(payloads) or q:
        while len(q) < 2 and k < len(payloads):
            link.send_v6c(payloads[k], 128, kind=proto.V6C_KIND_RGB, model0=MODEL["index"], thr=1, flags=F)
            q.append(k)
            k += 1
        i = q.popleft()
        st, lg, _ = link.recv_v6c(F, 128)
        out[i] = lg
    return out, time.perf_counter() - t0


def pc_pipeline(logits, fps, step, conf):
    """老流程的 PC 部分（和 echowin 一样）：逐帧 clean / 质控 / 容积，再逐拍分析。"""
    masks, infos, vols = [], [], []
    for lg in logits:
        q, raw = echo_ws.mask_from_logit(lg)
        mc, info = echo_ws.frame_info(q, raw)
        masks.append(mc)
        infos.append(info)
        vols.append(echo_ws.volume_pca(mc))
    r = echo_ws.analyze(vols, fps, step, conf["cfg"])
    return masks, infos, vols, r


def beats_of(r):
    return [(b["ed"], b["es"]) for b in r["beats"]]


def check_one(link, name, conf, realtime=False, flags_extra=0, compare_stream=True):
    path = os.path.join(ECHONET, "Videos", name)
    v, fps = echo_ws.read_avi(path)
    step = echo_ws.realtime_step(fps) if realtime else 1
    mode = "realtime" if realtime else "full"
    link.connect()
    r = jobs.echo_ef(link, conf, MODEL["scale"], frames=v, fps=fps, realtime=realtime,
                     flags=jobs.ECHO_LOGITS | jobs.ECHO_MASKS | flags_extra)
    res = r["result"]
    if not res.get("ok"):
        return dict(name=name, mode=mode, pass_=False, error=res.get("error"))
    na = res["analysed"]
    lgs = [r["blobs"]["logits"][i * 16384:(i + 1) * 16384] for i in range(na)]
    ck = {}
    ck["clean_where"] = (res["clean"]["pl"] == res["analysed"]) if not (flags_extra & jobs.ECHO_PS_CLEAN) else True
    ck["counts"] = (res["n_frames"] == len(v) and res["step"] == step and na == len(range(0, len(v), step))
                    and res["d"] == echo_ws.temporal_offset(fps) and res["fps"] == fps)
    t_stream = None
    if compare_stream:
        idx, pl = echo_ws.payloads(v, fps, MODEL.get("enc", "t3"), step)
        ls, t_stream = stream(link, pl)
        ck["logits_vs_stream"] = ls == lgs
    t0 = time.perf_counter()
    masks, infos, vols, rpc = pc_pipeline(lgs, fps, step, conf)
    lvl, reasons, score = echo_ws.qc(infos, rpc)
    t_pc = time.perf_counter() - t0
    bm = jobs.unpack_mask(r["blobs"]["masks"], (na, 112, 112))
    ck["masks"] = bool((bm == np.stack(masks)).all())
    ck["frame_info"] = all([i["ncomp"], i["area"], int(i["border"]), i["unc"]] == list(b)
                           for i, b in zip(infos, res["info"]))
    bv = np.array(res["vols"])
    pv = np.array(vols)
    rel = float(np.max(np.abs(bv - pv) / np.maximum(np.abs(pv), 1e-12))) if len(pv) else 0.0
    ck["volumes_rel_1e-3"] = rel < 1e-3
    nvd = int(np.sum(np.abs(bv - pv) > 1e-9 * np.maximum(np.abs(pv), 1e-12)))
    # 同一组（板上的）容积喂给 PC 的 analyze：移植必须完全一致
    rb = echo_ws.analyze(list(bv), fps, step, conf["cfg"])
    ck["analysis_port_exact"] = (beats_of(rb) == beats_of(res) and
                                 (rb["ef"] == res["ef_raw"] or (rb["ef"] != rb["ef"] and res["ef_raw"] is None)) and
                                 rb.get("hr") == res["hr"] and bool(rb["fallback"]) == res["fallback"])
    cal = echo_ws.calibrated(rpc["ef"], conf, mode)
    ck["ef_vs_pc_0.01"] = res["ef"] is not None and abs(res["ef"] - cal) < 0.01
    ck["beats_vs_pc"] = beats_of(rpc) == beats_of(res)
    ck["qc_level_vs_pc"] = res["qc"]["level"] == lvl and abs(res["qc"]["score"] - score) < 1e-3
    why = echo_ws.qc_bits(res["qc"]["multi"], res["qc"]["border"], res["qc"]["empty"],
                          dict(ef_sd=res.get("ef_sd"), fallback=res["fallback"], beats=res["beats"]))
    ck["qc_reasons_vs_pc"] = why == res["qc"]["why"] and echo_ws.qc_reasons(why, res["qc"], dict(
        ef_sd=res.get("ef_sd"), beats=res["beats"])) == reasons
    out = dict(name=name, mode=mode, n=len(v), fps=fps, analysed=na, checks=ck, pass_=all(ck.values()),
               ef=res["ef"], ef_pc=cal, ef_diff=None if res["ef"] is None else res["ef"] - cal,
               beats=len(res["beats"]), qc=res["qc"]["level"], vol_rel_max=rel, vol_frames_differ=nvd,
               clean=res["clean"], job_s=round(r["wall_s"], 3), board_ms=res["ms"], pl_ms=res["pl_ms"],
               stream_s=None if t_stream is None else round(t_stream, 3), pc_post_s=round(t_pc, 3),
               duration_s=len(v) / fps)
    print("  %-26s %-8s %3d/%3d fr  %s  EF %.4f (PC %.4f)  beats %d  QC %d  vol rel %.1e (%d fr)  job %.2f s  "
          "stream %s + PC %.2f s  clean PL %d/%d"
          % (name, mode, na, len(v), "PASS" if out["pass_"] else "FAIL " + str([k for k, x in ck.items() if not x]),
             res["ef"] if res["ef"] is not None else float("nan"), cal, len(res["beats"]), res["qc"]["level"], rel, nvd,
             r["wall_s"], "%.2f" % t_stream if t_stream is not None else "-", t_pc, res["clean"]["pl"], na), flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.254")
    ap.add_argument("--port", type=int, default=5060)
    ap.add_argument("--videos", type=int, default=20)
    ap.add_argument("--rt", type=int, default=5)
    ap.add_argument("--out", default=os.path.join(HERE, "board_check_v5", "echo_jobs_acceptance.json"))
    a = ap.parse_args()
    conf = echo_ws.load_config()
    echo_ws.SCALE[0] = MODEL["scale"]
    echo_ws.THRESH = tuple(conf.get("qc_thresholds", echo_ws.THRESH))
    link = imagelink.ImageLink(a.host, a.port, timeout=20.0)
    rep = dict(date=time.strftime("%Y-%m-%d %H:%M:%S"), caps=jobs.caps(link))
    print("caps:", rep["caps"], flush=True)
    names = val_videos(a.videos)
    print("== A full mode, PC-decoded frames (%d VAL videos)" % len(names), flush=True)
    rep["full"] = [check_one(link, n, conf) for n in names]
    print("== B realtime mode", flush=True)
    rep["realtime"] = [check_one(link, n, conf, realtime=True) for n in names[:a.rt]]
    print("== C PS-worker clean / live + paced", flush=True)
    rep["ps_clean"] = check_one(link, names[0], conf, flags_extra=jobs.ECHO_PS_CLEAN, compare_stream=False)
    rep["ps_clean"]["pass_"] &= rep["ps_clean"]["clean"]["pl"] == 0
    frames, prog = [], []
    v, fps = echo_ws.read_avi(os.path.join(ECHONET, "Videos", names[0]))
    link.connect()
    t0 = time.perf_counter()
    r = jobs.echo_ef(link, conf, MODEL["scale"], frames=v, fps=fps, realtime=True,
                     flags=jobs.ECHO_LIVE | jobs.ECHO_PACE, on_frame=frames.append, on_progress=prog.append)
    wall = time.perf_counter() - t0
    res = r["result"]
    rt_ref = next(x for x in rep["realtime"] if x["name"] == names[0])
    rep["live"] = dict(name=names[0], frames=len(frames), analysed=res["analysed"], lag_s=res.get("lag_s"),
                       wall_s=wall, duration_s=len(v) / fps, live_ef_updates=sum(1 for p in prog if "ef_raw" in p),
                       ef=res["ef"], ef_rt=rt_ref["ef"],
                       pass_=len(frames) == res["analysed"] and res["ef"] == rt_ref["ef"] and
                       [f["vol"] for f in frames] == res["vols"])
    print("  live+paced %s: %d frame blobs / %d analysed, %d rolling-EF updates, wall %.2f s for a %.2f s clip, "
          "last frame lag %.0f ms, EF %.2f (= realtime run: %s)"
          % (names[0], len(frames), res["analysed"], rep["live"]["live_ef_updates"], wall, len(v) / fps,
             1000 * (res.get("lag_s") or 0), res["ef"], res["ef"] == rt_ref["ef"]), flush=True)
    ok = (all(x["pass_"] for x in rep["full"] + rep["realtime"]) and rep["ps_clean"]["pass_"]
          and rep["live"]["pass_"])
    full = rep["full"]
    rep["summary"] = dict(
        videos=len(full), job_s=sum(x["job_s"] for x in full), old_s=sum(x["stream_s"] + x["pc_post_s"] for x in full),
        pl_s=sum(x["pl_ms"] for x in full) / 1000, video_s=sum(x["duration_s"] for x in full),
        vol_rel_max=max(x["vol_rel_max"] for x in full),
        vol_frames_differ=sum(x["vol_frames_differ"] for x in full), frames=sum(x["analysed"] for x in full),
        ef_diff_abs_max=max(abs(x["ef_diff"]) for x in full if x["ef_diff"] is not None),
        worker_ms=sum(x["board_ms"].get("worker", 0) for x in full))
    print("summary:", rep["summary"], flush=True)
    rep["pass"] = bool(ok)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    print("ECHO_JOBS_BOARD_PASS" if ok else "ECHO_JOBS_BOARD_FAIL", "->", a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
