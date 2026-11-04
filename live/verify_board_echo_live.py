# -*- coding: utf-8 -*-
"""verify_board_echo_live.py —— 服务端 v5.22 echo_ef mode 3（现场模式，板上信号源）上板验收 + F2 / F4 的计时数据。

信号源 = make_y8.py 做的"模拟采集画面"（EchoNet VAL，TEST 不碰），先传到板上 /root/echo_src（已有且大小相同就不传）。
每个整数放大（z4）的源：
  A 推荐档 + 固定裁切（真值方块）：板上裁好的 112 帧 == 原视频（逐位）；结果（关键帧序列、每帧掩膜、容积、EF、灯色、
    区间）== 主机参考 host_reference(原视频)；每帧 "live" 块的掩膜 / 容积 == 最终结果；
  B 推荐档 + 自动裁切：报告找到的方块与真值的偏差；结果 == 把板上裁好的 112 帧当成 mode 2 任务再跑一次（板上，同一引擎）；
  C 每帧推理 + 固定裁切：结果 == 主机参考（full）；计时看"每帧都上 PL"时跟不跟得上帧率。
非整数放大（z2p68）的源：只做 B。
计时（每帧 7 个时间戳，job_echo.c）：延迟 = 采到 -> 掩膜和容积算完；存 results/live_trace.npz 给 f2_f4_report.py。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe verify_board_echo_live.py
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLD = os.path.normpath(os.path.join(HERE, ".."))
UI = os.path.join(PLD, "tcp_image", "tcp_image_ui")
sys.path.insert(0, UI)
sys.path.insert(0, os.path.join(PLD, "keyframe"))
sys.path.insert(0, os.path.join(PLD, "tcp_image", ".board_tools"))
os.environ["PATH"] = r"C:\msys64\mingw64\bin;" + os.environ["PATH"]
import numpy as np  # noqa: E402

import echo_ws  # noqa: E402
import imagelink  # noqa: E402
import jobs  # noqa: E402
from verify_board_echo_kf import ECHONET, E, MODEL, host_reference  # noqa: E402

HOST = "192.168.1.254"
REMOTE = "/root/echo_src"
SKIP = ("mode", "stream", "upload_bytes", "ms", "pl_ms", "pl_split")


def upload(names):
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password="root", timeout=10, allow_agent=False, look_for_keys=False)
    try:
        c.exec_command("mkdir -p " + REMOTE)[1].channel.recv_exit_status()
        s = c.open_sftp()
        for n in names:
            local = os.path.join(HERE, "src", n)
            try:
                same = s.stat(REMOTE + "/" + n).st_size == os.path.getsize(local)
            except IOError:
                same = False
            if not same:
                t0 = time.time()
                s.put(local, REMOTE + "/" + n)
                print("  uploaded %s (%.0f MB, %.1f s)" % (n, os.path.getsize(local) / 2 ** 20, time.time() - t0), flush=True)
    finally:
        c.close()


def core(res):
    """结果里与时间、模式、流信息无关的部分"""
    r = {k: v for k, v in res.items() if k not in SKIP}
    if "keyframe" in r:
        r["keyframe"] = {k: v for k, v in r["keyframe"].items()
                         if k not in ("pl_ms_per_key", "track_ms_per_frame", "track_ms_max")}
    return r


def digest(res, masks):
    h = hashlib.sha256(json.dumps(core(res), sort_keys=True, separators=(",", ":")).encode())
    h.update(masks)
    return h.hexdigest()


def host_check(res, blobs, h):
    hm = np.packbits(np.asarray(h["masks"], bool).ravel(), bitorder="little").tobytes()
    iv, hi = res.get("interval"), h["interval"]
    return dict(key=res["key"] == h["key"], masks=blobs["masks"][:len(hm)] == hm,
                vols=list(res["vols"]) == list(h["vols"]), ef=res["ef"] == h["ef"],
                light=res["quality"]["light"] == h["light"],
                interval=(iv is None and hi is None) or (iv is not None and abs(iv["sigma"] - hi["sigma"]) <=
                                                         1e-12 * hi["sigma"] and iv["review"] == hi["review"]))


def run_live(link, conf, kf, name, crop, live):
    link.connect()
    t0 = time.perf_counter()
    prog = []
    r = jobs.echo_live(link, conf, MODEL["scale"], kf, name, crop=crop, every=1,
                       on_live=lambda d: live.append(d), on_progress=lambda p: prog.append(p))
    r["pc_wall_s"] = time.perf_counter() - t0
    r["live_progress"] = [p for p in prog if p.get("stage") == "live" and "ef" in p]
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "results", "live_acceptance.json"))
    a = ap.parse_args()
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    idx = json.load(open(os.path.join(HERE, "src", "index.json"), encoding="utf-8"))
    conf = echo_ws.load_config()
    fidx = json.load(open(os.path.join(E, "results", "frames", "t3_int_val.json"), encoding="utf-8"))["videos"]
    qall = np.load(os.path.join(PLD, "quality", "results", "q", "t3_val_q112.npy"), mmap_mode="r")
    link = imagelink.ImageLink(HOST, 5060, timeout=30.0)
    caps = jobs.caps(link)
    print("caps:", caps, flush=True)
    rep = dict(date=time.strftime("%Y-%m-%d %H:%M:%S"), caps=caps, runs=[],
               caps_ok=3 in caps.get("echo_modes", []))                       # v5.22+
    upload(sorted(idx))
    traces = {}
    for fn, m in idx.items():
        v, fps = echo_ws.read_avi(os.path.join(ECHONET, "Videos", m["video"]))
        e = fidx[m["video"]]
        q = np.ascontiguousarray(qall[e["start"]:e["start"] + e["n"]])
        truth = (m["x"], m["y"], m["side"])
        cases = [("B", "keyframe", None)] if m["zoom"] != 4 else \
            [("A", "keyframe", truth), ("B", "keyframe", None), ("C", "full", truth)]
        for case, cfg_name, crop in cases:
            kf = jobs.load_kf_config(cfg_name)
            live = []
            r = run_live(link, conf, kf, fn, crop, live)
            res, bl = r["result"], r["blobs"]
            out = dict(source=fn, case=case, config=cfg_name, crop=crop, ok=bool(res.get("ok")))
            if not res.get("ok"):
                out.update(error=res.get("error"), pass_=False)
                rep["runs"].append(out)
                print("  %-30s %s FAIL %s" % (fn, case, res.get("error")), flush=True)
                continue
            st = res["stream"]
            f112 = np.frombuffer(bl["frames112"], np.uint8).reshape(-1, 112, 112)
            tr = np.frombuffer(bl["trace"], np.uint32).reshape(-1, 7)
            n = res["n_frames"]
            bits = np.unpackbits(np.frombuffer(bl["masks"], np.uint8), bitorder="little")[:n * 112 * 112] \
                .reshape(n, 112, 112).astype(bool)
            ck = dict(live_blobs=len(live) == n and all(
                d["t"] == t and np.array_equal(np.unpackbits(np.frombuffer(d["mask"], np.uint8), bitorder="little")
                                               [:112 * 112].reshape(112, 112).astype(bool), bits[t])
                and d["vol"] == res["vols"][t] and d["key"] == (res["key"][t] == "1") for t, d in enumerate(live)),
                all_frames=n == len(v))
            if crop is not None:
                ck["frames112_equal_video"] = f112.shape == v.shape and np.array_equal(f112, v)
                ck.update({"host_" + k: x for k, x in host_check(res, bl, host_reference(v, fps, q, kf, conf)).items()})
            else:
                link.connect()
                r2 = jobs.echo_ef(link, conf, MODEL["scale"], frames=f112, fps=fps, flags=jobs.ECHO_MASKS, keyframe=kf)
                ck["equal_mode2_on_frames112"] = digest(res, bl["masks"]) == digest(r2["result"], r2["blobs"]["masks"])
                out["box"] = [st["crop"]["x"], st["crop"]["y"], st["crop"]["side"]]
                out["box_vs_truth_px"] = [st["crop"]["x"] - m["x"], st["crop"]["y"] - m["y"], st["crop"]["side"] - m["side"]]
            lat = (tr[:, 5].astype(np.int64) - tr[:, 0]) / 1000.0
            work = (tr[:, 4].astype(np.int64) - tr[:, 3]) / 1000.0
            key = np.array([c == "1" for c in res["key"]])
            out.update(checks=ck, pass_=all(ck.values()), frames=n, fps=fps, ef=res["ef"], key_frac=res["keyframe"]["key_frac"],
                       light=res["quality"]["light"], interval=res.get("interval"),
                       latency_ms=st["latency_ms"], latency_after_detect_ms=dict(
                           p50=float(np.median(lat[st["detect_frames"]:])) if n > st["detect_frames"] else None,
                           max=float(lat[st["detect_frames"]:].max()) if n > st["detect_frames"] else None),
                       pl_ms=float(work[key].mean()) if key.any() else None,
                       track_ms=float(work[~key].mean()) if (~key).any() else None,
                       resize_ms=st["resize_ms_mean"], board_total_s=res["ms"]["total"] / 1000.0,
                       clip_s=n / fps, rolling_updates=len(r["live_progress"]),
                       last_rolling_ef=r["live_progress"][-1]["ef"] if r["live_progress"] else None)
            traces["%s|%s" % (fn, case)] = tr
            traces["%s|%s|key" % (fn, case)] = key
            rep["runs"].append(out)
            print("  %-30s %s %-8s %s  EF %.2f key %.3f  latency p50 %.1f / p99 %.1f / max %.1f ms (after detect p50 %s)"
                  "  PL %.1f  track %s  resize %.2f ms  %s" % (
                      fn, case, cfg_name, "PASS" if out["pass_"] else "FAIL " + str([k for k, x in ck.items() if not x]),
                      res["ef"], res["keyframe"]["key_frac"], st["latency_ms"]["p50"], st["latency_ms"]["p99"],
                      st["latency_ms"]["max"], "%.1f" % out["latency_after_detect_ms"]["p50"]
                      if out["latency_after_detect_ms"]["p50"] is not None else "-", out["pl_ms"] or 0,
                      "%.2f" % out["track_ms"] if out["track_ms"] is not None else "-", st["resize_ms_mean"],
                      ("box %s (truth %s)" % (out["box"], list(truth))) if crop is None else ""), flush=True)
    rep["pass_"] = rep["caps_ok"] and all(r_["pass_"] for r_ in rep["runs"])
    np.savez_compressed(os.path.join(HERE, "results", "live_trace.npz"), **traces)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("ECHO_LIVE_BOARD_PASS" if rep["pass_"] else "ECHO_LIVE_BOARD_FAIL")


if __name__ == "__main__":
    main()
