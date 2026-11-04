# -*- coding: utf-8 -*-
"""_verify_heartapp.py —— 心超工作站主界面（heartapp.py + heartview.py）在真板上的自证（板端 v5.24）。

  1. 主窗口起来、检测连接：caps 报 v5.24、支持关键帧 / 现场 / 全心；
  2. 关键帧模式：打开 HMC-QU 开发集录屏（设计报告图 7 / 图 8 同一段），板上整病例 -> EF / 区间 / 质量灯 / 全心卡片；
     上位机按板上容积重算的逐拍 EF 与板上一致（纠错后的重算就靠这条规则）；
  3. 点击纠错：在一个跟踪帧的左室边缘内侧右键 -> FPGA echo_inter 重算本帧（面积变小）、echo_seq 把后面各帧跟着重算到视频末尾；
     撤销 -> 回到纠错前；再纠一次后重置 -> 与板上原始结果逐位相同；
  4. 证据报告：有「人工纠错」一节、页脚是 HMC-QU 出处；
  5. 现场模式：板上信号源 ES000108_4CH_1.y8，结束后有延迟 P50 / P99、EF、区间；
  6. 快照模式：嵌入的 CAMUS 序列窗口载入 patient0027 四腔、单步上板一帧。
每步截图到 board_check_v5/heartapp_*.png。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe _verify_heartapp.py [--host 192.168.1.254] [--no-live]
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLD = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import numpy as np  # noqa: E402

FAILS = []
CLIP = os.path.join(PLD, "HMC-QU Dataset-Kaggle v1.0 (1)", "HMC-QU Dataset-Kaggle v1.0", "HMC-QU", "A4C", "ES000108 _4CH_1.avi")
OUT = os.path.join(HERE, "board_check_v5")


def check(label, ok, extra=""):
    print("%-64s %s %s" % (label, "OK" if ok else "FAIL", extra), flush=True)
    if not ok:
        FAILS.append(label)


class Ev(object):
    def __init__(self, x, y):
        self.x, self.y = x, y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.254")
    ap.add_argument("--no-live", action="store_true")
    args = ap.parse_args()
    import tkinter as tk
    import heartapp
    heartapp._enable_dpi_awareness()
    root = tk.Tk()
    app = heartapp.HeartApp(root, args.host, 5060, "keyframe")
    app.link.timeout = 60

    def pump(cond, timeout):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            root.update()
            time.sleep(0.01)
        return cond()

    def shot(name):
        try:
            from PIL import ImageGrab
            root.lift()
            for _ in range(15):
                root.update()
                time.sleep(0.03)
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(os.path.join(OUT, name))
        except Exception as ex:
            print("screenshot skipped:", ex)

    os.makedirs(OUT, exist_ok=True)
    root.update()
    app.check_board()
    check("board caps (v5.24+, keyframe / live / heart)", pump(lambda: "已连接" in app.var_link.get(), 30)
          and "关键帧 / 现场 / 全心" in app.var_status.get(), app.var_status.get())

    # ---- 2. 关键帧模式 ----
    v = app.views["keyframe"]
    v.open_report = False
    v.load(CLIP)
    check("HMC-QU clip loaded", pump(lambda: v.frames is not None, 60), v.var_name.get())
    v.run()
    check("keyframe job finished", pump(lambda: not v.busy and v.res is not None, 180), v.var_txt.get()[:90])
    res = v.res
    check("EF / interval / quality / heart present", res is not None and res.get("interval") and res.get("quality")
          and v.heart is not None and v.var_ef.get().endswith("%"), "EF %s %s" % (v.var_ef.get(), v.var_review.get()))
    kf = res["keyframe"]
    check("keyframe fraction about 1/6", 0.1 < kf["key_frac"] < 0.3, "%.3f" % kf["key_frac"])
    r_pc = v.ws.analyze(v.vols, v.fps, 1, v.conf["cfg"])
    same_beats = [(b["ed"], b["es"]) for b in r_pc["beats"]] == [(b["ed"], b["es"]) for b in res["beats"]]
    ef_pc = v._cal(r_pc["ef"])
    check("PC re-analysis of board volumes == board beats / EF", same_beats and abs(ef_pc - res["ef"]) < 0.05,
          "PC %.3f board %.3f" % (ef_pc, res["ef"]))
    shot("heartapp_keyframe.png")

    # ---- 3. 点击纠错 ----
    from scipy import ndimage
    tracked = [t for t in range(len(v.keyv) - 8) if not v.keyv[t] and v.keyv[t - 1] and t > 0]
    t = tracked[len(tracked) // 2]
    check("result loops on its own after analysis (PC)", v.playing)
    v._pause()                                       # 分析完会自动循环播放；点击纠错先停
    v._goto(t)
    m0 = v.masks[t].copy()
    ring = m0 & ~ndimage.binary_erosion(m0, iterations=3)
    rr, cc = np.nonzero(ring & ndimage.binary_erosion(m0, iterations=1))
    k = len(rr) // 2
    a0, vol0 = int(m0.sum()), v.vols[t]
    v._click(Ev(int(cc[k]) * 4 + 2, int(rr[k]) * 4 + 2), False)
    check("negative click: FPGA recompute finished", pump(lambda: not v.busy and t in v.fixed, 30), v.var_fix.get())
    prop = sorted(k_ for k_, s in v.fixed.items() if s == "prop")
    check("all later frames recomputed (to the end of the video)", prop == list(range(t + 1, len(v.frames))),
          "%d..%d of %d" % (prop[0] + 1, prop[-1] + 1, len(v.frames)) if prop else "none")
    a1 = int(v.masks[t].sum())
    check("mask at clicked frame shrank, volume recomputed", a1 < a0 and v.vols[t] != vol0, "area %d -> %d" % (a0, a1))
    check("EF recomputed on PC and labelled", "人工纠错" in v.var_efnote.get() and v.ef_now is not None,
          v.var_efnote.get())
    shot("heartapp_fix.png")
    v.undo_fix()
    check("undo restores the board result", not v.fixed and np.array_equal(v.masks[t], m0) and v.vols == v.orig["vols"]
          and abs(v.ef_now - res["ef"]) < 1e-9)
    v._click(Ev(int(cc[k]) * 4 + 2, int(rr[k]) * 4 + 2), False)
    pump(lambda: not v.busy and t in v.fixed, 30)
    v._goto(t + 2)
    v._click(Ev(224, 224), True)
    pump(lambda: not v.busy and len(v.undo) == 2, 30)
    check("two corrections stacked", len(v.undo) == 2, v.var_fix.get())
    v._goto(t - 1)                                   # 在更早的帧纠错：往后重算，到第 t 帧（人工纠过）前停
    v._click(Ev(224, 224), True)
    pump(lambda: not v.busy and len(v.undo) == 3, 30)
    check("earlier correction stops before a manually fixed frame", len(v.undo) == 3 and v.fixed.get(t) == "fix"
          and set(v.undo[-1]["masks"]) == {t - 1}, v.var_fix.get())

    # ---- 4. 报告 ----
    v._report()
    rp = getattr(v, "last_report", None)
    html = open(rp, encoding="utf-8").read() if rp and os.path.exists(rp) else ""
    check("report: correction section + HMC-QU data note", "人工纠错（医生点击，FPGA 重算）" in html and "HMC-QU" in html
          and "全心：节段室壁运动与右心" in html, rp or "")
    v.reset_fix()
    check("reset == board original (bit for bit)", not v.fixed and not v.undo and all(
        np.array_equal(v.masks[k_], v.orig["masks"][k_]) for k_ in v.masks) and v.vols == list(res["vols"]))

    # ---- 5. 现场模式 ----
    if not args.no_live:
        import heartview
        app.switch("live")
        lv = app.views["live"]
        lv.open_report = False
        check("live defaults: 50 fps demo source + fixed crop", heartview.DEMO_SRC in lv.var_src.get()
              and lv.var_crop.get() == heartview.CROP_FIXED, lv.var_src.get())

        def live_run(sched):
            lv.var_sched.set(next(v for k_, v in heartview.SCHEDS if k_ == sched))
            lv.run()
            ok = pump(lambda: not lv.busy and lv.res is not None, 180)
            return ok and lv.res["stream"]["latency_ms"], (sum(lv.proc) / len(lv.proc)) if lv.proc else None
        lat_k, proc_k = live_run("keyframe")
        gap = 1000.0 / lv.fps
        check("live z4 recommended: no dropped frames, latency P99 < 80 ms, budget < 40 %",
              lat_k and lat_k["p99"] < 80 and proc_k < 0.4 * gap and "不丢" in lv.var_k_fps.get(),
              "P50 %.0f P99 %.0f proc %.1f ms (%s) %s" % (lat_k["p50"], lat_k["p99"], proc_k, lv.var_k_proc.get(),
                                                       lv.var_k_fps.get()) if lat_k else "")
        check("live: per-frame latency trace == frames", len(lv.lat) == lv.res["n_frames"], len(lv.lat))
        ro = lv.res.get("rolling") or {}
        check("live (v5.25): rolling quality light + view while scanning, summary in the result card",
              len(ro.get("updates", [])) > 10 and ro.get("views") and lv.roll[0] is not None and lv.roll[1] == 2
              and "扫查过程" in lv.var_qwhy.get(), lv.var_qwhy.get().split("\n")[-1])
        lat_f, proc_f = live_run("full")
        check("live z4 per-frame inference: budget > 100 %, latency accumulates", lat_f and proc_f > gap
              and lat_f["max"] > 300, "P50 %.0f P99 %.0f max %.0f proc %.1f ms" % (
                  lat_f["p50"], lat_f["p99"], lat_f["max"], proc_f) if lat_f else "")
        check("latency chart keeps the previous run as the grey reference", lv.prev_run is not None
              and lv.prev_run["label"].startswith("推荐档"), (lv.prev_run or {}).get("label"))
        shot("heartapp_live_compare.png")
        src = next((s for s in lv.sources if s[1]["name"] == "ES000108_4CH_1.y8"), None)
        check("live source ES000108_4CH_1.y8 listed", src is not None)
        if src:
            lv.var_src.set(src[0])
            lat_e, _ = live_run("keyframe")
            check("live ES000108 fixed crop == design report fig. 7 (EF 41 %, review)", lat_e
                  and round(lv.res["ef"]) == 41 and lv.res["interval"]["review"] and lat_e["p99"] < 80,
                  "EF %.1f P50 %.0f P99 %.0f" % (lv.res["ef"], lat_e["p50"], lat_e["p99"]) if lat_e else "")
            shot("heartapp_live.png")
        # v5.26：CAMUS 模拟扫查源 -> CAMUS 口径的滚动灯 + 切面（服务端 caps "live_roll_camus"）
        cs = next((s for s in lv.sources if s[1].get("quality") == "camus"), None)
        check("live (v5.26): CAMUS simulated-scan source listed", cs is not None)
        import re
        srv = re.search(r"v5\.(\d+)", app.var_link.get())
        if srv is None or int(srv.group(1)) < 26:
            print("SKIP live (v5.26) CAMUS chain: server %s" % app.var_link.get(), flush=True)
        elif cs:
            lv.var_src.set(cs[0])
            lat_c, _ = live_run("keyframe")
            ro = lv.res.get("rolling") or {}
            segs = cs[1].get("segments") or []
            ls = [(u[0], u[1]) for u in ro.get("updates", [])]
            seg_light = [max(range(3), key=[l for t, l in ls if s["start"] <= t < s["start"] + s["n"]].count) for s in segs]
            check("live (v5.26) CAMUS chain: chain 1, lights follow the expert grades (Good green, Poor red), "
                  "tag + note in the card, report uses the CAMUS light, P99 < 80 ms, judge-then-hold",
                  lat_c and ro.get("chain") in (1, 2) and len(ls) > 10 and "wall_motion" not in lv.res and not lv.hints and seg_light[:2] == [0, 2] and seg_light[-1] == 0
                  and lv.roll_tag == "CAMUS 专家等级标定" and "CAMUS 专家等级标定" in lv.var_qwhy.get()
                  and ("EF 没有临床意义" in lv.var_qwhy.get() or "CAMUS 训练集校准" in lv.var_qwhy.get()) and lv._report_quality()["light"] == ls[-1][1]
                  and "CAMUS" in lv.data_note() and lat_c["p99"] < 80
                  and (ro.get("hold") or {}).get("held_frames", 0) > 0 and "判定后保持" in lv.var_qwhy.get(),
                  "segments %s -> majority light %s; P50 %.0f P99 %.0f" % (
                      [s["quality"] for s in segs], seg_light, lat_c["p50"], lat_c["p99"]) if lat_c else "")
            shot("heartapp_live_camus.png")

    # ---- 6. 快照模式 ----
    app.switch("snapshot")
    sv = app.views["snapshot"]
    ok = sv.load(os.path.join(PLD, "echo", "samples", "seq_patient0027_4CH"))
    check("snapshot: CAMUS sequence loaded in the embedded view", ok and sv.frames is not None)
    sv._step_once()
    check("snapshot: one frame segmented on the board", pump(lambda: sv.tracker.steps and not sv.busy, 30),
          sv.var_frame.get())
    shot("heartapp_snapshot.png")
    app.switch("keyframe")
    check("switching back keeps keyframe results", app.views["keyframe"].res is res)
    root.destroy()

    # ---- 7. --demo 预置 ----
    root = tk.Tk()
    app = heartapp.HeartApp(root, args.host, 5060, "keyframe")
    heartapp.prepare_demo(app)
    ok = pump(lambda: app.views["keyframe"].frames is not None, 60)
    check("--demo: three modes prepared, ends in live mode", ok and app.mode == "live"
          and app.views["snapshot"].frames is not None, app.var_status.get())
    root.destroy()
    with open(os.path.join(OUT, "heartapp_board.json"), "w", encoding="utf-8") as f:
        json.dump(dict(fails=FAILS, report=rp, ef=res["ef"], key_frac=kf["key_frac"]), f, indent=1, ensure_ascii=False)
    print("HEARTAPP_PASS" if not FAILS else "HEARTAPP_FAIL %s" % FAILS)


if __name__ == "__main__":
    main()
