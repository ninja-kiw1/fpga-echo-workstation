# -*- coding: utf-8 -*-
"""verify_board_tile_ui.py —— 部分 C 的真板界面验收：真的 Tk 主窗口 + 大图精查窗口，全程走界面自己的代码路径。

1. 主窗口载入 samples/im0002.ppm（STARE 700x605 原图），「框选放大」保持 1 倍：拖框 → 发送 = 原来的 ROI 帧
   （v5 控制头；界面自己核对"ROI 与整图推理逐值一致"），证明 1 倍仍是老 ROI。
2. 「框选放大」改成 2 倍，同一个框 → 发送：打开大图精查窗口，先算 1 倍总览，再按这张图的上限（STARE 700 宽 = 原图 1.167 倍）
   分块重新推理、其余插值放大显示（说明里写"仅插值"）。
   核对：总览 logit == 1 倍整层参照；放大视图 logit == 2 倍层上框内参照（tile_ref，= 整幅推理，逐位）；
   框的映射 = tile_engine.box_to_level(帧框 → 原图 → 2 倍层)；块的计算顺序 = 按总览不确定度；指标行有数。
3. HRF 训练图（板上 /data 有预制层时走文件）：总览 + 一个建议框按 2 倍放大，同样逐位核对，并核对层来自板上文件。
4. 选一个超过上限的倍数：按上限推理、显示放大 = 倍数 / 上限，说明里写"仅插值"。
结果：board_check_v5/tile_ui.json，打印 TILE_UI_BOARD_PASS / FAIL。
"""
import json
import os
import sys
import tkinter as tk
from pathlib import Path

import numpy as np
from PIL import Image

import app
import imagelink
import tile_engine as te

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\PLD\hrf")
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else HERE / "board_check_v5" / "tile_ui.json"
OUT.parent.mkdir(exist_ok=True, parents=True)
root = tk.Tk()
root.withdraw()
ui = app.App(root, "192.168.1.254", 5060, preset_flags=imagelink._parse_flags("proc,overlay,stats,logit"))
report = {"pass": False}
state = {"done": False}
REF = {}


def finish(error=None):
    if state["done"]:
        return
    state["done"] = True
    if error:
        report["error"] = str(error)
    else:
        report["pass"] = True
    ui.link.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("TILE_UI_BOARD_PASS" if report["pass"] else "TILE_UI_BOARD_FAIL", json.dumps(report, ensure_ascii=False,
                                                                                         default=str)[:800], flush=True)
    root.destroy()


def ref_box(rgb0, mag, box):
    """mag 倍层上框内的参照 logit（分块参照 = 整幅推理）。"""
    from tile_ref import Ref
    lvl, _ = te.make_level(rgb0, te.scale_for_mag(mag, rgb0.shape[1]))
    r = Ref(limit=True, max_cache=12)
    out, _ = te.run_tiles(lvl, box, r.run, skip_fov=True)
    r.close()
    return out, lvl


def check_window(win, tag):
    """总览 + 放大视图与参照逐位相同。"""
    ov = win.overview
    lvl1 = win.level(win.m_ov)[0]
    H, W = lvl1.shape[:2]
    ref1, _ = ref_box(win.rgb0, win.m_ov, (0, 0, W, H))
    assert np.array_equal(ov["logits"], ref1), "%s overview logits != reference" % tag
    z = win.zoom
    refz, lvlz = ref_box(win.rgb0, z["mag"], z["box"])
    assert np.array_equal(z["logits"], refz), "%s zoom logits != reference (%d px)" % (tag, int((z["logits"] != refz).sum()))
    s1, sz = win.level(win.m_ov)[2], win.level(z["mag"])[2]
    assert z["box"] == te.box_to_level(te.box_to_orig(win.box1, s1), sz, lvlz.shape[1], lvlz.shape[0]), "box mapping"
    got = sorted(z["out"]["tiles"], key=lambda t: t["pos"])
    order = te.order_by_uncertainty(z["tiles"], lvlz.shape[1],
                                    lvlz.shape[0], ov["logits"], ov["fov"], win.cfg["thr"], win.cfg["band"])
    assert [t["index"] for t in got] == order, "tiles not computed in uncertainty order"
    assert win.var_m1.get() and win.var_m2.get() and "血管密度" in win.var_m3.get(), "metrics lines empty"
    return dict(mag=z["mag"], box1=list(win.box1), box=list(z["box"]), tiles=len(z["tiles"]),
                run=z["out"]["result"]["run"], skipped=z["out"]["result"]["skipped"], job_ms=z["out"]["result"]["ms"],
                source=z["out"]["result"]["source"], overview_ms=ov["out"]["result"]["ms"],
                m1=win.var_m1.get(), m2=win.var_m2.get(), m3=win.var_m3.get(), zoom_note=win.var_zoomnote.get(),
                overview_note=win.var_ovnote.get())


