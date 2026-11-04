# -*- coding: utf-8 -*-
"""_demo_screens.py —— 按《现场验收演示清单》第二节在真板上走一遍完整演示，每个关键步骤截一张图。

输出：E:/PLD/demo_20261101_v525/NN_步骤.png（10-31 晚，服务端 v5.25；之前一版在 demo_20261031_full）（窗口截图；证据报告用 Edge 无界面截整页）+ index.html + shots.json（本地看，不外传）。
画面含 EchoNet / HMC-QU / CAMUS 公开数据，只作演示准备，不随交付包分发。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe _demo_screens.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLD = os.path.normpath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(PLD, "demo_20261102_v526")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
sys.path.insert(0, HERE)
import numpy as np  # noqa: E402

SHOTS = []


class Ev(object):
    def __init__(self, x, y):
        self.x, self.y = x, y


def main():
    import tkinter as tk
    from PIL import ImageGrab
    from scipy import ndimage
    import heartapp
    import heartview
    os.makedirs(OUT, exist_ok=True)
    heartapp._enable_dpi_awareness()
    root = tk.Tk()
    app = heartapp.HeartApp(root, "192.168.1.254", 5060, "keyframe")
    app.link.timeout = 60
    root.geometry("+0+0")

    def pump(cond, timeout):
        t0 = time.time()
        while not cond() and time.time() - t0 < timeout:
            root.update()
            time.sleep(0.01)
        return cond()

    def settle(sec=0.6):
        t0 = time.time()
        while time.time() - t0 < sec:
            root.update()
            time.sleep(0.02)

    def shot(name, caption, win=None):
        win = win or root
        win.attributes("-topmost", True)
        settle(0.5)
        x, y = win.winfo_rootx(), win.winfo_rooty()
        p = os.path.join(OUT, "%02d_%s.png" % (len(SHOTS) + 1, name))
        ImageGrab.grab((x, y, x + win.winfo_width(), y + win.winfo_height())).save(p)
        win.attributes("-topmost", False)
        SHOTS.append((os.path.basename(p), caption))
        print("SHOT", os.path.basename(p), "|", caption, flush=True)

    # ---- A 开场 ----
    heartapp.prepare_demo(app)
    app.check_board()
    pump(lambda: "已连接" in app.var_link.get() and app.views["keyframe"].frames is not None, 60)
    settle(1.0)
    shot("首屏_现场模式", "A 开场：打开后停在现场模式，已连接 %s；" % app.var_link.get() + "视频区、曲线、时间线写着会显示什么")

    # ---- B 实时性 ----
    lv = app.views["live"]
    lv.open_report = False
    lv.run()
    pump(lambda: len(lv.lat) >= 100, 60)
    shot("现场推荐档_运行中", "B 第 2 步（运行中）：轮廓逐帧更新；图像质量灯边扫边变（%s），下面一行「%s」；时间线蓝 = 关键帧、灰 = 跟踪帧，延迟图锯齿在 80 ms 虚线下" % (
        lv.var_light.get(), lv.var_qwhy.get()))
    pump(lambda: not lv.busy and lv.res is not None, 120)
    settle(1.5)
    shot("现场推荐档_结束", "B 第 2 步（结束）：每帧处理 %s，延迟 P50 / P99 %s，%s" % (
        lv.var_k_proc.get(), lv.var_k_lat.get(), lv.var_k_fps.get()))
    lv.var_sched.set(heartview.SCHEDS[1][1])
    lv.run()
    pump(lambda: not lv.busy and lv.res is not None, 120)
    settle(1.5)
    shot("现场每帧推理_对照", "B 第 3 步：每帧推理，蓝线涨到几百 ms，灰色锯齿是上一次推荐档；每帧处理 %s" % lv.var_k_proc.get())
    src = next(s for s in lv.sources if s[1]["name"] == "ES000108_4CH_1.y8")
    lv.var_src.set(src[0])
    lv.var_sched.set(heartview.SCHEDS[0][1])
    lv.run()
    pump(lambda: not lv.busy and lv.res is not None, 120)
    settle(1.5)
    shot("现场ES000108_复核与全心", "B 第 4 步：ES000108，EF %s，%s；全心卡片：节段 / RV/LV / RA/LV" % (
        lv.var_ef.get(), lv.var_review.get()))
    # v5.26：CAMUS 模拟扫查源（两腔 Good → 四腔 Poor → 四腔 Medium → 四腔 Good），CAMUS 口径的灯 + 判定后保持
    cs = next((s for s in lv.sources if s[1].get("quality") == "camus"), None)
    if cs is not None:
        lv.var_src.set(cs[0])
        lv.run()
        n_ = lambda: len(lv.vols)                    # noqa: E731
        q_ = lambda: lv.var_qwhy.get().split("\n")[0]  # noqa: E731
        if pump(lambda: lv.roll_hold and n_() < 120, 30):
            shot("CAMUS_1_两腔Good_已判定", "B 第 4b 步（CAMUS 第 1 段 两腔 Good，第 %d 帧）：灯 %s，「%s」——判定后板上停跑 F1" % (
                n_(), lv.var_light.get(), q_()))
        if pump(lambda: lv.roll_moved and not lv.roll_hold and n_() >= 100, 30):
            shot("CAMUS_2_换段_重新判断", "B 第 4b 步（第 2 段开始 = 探头移动，第 %d 帧）：「%s」" % (n_(), q_()))
        if pump(lambda: lv.roll_hold and 150 <= n_() < 220, 30):
            shot("CAMUS_3_四腔Poor_已判定", "B 第 4b 步（第 2 段 四腔 Poor，第 %d 帧）：灯 %s，「%s」" % (
                n_(), lv.var_light.get(), q_()))
        if pump(lambda: n_() >= 260, 30):
            shot("CAMUS_4_四腔Medium_判断中", "B 第 4b 步（第 3 段 四腔 Medium，第 %d 帧）：灯在黄 / 红之间，一直不锁定：「%s」" % (
                n_(), q_()))
        if pump(lambda: lv.roll_hold and n_() >= 300, 30):
            shot("CAMUS_5_四腔Good_已判定", "B 第 4b 步（第 4 段 四腔 Good，第 %d 帧）：灯 %s，「%s」" % (
                n_(), lv.var_light.get(), q_()))
        pump(lambda: not lv.busy and lv.res is not None, 120)
        settle(1.5)
        shot("CAMUS_6_结束", "B 第 4b 步（结束）：%s；延迟 P50 / P99 %s" % (
            lv.var_qwhy.get().replace("\n", " "), lv.var_k_lat.get()))
    # 11-02：同一个病人的模拟找切面（两腔 → 四腔没放好 → 慢慢调好 → 到位不动）
    ss = next((s for s in lv.sources if s[1]["name"] == "CAMUS_view_search_z3.y8"), None)
    if ss is not None:
        lv.var_src.set(ss[0])
        lv.run()
        n_ = lambda: len(lv.vols)                    # noqa: E731
        q_ = lambda: lv.var_qwhy.get().split("\n")[0]  # noqa: E731
        if pump(lambda: lv.roll_hold and n_() < 115, 30):
            shot("找切面_1_两腔_已判定", "B 第 4c 步（第 %d 帧，探头角度不对 = 两腔）：灯 %s，「%s」" % (n_(), lv.var_light.get(), q_()))
        if pump(lambda: lv.roll_hold and 150 <= n_() < 360 and lv.roll[0] in (1, 2), 30):
            shot("找切面_2_没放好_黄灯", "B 第 4c 步（第 %d 帧，转到四腔但没放好）：灯 %s，「%s」" % (n_(), lv.var_light.get(), q_()))
        if pump(lambda: not lv.roll_hold and lv.roll_moved and n_() >= 300, 30):
            shot("找切面_3_调整中_重判", "B 第 4c 步（第 %d 帧，慢慢调探头）：「%s」" % (n_(), q_()))
        if pump(lambda: lv.roll_hold and n_() >= 450 and lv.roll[0] == 0, 30):
            shot("找切面_4_到位_绿灯", "B 第 4c 步（第 %d 帧，到位不动）：灯 %s，「%s」" % (n_(), lv.var_light.get(), q_()))
        pump(lambda: not lv.busy and lv.res is not None, 120)
        settle(1.5)
        shot("找切面_5_结束", "B 第 4c 步（结束，同一个病人，容积曲线连续）：%s；延迟 P50 / P99 %s" % (
            lv.var_qwhy.get().replace("\n", " "), lv.var_k_lat.get()))

    # ---- C 可核对、可纠错 ----
    app.switch("keyframe")
    kv = app.views["keyframe"]
    kv.open_report = False
    kv.run()
    pump(lambda: not kv.busy and kv.res is not None, 120)
    settle(1.2)
    shot("关键帧_分析结果", "C 第 5 步：关键帧模式开始分析，约 0.4 s 出结果（与第 4 步一致），画面自动循环")
    x_of, _ = kv._xy()
    b0 = kv.result["beats"][0]
    kv._curve_click(x_of(b0["es"]))                  # 点 ▼ES
    settle(0.6)
    shot("关键帧_点ES跳帧", "C 第 6 步：点容积曲线上的 ▼ES，画面停下并跳到收缩末帧（左上角 ES）")
    t1 = 9                                           # 清单第 7 步：停在第 10 帧
    kv._goto(t1)
    settle(0.3)
    shot("关键帧_停在第10帧", "C 第 7 步（前）：暂停在第 10 帧（下方写着这一帧是关键帧还是跟踪帧）")
    m = kv.masks[t1]
    ring = m & ~ndimage.binary_erosion(m, iterations=3) & ndimage.binary_erosion(m, iterations=1)
    rr, cc = np.nonzero(ring)
    k = int(np.argmin(rr))                           # 轮廓上沿（心尖附近）往里一点
    later0 = {t: kv.masks[t].copy() for t in range(t1 + 1, len(kv.frames))}
    kv._click(Ev(int(cc[k]) * 4 + 2, int(rr[k]) * 4 + 2), False)
    pump(lambda: not kv.busy and t1 in kv.fixed, 60)
    settle(0.5)
    shot("关键帧_右键纠错后", "C 第 7 步：右键去多，%s" % kv.var_fix.get())
    changed = sum(not np.array_equal(kv.masks[t], later0[t]) for t in later0)
    print("later frames changed: %d / %d" % (changed, len(later0)), flush=True)
    kv._goto(t1 + 10)
    settle(0.4)
    shot("关键帧_后面帧跟着改", "C 第 7 步：播放到第 %d 帧，轮廓也跟着变了（下方写着「纠错后跟着重算（FPGA）」）；"
         "后面 %d / %d 帧的掩膜与纠错前不同" % (t1 + 11, changed, len(later0)))
    kv._report()
    rp = kv.last_report
    png = os.path.join(OUT, "%02d_证据报告.png" % (len(SHOTS) + 1))
    subprocess.call([EDGE, "--headless", "--disable-gpu", "--hide-scrollbars", "--window-size=900,5200",
                     "--screenshot=%s" % png, "file:///" + rp.replace("\\", "/")], timeout=60)
    if os.path.exists(png):
        SHOTS.append((os.path.basename(png), "C 第 8 步：导出的证据报告（整页），含「人工纠错」一节"))
        print("SHOT", os.path.basename(png), flush=True)
    kv.undo_fix()
    settle(0.4)
    shot("关键帧_撤销", "C 第 9 步：撤销（Ctrl+Z）退回上一步：橙色标记消失，EF 回到板上原值")
    kv._click(Ev(int(cc[k]) * 4 + 2, int(rr[k]) * 4 + 2), False)
    pump(lambda: not kv.busy and t1 in kv.fixed, 60)
    kv.reset_fix()
    settle(0.4)
    shot("关键帧_重置", "C 第 9 步：重置，回到板上原始结果，EF 41%")

    # ---- D 三个模式打通 ----
    kv._pause()
    kv._goto(b0["ed"] + 3)
    dlg = kv._calibrate()                            # 「标尺定标…」按钮：框里已自动放好两点（只截图，不确定）
    shot("送到快照_屏幕标尺定标", "D 第 10 步（可选看）：「标尺定标…」框里已自动读出两个长刻度（%s）；演示时不用打开，"
         "点「送到快照测量」会自动定标" % (dlg.var_txt.get().split("：")[1].split("；")[0] if dlg.auto else "没读出"), win=dlg.top)
    dlg.top.destroy()
    kv._send_snapshot()                              # 未定标：自动读刻度、直接定标并送到快照
    sv = app.views["snapshot"]
    pump(lambda: app.mode == "snapshot" and sv.mono is not None and sv.ef_result is not None and not sv.busy, 60)
    settle(0.8)
    shot("快照_单平面Simpson", "D 第 10 步：自动切到快照模式，当前这一拍单平面 Simpson：EF %s、%s、%s" % (
        sv.var_mef.get(), sv.var_edv.get(), sv.var_esv.get()))

    # ---- E 正式测量 ----
    sv.var_case.set("%s（%s）" % sv.cases[0])
    sv.measure_case()
    pump(lambda: sv.auto is not None and sv.tracker is not None and len(sv.tracker.steps) >= 12, 30)
    shot("快照_一键双平面_运行中", "E 第 11 步（运行中）：四腔 / 二腔逐帧在板上分割，轮廓和面积曲线跟着动")
    pump(lambda: sv.ef_result is not None and sv.mono is None and not sv.busy and sv.auto is None, 120)
    settle(0.8)
    shot("快照_双平面结果", "E 第 11 步：双平面 Simpson EF %s（CAMUS 参考 40%%）、%s、%s；左房 / 左室质量 / 壁厚" % (
        sv.var_mef.get(), sv.var_edv.get(), sv.var_esv.get()))
    rw = sv._open_reg()
    rw.var_a.set("1")
    rw.var_b.set("2")
    rw.var_dx.set("3")
    rw.var_dy.set("-2")
    rw.start("full")
    pump(lambda: rw.result is not None, 60)
    shot("快照_连续帧配准", "E 第 11b 步（可选）：连续帧配准，已知平移 (3, −2) 找回 ✓，PL 6.1 ms", win=rw.top)
    rw.top.destroy()

    # ---- F 平台扩展 ----
    app._open_platform()
    pf = app.platform
    a = pf.app
    n0 = len(a.hist)
    a._on_send()
    pump(lambda: len(a.hist) > n0 and not a.busy, 60)
    settle(0.8)
    shot("平台扩展_眼底", "F 第 12 步（可选）：平台扩展，眼底血管 512 档一帧（%s，PL %s）" % (a.var_total.get(), a.var_infer.get()),
         win=pf.top)
    pf.switch("skin")
    n0 = len(a.hist)
    a._on_send()
    pump(lambda: len(a.hist) > n0 and not a.busy, 60)
    settle(0.8)
    shot("平台扩展_皮肤镜", "F 第 12 步（可选）：皮肤镜页签，默认 ISIC_0011336，同一引擎换权重（PL %s）" % a.var_infer.get(),
         win=pf.top)
    pf.hide()
    root.destroy()

    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write("<!doctype html><meta charset='utf-8'><title>演示关键步骤</title><style>body{font-family:'Microsoft YaHei UI';"
                "margin:24px;background:#eef1f5}figure{background:#fff;padding:12px;margin:0 0 24px}img{max-width:100%}"
                "figcaption{font-size:15px;margin-bottom:8px}</style><h1>现场验收演示：关键步骤（真板 v5.25）</h1>"
                "<p>仅用于演示准备，画面来自公开数据集（EchoNet-Dynamic / HMC-QU / CAMUS / STARE），不外传。</p>")
        for fn, cap in SHOTS:
            f.write("<figure><figcaption>%s</figcaption><img src='%s'></figure>" % (cap, fn))
    import json
    with open(os.path.join(OUT, "shots.json"), "w", encoding="utf-8") as f:
        json.dump(SHOTS, f, ensure_ascii=False, indent=1)
    print("DEMO_SCREENS_DONE %d" % len(SHOTS))


if __name__ == "__main__":
    main()
