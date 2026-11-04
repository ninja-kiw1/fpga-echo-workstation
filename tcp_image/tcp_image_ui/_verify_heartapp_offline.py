# -*- coding: utf-8 -*-
"""_verify_heartapp_offline.py —— 心超工作站现场模式的演示改动不连板自证（板子不在时用；连板用 _verify_heartapp.py）。

jobs.caps / jobs.echo_live 换成假的：结果 = 主机参考（E:/PLD/keyframe/echo_core.dll，板上同一份 C，见 _verify_echowin_live.py），
逐帧 "live" 块带合成的板上时间戳（推荐档：关键帧 60 ms、其余 8 ms，不累积；每帧推理：处理 24.3 ms > 帧间隔 20 ms，延迟累积）。
查：默认 50 fps 源 + 固定方块；逐帧延迟进图、预算 / 帧率指标；第二次跑时上一次留作灰线；--demo 预置三个模式；连不上时的路由提示。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe _verify_heartapp_offline.py
"""
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

import echo_ws  # noqa: E402
import heartapp  # noqa: E402
import heartview  # noqa: E402
import jobs  # noqa: E402
from _verify_echowin_kf import fake_result  # noqa: E402

FAILS = []


def check(label, ok, extra=""):
    print("%-64s %s %s" % (label, "OK" if ok else "FAIL", extra), flush=True)
    if not ok:
        FAILS.append(label)


class FakeLink(object):
    host, port, timeout = "fake", 0, 15

    def connect(self):
        pass

    def close(self):
        pass


def main():
    import tkinter as tk
    E = os.path.join(PLD, "echonet")
    idx = json.load(open(os.path.join(E, "results", "frames", "t3_int_val.json"), encoding="utf-8"))["videos"]
    qall = np.load(os.path.join(PLD, "quality", "results", "q", "t3_val_q112.npy"), mmap_mode="r")
    src = dict(heartview.live_sources())
    src = next(s for s in src.values() if s.get("name") == heartview.DEMO_SRC)
    v, fps = echo_ws.read_avi(os.path.join(heartview.ECHONET_DIR, "Videos", src["video"]))
    e = idx[src["video"]]
    q = np.ascontiguousarray(qall[e["start"]:e["start"] + e["n"]])
    sent = {}

    def fake_live(link, conf, scale, kf, name, source="file", crop=None, model=16, on_live=None, on_progress=None,
                  heart=None, **kw):
        sent.update(name=name, crop=crop, kf=kf)
        r, h = fake_result(v, fps, q, kf, conf, scale)
        res = r["result"]
        full = kf["policy"]["kind"] == "full"
        gap = 1e6 / fps
        lat, t_free = [], 0.0
        for t in range(len(v)):                                # 合成时间戳（µs）：单线程处理，帧按 gap 到达
            cap = t * gap
            key = h["key"][t] == "1"
            work = 24300.0 if full else (23000.0 if key else 770.0)
            start = max(cap + (40000.0 if key else 0.0), t_free)   # 关键帧先等后 40 ms 的帧（t3 编码），板上 start 在等之后
            end = start + work
            t_free = end
            lat.append((end - cap) / 1000.0)
            d = dict(t=t, key=key, vol=h["vols"][t], mask=np.packbits(np.asarray(h["masks"][t], bool).ravel(),
                                                                         bitorder="little").tobytes(),
                     frame=v[t].tobytes(), stamps=dict(captured=int(cap), ready=int(cap), start=int(start),
                                                       work0=int(start), work1=int(start + work), end=int(end),
                                                       sent_prev=0))
            on_live(d)
        res.update(mode=3, stream=dict(source="file", name=name, W=640, H=480, src_fps=fps,
                                       crop=dict(auto=crop is None, x=crop[0], y=crop[1], side=crop[2]),
                                       detect_frames=0, captured=len(v), analysed=len(v), latency_ms=dict(
                                           p50=float(np.median(lat)), p99=float(np.percentile(lat, 99)), max=max(lat))))
        r["blobs"]["frames112"] = np.ascontiguousarray(v).tobytes()
        return r

    jobs.caps = lambda link: {"server": "v5.24(fake)", "echo_modes": [0, 1, 2, 3]}
    jobs.echo_live = fake_live
    heartapp._enable_dpi_awareness()
    root = tk.Tk()
    app = heartapp.HeartApp(root, "127.0.0.1", 5060, "keyframe")
    app.link = FakeLink()

    def pump(cond, timeout):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            root.update()
            time.sleep(0.01)
        return cond()

    heartapp.prepare_demo(app)
    check("--demo: snapshot / keyframe prepared, ends in live mode", pump(
        lambda: app.views["keyframe"].frames is not None, 60) and app.mode == "live"
        and app.views["snapshot"].frames is not None, app.var_status.get())
    lv = app.views["live"]
    lv.open_report = False
    check("live defaults: 50 fps demo source + fixed crop", heartview.DEMO_SRC in lv.var_src.get()
          and lv.var_crop.get() == heartview.CROP_FIXED)
    lv.run()
    check("recommended run finished", pump(lambda: not lv.busy and lv.res is not None, 120), lv.var_txt.get()[:80])
    check("fixed crop sent", sent.get("crop") == (src["truth"]), sent.get("crop"))
    check("latency trace == frames, budget chip ~ 5 ms / 20 ms", len(lv.lat) == len(v) and "/ 20 ms" in lv.var_k_proc.get(),
          "%s | %s | %s" % (lv.var_k_proc.get(), lv.var_k_lat.get(), lv.var_k_fps.get()))
    check("frame-rate chip: no dropped frames", "不丢" in lv.var_k_fps.get(), lv.var_k_fps.get())
    lv.var_sched.set(heartview.SCHEDS[1][1])
    lv.run()
    pump(lambda: not lv.busy and lv.res is not None, 120)
    check("per-frame run: budget > 100 %, previous run kept as grey line", lv.prev_run is not None
          and lv.prev_run["label"].startswith("推荐档") and float(lv.var_k_proc.get().split("（")[1].rstrip("%）")) > 100,
          "%s | prev %s" % (lv.var_k_proc.get(), lv.prev_run and lv.prev_run["label"]))
    for _ in range(20):
        root.update()
        time.sleep(0.03)
    try:
        from PIL import ImageGrab
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(
            os.path.join(HERE, "board_check_v5", "heartapp_live_compare_offline.png"))
    except Exception as ex:
        print("screenshot skipped:", ex)
    check("window fits a 1080p work area (height <= 1040 logical px at this DPI)",
          root.winfo_height() / (root.winfo_fpixels("1i") / 96.0) <= 1040,
          "%dx%d px at %.0f dpi" % (root.winfo_width(), root.winfo_height(), root.winfo_fpixels("1i")))
    hint = heartapp.route_hint("192.168.1.254")
    print("route hint now:", hint or "(none)")
    root.destroy()
    print("HEARTAPP_OFFLINE_PASS" if not FAILS else "HEARTAPP_OFFLINE_FAIL %s" % FAILS)


if __name__ == "__main__":
    main()