def wait(cond, then):
    if state["done"]:
        return
    try:
        if cond():
            then()
        else:
            root.after(200, lambda: wait(cond, then))
    except Exception as exc:                          # noqa: BLE001
        finish(repr(exc))


def step1():
    ui._load_path(str(HERE / "samples" / "im0002.ppm"))
    assert ui.orig_rgb.shape == (605, 700, 3), ui.orig_rgb.shape
    assert ui.var_zoom.get() == app.ZOOM_1X
    ui.root.after(100, ui._on_send)                   # 整帧（ROI 核对的参照）
    wait(lambda: not ui.busy and ui.logit_plane is not None, step1b)


def step1b():
    ui.roi = (201, 183, 127, 95)
    ui._draw_roi()
    state["frame_count"] = ui.ai_stats["frame_count"]
    ui._on_send()
    wait(lambda: not ui.busy and ui.frame_roi is not None, step1c)


def step1c():
    assert "ROI 与整图推理逐值一致" in ui.roi_verify_note, ui.roi_verify_note
    report["zoom1_old_roi"] = dict(note=ui.roi_verify_note, crop=[ui.ai_stats["crop_w"], ui.ai_stats["crop_h"]])
    lab = [v for v in ui.zoom_values if v.startswith("2×")]
    assert lab, ui.zoom_values
    ui.var_zoom.set(lab[0])
    assert "倍层上" in ui.var_roi_note.get() and "插值放大" in ui.var_roi_note.get(), ui.var_roi_note.get()   # STARE 700 宽：2 倍超过原图分辨率
    report["zoom2_note"] = ui.var_roi_note.get()
    ui._on_send()                                     # -> 大图精查窗口
    wait(lambda: getattr(ui, "big_win", None) is not None and ui.big_win.zoom is not None
         and ui.big_win.zoom.get("out") is not None and not ui.busy, step2)


def step2():
    win = ui.big_win
    report["stare_window"] = check_window(win, "stare")
    # STARE 700 宽：2 倍超过原图分辨率 -> 按原图（1.167 倍，层 = 原图本身）推理 + 插值放大显示
    assert abs(win.zoom["mag"] - 700 / 600.0) < 1e-9 and win.disp_extra > 1, (win.zoom["mag"], win.disp_extra)
    assert "仅插值" in win.var_zoomnote.get(), win.var_zoomnote.get()
    # 超过上限：按上限推理 + 插值显示
    over = [lab for lab in win.mag_labels if "仅显示放大" in lab]
    if over:
        win.var_mag.set(over[0])
        win.run_zoom()
        wait(lambda: win.zoom.get("out") is not None and not ui.busy, lambda: step3(win))
    else:
        report["over_cap"] = "no magnification above the cap in tile_config.json"
        step4()


def step3(win):
    mag, extra = win._mag()
    assert abs(win.zoom["mag"] - win.cap) < 1e-9 and extra > 1, (win.zoom["mag"], extra)
    assert "仅插值" in win.var_zoomnote.get(), win.var_zoomnote.get()
    report["over_cap"] = dict(label=win.var_mag.get(), inference_mag=win.zoom["mag"], display_extra=extra,
                              note=win.var_zoomnote.get())
    step4()


def step4():
    sp = json.load(open(r"E:\PLD\hrf\split.json", encoding="utf-8"))
    name = sp["train"][1]
    path = os.path.join(r"E:\PLD\hrf\all\images", sp["files"][name]["image"])
    ui._load_path(path)
    win = ui._open_bigwin()
    state["hrf_win"] = win
    wait(lambda: win.overview is not None and not ui.busy, lambda: step5(win))


def step5(win):
    assert win.sugg, "no suggestions"
    u, box = win.sugg[0]
    win.box1 = box
    for lab in win.mag_labels:
        if lab.startswith("2×"):
            win.var_mag.set(lab)
    win.run_zoom()
    wait(lambda: win.zoom.get("out") is not None and not ui.busy, lambda: step6(win))


def step6(win):
    r = check_window(win, "hrf")
    r["suggestion_unsure"] = win.sugg[0][0]
    r["pyramid_case"] = bool(win.case)
    if win.case:
        assert r["source"] == "file", "pyramid level exists on the board but was not used"
    report["hrf_window"] = r
    finish()


root.after(300, lambda: wait(lambda: True, step1))
root.after(600000, lambda: finish("GUI test timeout"))
root.mainloop()
