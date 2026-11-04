# -*- coding: utf-8 -*-
"""_verify_ctwin.py —— ⑤「CT 整卷分割」窗口的上位机侧自证（不连板）。

“假板子”按收到的顺序回放整数模型对验证病例的输出（E:/PLD/ct/results/quant/<tag>/outputs/<case>.npy，
已由 export_verify_ct.py 证明与 HLS 位级模拟器逐位相同）。检查：
  1. 窗口载入 imagesTr 的病例后，发出去的每个负载等于离线 common.box(stack_input(...)) 的字节（直接比对 E:/PLD/ct 的缓存）；
  2. 整卷跑完后窗口里的最终掩膜 == ct_volume.postprocess(参考输出, 选定后处理)，显示的 Dice 等于离线评测；
  3. 截一张窗口图（board_check_v5/ctwin_selftest.png）。
两阶段（proto 里有 ct_spleen_roi 时窗口默认）：假板子对 15 号模型回放 results/quant/roi_prior/outputs，另外检查
  第二遍的每个负载 == roi.fine_inputs（由粗结果定框），最终掩膜 == roi.combine，显示的 Dice == eval_roi.py 的两阶段结果。
--board：换成真板子（imagelink 连 192.168.1.254:5060，板端 v5.6+），另外检查每层板端 logit == 整数参考；
         截图 board_check_v5/ctwin_board_<case>.png，结果 board_check_v5/gui_acceptance_ct_<case>.json。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe _verify_ctwin.py [--case spleen_10] [--board]
（要先设好 TCL_LIBRARY / TK_LIBRARY，见 run.bat）
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CT = os.path.normpath(os.path.join(HERE, "..", "..", "ct"))
sys.path.insert(0, HERE)

import numpy as np          # noqa: E402

import ct_volume            # noqa: E402
import ctwin                # noqa: E402
import proto                # noqa: E402

FAILS = []


def check(label, ok, extra=""):
    print("%-60s %s %s" % (label, "OK" if ok else "FAIL", extra), flush=True)
    if not ok:
        FAILS.append(label)


class FakeLink(object):
    """outputs: {model index: [z, 128, 128] int8}, replayed in send order per model."""

    def __init__(self, outputs):
        self.outputs, self.sent, self.k = outputs, [], 0
        self.next = {m: 0 for m in outputs}
        self.host, self.port = "fake", 0

    def send_v6c(self, payload, w, **kw):
        self.sent.append((payload, w, kw))

    def recv_v6c(self, flags, w, nextra=0):
        m = self.sent[self.k][2]["model0"]
        self.k += 1
        q = self.outputs[m][self.next[m]]
        self.next[m] += 1
        time.sleep(0.005)
        return dict(model=m, ai_us=37800), q.tobytes(), None

    def close(self):
        pass


class FakeApp(object):
    def __init__(self, root, link):
        import tkinter as tk
        self.root, self.link, self.busy = root, link, False
        self.var_host, self.var_port = tk.StringVar(value=link.host), tk.StringVar(value=str(link.port))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="spleen_10")
    ap.add_argument("--tag", default="z3_r128")
    ap.add_argument("--fine", default="roi_prior")
    ap.add_argument("--board", action="store_true")
    ap.add_argument("--job", action="store_true", help="运行方式 = 板上整病例（v7 任务，需 --board、板端 v5.11+）")
    args = ap.parse_args()
    import tkinter as tk
    try:                                    # same as app.py: physical pixels, so the screenshot box is right
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    res = os.path.join(CT, "results")
    ref = np.load(os.path.join(res, "quant", args.tag, "outputs", args.case + ".npy"))
    two = ctwin.ct_model("ct_spleen_roi") is not None
    if two:
        ref1 = np.load(os.path.join(res, "quant", args.fine, "outputs", args.case + ".npy"))
        ev = json.load(open(os.path.join(res, "eval", "two_stage_" + args.fine, "val.json"), encoding="utf-8"))
        chosen = "roi"
        want = next(r for r in ev["per_case"] if r["case"] == args.case)["two"]["dice"]
    else:
        ev = json.load(open(os.path.join(res, "eval", args.tag, "val.json"), encoding="utf-8"))
        chosen = ev["chosen"]
        want = next(r for r in ev["per_case"] if r["case"] == args.case)[chosen]["dice"]
    cache = np.load(os.path.join(CT, "data", "cache", args.case + ".npz"))
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
        link = FakeLink({14: ref, 15: ref1} if two else {14: ref})
    app = FakeApp(root, link)
    win = ctwin.CTWindow(app)
    if args.job and not args.board:
        raise SystemExit("--job needs --board")
    win.var_mode.set(ctwin.MODES[0 if args.job else 1][1])     # 默认测逐层流式（发出的每个负载都可查）
    check("post-processing default = validation choice", win._variant() == chosen, chosen + (" (two-stage)" if two else ""))
    path = os.path.join(ctwin.MSD_DIR, "imagesTr", args.case + ".nii.gz")
    win.load(path)

    def pump(cond, timeout):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            root.update()
            time.sleep(0.01)
        return cond()

    check("volume loaded", pump(lambda: win.payloads is not None, 30), "%d slices" % len(ref))
    check("label found and identical to the training cache",
          win.vol["label"] is not None and np.array_equal(win.vol["label"], cache["lab"].astype(bool)))
    # offline payloads straight from the training cache (same code path as train / eval / export)
    sys.path.insert(0, CT)
    import common
    x = common.box(common.stack_input(cache["img"], "z3", float(cache["spacing"][2])).transpose(0, 3, 1, 2), 4)
    x = x.transpose(0, 2, 3, 1)
    win.run()
    check("whole volume streamed", pump(lambda: win.stats is not None, 120), "%s" % win.stats)
    if two and not args.job:
        check("ROI pass streamed", pump(lambda: win.stats_fine is not None, 120), "%s" % win.stats_fine)
    if args.job:
        check("board case job ran (no compact frames sent)", win.job is not None and not link.sent,
              "board %.2f s, PL %.2f s" % (win.job["res"]["ms"]["total"] / 1000, win.job["res"]["pl_ms"] / 1000))
    print("status:", win.var_txt.get())
    sent0 = [t for t in link.sent if t[2]["model0"] == 14]
    sent1 = [t for t in link.sent if t[2]["model0"] == 15]
    if args.board:
        check("board coarse logits == integer reference (every slice)", np.array_equal(win.logits, ref))
    if not args.job:
        check("every coarse payload == offline cache payload",
              len(sent0) == len(x) and all(p == np.ascontiguousarray(s).tobytes() for (p, w, kw), s in zip(sent0, x)))
        check("compact RGB frames at 128",
              all(w == 128 and kw["kind"] == proto.V6C_KIND_RGB for p, w, kw in link.sent))
    check("final mask computed", pump(lambda: win.final is not None, 60))
    if two:
        import roi
        m0 = roi.coarse_mask(ref)
        rw = roi.roi_window(m0, float(cache["spacing"][2]))
        xf = roi.fine_inputs(cache["img"], m0, rw, "roi_prior", float(cache["spacing"][2]))
        check("ROI window == roi.roi_window", tuple(win.win) == tuple(rw), str(rw))
        if not args.job:
            check("every ROI payload == roi.fine_inputs",
                  len(sent1) == len(xf) and all(p == np.ascontiguousarray(s).tobytes() for (p, w, kw), s in zip(sent1, xf)))
        if args.board:
            check("board fine logits == integer reference (every ROI slice)", np.array_equal(win.fine_logits, ref1))
        check("final mask == roi.combine(reference)", np.array_equal(win.final, roi.combine(ref1, rw, len(ref), 0.5)))
    else:
        check("final mask == ct_volume.postprocess(reference, chosen)",
              np.array_equal(win.final, ct_volume.postprocess(ref, chosen)))
    txt = win.var_res.get()
    check("displayed Dice == offline evaluation", ("%.3f" % want) in txt, "%s | want %.3f" % (txt, want))
    for _ in range(30):
        root.update()
        time.sleep(0.05)
    win.top.update()
    try:
        from PIL import ImageGrab
        x0, y0 = win.top.winfo_rootx(), win.top.winfo_rooty()
        img = ImageGrab.grab((x0, y0, x0 + win.top.winfo_width(), y0 + win.top.winfo_height()))
        out = os.path.join(HERE, "board_check_v5", ("ctwin_board_%s%s.png" % ("job_" if args.job else "", args.case)) if args.board else "ctwin_selftest.png")
        img.save(out)
        print("screenshot", out)
    except Exception as e:
        print("screenshot skipped:", e)
    if args.board:
        rep = dict(case=args.case, mode="job" if args.job else "stream", post=chosen, checks_failed=FAILS,
                   stats=win.stats, stats_fine=win.stats_fine, job=win.job and win.job["res"],
                   window=None if win.win is None else list(win.win), result=win.var_res.get(),
                   status=win.var_txt.get(), utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        json.dump(rep, open(os.path.join(HERE, "board_check_v5", "gui_acceptance_ct_%s%s.json"
                                         % ("job_" if args.job else "", args.case)), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=1)
    win.close()
    root.destroy()
    print("CTWIN_SELFTEST_PASS" if not FAILS else "CTWIN_SELFTEST_FAIL %s" % FAILS)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
