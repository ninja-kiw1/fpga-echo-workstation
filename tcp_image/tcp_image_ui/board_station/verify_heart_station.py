# -*- coding: utf-8 -*-
"""verify_heart_station.py —— 在板上（DP 显示器 :0，tkinter -> GTK3 兼容层）自证心超工作站，连本机 127.0.0.1:5060。

  1. heartapp 在兼容层下建得起来；--demo 预置：快照（CAMUS patient0027）、关键帧（HMC-QU ES000108）、停在现场模式；
  2. 关键帧模式：板上整病例，EF / 区间 / 全心；点击纠错一次（echo_inter + echo_seq）、撤销；导出报告；
  3. 现场模式（按演示清单顺序，放在最前）：50 fps 源推荐档 / 每帧推理对照、ES000108 复核 + 全心、现场纠错往后重算；
  4. 平台扩展：眼底一帧、皮肤镜一帧；PL 程序台流水线；
  5. 兼容层缺口报告（TK_SHIM_LENIENT=1 时）。截图（scrot）到 /data/board_station/logs/heart_*.png。
要先停掉正在跑的工作站（板端服务一次只服务一个连接）。

    python3 -u verify_heart_station.py
"""
import os
import sys
import time
import faulthandler  # noqa: E402
import signal  # noqa: E402
faulthandler.register(signal.SIGUSR1, all_threads=True)      # 卡住时 kill -USR1 <pid> 打出各线程的栈

BOARD = os.path.dirname(os.path.abspath(__file__))
sys.argv = [sys.argv[0]]
import station_heart  # noqa: E402,F401  （装好兼容层、补丁、EXTRA_TOOLS）

import heartapp  # noqa: E402
import heartview  # noqa: E402
import tkinter as tk  # noqa: E402
from open_wins import shoot_rect  # noqa: E402

LOGS = "/data/board_station/logs"
FAILS = []


def check(label, ok, extra=""):
    print("%-58s %s %s" % (label, "OK" if ok else "FAIL", extra), flush=True)
    if not ok:
        FAILS.append(label)


class Ev(object):
    def __init__(self, x, y):
        self.x, self.y = x, y


