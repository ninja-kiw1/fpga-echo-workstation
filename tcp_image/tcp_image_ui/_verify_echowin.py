# -*- coding: utf-8 -*-
"""_verify_echowin.py —— ③「实时心超工作站」窗口自证。

不连板（默认）：假板子按帧号回放整数模型对这段视频的输出（--q 指向预先算好的 [n, 128, 128] int8）。
连板（--board）：真板子（192.168.1.254:5060，板端 v5.8+）。
检查：
  1. 载入 EchoNet 视频；两种模式（实时 / 全帧）各跑一遍；
  2. 发出去的负载 == 离线 video_inputs（逐字节，按分析帧号）；模型号、128、RGB；
  3. 逐帧容积 == 离线整数特征（results/frames/t3_int_val.npz，若已有）；最终 EF == eval_ef.py 的逐段结果（若已有）；
  4. 证据报告能生成（HTML 存在、内含曲线和关键帧）；截图 board_check_v5/echowin_<mode>.png。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe _verify_echowin.py --video 0X100009310A3BD7FC.avi --q <npy>
    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe _verify_echowin.py --video 0X100009310A3BD7FC.avi --board
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EN = os.path.normpath(os.path.join(HERE, "..", "..", "echonet"))
sys.path.insert(0, HERE)

import numpy as np          # noqa: E402

import echowin              # noqa: E402
import proto                # noqa: E402

FAILS = []


def check(label, ok, extra=""):
    print("%-58s %s %s" % (label, "OK" if ok else "FAIL", extra), flush=True)
    if not ok:
        FAILS.append(label)


class FakeLink(object):
    """回放：负载里 G 通道（本帧）去匹配视频的帧，找到帧号后回该帧的 int8 logit。"""

    def __init__(self, q, frames):
        self.q, self.frames, self.sent, self.k = q, frames, [], 0
        self.host, self.port = "fake", 0

    def send_v6c(self, payload, w, **kw):
        self.sent.append((payload, w, kw))

    def recv_v6c(self, flags, w, nextra=0):
        p = np.frombuffer(self.sent[self.k][0], np.uint8).reshape(128, 128, 3)[8:120, 8:120, 1]
        self.k += 1
        t = int(np.argmin([np.abs(f.astype(int) - p).sum() for f in self.frames]))
        time.sleep(0.004)
        return dict(model=self.sent[self.k - 1][2]["model0"], ai_us=37800), self.q[t].tobytes(), None

    def close(self):
        pass


class FakeApp(object):
    def __init__(self, root, link):
        import tkinter as tk
        self.root, self.link, self.busy = root, link, False
        self.var_host, self.var_port = tk.StringVar(value=link.host), tk.StringVar(value=str(link.port))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="0X100009310A3BD7FC.avi")
    ap.add_argument("--q", default=None)
    ap.add_argument("--board", action="store_true")
    ap.add_argument("--features", default="t3_int")
    ap.add_argument("--job", action="store_true", help="运行方式 = 板上整病例（v7 任务 echo_ef，需 --board、板端 v5.12+）")
    args = ap.parse_args()
    import tkinter as tk
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    import echo_ws
    path = os.path.join(echowin.ECHONET_DIR, "Videos", args.video)
    frames, fps = echo_ws.read_avi(path)
    root = tk.Tk()
    root.withdraw()
    if args.board:
        import imagelink
        link = imagelink.ImageLink("192.168.1.254", 5060, timeout=30)
        link.sent = []
        _send = link.send_v6c

        def send(payload, w, **kw):
            link.sent.append((payload, w, kw))
            _send(payload, w, **kw)
        link.send_v6c = send
    else:
        link = FakeLink(np.load(args.q), frames)
    app = FakeApp(root, link)
    win = echowin.EchoWindow(app)
    win.open_report = False
    if args.job and not args.board:
        raise SystemExit("--job needs --board")
    win.var_where.set(echowin.RUNS[0 if args.job else 1][1])      # 默认测逐帧流式（发出的每个负载都可查）

    def pump(cond, timeout):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            root.update()
            time.sleep(0.01)
        return cond()

    win.load(path)
    check("video loaded", pump(lambda: win.frames is not None, 30), "%d frames %.0f fps" % (len(frames), fps))
    feat = idx = ev = None
    fpath = os.path.join(EN, "results", "frames", args.features + "_val.npz")
    if os.path.exists(fpath):
        feat = np.load(fpath)
        idx = json.load(open(fpath[:-4] + ".json", encoding="utf-8"))["videos"].get(args.video)
    epath = os.path.join(EN, "results", "ef", args.features + "_val.json")
    if os.path.exists(epath):
        ev = next((r for r in json.load(open(epath, encoding="utf-8"))["per_video"] if r["video"] == args.video), None)
    report = dict(video=args.video, board=args.board, modes={})
    for mode, label in echowin.MODES:
        if mode == "keyframe":          # v5.21 关键帧模式只走板上整病例，另有 _verify_echowin_kf.py
            continue
        link.sent.clear() if hasattr(link.sent, "clear") else None
        if not args.board:
            link.k = 0
        win.var_mode.set(label)
        win.run()
        check("[%s] analysis finished" % mode, pump(lambda: not win.busy and win.stats is not None, 120), str(win.stats))
        if args.job:
            check("[%s] board case job ran (no compact frames sent)" % mode,
                  getattr(win, "job", None) is not None and not link.sent, win.var_txt.get()[:120])
        elif args.board and getattr(link, "pp_available", False):      # v5.10+: largest + fill holes on the PL
            check("[%s] largest + fill holes ran on the PL" % mode, getattr(win, "pp_frames", 0) > 0,
                  "%d / %d frames" % (getattr(win, "pp_frames", 0), len(win.vols)))
        step = echo_ws.realtime_step(fps) if mode == "realtime" else 1
        ids, pl = echo_ws.payloads(frames, fps, "t3", step)
        sent = [s for s in link.sent if s[2]["model0"] == 16]
        if not args.job:
            check("[%s] payloads == echo_ws.payloads (%d frames)" % (mode, len(pl)),
                  len(sent) == len(pl) and all(a[0] == b for a, b in zip(sent, pl)))
            check("[%s] compact RGB frames, model 16, 128" % mode,
                  all(w == 128 and kw["model0"] == 16 and kw["kind"] == proto.V6C_KIND_RGB for _, w, kw in sent))
        else:
            check("[%s] analysed frames == echo_ws.payloads frames" % mode, list(win.idx) == list(ids))
        if feat is not None and idx is not None:
            off = feat["vol"][idx["start"]:idx["start"] + idx["n"]]
            ref = np.array([off[t] for t in ids])
            if args.job:     # 板上容积：OpenBLAS 舍入决定的"中点像素"平局，个别帧差一两个像素（§9.55），按文档容差
                rel = np.abs(np.array(win.vols) - ref) / np.maximum(np.abs(ref), 1e-12)
                check("[%s] per-frame volumes ~ offline integer features (rel < 1e-3)" % mode, bool(rel.max() < 1e-3),
                      "max rel %.1e, %d / %d frames differ" % (rel.max(), int((rel > 1e-9).sum()), len(rel)))
            else:
                check("[%s] per-frame volumes == offline integer features" % mode,
                      np.allclose(win.vols, ref, rtol=0, atol=1e-9))
        if ev is not None:
            ef_ui = echo_ws.calibrated(win.result["ef"], win.conf, mode)
            tol = 0.01 if args.job else 1e-9     # 板上整病例：EF 差 < 0.01 个百分点（§9.55）
            check("[%s] EF == eval_ef.py (tol %g)" % (mode, tol), abs(ef_ui - ev[mode]["ef"]) < tol,
                  "%.4f vs %.4f" % (ef_ui, ev[mode]["ef"]))
        win._report()
        rp = getattr(win, "last_report", None)
        ok = bool(rp and os.path.exists(rp) and "data:image/png;base64" in open(rp, encoding="utf-8").read())
        check("[%s] evidence report written" % mode, ok, rp or "")
        for _ in range(20):
            root.update()
            time.sleep(0.05)
        try:
            from PIL import ImageGrab
            x0, y0 = win.top.winfo_rootx(), win.top.winfo_rooty()
            img = ImageGrab.grab((x0, y0, x0 + win.top.winfo_width(), y0 + win.top.winfo_height()))
            out = os.path.join(HERE, "board_check_v5", "echowin_%s%s%s.png" % (mode, "_board" if args.board else "",
                                                                                "_job" if args.job else ""))
            img.save(out)
        except Exception as e:
            print("screenshot skipped:", e)
        report["modes"][mode] = dict(stats=win.stats, ef=echo_ws.calibrated(win.result["ef"], win.conf, mode),
                                     beats=len(win.result["beats"]), hr=win.result.get("hr"), qc=win.qc_level,
                                     status=win.var_txt.get(), report=rp,
                                     pp_frames=getattr(win, "pp_frames", 0), frames=len(win.vols))
    if args.board:
        report["checks_failed"] = FAILS
        json.dump(report, open(os.path.join(HERE, "board_check_v5", "gui_acceptance_echonet%s.json"
                                            % ("_job" if args.job else "")), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    win.close()
    root.destroy()
    print("ECHOWIN_SELFTEST_PASS" if not FAILS else "ECHOWIN_SELFTEST_FAIL %s" % FAILS)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
