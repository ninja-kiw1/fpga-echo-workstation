# -*- coding: utf-8 -*-
"""F3 逐位可复现：同一段视频在板上重复跑 N 次，结果哈希完全相同，且等于主机参考（同一份 C 编的 echo_core.dll）。

EchoNet VAL 按文件名排序的前 --videos 段（TEST 不碰），每段：
  * mode 2 关键帧推荐档（echo_kf_config.json "keyframe"）× N 次；
  * mode 0 每帧推理（老路径，v5.12 起）× N 次。
哈希 = sha256(规范化 JSON(结果里与时间无关的全部字段) + 掩膜块)。时间字段（ms / pl_ms / 每关键帧 PL / 每跟踪帧 PS /
PL 分段统计 / 上传字节）单独记下，给出 N 次的中位数、最小、最大，作为"结果确定、时间也稳"的证据。
mode 2 另与主机参考比：关键帧序列、每帧掩膜、每帧容积、EF 逐位相同。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe f3_repeat.py [--videos 5] [--n 20]
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
os.environ["PATH"] = r"C:\msys64\mingw64\bin;" + os.environ["PATH"]
import numpy as np  # noqa: E402

import echo_ws  # noqa: E402
import imagelink  # noqa: E402
import jobs  # noqa: E402
from verify_board_echo_kf import ECHONET, E, MODEL, host_reference, val_videos  # noqa: E402

TIME_KEYS = ("ms", "pl_ms", "pl_split", "upload_bytes", "paced", "lag_s", "pp_ms", "worker_ms")
KF_TIME = ("pl_ms_per_key", "track_ms_per_frame", "track_ms_max")


def split_times(res):
    """结果 -> (与时间无关的部分, 时间字段)"""
    r = {k: v for k, v in res.items() if k not in TIME_KEYS and not k.startswith("pl_")}
    t = {k: res[k] for k in res if k in TIME_KEYS or k.startswith("pl_")}
    if "keyframe" in r:
        r["keyframe"] = {k: v for k, v in r["keyframe"].items() if k not in KF_TIME}
        t.update({k: res["keyframe"][k] for k in KF_TIME})
    return r, t


def digest(res, blobs):
    r, _ = split_times(res)
    h = hashlib.sha256(json.dumps(r, sort_keys=True, separators=(",", ":")).encode())
    h.update(blobs.get("masks", b""))
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", type=int, default=5)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", default=os.path.join(HERE, "f3_repeat.json"))
    a = ap.parse_args()
    conf = echo_ws.load_config()
    idx = json.load(open(os.path.join(E, "results", "frames", "t3_int_val.json"), encoding="utf-8"))["videos"]
    qall = np.load(os.path.join(PLD, "quality", "results", "q", "t3_val_q112.npy"), mmap_mode="r")
    kf = jobs.load_kf_config("keyframe")
    link = imagelink.ImageLink("192.168.1.254", 5060, timeout=30.0)
    rep = dict(date=time.strftime("%Y-%m-%d %H:%M:%S"), caps=jobs.caps(link), n=a.n, videos=[])
    print("caps:", rep["caps"], flush=True)
    for name in val_videos(a.videos):
        v, fps = echo_ws.read_avi(os.path.join(ECHONET, "Videos", name))
        e = idx[name]
        q = np.ascontiguousarray(qall[e["start"]:e["start"] + e["n"]])
        h = host_reference(v, fps, q, kf, conf)
        hm = np.packbits(np.asarray(h["masks"], bool).ravel(), bitorder="little").tobytes()
        out = dict(video=name, frames=len(v))
        for mode in ("keyframe", "full"):
            hashes, times, wall, host_ok = [], [], [], []
            for _ in range(a.n):
                link.connect()
                r = jobs.echo_ef(link, conf, MODEL["scale"], frames=v, fps=fps, flags=jobs.ECHO_MASKS,
                                 keyframe=kf if mode == "keyframe" else None)
                res = r["result"]
                hashes.append(digest(res, r["blobs"]))
                times.append(split_times(res)[1])
                wall.append(r["wall_s"])
                if mode == "keyframe":
                    host_ok.append(res["key"] == h["key"] and r["blobs"]["masks"][:len(hm)] == hm
                                   and list(res["vols"]) == list(h["vols"]) and res["ef"] == h["ef"])
            tot = np.array([t["ms"]["total"] for t in times])
            o = dict(distinct_hashes=len(set(hashes)), hash=hashes[0][:16],
                     board_total_ms=dict(median=float(np.median(tot)), min=float(tot.min()), max=float(tot.max())),
                     wall_s=dict(median=float(np.median(wall)), min=float(min(wall)), max=float(max(wall))))
            if mode == "keyframe":
                pk = np.array([t["pl_ms_per_key"] for t in times])
                tr = np.array([t["track_ms_per_frame"] for t in times])
                o.update(host_equal=all(host_ok), pl_ms_per_key=[float(pk.min()), float(np.median(pk)), float(pk.max())],
                         track_ms_per_frame=[float(tr.min()), float(np.median(tr)), float(tr.max())])
            out[mode] = o
            print("  %-26s %-8s %d runs, %d distinct hash%s %s  board total %.0f ms [%.0f, %.0f]%s" % (
                name, mode, a.n, o["distinct_hashes"], "" if o["distinct_hashes"] == 1 else "ES", o["hash"],
                o["board_total_ms"]["median"], o["board_total_ms"]["min"], o["board_total_ms"]["max"],
                ("  = host: %s  PL ms/key %s  track ms/fr %s" % (o["host_equal"], np.round(o["pl_ms_per_key"], 2),
                                                                  np.round(o["track_ms_per_frame"], 3)))
                if mode == "keyframe" else ""), flush=True)
        rep["videos"].append(out)
    ok = all(v_[m]["distinct_hashes"] == 1 for v_ in rep["videos"] for m in ("keyframe", "full")) and \
        all(v_["keyframe"]["host_equal"] for v_ in rep["videos"])
    rep["pass_"] = ok
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    print("F3_REPEAT_PASS" if ok else "F3_REPEAT_FAIL")


if __name__ == "__main__":
    main()
