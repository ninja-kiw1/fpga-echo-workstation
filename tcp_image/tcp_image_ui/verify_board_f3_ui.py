# -*- coding: utf-8 -*-
"""Drive the real Tk sequence window (F3) against the board.

Opens 「序列跟踪」 from the main UI, loads a demo sequence folder (CAMUS 4CH ED -> ES PNG
frames), plays it through on the board, then:
  * every step: the board reports the model that was asked for (2 for step 0, the
    propagation model after), and the local mask equals the board's mask bytes;
  * the step masks equal an offline integer propagation of the same PNG frames
    (host_stretch -> seqeval.encode -> int8_reference), i.e. the UI path is the
    evaluated pipeline;
  * a positive click on the last frame goes to model 3 and is verified; undo restores;
  * ping-pong: plays back ES -> ED on the board and reports the ED Dice against the
    expert label after one full round trip (drift check).
Results: board_check_v5/gui_acceptance_f3.json (+ contour images of ED / ES).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_f3_ui.py [--folder ...]
"""
import argparse
import json
import os
import sys
import types
from pathlib import Path
import tkinter as tk

HERE = Path(__file__).resolve().parent
ECHO = HERE.parents[1] / "echo"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ECHO))

import numpy as np                      # noqa: E402
import torch                            # noqa: E402
from PIL import Image                   # noqa: E402

import app                              # noqa: E402
import imageio                          # noqa: E402
import proto                            # noqa: E402
import sequence                         # noqa: E402
from common import RESULTS, host_stretch, nearest_map   # noqa: E402
from int8_reference import run_integer  # noqa: E402
from seqeval import encode              # noqa: E402

OUT = HERE / "board_check_v5"
OUT.mkdir(exist_ok=True)
ap = argparse.ArgumentParser()
ap.add_argument("--folder", default=str(ECHO / "samples" / "seq_patient0027_4CH"))
ap.add_argument("--seq-tag", default="seq_none_ex")
args = ap.parse_args()

root = tk.Tk()
root.withdraw()
ui = app.App(root, "192.168.1.254", 5060)
win = ui._open_sequence()
report = {"pass": False, "folder": args.folder}
state = {"phase": "play", "done": False}


def finish(error=None):
    if state["done"]:
        return
    state["done"] = True
    if error:
        report["error"] = str(error)
    else:
        report["pass"] = True
    ui.link.close()
    (OUT / "gui_acceptance_f3.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("GUI_F3_PASS" if report["pass"] else "GUI_F3_FAIL", json.dumps(report, ensure_ascii=False)[:800], flush=True)
    root.destroy()


def int_model(tag):
    d = RESULTS / "quant" / tag
    plan = json.loads((d / "quant_plan.json").read_text(encoding="utf-8"))
    with np.load(d / "parameters.npz", allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}

    def p(rgb):
        with torch.inference_mode():
            q = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).unsqueeze(0)
            return run_integer(plan, arrays, q)[0][0, 0].numpy() >= 1
    return p


def m128(step):
    return np.frombuffer(step.mask, np.uint8).reshape(512, 512)[::4, ::4].astype(bool)


def expert_dice(mask128, gt_png):
    gt = np.array(Image.open(gt_png)) > 0
    h, w = gt.shape
    big = np.repeat(np.repeat(mask128, 4, 0), 4, 1)
    pred = big[nearest_map(512, h)][:, nearest_map(512, w)]
    return float(2 * (pred & gt).sum() / max(pred.sum() + gt.sum(), 1))


def check_steps(steps, models):
    for k, (s, m) in enumerate(zip(steps, models)):
        assert s.verified, "step %d: local mask != board mask bytes" % k
        assert s.ai_us and s.ai_us > 0, "step %d: no board AI time" % k


