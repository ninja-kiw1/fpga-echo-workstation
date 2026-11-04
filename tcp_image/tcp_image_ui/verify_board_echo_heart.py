# -*- coding: utf-8 -*-
"""verify_board_echo_heart.py —— 服务端 v5.24 全心扩展（echo_ef mode 2 / 3 + "HC01"：节段室壁运动 + 右室 / 右房）上板验收。

主机参考 = E:/PLD/keyframe/heart_ref.py（同一份 C：src/cardiac_ext.c 编进 echo_core.dll，调用顺序 = job_echo.c heart_json），
输入 = 主机参考引擎的左室掩膜（verify_board_echo_kf.host_reference）+ 程序 21 / 22 的整数 logit（E:/PLD/wallmotion/rh_logits.py，
GPU 整数路径先与 CPU 参考逐位核过；程序包板上自检逐位）。逐位比较：
  A. EchoNet VAL 前 --videos 段（TEST 不碰）mode 2 推荐档 + HC01：key / 掩膜 / 容积 / EF 照旧，另比 "wall_motion" 全部字段、
     "right_heart" 全部字段、"rh_masks" 块；
  B. HMC-QU DEV 前 --hmc 段（TEST 不碰）同上；
  C. mode 3（板上 .y8 源，固定裁切）+ HC01：板上全心结果 == 把板上回来的 112 帧再当 mode 2 + HC01 跑一次；
  D. 不带 HC01 的 mode 2：结果里没有 "wall_motion" / "right_heart"（与 v5.23 相同的字段）。
用 srtp-vision 的 python：
    python verify_board_echo_heart.py [--videos 20] [--hmc 20] [--out board_check_v5/echo_heart_acceptance.json]
"""
import argparse
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
import heart_ref  # noqa: E402
import imagelink  # noqa: E402
import jobs  # noqa: E402
from verify_board_echo_kf import ECHONET, E, MODEL, host_reference, val_videos  # noqa: E402

WM = os.path.join(PLD, "wallmotion")


