# -*- coding: utf-8 -*-
"""verify_board_echo_kf.py —— 服务端 v5.21 echo_ef mode 2（创新 ①②③：关键帧调度 + 质量灯 + 保形 EF 区间）上板验收。

只用 EchoNet VAL（FileList 按文件名排序取前 --videos 段；TEST 不碰）。每段视频、两套配置（echo_kf_config.json 的
"keyframe" 推荐档和 "full" 每帧推理）各跑一次 jobs.echo_ef(..., keyframe=cfg)，与主机参考比：
  主机参考 = 同一份 C（E:/PLD/keyframe/echo_core.dll，由 tcp_image_rx/src 的 echo_num.c / kf.c / echo_quality.c / bm_sad.c
  按板上 makefile 的数值选项编出），输入是程序 16 的整数 logit（E:/PLD/quality/results/q，与板上 PL 逐位一致的整数模型），
  调用顺序与 src/job_echo.c job_echo_kf 相同：clean -> kf 调度 / 跟踪 -> 质量特征 -> 容积 -> 逐拍 -> 校准 -> 区间。
  逐位相同：关键帧序列（"key"）、每帧掩膜、每帧容积、EF / 各拍 / 心率、9 个质量特征、得分、灯色、原因；
  区间：sigma 相对差 < 1e-12（exp() 是两套 libm），复核标志相同。
另记：关键帧占比、每关键帧 PL 时间、每跟踪帧 PS 时间（板上 A53 实测，对照主机仿真的推算 0.6–1.0 ms）。
先确认 PC 解码的帧与缓存（主机参考用的）逐位相同。
用 srtp-vision 的 python：
    python verify_board_echo_kf.py [--videos 20] [--out board_check_v5/echo_kf_acceptance.json]
"""
import argparse
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLD = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PLD, "keyframe"))
os.environ["PATH"] = r"C:\msys64\mingw64\bin;" + os.environ["PATH"]
import numpy as np  # noqa: E402

import echo_core  # noqa: E402
import echo_ws  # noqa: E402
import imagelink  # noqa: E402
import jobs  # noqa: E402
import proto  # noqa: E402

ECHONET = os.path.join(PLD, "EchoNet-Dynamic")
E = os.path.join(PLD, "echonet")
MODEL = next(m for m in proto.MODELS if m["name"] == "echonet_lv")


def val_videos(n):
    with open(os.path.join(ECHONET, "FileList.csv"), encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["Split"].upper() == "VAL"]
    return sorted(r["FileName"] if r["FileName"].endswith(".avi") else r["FileName"] + ".avi" for r in rows)[:n]


def host_reference(v, fps, q, kf, conf):
    cm, nc = zip(*[echo_core.clean112(x >= 1) for x in q])
    cm, nc = np.stack(cm), np.array(nc, np.int32)
    p = dict(kf["policy"])
    masks, key, _ = echo_core.propagate(v, cm, p, kf["track"], fps)
    feats = echo_core.clip_features(v, q, masks, key, nc)
    vols = [echo_core.volume_pca(m) for m in masks]
    z = echo_core.analyze(vols, fps, 1, conf["cfg"])
    a, b = kf["a"], kf["b"]
    ef = a * z["ef"] + b if z["ef"] == z["ef"] else None
    m = kf["model"]
    score = m["w"][0]
    contrib = []
    for i, k in enumerate(echo_core.QF):                     # same order as eq_acc_finish
        c = m["w"][i + 1] * ((feats[k] - m["mu"][i]) / m["sd"][i])
        contrib.append(c)
        score += c
    light = 2 if score >= m["t_red"] else (1 if score >= m["t_green"] else 0)
    iv = None
    if ef is not None:
        iv = echo_core.interval(echo_core.eq_model(dict(m, b=m["b_sigma"])), ef,
                                (z["ef_sd"] or 0.0) * abs(a), len(z["beats"]), int(z["fallback"]), feats)
    return dict(masks=masks, key="".join("1" if k else "0" for k in key), vols=vols, z=z, ef=ef, feats=feats,
                score=score, light=light, interval=iv)


