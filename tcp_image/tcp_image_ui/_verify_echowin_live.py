# -*- coding: utf-8 -*-
"""_verify_echowin_live.py —— 心超窗口「板上信号源（现场）」运行方式（服务端 v5.22+ echo_ef mode 3）的自证。

默认不连板：jobs.caps / jobs.echo_live 换成假的。假 echo_live 用主机参考（E:/PLD/keyframe/echo_core.dll = 板上同一份 C）
按"固定方块"的结果拼出板端 RESULT（mode 3，含 "stream"）和逐帧 "live" 块（带本帧图像，即 v5.23 的格式），走真实的 Tk 窗口：
  1. 运行方式选「板上信号源」、信号源选第一个 ×4 测试源、画面方块选「固定」；发出的参数 = jobs.echo_live_params（mode 3，800 字节）；
  2. 运行中逐帧收到画面 / 轮廓 / 容积（live 块全部进窗口）；
  3. 结束后：画面 = 板上回来的 112 帧（= 原视频）、EF = 板上校准值、80 % 区间一行、质量提示、关键帧标记、
     状态栏有画面方块和延迟；报告里运行方式写"现场（板上信号源 …）"并含"不确定度与质量提示"；截图 board_check_v5/echowin_live.png；
  4. 老板端（caps 没有 mode 3）：提示要 v5.22+、不发任务；「停止」：断开、提示没有最终结果。
--board：真板子（192.168.1.254:5060，v5.22+；v5.22 运行中只画轮廓，v5.23+ 连图像），检查同上（结果与主机参考逐位相同），
截图 echowin_live_board.png。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe _verify_echowin_live.py [--board]
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

import echowin  # noqa: E402
import jobs  # noqa: E402
from _verify_echowin_kf import FakeApp, fake_result  # noqa: E402
from verify_board_echo_kf import host_reference  # noqa: E402

FAILS = []


def check(label, ok, extra=""):
    print("%-62s %s %s" % (label, "OK" if ok else "FAIL", extra), flush=True)
    if not ok:
        FAILS.append(label)


class FakeLink(object):
    host, port = "fake", 0

    def connect(self):
        pass

    def close(self):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", action="store_true")
    args = ap.parse_args()
    import tkinter as tk
    import echo_ws
    E = os.path.join(PLD, "echonet")
    idx = json.load(open(os.path.join(E, "results", "frames", "t3_int_val.json"), encoding="utf-8"))["videos"]
    qall = np.load(os.path.join(PLD, "quality", "results", "q", "t3_val_q112.npy"), mmap_mode="r")
    srcs = echowin.live_sources()
    label, src = next(s for s in srcs if s[1]["kind"] == "file" and s[1]["name"].endswith("_z4.y8"))
    v, fps = echo_ws.read_avi(os.path.join(echowin.ECHONET_DIR, "Videos", src["video"]))
    e = idx[src["video"]]
    q = np.ascontiguousarray(qall[e["start"]:e["start"] + e["n"]])
    sent, state = {}, dict(caps=None if args.board else {"server": "v5.23", "echo_modes": [0, 1, 2, 3]}, slow=False)
    real_caps, real_live = jobs.caps, jobs.echo_live

    def fake_caps(link):
        return real_caps(link) if state["caps"] is None else state["caps"]

    def fake_live(link, conf, scale, kf, name, source="file", crop=None, model=16, on_live=None, on_progress=None, heart=None,
                  **kw):
        sent.update(kf=kf, name=name, crop=crop, params=jobs.echo_live_params(
            jobs.echo_params(conf, scale, model, False, 0, 50.0), kf, name, source, crop, **kw), kw=kw)
        if args.board and not state["slow"]:
            sent["lives"] = 0

            def count(d):
                sent["lives"] += 1
                sent["frame_in_blob"] = d["frame"] is not None
                on_live(d)
            r = real_live(link, conf, scale, kf, name, source=source, crop=crop, model=model, on_live=count,
                          on_progress=on_progress, heart=heart, **kw)
            sent["h"], sent["r"] = host_reference(v, fps, q, kf, conf), r
            return r
        r, h = fake_result(v, fps, q, kf, conf, scale)
        sent["h"] = h
        res = r["result"]
        res.update(mode=3, stream=dict(source="file", name=name, W=640, H=480, src_fps=fps,
                                       crop=dict(auto=crop is None, x=crop[0], y=crop[1], side=crop[2],
                                                 moving_box=list(crop) + [crop[2]], cells=0),
                                       detect_frames=0, captured=len(v), analysed=len(v), capture_error=None,
                                       resize_ms_mean=2.5, resize_ms_max=3.0,
                                       latency_ms=dict(p50=29.0, p99=67.0, max=68.0)))
        bits = np.asarray(h["masks"], bool)
        sent["lives"] = 0
        for t in range(len(v)):
            if state["slow"]:
                time.sleep(0.02)
            d = dict(t=t, key=h["key"][t] == "1", vol=h["vols"][t], stamps={},
                     mask=np.packbits(bits[t].ravel(), bitorder="little").tobytes(), frame=v[t].tobytes())
            on_live(d)
            sent["lives"] += 1
            if t % 25 == 24 and on_progress:
                on_progress(dict(stage="live", done=t + 1, total=len(v), captured=t + 1, ef=h["ef"], ef_raw=0.0,
                                 beats=2, hr=60.0, key_frac=0.18, lag_ms=20.0))
        r["blobs"]["frames112"] = np.ascontiguousarray(v).tobytes()
        return r

    jobs.caps, jobs.echo_live = fake_caps, fake_live
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    root.withdraw()
    if args.board:
        import imagelink
        link = imagelink.ImageLink("192.168.1.254", 5060, timeout=30)
    else:
        link = FakeLink()
    app = FakeApp(root, link)
    win = echowin.EchoWindow(app)
    win.open_report = False
    seen = dict(max_masks=0)

    def pump(cond, timeout):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            root.update()
            seen["max_masks"] = max(seen["max_masks"], len(win.masks))
            time.sleep(0.01)
        return cond()

    win.var_where.set(echowin.RUNS[2][1])
    win.var_src.set(label)
    win.var_crop.set(echowin.CROPS[1])
    win.run()
    check("live job finished", pump(lambda: not win.busy and win.stats is not None, 120), win.var_txt.get()[:100])
    p = sent.get("params", b"")
    check("params = echo_live_params (mode 3, 800 bytes, fixed square)", len(p) == 800 and p[2] == 3
          and sent["crop"] == src["truth"] and sent["kw"].get("out_flags", 0) & jobs.LIVE_FRAME, sent.get("crop"))
    check("every live blob reached the window", sent.get("lives") == len(v) and seen["max_masks"] == len(v),
          "%s blobs, %d masks" % (sent.get("lives"), seen["max_masks"]))
    h = sent["h"]
    check("frames shown = board 112 frames = original video", np.array_equal(np.asarray(win.frames), v))
    if args.board:
        res = sent["r"]["result"]
        hm = np.packbits(np.asarray(h["masks"], bool).ravel(), bitorder="little").tobytes()
        check("board == host reference: key / masks / vols / EF", res["key"] == h["key"]
              and sent["r"]["blobs"]["masks"][:len(hm)] == hm and list(res["vols"]) == list(h["vols"])
              and res["ef"] == h["ef"], "server %s, frame in live blob: %s" % (
                  real_caps(link).get("server"), sent.get("frame_in_blob")))
    check("EF shown = board calibrated EF", win.var_ef.get() == "EF %.0f%%" % h["ef"], win.var_ef.get())
    lo, hi = max(0, h["interval"]["lo80"]), min(100, h["interval"]["hi80"])
    ci = win.var_ci.get()
    check("80 % interval line", ("%.0f–%.0f%%" % (lo, hi)) in ci and (("建议复核" in ci) == h["interval"]["review"]), ci)
    check("quality hint in the clinical box", "图像质量提示" in win.var_clin.get())
    check("keyframe marks = clip length", win.keyv is not None and len(win.keyv) == len(v)
          and sum(win.keyv) == h["key"].count("1"))
    check("status line has the square and the latency", "画面方块固定" in win.var_txt.get() and "中位数" in win.var_txt.get(),
          win.var_txt.get().splitlines()[-1][:100])
    win._report()
    rp = getattr(win, "last_report", None)
    html = open(rp, encoding="utf-8").read() if rp and os.path.exists(rp) else ""
    check("report: live run mode + uncertainty section", "现场（板上信号源" in html and "不确定度与质量提示" in html, rp or "")
    try:
        from PIL import ImageGrab
        win.top.deiconify()
        for _ in range(20):
            root.update()
            time.sleep(0.03)
        x, y = win.top.winfo_rootx(), win.top.winfo_rooty()
        ImageGrab.grab((x, y, x + win.top.winfo_width(), y + win.top.winfo_height())).save(
            os.path.join(HERE, "board_check_v5", "echowin_live%s.png" % ("_board" if args.board else "")))
    except Exception as ex:
        print("screenshot skipped:", ex)
    # stop: disconnect, no final result (offline only: the fake streams slowly)
    if not args.board:
        state["slow"] = True
        win.run()
        pump(lambda: len(win.vols) > 20, 30)
        win._stop()
        check("stop: disconnect, message, not busy", pump(lambda: not win.busy, 30) and "已停止" in win.var_txt.get(),
              win.var_txt.get()[:60])
        state["slow"] = False
    state["caps"] = {"server": "v5.21", "echo_modes": [0, 1, 2]}
    sent.clear()
    win.run()
    pump(lambda: not win.busy, 30)
    check("old board: asks for v5.22+, no job sent", "v5.22" in win.var_txt.get() and "params" not in sent,
          win.var_txt.get()[:80])
    win.top.destroy()
    root.destroy()
    with open(os.path.join(HERE, "board_check_v5", "echowin_live%s.json" % ("_board" if args.board else "")), "w",
              encoding="utf-8") as f:
        json.dump(dict(source=src["name"], board=args.board, fails=FAILS, report=rp), f, indent=1, ensure_ascii=False)
    print("ECHOWIN_LIVE_PASS" if not FAILS else "ECHOWIN_LIVE_FAIL %s" % FAILS)


if __name__ == "__main__":
    main()
