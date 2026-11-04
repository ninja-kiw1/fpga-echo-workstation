# -*- coding: utf-8 -*-
"""_verify_echowin_kf.py —— 心超窗口「关键帧」模式（板端 v5.21 echo_ef mode 2，创新 ①②③）的离线自证。

不连板：jobs.caps / jobs.echo_ef 换成假的，假 echo_ef 用主机参考（E:/PLD/keyframe/echo_core.dll = 板上同一份 C，
verify_board_echo_kf.py 已证板上逐位相同）按板端 JSON 的字段拼出结果（含 "masks" 块），走真实的 Tk 窗口：
  1. 默认模式是「关键帧」，运行方式自动是板上整病例；发出的参数 = jobs.echo_kf_params(..., load_kf_config("keyframe"))；
  2. 主界面 EF = 板上校准值；80 % 区间一行（跨 40 / 50 时红字 + "建议复核"）；质量提示只出现在临床测量栏，不提 stability；
  3. 时间线画出关键帧（蓝）/ 跟踪帧（灰），帧数 = 视频帧数；曲线、逐拍标注用这套调度的 a / b；
  4. 证据报告含"不确定度与质量提示"一节（80 % / 90 % 区间、关键帧统计）；截图 board_check_v5/echowin_keyframe.png。
另外：老板端（caps 没有 echo_modes）时给出"需要 v5.21+"的提示、不发任务。
--board：真板子（192.168.1.254:5060，板端 v5.21+）跑真实任务，另查板上关键帧序列 / 每帧掩膜 / 容积 / EF / 灯色 / 区间
与主机参考逐位相同；截图 echowin_keyframe_board.png。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe _verify_echowin_kf.py [--video 0X100009310A3BD7FC.avi] [--board]
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
import jobs  # noqa: E402
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


class FakeApp(object):
    def __init__(self, root, link):
        import tkinter as tk
        self.root, self.link, self.busy = root, link, False
        self.var_host, self.var_port = tk.StringVar(value=link.host), tk.StringVar(value=str(link.port))


def fake_result(v, fps, q, kf, conf, scale):
    """主机参考 -> 板端 mode 2 的 RESULT / blobs（只拼窗口用到的字段）"""
    import echo_ws
    h = host_reference(v, fps, q, kf, conf)
    z, ef = h["z"], h["ef"]
    a, b = kf["a"], kf["b"]
    cal = lambda e: a * e + b  # noqa: E731
    clinical = echo_ws.clinical(z["beats"] and [dict(ed=int(x[0]), es=int(x[1]), v_ed=x[2], v_es=x[3], ef=x[4]) for x in z["beats"]],
                                fps, ef, cal, echo_ws.clinical_conf(conf, "full"))
    m = kf["model"]
    contrib = {k: m["w"][i + 1] * ((h["feats"][k] - m["mu"][i]) / m["sd"][i]) for i, k in enumerate(echo_core.QF)}
    reasons = [k for k, c in sorted(contrib.items(), key=lambda kv: -kv[1]) if c > 0][:2]
    n = len(v)
    key = h["key"]
    nk = key.count("1")
    res = dict(ok=True, mode=2, n_frames=n, fps=fps, step=1, analysed=n, ef_raw=z["ef"], ef=ef, fallback=z["fallback"],
               hr=z["hr"], cycle_s=None, beats=[dict(ed=int(x[0]), es=int(x[1]), v_ed=x[2], v_es=x[3], ef=x[4],
                                                    ef_cal=cal(x[4])) for x in z["beats"]],
               qc=dict(level=0, score=0.0, why=0, multi=0.0, unc=0.0, border=0.0, empty=0.0, spread=0.0),
               clinical=clinical, keyframe=dict(n_key=nk, key_frac=nk / n, reasons=dict(interval=0, residual=0),
                                                pl_ms_per_key=22.9, track_ms_per_frame=0.81, track_ms_max=1.0),
               key=key, quality=dict(light=h["light"], score=h["score"], reasons=reasons, features=h["feats"]),
               interval=h["interval"], vols=h["vols"], pl_ms=22.9 * nk, ms=dict(analysis=1.0, total=900.0))
    if z["ef_sd"] is not None:
        res["ef_sd"] = z["ef_sd"]
    bits = np.packbits(np.asarray(h["masks"], bool).ravel(), bitorder="little").tobytes()
    return dict(result=res, blobs=dict(masks=bits), progress=[], status=0, upload_s=0.1, wall_s=0.9), h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="0X100009310A3BD7FC.avi")
    ap.add_argument("--board", action="store_true")
    args = ap.parse_args()
    import tkinter as tk
    import echo_ws
    E = os.path.join(PLD, "echonet")
    idx = json.load(open(os.path.join(E, "results", "frames", "t3_int_val.json"), encoding="utf-8"))["videos"]
    qall = np.load(os.path.join(PLD, "quality", "results", "q", "t3_val_q112.npy"), mmap_mode="r")
    path = os.path.join(echowin.ECHONET_DIR, "Videos", args.video)
    v, fps = echo_ws.read_avi(path)
    e = idx[args.video]
    q = np.ascontiguousarray(qall[e["start"]:e["start"] + e["n"]])
    sent = {}
    state = dict(caps=None if args.board else {"server": "v5.21", "echo_modes": [0, 1, 2]})
    real_caps, real_echo_ef = jobs.caps, jobs.echo_ef

    def fake_caps(link):
        return real_caps(link) if state["caps"] is None else state["caps"]

    def fake_echo_ef(link, conf, scale, frames, fps_, model=16, realtime=False, flags=0, on_progress=None, on_frame=None,
                     keyframe=None, heart=None):
        sent["keyframe"] = keyframe
        sent["params"] = jobs.echo_kf_params(jobs.echo_params(conf, scale, model, realtime, len(frames), fps_), keyframe) \
            if keyframe else None
        if args.board:            # 真板子：原样发任务，再算主机参考对照
            r = real_echo_ef(link, conf, scale, frames, fps_, model=model, realtime=realtime, flags=flags,
                             on_progress=on_progress, on_frame=on_frame, keyframe=keyframe)
            sent["h"], sent["r"] = host_reference(np.asarray(frames), fps_, q, keyframe, conf), r
            return r
        if on_progress:
            on_progress(dict(stage="frames", done=len(frames), total=len(frames)))
        r, h = fake_result(np.asarray(frames), fps_, q, keyframe, conf, scale)
        sent["h"] = h
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
        link = imagelink.ImageLink("192.168.1.254", 5060, timeout=30)
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

    check("default mode is keyframe", win._mode() == "keyframe", win.var_mode.get())
    win.var_where.set(echowin.RUNS[1][1])                 # 用户选了逐帧流式：关键帧模式应自动改回整病例
    win.load(path)
    check("video loaded", pump(lambda: win.frames is not None, 30), "%d frames" % len(v))
    win.run()
    check("job finished", pump(lambda: not win.busy and win.stats is not None, 120), win.var_txt.get()[:90])
    check("run mode switched to board case job", win._where() == "job", win.var_where.get())
    kf = jobs.load_kf_config("keyframe")
    check("params = echo_kf_params(load_kf_config('keyframe'))", sent.get("keyframe") == kf and sent["params"][2] == 2
          and len(sent["params"]) == 688)
    h = sent["h"]
    if args.board:
        res = sent["r"]["result"]
        bm = np.unpackbits(np.frombuffer(sent["r"]["blobs"]["masks"], np.uint8), bitorder="little")
        hm = np.asarray(h["masks"], bool).ravel()
        check("board == host reference: key / masks / vols / EF", res.get("key") == h["key"]
              and np.array_equal(bm[:hm.size].astype(bool), hm) and res.get("vols") == list(h["vols"])
              and res.get("ef") == h["ef"], "key %.3f, PL %.1f ms/key, track %.2f ms/fr, job %.2f s" % (
                  res["keyframe"]["key_frac"], res["keyframe"]["pl_ms_per_key"],
                  res["keyframe"]["track_ms_per_frame"], sent["r"].get("wall_s", 0)))
        check("board == host reference: light / interval", res["quality"]["light"] == h["light"]
              and bool(res["interval"]["review"]) == bool(h["interval"]["review"])
              and abs(res["interval"]["lo80"] - h["interval"]["lo80"]) < 1e-9
              and abs(res["interval"]["hi80"] - h["interval"]["hi80"]) < 1e-9,
              "light %d, 80%% [%.1f, %.1f]" % (res["quality"]["light"], res["interval"]["lo80"], res["interval"]["hi80"]))
    check("EF shown = board calibrated EF", win.var_ef.get() == "EF %.0f%%" % h["ef"], win.var_ef.get())
    ci = win.var_ci.get()
    lo, hi = max(0, h["interval"]["lo80"]), min(100, h["interval"]["hi80"])
    check("80 % interval line", ("%.0f–%.0f%%" % (lo, hi)) in ci and (("建议复核" in ci) == h["interval"]["review"]), ci)
    check("quality hint only in the clinical box, no 'stability'", "图像质量提示" in win.var_clin.get()
          and "图像质量" not in ci and "稳定" not in win.var_clin.get(), win.var_clin.get().splitlines()[-1][:80])
    check("keyframe marks = video length", win.keyv is not None and len(win.keyv) == len(v)
          and sum(win.keyv) == h["key"].count("1"), "%d keyframes" % sum(win.keyv or []))
    check("masks for every frame", len(win.masks) == len(v))
    check("beat EFs use the schedule's a / b", all(abs(win._cal(b["ef"]) - (kf["a"] * b["ef"] + kf["b"])) < 1e-12
                                                  for b in win.result["beats"]))
    win._report()
    rp = getattr(win, "last_report", None)
    html = open(rp, encoding="utf-8").read() if rp and os.path.exists(rp) else ""
    check("report has the uncertainty / quality section", "不确定度与质量提示" in html and "90% 区间" in html
          and "关键帧：PL 推理" in html, rp or "")
    try:
        from PIL import ImageGrab
        win.top.deiconify()
        root.update()
        time.sleep(0.5)
        root.update()
        x, y = win.top.winfo_rootx(), win.top.winfo_rooty()
        ImageGrab.grab((x, y, x + win.top.winfo_width(), y + win.top.winfo_height())).save(
            os.path.join(HERE, "board_check_v5", "echowin_keyframe%s.png" % ("_board" if args.board else "")))
    except Exception as ex:
        print("screenshot skipped:", ex)
    # old board: no echo_modes -> message, no job
    state["caps"] = {"server": "v5.20"}
    sent.clear()
    win.run()
    pump(lambda: not win.busy, 30)
    check("old board: asks for v5.21+, no job sent", "v5.21" in win.var_txt.get() and "params" not in sent,
          win.var_txt.get()[:80])
    win.top.destroy()
    root.destroy()
    out = dict(video=args.video, board=args.board, fails=FAILS, report=rp)
    with open(os.path.join(HERE, "board_check_v5", "echowin_keyframe%s.json" % ("_board" if args.board else "")), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print("ECHOWIN_KF_PASS" if not FAILS else "ECHOWIN_KF_FAIL %s" % FAILS)


if __name__ == "__main__":
    main()
