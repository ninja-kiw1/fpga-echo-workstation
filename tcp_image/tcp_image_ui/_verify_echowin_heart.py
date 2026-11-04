# -*- coding: utf-8 -*-
"""_verify_echowin_heart.py —— 心超窗口的全心扩展（服务端 v5.24：echo_ef mode 2 + "HC01" 节段室壁运动 + 右室 / 右房）自证。

默认不连板：jobs.caps / jobs.echo_ef 换成假的，假板子 = 主机参考（verify_board_echo_kf.host_reference + E:/PLD/keyframe/heart_ref.py，
同一份 C），走真实的 Tk 窗口：
  1. 打开一段 HMC-QU 开发集录屏（640x422 等，窗口按 echo_ws.screen_to_112 取扇区）：帧 == 评估用缓存，参考行显示 HMC-QU 标注；
  2. 关键帧模式 + 板端报 "heart"：发出的参数 = echo_kf_params + heart_block（864 字节，尾部 "HC01"）；
  3. 结果：临床框有节段室壁运动 / 右室 / 右房三类文字，ED / ES 帧上画了右心轮廓，右上角有 6 节段示意，报告有"全心"一节；
  4. 板端不报 "heart"（v5.23）：不发 HC01，没有全心文字。
--board：真板子（v5.24+），另比板上全心结果 == 主机参考（逐位），截图 echowin_heart_board.png。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe _verify_echowin_heart.py [--board] [--clip "ES000108 _4CH_1"]
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
import echowin  # noqa: E402
import heart_ref  # noqa: E402
import jobs  # noqa: E402
from _verify_echowin_kf import FakeApp, FakeLink, fake_result  # noqa: E402
from verify_board_echo_kf import host_reference  # noqa: E402

FAILS = []
WM = os.path.join(PLD, "wallmotion")
VID = os.path.join(PLD, "HMC-QU Dataset-Kaggle v1.0 (1)", "HMC-QU Dataset-Kaggle v1.0", "HMC-QU", "A4C")


def check(label, ok, extra=""):
    print("%-62s %s %s" % (label, "OK" if ok else "FAIL", extra), flush=True)
    if not ok:
        FAILS.append(label)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", action="store_true")
    ap.add_argument("--clip", default="ES000108 _4CH_1")
    args = ap.parse_args()
    import tkinter as tk
    C = os.path.join(WM, "results", "cache")
    idx = json.load(open(os.path.join(C, "index.json")))
    split = json.load(open(os.path.join(WM, "results", "split.json")))["split"]
    assert split[args.clip] == "dev", "self-test only on DEV clips"
    e = idx[args.clip]
    s = slice(e["start"], e["start"] + e["n"])
    F = np.load(os.path.join(C, "frames.npy"), mmap_mode="r")
    q = np.ascontiguousarray(np.load(os.path.join(C, "q.npy"), mmap_mode="r")[s])
    rv_q = np.ascontiguousarray(np.load(os.path.join(C, "rv_q.npy"), mmap_mode="r")[s])
    ra_q = np.ascontiguousarray(np.load(os.path.join(C, "ra_q.npy"), mmap_mode="r")[s])
    sent = {}
    state = dict(caps=None if args.board else {"server": "v5.24", "echo_modes": [0, 1, 2, 3], "heart": ["wall_motion", "right_heart"]})
    real_caps, real_echo_ef = jobs.caps, jobs.echo_ef

    def fake_caps(link):
        return real_caps(link) if state["caps"] is None else state["caps"]

    def fake_echo_ef(link, conf, scale, frames, fps_, model=16, realtime=False, flags=0, on_progress=None, on_frame=None,
                     keyframe=None, heart=None):
        p = jobs.echo_kf_params(jobs.echo_params(conf, scale, model, realtime, len(frames), fps_), keyframe)
        sent.update(params=p + (jobs.heart_block(heart) if heart else b""), heart=heart)
        h = host_reference(np.asarray(frames), fps_, q, keyframe, conf)
        sent["h"] = h
        beats = [(int(x[0]), int(x[1])) for x in h["z"]["beats"]]
        if heart:
            hh, rh = heart_ref.heart_reference(echo_core.hc_par(jobs.heart_block(heart)), 3, np.asarray(h["masks"], bool),
                                               beats, h["vols"], h["ef"], rv_q, ra_q)
            sent["hh"], sent["rh"] = hh, rh
        if args.board and state["caps"] is None:
            r = real_echo_ef(link, conf, scale, frames, fps_, model=model, realtime=realtime, flags=flags,
                             on_progress=on_progress, on_frame=on_frame, keyframe=keyframe, heart=heart)
            sent["r"] = r
            return r
        r, _ = fake_result(np.asarray(frames), fps_, q, keyframe, conf, scale)
        if heart:
            r["result"].update(sent["hh"])
            if sent["rh"]:
                r["blobs"]["rh_masks"] = np.packbits(np.stack([m for t in sent["rh"] for m in t]).ravel(),
                                                     bitorder="little").tobytes()
        return r

    jobs.caps, jobs.echo_ef = fake_caps, fake_echo_ef
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    root.withdraw()
    if args.board:
        import imagelink
        link = imagelink.ImageLink("192.168.1.254", 5060, timeout=60)
    else:
        link = FakeLink()
    app = FakeApp(root, link)
    win = echowin.EchoWindow(app)
    win.open_report = False

    def pump(cond, timeout):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            root.update()
            time.sleep(0.01)
        return cond()

    win.load(os.path.join(VID, args.clip + ".avi"))
    check("HMC-QU clip loaded (screen -> 112)", pump(lambda: win.frames is not None and win.name == args.clip + ".avi", 60))
    check("window frames == evaluation cache", np.array_equal(win.frames, F[s]) and win.fps == e["fps"])
    check("reference line shows the HMC-QU labels", "HMC-QU 标注" in win.var_ref.get(), win.var_ref.get())
    win.var_where.set(echowin.RUNS[1][1])
    win.var_mode.set(echowin.MODES[0][1])
    win.run()
    check("keyframe job finished", pump(lambda: not win.busy and win.stats is not None, 120), win.var_txt.get()[:80])
    p = sent.get("params", b"")
    check("params = KF01 + HC01 (864 bytes)", len(p) == 864 and p[688:692] == b"HC01" and p[2] == 2, len(p))
    clin = win.var_heart.get()
    check("heart card: wall motion / RV / RA lines", "节段室壁运动" in clin and "右室" in clin and "右房" in clin,
          clin.splitlines()[-1][:80] if clin else "")
    check("RV / RA contours on ED / ES frames", bool(win.rh_frames), sorted(win.rh_frames)[:6])
    if args.board:
        res = sent["r"]["result"]
        rh_b = jobs.unpack_rh(sent["r"]["blobs"]["rh_masks"], len(res["right_heart"]["pairs"])) \
            if "rh_masks" in sent["r"]["blobs"] else []
        ck = heart_ref.compare(res, sent["hh"], rh_b, sent["rh"])
        check("board heart == host reference (bit for bit)", all(ck.values()), "server %s %s" % (
            real_caps(link).get("server"), ck))
    else:
        check("window heart == host reference", win.heart["wall_motion"] == sent["hh"]["wall_motion"]
              and win.heart["right_heart"] == sent["hh"]["right_heart"])
    win._show_frame(win.result["beats"][0]["es"] if win.result["beats"] else 0)
    win._report()
    rp = getattr(win, "last_report", None)
    html = open(rp, encoding="utf-8").read() if rp and os.path.exists(rp) else ""
    check("report: whole-heart section with figures", "全心：节段室壁运动与右心" in html and html.count("<figure>") >= 3, rp or "")
    try:
        from PIL import ImageGrab
        win.top.deiconify()
        for _ in range(20):
            root.update()
            time.sleep(0.03)
        x, y = win.top.winfo_rootx(), win.top.winfo_rooty()
        ImageGrab.grab((x, y, x + win.top.winfo_width(), y + win.top.winfo_height())).save(
            os.path.join(HERE, "board_check_v5", "echowin_heart%s.png" % ("_board" if args.board else "")))
    except Exception as ex:
        print("screenshot skipped:", ex)
    state["caps"] = {"server": "v5.23", "echo_modes": [0, 1, 2, 3]}
    sent.clear()
    win.run()
    pump(lambda: not win.busy, 120)
    check("old board (no heart caps): no HC01 sent, no heart text", len(sent.get("params", b"")) == 688
          and "节段室壁运动" not in win.var_heart.get() and not win.rh_frames, len(sent.get("params", b"")))
    win.top.destroy()
    root.destroy()
    with open(os.path.join(HERE, "board_check_v5", "echowin_heart%s.json" % ("_board" if args.board else "")), "w",
              encoding="utf-8") as f:
        json.dump(dict(clip=args.clip, board=args.board, fails=FAILS, report=rp), f, indent=1, ensure_ascii=False)
    print("ECHOWIN_HEART_PASS" if not FAILS else "ECHOWIN_HEART_FAIL %s" % FAILS)


if __name__ == "__main__":
    main()
