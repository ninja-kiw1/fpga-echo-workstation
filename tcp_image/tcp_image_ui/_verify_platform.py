# -*- coding: utf-8 -*-
"""_verify_platform.py —— 「平台扩展」窗口（platformwin.py）在真板上的自证（服务端 v5.24）。

  1. 心超工作站点「平台扩展…」：同一进程里的窗口（不另起 app.py），共用板子连接与 busy；
  2. 眼底血管页：示例图上板一帧（512 档，回传叠加 + 统计）；皮肤镜页：换模型、示例图、上板一帧；
  3. CT 脾脏整卷页：载入 MSD 一卷，板上整卷分割，出体积；
  4. PL 程序台页：默认流水线（编译算子 + 模型）在板上跑完；
  5. 关窗口 = 藏起来，再开结果还在；心超工作站的 busy 没被单幅图页的构造动过。
截图 board_check_v5/platform_*.png。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe _verify_platform.py [--host 192.168.1.254] [--no-ct]
"""
import argparse
import glob
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLD = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "board_check_v5")
FAILS = []


def check(label, ok, extra=""):
    print("%-62s %s %s" % (label, "OK" if ok else "FAIL", extra), flush=True)
    if not ok:
        FAILS.append(label)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.254")
    ap.add_argument("--no-ct", action="store_true")
    args = ap.parse_args()
    import tkinter as tk
    import heartapp
    heartapp._enable_dpi_awareness()
    root = tk.Tk()
    heart = heartapp.HeartApp(root, args.host, 5060, "keyframe")
    heart.link.timeout = 60

    def pump(cond, timeout):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            root.update()
            time.sleep(0.01)
        return cond()

    def shot(win, name):
        try:
            from PIL import ImageGrab
            win.lift()
            for _ in range(20):
                root.update()
                time.sleep(0.03)
            x, y = win.winfo_rootx(), win.winfo_rooty()
            ImageGrab.grab((x, y, x + win.winfo_width(), y + win.winfo_height())).save(os.path.join(OUT, name))
        except Exception as ex:
            print("screenshot skipped:", ex)

    root.update()
    heart._open_platform()
    pf = heart.platform
    check("platform window opened in-process, fundus page", pf is not None and pf.app is not None and pf.cur == "fundus"
          and pf.app.link is heart.link, pf.var_desc.get()[:60])
    a = pf.app
    check("fundus program: model 0 at 512, a sample image loaded", a._ui_model()["name"] == "fundus"
          and a.var_ai_res.get() == "512" and a.image is not None, a.var_pattern.get())

    def send_and_wait():
        n0 = len(a.hist)
        a._on_send()
        return pump(lambda: len(a.hist) > n0 and not a.busy, 60)
    ok = send_and_wait()
    check("fundus: one frame on the board (overlay + stats)", ok and a.ai_stats is not None and "overlay" in a.reply_images,
          "%s | %s" % (a.var_total.get(), a.var_infer.get()))
    check("busy is shared with the echo workstation", not heart.busy and a.busy is heart.busy)
    shot(pf.top, "platform_fundus.png")

    pf.switch("skin")
    check("skin program: model 1 at 128, ISIC sample loaded", a._ui_model()["name"] == "skin" and a.var_ai_res.get() == "128"
          and a.var_pattern.get() == "ISIC_0011336",
          a.var_prog_title.get())
    ok = send_and_wait()
    check("skin: one frame on the board", ok and a.ai_stats is not None and a.ai_stats.get("model") == 1,
          "%s | %s" % (a.var_total.get(), a.var_infer.get()))
    shot(pf.top, "platform_skin.png")

    if not args.no_ct:
        pf.switch("ct")
        cw = getattr(pf, "ct_win", None)
        vols = sorted(glob.glob(os.path.join(PLD, "Task09_Spleen", "Task09_Spleen", "imagesTr", "spleen_*.nii.gz")),
                      key=os.path.getsize)
        check("CT page built (embedded CTWindow)", cw is not None and bool(vols))
        if cw is not None and vols:
            cw.load(vols[0])
            pump(lambda: cw.payloads is not None and not cw.busy, 120)
            cw.run()
            ok = pump(lambda: not cw.busy and not heart.busy and cw.final is not None, 300)
            check("CT: whole volume segmented on the board", ok, cw.var_res.get()[:80] or cw.var_txt.get()[:80])
            shot(pf.top, "platform_ct.png")

    pf.switch("prog")
    pw = getattr(pf, "prog_win", None)
    check("PL program page built (embedded ProgWindow)", pw is not None)
    if pw is not None:
        pf.switch("fundus")
        pf.switch("prog")
        pw.run()
        ok = pump(lambda: not pw.busy and not heart.busy, 120)
        check("PL program desk: pipeline ran on the board", ok and "失败" not in pw.var_txt.get(), pw.var_txt.get()[:90])
        shot(pf.top, "platform_prog.png")

    pf.hide()
    root.update()
    hidden = pf.top.state() == "withdrawn"
    heart._open_platform()
    check("close = hide; reopen = same window with results", hidden and heart.platform is pf and pf.app is a
          and a.ai_stats is not None)
    root.destroy()
    print("PLATFORM_PASS" if not FAILS else "PLATFORM_FAIL %s" % FAILS)


if __name__ == "__main__":
    main()