def run_one(link, label, v, fps, q, rv_q, ra_q, kf, conf, hc, block):
    link.connect()
    r = jobs.echo_ef(link, conf, MODEL["scale"], frames=v, fps=fps, keyframe=kf, flags=jobs.ECHO_MASKS, heart=hc)
    res = r["result"]
    if not res.get("ok"):
        return dict(name=label, pass_=False, error=res.get("error"))
    h = host_reference(v, fps, q, kf, conf)
    bm = jobs.unpack_mask(r["blobs"]["masks"], (len(v), 112, 112))
    ck = dict(key=res["key"] == h["key"], masks=bool((bm == h["masks"]).all()), vols=list(res["vols"]) == list(h["vols"]),
              ef=res["ef"] == h["ef"])
    par = echo_core.hc_par(block)
    beats = [(b["ed"], b["es"]) for b in res["beats"]]
    hh, rh_h = heart_ref.heart_reference(par, 3, np.asarray(h["masks"], bool), beats, h["vols"], h["ef"], rv_q, ra_q)
    rh_b = jobs.unpack_rh(r["blobs"]["rh_masks"], len(res["right_heart"]["pairs"])) if "rh_masks" in r["blobs"] else []
    ck.update(heart_ref.compare(res, hh, rh_b, rh_h))
    ck = {k: bool(x) for k, x in ck.items()}
    wm, rh = res.get("wall_motion"), res.get("right_heart") or {}
    out = dict(name=label, checks=ck, pass_=all(ck.values()), ef=res["ef"], wall_motion=wm,
               right_heart={k: rh.get(k) for k in ("n_rv", "n_ra", "rv_lv", "rv_fac", "ra_lv", "pl_ms", "error")},
               board_ms=res["ms"], job_s=round(r["wall_s"], 3))
    print("  %-28s %s  EF %s  WM %s seg %s  RV/LV %s FAC %s RA/LV %s (%d/%d pairs ok)  RH PL %.1f ms  job %.2f s" % (
        label, "PASS" if out["pass_"] else "FAIL " + str([k for k, x in ck.items() if not x]),
        "%.1f" % res["ef"] if res["ef"] is not None else "-",
        ("%.2f/%s" % (wm["score"], wm["flag"])) if wm and wm["score"] is not None else "-",
        "".join("1" if s else "0" for s in wm["seg"]) if wm else "-",
        "%.2f" % rh["rv_lv"] if rh.get("rv_lv") is not None else "-",
        "%.2f" % rh["rv_fac"] if rh.get("rv_fac") is not None else "-",
        "%.2f" % rh["ra_lv"] if rh.get("ra_lv") is not None else "-", rh.get("n_rv", 0), len(rh.get("pairs", [])),
        rh.get("pl_ms", 0.0), r["wall_s"]), flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.254")
    ap.add_argument("--port", type=int, default=5060)
    ap.add_argument("--videos", type=int, default=20)
    ap.add_argument("--hmc", type=int, default=20)
    ap.add_argument("--out", default=os.path.join(HERE, "board_check_v5", "echo_heart_acceptance.json"))
    a = ap.parse_args()
    conf = echo_ws.load_config()
    kf = jobs.load_kf_config("keyframe")
    hc = jobs.load_heart_config()
    block = jobs.heart_block(hc)
    link = imagelink.ImageLink(a.host, a.port, timeout=60.0)
    rep = dict(date=time.strftime("%Y-%m-%d %H:%M:%S"), caps=jobs.caps(link), runs=[])
    print("caps:", rep["caps"], flush=True)
    rep["caps_ok"] = "heart" in rep["caps"]
    # A. EchoNet VAL
    idx = json.load(open(os.path.join(E, "results", "frames", "t3_int_val.json"), encoding="utf-8"))["videos"]
    qall = np.load(os.path.join(PLD, "quality", "results", "q", "t3_val_q112.npy"), mmap_mode="r")
    rhz = np.load(os.path.join(WM, "results", "echonet_val_rh.npz"))
    rh_names = list(rhz["names"])
    for name in val_videos(a.videos):
        v, fps = echo_ws.read_avi(os.path.join(ECHONET, "Videos", name))
        e = idx[name]
        k = rh_names.index(name)
        s = slice(int(rhz["starts"][k]), int(rhz["starts"][k + 1]))
        q = np.ascontiguousarray(qall[e["start"]:e["start"] + e["n"]])
        rep["runs"].append(dict(set="echonet_val", **run_one(link, name, v, fps, q, rhz["rv"][s], rhz["ra"][s], kf, conf,
                                                               hc, block)))
    # B. HMC-QU DEV
    C = os.path.join(WM, "results", "cache")
    hidx = json.load(open(os.path.join(C, "index.json")))
    split = json.load(open(os.path.join(WM, "results", "split.json")))["split"]
    F, Q = np.load(os.path.join(C, "frames.npy"), mmap_mode="r"), np.load(os.path.join(C, "q.npy"), mmap_mode="r")
    RV, RA = np.load(os.path.join(C, "rv_q.npy"), mmap_mode="r"), np.load(os.path.join(C, "ra_q.npy"), mmap_mode="r")
    for name in sorted(n for n in hidx if split[n] == "dev")[:a.hmc]:
        e = hidx[name]
        s = slice(e["start"], e["start"] + e["n"])
        rep["runs"].append(dict(set="hmcqu_dev", **run_one(link, "HMC " + name, np.ascontiguousarray(F[s]), e["fps"],
                                                             np.ascontiguousarray(Q[s]), RV[s], RA[s], kf, conf, hc,
                                                             block)))
    # C. mode 3 + HC01 == mode 2 + HC01 on the returned frames (board vs board)
    src = json.load(open(os.path.join(PLD, "live", "src", "index.json"), encoding="utf-8"))
    nm = next((n for n in sorted(src) if n.endswith("_z4.y8")), None)
    s0 = dict(src[nm], name=nm) if nm else None
    if s0 is not None:
        link.connect()
        r3 = jobs.echo_live(link, conf, MODEL["scale"], kf, s0["name"], crop=(s0["x"], s0["y"], s0["side"]), heart=hc)
        res3 = r3["result"]
        fr = np.frombuffer(r3["blobs"]["frames112"], np.uint8).reshape(-1, 112, 112)
        link.connect()
        r2 = jobs.echo_ef(link, conf, MODEL["scale"], frames=fr, fps=res3["fps"], keyframe=kf, flags=jobs.ECHO_MASKS,
                          heart=hc)
        res2 = r2["result"]
        same = res3.get("wall_motion") == res2.get("wall_motion") and res3.get("right_heart", {}).get("pairs") == \
            res2.get("right_heart", {}).get("pairs") and r3["blobs"].get("rh_masks") == r2["blobs"].get("rh_masks")
        rep["runs"].append(dict(set="live", name="live " + s0["name"], pass_=bool(same and res3.get("ok")),
                                wall_motion=res3.get("wall_motion"),
                                right_heart={k: res3.get("right_heart", {}).get(k) for k in ("rv_lv", "rv_fac", "ra_lv")}))
        print("  live %-23s %s (mode 3 heart == mode 2 heart on the board's frames)" % (s0["name"], "PASS" if same else "FAIL"),
              flush=True)
    else:
        rep["runs"].append(dict(set="live", name="live", pass_=False, error="no .y8 source index"))
    # D. no HC01 -> no heart members
    name = val_videos(1)[0]
    v, fps = echo_ws.read_avi(os.path.join(ECHONET, "Videos", name))
    link.connect()
    r = jobs.echo_ef(link, conf, MODEL["scale"], frames=v, fps=fps, keyframe=kf, flags=jobs.ECHO_MASKS)
    plain = "wall_motion" not in r["result"] and "right_heart" not in r["result"] and "rh_masks" not in r["blobs"]
    rep["runs"].append(dict(set="no_hc", name=name, pass_=bool(plain)))
    print("  no HC01 %-20s %s" % (name, "PASS" if plain else "FAIL"), flush=True)
    ok = rep["caps_ok"] and all(r_["pass_"] for r_ in rep["runs"])
    hm = [r_ for r_ in rep["runs"] if r_.get("set") in ("echonet_val", "hmcqu_dev") and r_.get("right_heart")]
    rep["summary"] = dict(runs=len(rep["runs"]), pass_=ok,
                          rh_pl_ms_mean=float(np.mean([r_["right_heart"]["pl_ms"] for r_ in hm])) if hm else None)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(json.dumps(rep["summary"], ensure_ascii=False))
    print("ECHO_HEART_BOARD_PASS" if ok else "ECHO_HEART_BOARD_FAIL")


if __name__ == "__main__":
    main()
