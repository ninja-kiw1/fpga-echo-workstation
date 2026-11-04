"""Drive the real Tk UI against the v5 board: fundus (threshold + ROI), then switch
the AI model in the UI to echo_lv (with click correction: two clicks, undo, reset)
and skin. Results: board_check_v5/gui_acceptance_v5.json.

Same checks as verify_board_v4_ui.py for fundus (that script expects stats
version 4 exactly and writes into board_check_v4/), plus per model: the board
reports the model that was asked for, the UI applied the model's defaults
(threshold, resolution), morphology / ROI are disabled where meaningless, and
the locally recomputed mask / overlay equal the board's bytes.
"""
import json
import types
from pathlib import Path
import tkinter as tk

import app
import confidence
import imageio
import imagelink
import interactive
import proto

HERE = Path(__file__).resolve().parent
OUT = HERE / "board_check_v5"
OUT.mkdir(exist_ok=True)
ECHO_SAMPLE = HERE.parents[1] / "echo" / "samples" / "patient0027_4CH_ED.png"
SKIN_SAMPLE = HERE.parents[1] / "skin" / "samples" / "ISIC_0011336.ppm"
root = tk.Tk()
root.withdraw()
ui = app.App(root, "192.168.1.254", 5060,
             preset_flags=imagelink._parse_flags("proc,mask,overlay,stats,logit,morph"))
report = {"pass": False}
state = {"phase": "fundus_full", "done": False}


def finish(error=None):
    if state["done"]:
        return
    state["done"] = True
    if error:
        report["error"] = str(error)
    else:
        report["pass"] = True
    ui.link.close()
    (OUT / "gui_acceptance_v5.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("GUI_V5_PASS" if report["pass"] else "GUI_V5_FAIL", json.dumps(report, ensure_ascii=False)[:600], flush=True)
    root.destroy()


def send(phase, delay=100):
    state["phase"] = phase
    root.after(delay, ui._on_send)
    root.after(delay + 200, poll)


def common_checks(model):
    assert ui.ai_stats and ui.board_stat_version >= proto.AI_STAT_VERSION_MODEL, ui.var_status.get()
    assert ui.ai_stats["model"] == model, (ui.ai_stats["model"], model)
    assert "逐字节一致" in ui.verify_note, ui.verify_note


def local_threshold():
    try:
        assert ui.ai_stats["frame_count"] == report["fundus"]["frame_count"]
        assert ui.view_images["mask"] == confidence.mask_rgb(ui.logit_plane, 0)
        assert ui.view_images["mask"] != ui.reply_images["mask"]
        assert "本地重算" in ui.var_conf_note.get()
        report["fundus"]["local_threshold_without_new_frame"] = True
        ui.var_ai_thr.set("-3")
        ui.roi = (201, 183, 127, 95)
        ui._draw_roi()
        send("fundus_roi")
    except Exception as exc:
        finish(repr(exc))


def switch_model(label, sample, phase, inter=False):
    ui.var_ai_model.set(label)
    ui.var_inter.set(inter)
    ui._on_inter_toggle()
    m = ui._ui_model()
    assert ui.var_ai_thr.get() == str(m["thr"]) and ui.var_ai_res.get() == str(m["res"])
    assert not ui.var_f_morph.get() and ui.roi is None, "morph / ROI must be off for " + m["name"]
    ui._roi_press(types.SimpleNamespace(x=100, y=100))
    assert ui.roi_drag_start is None, "ROI drag must be refused for " + m["name"]
    ui._load_path(str(sample))
    send(phase, 200)


def poll():
    if ui.busy:
        root.after(100, poll)
        return
    phase = state["phase"]
    try:
        if phase == "fundus_full":
            common_checks(0)
            assert "morph" in ui.reply_images and "morph" in ui.ai_stats
            report["fundus"] = dict(frame_count=ui.ai_stats["frame_count"], confidence_note=ui.verify_note,
                                    morphology_note=ui.var_morph.get(), ai_line=ui.var_ai.get())
            ui.var_show.set("heat")
            assert ui._render_right() == "heat"
            imageio.save_image(str(OUT / "gui_fundus_heat.png"), ui.last_reply)
            ui.var_ai_thr.set("0")
            root.after(150, local_threshold)
        elif phase == "fundus_roi":
            common_checks(0)
            assert "ROI 与整图推理逐值一致" in ui.roi_verify_note, ui.roi_verify_note
            report["fundus"]["roi_note"] = ui.roi_verify_note
            ui._roi_clear()
            switch_model("心超左心室", ECHO_SAMPLE, "echo", inter=True)
        elif phase in ("echo", "skin"):
            model = 2 if phase == "echo" else 1
            common_checks(model)
            assert ui.ai_stats["ai_res"] == 128 and "morph" not in ui.reply_images
            m = proto.MODELS[model]
            assert m["label"] in ui.var_ai.get() and m["target"] in ui.var_ai.get(), ui.var_ai.get()
            assert abs(confidence.SCALE - m["scale"]) < 1e-15
            report[phase] = dict(confidence_note=ui.verify_note, ai_line=ui.var_ai.get(),
                                 conf_note=ui.var_conf_note.get())
            ui.var_show.set("overlay")
            ui._render_right()
            imageio.save_image(str(OUT / f"gui_{phase}_overlay.png"), ui.last_reply)
            if phase == "echo":
                assert ui.inter is not None and len(ui.inter.history) == 1, "auto result must start a session"
                state["auto_plane"] = ui.logit_plane
                mask = interactive.prior_from_plane(ui.logit_plane, 1)
                ys = [i // 512 for i in range(0, 512 * 512, 7) if mask[i]]
                xs = [i % 512 for i in range(0, 512 * 512, 7) if mask[i]]
                state["phase"] = "echo_click1"
                ui._inter_click(types.SimpleNamespace(x=sum(xs) // len(xs), y=sum(ys) // len(ys)), True)
                root.after(200, poll)
            else:
                finish()
        elif phase in ("echo_click1", "echo_click2"):
            common_checks(3)
            n = 1 if phase == "echo_click1" else 2
            assert len(ui.inter.history) == n + 1 and len(ui.inter.clicks) == n, ui.inter.history[-1][1]
            assert ui.ai_stats["ai_res"] == 128 and "proc" not in ui.reply_images
            report[phase] = dict(confidence_note=ui.verify_note, ai_line=ui.var_ai.get(), clicks=ui.inter.clicks)
            imageio.save_image(str(OUT / f"gui_{phase}_overlay.png"), ui.view_images["overlay"])
            if phase == "echo_click1":
                state["click1_plane"] = ui.logit_plane
                state["phase"] = "echo_click2"
                ui._inter_click(types.SimpleNamespace(x=30, y=480), False)
                root.after(200, poll)
            else:
                ui._inter_undo()
                assert len(ui.inter.history) == 2 and ui.logit_plane == state["click1_plane"]
                ui._inter_reset()
                assert len(ui.inter.history) == 1 and ui.logit_plane == state["auto_plane"]
                report["echo_undo_reset_local"] = True
                switch_model("皮肤镜病灶", SKIN_SAMPLE, "skin")
    except Exception as exc:
        finish(repr(exc))


ui._load_path(str(HERE / "samples/im0002.ppm"))
send("fundus_full", 200)
root.after(60000, lambda: finish("GUI test timeout"))
root.mainloop()
raise SystemExit(0 if report["pass"] else 1)