def poll():
    if win.busy or win.playing:
        root.after(100, poll)
        return
    tr = win.tracker
    try:
        if state["phase"] == "play":
            n = len(win.frames)
            assert len(tr.steps) == n, (len(tr.steps), n, win.var_status.get())
            seq_m = tr.seq
            check_steps(tr.steps, [2] + [seq_m["index"]] * (n - 1))
            names = sorted(f for f in os.listdir(args.folder) if f.endswith(".png") and not f.endswith("_gt.png"))
            x128 = [host_stretch(np.array(Image.open(os.path.join(args.folder, f))))[::4, ::4] for f in names]
            f1, prop = int_model("lv_r128"), int_model(args.seq_tag if seq_m["index"] >= 4 else "inter2_r128")
            ref = [f1(np.repeat(x128[0][..., None], 3, -1))]
            for t in range(1, n):
                ref.append(prop(encode(seq_m.get("enc", "none"), x128[t], ref[-1], x128[t - 1])))
            same = [bool(np.array_equal(m128(s), r)) for s, r in zip(tr.steps, ref)]
            assert all(same), "steps differing from offline: %s" % [k for k, v in enumerate(same) if not v]
            kf = tr.keyframes()
            gts = sorted(f for f in os.listdir(args.folder) if f.endswith("_gt.png"))
            report["play"] = dict(
                frames=n, seq_model=seq_m["index"], encoding=seq_m.get("enc", "none"),
                all_steps_equal_offline=True,
                ai_ms_mean=float(np.mean([s.ai_us for s in tr.steps[1:]]) / 1000),
                round_trip_ms_mean=float(np.mean([s.rt_ms for s in tr.steps[1:]])),
                areas_px=[s.area for s in tr.steps],
                keyframes=dict(ed_frame=kf[0], ed_area=tr.mapper.label(kf[1]), es_frame=kf[2],
                               es_area=tr.mapper.label(kf[3]), fac=kf[4]),
                ed_dice=expert_dice(m128(tr.steps[0]), os.path.join(args.folder, gts[0])),
                es_dice=expert_dice(m128(tr.steps[-1]), os.path.join(args.folder, gts[-1])),
                frame_line=win.var_frame.get(), key_line=win.var_key.get())
            for k, tag in ((0, "ed"), (n - 1, "es")):
                s = tr.steps[k]
                imageio.save_image(str(OUT / ("gui_f3_%s_contour.png" % tag)),
                                   sequence.contour_rgb(win.frames[s.frame], s.mask))
            state["before_click"] = tr.steps[-1].mask
            ys, xs = np.nonzero(m128(tr.steps[-1]))
            state["phase"] = "click"
            win._click(types.SimpleNamespace(x=int(xs.mean()) * 4, y=int(ys.mean()) * 4), True)
            root.after(200, poll)
        elif state["phase"] == "click":
            s = tr.steps[-1]
            assert s.kind == "fix" and s.verified and len(tr.clicks()) == 1, (s.kind, s.verified)
            report["click"] = dict(model=tr.inter["index"], area=tr.mapper.label(s.area), frame_line=win.var_frame.get())
            win._undo()
            assert tr.steps[-1].mask == state["before_click"] and tr.steps[-1].kind == "prop"
            report["click"]["undo_restores"] = True
            win.var_pingpong.set(True)
            win._on_pingpong()
            state["phase"] = "pingpong"
            state["target"] = 2 * len(win.frames) - 1          # back at ED
            win._toggle_play()
            root.after(200, poll_pingpong)
            return
    except Exception as exc:
        finish(repr(exc))


def poll_pingpong():
    tr = win.tracker
    if len(tr.steps) < state["target"]:
        if not win.playing and not win.busy:
            return finish("ping-pong stopped early: %s" % win.var_status.get())
        root.after(100, poll_pingpong)
        return
    if win.playing:
        win._toggle_play()                      # pause
    if win.busy:
        root.after(100, poll_pingpong)
        return
    try:
        steps = tr.steps[:state["target"]]
        assert steps[-1].frame == 0 and all(s.verified for s in steps)
        gts = sorted(f for f in os.listdir(args.folder) if f.endswith("_gt.png"))
        report["pingpong"] = dict(
            steps=len(steps), frames_order_ok=[s.frame for s in steps] == [tr.frame_at(k) for k in range(len(steps))],
            ed_dice_start=report["play"]["ed_dice"],
            ed_dice_after_round_trip=expert_dice(m128(steps[-1]), os.path.join(args.folder, gts[0])),
            area_start_px=steps[0].area, area_after_round_trip_px=steps[-1].area)
        assert report["pingpong"]["frames_order_ok"]
        finish()
    except Exception as exc:
        finish(repr(exc))


if not win.load(args.folder):
    finish("load failed")
else:
    win._toggle_play()
    root.after(300, poll)
    root.after(180000, lambda: finish("GUI F3 test timeout"))
    root.mainloop()
raise SystemExit(0 if report["pass"] else 1)