def check(link, name, v, fps, q, kf, mode, conf):
    link.connect()
    r = jobs.echo_ef(link, conf, MODEL["scale"], frames=v, fps=fps, keyframe=kf, flags=jobs.ECHO_MASKS)
    res = r["result"]
    if not res.get("ok"):
        return dict(name=name, mode=mode, pass_=False, error=res.get("error"))
    h = host_reference(v, fps, q, kf, conf)
    ck = {}
    ck["key"] = res["key"] == h["key"]
    bm = jobs.unpack_mask(r["blobs"]["masks"], (len(v), 112, 112))
    ck["masks"] = bool((bm == h["masks"]).all())
    ck["vols"] = list(res["vols"]) == list(h["vols"])
    ck["ef"] = res["ef_raw"] == h["z"]["ef"] and res["ef"] == h["ef"]
    ck["beats"] = [(b_["ed"], b_["es"], b_["ef"]) for b_ in res["beats"]] == [(int(b_[0]), int(b_[1]), b_[4]) for b_ in h["z"]["beats"]]
    ck["hr"] = res["hr"] == h["z"]["hr"]
    qb = res["quality"]
    ck["features"] = all(qb["features"][k] == h["feats"][k] for k in echo_core.QF)
    ck["score_light"] = qb["score"] == h["score"] and qb["light"] == h["light"]
    iv = res["interval"]
    ck["interval"] = (iv is None and h["interval"] is None) or (
        iv is not None and abs(iv["sigma"] - h["interval"]["sigma"]) <= 1e-12 * h["interval"]["sigma"]
        and iv["review"] == h["interval"]["review"])
    ck = {k: bool(x) for k, x in ck.items()}
    k_ = res["keyframe"]
    out = dict(name=name, mode=mode, n=len(v), fps=fps, checks=ck, pass_=all(ck.values()), ef=res["ef"],
               key_frac=k_["key_frac"], reasons=k_["reasons"], pl_ms_per_key=k_["pl_ms_per_key"],
               track_ms_per_frame=k_["track_ms_per_frame"], track_ms_max=k_["track_ms_max"],
               light=qb["light"], reasons_q=qb["reasons"], interval=iv, job_s=round(r["wall_s"], 3), board_ms=res["ms"])
    print("  %-26s %-8s %s  key %.3f  EF %.2f  light %d  80%% [%s]  track %.3f ms/fr (max %.2f)  PL %.1f ms/key  job %.2f s"
          % (name, mode, "PASS" if out["pass_"] else "FAIL " + str([k for k, x in ck.items() if not x]), k_["key_frac"],
             res["ef"] if res["ef"] is not None else float("nan"), qb["light"],
             "%.1f-%.1f" % (iv["lo80"], iv["hi80"]) if iv else "-", k_["track_ms_per_frame"], k_["track_ms_max"],
             k_["pl_ms_per_key"], r["wall_s"]), flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.254")
    ap.add_argument("--port", type=int, default=5060)
    ap.add_argument("--videos", type=int, default=20)
    ap.add_argument("--out", default=os.path.join(HERE, "board_check_v5", "echo_kf_acceptance.json"))
    a = ap.parse_args()
    conf = echo_ws.load_config()
    idx = json.load(open(os.path.join(E, "results", "frames", "t3_int_val.json"), encoding="utf-8"))["videos"]
    vid = json.load(open(os.path.join(E, "data", "cache", "val_videos.json"), encoding="utf-8"))
    cache = np.load(os.path.join(E, "data", "cache", "val_videos.npy"), mmap_mode="r")
    qall = np.load(os.path.join(PLD, "quality", "results", "q", "t3_val_q112.npy"), mmap_mode="r")
    link = imagelink.ImageLink(a.host, a.port, timeout=30.0)
    rep = dict(date=time.strftime("%Y-%m-%d %H:%M:%S"), caps=jobs.caps(link), runs=[])
    print("caps:", rep["caps"], flush=True)
    rep["caps_ok"] = 2 in rep["caps"].get("echo_modes", [])           # v5.21+
    for name in val_videos(a.videos):
        v, fps = echo_ws.read_avi(os.path.join(ECHONET, "Videos", name))
        e, ev = idx[name], vid[name]
        same = np.array_equal(v, cache[ev["start"]:ev["start"] + ev["n"]]) and fps == e["fps"]
        if not same:
            rep["runs"].append(dict(name=name, pass_=False, error="PC-decoded frames differ from the cache"))
            continue
        q = np.ascontiguousarray(qall[e["start"]:e["start"] + e["n"]])
        for mode in ("keyframe", "full"):
            rep["runs"].append(check(link, name, v, fps, q, jobs.load_kf_config(mode), mode, conf))
    ok = all(r_["pass_"] for r_ in rep["runs"]) and rep["caps_ok"]
    kfr = [r_ for r_ in rep["runs"] if r_.get("mode") == "keyframe" and "track_ms_per_frame" in r_]
    rep["summary"] = dict(runs=len(rep["runs"]), pass_=ok,
                          track_ms_per_frame_mean=float(np.mean([r_["track_ms_per_frame"] for r_ in kfr])) if kfr else None,
                          track_ms_max=float(np.max([r_["track_ms_max"] for r_ in kfr])) if kfr else None,
                          pl_ms_per_key_mean=float(np.mean([r_["pl_ms_per_key"] for r_ in kfr])) if kfr else None,
                          key_frac_mean=float(np.mean([r_["key_frac"] for r_ in kfr])) if kfr else None)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    print(json.dumps(rep["summary"], ensure_ascii=False))
    print("ECHO_KF_BOARD_PASS" if ok else "ECHO_KF_BOARD_FAIL")


if __name__ == "__main__":
    main()