def main():
    root = tk.Tk()
    app = heartapp.HeartApp(root, "127.0.0.1", 5060, "keyframe")
    app.link.timeout = 60
    try:
        root.state("zoomed")
    except Exception:
        pass

    def pump(cond, timeout):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            root.update()
            time.sleep(0.01)
        return cond()

    def shot(name, win=None):
        win = win or root
        for _ in range(30):
            root.update()
            time.sleep(0.03)
        try:
            im, size, colors = shoot_rect(os.path.join(LOGS, name), win.winfo_rootx(), win.winfo_rooty(),
                                          win.winfo_width(), win.winfo_height())
            print("  shot %s: %s bytes, %s colours" % (name, size, colors))
        except Exception as ex:
            print("  shot %s skipped: %r" % (name, ex))

    app.check_board()
    check("caps over loopback (v5.24+)", pump(lambda: "已连接" in app.var_link.get(), 30), app.var_status.get())
    heartapp.prepare_demo(app)
    kv = app.views["keyframe"]
    check("--demo: snapshot + keyframe materials, ends in live", pump(lambda: kv.frames is not None, 60)
          and app.mode == "live" and app.views["snapshot"].frames is not None, kv.var_name.get())


    # ---- 演示 B：现场模式（清单第 2–4 步）----
    import numpy as np
    app.switch("live")
    lv = app.views["live"]
    lv.open_report = False
    check("live default source = 50 fps demo + fixed crop", heartview.DEMO_SRC in lv.var_src.get()
          and lv.var_crop.get() == heartview.CROP_FIXED, lv.var_src.get())

    def live_run(sched):
        lv.var_sched.set(next(v for k_, v in heartview.SCHEDS if k_ == sched))
        lv.run()
        ok_ = pump(lambda: not lv.busy and lv.res is not None, 180)
        return (lv.res["stream"]["latency_ms"] if ok_ else None), ((sum(lv.proc) / len(lv.proc)) if lv.proc else None)
    lat, proc = live_run("keyframe")
    check("step 2 live recommended: no dropped frames, P99 < 80 ms, budget < 40 %", lat is not None and lat["p99"] < 80
          and proc < 0.4 * 1000.0 / lv.fps and "不丢" in lv.var_k_fps.get(),
          "%s | %s | %s" % (lv.var_k_proc.get(), lv.var_k_lat.get(), lv.var_k_fps.get()))
    check("step 2 (v5.25) board DP: no rolling light (CPU budget), live result as before", "rolling" not in (lv.res or {})
          and lv.roll == (None, 0), "rolling off on DP")
    shot("heart_live.png")
    lat, proc = live_run("full")
    check("step 3 live per-frame: budget > 100 %, latency accumulates, grey reference", lat is not None
          and proc > 1000.0 / lv.fps and lat["max"] > 300 and lv.prev_run is not None,
          "%s | %s" % (lv.var_k_proc.get(), lv.var_k_lat.get()))
    shot("heart_live_full.png")
    src = next((s_ for s_ in lv.sources if s_[1]["name"] == "ES000108_4CH_1.y8"), None)
    check("step 4 source ES000108_4CH_1.y8 listed", src is not None)
    if src:
        lv.var_src.set(src[0])
        lat, _ = live_run("keyframe")
        check("step 4 live ES000108: EF 41 %, review, whole heart", lat is not None and round(lv.res["ef"]) == 41
              and lv.res["interval"]["review"] and lv.heart is not None and lat["p99"] < 80,
              "EF %s %s | %s" % (lv.var_ef.get(), lv.var_review.get(), lv.var_k_lat.get()))
        shot("heart_live_es108.png")
        lv._pause()
        lv._goto(9)
        m = lv.masks[9]
        rr, cc = np.nonzero(m)
        lv._click(Ev(int(cc.mean()) * 4 + 2, int(rr.min() + 2) * 4 + 2), False)
        ok = pump(lambda: not lv.busy and 9 in lv.fixed, 90)
        prop = sorted(k_ for k_, v in lv.fixed.items() if v == "prop")
        check("live click-correction: all later frames recomputed", ok and prop == list(range(10, len(lv.frames))),
              lv.var_fix.get())
        lv.reset_fix()
    # v5.26：CAMUS 模拟扫查源 -> CAMUS 口径滚动灯 + 切面（这个源 DP 上也开灯）
    cs = next((s_ for s_ in lv.sources if s_[1].get("quality") == "camus"), None)
    check("v5.26 CAMUS simulated-scan source listed", cs is not None)
    if cs and "v5.2" in app.var_link.get() and int(app.var_link.get().split("v5.")[1][:2]) >= 26:
        lv.var_src.set(cs[0])
        lat, _ = live_run("keyframe")
        ro = (lv.res or {}).get("rolling") or {}
        segs = cs[1].get("segments") or []
        ls = [(u[0], u[1]) for u in ro.get("updates", [])]
        seg_light = [max(range(3), key=[l_ for t_, l_ in ls if s_["start"] <= t_ < s_["start"] + s_["n"]].count)
                     for s_ in segs]
        check("v5.26 board DP CAMUS chain: lights follow the expert grades, tag + note, P99 < 80 ms, judge-then-hold", lat is not None
              and ro.get("chain") in (1, 2) and "wall_motion" not in lv.res and seg_light[:2] == [0, 2] and seg_light[-1] == 0
              and "CAMUS 专家等级标定" in lv.var_qwhy.get() and lat["p99"] < 80
              and (ro.get("hold") or {}).get("held_frames", 0) > 0,
              "segments %s -> %s | %s" % ([s_["quality"] for s_ in segs], seg_light, lv.var_k_lat.get()))
        shot("heart_live_camus.png")

    # ---- 演示 C：关键帧模式（清单第 5–9 步）----
    app.switch("keyframe")
    kv.open_report = False
    kv.run()
    ok = pump(lambda: not kv.busy and kv.res is not None, 180)
    check("keyframe: board whole case, EF / interval / heart", ok and kv.res.get("interval") is not None
          and kv.heart is not None, "EF %s %s" % (kv.var_ef.get(), kv.var_review.get()))
    if ok:
        r_pc = kv.ws.analyze(kv.vols, kv.fps, 1, kv.conf["cfg"])
        check("re-analysis (compat find_peaks) == board EF", abs(kv._cal(r_pc["ef"]) - kv.res["ef"]) < 0.05,
              "%.3f vs %.3f" % (kv._cal(r_pc["ef"]), kv.res["ef"]))
        t = 9                                        # 清单第 7 步：停在第 10 帧
        kv._pause()
        kv._goto(t)
        m = kv.masks[t]
        rr, cc = np.nonzero(m)
        kv._click(Ev(int(cc.mean()) * 4 + 2, int(rr.min() + 2) * 4 + 2), False)
        check("step 7 click-correction on the board", pump(lambda: not kv.busy and t in kv.fixed, 60), kv.var_fix.get())
        prop = sorted(k_ for k_, v in kv.fixed.items() if v == "prop")
        check("step 7 all later frames recomputed (to the end)", prop == list(range(t + 1, len(kv.frames))),
              "%d..%d" % (prop[0] + 1, prop[-1] + 1) if prop else "none")
        shot("heart_keyframe_fix.png")
        kv._goto(t + 10)
        shot("heart_keyframe_fix_later.png")
        kv.undo_fix()
        check("undo", not kv.fixed)
        kv._report()
        rp = getattr(kv, "last_report", "")
        check("evidence report written", bool(rp) and os.path.exists(rp), rp)
        kv._send_snapshot()                          # 还没定标：自动读屏上深度刻度、直接定标并送（10-31，不弹框）
        auto = getattr(kv, "calib_auto", None)
        check("screen-scale calibration read automatically (5 cm = ~139 px, no dialog)", auto is not None
              and abs(auto["px_5cm"] - 139.0) < 2 and kv._spacing() is not None, kv.btn_scale.cget("text"))
        sv0 = app.views["snapshot"]
        ok = pump(lambda: sv0.mono is not None and sv0.ef_result is not None and not sv0.busy, 60)
        # 板上 OpenCV 3.4 解 AVI 与 PC 的 4.x 略有差别（关键帧 EF 41.203 对 PC 41.228），所以给容差：PC 上是 33 % / 64 / 43 mL
        check("keyframe -> snapshot single-plane Simpson (~33 %, ~64 / 43 mL)", ok and abs(sv0.ef_result["ef"] - 33) <= 2
              and abs(sv0.ef_result["edv"] - 64) <= 3, "%s %s %s" % (sv0.var_mef.get(), sv0.var_edv.get(), sv0.var_esv.get()))
        shot("heart_mono.png")
        app.switch("keyframe")

    app.switch("snapshot")
    sv = app.views["snapshot"]
    check("snapshot: three CAMUS cases available", len(sv.cases) == 3, [c[0] for c in sv.cases])
    sv.var_case.set("%s（%s）" % sv.cases[0])
    sv.measure_case()
    ok = pump(lambda: sv.ef_result is not None and not sv.busy and sv.auto is None, 180)
    check("snapshot: one-click biplane Simpson == fig. 6 (EF 39 %)", ok and sv.ef_result["ef"] == 39
          and sv.ef_result.get("cardiac") is not None, "%s %s %s" % (sv.var_mef.get(), sv.var_edv.get(), sv.var_esv.get()))
    shot("heart_snapshot_measure.png")

    app._open_platform()
    pf = app.platform
    a = pf.app
    n0 = len(a.hist)
    a._on_send()
    check("platform fundus: one frame", pump(lambda: len(a.hist) > n0 and not a.busy, 60) and a.ai_stats is not None,
          "%s | %s" % (a.var_total.get(), a.var_infer.get()))
    shot("heart_platform_fundus.png", pf.top)
    pf.switch("skin")
    n0 = len(a.hist)
    a._on_send()
    check("platform skin: one frame", pump(lambda: len(a.hist) > n0 and not a.busy, 60)
          and (a.ai_stats or {}).get("model") == 1, "%s | %s" % (a.var_pattern.get(), a.var_infer.get()))
    shot("heart_platform_skin.png", pf.top)
    pf.switch("prog")
    pw = pf.prog_win
    pw.run()
    check("platform PL program desk pipeline", pump(lambda: not pw.busy and not app.busy, 60)
          and "失败" not in pw.var_txt.get(), pw.var_txt.get()[:70])
    check("extra tools (TF video / self-test) in 平台扩展 → 更多", [t[0] for t in app.extra_tools] == ["TF 卡视频", "板端自检"])
    shot("heart_platform_prog.png", pf.top)
    pf.hide()
    try:
        from tkinter import _widgets as tw
        tw.missing_report()
    except Exception:
        pass
    root.destroy()
    print("HEART_STATION_PASS" if not FAILS else "HEART_STATION_FAIL %s" % FAILS, flush=True)


if __name__ == "__main__":
    main()
